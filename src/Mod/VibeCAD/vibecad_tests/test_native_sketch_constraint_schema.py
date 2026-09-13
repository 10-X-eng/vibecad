# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json

from jsonschema import Draft202012Validator

from VibeCADNativeCapabilityRegistry import (
    MAX_NATIVE_SCHEMAS_JSON_BYTES,
    resolve_native_provider_surface,
)
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeSketchConstraintBindings import SKETCH_CONSTRAINT_CAPABILITY_NAME
from VibeCADNativeSketchConstraintSchema import (
    sketch_constraint_capability_definition,
)
from VibeCADRibbonSurface import RibbonAction, RibbonGroup, RibbonSurface


def _valid() -> dict[str, object]:
    return {
        "operation": "infer_dimension",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 12,
        "expected_constraint_count": 4,
        "expected_external_geometry_count": 1,
        "selection": [
            {"geometry_index": 3, "position": "end"},
            {"geometry_index": -3, "position": "whole"},
        ],
        "expected_inference": "distance",
        "dimension": {"value": 5.25, "unit": "mm"},
        "driving": True,
    }


def _valid_distance_x() -> dict[str, object]:
    return {
        "operation": "constrain_distance_x",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 12,
        "expected_constraint_count": 4,
        "expected_external_geometry_count": 1,
        "selection": [
            {"geometry_index": 3, "position": "end"},
            {"geometry_index": -3, "position": "start"},
        ],
        "dimension": {"value": 5.25, "unit": "mm"},
        "driving": True,
    }


def _valid_distance_y() -> dict[str, object]:
    return {**_valid_distance_x(), "operation": "constrain_distance_y"}


def _valid_distance() -> dict[str, object]:
    return {
        **_valid_distance_x(),
        "operation": "constrain_distance",
        "dimension": {"value": 9.0, "unit": "mm"},
    }


def _valid_radiam() -> dict[str, object]:
    return {
        **_valid_distance_x(),
        "operation": "constrain_radius_diameter",
        "selection": [{"geometry_index": 3, "position": "whole"}],
        "expected_constraint": "diameter",
        "dimension": {"value": 12.0, "unit": "mm"},
    }


def _valid_radius() -> dict[str, object]:
    return {
        **_valid_distance_x(),
        "operation": "constrain_radius",
        "selection": [{"geometry_index": 3, "position": "whole"}],
        "dimension": {"value": 6.0, "unit": "mm"},
    }


def _valid_diameter() -> dict[str, object]:
    return {
        **_valid_distance_x(),
        "operation": "constrain_diameter",
        "selection": [{"geometry_index": 3, "position": "whole"}],
        "dimension": {"value": 12.0, "unit": "mm"},
    }


def _valid_angle() -> dict[str, object]:
    return {
        **_valid_distance_x(),
        "operation": "constrain_angle",
        "selection": [
            {"geometry_index": 3, "position": "start"},
            {"geometry_index": -1, "position": "whole"},
        ],
        "expected_form": "line_line",
        "dimension": {"value": 35.0, "unit": "deg"},
    }


def _valid_lock() -> dict[str, object]:
    return {
        "operation": "constrain_lock",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 12,
        "expected_constraint_count": 4,
        "expected_external_geometry_count": 1,
        "target": {
            "form": "absolute",
            "point": {"geometry_index": 3, "position": "end"},
            "expected_position_mm": {"x": -5.0, "y": 8.25},
        },
        "driving": True,
    }


def _valid_coincident() -> dict[str, object]:
    return {
        "operation": "constrain_coincident",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 12,
        "expected_constraint_count": 4,
        "expected_external_geometry_count": 1,
        "target": {
            "form": "point_point",
            "first_point": {"geometry_index": 3, "position": "end"},
            "second_point": {"geometry_index": -3, "position": "start"},
        },
    }


def _valid_horizontal_vertical() -> dict[str, object]:
    return {
        "operation": "constrain_horizontal_vertical",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 12,
        "expected_constraint_count": 4,
        "expected_external_geometry_count": 1,
        "selection": [{"geometry_index": 3, "position": "whole"}],
        "expected_inference": "horizontal",
    }


def _valid_horizontal() -> dict[str, object]:
    value = _valid_horizontal_vertical()
    value["operation"] = "constrain_horizontal"
    del value["expected_inference"]
    return value


def _valid_vertical() -> dict[str, object]:
    return {**_valid_horizontal(), "operation": "constrain_vertical"}


def _valid_parallel() -> dict[str, object]:
    return {
        **_valid_horizontal(),
        "operation": "constrain_parallel",
        "selection": [
            {"geometry_index": 3, "position": "whole"},
            {"geometry_index": -3, "position": "whole"},
        ],
    }


def _valid_perpendicular() -> dict[str, object]:
    return {
        "operation": "constrain_perpendicular",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 12,
        "expected_constraint_count": 4,
        "expected_external_geometry_count": 1,
        "target": {
            "form": "curve_curve",
            "first_curve": {"geometry_index": 3, "position": "whole"},
            "second_curve": {"geometry_index": -3, "position": "whole"},
        },
    }


def _valid_tangent() -> dict[str, object]:
    return {
        **_valid_perpendicular(),
        "operation": "constrain_tangent",
    }


def _valid_equal() -> dict[str, object]:
    return {
        **_valid_parallel(),
        "operation": "constrain_equal",
        "selection": [
            {"geometry_index": 3, "position": "whole"},
            {"geometry_index": 4, "position": "whole"},
            {"geometry_index": -3, "position": "whole"},
        ],
    }


def _valid_symmetric() -> dict[str, object]:
    return {
        "operation": "constrain_symmetric",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 12,
        "expected_constraint_count": 4,
        "expected_external_geometry_count": 1,
        "target": {
            "form": "points_about_line",
            "first_point": {"geometry_index": 3, "position": "start"},
            "second_point": {"geometry_index": 4, "position": "end"},
            "symmetry_line": {"geometry_index": -1, "position": "whole"},
        },
    }


def _valid_block() -> dict[str, object]:
    return {
        "operation": "constrain_block",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 12,
        "expected_constraint_count": 4,
        "expected_external_geometry_count": 1,
        "selection": [
            {"geometry_index": 3, "position": "whole"},
            {"geometry_index": 4, "position": "whole"},
        ],
    }


