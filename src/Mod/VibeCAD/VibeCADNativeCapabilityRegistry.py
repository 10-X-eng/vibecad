# SPDX-License-Identifier: LGPL-2.1-or-later

"""Fail-closed capability registry for the Native provider surface.

Definitions, implementations, and the human-selected action inventory are
separate authorities. A Native surface is advertised only when every live
provider-eligible action has one exact definition and one implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import re
from typing import Any, Callable, Mapping

from VibeCADNativeActionManifest import (
    NativeActionPlan,
    resolve_native_action_inventory,
)
from VibeCADNativeContextManifest import (
    context_actions_for_surface,
    provider_context_actions_for_surface,
)
from VibeCADNativeSurface import NativeSurfaceSnapshot
from VibeCADNativeSchemaRules import (
    NativeSchemaRuleError,
    validate_bounded_parameter_schema,
)
from VibeCADRibbonSurface import RibbonSurface, SURFACE_IDS


MAX_NATIVE_TOOLS_PER_SURFACE = 24
MAX_NATIVE_SCHEMAS_JSON_BYTES = 64 * 1024
_CAPABILITY_NAME = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
_VARIANT_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_PRIMARY_CLASSES = frozenset({"read", "mutation", "view", "export"})


class NativeCapabilityRegistryError(RuntimeError):
    """A Native capability declaration violates the registry contract."""


def _compact_integral_json_numbers(value: Any) -> Any:
    """Use JSON integers for integral schema numbers without changing semantics."""
    if type(value) is float and math.isfinite(value) and value.is_integer():
        return int(value)
    if isinstance(value, Mapping):
        return {
            key: _compact_integral_json_numbers(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_compact_integral_json_numbers(item) for item in value]
    return value


def _canonical_schema(value: Mapping[str, Any]) -> str:
    try:
        encoded = json.dumps(
            _compact_integral_json_numbers(value),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise NativeCapabilityRegistryError(
            f"Capability parameters must be bounded JSON: {exc}"
        ) from exc
    if not isinstance(decoded, dict):
        raise NativeCapabilityRegistryError("Capability parameters must be an object.")
    if decoded.get("type") != "object":
        raise NativeCapabilityRegistryError(
            "Capability variant parameters must declare type='object'."
        )
    if decoded.get("additionalProperties") is not False:
        raise NativeCapabilityRegistryError(
            "Capability variant parameters must reject additional properties."
        )
    properties = decoded.get("properties")
    if not isinstance(properties, dict):
        raise NativeCapabilityRegistryError(
            "Capability variant parameters must declare properties."
        )
    if "operation" in properties:
        raise NativeCapabilityRegistryError(
            "The registry owns the operation discriminator."
        )
    required = decoded.get("required", [])
    if not isinstance(required, list) or any(
        not isinstance(name, str) or name not in properties for name in required
    ):
        raise NativeCapabilityRegistryError(
            "Capability required fields must name declared properties."
        )
    try:
        validate_bounded_parameter_schema(decoded)
    except NativeSchemaRuleError as exc:
        raise NativeCapabilityRegistryError(str(exc)) from exc
    return encoded


def _operation_field_map(
    operations: tuple[str, ...],
    branches: tuple[Mapping[str, Any], ...],
) -> str:
    grouped: dict[tuple[tuple[str, ...], tuple[str, ...]], list[str]] = {}
    for operation, branch in zip(operations, branches, strict=True):
        properties = tuple(
            name for name in branch["properties"] if name != "operation"
        )
        required = tuple(
            name
            for name in branch.get("required", ())
            if name != "operation"
        )
        optional = tuple(name for name in properties if name not in required)
        grouped.setdefault((required, optional), []).append(operation)
    entries = []
    for (required, optional), group_operations in grouped.items():
        fields = ",".join(required) if required else "none"
        if optional:
            fields += f"; optional {','.join(optional)}"
        entries.append(f"{'|'.join(group_operations)}={fields}")
    return "Fields by operation: " + "; ".join(entries) + "."


def _serialized_schema(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _unique_schema_options(
    options: tuple[Mapping[str, Any], ...],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    encoded = set()
    for option in options:
        key = _serialized_schema(option)
        if key in encoded:
            continue
        encoded.add(key)
        result.append(json.loads(key))
    return result


def _smaller_schema(
    candidate: dict[str, Any],
    fallback: dict[str, Any],
) -> dict[str, Any]:
    return (
        candidate
        if len(_serialized_schema(candidate)) < len(_serialized_schema(fallback))
        else fallback
    )


def _numeric_union(options: list[dict[str, Any]]) -> dict[str, Any] | None:
    kind = options[0].get("type")
    allowed = {
        "type",
        "minimum",
        "exclusiveMinimum",
        "maximum",
        "exclusiveMaximum",
    }
    if kind not in {"integer", "number"} or any(
        option.get("type") != kind or not set(option) <= allowed
        for option in options
    ):
        return None

    def lower(option: Mapping[str, Any]) -> tuple[Any | None, bool]:
        if "minimum" in option:
            return option["minimum"], False
        if "exclusiveMinimum" in option:
            return option["exclusiveMinimum"], True
        return None, False

    def upper(option: Mapping[str, Any]) -> tuple[Any | None, bool]:
        if "maximum" in option:
            return option["maximum"], False
        if "exclusiveMaximum" in option:
            return option["exclusiveMaximum"], True
        return None, False

    result: dict[str, Any] = {"type": kind}
    lowers = [lower(option) for option in options]
    if all(value is not None for value, _exclusive in lowers):
        lower_value = min(value for value, _exclusive in lowers)
        exclusive = all(
            is_exclusive
            for value, is_exclusive in lowers
            if value == lower_value
        )
        result["exclusiveMinimum" if exclusive else "minimum"] = lower_value
    uppers = [upper(option) for option in options]
    if all(value is not None for value, _exclusive in uppers):
        upper_value = max(value for value, _exclusive in uppers)
        exclusive = all(
            is_exclusive
            for value, is_exclusive in uppers
            if value == upper_value
        )
        result["exclusiveMaximum" if exclusive else "maximum"] = upper_value
    return result


def _unlabelled_closed_object_union(
    options: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if any(
        option.get("type") != "object"
        or option.get("additionalProperties") is not False
        or not isinstance(option.get("properties"), Mapping)
        or "description" in option
        for option in options
    ):
        return None
    property_options: dict[str, list[Mapping[str, Any]]] = {}
    for option in options:
        for name, schema in option["properties"].items():
            property_options.setdefault(name, []).append(schema)
    properties = {
        name: _compact_schema_options(tuple(choices))
        for name, choices in property_options.items()
    }
    required = [
        name
        for name in options[0].get("required", ())
        if all(name in option.get("required", ()) for option in options[1:])
    ]
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _array_union(options: list[dict[str, Any]]) -> dict[str, Any] | None:
    allowed = {"type", "items", "minItems", "maxItems", "uniqueItems"}
    if any(
        option.get("type") != "array"
        or not set(option) <= allowed
        or not isinstance(option.get("items"), Mapping)
        for option in options
    ):
        return None
    result = {
        "type": "array",
        "items": _compact_schema_options(
            tuple(option["items"] for option in options)
        ),
        "minItems": min(int(option.get("minItems", 0)) for option in options),
        "maxItems": max(int(option["maxItems"]) for option in options),
    }
    if all(option.get("uniqueItems") is True for option in options):
        result["uniqueItems"] = True
    return result


def _compact_schema_options(
    options: tuple[Mapping[str, Any], ...],
) -> dict[str, Any]:
    """Return the smallest explicit typed union accepted by every branch."""

    unique = _unique_schema_options(options)
    if len(unique) == 1:
        return unique[0]
    fallback = {"anyOf": unique}
    string_values = []
    strings = True
    for option in unique:
        if option.get("type") != "string" or not set(option) <= {
            "type",
            "const",
            "enum",
        }:
            strings = False
            break
        values = option.get("enum", [option.get("const")])
        if not isinstance(values, list) or any(
            not isinstance(value, str) for value in values
        ):
            strings = False
            break
        for value in values:
            if value not in string_values:
                string_values.append(value)
    if strings:
        return _smaller_schema(
            {"type": "string", "enum": string_values},
            fallback,
        )
    candidate = (
        _numeric_union(unique)
        or _unlabelled_closed_object_union(unique)
        or _array_union(unique)
    )
    return _smaller_schema(candidate, fallback) if candidate is not None else fallback


def _compact_closed_object_options(
    operations: tuple[str, ...],
    options: tuple[Mapping[str, Any], ...],
) -> dict[str, Any] | None:
    """Compact operation-specific closed objects without weakening execution."""

    if not options or len(operations) != len(options) or any(
        option.get("type") != "object"
        or option.get("additionalProperties") is not False
        or not isinstance(option.get("properties"), Mapping)
        for option in options
    ):
        return None
    property_options: dict[str, list[Mapping[str, Any]]] = {}
    for option in options:
        for name, schema in option["properties"].items():
            choices = property_options.setdefault(name, [])
            if schema not in choices:
                choices.append(schema)
    properties = {
        name: _compact_schema_options(tuple(choices))
        for name, choices in property_options.items()
    }
    required = [
        name
        for name in options[0].get("required", ())
        if all(name in option.get("required", ()) for option in options[1:])
    ]
    descriptions = []
    for operation, option in zip(operations, options, strict=True):
        description = option.get("description")
        if isinstance(description, str) and description.strip():
            descriptions.append(f"{operation}: {description.strip()}")
    field_map = _operation_field_map(operations, options)
    if descriptions:
        field_map += " Details: " + " ".join(descriptions)
    compact = {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
        "description": field_map,
    }
    original = {"anyOf": _unique_schema_options(options)}
    return compact if len(_serialized_schema(compact)) < len(
        _serialized_schema(original)
    ) else None


def _compact_multi_variant_parameters(
    operations: tuple[str, ...],
    branches: tuple[Mapping[str, Any], ...],
) -> dict[str, Any]:
    property_options: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for operation, branch in zip(operations, branches, strict=True):
        for name, schema in branch["properties"].items():
            if name == "operation":
                continue
            options = property_options.setdefault(name, [])
            options.append((operation, schema))
    properties: dict[str, Any] = {
        "operation": {
            "type": "string",
            "enum": list(operations),
            "description": _operation_field_map(operations, branches),
        }
    }
    for name, operation_options in property_options.items():
        unique = _unique_schema_options(
            tuple(option for _operation, option in operation_options)
        )
        if len(unique) == 1:
            properties[name] = unique[0]
            continue
        compact = _compact_closed_object_options(
            tuple(operation for operation, _option in operation_options),
            tuple(option for _operation, option in operation_options),
        )
        properties[name] = compact or _compact_schema_options(tuple(unique))
    required = [
        name
        for name in branches[0].get("required", ())
        if name == "operation"
        or all(name in branch.get("required", ()) for branch in branches[1:])
    ]
    parameters = {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }
    try:
        validate_bounded_parameter_schema(parameters)
    except NativeSchemaRuleError as exc:
        raise NativeCapabilityRegistryError(str(exc)) from exc
    return parameters


@dataclass(frozen=True, slots=True)
class NativeCapabilityVariant:
    operation: str
    description: str
    action_ids: frozenset[str]
    surface_ids: frozenset[str]
    exact_target_type: str | None
    transaction_behavior: str
    background_required: bool
    parameters: Mapping[str, Any] = field(repr=False, compare=False)
    _parameters_json: str = field(init=False, repr=False, compare=True)

    def __post_init__(self) -> None:
        if not _VARIANT_NAME.fullmatch(self.operation):
            raise NativeCapabilityRegistryError(
                f"Invalid capability operation variant {self.operation!r}."
            )
        if not self.description.strip() or len(self.description) > 240:
            raise NativeCapabilityRegistryError(
                f"Capability variant {self.operation!r} needs a concise description."
            )
        if not self.action_ids or any(not value.strip() for value in self.action_ids):
            raise NativeCapabilityRegistryError(
                f"Capability variant {self.operation!r} needs exact action IDs."
            )
        if not self.surface_ids or any(
            value not in SURFACE_IDS or value == "unavailable"
            for value in self.surface_ids
        ):
            raise NativeCapabilityRegistryError(
                f"Capability variant {self.operation!r} has invalid surfaces."
            )
        if not self.transaction_behavior.strip():
            raise NativeCapabilityRegistryError(
                f"Capability variant {self.operation!r} needs transaction behavior."
            )
        object.__setattr__(self, "_parameters_json", _canonical_schema(self.parameters))

    def provider_parameters(self) -> dict[str, Any]:
        parameters = json.loads(self._parameters_json)
        properties = dict(parameters["properties"])
        properties["operation"] = {
            "type": "string",
            "const": self.operation,
        }
        parameters["properties"] = {"operation": properties.pop("operation"), **properties}
        parameters["required"] = [
            "operation",
            *[name for name in parameters.get("required", []) if name != "operation"],
        ]
        return parameters


@dataclass(frozen=True, slots=True)
class NativeCapabilityDefinition:
    name: str
    description: str
    primary_classification: str
    variants: tuple[NativeCapabilityVariant, ...]

    def __post_init__(self) -> None:
        if not _CAPABILITY_NAME.fullmatch(self.name):
            raise NativeCapabilityRegistryError(
                f"Capability name {self.name!r} must use domain.operation."
            )
        if not self.description.strip() or len(self.description) > 240:
            raise NativeCapabilityRegistryError(
                f"Capability {self.name!r} needs one concise description."
            )
        if self.primary_classification not in _PRIMARY_CLASSES:
            raise NativeCapabilityRegistryError(
                f"Capability {self.name!r} has invalid primary classification."
            )
        if not self.variants:
            raise NativeCapabilityRegistryError(
                f"Capability {self.name!r} has no operation variants."
            )
        operations = [variant.operation for variant in self.variants]
        if len(operations) != len(set(operations)):
            raise NativeCapabilityRegistryError(
                f"Capability {self.name!r} repeats an operation variant."
            )

    def provider_schema(
        self,
        required_operations: tuple[str, ...],
    ) -> dict[str, Any]:
        variants = {variant.operation: variant for variant in self.variants}
        missing = [operation for operation in required_operations if operation not in variants]
        if missing:
            raise NativeCapabilityRegistryError(
                f"Capability {self.name!r} lacks variants: {sorted(set(missing))}."
            )
        ordered = tuple(dict.fromkeys(required_operations))
        branches = tuple(
            variants[operation].provider_parameters() for operation in ordered
        )
        operation_summary = ", ".join(ordered)
        return {
            "name": self.name,
            "description": (
                f"{self.description} Operations: {operation_summary}."
                if len(ordered) > 1
                else self.description
            ),
            "parameters": (
                {"oneOf": list(branches)}
                if len(branches) == 1
                else _compact_multi_variant_parameters(ordered, branches)
            ),
        }


@dataclass(frozen=True, slots=True)
class NativeCapabilityImplementation:
    name: str
    handler: Callable[[Any], Mapping[str, Any]] = field(
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if not _CAPABILITY_NAME.fullmatch(self.name) or not callable(self.handler):
            raise NativeCapabilityRegistryError(
                "A Native implementation needs a valid name and callable handler."
            )


class NativeCapabilityRegistry:
    """Own exact definitions and implementations without executing either."""

    def __init__(self) -> None:
        self._definitions: dict[str, NativeCapabilityDefinition] = {}
        self._implementations: dict[str, NativeCapabilityImplementation] = {}
        self._shared_definition_names: list[str] = []

    def register_definition(self, definition: NativeCapabilityDefinition) -> None:
        if not isinstance(definition, NativeCapabilityDefinition):
            raise TypeError("definition must be a NativeCapabilityDefinition")
        if definition.name in self._definitions:
            raise NativeCapabilityRegistryError(
                f"Native capability {definition.name!r} is already defined."
            )
        self._definitions[definition.name] = definition

    def register_shared_definition(
        self,
        definition: NativeCapabilityDefinition,
    ) -> None:
        """Register one capability required on each of its declared surfaces."""

        self.register_definition(definition)
        self._shared_definition_names.append(definition.name)

    def register_implementation(
        self,
        implementation: NativeCapabilityImplementation,
    ) -> None:
        if not isinstance(implementation, NativeCapabilityImplementation):
            raise TypeError("implementation must be a NativeCapabilityImplementation")
        if implementation.name in self._implementations:
            raise NativeCapabilityRegistryError(
                f"Native capability {implementation.name!r} already has an implementation."
            )
        self._implementations[implementation.name] = implementation

    def definition(self, name: str) -> NativeCapabilityDefinition | None:
        return self._definitions.get(str(name))

    def implementation(self, name: str) -> NativeCapabilityImplementation | None:
        return self._implementations.get(str(name))

    @property
    def definition_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._definitions))

    @property
    def implementation_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._implementations))

    @property
    def shared_definition_names(self) -> tuple[str, ...]:
        return tuple(self._shared_definition_names)


@dataclass(frozen=True, slots=True)
class NativeProviderSurface:
    snapshot: NativeSurfaceSnapshot
    available: bool
    unavailable_reason: str
    tool_names: tuple[str, ...]
    schemas: tuple[dict[str, Any], ...]
    human_only_action_ids: tuple[str, ...]
    missing_definition_names: tuple[str, ...]
    missing_implementation_names: tuple[str, ...]
    incomplete_definition_names: tuple[str, ...]
    missing_action_ids: tuple[str, ...] = ()

    def summary(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "mode": "native",
            "surface_id": self.snapshot.surface_id,
            "surface_revision": self.snapshot.revision,
            "available": self.available,
            "tool_count": len(self.tool_names),
            "human_only_action_count": len(self.human_only_action_ids),
        }
        if not self.available:
            result["unavailable_reason"] = self.unavailable_reason
        return result

    def debug_summary(self) -> dict[str, Any]:
        return {
            **self.summary(),
            "manifest_sha256": self.snapshot.manifest_sha256,
            "tool_names": list(self.tool_names),
            "human_only_action_ids": list(self.human_only_action_ids),
            "missing_definition_names": list(self.missing_definition_names),
            "missing_implementation_names": list(self.missing_implementation_names),
            "incomplete_definition_names": list(self.incomplete_definition_names),
            "missing_action_ids": list(self.missing_action_ids),
        }


@dataclass(frozen=True, slots=True)
class _RequiredAction:
    action_id: str
    capability_family: str
    operation_variant: str
    primary_classification: str
    transaction_behavior: str
    background_required: bool


def _primary_classification(classification: Any) -> str:
    values = tuple(
        name for name in _PRIMARY_CLASSES if bool(getattr(classification, name, False))
    )
    if len(values) != 1:
        raise NativeCapabilityRegistryError(
            "Every provider action needs one primary classification."
        )
    return values[0]


def _required_actions(
    surface: RibbonSurface,
    ribbon_plans: tuple[NativeActionPlan, ...],
) -> tuple[_RequiredAction, ...]:
    context_plans = provider_context_actions_for_surface(surface.surface_id)
    result: list[_RequiredAction] = []
    for plan in ribbon_plans:
        if plan.classification.parent_only or plan.classification.human_only:
            continue
        if not plan.operation_variant:
            raise NativeCapabilityRegistryError(
                f"Provider ribbon action {plan.command_id!r} has no operation variant."
            )
        result.append(
            _RequiredAction(
                plan.command_id,
                plan.capability_family,
                plan.operation_variant,
                _primary_classification(plan.classification),
                plan.transaction_behavior,
                plan.background_required,
            )
        )
    for plan in context_plans:
        if not plan.operation_variant:
            raise NativeCapabilityRegistryError(
                f"Provider context action {plan.action_id!r} has no operation variant."
            )
        result.append(
            _RequiredAction(
                plan.action_id,
                plan.capability_family,
                plan.operation_variant,
                _primary_classification(plan.classification),
                plan.transaction_behavior,
                plan.background_required,
            )
        )
    return tuple(result)


def _shared_requirements(
    surface_id: str,
    registry: NativeCapabilityRegistry,
) -> tuple[_RequiredAction, ...]:
    result = []
    for name in registry.shared_definition_names:
        definition = registry.definition(name)
        if definition is None:
            raise NativeCapabilityRegistryError(
                f"Shared capability {name!r} has no definition."
            )
        for variant in definition.variants:
            if surface_id not in variant.surface_ids:
                continue
            result.append(
                _RequiredAction(
                    sorted(variant.action_ids)[0],
                    definition.name,
                    variant.operation,
                    definition.primary_classification,
                    variant.transaction_behavior,
                    variant.background_required,
                )
            )
    return tuple(result)


def _definition_covers(
    definition: NativeCapabilityDefinition,
    requirement: _RequiredAction,
    surface_id: str,
) -> bool:
    return any(
        variant.operation == requirement.operation_variant
        and requirement.action_id in variant.action_ids
        and surface_id in variant.surface_ids
        and variant.transaction_behavior == requirement.transaction_behavior
        and variant.background_required is requirement.background_required
        for variant in definition.variants
    )


def resolve_native_provider_surface(
    surface: RibbonSurface,
    registry: NativeCapabilityRegistry | None = None,
) -> NativeProviderSurface:
    """Resolve a complete Native schema set or advertise no Native tools."""

    if not isinstance(surface, RibbonSurface):
        raise TypeError("surface must be a RibbonSurface")
    selected_registry = registry or NativeCapabilityRegistry()
    if not isinstance(selected_registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")

    snapshot = NativeSurfaceSnapshot.from_surface(surface)
    action_inventory = resolve_native_action_inventory(surface)
    observed_action_ids = frozenset(surface.command_ids)
    missing_action_ids = tuple(
        action_id
        for action_id in action_inventory.required_action_ids
        if action_id not in observed_action_ids
    )
    requirements = (
        *_shared_requirements(surface.surface_id, selected_registry),
        *_required_actions(surface, action_inventory.plans),
    )
    families = tuple(dict.fromkeys(item.capability_family for item in requirements))
    if len(families) > MAX_NATIVE_TOOLS_PER_SURFACE:
        raise NativeCapabilityRegistryError(
            f"Native surface {surface.surface_id!r} requires {len(families)} tools; "
            f"limit is {MAX_NATIVE_TOOLS_PER_SURFACE}."
        )

    family_classes: dict[str, set[str]] = {}
    for requirement in requirements:
        family_classes.setdefault(requirement.capability_family, set()).add(
            requirement.primary_classification
        )
    mixed = sorted(name for name, values in family_classes.items() if len(values) != 1)
    if mixed:
        raise NativeCapabilityRegistryError(
            f"Native capability families mix primary classifications: {mixed}."
        )

    missing_definitions: list[str] = []
    missing_implementations: list[str] = []
    incomplete_definitions: list[str] = []
    schemas: list[dict[str, Any]] = []
    for family in families:
        definition = selected_registry.definition(family)
        implementation = selected_registry.implementation(family)
        if definition is None:
            missing_definitions.append(family)
        else:
            family_requirements = tuple(
                item for item in requirements if item.capability_family == family
            )
            expected_class = next(iter(family_classes[family]))
            if definition.primary_classification != expected_class or any(
                not _definition_covers(definition, item, surface.surface_id)
                for item in family_requirements
            ):
                incomplete_definitions.append(family)
            else:
                schemas.append(
                    definition.provider_schema(
                        tuple(item.operation_variant for item in family_requirements)
                    )
                )
        if implementation is None:
            missing_implementations.append(family)

    human_only = tuple(
        dict.fromkeys(
            plan.command_id
            for plan in action_inventory.plans
            if plan.classification.human_only
        )
    ) + tuple(
        plan.action_id
        for plan in context_actions_for_surface(surface.surface_id)
        if plan.classification.human_only
    )
    complete = not (
        missing_action_ids
        or missing_definitions
        or missing_implementations
        or incomplete_definitions
    )
    if complete:
        schema_bytes = len(
            json.dumps(
                schemas,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        if schema_bytes > MAX_NATIVE_SCHEMAS_JSON_BYTES:
            raise NativeCapabilityRegistryError(
                f"Native surface schemas use {schema_bytes} bytes; limit is "
                f"{MAX_NATIVE_SCHEMAS_JSON_BYTES}."
            )
    else:
        schemas = []

    return NativeProviderSurface(
        snapshot=snapshot,
        available=complete,
        unavailable_reason=(
            "" if complete else "Native mode is not yet complete for this ribbon."
        ),
        tool_names=families if complete else (),
        schemas=tuple(schemas),
        human_only_action_ids=human_only,
        missing_definition_names=tuple(missing_definitions),
        missing_implementation_names=tuple(missing_implementations),
        incomplete_definition_names=tuple(incomplete_definitions),
        missing_action_ids=missing_action_ids,
    )
