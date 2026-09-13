# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

import VibeCADNativeActionManifest as action_manifest_module
import VibeCADNativeCapabilityRegistry as registry_module
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
    NativeCapabilityRegistryError,
    NativeCapabilityVariant,
    NativeProviderSurface,
    project_native_provider_operations,
    provider_visible_native_schema,
    resolve_native_provider_surface,
)
from VibeCADRibbonSurface import RibbonSurface

from vibecad_tests.test_ribbon_surface import _manifest


def _parameters(**properties: object) -> dict[str, object]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _variant(
    operation: str,
    action_id: str | None,
    *,
    transaction_behavior: str,
    exact_target_type: str | None = None,
    parameters: dict[str, object] | None = None,
    action_ids: frozenset[str] | None = None,
    provider_supplemental: bool = False,
) -> NativeCapabilityVariant:
    return NativeCapabilityVariant(
        operation=operation,
        description=f"Perform {operation.replace('_', ' ')}.",
        action_ids=action_ids or frozenset({str(action_id)}),
        surface_ids=frozenset({"model"}),
        exact_target_type=exact_target_type,
        transaction_behavior=transaction_behavior,
        background_required=False,
        parameters=parameters or _parameters(),
        provider_supplemental=provider_supplemental,
    )


def _primitive_definition() -> NativeCapabilityDefinition:
    names = (
        "Box",
        "Cylinder",
        "Sphere",
        "Cone",
        "Ellipsoid",
        "Torus",
        "Prism",
        "Wedge",
        "Tube",
    )
    return NativeCapabilityDefinition(
        name="model.primitive",
        description="Create one exact solid primitive.",
        primary_classification="mutation",
        variants=tuple(
            _variant(
                name.lower(),
                f"PartDesign::Design{name}",
                transaction_behavior="document",
            )
            for name in names
        ),
    )
def _inspection_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name="inspect.query",
        description="Check exact target geometry validity.",
        primary_classification="read",
        variants=(
            _variant(
                "validity",
                "Part_CheckGeometry",
                transaction_behavior="none",
                exact_target_type="Part::Feature",
                parameters=_parameters(
                    target={
                        "type": "object",
                        "properties": {
                            "object_name": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 128,
                            }
                        },
                        "required": ["object_name"],
                        "additionalProperties": False,
                    }
                ),
            ),
        ),
    )


def _register_complete() -> NativeCapabilityRegistry:
    registry = NativeCapabilityRegistry()
    for definition in (
        _primitive_definition(),
        _inspection_definition(),
    ):
        registry.register_definition(definition)
        registry.register_implementation(
            NativeCapabilityImplementation(
                definition.name,
                lambda _arguments: {"result": "test-only"},
            )
        )
    return registry


def _surface() -> RibbonSurface:
    return RibbonSurface.from_manifest(_manifest(), revision=9)


def _focused_inventory_by_surface() -> dict[str, tuple[str, ...]]:
    return {"model": _surface().command_ids}


@pytest.fixture(autouse=True)
def _use_the_focused_fixture_as_this_module_surface_inventory(monkeypatch) -> None:
    """Keep registry-unit fixtures small without weakening production checks."""

    monkeypatch.setattr(
        action_manifest_module,
        "KNOWN_ACTIONS_BY_SURFACE",
        _focused_inventory_by_surface(),
    )


def test_production_empty_registry_keeps_native_fully_disabled() -> None:
    surface = resolve_native_provider_surface(_surface())

    assert surface.available is False
    assert surface.tool_names == ()
    assert surface.schemas == ()
    assert surface.missing_definition_names == (
        "model.primitive",
        "inspect.query",
    )
    assert surface.missing_implementation_names == surface.missing_definition_names
    assert surface.incomplete_definition_names == ()
    assert surface.summary() == {
        "mode": "native",
        "surface_id": "model",
        "surface_revision": 9,
        "available": False,
        "tool_count": 0,
        "human_only_action_count": 2,
        "unavailable_reason": "Native mode is not yet complete for this ribbon.",
    }


def test_single_purpose_provider_tool_does_not_repeat_its_operation_name() -> None:
    definition = _inspection_definition()

    parameters = provider_visible_native_schema(
        definition.provider_schema(("validity",))
    )["parameters"]["oneOf"][0]

    assert "operation" not in parameters["properties"]
    assert parameters["required"] == ["target"]