def _valid_group() -> dict[str, object]:
    return {
        "operation": "constrain_group",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 12,
        "expected_constraint_count": 4,
        "expected_external_geometry_count": 1,
        "selection": [
            {"geometry_index": 3, "position": "whole"},
            {"geometry_index": 4, "position": "whole"},
        ],
    }


def _valid_driving() -> dict[str, object]:
    return {
        "operation": "toggle_driving_reference",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 12,
        "expected_constraint_count": 4,
        "expected_external_geometry_count": 1,
        "targets": [
            {"constraint_index": 1, "expected_driving": True},
            {"constraint_index": 3, "expected_driving": False},
        ],
    }


def _valid_active() -> dict[str, object]:
    return {
        "operation": "toggle_active_inactive",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 12,
        "expected_constraint_count": 4,
        "expected_external_geometry_count": 1,
        "targets": [
            {"constraint_index": 1, "expected_active": True},
            {"constraint_index": 3, "expected_active": False},
        ],
    }


def test_dimension_variant_maps_only_the_contextual_human_command() -> None:
    definition = sketch_constraint_capability_definition()

    assert definition.name == SKETCH_CONSTRAINT_CAPABILITY_NAME
    assert definition.primary_classification == "mutation"
    assert len(definition.variants) == 23
    variant = next(
        item for item in definition.variants if item.operation == "infer_dimension"
    )
    assert variant.operation == "infer_dimension"
    assert variant.action_ids == frozenset({"Sketcher_Dimension"})
    assert variant.surface_ids == frozenset({"sketch.edit"})
    assert variant.exact_target_type == "ActiveSketchExactElementsAndExpectedInference"
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False


def test_horizontal_distance_variant_maps_only_the_human_command() -> None:
    definition = sketch_constraint_capability_definition()
    variant = next(
        item for item in definition.variants if item.operation == "constrain_distance_x"
    )

    assert variant.action_ids == frozenset({"Sketcher_ConstrainDistanceX"})
    assert variant.surface_ids == frozenset({"sketch.edit"})
    assert variant.exact_target_type == "ActiveSketchExactHorizontalDistance"
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False


def test_vertical_distance_variant_maps_only_the_human_command() -> None:
    definition = sketch_constraint_capability_definition()
    variant = next(
        item for item in definition.variants if item.operation == "constrain_distance_y"
    )

    assert variant.action_ids == frozenset({"Sketcher_ConstrainDistanceY"})
    assert variant.surface_ids == frozenset({"sketch.edit"})
    assert variant.exact_target_type == "ActiveSketchExactVerticalDistance"
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False


def test_general_distance_variant_maps_only_the_human_command() -> None:
    definition = sketch_constraint_capability_definition()
    variant = next(
        item for item in definition.variants if item.operation == "constrain_distance"
    )

    assert variant.action_ids == frozenset({"Sketcher_ConstrainDistance"})
    assert variant.surface_ids == frozenset({"sketch.edit"})
    assert variant.exact_target_type == "ActiveSketchExactGeneralDistance"
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False


def test_combined_radius_diameter_variant_maps_only_the_human_command() -> None:
    definition = sketch_constraint_capability_definition()
    variant = next(
        item
        for item in definition.variants
        if item.operation == "constrain_radius_diameter"
    )

    assert variant.action_ids == frozenset({"Sketcher_ConstrainRadiam"})
    assert variant.surface_ids == frozenset({"sketch.edit"})
    assert variant.exact_target_type == "ActiveSketchExactCircleOrCircularArcSize"
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False


def test_radius_variant_maps_only_the_human_command() -> None:
    definition = sketch_constraint_capability_definition()
    variant = next(
        item for item in definition.variants if item.operation == "constrain_radius"
    )

    assert variant.action_ids == frozenset({"Sketcher_ConstrainRadius"})
    assert variant.surface_ids == frozenset({"sketch.edit"})
    assert variant.exact_target_type == "ActiveSketchExactCircleOrCircularArcRadius"
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False


def test_diameter_variant_maps_only_the_human_command() -> None:
    definition = sketch_constraint_capability_definition()
    variant = next(
        item for item in definition.variants if item.operation == "constrain_diameter"
    )

    assert variant.action_ids == frozenset({"Sketcher_ConstrainDiameter"})
    assert variant.surface_ids == frozenset({"sketch.edit"})
    assert variant.exact_target_type == "ActiveSketchExactCircleOrCircularArcDiameter"
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False


def test_angle_variant_maps_only_the_human_command() -> None:
    definition = sketch_constraint_capability_definition()
    variant = next(
        item for item in definition.variants if item.operation == "constrain_angle"
    )

    assert variant.action_ids == frozenset({"Sketcher_ConstrainAngle"})
    assert variant.surface_ids == frozenset({"sketch.edit"})
    assert variant.exact_target_type == "ActiveSketchExactAngleForm"
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False


def test_lock_variant_maps_only_the_human_command() -> None:
    definition = sketch_constraint_capability_definition()
    variant = next(
        item for item in definition.variants if item.operation == "constrain_lock"
    )

    assert variant.action_ids == frozenset({"Sketcher_ConstrainLock"})
    assert variant.surface_ids == frozenset({"sketch.edit"})
    assert variant.exact_target_type == "ActiveSketchExactPointLock"
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False


def test_coincident_variant_maps_only_the_unified_human_command() -> None:
    definition = sketch_constraint_capability_definition()
    variant = next(
        item for item in definition.variants if item.operation == "constrain_coincident"
    )

    assert variant.action_ids == frozenset({"Sketcher_ConstrainCoincidentUnified"})
    assert variant.surface_ids == frozenset({"sketch.edit"})
    assert variant.exact_target_type == "ActiveSketchExactCoincidenceForm"
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False


def test_horizontal_vertical_variant_maps_only_the_automatic_human_command() -> None:
    definition = sketch_constraint_capability_definition()
    variant = next(
        item
        for item in definition.variants
        if item.operation == "constrain_horizontal_vertical"
    )

    assert variant.action_ids == frozenset({"Sketcher_ConstrainHorVer"})
    assert variant.surface_ids == frozenset({"sketch.edit"})
    assert variant.exact_target_type == "ActiveSketchExactHorizontalVerticalInference"
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False


