# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json

from VibeCADNativeCapabilityRegistry import NativeCapabilityRegistry
from VibeCADNativeCommonSchema import (
    COMMON_NATIVE_SURFACES,
    DOCUMENT_SAVE_SURFACES,
    common_capability_definitions,
    register_common_capability_definitions,
)
from VibeCADRibbonSurface import SURFACE_IDS


EXPECTED_COMMON_TOOLS = (
    "state.read",
    "view.control",
    "inspect.query",
    "document.save",
    "document.undo",
)


def _schema(definition):
    return definition.provider_schema(
        tuple(variant.operation for variant in definition.variants)
    )


def test_common_registry_is_five_small_intent_focused_tools() -> None:
    registry = NativeCapabilityRegistry()
    register_common_capability_definitions(registry)

    assert registry.definition_names == tuple(sorted(EXPECTED_COMMON_TOOLS))
    assert registry.shared_definition_names == EXPECTED_COMMON_TOOLS
    assert registry.implementation_names == ()
    assert COMMON_NATIVE_SURFACES == SURFACE_IDS - {"unavailable"}


def test_common_variants_use_explicit_eligible_surfaces() -> None:
    definitions = common_capability_definitions()
    variants = {
        (definition.name, variant.operation): variant
        for definition in definitions
        for variant in definition.variants
    }

    assert all(
        variant.surface_ids == COMMON_NATIVE_SURFACES
        for definition in definitions
        if definition.name != "document.save"
        for variant in definition.variants
        if variant.operation
        not in {
            "drawing_projected_geometry",
            "set_object_visibility",
            "capture_active_sketch",
        }
    )
    assert variants[("view.control", "set_object_visibility")].surface_ids == frozenset(
        {"model"}
    )
    assert variants[("view.control", "capture_active_sketch")].surface_ids == frozenset(
        {"sketch.edit"}
    )
    drawing_projection = variants[("inspect.query", "drawing_projected_geometry")]
    assert drawing_projection.surface_ids == frozenset({"drawing"})
    assert drawing_projection.provider_supplemental is True
    assert DOCUMENT_SAVE_SURFACES == COMMON_NATIVE_SURFACES - {"sketch.edit"}
    assert all(
        variant.surface_ids == DOCUMENT_SAVE_SURFACES
        for variant in definitions[3].variants
    )
    assert [variant.operation for variant in definitions[0].variants] == [
        "active",
        "selection",
    ]
    assert [variant.operation for variant in definitions[1].variants] == [
        "fit_all",
        "isometric",
        "set_grid",
        "set_object_visibility",
        "capture_all",
        "capture_selection",
        "capture_objects",
        "capture_active_sketch",
    ]
    assert [
        variant.transaction_behavior for variant in definitions[1].variants[:3]
    ] == ["presentation", "presentation", "presentation"]
    assert [variant.operation for variant in definitions[2].variants] == [
        "distance",
        "angle",
        "radius",
        "mass_properties",
        "inspection_result",
        "element",
        "drawing_projected_geometry",
        "validity",
    ]
    assert definitions[2].preserve_operation_branches is False


def test_active_sketch_capture_is_only_advertised_during_sketch_edit() -> None:
    definitions = {
        definition.name: definition for definition in common_capability_definitions()
    }
    capture = next(
        variant
        for variant in definitions["view.control"].variants
        if variant.operation == "capture_active_sketch"
    )

    assert capture.surface_ids == frozenset({"sketch.edit"})


def test_exact_targets_and_arrays_are_bounded_in_final_common_schemas() -> None:
    definitions = {
        definition.name: definition for definition in common_capability_definitions()
    }
    inspect_branches = {
        operation: definitions["inspect.query"].provider_schema((operation,))[
            "parameters"
        ]["oneOf"][0]
        for operation in ("element", "mass_properties")
    }
    element_targets = inspect_branches["element"]["properties"]["targets"]
    assert (element_targets["minItems"], element_targets["maxItems"]) == (1, 1)
    element = element_targets["items"]
    assert element["additionalProperties"] is False
    assert element["properties"]["subelement"]["pattern"].startswith("^")
    assert element["properties"]["object_name"]["maxLength"] == 128
    mass_targets = inspect_branches["mass_properties"]["properties"]["targets"]
    assert (
        mass_targets["minItems"],
        mass_targets["maxItems"],
        mass_targets["uniqueItems"],
    ) == (
        1,
        16,
        True,
    )
    assert mass_targets["items"]["required"] == ["object_name"]
    assert set(mass_targets["items"]["properties"]) == {"object_name"}

    combined = definitions["inspect.query"].provider_schema(
        ("mass_properties", "element")
    )["parameters"]
    assert "oneOf" not in combined
    assert combined["properties"]["targets"]["type"] == "array"
    combined_target = combined["properties"]["targets"]["items"]
    assert combined_target["additionalProperties"] is False
    assert set(combined_target["properties"]) == {"object_name", "subelement"}
    assert combined_target["required"] == ["object_name"]
    capture_branches = {
        "capture_objects": definitions["view.control"].provider_schema(
            ("capture_objects",)
        )["parameters"]["oneOf"][0]
    }
    assert capture_branches["capture_objects"]["properties"]["targets"][
        "maxItems"
    ] == 16


def test_host_bookkeeping_is_not_exposed_as_provider_arguments() -> None:
    serialized = json.dumps(
        [_schema(definition) for definition in common_capability_definitions()],
        sort_keys=True,
    )

    for forbidden in (
        "document_uid",
        "expected_revision",
        "idempotency_token",
        "surface_id",
        "workbench",
        "command_id",
        "runCommand",
    ):
        assert forbidden not in serialized


def test_save_and_undo_have_no_save_as_redo_or_arbitrary_history_variant() -> None:
    definitions = {value.name: value for value in common_capability_definitions()}

    assert [value.operation for value in definitions["document.save"].variants] == [
        "existing_path"
    ]
    assert [value.operation for value in definitions["document.undo"].variants] == [
        "assistant_local"
    ]
    assert definitions["document.save"].primary_classification == "export"
    assert definitions["document.undo"].primary_classification == "mutation"
