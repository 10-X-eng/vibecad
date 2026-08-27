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


MAX_NATIVE_SCHEMAS_JSON_BYTES = 64 * 1024
# Analyze resolves its complete registry before exact study state projects the
# much smaller turn surface. This ceiling protects registry completeness; the
# scoped turn still obeys the session's 128-KiB transport limit.
# Manufacture also resolves its complete registry before exact setup state
# projects the smaller turn surface. Its full inventory stays below 128 KiB;
# only the projected setup-relevant tools are sent to the provider.
# Drawing likewise owns more than one hundred shipped actions. Its focused
# families use the same compact provider form as Model: an exact operation
# field map on the wire followed by validation against the selected variant's
# original closed schema before dispatch. The surface stays below the provider
# transport ceiling while retaining the focused tools requested during live
# acceptance.
MAX_NATIVE_SCHEMAS_JSON_BYTES_BY_SURFACE = {
    "analyze": 160 * 1024,
    "manufacture": 128 * 1024,
    "drawing": 120 * 1024,
}
_CAPABILITY_NAME = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
_VARIANT_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_PRIMARY_CLASSES = frozenset({"read", "mutation", "view", "export"})


class NativeCapabilityRegistryError(RuntimeError):
    """A Native capability declaration violates the registry contract."""


def _compact_integral_json_numbers(value: Any) -> Any:
    """Use JSON integers for integral schema numbers without changing semantics."""
    if (
        type(value) is float
        and math.isfinite(value)
        and value.is_integer()
        and abs(value) <= 9_007_199_254_740_991
    ):
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
    branch_fields = []
    for branch in branches:
        properties = tuple(
            name for name in branch["properties"] if name != "operation"
        )
        required = tuple(
            name
            for name in branch.get("required", ())
            if name != "operation"
        )
        optional = tuple(name for name in properties if name not in required)
        branch_fields.append((required, optional))
    field_groups = set(branch_fields)
    common_required: tuple[str, ...] = ()
    common_optional: tuple[str, ...] = ()
    if len(field_groups) > 1:
        common_required = tuple(
            name
            for name in branch_fields[0][0]
            if all(name in required for required, _optional in branch_fields[1:])
        )
        common_optional = tuple(
            name
            for name in branch_fields[0][1]
            if all(name in optional for _required, optional in branch_fields[1:])
        )
    grouped: dict[tuple[tuple[str, ...], tuple[str, ...]], list[str]] = {}
    for operation, (required, optional) in zip(
        operations,
        branch_fields,
        strict=True,
    ):
        required = tuple(
            name for name in required if name not in common_required
        )
        optional = tuple(
            name for name in optional if name not in common_optional
        )
        grouped.setdefault((required, optional), []).append(operation)
    groups = list(grouped.items())
    dominant: tuple[tuple[str, ...], tuple[str, ...]] | None = None
    if len(groups) > 1:
        candidate = max(groups, key=lambda item: len(item[1]))
        if len(candidate[1]) >= 5 and len(operations) - len(candidate[1]) <= 3:
            dominant = candidate[0]
            groups = [item for item in groups if item[0] != dominant]
            groups.append(candidate)
    rendered: dict[str, dict[str, Any]] = {}
    for (required, optional), group_operations in groups:
        fields = ",".join(required) if required else "none"
        if optional:
            fields += (
                f"; optional {','.join(optional)}"
                if len(optional) <= 4
                else "+schema options"
            )
        record = rendered.setdefault(
            fields,
            {"operations": [], "dominant": False, "group_count": 0},
        )
        record["operations"].extend(group_operations)
        record["dominant"] = record["dominant"] or dominant == (required, optional)
        record["group_count"] += 1
    entries = []
    # The enclosing object already marks fields shared by every operation as
    # required or optional. Repeating them in the discriminator wastes provider
    # context and makes the operation-specific map harder to scan.
    for fields, record in rendered.items():
        if len(rendered) == 1:
            entries.append(fields)
        elif record["dominant"] and record["group_count"] == 1:
            entries.append(f"otherwise={fields}")
        else:
            entries.append(f"{'|'.join(record['operations'])}={fields}")
    return "Fields: " + "; ".join(entries) + "."