def test_horizontal_variant_maps_only_the_explicit_human_command() -> None:
    definition = sketch_constraint_capability_definition()
    variant = next(
        item for item in definition.variants if item.operation == "constrain_horizontal"
    )

    assert variant.action_ids == frozenset({"Sketcher_ConstrainHorizontal"})
    assert variant.surface_ids == frozenset({"sketch.edit"})
    assert variant.exact_target_type == "ActiveSketchExactHorizontalAlignment"
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False


def test_vertical_variant_maps_only_the_explicit_human_command() -> None:
    definition = sketch_constraint_capability_definition()
    variant = next(
        item for item in definition.variants if item.operation == "constrain_vertical"
    )

    assert variant.action_ids == frozenset({"Sketcher_ConstrainVertical"})
    assert variant.surface_ids == frozenset({"sketch.edit"})
    assert variant.exact_target_type == "ActiveSketchExactVerticalAlignment"
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False


def test_parallel_variant_maps_only_the_human_command() -> None:
    definition = sketch_constraint_capability_definition()
    variant = next(
        item for item in definition.variants if item.operation == "constrain_parallel"
    )

    assert variant.action_ids == frozenset({"Sketcher_ConstrainParallel"})
    assert variant.surface_ids == frozenset({"sketch.edit"})
    assert variant.exact_target_type == "ActiveSketchExactOrderedLinePair"
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False


def test_perpendicular_variant_maps_only_the_human_command() -> None:
    definition = sketch_constraint_capability_definition()
    variant = next(
        item
        for item in definition.variants
        if item.operation == "constrain_perpendicular"
    )

    assert variant.action_ids == frozenset({"Sketcher_ConstrainPerpendicular"})
    assert variant.surface_ids == frozenset({"sketch.edit"})
    assert variant.exact_target_type == "ActiveSketchExactPerpendicularForm"
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False


def test_tangent_variant_maps_only_the_human_command() -> None:
    definition = sketch_constraint_capability_definition()
    variant = next(
        item for item in definition.variants if item.operation == "constrain_tangent"
    )

    assert variant.action_ids == frozenset({"Sketcher_ConstrainTangent"})
    assert variant.surface_ids == frozenset({"sketch.edit"})
    assert variant.exact_target_type == "ActiveSketchExactTangentFormOrReplacement"
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False


def test_equal_variant_maps_only_the_human_command() -> None:
    definition = sketch_constraint_capability_definition()
    variant = next(
        item for item in definition.variants if item.operation == "constrain_equal"
    )

    assert variant.action_ids == frozenset({"Sketcher_ConstrainEqual"})
    assert variant.surface_ids == frozenset({"sketch.edit"})
    assert variant.exact_target_type == "ActiveSketchExactOrderedCompatibleEdgeChain"
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False


def test_symmetric_variant_maps_only_the_human_command() -> None:
    definition = sketch_constraint_capability_definition()
    variant = next(
        item for item in definition.variants if item.operation == "constrain_symmetric"
    )

    assert variant.action_ids == frozenset({"Sketcher_ConstrainSymmetric"})
    assert variant.surface_ids == frozenset({"sketch.edit"})
    assert variant.exact_target_type == "ActiveSketchExactSymmetricForm"
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False


def test_block_variant_maps_only_the_human_command() -> None:
    definition = sketch_constraint_capability_definition()
    variant = next(
        item for item in definition.variants if item.operation == "constrain_block"
    )

    assert variant.action_ids == frozenset({"Sketcher_ConstrainBlock"})
    assert variant.surface_ids == frozenset({"sketch.edit"})
    assert variant.exact_target_type == "ActiveSketchExactInternalWholeEdgeSet"
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False


def test_group_variant_maps_only_the_human_command() -> None:
    definition = sketch_constraint_capability_definition()
    variant = next(
        item for item in definition.variants if item.operation == "constrain_group"
    )

    assert variant.action_ids == frozenset({"Sketcher_ConstrainGroup"})
    assert variant.surface_ids == frozenset({"sketch.edit"})
    assert variant.exact_target_type == "ActiveSketchExactInternalWholeGeometrySet"
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False


def test_driving_variant_maps_only_the_selected_constraint_human_command() -> None:
    definition = sketch_constraint_capability_definition()
    variant = next(
        item
        for item in definition.variants
        if item.operation == "toggle_driving_reference"
    )

    assert variant.action_ids == frozenset({"Sketcher_ToggleDrivingConstraint"})
    assert variant.surface_ids == frozenset({"sketch.edit"})
    assert variant.exact_target_type == ("ActiveSketchExactDimensionalConstraintStates")
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False


def test_active_variant_maps_only_the_selected_constraint_human_command() -> None:
    definition = sketch_constraint_capability_definition()
    variant = next(
        item
        for item in definition.variants
        if item.operation == "toggle_active_inactive"
    )

    assert variant.action_ids == frozenset({"Sketcher_ToggleActiveConstraint"})
    assert variant.surface_ids == frozenset({"sketch.edit"})
    assert variant.exact_target_type == "ActiveSketchExactConstraintActiveStates"
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False