def test_provider_compacts_nested_closed_style_union() -> None:
    definition = NativeCapabilityDefinition(
        name="model.focused",
        description="Perform one focused operation.",
        primary_classification="mutation",
        variants=(
            _variant(
                "first",
                "Focused_First",
                transaction_behavior="document",
                parameters=_parameters(
                    lead={
                        "oneOf": [
                            {
                                "type": "object",
                                "properties": {
                                    "style": {"type": "string", "const": "arc"},
                                    "radius_mm": {
                                        "type": "number",
                                        "exclusiveMinimum": 0,
                                    },
                                },
                                "required": ["style", "radius_mm"],
                                "additionalProperties": False,
                            },
                            {
                                "type": "object",
                                "properties": {
                                    "style": {"type": "string", "const": "line"},
                                    "length_mm": {
                                        "type": "number",
                                        "exclusiveMinimum": 0,
                                    },
                                },
                                "required": ["style", "length_mm"],
                                "additionalProperties": False,
                            },
                        ]
                    }
                ),
            ),
        ),
    )

    parameters = provider_visible_native_schema(
        definition.provider_schema(("first",))
    )["parameters"]["oneOf"][0]
    lead = parameters["properties"]["lead"]

    assert "oneOf" not in lead
    assert lead["properties"]["style"] == {
        "type": "string",
        "enum": ["arc", "line"],
    }
    assert lead["required"] == ["style"]
    assert "arc=radius_mm" in lead["description"]
    assert "line=length_mm" in lead["description"]


def test_operation_projection_accepts_an_exact_singleton_provider_schema() -> None:
    definition = NativeCapabilityDefinition(
        name="model.focused",
        description="Perform one focused operation.",
        primary_classification="mutation",
        variants=(
            _variant(
                "first",
                "Focused_First",
                transaction_behavior="document",
                parameters=_parameters(first_value={"type": "number"}),
            ),
            _variant(
                "second",
                "Focused_Second",
                transaction_behavior="document",
                parameters=_parameters(
                    second_value={"type": "string", "maxLength": 32}
                ),
            ),
        ),
    )
    registry = NativeCapabilityRegistry()
    registry.register_definition(definition)
    schema = provider_visible_native_schema(definition.provider_schema(("first",)))
    surface = NativeProviderSurface(
        snapshot=_surface(),
        available=True,
        unavailable_reason="",
        tool_names=(definition.name,),
        schemas=(schema,),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )

    projected = project_native_provider_operations(
        surface,
        registry,
        {definition.name: ("first",)},
    )

    assert provider_visible_native_schema(projected.schemas[0]) == schema


def test_partial_registry_never_advertises_a_partial_surface() -> None:
    registry = NativeCapabilityRegistry()
    definition = _primitive_definition()
    registry.register_definition(definition)
    registry.register_implementation(
        NativeCapabilityImplementation(definition.name, lambda _arguments: {})
    )

    surface = resolve_native_provider_surface(_surface(), registry)

    assert surface.available is False
    assert surface.tool_names == ()
    assert surface.schemas == ()
    assert surface.missing_definition_names == (
        "inspect.query",
    )
    assert surface.missing_implementation_names == surface.missing_definition_names


def test_complete_registry_emits_only_live_variants_in_live_family_order() -> None:
    surface = resolve_native_provider_surface(_surface(), _register_complete())

    assert surface.available is True
    assert surface.unavailable_reason == ""
    assert surface.tool_names == (
        "model.primitive",
        "inspect.query",
    )
    assert tuple(schema["name"] for schema in surface.schemas) == surface.tool_names
    assert surface.schemas[0]["parameters"]["properties"]["operation"]["enum"] == [
        "box",
        "cylinder",
        "sphere",
        "cone",
        "ellipsoid",
        "torus",
        "prism",
        "wedge",
        "tube",
    ]
    assert "operation" not in surface.schemas[1]["parameters"]["oneOf"][0][
        "properties"
    ]
    serialized = repr(surface.schemas)
    assert "PartDesign::DesignBox" not in serialized
    assert "command_id" not in serialized
    assert "runCommand" not in serialized