def _serialized_schema(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _provider_without_internal_state_fields(value: Any) -> Any:
    if isinstance(value, list):
        return [_provider_without_internal_state_fields(item) for item in value]
    if not isinstance(value, Mapping):
        return value
    result = {
        str(key): _provider_without_internal_state_fields(item)
        for key, item in value.items()
    }
    properties = result.get("properties")
    if isinstance(properties, dict):
        internal = {
            name
            for name in properties
            if name.startswith("expected_") and name.endswith("sha256")
        }
        for name in internal:
            properties.pop(name, None)
        required = result.get("required")
        if isinstance(required, list):
            result["required"] = [name for name in required if name not in internal]
    return result


def provider_visible_native_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Publish one compact provider schema while retaining exact dispatch validation."""

    projected = json.loads(_serialized_schema(schema))
    if str(projected.get("name") or "").startswith("drawing."):
        projected = _provider_without_internal_state_fields(projected)
    parameters = projected.get("parameters")
    branches = parameters.get("oneOf") if isinstance(parameters, Mapping) else None
    if (
        isinstance(branches, list)
        and len(branches) == 1
        and isinstance(branches[0], dict)
    ):
        branch = branches[0]
        properties = branch.get("properties")
        required = branch.get("required", [])
        operation = (
            properties.get("operation") if isinstance(properties, dict) else None
        )
        if (
            isinstance(operation, Mapping)
            and "const" in operation
            and "operation" not in required
        ):
            properties.pop("operation", None)
    return _compact_nested_provider_unions(projected)


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
    labels = _closed_object_discriminator_labels(tuple(unique))
    discriminated = (
        _compact_closed_object_options(
            tuple("/".join(label) for label in labels),
            tuple(unique),
        )
        if labels is not None
        else None
    )
    candidate = (
        discriminated
        or _numeric_union(unique)
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


def _closed_object_discriminator_labels(
    branches: tuple[Mapping[str, Any], ...],
) -> tuple[tuple[str, ...], ...] | None:
    """Return labels for one shared explicit string discriminator."""

    if not branches or any(
        branch.get("type") != "object"
        or branch.get("additionalProperties") is not False
        or not isinstance(branch.get("properties"), Mapping)
        for branch in branches
    ):
        return None
    common_required = set(branches[0].get("required", ()))
    for branch in branches[1:]:
        common_required.intersection_update(branch.get("required", ()))
    for name in branches[0].get("required", ()):
        if name not in common_required:
            continue
        labels = []
        for branch in branches:
            discriminator = branch["properties"].get(name)
            if not isinstance(discriminator, Mapping) or discriminator.get(
                "type"
            ) != "string":
                break
            values = discriminator.get("enum", [discriminator.get("const")])
            if (
                not isinstance(values, list)
                or not values
                or any(
                    not isinstance(value, str) or not value for value in values
                )
            ):
                break
            labels.append(tuple(values))
        if len(labels) == len(branches):
            return tuple(labels)
    return None


def _compact_nested_provider_unions(value: Any) -> Any:
    """Compact repeated nested provider branches without weakening dispatch.

    Multi-operation families can otherwise repeat the same exact target and
    machining grammars many times inside a top-level field union. The provider
    receives one bounded closed field union and an exact per-kind field map;
    Native dispatch still validates the call against the selected variant's
    original closed schema before it creates a ticket or runs an implementation.
    """

    if isinstance(value, list):
        return [_compact_nested_provider_unions(item) for item in value]
    if not isinstance(value, Mapping):
        return value

    if set(value) <= {"anyOf", "description"} and isinstance(
        value.get("anyOf"), list
    ):
        flattened: list[Mapping[str, Any]] = []
        for option in value["anyOf"]:
            if not isinstance(option, Mapping):
                flattened = []
                break
            if set(option) == {"oneOf"} and isinstance(option.get("oneOf"), list):
                branches = option["oneOf"]
            elif set(option) == {"anyOf"} and isinstance(
                option.get("anyOf"), list
            ):
                branches = option["anyOf"]
            else:
                branches = [option]
            if any(not isinstance(branch, Mapping) for branch in branches):
                flattened = []
                break
            flattened.extend(branches)
        if flattened:
            compact = _compact_schema_options(
                tuple(
                    _compact_nested_provider_unions(branch)
                    for branch in flattened
                )
            )
            if "description" in value:
                compact["description"] = value["description"]
            if len(_serialized_schema(compact)) < len(_serialized_schema(value)):
                return compact

    result = {
        key: _compact_nested_provider_unions(item)
        for key, item in value.items()
    }
    if set(result) <= {"oneOf", "description"} and isinstance(
        result.get("oneOf"), list
    ):
        branches = result["oneOf"]
        labels = (
            _closed_object_discriminator_labels(tuple(branches))
            if all(isinstance(branch, Mapping) for branch in branches)
            else None
        )
        if labels is not None:
            compact = _compact_closed_object_options(
                tuple("/".join(label) for label in labels),
                tuple(branches),
            )
            if compact is not None:
                if "description" in result:
                    compact["description"] = (
                        f"{result['description']} {compact['description']}"
                    )
                if len(_serialized_schema(compact)) < len(
                    _serialized_schema(result)
                ):
                    return compact
    return result


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
    parameters = _compact_nested_provider_unions({
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    })
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
    provider_supplemental: bool = False
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
        if type(self.provider_supplemental) is not bool:
            raise NativeCapabilityRegistryError(
                f"Capability variant {self.operation!r} has invalid supplemental state."
            )
        object.__setattr__(self, "_parameters_json", _canonical_schema(self.parameters))

    def provider_parameters(self, *, require_operation: bool = True) -> dict[str, Any]:
        parameters = json.loads(self._parameters_json)
        properties = dict(parameters["properties"])
        properties["operation"] = {
            "type": "string",
            "const": self.operation,
            "description": self.description,
        }
        parameters["properties"] = {"operation": properties.pop("operation"), **properties}
        required = [
            name for name in parameters.get("required", []) if name != "operation"
        ]
        parameters["required"] = (
            ["operation", *required] if require_operation else required
        )
        return parameters


@dataclass(frozen=True, slots=True)
class NativeCapabilityDefinition:
    name: str
    description: str
    primary_classification: str
    variants: tuple[NativeCapabilityVariant, ...]
    preserve_operation_branches: bool = False

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
        if type(self.preserve_operation_branches) is not bool:
            raise NativeCapabilityRegistryError(
                f"Capability {self.name!r} has invalid branch-preservation state."
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
            variants[operation].provider_parameters(
                require_operation=len(ordered) != 1,
            )
            for operation in ordered
        )
        return {
            "name": self.name,
            "description": self.description,
            "parameters": (
                {"oneOf": list(branches)}
                if len(branches) == 1 or self.preserve_operation_branches
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


def project_native_provider_surface(
    surface: NativeProviderSurface,
    tool_names: tuple[str, ...],
) -> NativeProviderSurface:
    """Select exact families from one already-validated complete surface."""

    if not isinstance(surface, NativeProviderSurface):
        raise TypeError("surface must be a NativeProviderSurface")
    if not surface.available:
        return surface
    requested = tuple(str(name or "").strip() for name in tool_names)
    if not requested or any(not name for name in requested):
        raise NativeCapabilityRegistryError(
            "A projected Native surface requires at least one exact tool name."
        )
    if len(requested) != len(set(requested)):
        raise NativeCapabilityRegistryError(
            "A projected Native surface cannot repeat tool names."
        )
    missing = sorted(set(requested) - set(surface.tool_names))
    if missing:
        raise NativeCapabilityRegistryError(
            "Projected tools are outside the complete Native surface: "
            + ", ".join(missing)
            + "."
        )
    selected = set(requested)
    names = tuple(name for name in surface.tool_names if name in selected)
    schemas = tuple(
        schema
        for name, schema in zip(surface.tool_names, surface.schemas, strict=True)
        if name in selected
    )
    return NativeProviderSurface(
        snapshot=surface.snapshot,
        available=True,
        unavailable_reason="",
        tool_names=names,
        schemas=schemas,
        human_only_action_ids=surface.human_only_action_ids,
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
        missing_action_ids=(),
    )


def _provider_schema_operations(schema: Mapping[str, Any]) -> tuple[str, ...]:
    parameters = schema.get("parameters")
    if not isinstance(parameters, Mapping):
        return ()
    branches = parameters.get("oneOf")
    candidates = branches if isinstance(branches, list) else [parameters]
    result = []
    for branch in candidates:
        if not isinstance(branch, Mapping):
            continue
        properties = branch.get("properties")
        operation = (
            properties.get("operation")
            if isinstance(properties, Mapping)
            else None
        )
        if not isinstance(operation, Mapping):
            continue
        values = (
            [operation.get("const")]
            if "const" in operation
            else list(operation.get("enum") or ())
        )
        for value in values:
            clean = str(value or "").strip()
            if clean and clean not in result:
                result.append(clean)
    return tuple(result)


def _authorized_provider_schema_operations(
    schema: Mapping[str, Any],
    definition: NativeCapabilityDefinition,
) -> tuple[str, ...]:
    """Recover the exact authorized variants, including a focused singleton schema."""

    operations = _provider_schema_operations(schema)
    if operations:
        return operations
    matches = tuple(
        variant.operation
        for variant in definition.variants
        if provider_visible_native_schema(
            definition.provider_schema((variant.operation,))
        )
        == schema
    )
    return matches if len(matches) == 1 else ()


def project_native_provider_operations(
    surface: NativeProviderSurface,
    registry: NativeCapabilityRegistry,
    operations_by_tool: Mapping[str, tuple[str, ...]],
) -> NativeProviderSurface:
    """Select exact operation variants from an already-authorized surface."""

    if not isinstance(surface, NativeProviderSurface):
        raise TypeError("surface must be a NativeProviderSurface")
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    if not isinstance(operations_by_tool, Mapping):
        raise TypeError("operations_by_tool must be a mapping")
    if not surface.available:
        return surface
    names = []
    schemas = []
    for name, schema in zip(surface.tool_names, surface.schemas, strict=True):
        requested = operations_by_tool.get(name)
        if requested is None:
            names.append(name)
            schemas.append(schema)
            continue
        operations = tuple(dict.fromkeys(str(value) for value in requested if value))
        if not operations:
            continue
        definition = registry.definition(name)
        if definition is None:
            raise NativeCapabilityRegistryError(
                f"Projected Native capability {name!r} has no definition."
            )
        allowed = set(_authorized_provider_schema_operations(schema, definition))
        if not set(operations) <= allowed:
            raise NativeCapabilityRegistryError(
                f"Projected operations for {name!r} exceed its authorized surface."
            )
        names.append(name)
        schemas.append(definition.provider_schema(operations))
    if not names:
        raise NativeCapabilityRegistryError(
            "A projected Native operation surface requires at least one tool."
        )
    return NativeProviderSurface(
        snapshot=surface.snapshot,
        available=True,
        unavailable_reason="",
        tool_names=tuple(names),
        schemas=tuple(schemas),
        human_only_action_ids=surface.human_only_action_ids,
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
        missing_action_ids=(),
    )


@dataclass(frozen=True, slots=True)
class _RequiredAction:
    action_id: str
    capability_family: str
    operation_variant: str
    primary_classification: str
    exact_target_type: str | None
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
                plan.exact_target_type,
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
                plan.exact_target_type,
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
                    variant.exact_target_type,
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
        and (
            requirement.exact_target_type is None
            or variant.exact_target_type == requirement.exact_target_type
        )
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
                operations = [
                    item.operation_variant for item in family_requirements
                ]
                operations.extend(
                    variant.operation
                    for variant in definition.variants
                    if variant.provider_supplemental
                    and surface.surface_id in variant.surface_ids
                )
                schemas.append(
                    definition.provider_schema(
                        tuple(dict.fromkeys(operations))
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
        schema_limit = MAX_NATIVE_SCHEMAS_JSON_BYTES_BY_SURFACE.get(
            surface.surface_id,
            MAX_NATIVE_SCHEMAS_JSON_BYTES,
        )
        provider_schemas = [provider_visible_native_schema(schema) for schema in schemas]
        schema_bytes = len(
            json.dumps(
                provider_schemas,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        if schema_bytes > schema_limit:
            family_sizes = sorted(
                (
                    (
                        str(schema.get("name") or "unknown"),
                        len(
                            json.dumps(
                                schema,
                                ensure_ascii=True,
                                sort_keys=True,
                                separators=(",", ":"),
                            ).encode("utf-8")
                        ),
                    )
                    for schema in schemas
                ),
                key=lambda item: (-item[1], item[0]),
            )
            largest = ", ".join(
                f"{name}={size}" for name, size in family_sizes[:5]
            )
            raise NativeCapabilityRegistryError(
                f"Native surface schemas use {schema_bytes} bytes; limit is "
                f"{schema_limit}. Largest families: {largest}."
            )
        schemas = provider_schemas
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