def test_dimension_schema_is_closed_exact_and_bounded() -> None:
    definition = sketch_constraint_capability_definition()
    schema = definition.provider_schema(("infer_dimension",))
    validator = Draft202012Validator(schema["parameters"])
    valid = _valid()

    assert list(validator.iter_errors(valid)) == []
    for invalid in (
        {**valid, "unexpected": True},
        {**valid, "selection": []},
        {**valid, "selection": valid["selection"] * 2},
        {**valid, "selection": [{"geometry_index": -2000, "position": "whole"}]},
        {**valid, "selection": [{"geometry_index": 0, "position": "bad"}]},
        {**valid, "expected_inference": "radius"},
        {**valid, "dimension": {"value": 0.0, "unit": "mm"}},
        {**valid, "dimension": {"value": 5.0, "unit": "inch"}},
        {**valid, "driving": 1},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_horizontal_distance_schema_is_closed_exact_and_bounded() -> None:
    definition = sketch_constraint_capability_definition()
    schema = definition.provider_schema(("constrain_distance_x",))
    validator = Draft202012Validator(schema["parameters"])
    valid = _valid_distance_x()

    assert list(validator.iter_errors(valid)) == []
    for invalid in (
        {**valid, "unexpected": True},
        {**valid, "selection": []},
        {**valid, "selection": valid["selection"] * 2},
        {**valid, "selection": [{"geometry_index": -2000, "position": "whole"}]},
        {**valid, "selection": [{"geometry_index": 0, "position": "bad"}]},
        {**valid, "dimension": {"value": 5.0, "unit": "deg"}},
        {**valid, "dimension": {"value": 1_000_001.0, "unit": "mm"}},
        {**valid, "driving": 1},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES

    operations = tuple(variant.operation for variant in definition.variants)
    combined = json.dumps(
        [definition.provider_schema(operations)],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(combined) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_vertical_distance_schema_is_closed_exact_and_bounded() -> None:
    definition = sketch_constraint_capability_definition()
    schema = definition.provider_schema(("constrain_distance_y",))
    validator = Draft202012Validator(schema["parameters"])
    valid = _valid_distance_y()

    assert list(validator.iter_errors(valid)) == []
    for invalid in (
        {**valid, "unexpected": True},
        {**valid, "selection": []},
        {**valid, "selection": valid["selection"] * 2},
        {**valid, "selection": [{"geometry_index": -2000, "position": "whole"}]},
        {**valid, "selection": [{"geometry_index": 0, "position": "bad"}]},
        {**valid, "dimension": {"value": 5.0, "unit": "deg"}},
        {**valid, "dimension": {"value": -1_000_001.0, "unit": "mm"}},
        {**valid, "driving": 1},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_general_distance_schema_is_closed_exact_and_bounded() -> None:
    definition = sketch_constraint_capability_definition()
    schema = definition.provider_schema(("constrain_distance",))
    validator = Draft202012Validator(schema["parameters"])
    valid = _valid_distance()

    assert list(validator.iter_errors(valid)) == []
    for invalid in (
        {**valid, "unexpected": True},
        {**valid, "selection": []},
        {**valid, "selection": valid["selection"] * 2},
        {**valid, "selection": [{"geometry_index": -2000, "position": "whole"}]},
        {**valid, "selection": [{"geometry_index": 0, "position": "bad"}]},
        {**valid, "dimension": {"value": 5.0, "unit": "deg"}},
        {**valid, "dimension": {"value": 1_000_001.0, "unit": "mm"}},
        {**valid, "driving": 1},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_combined_radius_diameter_schema_is_closed_exact_and_bounded() -> None:
    definition = sketch_constraint_capability_definition()
    schema = definition.provider_schema(("constrain_radius_diameter",))
    validator = Draft202012Validator(schema["parameters"])
    valid = _valid_radiam()

    assert list(validator.iter_errors(valid)) == []
    for invalid in (
        {**valid, "unexpected": True},
        {**valid, "selection": []},
        {**valid, "selection": valid["selection"] * 2},
        {**valid, "selection": [{"geometry_index": -2000, "position": "whole"}]},
        {**valid, "selection": [{"geometry_index": 0, "position": "bad"}]},
        {**valid, "expected_constraint": "weight"},
        {**valid, "dimension": {"value": 5.0, "unit": "deg"}},
        {**valid, "dimension": {"value": 0.0, "unit": "mm"}},
        {**valid, "dimension": {"value": 1_000_001.0, "unit": "mm"}},
        {**valid, "driving": 1},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_radius_schema_is_closed_exact_and_bounded() -> None:
    definition = sketch_constraint_capability_definition()
    schema = definition.provider_schema(("constrain_radius",))
    validator = Draft202012Validator(schema["parameters"])
    valid = _valid_radius()

    assert list(validator.iter_errors(valid)) == []
    for invalid in (
        {**valid, "unexpected": True},
        {**valid, "selection": []},
        {**valid, "selection": valid["selection"] * 2},
        {**valid, "selection": [{"geometry_index": -2000, "position": "whole"}]},
        {**valid, "selection": [{"geometry_index": 0, "position": "bad"}]},
        {**valid, "expected_constraint": "radius"},
        {**valid, "dimension": {"value": 5.0, "unit": "deg"}},
        {**valid, "dimension": {"value": 0.0, "unit": "mm"}},
        {**valid, "dimension": {"value": 1_000_001.0, "unit": "mm"}},
        {**valid, "driving": 1},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_diameter_schema_is_closed_exact_and_bounded() -> None:
    definition = sketch_constraint_capability_definition()
    schema = definition.provider_schema(("constrain_diameter",))
    validator = Draft202012Validator(schema["parameters"])
    valid = _valid_diameter()

    assert list(validator.iter_errors(valid)) == []
    for invalid in (
        {**valid, "unexpected": True},
        {**valid, "selection": []},
        {**valid, "selection": valid["selection"] * 2},
        {**valid, "selection": [{"geometry_index": -2000, "position": "whole"}]},
        {**valid, "selection": [{"geometry_index": 0, "position": "bad"}]},
        {**valid, "expected_constraint": "diameter"},
        {**valid, "dimension": {"value": 5.0, "unit": "deg"}},
        {**valid, "dimension": {"value": 0.0, "unit": "mm"}},
        {**valid, "dimension": {"value": 1_000_001.0, "unit": "mm"}},
        {**valid, "driving": 1},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_angle_schema_is_closed_exact_and_bounded() -> None:
    definition = sketch_constraint_capability_definition()
    schema = definition.provider_schema(("constrain_angle",))
    validator = Draft202012Validator(schema["parameters"])
    valid = _valid_angle()

    assert list(validator.iter_errors(valid)) == []
    for invalid in (
        {**valid, "unexpected": True},
        {**valid, "selection": []},
        {**valid, "selection": valid["selection"] * 2},
        {**valid, "selection": [{"geometry_index": -2000, "position": "whole"}]},
        {**valid, "selection": [{"geometry_index": 0, "position": "bad"}]},
        {**valid, "expected_form": "supplementary"},
        {**valid, "dimension": {"value": 35.0, "unit": "rad"}},
        {**valid, "dimension": {"value": -181.0, "unit": "deg"}},
        {**valid, "dimension": {"value": 361.0, "unit": "deg"}},
        {**valid, "driving": 1},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_lock_schema_is_closed_exact_discriminated_and_bounded() -> None:
    definition = sketch_constraint_capability_definition()
    schema = definition.provider_schema(("constrain_lock",))
    validator = Draft202012Validator(schema["parameters"])
    absolute = _valid_lock()
    relative = {
        **absolute,
        "target": {
            "form": "relative",
            "point": {"geometry_index": 3, "position": "end"},
            "reference": {"geometry_index": -1, "position": "start"},
            "expected_offset_mm": {"x": 5.0, "y": -8.25},
        },
        "driving": False,
    }

    assert list(validator.iter_errors(absolute)) == []
    assert list(validator.iter_errors(relative)) == []
    for invalid in (
        {**absolute, "unexpected": True},
        {**absolute, "target": {"form": "absolute"}},
        {
            **absolute,
            "target": {
                **absolute["target"],
                "reference": {"geometry_index": 0, "position": "start"},
            },
        },
        {
            **relative,
            "target": {
                **relative["target"],
                "expected_position_mm": {"x": 0.0, "y": 0.0},
            },
        },
        {
            **absolute,
            "target": {
                **absolute["target"],
                "point": {"geometry_index": -2000, "position": "whole"},
            },
        },
        {
            **absolute,
            "target": {
                **absolute["target"],
                "point": {"geometry_index": 0, "position": "bad"},
            },
        },
        {
            **absolute,
            "target": {
                **absolute["target"],
                "expected_position_mm": {"x": -1_000_001.0, "y": 0.0},
            },
        },
        {**absolute, "driving": 1},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_coincident_schema_is_closed_exact_discriminated_and_bounded() -> None:
    definition = sketch_constraint_capability_definition()
    schema = definition.provider_schema(("constrain_coincident",))
    validator = Draft202012Validator(schema["parameters"])
    point_point = _valid_coincident()
    point_on_object = {
        **point_point,
        "target": {
            "form": "point_on_object",
            "point": {"geometry_index": 3, "position": "end"},
            "curve": {"geometry_index": -3, "position": "whole"},
        },
    }
    concentric = {
        **point_point,
        "target": {
            "form": "concentric",
            "first_curve": {"geometry_index": 7, "position": "whole"},
            "second_curve": {"geometry_index": 8, "position": "whole"},
        },
    }

    for valid in (point_point, point_on_object, concentric):
        assert list(validator.iter_errors(valid)) == []
    for invalid in (
        {**point_point, "unexpected": True},
        {**point_point, "target": {"form": "point_point"}},
        {
            **point_point,
            "target": {
                **point_point["target"],
                "curve": {"geometry_index": 0, "position": "whole"},
            },
        },
        {
            **point_on_object,
            "target": {
                **point_on_object["target"],
                "first_point": {"geometry_index": 0, "position": "end"},
            },
        },
        {
            **concentric,
            "target": {**concentric["target"], "form": "multiple"},
        },
        {
            **point_point,
            "target": {
                **point_point["target"],
                "first_point": {"geometry_index": -2000, "position": "whole"},
            },
        },
        {
            **point_point,
            "target": {
                **point_point["target"],
                "second_point": {"geometry_index": 0, "position": "bad"},
            },
        },
        {
            **point_point,
            "target": {
                **point_point["target"],
                "second_point": {"geometry_index": 0, "position": "whole"},
            },
        },
        {
            **point_on_object,
            "target": {
                **point_on_object["target"],
                "curve": {"geometry_index": 0, "position": "center"},
            },
        },
        {**point_point, "driving": True},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_horizontal_vertical_schema_is_closed_exact_and_bounded() -> None:
    definition = sketch_constraint_capability_definition()
    schema = definition.provider_schema(("constrain_horizontal_vertical",))
    validator = Draft202012Validator(schema["parameters"])
    line = _valid_horizontal_vertical()
    points = {
        **line,
        "selection": [
            {"geometry_index": 3, "position": "end"},
            {"geometry_index": -1, "position": "start"},
        ],
        "expected_inference": "vertical",
    }

    assert list(validator.iter_errors(line)) == []
    assert list(validator.iter_errors(points)) == []
    for invalid in (
        {**line, "unexpected": True},
        {**line, "selection": []},
        {**line, "selection": line["selection"] * 2},
        {**line, "selection": [{"geometry_index": 3, "position": "start"}]},
        {
            **points,
            "selection": [
                {"geometry_index": 3, "position": "whole"},
                {"geometry_index": -1, "position": "start"},
            ],
        },
        {
            **points,
            "selection": points["selection"]
            + [{"geometry_index": 4, "position": "start"}],
        },
        {**line, "selection": [{"geometry_index": -2000, "position": "whole"}]},
        {**line, "selection": [{"geometry_index": 0, "position": "bad"}]},
        {**line, "expected_inference": "nearest"},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_horizontal_schema_is_closed_exact_and_bounded() -> None:
    definition = sketch_constraint_capability_definition()
    schema = definition.provider_schema(("constrain_horizontal",))
    validator = Draft202012Validator(schema["parameters"])
    line = _valid_horizontal()
    points = {
        **line,
        "selection": [
            {"geometry_index": 3, "position": "end"},
            {"geometry_index": -1, "position": "start"},
        ],
    }

    assert list(validator.iter_errors(line)) == []
    assert list(validator.iter_errors(points)) == []
    for invalid in (
        {**line, "unexpected": True},
        {**line, "expected_inference": "horizontal"},
        {**line, "selection": []},
        {**line, "selection": line["selection"] * 2},
        {**line, "selection": [{"geometry_index": 3, "position": "start"}]},
        {
            **points,
            "selection": [
                {"geometry_index": 3, "position": "whole"},
                {"geometry_index": -1, "position": "start"},
            ],
        },
        {
            **points,
            "selection": points["selection"]
            + [{"geometry_index": 4, "position": "start"}],
        },
        {**line, "selection": [{"geometry_index": -2000, "position": "whole"}]},
        {**line, "selection": [{"geometry_index": 0, "position": "bad"}]},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_vertical_schema_is_closed_exact_and_bounded() -> None:
    definition = sketch_constraint_capability_definition()
    schema = definition.provider_schema(("constrain_vertical",))
    validator = Draft202012Validator(schema["parameters"])
    line = _valid_vertical()
    points = {
        **line,
        "selection": [
            {"geometry_index": 3, "position": "end"},
            {"geometry_index": -1, "position": "start"},
        ],
    }

    assert list(validator.iter_errors(line)) == []
    assert list(validator.iter_errors(points)) == []
    for invalid in (
        {**line, "unexpected": True},
        {**line, "expected_inference": "vertical"},
        {**line, "selection": []},
        {**line, "selection": line["selection"] * 2},
        {**line, "selection": [{"geometry_index": 3, "position": "start"}]},
        {
            **points,
            "selection": [
                {"geometry_index": 3, "position": "whole"},
                {"geometry_index": -1, "position": "start"},
            ],
        },
        {
            **points,
            "selection": points["selection"]
            + [{"geometry_index": 4, "position": "start"}],
        },
        {**line, "selection": [{"geometry_index": -2000, "position": "whole"}]},
        {**line, "selection": [{"geometry_index": 0, "position": "bad"}]},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_parallel_schema_is_closed_exact_and_bounded() -> None:
    definition = sketch_constraint_capability_definition()
    schema = definition.provider_schema(("constrain_parallel",))
    validator = Draft202012Validator(schema["parameters"])
    valid = _valid_parallel()

    assert list(validator.iter_errors(valid)) == []
    for invalid in (
        {**valid, "unexpected": True},
        {**valid, "selection": []},
        {**valid, "selection": valid["selection"][:1]},
        {
            **valid,
            "selection": valid["selection"]
            + [{"geometry_index": 4, "position": "whole"}],
        },
        {
            **valid,
            "selection": [
                {"geometry_index": 3, "position": "start"},
                {"geometry_index": -3, "position": "whole"},
            ],
        },
        {
            **valid,
            "selection": [
                {"geometry_index": -2000, "position": "whole"},
                {"geometry_index": 3, "position": "whole"},
            ],
        },
        {
            **valid,
            "selection": [
                {"geometry_index": 3, "position": "bad"},
                {"geometry_index": -3, "position": "whole"},
            ],
        },
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_perpendicular_schema_is_closed_exact_discriminated_and_bounded() -> None:
    definition = sketch_constraint_capability_definition()
    schema = definition.provider_schema(("constrain_perpendicular",))
    validator = Draft202012Validator(schema["parameters"])
    curve_curve = _valid_perpendicular()
    endpoint_curve = {
        **curve_curve,
        "target": {
            "form": "endpoint_curve",
            "endpoint": {"geometry_index": 3, "position": "end"},
            "curve": {"geometry_index": -3, "position": "whole"},
        },
    }
    endpoint_endpoint = {
        **curve_curve,
        "target": {
            "form": "endpoint_endpoint",
            "first_endpoint": {"geometry_index": 3, "position": "end"},
            "second_endpoint": {"geometry_index": 4, "position": "start"},
        },
    }
    point_pair_line = {
        **curve_curve,
        "target": {
            "form": "point_pair_line",
            "first_point": {"geometry_index": 3, "position": "start"},
            "second_point": {"geometry_index": 3, "position": "end"},
            "line": {"geometry_index": -1, "position": "whole"},
        },
    }
    via_point = {
        **curve_curve,
        "target": {
            "form": "curves_via_point",
            "first_curve": {"geometry_index": 3, "position": "whole"},
            "second_curve": {"geometry_index": 4, "position": "whole"},
            "point": {"geometry_index": 3, "position": "end"},
        },
    }

    for valid in (
        curve_curve,
        endpoint_curve,
        endpoint_endpoint,
        point_pair_line,
        via_point,
    ):
        assert list(validator.iter_errors(valid)) == []
    for invalid in (
        {**curve_curve, "unexpected": True},
        {**curve_curve, "target": {"form": "curve_curve"}},
        {
            **curve_curve,
            "target": {**curve_curve["target"], "form": "tangent"},
        },
        {
            **curve_curve,
            "target": {
                **curve_curve["target"],
                "first_curve": {"geometry_index": 3, "position": "start"},
            },
        },
        {
            **endpoint_curve,
            "target": {
                **endpoint_curve["target"],
                "endpoint": {"geometry_index": 3, "position": "center"},
            },
        },
        {
            **via_point,
            "target": {
                **via_point["target"],
                "point": {"geometry_index": 3, "position": "whole"},
            },
        },
        {
            **point_pair_line,
            "target": {
                **point_pair_line["target"],
                "line": {"geometry_index": -2000, "position": "whole"},
            },
        },
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_tangent_schema_is_closed_exact_discriminated_and_bounded() -> None:
    definition = sketch_constraint_capability_definition()
    schema = definition.provider_schema(("constrain_tangent",))
    validator = Draft202012Validator(schema["parameters"])
    curve_curve = _valid_tangent()
    endpoint_curve = {
        **curve_curve,
        "target": {
            "form": "endpoint_curve",
            "endpoint": {"geometry_index": 3, "position": "end"},
            "curve": {"geometry_index": -3, "position": "whole"},
        },
    }
    endpoint_endpoint = {
        **curve_curve,
        "target": {
            "form": "endpoint_endpoint",
            "first_endpoint": {"geometry_index": 3, "position": "end"},
            "second_endpoint": {"geometry_index": 4, "position": "start"},
        },
    }
    via_point = {
        **curve_curve,
        "target": {
            "form": "curves_via_point",
            "first_curve": {"geometry_index": 3, "position": "whole"},
            "second_curve": {"geometry_index": 4, "position": "whole"},
            "point": {"geometry_index": 3, "position": "end"},
        },
    }
    replace_endpoint_curve = {
        **curve_curve,
        "target": {
            "form": "replace_with_endpoint_curve",
            "constraint_index": 2,
            "endpoint": {"geometry_index": 3, "position": "end"},
            "curve": {"geometry_index": 4, "position": "whole"},
        },
    }
    replace_endpoint_endpoint = {
        **curve_curve,
        "target": {
            "form": "replace_with_endpoint_endpoint",
            "constraint_index": 3,
            "first_endpoint": {"geometry_index": 3, "position": "end"},
            "second_endpoint": {"geometry_index": 4, "position": "start"},
        },
    }

    for valid in (
        curve_curve,
        endpoint_curve,
        endpoint_endpoint,
        via_point,
        replace_endpoint_curve,
        replace_endpoint_endpoint,
    ):
        assert list(validator.iter_errors(valid)) == []
    for invalid in (
        {**curve_curve, "unexpected": True},
        {**curve_curve, "target": {"form": "curve_curve"}},
        {
            **curve_curve,
            "target": {**curve_curve["target"], "form": "tangent"},
        },
        {
            **curve_curve,
            "target": {
                **curve_curve["target"],
                "first_curve": {"geometry_index": 3, "position": "start"},
            },
        },
        {
            **endpoint_curve,
            "target": {
                **endpoint_curve["target"],
                "endpoint": {"geometry_index": 3, "position": "center"},
            },
        },
        {
            **via_point,
            "target": {
                **via_point["target"],
                "point": {"geometry_index": 3, "position": "whole"},
            },
        },
        {
            **replace_endpoint_curve,
            "target": {
                **replace_endpoint_curve["target"],
                "constraint_index": -1,
            },
        },
        {
            **replace_endpoint_endpoint,
            "target": {
                **replace_endpoint_endpoint["target"],
                "constraint_index": 2.5,
            },
        },
        {
            **replace_endpoint_endpoint,
            "target": {
                **replace_endpoint_endpoint["target"],
                "curve": {"geometry_index": 4, "position": "whole"},
            },
        },
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_equal_schema_is_closed_exact_and_bounded() -> None:
    definition = sketch_constraint_capability_definition()
    schema = definition.provider_schema(("constrain_equal",))
    validator = Draft202012Validator(schema["parameters"])
    valid = _valid_equal()

    assert list(validator.iter_errors(valid)) == []
    for invalid in (
        {**valid, "unexpected": True},
        {**valid, "selection": []},
        {**valid, "selection": valid["selection"][:1]},
        {**valid, "selection": valid["selection"] * 6},
        {
            **valid,
            "selection": [
                {"geometry_index": 3, "position": "start"},
                {"geometry_index": 4, "position": "whole"},
            ],
        },
        {
            **valid,
            "selection": [
                {"geometry_index": -2000, "position": "whole"},
                {"geometry_index": 4, "position": "whole"},
            ],
        },
        {
            **valid,
            "selection": [
                {"geometry_index": 3, "position": "bad"},
                {"geometry_index": 4, "position": "whole"},
            ],
        },
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_symmetric_schema_is_closed_exact_discriminated_and_bounded() -> None:
    definition = sketch_constraint_capability_definition()
    schema = definition.provider_schema(("constrain_symmetric",))
    validator = Draft202012Validator(schema["parameters"])
    points_about_line = _valid_symmetric()
    points_about_point = {
        **points_about_line,
        "target": {
            "form": "points_about_point",
            "first_point": {"geometry_index": 3, "position": "start"},
            "second_point": {"geometry_index": 4, "position": "end"},
            "symmetry_point": {"geometry_index": -1, "position": "start"},
        },
    }
    curve_about_line = {
        **points_about_line,
        "target": {
            "form": "curve_about_line",
            "curve": {"geometry_index": 3, "position": "whole"},
            "symmetry_line": {"geometry_index": -3, "position": "whole"},
        },
    }
    curve_about_point = {
        **points_about_line,
        "target": {
            "form": "curve_about_point",
            "curve": {"geometry_index": 3, "position": "whole"},
            "symmetry_point": {"geometry_index": 4, "position": "center"},
        },
    }

    for valid in (
        points_about_line,
        points_about_point,
        curve_about_line,
        curve_about_point,
    ):
        assert list(validator.iter_errors(valid)) == []
    for invalid in (
        {**points_about_line, "unexpected": True},
        {**points_about_line, "target": {"form": "points_about_line"}},
        {
            **points_about_line,
            "target": {**points_about_line["target"], "form": "reflection"},
        },
        {
            **points_about_line,
            "target": {
                **points_about_line["target"],
                "first_point": {"geometry_index": 3, "position": "whole"},
            },
        },
        {
            **points_about_line,
            "target": {
                **points_about_line["target"],
                "symmetry_line": {"geometry_index": -1, "position": "start"},
            },
        },
        {
            **curve_about_line,
            "target": {
                **curve_about_line["target"],
                "curve": {"geometry_index": 3, "position": "end"},
            },
        },
        {
            **curve_about_point,
            "target": {
                **curve_about_point["target"],
                "symmetry_point": {"geometry_index": -2000, "position": "start"},
            },
        },
        {
            **curve_about_point,
            "target": {
                **curve_about_point["target"],
                "symmetry_line": {"geometry_index": -1, "position": "whole"},
            },
        },
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_block_schema_is_closed_exact_and_bounded() -> None:
    definition = sketch_constraint_capability_definition()
    schema = definition.provider_schema(("constrain_block",))
    validator = Draft202012Validator(schema["parameters"])
    valid = _valid_block()

    assert list(validator.iter_errors(valid)) == []
    for invalid in (
        {**valid, "unexpected": True},
        {**valid, "selection": []},
        {**valid, "selection": valid["selection"] * 9},
        {
            **valid,
            "selection": [{"geometry_index": 3, "position": "start"}],
        },
        {
            **valid,
            "selection": [{"geometry_index": -2000, "position": "whole"}],
        },
        {
            **valid,
            "selection": [{"geometry_index": 3, "position": "bad"}],
        },
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_group_schema_is_closed_exact_and_bounded() -> None:
    definition = sketch_constraint_capability_definition()
    schema = definition.provider_schema(("constrain_group",))
    validator = Draft202012Validator(schema["parameters"])
    valid = _valid_group()

    assert list(validator.iter_errors(valid)) == []
    for invalid in (
        {**valid, "unexpected": True},
        {**valid, "selection": valid["selection"][:1]},
        {**valid, "selection": valid["selection"] * 9},
        {
            **valid,
            "selection": [
                {"geometry_index": 3, "position": "start"},
                {"geometry_index": 4, "position": "whole"},
            ],
        },
        {
            **valid,
            "selection": [
                {"geometry_index": -2000, "position": "whole"},
                {"geometry_index": 4, "position": "whole"},
            ],
        },
        {
            **valid,
            "selection": [
                {"geometry_index": 3, "position": "bad"},
                {"geometry_index": 4, "position": "whole"},
            ],
        },
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_driving_schema_is_closed_exact_and_bounded() -> None:
    definition = sketch_constraint_capability_definition()
    schema = definition.provider_schema(("toggle_driving_reference",))
    validator = Draft202012Validator(schema["parameters"])
    valid = _valid_driving()

    assert list(validator.iter_errors(valid)) == []
    for invalid in (
        {**valid, "unexpected": True},
        {**valid, "targets": []},
        {**valid, "targets": [valid["targets"][0], valid["targets"][0]]},
        {**valid, "targets": valid["targets"] * 9},
        {
            **valid,
            "targets": [{"constraint_index": -1, "expected_driving": True}],
        },
        {
            **valid,
            "targets": [
                {"constraint_index": 1, "expected_driving": True, "extra": True}
            ],
        },
        {
            **valid,
            "targets": [{"constraint_index": 1, "expected_driving": 1}],
        },
        {**valid, "expected_external_geometry_count": 1_000_001},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_active_schema_is_closed_exact_and_bounded() -> None:
    definition = sketch_constraint_capability_definition()
    schema = definition.provider_schema(("toggle_active_inactive",))
    validator = Draft202012Validator(schema["parameters"])
    valid = _valid_active()

    assert list(validator.iter_errors(valid)) == []
    for invalid in (
        {**valid, "unexpected": True},
        {**valid, "targets": []},
        {**valid, "targets": [valid["targets"][0], valid["targets"][0]]},
        {**valid, "targets": valid["targets"] * 9},
        {
            **valid,
            "targets": [{"constraint_index": -1, "expected_active": True}],
        },
        {
            **valid,
            "targets": [
                {"constraint_index": 1, "expected_active": True, "extra": True}
            ],
        },
        {
            **valid,
            "targets": [{"constraint_index": 1, "expected_active": 1}],
        },
        {**valid, "expected_external_geometry_count": 1_000_001},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_unfinished_constraint_family_remains_completely_unadvertised() -> None:
    registry = build_native_capability_registry()
    surface = RibbonSurface(
        "sketch.edit",
        1,
        (
            RibbonGroup(
                "Constraints",
                (
                    RibbonAction("Sketcher_Dimension", "Dimension", True, "command"),
                    RibbonAction(
                        "Sketcher_ConstrainDistanceX",
                        "Horizontal Distance",
                        True,
                        "command",
                    ),
                    RibbonAction(
                        "Sketcher_ConstrainDistanceY",
                        "Vertical Distance",
                        True,
                        "command",
                    ),
                    RibbonAction(
                        "Sketcher_ConstrainDistance",
                        "Distance",
                        True,
                        "command",
                    ),
                    RibbonAction(
                        "Sketcher_ConstrainRadiam",
                        "Radius/Diameter",
                        True,
                        "command",
                    ),
                    RibbonAction(
                        "Sketcher_ConstrainRadius",
                        "Radius",
                        True,
                        "command",
                    ),
                    RibbonAction(
                        "Sketcher_ConstrainDiameter",
                        "Diameter",
                        True,
                        "command",
                    ),
                    RibbonAction(
                        "Sketcher_ConstrainAngle",
                        "Angle",
                        True,
                        "command",
                    ),
                    RibbonAction(
                        "Sketcher_ConstrainLock",
                        "Lock",
                        True,
                        "command",
                    ),
                    RibbonAction(
                        "Sketcher_ConstrainCoincidentUnified",
                        "Coincident",
                        True,
                        "command",
                    ),
                    RibbonAction(
                        "Sketcher_ConstrainHorVer",
                        "Automatic Horizontal/Vertical",
                        True,
                        "command",
                    ),
                    RibbonAction(
                        "Sketcher_ConstrainHorizontal",
                        "Horizontal",
                        True,
                        "command",
                    ),
                    RibbonAction(
                        "Sketcher_ConstrainVertical",
                        "Vertical",
                        True,
                        "command",
                    ),
                    RibbonAction(
                        "Sketcher_ConstrainParallel",
                        "Parallel",
                        True,
                        "command",
                    ),
                    RibbonAction(
                        "Sketcher_ConstrainPerpendicular",
                        "Perpendicular",
                        True,
                        "command",
                    ),
                    RibbonAction(
                        "Sketcher_ConstrainTangent",
                        "Tangent",
                        True,
                        "command",
                    ),
                    RibbonAction(
                        "Sketcher_ConstrainEqual",
                        "Equal",
                        True,
                        "command",
                    ),
                    RibbonAction(
                        "Sketcher_ConstrainSymmetric",
                        "Symmetric",
                        True,
                        "command",
                    ),
                    RibbonAction(
                        "Sketcher_ConstrainBlock",
                        "Block",
                        True,
                        "command",
                    ),
                    RibbonAction(
                        "Sketcher_ConstrainGroup",
                        "Group",
                        True,
                        "command",
                    ),
                    RibbonAction(
                        "Sketcher_ToggleDrivingConstraint",
                        "Toggle Driving/Reference",
                        True,
                        "command",
                    ),
                    RibbonAction(
                        "Sketcher_ToggleActiveConstraint",
                        "Toggle Active/Inactive",
                        True,
                        "command",
                    ),
                    RibbonAction(
                        "Sketcher_CreateFillet",
                        "Fillet",
                        True,
                        "command",
                    ),
                ),
            ),
        ),
    )

    resolved = resolve_native_provider_surface(surface, registry)

    assert registry.definition(SKETCH_CONSTRAINT_CAPABILITY_NAME) is None
    assert resolved.available is False
    assert resolved.tool_names == ()
    assert resolved.schemas == ()
    assert resolved.missing_action_ids
    assert SKETCH_CONSTRAINT_CAPABILITY_NAME not in (
        resolved.incomplete_definition_names
    )