def test_complete_family_includes_declared_supplemental_operations() -> None:
    feature = NativeCapabilityDefinition(
        name="model.primitive",
        description="Create or configure one exact solid feature.",
        primary_classification="mutation",
        variants=(
            *_primitive_definition().variants,
            _variant(
                "configure",
                "VibeCAD_ModelConfigureFeature",
                transaction_behavior="document",
                provider_supplemental=True,
            ),
        ),
    )
    registry = NativeCapabilityRegistry()
    for definition in (
        feature,
        _inspection_definition(),
    ):
        registry.register_definition(definition)
        registry.register_implementation(
            NativeCapabilityImplementation(definition.name, lambda _arguments: {})
        )

    surface = resolve_native_provider_surface(_surface(), registry)

    assert surface.available is True
    assert surface.schemas[0]["parameters"]["properties"]["operation"]["enum"] == [
        "box",
        "cylinder",
        "sphere",
        "cone",
        "ellipsoid",
        "torus",
        "prism",
        "wedge",
        "tube",
        "configure",
    ]


def test_fixed_surface_fails_closed_when_a_manifest_required_action_is_missing() -> None:
    manifest = _manifest()
    manifest["groups"] = [
        group for group in manifest["groups"] if group["label"] != "Inspect"
    ]
    live = RibbonSurface.from_manifest(manifest, revision=10)

    surface = resolve_native_provider_surface(live, _register_complete())

    assert surface.available is False
    assert surface.tool_names == ()
    assert surface.schemas == ()
    assert surface.missing_action_ids == ("Part_CheckGeometry",)


def test_provider_keeps_operations_in_the_enum_without_description_duplication() -> None:
    definition = NativeCapabilityDefinition(
        name="model.primitive",
        description="Create an exact feature.",
        primary_classification="mutation",
        variants=(
            _variant("primitive", None, transaction_behavior="document"),
            _variant("profile", None, transaction_behavior="document"),
        ),
    )

    schema = definition.provider_schema(("primitive", "profile"))

    assert schema["description"] == "Create an exact feature."
    parameters = schema["parameters"]
    assert parameters["type"] == "object"
    assert parameters["additionalProperties"] is False
    assert parameters["required"] == ["operation"]
    operation = parameters["properties"]["operation"]
    assert operation["enum"] == ["primitive", "profile"]
    assert operation["description"] == (
        "Fields: none."
    )
    assert all(variant.description not in repr(schema) for variant in definition.variants)


def test_preserved_operation_branches_describe_the_selected_operation() -> None:
    definition = NativeCapabilityDefinition(
        name="model.exact",
        description="Apply one exact operation.",
        primary_classification="mutation",
        variants=(
            _variant(
                "create",
                "Part_Create",
                transaction_behavior="document",
                parameters=_parameters(label={"type": "string", "maxLength": 80}),
            ),
            _variant(
                "edit",
                "Part_Edit",
                transaction_behavior="document",
                parameters=_parameters(target={"type": "string", "maxLength": 80}),
            ),
        ),
        preserve_operation_branches=True,
    )

    branches = definition.provider_schema(("create", "edit"))["parameters"]["oneOf"]

    assert branches[0]["properties"]["operation"] == {
        "type": "string",
        "const": "create",
        "description": "Perform create.",
    }
    assert branches[1]["properties"]["operation"]["description"] == "Perform edit."


def test_compact_multi_operation_schema_keeps_closed_typed_field_union() -> None:
    definition = NativeCapabilityDefinition(
        name="model.compact",
        description="Apply one compact operation.",
        primary_classification="mutation",
        variants=(
            _variant(
                "number",
                "Part_Number",
                transaction_behavior="document",
                parameters=_parameters(value={"type": "number", "minimum": 0}),
            ),
            _variant(
                "name",
                "Part_Name",
                transaction_behavior="document",
                parameters=_parameters(
                    value={"type": "string", "maxLength": 32}
                ),
            ),
        ),
    )

    parameters = definition.provider_schema(("number", "name"))["parameters"]

    assert parameters["additionalProperties"] is False
    assert parameters["required"] == ["operation", "value"]
    assert set(parameters["properties"]) == {"operation", "value"}
    assert parameters["properties"]["operation"]["enum"] == ["number", "name"]
    assert parameters["properties"]["operation"]["description"] == (
        "Fields: value."
    )
    assert parameters["properties"]["value"]["anyOf"] == [
        {"minimum": 0, "type": "number"},
        {"maxLength": 32, "type": "string"},
    ]


