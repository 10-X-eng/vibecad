# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json

from VibeCADNativeModelFeatureSchema import model_feature_capability_definition
from VibeCADNativeModelPrimitiveSchema import (
    model_box_capability_definition,
    model_cylinder_capability_definition,
    model_primitive_capability_definition,
)


EXPECTED_KINDS = (
    "box",
    "cylinder",
    "sphere",
    "cone",
    "ellipsoid",
    "torus",
    "prism",
    "wedge",
    "tube",
)


def _contract():
    definition = model_feature_capability_definition()
    schema = definition.provider_schema(("primitive",))
    branch = schema["parameters"]["oneOf"][0]
    return definition, branch, branch["properties"]["definition"]


def test_model_feature_contract_covers_each_current_design_primitive() -> None:
    definition, branch, primitive = _contract()

    assert definition.name == "model.feature"
    assert branch["properties"]["operation"]["const"] == "primitive"
    assert tuple(primitive["properties"]["kind"]["enum"]) == EXPECTED_KINDS
    variant = next(item for item in definition.variants if item.operation == "primitive")
    assert variant.surface_ids == frozenset({"model"})
    assert variant.action_ids == frozenset(
        f"PartDesign::Design{name.title()}" for name in EXPECTED_KINDS
    )


def test_focused_primitive_contract_has_natural_exact_operations() -> None:
    definition = model_primitive_capability_definition()
    operations = tuple(variant.operation for variant in definition.variants)
    schema = definition.provider_schema(operations)
    parameters = schema["parameters"]

    assert definition.name == "model.primitive"
    assert operations == EXPECTED_KINDS[2:]
    assert parameters["properties"]["operation"]["enum"] == list(EXPECTED_KINDS[2:])
    assert parameters["required"] == ["operation", "label", "center_mm"]
    field_map = parameters["properties"]["operation"]["description"]
    assert "tube=outer_radius_mm,inner_radius_mm,height_mm" in field_map

    box = model_box_capability_definition()
    box_branch = box.provider_schema(("box",))["parameters"]["oneOf"][0]
    assert box.name == "model.box"
    assert box_branch["required"] == [
        "label",
        "center_mm",
        "length_mm",
        "width_mm",
        "height_mm",
    ]
    assert "definition" not in box_branch["properties"]

    cylinder = model_cylinder_capability_definition()
    cylinder_branch = cylinder.provider_schema(("cylinder",))["parameters"]["oneOf"][0]
    assert cylinder.name == "model.cylinder"
    assert cylinder_branch["required"] == [
        "label",
        "center_mm",
        "radius_mm",
        "height_mm",
    ]
    assert "definition" not in cylinder_branch["properties"]


def test_focused_primitive_variants_enforce_each_shape_before_mutation() -> None:
    definitions = (
        model_box_capability_definition(),
        model_cylinder_capability_definition(),
        model_primitive_capability_definition(),
    )
    selectors = {
        variant.operation: variant
        for definition in definitions
        for variant in definition.variants
    }

    assert tuple(selectors) == EXPECTED_KINDS
    assert selectors["box"].parameters["required"] == [
        "label",
        "center_mm",
        "length_mm",
        "width_mm",
        "height_mm",
    ]
    assert selectors["tube"].parameters["required"] == [
        "label",
        "center_mm",
        "outer_radius_mm",
        "inner_radius_mm",
        "height_mm",
    ]
    assert {
        action_id
        for definition in definitions
        for variant in definition.variants
        for action_id in variant.action_ids
    } == {f"PartDesign::Design{name.title()}" for name in EXPECTED_KINDS}
    assert all(
        "rotation" in variant.parameters["properties"]
        and "rotation" not in variant.parameters["required"]
        for definition in definitions
        for variant in definition.variants
    )


def test_every_primitive_requires_explicit_placement_and_clear_result_semantics() -> None:
    _definition, branch, primitive = _contract()

    assert branch["required"] == [
        "label",
        "placement",
        "result",
        "definition",
    ]
    assert branch["additionalProperties"] is False
    result = branch["properties"]["result"]
    new_body, existing_body = result["oneOf"]
    assert new_body["properties"]["mode"]["const"] == "new_body"
    assert new_body["required"] == ["mode"]
    assert existing_body["properties"]["mode"]["enum"] == [
        "join", "cut", "intersect"
    ]
    assert existing_body["required"] == ["mode", "targets"]
    assert existing_body["properties"]["targets"]["minItems"] == 1
    assert existing_body["properties"]["targets"]["maxItems"] == 16
    assert primitive["additionalProperties"] is False
    assert primitive["required"] == ["kind"]


def test_compact_primitive_contract_names_every_exact_kind_field() -> None:
    _definition, _branch, primitive = _contract()
    expected_fields = {
        "kind",
        "length_mm",
        "width_mm",
        "height_mm",
        "radius_mm",
        "sweep_degrees",
        "latitude_start_degrees",
        "latitude_end_degrees",
        "radius1_mm",
        "radius2_mm",
        "radius_x_mm",
        "radius_y_mm",
        "radius_z_mm",
        "major_radius_mm",
        "minor_radius_mm",
        "section_start_degrees",
        "section_end_degrees",
        "sides",
        "circumradius_mm",
        "xmin_mm",
        "ymin_mm",
        "zmin_mm",
        "x2min_mm",
        "z2min_mm",
        "xmax_mm",
        "ymax_mm",
        "zmax_mm",
        "x2max_mm",
        "z2max_mm",
        "outer_radius_mm",
        "inner_radius_mm",
    }

    assert set(primitive["properties"]) == expected_fields
    assert all(kind in primitive["description"] for kind in EXPECTED_KINDS)


def test_primitive_contract_has_no_selection_or_gui_command_escape_hatch() -> None:
    definition = model_feature_capability_definition()
    serialized = json.dumps(
        definition.provider_schema(("primitive",)),
        sort_keys=True,
    )

    for forbidden in ("selection", "runCommand", "workbench", "ribbon"):
        assert forbidden not in serialized
