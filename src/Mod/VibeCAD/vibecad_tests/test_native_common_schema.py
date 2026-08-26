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
    "drawing.sources",
    "drawing.projected_geometry",
    "document.save",
    "document.undo",
)


def _schema(definition):
    return definition.provider_schema(
        tuple(variant.operation for variant in definition.variants)
    )


def test_common_registry_uses_small_intent_focused_tools() -> None:
    registry = NativeCapabilityRegistry()
    register_common_capability_definitions(registry)

    assert registry.definition_names == tuple(sorted(EXPECTED_COMMON_TOOLS))
    assert registry.shared_definition_names == EXPECTED_COMMON_TOOLS
    assert registry.implementation_names == ()
    assert COMMON_NATIVE_SURFACES == SURFACE_IDS - {"unavailable"}


def test_geometry_inspection_names_the_source_and_projection_index_conventions() -> None:
    definitions = {item.name: item for item in common_capability_definitions()}

    assert "Face1, Edge1, or Vertex1" in definitions["inspect.query"].description
    assert "Face0, Edge0, or Vertex0" in definitions[
        "drawing.projected_geometry"
    ].description


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
        if definition.name
        not in {
            "document.save",
            "drawing.sources",
            "drawing.projected_geometry",
        }
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
    drawing_sources = variants[("drawing.sources", "list")]
    assert drawing_sources.surface_ids == frozenset({"drawing"})
    assert drawing_sources.provider_supplemental is True
    drawing_projection = variants[("drawing.projected_geometry", "read")]
    assert drawing_projection.surface_ids == frozenset({"drawing"})
    assert drawing_projection.provider_supplemental is True
    assert DOCUMENT_SAVE_SURFACES == COMMON_NATIVE_SURFACES - {"sketch.edit"}
    assert all(
        variant.surface_ids == DOCUMENT_SAVE_SURFACES
        for variant in next(
            definition for definition in definitions if definition.name == "document.save"
        ).variants
    )
    assert [variant.operation for variant in definitions[0].variants] == [
        "active",
        "selection",
    ]
    assert [variant.operation for variant in definitions[1].variants] == [
        "fit_all",
        "isometric",
        "set_isometric",
        "set_front",
        "set_rear",
        "set_left",
        "set_right",
        "set_top",
        "set_bottom",
        "set_grid",
        "set_section_view",
        "set_object_visibility",
        "capture_all",
        "capture_selection",
        "capture_objects",
        "capture_active_sketch",
    ]
    assert all(
        variant.transaction_behavior == "presentation"
        for variant in definitions[1].variants[:11]
    )
    set_grid = next(
        variant
        for variant in definitions[1].variants
        if variant.operation == "set_grid"
    )
    assert set_grid.parameters["required"] == []
    assert set_grid.parameters["properties"]["visible"]["default"] is True
    assert [variant.operation for variant in definitions[2].variants] == [
        "distance",
        "angle",
        "radius",
        "mass_properties",
        "inspection_result",
        "element",
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
    assert (element_targets["minItems"], element_targets["maxItems"]) == (1, 256)
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

    variants = {
        variant.operation: variant
        for variant in definitions["inspect.query"].variants
    }
    assert variants["element"].description == (
        "Read each exact subelement's type and available size, endpoints, radius, "
        "center, or normal."
    )
    assert variants["radius"].description == (
        "Measure known circular edges or cylindrical faces in mm."
    )

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


def test_first_projected_geometry_page_needs_no_prior_projection_hash() -> None:
    definition = next(
        item
        for item in common_capability_definitions()
        if item.name == "drawing.projected_geometry"
    )
    branch = definition.provider_schema(("read",))[
        "parameters"
    ]["oneOf"][0]

    assert branch["required"] == ["view"]
    assert branch["properties"]["offset"]["default"] == 0
    assert "page_size" not in branch["properties"]
    assert branch["properties"]["view"]["required"] == ["object_name"]
    assert branch["properties"]["view"]["properties"][
        "expected_state_sha256"
    ]["default"] == ""
    assert branch["properties"]["view"]["properties"][
        "expected_projection_state_sha256"
    ][
        "default"
    ] == ""
    assert "expected_projection_state_sha256" not in branch["properties"]


def test_drawing_source_pages_are_server_sized() -> None:
    definition = next(
        item
        for item in common_capability_definitions()
        if item.name == "drawing.sources"
    )
    branch = definition.provider_schema(("list",))["parameters"]["oneOf"][0]

    assert branch["properties"]["offset"]["default"] == 0
    assert "page_size" not in branch["properties"]


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