def test_compact_multi_operation_schema_merges_repeated_closed_objects() -> None:
    definition = NativeCapabilityDefinition(
        name="model.compact",
        description="Apply one compact operation.",
        primary_classification="mutation",
        variants=(
            _variant(
                "first",
                "Part_First",
                transaction_behavior="document",
                parameters=_parameters(
                    definition={
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string", "const": "first"},
                            "distance": {"type": "number", "minimum": 0},
                        },
                        "required": ["kind", "distance"],
                        "additionalProperties": False,
                    }
                ),
            ),
            _variant(
                "second",
                "Part_Second",
                transaction_behavior="document",
                parameters=_parameters(
                    definition={
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string", "const": "second"},
                            "count": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 10,
                            },
                        },
                        "required": ["kind", "count"],
                        "additionalProperties": False,
                    }
                ),
            ),
        ),
    )

    definition_schema = definition.provider_schema(("first", "second"))[
        "parameters"
    ]["properties"]["definition"]

    assert definition_schema["type"] == "object"
    assert definition_schema["additionalProperties"] is False
    assert definition_schema["required"] == ["kind"]
    assert set(definition_schema["properties"]) == {"kind", "distance", "count"}
    assert definition_schema["properties"]["kind"] == {
        "enum": ["first", "second"],
        "type": "string",
    }
    assert definition_schema["description"] == (
        "Fields: first=distance; second=count."
    )


def test_compact_object_union_preserves_the_broadest_explicit_array_contract() -> None:
    definition = NativeCapabilityDefinition(
        name="model.compact",
        description="Apply one compact operation.",
        primary_classification="mutation",
        variants=(
            _variant(
                "short",
                "Part_Short",
                transaction_behavior="document",
                parameters=_parameters(
                    definition={
                        "type": "object",
                        "properties": {
                            "values": {
                                "type": "array",
                                "items": {
                                    "type": "number",
                                    "minimum": 0,
                                    "maximum": 4,
                                },
                                "minItems": 2,
                                "maxItems": 2,
                            }
                        },
                        "required": ["values"],
                        "additionalProperties": False,
                    }
                ),
            ),
            _variant(
                "long",
                "Part_Long",
                transaction_behavior="document",
                parameters=_parameters(
                    definition={
                        "type": "object",
                        "properties": {
                            "values": {
                                "type": "array",
                                "items": {
                                    "type": "number",
                                    "minimum": -10,
                                    "maximum": 10,
                                },
                                "minItems": 3,
                                "maxItems": 8,
                            }
                        },
                        "required": ["values"],
                        "additionalProperties": False,
                    }
                ),
            ),
        ),
    )

    values = definition.provider_schema(("short", "long"))["parameters"][
        "properties"
    ]["definition"]["properties"]["values"]

    assert values == {
        "type": "array",
        "items": {
            "type": "number",
            "minimum": -10,
            "maximum": 10,
        },
        "minItems": 2,
        "maxItems": 8,
    }


def test_wrong_action_coverage_fails_closed_without_schema_leakage() -> None:
    registry = _register_complete()
    wrong = NativeCapabilityDefinition(
        name="model.primitive",
        description="Create one exact solid feature variant.",
        primary_classification="mutation",
        variants=(
            _variant(
                "box",
                None,
                transaction_behavior="document",
                action_ids=frozenset(
                    {"PartDesign::WrongBox", "PartDesign::DesignCylinder"}
                ),
            ),
        ),
    )
    replacement = NativeCapabilityRegistry()
    replacement.register_definition(wrong)
    replacement.register_implementation(
        NativeCapabilityImplementation("model.primitive", lambda _arguments: {})
    )
    inspection = registry.definition("inspect.query")
    assert inspection is not None
    replacement.register_definition(inspection)
    replacement.register_implementation(
        NativeCapabilityImplementation("inspect.query", lambda _arguments: {})
    )

    surface = resolve_native_provider_surface(_surface(), replacement)

    assert surface.available is False
    assert surface.schemas == ()
    assert surface.incomplete_definition_names == ("model.primitive",)


def test_primary_classification_mismatch_fails_closed() -> None:
    registry = NativeCapabilityRegistry()
    wrong = NativeCapabilityDefinition(
        name="model.primitive",
        description="Read one exact feature.",
        primary_classification="read",
        variants=_primitive_definition().variants,
    )
    registry.register_definition(wrong)
    registry.register_implementation(
        NativeCapabilityImplementation("model.primitive", lambda _arguments: {})
    )

    surface = resolve_native_provider_surface(_surface(), registry)

    assert surface.available is False
    assert "model.primitive" in surface.incomplete_definition_names


def test_registry_rejects_duplicate_definition_and_implementation() -> None:
    registry = NativeCapabilityRegistry()
    definition = _primitive_definition()
    implementation = NativeCapabilityImplementation(
        definition.name,
        lambda _arguments: {},
    )
    registry.register_definition(definition)
    registry.register_implementation(implementation)

    with pytest.raises(NativeCapabilityRegistryError, match="already defined"):
        registry.register_definition(definition)
    with pytest.raises(NativeCapabilityRegistryError, match="already has"):
        registry.register_implementation(implementation)


def test_variant_schema_rejects_generic_operation_or_open_objects() -> None:
    with pytest.raises(NativeCapabilityRegistryError, match="owns the operation"):
        _variant(
            "bad",
            "Part_Bad",
            transaction_behavior="document",
            parameters=_parameters(operation={"type": "string"}),
        )
    with pytest.raises(NativeCapabilityRegistryError, match="additional properties"):
        _variant(
            "bad",
            "Part_Bad",
            transaction_behavior="document",
            parameters={"type": "object", "properties": {}},
        )


def test_canonical_schema_uses_compact_integral_json_numbers() -> None:
    variant = _variant(
        "bounded",
        "Part_Bounded",
        transaction_behavior="document",
        parameters=_parameters(
            value={
                "type": "number",
                "minimum": -1_000_000.0,
                "maximum": 1.5,
            }
        ),
    )

    number = variant.provider_parameters()["properties"]["value"]

    assert number["minimum"] == -1_000_000
    assert type(number["minimum"]) is int
    assert number["maximum"] == 1.5
    assert type(number["maximum"]) is float


def test_canonical_schema_keeps_large_bounds_in_scientific_json() -> None:
    variant = _variant(
        "bounded",
        "Part_Bounded",
        transaction_behavior="document",
        parameters=_parameters(
            value={"type": "number", "minimum": -1.0e30, "maximum": 1.0e30}
        ),
    )

    number = variant.provider_parameters()["properties"]["value"]

    assert number == {"type": "number", "minimum": -1.0e30, "maximum": 1.0e30}
    assert type(number["minimum"]) is float
    assert type(number["maximum"]) is float


def test_schema_budgets_fail_before_advertisement(monkeypatch) -> None:
    monkeypatch.setattr(registry_module, "MAX_NATIVE_SCHEMAS_JSON_BYTES", 10)
    with pytest.raises(NativeCapabilityRegistryError, match="schemas use"):
        resolve_native_provider_surface(_surface(), _register_complete())

    monkeypatch.setattr(registry_module, "MAX_NATIVE_SCHEMAS_JSON_BYTES", 64 * 1024)
    monkeypatch.setattr(
        registry_module,
        "MAX_NATIVE_SCHEMAS_JSON_BYTES_BY_SURFACE",
        {"model": 10},
    )
    with pytest.raises(NativeCapabilityRegistryError, match="limit is 10"):
        resolve_native_provider_surface(_surface(), _register_complete())


def test_registry_exposes_no_activation_or_generic_dispatch_api() -> None:
    public_names = {
        name for name in vars(registry_module) if not name.startswith("_")
    }
    forbidden_fragments = ("activate", "switch", "dispatch", "run_command", "execute")
    assert not any(
        fragment in name.lower()
        for name in public_names
        for fragment in forbidden_fragments
    )
