# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json

from jsonschema import Draft202012Validator
import pytest

from VibeCADNativeCapabilityRegistry import (
    MAX_NATIVE_SCHEMAS_JSON_BYTES,
    resolve_native_provider_surface,
)
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeSketchGeometryBindings import SKETCH_GEOMETRY_CAPABILITY_NAME
from VibeCADNativeSketchGeometrySchema import sketch_geometry_capability_definition
from VibeCADRibbonSurface import RibbonAction, RibbonGroup, RibbonSurface


def _arguments() -> dict[str, object]:
    return {
        "operation": "create_point",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 2,
        "expected_constraint_count": 3,
        "position_mm": {"x": 12.5, "y": -4.0},
    }


def test_finished_geometry_variants_match_live_ribbon_actions_exactly() -> None:
    definition = sketch_geometry_capability_definition()

    assert definition.name == SKETCH_GEOMETRY_CAPABILITY_NAME
    assert definition.primary_classification == "mutation"
    variants = {variant.operation: variant for variant in definition.variants}
    assert set(variants) == {
        "create_point",
        "create_line",
        "create_polyline",
        "create_arc",
        "create3_point_arc",
        "create_arc_of_ellipse",
        "create_arc_of_hyperbola",
        "create_arc_of_parabola",
        "create_circle",
        "create3_point_circle",
        "create_ellipse",
        "create3_point_ellipse",
        "create_rectangle",
        "create_center_rectangle",
        "create_oblong",
        "create_triangle",
        "create_square",
        "create_pentagon",
        "create_hexagon",
        "create_heptagon",
        "create_octagon",
        "create_regular_polygon",
        "create_slot",
        "create_arc_slot",
        "create_b_spline",
        "create_periodic_b_spline",
        "create_b_spline_by_interpolation",
        "create_periodic_b_spline_by_interpolation",
        "create_text",
        "toggle_construction",
        "create_fillet",
        "create_chamfer",
        "trim",
        "split",
        "extend",
        "project_external_geometry",
        "intersect_external_geometry",
        "carbon_copy",
        "translate",
        "rotate",
        "scale",
        "offset",
        "symmetry",
        "remove_axis_alignment",
        "convert_to_nurbs",
        "increase_bspline_degree",
        "decrease_bspline_degree",
        "increase_bspline_knot_multiplicity",
        "decrease_bspline_knot_multiplicity",
        "insert_bspline_knot",
        "join_curves",
        "restore_internal_alignment_geometry",
    }
    assert variants["create_point"].action_ids == frozenset({"Sketcher_CreatePoint"})
    assert variants["create_line"].action_ids == frozenset({"Sketcher_CreateLine"})
    assert variants["create_polyline"].action_ids == frozenset(
        {"Sketcher_CreatePolyline"}
    )
    assert variants["create_arc"].action_ids == frozenset({"Sketcher_CreateArc"})
    assert variants["create3_point_arc"].action_ids == frozenset(
        {"Sketcher_Create3PointArc"}
    )
    assert variants["create_arc_of_ellipse"].action_ids == frozenset(
        {"Sketcher_CreateArcOfEllipse"}
    )
    assert variants["create_arc_of_hyperbola"].action_ids == frozenset(
        {"Sketcher_CreateArcOfHyperbola"}
    )
    assert variants["create_arc_of_parabola"].action_ids == frozenset(
        {"Sketcher_CreateArcOfParabola"}
    )
    assert variants["create_circle"].action_ids == frozenset({"Sketcher_CreateCircle"})
    assert variants["create3_point_circle"].action_ids == frozenset(
        {"Sketcher_Create3PointCircle"}
    )
    assert variants["create_ellipse"].action_ids == frozenset(
        {"Sketcher_CreateEllipseByCenter"}
    )
    assert variants["create3_point_ellipse"].action_ids == frozenset(
        {"Sketcher_CreateEllipseBy3Points"}
    )
    assert variants["create_rectangle"].action_ids == frozenset(
        {"Sketcher_CreateRectangle"}
    )
    assert variants["create_center_rectangle"].action_ids == frozenset(
        {"Sketcher_CreateRectangle_Center"}
    )
    assert variants["create_oblong"].action_ids == frozenset({"Sketcher_CreateOblong"})
    assert variants["create_triangle"].action_ids == frozenset(
        {"Sketcher_CreateTriangle"}
    )
    assert variants["create_square"].action_ids == frozenset({"Sketcher_CreateSquare"})
    assert variants["create_pentagon"].action_ids == frozenset(
        {"Sketcher_CreatePentagon"}
    )
    assert variants["create_hexagon"].action_ids == frozenset(
        {"Sketcher_CreateHexagon"}
    )
    assert variants["create_heptagon"].action_ids == frozenset(
        {"Sketcher_CreateHeptagon"}
    )
    assert variants["create_octagon"].action_ids == frozenset(
        {"Sketcher_CreateOctagon"}
    )
    assert variants["create_regular_polygon"].action_ids == frozenset(
        {"Sketcher_CreateRegularPolygon"}
    )
    assert variants["create_slot"].action_ids == frozenset({"Sketcher_CreateSlot"})
    assert variants["create_arc_slot"].action_ids == frozenset(
        {"Sketcher_CreateArcSlot"}
    )
    assert variants["create_b_spline"].action_ids == frozenset(
        {"Sketcher_CreateBSpline"}
    )
    assert variants["create_periodic_b_spline"].action_ids == frozenset(
        {"Sketcher_CreatePeriodicBSpline"}
    )
    assert variants["create_b_spline_by_interpolation"].action_ids == frozenset(
        {"Sketcher_CreateBSplineByInterpolation"}
    )
    assert variants[
        "create_periodic_b_spline_by_interpolation"
    ].action_ids == frozenset({"Sketcher_CreatePeriodicBSplineByInterpolation"})
    assert variants["create_text"].action_ids == frozenset({"Sketcher_CreateText"})
    assert variants["toggle_construction"].action_ids == frozenset(
        {"Sketcher_ToggleConstruction"}
    )
    assert variants["create_fillet"].action_ids == frozenset({"Sketcher_CreateFillet"})
    assert variants["create_chamfer"].action_ids == frozenset(
        {"Sketcher_CreateChamfer"}
    )
    assert variants["trim"].action_ids == frozenset({"Sketcher_Trimming"})
    assert variants["split"].action_ids == frozenset({"Sketcher_Split"})
    assert variants["extend"].action_ids == frozenset({"Sketcher_Extend"})
    assert variants["project_external_geometry"].action_ids == frozenset(
        {"Sketcher_Projection"}
    )
    assert variants["intersect_external_geometry"].action_ids == frozenset(
        {"Sketcher_Intersection"}
    )
    assert variants["carbon_copy"].action_ids == frozenset({"Sketcher_CarbonCopy"})
    assert variants["translate"].action_ids == frozenset({"Sketcher_Translate"})
    assert variants["rotate"].action_ids == frozenset({"Sketcher_Rotate"})
    assert variants["scale"].action_ids == frozenset({"Sketcher_Scale"})
    assert variants["offset"].action_ids == frozenset({"Sketcher_Offset"})
    assert variants["symmetry"].action_ids == frozenset({"Sketcher_Symmetry"})
    assert variants["remove_axis_alignment"].action_ids == frozenset(
        {"Sketcher_RemoveAxesAlignment"}
    )
    assert variants["convert_to_nurbs"].action_ids == frozenset(
        {"Sketcher_BSplineConvertToNURBS"}
    )
    assert variants["increase_bspline_degree"].action_ids == frozenset(
        {"Sketcher_BSplineIncreaseDegree"}
    )
    assert variants["decrease_bspline_degree"].action_ids == frozenset(
        {"Sketcher_BSplineDecreaseDegree"}
    )
    assert variants["increase_bspline_knot_multiplicity"].action_ids == frozenset(
        {"Sketcher_BSplineIncreaseKnotMultiplicity"}
    )
    assert variants["decrease_bspline_knot_multiplicity"].action_ids == frozenset(
        {"Sketcher_BSplineDecreaseKnotMultiplicity"}
    )
    assert variants["insert_bspline_knot"].action_ids == frozenset(
        {"Sketcher_BSplineInsertKnot"}
    )
    assert variants["join_curves"].action_ids == frozenset({"Sketcher_JoinCurves"})
    assert variants["restore_internal_alignment_geometry"].action_ids == frozenset(
        {"Sketcher_RestoreInternalAlignmentGeometry"}
    )
    for variant in variants.values():
        assert variant.surface_ids == frozenset({"sketch.edit"})
        expected_target = {
            "toggle_construction": "ActiveSketchExactGeometryAndExpectedStates",
            "create_fillet": "ActiveSketchExactFilletTargetAndExpectedState",
            "create_chamfer": "ActiveSketchExactChamferTargetAndExpectedState",
            "trim": "ActiveSketchExactTrimTargetAndExpectedState",
            "split": "ActiveSketchExactSplitTargetAndExpectedState",
            "extend": "ActiveSketchExactExtendTargetAndExpectedState",
            "project_external_geometry": (
                "ActiveSketchExactExternalSourceAndExpectedState"
            ),
            "intersect_external_geometry": (
                "ActiveSketchExactExternalSourceAndExpectedState"
            ),
            "carbon_copy": "ActiveSketchExactCarbonCopySourceAndExpectedState",
            "translate": "ActiveSketchExactTranslateTargetsAndExpectedState",
            "rotate": "ActiveSketchExactRotateTargetsAndExpectedState",
            "scale": "ActiveSketchExactScaleTargetsAndExpectedState",
            "offset": "ActiveSketchExactOffsetTargetsAndExpectedState",
            "symmetry": "ActiveSketchExactSymmetryTargetsAndExpectedState",
            "remove_axis_alignment": (
                "ActiveSketchExactInternalGeometryAndExpectedConstraintState"
            ),
            "convert_to_nurbs": "ActiveSketchExactConvertibleEdgesAndExpectedState",
            "increase_bspline_degree": (
                "ActiveSketchExactBSplineEdgesAndExpectedState"
            ),
            "decrease_bspline_degree": ("ActiveSketchExactBSplineAndMaximumDeviation"),
            "increase_bspline_knot_multiplicity": (
                "ActiveSketchExactBSplineKnotAndExpectedState"
            ),
            "decrease_bspline_knot_multiplicity": (
                "ActiveSketchExactBSplineKnotAndMaximumDeviation"
            ),
            "insert_bspline_knot": "ActiveSketchExactBSplineAndParameter",
            "join_curves": "ActiveSketchExactCurveEndpointPair",
            "restore_internal_alignment_geometry": (
                "ActiveSketchExactInternalAlignmentTargets"
            ),
        }.get(variant.operation, "ActiveSketchAndExpectedStateCounts")
        assert variant.exact_target_type == expected_target
        assert variant.transaction_behavior == "document"
        assert variant.background_required is False


def test_point_provider_schema_is_closed_and_bounded() -> None:
    schema = sketch_geometry_capability_definition().provider_schema(("create_point",))
    validator = Draft202012Validator(schema["parameters"])
    valid = _arguments()

    assert list(validator.iter_errors(valid)) == []
    invalid = []
    for missing in (
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "position_mm",
    ):
        value = dict(valid)
        del value[missing]
        invalid.append(value)
    invalid.extend(
        (
            {**valid, "unexpected": True},
            {**valid, "expected_geometry_count": -1},
            {**valid, "expected_constraint_count": 1.25},
            {**valid, "position_mm": {"x": 1_000_001.0, "y": 0.0}},
            {**valid, "position_mm": {"x": 0.0, "y": 0.0, "z": 0.0}},
            {**valid, "sketch": {"object_name": "Bad Name"}},
        )
    )
    assert all(list(validator.iter_errors(value)) for value in invalid)
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 890
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_line_provider_schema_is_closed_and_bounded() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("create_line",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "create_line",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 2,
        "expected_constraint_count": 3,
        "start_mm": {"x": -2.0, "y": 3.0},
        "end_mm": {"x": 8.0, "y": -1.0},
    }

    assert list(validator.iter_errors(valid)) == []
    assert list(validator.iter_errors({**valid, "unexpected": True}))
    assert list(validator.iter_errors({**valid, "end_mm": {"x": 8.0}}))
    encoded = json.dumps(
        [definition.provider_schema(("create_point", "create_line"))],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 1546
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_polyline_provider_schema_is_closed_and_bounded() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("create_polyline",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "create_polyline",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 2,
        "expected_constraint_count": 3,
        "vertices_mm": [
            {"x": 0.0, "y": 0.0},
            {"x": 5.0, "y": 2.0},
            {"x": 9.0, "y": -1.0},
        ],
        "closed": False,
    }

    assert list(validator.iter_errors(valid)) == []
    assert list(validator.iter_errors({**valid, "closed": "false"}))
    assert list(
        validator.iter_errors({**valid, "vertices_mm": valid["vertices_mm"][:1]})
    )
    encoded = json.dumps(
        [
            definition.provider_schema(
                ("create_point", "create_line", "create_polyline")
            )
        ],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 1969
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_center_radius_arc_provider_schema_is_closed_and_bounded() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("create_arc",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "create_arc",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 2,
        "expected_constraint_count": 3,
        "center_mm": {"x": 5.0, "y": -3.0},
        "radius_mm": 8.0,
        "start_angle_degrees": 30.0,
        "sweep_angle_degrees": 120.0,
    }

    assert list(validator.iter_errors(valid)) == []
    for invalid in (
        {**valid, "radius_mm": 0.0},
        {**valid, "start_angle_degrees": -1.0},
        {**valid, "start_angle_degrees": 360.0},
        {**valid, "sweep_angle_degrees": 0.0},
        {**valid, "sweep_angle_degrees": 360.0},
        {**valid, "unexpected": True},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [
            definition.provider_schema(
                ("create_point", "create_line", "create_polyline", "create_arc")
            )
        ],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 2564
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_three_point_arc_provider_schema_is_closed_and_bounded() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("create3_point_arc",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "create3_point_arc",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 2,
        "expected_constraint_count": 3,
        "first_endpoint_mm": {"x": 0.0, "y": 0.0},
        "second_endpoint_mm": {"x": 10.0, "y": 0.0},
        "rim_point_mm": {"x": 5.0, "y": 5.0},
    }

    assert list(validator.iter_errors(valid)) == []
    assert list(validator.iter_errors({**valid, "rim_point_mm": {"x": 5.0}}))
    assert list(validator.iter_errors({**valid, "unexpected": True}))
    encoded = json.dumps(
        [
            definition.provider_schema(
                (
                    "create_point",
                    "create_line",
                    "create_polyline",
                    "create_arc",
                    "create3_point_arc",
                )
            )
        ],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 3388
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_elliptical_arc_provider_schema_is_closed_and_bounded() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("create_arc_of_ellipse",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "create_arc_of_ellipse",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 2,
        "expected_constraint_count": 3,
        "center_mm": {"x": 4.0, "y": -2.0},
        "major_radius_mm": 10.0,
        "minor_radius_mm": 4.0,
        "rotation_degrees": 30.0,
        "start_parameter_degrees": 20.0,
        "sweep_parameter_degrees": 130.0,
    }

    assert list(validator.iter_errors(valid)) == []
    for invalid in (
        {**valid, "major_radius_mm": 0.0},
        {**valid, "minor_radius_mm": 0.0},
        {**valid, "rotation_degrees": 360.0},
        {**valid, "start_parameter_degrees": -1.0},
        {**valid, "sweep_parameter_degrees": 0.0},
        {**valid, "sweep_parameter_degrees": 360.0},
        {**valid, "unexpected": True},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [
            definition.provider_schema(
                (
                    "create_point",
                    "create_line",
                    "create_polyline",
                    "create_arc",
                    "create3_point_arc",
                    "create_arc_of_ellipse",
                )
            )
        ],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 4011
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_hyperbolic_arc_provider_schema_is_closed_and_bounded() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("create_arc_of_hyperbola",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "create_arc_of_hyperbola",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 2,
        "expected_constraint_count": 3,
        "center_mm": {"x": 4.0, "y": -2.0},
        "major_radius_mm": 5.0,
        "minor_radius_mm": 3.0,
        "rotation_degrees": 15.0,
        "start_parameter": -1.0,
        "end_parameter": 1.0,
    }

    assert list(validator.iter_errors(valid)) == []
    for invalid in (
        {**valid, "major_radius_mm": 0.0},
        {**valid, "minor_radius_mm": 0.0},
        {**valid, "rotation_degrees": 360.0},
        {**valid, "start_parameter": -20.1},
        {**valid, "end_parameter": 20.1},
        {**valid, "unexpected": True},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [
            definition.provider_schema(
                (
                    "create_point",
                    "create_line",
                    "create_polyline",
                    "create_arc",
                    "create3_point_arc",
                    "create_arc_of_ellipse",
                    "create_arc_of_hyperbola",
                )
            )
        ],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 4357
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_parabolic_arc_provider_schema_is_closed_and_bounded() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("create_arc_of_parabola",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "create_arc_of_parabola",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 2,
        "expected_constraint_count": 3,
        "vertex_mm": {"x": 4.0, "y": -2.0},
        "focal_length_mm": 5.0,
        "rotation_degrees": 15.0,
        "start_parameter_mm": -4.0,
        "end_parameter_mm": 6.0,
    }

    assert list(validator.iter_errors(valid)) == []
    for invalid in (
        {**valid, "focal_length_mm": 0.0},
        {**valid, "rotation_degrees": 360.0},
        {**valid, "start_parameter_mm": -1_000_001.0},
        {**valid, "end_parameter_mm": 1_000_001.0},
        {**valid, "unexpected": True},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [
            definition.provider_schema(
                (
                    "create_point",
                    "create_line",
                    "create_polyline",
                    "create_arc",
                    "create3_point_arc",
                    "create_arc_of_ellipse",
                    "create_arc_of_hyperbola",
                    "create_arc_of_parabola",
                )
            )
        ],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 5004
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_circle_provider_schema_is_closed_and_bounded() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("create_circle",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "create_circle",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 2,
        "expected_constraint_count": 3,
        "center_mm": {"x": 4.0, "y": -2.0},
        "radius_mm": 5.0,
    }

    assert list(validator.iter_errors(valid)) == []
    for invalid in (
        {**valid, "radius_mm": 0.0},
        {**valid, "center_mm": {"x": 1_000_001.0, "y": 0.0}},
        {**valid, "unexpected": True},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [
            definition.provider_schema(
                (
                    "create_point",
                    "create_line",
                    "create_polyline",
                    "create_arc",
                    "create3_point_arc",
                    "create_arc_of_ellipse",
                    "create_arc_of_hyperbola",
                    "create_arc_of_parabola",
                    "create_circle",
                )
            )
        ],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 5127
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_three_point_circle_provider_schema_is_closed_and_bounded() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("create3_point_circle",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "create3_point_circle",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 2,
        "expected_constraint_count": 3,
        "first_point_mm": {"x": -5.0, "y": 0.0},
        "second_point_mm": {"x": 5.0, "y": 0.0},
        "third_point_mm": {"x": 0.0, "y": 5.0},
    }
    assert list(validator.iter_errors(valid)) == []
    assert list(validator.iter_errors({**valid, "third_point_mm": {"x": 0.0}}))
    assert list(validator.iter_errors({**valid, "unexpected": True}))
    encoded = json.dumps(
        [
            definition.provider_schema(
                (
                    "create_point",
                    "create_line",
                    "create_polyline",
                    "create_arc",
                    "create3_point_arc",
                    "create_arc_of_ellipse",
                    "create_arc_of_hyperbola",
                    "create_arc_of_parabola",
                    "create_circle",
                    "create3_point_circle",
                )
            )
        ],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 5952
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_center_ellipse_provider_schema_is_closed_and_bounded() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("create_ellipse",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "create_ellipse",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 2,
        "expected_constraint_count": 3,
        "center_mm": {"x": 4.0, "y": -2.0},
        "major_radius_mm": 8.0,
        "minor_radius_mm": 3.0,
        "rotation_degrees": 30.0,
    }
    assert list(validator.iter_errors(valid)) == []
    for invalid in (
        {**valid, "major_radius_mm": 0.0},
        {**valid, "minor_radius_mm": 0.0},
        {**valid, "rotation_degrees": 360.0},
        {**valid, "unexpected": True},
    ):
        assert list(validator.iter_errors(invalid))
    operations = (
        "create_point",
        "create_line",
        "create_polyline",
        "create_arc",
        "create3_point_arc",
        "create_arc_of_ellipse",
        "create_arc_of_hyperbola",
        "create_arc_of_parabola",
        "create_circle",
        "create3_point_circle",
        "create_ellipse",
    )
    encoded = json.dumps(
        [definition.provider_schema(operations)],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 6117
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_three_point_ellipse_provider_schema_is_closed_and_bounded() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("create3_point_ellipse",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "create3_point_ellipse",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 2,
        "expected_constraint_count": 3,
        "first_axis_endpoint_mm": {"x": -8.0, "y": 0.0},
        "second_axis_endpoint_mm": {"x": 8.0, "y": 0.0},
        "rim_point_mm": {"x": 0.0, "y": 3.0},
    }
    assert list(validator.iter_errors(valid)) == []
    assert list(validator.iter_errors({**valid, "rim_point_mm": {"x": 0.0}}))
    assert list(validator.iter_errors({**valid, "unexpected": True}))
    operations = (
        "create_point",
        "create_line",
        "create_polyline",
        "create_arc",
        "create3_point_arc",
        "create_arc_of_ellipse",
        "create_arc_of_hyperbola",
        "create_arc_of_parabola",
        "create_circle",
        "create3_point_circle",
        "create_ellipse",
        "create3_point_ellipse",
    )
    encoded = json.dumps(
        [definition.provider_schema(operations)],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 6757
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_corner_rectangle_provider_schema_is_closed_and_bounded() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("create_rectangle",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "create_rectangle",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 2,
        "expected_constraint_count": 3,
        "first_corner_mm": {"x": -8.0, "y": -3.0},
        "opposite_corner_mm": {"x": 6.0, "y": 5.0},
    }
    assert list(validator.iter_errors(valid)) == []
    assert list(validator.iter_errors({**valid, "first_corner_mm": {"x": -8.0}}))
    assert list(validator.iter_errors({**valid, "unexpected": True}))
    operations = (
        "create_point",
        "create_line",
        "create_polyline",
        "create_arc",
        "create3_point_arc",
        "create_arc_of_ellipse",
        "create_arc_of_hyperbola",
        "create_arc_of_parabola",
        "create_circle",
        "create3_point_circle",
        "create_ellipse",
        "create3_point_ellipse",
        "create_rectangle",
    )
    encoded = json.dumps(
        [definition.provider_schema(operations)],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 7345
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_center_rectangle_provider_schema_is_closed_and_bounded() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("create_center_rectangle",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "create_center_rectangle",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 2,
        "expected_constraint_count": 3,
        "center_mm": {"x": 2.0, "y": 1.0},
        "corner_mm": {"x": 8.0, "y": 5.0},
    }
    assert list(validator.iter_errors(valid)) == []
    assert list(validator.iter_errors({**valid, "corner_mm": {"x": 8.0}}))
    assert list(validator.iter_errors({**valid, "unexpected": True}))
    operations = (
        "create_point",
        "create_line",
        "create_polyline",
        "create_arc",
        "create3_point_arc",
        "create_arc_of_ellipse",
        "create_arc_of_hyperbola",
        "create_arc_of_parabola",
        "create_circle",
        "create3_point_circle",
        "create_ellipse",
        "create3_point_ellipse",
        "create_rectangle",
        "create_center_rectangle",
    )
    encoded = json.dumps(
        [definition.provider_schema(operations)],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 7711
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_oblong_provider_schema_is_closed_and_bounded() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("create_oblong",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "create_oblong",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 2,
        "expected_constraint_count": 3,
        "first_corner_mm": {"x": -10.0, "y": -6.0},
        "opposite_corner_mm": {"x": 10.0, "y": 6.0},
        "radius_mm": 2.0,
    }
    assert list(validator.iter_errors(valid)) == []
    assert list(validator.iter_errors({**valid, "radius_mm": 0.0}))
    assert list(validator.iter_errors({**valid, "unexpected": True}))
    operations = (
        "create_point",
        "create_line",
        "create_polyline",
        "create_arc",
        "create3_point_arc",
        "create_arc_of_ellipse",
        "create_arc_of_hyperbola",
        "create_arc_of_parabola",
        "create_circle",
        "create3_point_circle",
        "create_ellipse",
        "create3_point_ellipse",
        "create_rectangle",
        "create_center_rectangle",
        "create_oblong",
    )
    encoded = json.dumps(
        [definition.provider_schema(operations)],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 7859
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_triangle_provider_schema_is_closed_and_bounded() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("create_triangle",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "create_triangle",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 2,
        "expected_constraint_count": 3,
        "center_mm": {"x": 1.0, "y": -2.0},
        "corner_mm": {"x": 8.0, "y": 4.0},
    }
    assert list(validator.iter_errors(valid)) == []
    assert list(validator.iter_errors({**valid, "corner_mm": {"x": 8.0}}))
    assert list(validator.iter_errors({**valid, "unexpected": True}))
    operations = (
        "create_point",
        "create_line",
        "create_polyline",
        "create_arc",
        "create3_point_arc",
        "create_arc_of_ellipse",
        "create_arc_of_hyperbola",
        "create_arc_of_parabola",
        "create_circle",
        "create3_point_circle",
        "create_ellipse",
        "create3_point_ellipse",
        "create_rectangle",
        "create_center_rectangle",
        "create_oblong",
        "create_triangle",
    )
    encoded = json.dumps(
        [definition.provider_schema(operations)],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 7910
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_square_provider_schema_is_closed_and_bounded() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("create_square",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "create_square",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 2,
        "expected_constraint_count": 3,
        "center_mm": {"x": -2.0, "y": 1.0},
        "corner_mm": {"x": 5.0, "y": 8.0},
    }
    assert list(validator.iter_errors(valid)) == []
    assert list(validator.iter_errors({**valid, "center_mm": {"y": 1.0}}))
    assert list(validator.iter_errors({**valid, "unexpected": True}))
    operations = (
        "create_point",
        "create_line",
        "create_polyline",
        "create_arc",
        "create3_point_arc",
        "create_arc_of_ellipse",
        "create_arc_of_hyperbola",
        "create_arc_of_parabola",
        "create_circle",
        "create3_point_circle",
        "create_ellipse",
        "create3_point_ellipse",
        "create_rectangle",
        "create_center_rectangle",
        "create_oblong",
        "create_triangle",
        "create_square",
    )
    encoded = json.dumps(
        [definition.provider_schema(operations)],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 7955
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_pentagon_provider_schema_is_closed_and_bounded() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("create_pentagon",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "create_pentagon",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 2,
        "expected_constraint_count": 3,
        "center_mm": {"x": 2.0, "y": 3.0},
        "corner_mm": {"x": 9.0, "y": 7.0},
    }
    assert list(validator.iter_errors(valid)) == []
    assert list(validator.iter_errors({**valid, "corner_mm": {"x": 9.0}}))
    assert list(validator.iter_errors({**valid, "unexpected": True}))
    operations = (
        "create_point",
        "create_line",
        "create_polyline",
        "create_arc",
        "create3_point_arc",
        "create_arc_of_ellipse",
        "create_arc_of_hyperbola",
        "create_arc_of_parabola",
        "create_circle",
        "create3_point_circle",
        "create_ellipse",
        "create3_point_ellipse",
        "create_rectangle",
        "create_center_rectangle",
        "create_oblong",
        "create_triangle",
        "create_square",
        "create_pentagon",
    )
    encoded = json.dumps(
        [definition.provider_schema(operations)],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 8006
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_hexagon_provider_schema_is_closed_and_bounded() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("create_hexagon",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "create_hexagon",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 2,
        "expected_constraint_count": 3,
        "center_mm": {"x": -3.0, "y": 4.0},
        "corner_mm": {"x": 6.0, "y": 4.0},
    }
    assert list(validator.iter_errors(valid)) == []
    assert list(validator.iter_errors({**valid, "center_mm": {"x": -3.0}}))
    assert list(validator.iter_errors({**valid, "unexpected": True}))
    operations = (
        "create_point",
        "create_line",
        "create_polyline",
        "create_arc",
        "create3_point_arc",
        "create_arc_of_ellipse",
        "create_arc_of_hyperbola",
        "create_arc_of_parabola",
        "create_circle",
        "create3_point_circle",
        "create_ellipse",
        "create3_point_ellipse",
        "create_rectangle",
        "create_center_rectangle",
        "create_oblong",
        "create_triangle",
        "create_square",
        "create_pentagon",
        "create_hexagon",
    )
    encoded = json.dumps(
        [definition.provider_schema(operations)],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 8054
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_heptagon_provider_schema_is_closed_and_bounded() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("create_heptagon",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "create_heptagon",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 2,
        "expected_constraint_count": 3,
        "center_mm": {"x": 3.0, "y": -4.0},
        "corner_mm": {"x": 9.0, "y": 2.0},
    }
    assert list(validator.iter_errors(valid)) == []
    assert list(validator.iter_errors({**valid, "corner_mm": {"y": 2.0}}))
    assert list(validator.iter_errors({**valid, "unexpected": True}))
    operations = (
        "create_point",
        "create_line",
        "create_polyline",
        "create_arc",
        "create3_point_arc",
        "create_arc_of_ellipse",
        "create_arc_of_hyperbola",
        "create_arc_of_parabola",
        "create_circle",
        "create3_point_circle",
        "create_ellipse",
        "create3_point_ellipse",
        "create_rectangle",
        "create_center_rectangle",
        "create_oblong",
        "create_triangle",
        "create_square",
        "create_pentagon",
        "create_hexagon",
        "create_heptagon",
    )
    encoded = json.dumps(
        [definition.provider_schema(operations)],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 8105
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_octagon_provider_schema_is_closed_and_bounded() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("create_octagon",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "create_octagon",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 2,
        "expected_constraint_count": 3,
        "center_mm": {"x": -5.0, "y": -1.0},
        "corner_mm": {"x": 2.0, "y": 6.0},
    }
    assert list(validator.iter_errors(valid)) == []
    assert list(validator.iter_errors({**valid, "center_mm": {"y": -1.0}}))
    assert list(validator.iter_errors({**valid, "unexpected": True}))
    operations = (
        "create_point",
        "create_line",
        "create_polyline",
        "create_arc",
        "create3_point_arc",
        "create_arc_of_ellipse",
        "create_arc_of_hyperbola",
        "create_arc_of_parabola",
        "create_circle",
        "create3_point_circle",
        "create_ellipse",
        "create3_point_ellipse",
        "create_rectangle",
        "create_center_rectangle",
        "create_oblong",
        "create_triangle",
        "create_square",
        "create_pentagon",
        "create_hexagon",
        "create_heptagon",
        "create_octagon",
    )
    encoded = json.dumps(
        [definition.provider_schema(operations)],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 8153
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_arbitrary_regular_polygon_provider_schema_is_closed_and_bounded() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("create_regular_polygon",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "create_regular_polygon",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 2,
        "expected_constraint_count": 3,
        "center_mm": {"x": 0.0, "y": 0.0},
        "corner_mm": {"x": 8.0, "y": 3.0},
        "side_count": 9,
    }
    assert list(validator.iter_errors(valid)) == []
    for invalid_count in (2, 10_000, 4.5, True):
        assert list(validator.iter_errors({**valid, "side_count": invalid_count}))
    assert list(validator.iter_errors({**valid, "unexpected": True}))
    operations = (
        "create_point",
        "create_line",
        "create_polyline",
        "create_arc",
        "create3_point_arc",
        "create_arc_of_ellipse",
        "create_arc_of_hyperbola",
        "create_arc_of_parabola",
        "create_circle",
        "create3_point_circle",
        "create_ellipse",
        "create3_point_ellipse",
        "create_rectangle",
        "create_center_rectangle",
        "create_oblong",
        "create_triangle",
        "create_square",
        "create_pentagon",
        "create_hexagon",
        "create_heptagon",
        "create_octagon",
        "create_regular_polygon",
    )
    encoded = json.dumps(
        [definition.provider_schema(operations)],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 8373
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_straight_slot_provider_schema_is_closed_and_bounded() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("create_slot",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "create_slot",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 2,
        "expected_constraint_count": 3,
        "start_center_mm": {"x": -10.0, "y": 1.0},
        "end_center_mm": {"x": 10.0, "y": 1.0},
        "radius_mm": 3.0,
    }
    assert list(validator.iter_errors(valid)) == []
    for invalid in (
        {**valid, "radius_mm": 0.0},
        {**valid, "end_center_mm": {"x": 10.0}},
        {**valid, "unexpected": True},
    ):
        assert list(validator.iter_errors(invalid))
    operations = (
        "create_point",
        "create_line",
        "create_polyline",
        "create_arc",
        "create3_point_arc",
        "create_arc_of_ellipse",
        "create_arc_of_hyperbola",
        "create_arc_of_parabola",
        "create_circle",
        "create3_point_circle",
        "create_ellipse",
        "create3_point_ellipse",
        "create_rectangle",
        "create_center_rectangle",
        "create_oblong",
        "create_triangle",
        "create_square",
        "create_pentagon",
        "create_hexagon",
        "create_heptagon",
        "create_octagon",
        "create_regular_polygon",
        "create_slot",
    )
    encoded = json.dumps(
        [definition.provider_schema(operations)],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 8946
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_arc_slot_provider_schema_is_closed_and_bounded() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("create_arc_slot",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "create_arc_slot",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 2,
        "expected_constraint_count": 3,
        "center_mm": {"x": 0.0, "y": 0.0},
        "centerline_radius_mm": 12.0,
        "start_angle_degrees": 20.0,
        "sweep_angle_degrees": -120.0,
        "slot_radius_mm": 2.0,
    }
    assert list(validator.iter_errors(valid)) == []
    for invalid in (
        {**valid, "centerline_radius_mm": 0.0},
        {**valid, "start_angle_degrees": 360.0},
        {**valid, "sweep_angle_degrees": 0.0},
        {**valid, "sweep_angle_degrees": -360.0},
        {**valid, "sweep_angle_degrees": 360.0},
        {**valid, "slot_radius_mm": 0.0},
        {**valid, "unexpected": True},
    ):
        assert list(validator.iter_errors(invalid))
    operations = tuple(
        variant.operation
        for variant in definition.variants
        if variant.operation
        not in {
            "create_b_spline",
            "create_periodic_b_spline",
            "create_b_spline_by_interpolation",
            "create_periodic_b_spline_by_interpolation",
            "create_text",
            "toggle_construction",
            "create_fillet",
            "create_chamfer",
            "trim",
            "split",
            "extend",
            "project_external_geometry",
            "intersect_external_geometry",
            "carbon_copy",
            "translate",
            "rotate",
            "scale",
            "offset",
            "symmetry",
            "remove_axis_alignment",
            "convert_to_nurbs",
            "increase_bspline_degree",
            "decrease_bspline_degree",
            "increase_bspline_knot_multiplicity",
            "decrease_bspline_knot_multiplicity",
            "insert_bspline_knot",
            "join_curves",
            "restore_internal_alignment_geometry",
        }
    )
    encoded = json.dumps(
        [definition.provider_schema(operations)],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 9444
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_nonperiodic_bspline_provider_schema_is_closed_and_bounded() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("create_b_spline",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "create_b_spline",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 2,
        "expected_constraint_count": 3,
        "control_points_mm": [
            {"x": 0.0, "y": 0.0},
            {"x": 5.0, "y": 8.0},
            {"x": 12.0, "y": 8.0},
            {"x": 18.0, "y": 0.0},
        ],
        "degree": 3,
    }
    assert list(validator.iter_errors(valid)) == []
    for invalid in (
        {**valid, "control_points_mm": valid["control_points_mm"][:1]},
        {**valid, "control_points_mm": valid["control_points_mm"] * 7},
        {**valid, "control_points_mm": [{"x": 0.0, "y": 0.0, "z": 0.0}] * 2},
        {**valid, "degree": 0},
        {**valid, "degree": 26},
        {**valid, "unexpected": True},
    ):
        assert list(validator.iter_errors(invalid))
    operations = tuple(
        variant.operation
        for variant in definition.variants
        if variant.operation
        not in {
            "create_periodic_b_spline",
            "create_b_spline_by_interpolation",
            "create_periodic_b_spline_by_interpolation",
            "create_text",
            "toggle_construction",
            "create_fillet",
            "create_chamfer",
            "trim",
            "split",
            "extend",
            "project_external_geometry",
            "intersect_external_geometry",
            "carbon_copy",
            "translate",
            "rotate",
            "scale",
            "offset",
            "symmetry",
            "remove_axis_alignment",
            "convert_to_nurbs",
            "increase_bspline_degree",
            "decrease_bspline_degree",
            "increase_bspline_knot_multiplicity",
            "decrease_bspline_knot_multiplicity",
            "insert_bspline_knot",
            "join_curves",
            "restore_internal_alignment_geometry",
        }
    )
    encoded = json.dumps(
        [definition.provider_schema(operations)],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 9904
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_periodic_bspline_provider_schema_is_closed_and_bounded() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("create_periodic_b_spline",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "create_periodic_b_spline",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 2,
        "expected_constraint_count": 3,
        "control_points_mm": [
            {"x": 0.0, "y": 0.0},
            {"x": 8.0, "y": 1.0},
            {"x": 12.0, "y": 7.0},
            {"x": 5.0, "y": 12.0},
            {"x": -3.0, "y": 7.0},
        ],
        "degree": 3,
    }
    assert list(validator.iter_errors(valid)) == []
    for invalid in (
        {**valid, "control_points_mm": valid["control_points_mm"][:1]},
        {**valid, "control_points_mm": valid["control_points_mm"] * 5},
        {**valid, "degree": 0},
        {**valid, "degree": 26},
        {**valid, "unexpected": True},
    ):
        assert list(validator.iter_errors(invalid))
    operations = tuple(
        variant.operation
        for variant in definition.variants
        if variant.operation
        not in {
            "create_b_spline_by_interpolation",
            "create_periodic_b_spline_by_interpolation",
            "create_text",
            "toggle_construction",
            "create_fillet",
            "create_chamfer",
            "trim",
            "split",
            "extend",
            "project_external_geometry",
            "intersect_external_geometry",
            "carbon_copy",
            "translate",
            "rotate",
            "scale",
            "offset",
            "symmetry",
            "remove_axis_alignment",
            "convert_to_nurbs",
            "increase_bspline_degree",
            "decrease_bspline_degree",
            "increase_bspline_knot_multiplicity",
            "decrease_bspline_knot_multiplicity",
            "insert_bspline_knot",
            "join_curves",
            "restore_internal_alignment_geometry",
        }
    )
    encoded = json.dumps(
        [definition.provider_schema(operations)],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 9982
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_interpolated_bspline_provider_schema_is_closed_and_bounded() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("create_b_spline_by_interpolation",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "create_b_spline_by_interpolation",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 2,
        "expected_constraint_count": 3,
        "interpolation_points_mm": [
            {"x": 0.0, "y": 0.0},
            {"x": 5.0, "y": 8.0},
            {"x": 12.0, "y": 7.0},
            {"x": 18.0, "y": 0.0},
        ],
    }
    assert list(validator.iter_errors(valid)) == []
    for invalid in (
        {**valid, "interpolation_points_mm": valid["interpolation_points_mm"][:1]},
        {**valid, "interpolation_points_mm": valid["interpolation_points_mm"] * 7},
        {
            **valid,
            "interpolation_points_mm": [{"x": 0.0, "y": 0.0, "z": 0.0}] * 2,
        },
        {**valid, "unexpected": True},
    ):
        assert list(validator.iter_errors(invalid))
    operations = tuple(
        variant.operation
        for variant in definition.variants
        if variant.operation
        not in {
            "create_periodic_b_spline_by_interpolation",
            "create_text",
            "toggle_construction",
            "create_fillet",
            "create_chamfer",
            "trim",
            "split",
            "extend",
            "project_external_geometry",
            "intersect_external_geometry",
            "carbon_copy",
            "translate",
            "rotate",
            "scale",
            "offset",
            "symmetry",
            "remove_axis_alignment",
            "convert_to_nurbs",
            "increase_bspline_degree",
            "decrease_bspline_degree",
            "increase_bspline_knot_multiplicity",
            "decrease_bspline_knot_multiplicity",
            "insert_bspline_knot",
            "join_curves",
            "restore_internal_alignment_geometry",
        }
    )
    encoded = json.dumps(
        [definition.provider_schema(operations)],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 10445
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_periodic_interpolated_bspline_provider_schema_is_closed_and_bounded() -> None:
    definition = sketch_geometry_capability_definition()
    operation = "create_periodic_b_spline_by_interpolation"
    schema = definition.provider_schema((operation,))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": operation,
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 2,
        "expected_constraint_count": 3,
        "interpolation_points_mm": [
            {"x": 0.0, "y": 0.0},
            {"x": 8.0, "y": 1.0},
            {"x": 12.0, "y": 7.0},
            {"x": 1.0, "y": 10.0},
        ],
    }
    assert list(validator.iter_errors(valid)) == []
    for invalid in (
        {**valid, "interpolation_points_mm": valid["interpolation_points_mm"][:1]},
        {**valid, "interpolation_points_mm": valid["interpolation_points_mm"] * 7},
        {**valid, "unexpected": True},
    ):
        assert list(validator.iter_errors(invalid))
    operations = tuple(
        variant.operation
        for variant in definition.variants
        if variant.operation
        not in {
            "create_text",
            "toggle_construction",
            "create_fillet",
            "create_chamfer",
            "trim",
            "split",
            "extend",
            "project_external_geometry",
            "intersect_external_geometry",
            "carbon_copy",
            "translate",
            "rotate",
            "scale",
            "offset",
            "symmetry",
            "remove_axis_alignment",
            "convert_to_nurbs",
            "increase_bspline_degree",
            "decrease_bspline_degree",
            "increase_bspline_knot_multiplicity",
            "decrease_bspline_knot_multiplicity",
            "insert_bspline_knot",
            "join_curves",
            "restore_internal_alignment_geometry",
        }
    )
    encoded = json.dumps(
        [definition.provider_schema(operations)],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 10574
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_text_provider_schema_is_closed_and_bounded() -> None:
    definition = sketch_geometry_capability_definition()
    operation = "create_text"
    schema = definition.provider_schema((operation,))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": operation,
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 2,
        "expected_constraint_count": 3,
        "text": "AI",
        "font_name": "default",
        "handle_start_mm": {"x": 2.0, "y": 3.0},
        "handle_end_mm": {"x": 42.0, "y": 3.0},
        "sizing_mode": "width",
    }
    assert list(validator.iter_errors(valid)) == []
    for invalid in (
        {**valid, "text": ""},
        {**valid, "text": "x" * 65},
        {**valid, "font_name": ""},
        {**valid, "font_name": "x" * 129},
        {**valid, "handle_start_mm": {"x": 2.0, "y": 3.0, "z": 0.0}},
        {**valid, "sizing_mode": "length"},
        {**valid, "unexpected": True},
    ):
        assert list(validator.iter_errors(invalid))
    operations = tuple(
        variant.operation
        for variant in definition.variants
        if variant.operation
        not in {
            "toggle_construction",
            "create_fillet",
            "create_chamfer",
            "trim",
            "split",
            "extend",
            "project_external_geometry",
            "intersect_external_geometry",
            "carbon_copy",
            "translate",
            "rotate",
            "scale",
            "offset",
            "symmetry",
            "remove_axis_alignment",
            "convert_to_nurbs",
            "increase_bspline_degree",
            "decrease_bspline_degree",
            "increase_bspline_knot_multiplicity",
            "decrease_bspline_knot_multiplicity",
            "insert_bspline_knot",
            "join_curves",
            "restore_internal_alignment_geometry",
        }
    )
    encoded = json.dumps(
        [definition.provider_schema(operations)],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 11336
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_construction_provider_schema_is_closed_bounded_and_exact() -> None:
    definition = sketch_geometry_capability_definition()
    operation = "toggle_construction"
    schema = definition.provider_schema((operation,))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": operation,
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 12,
        "expected_constraint_count": 4,
        "expected_external_geometry_count": 1,
        "targets": [
            {"geometry_index": 3, "expected_state": False},
            {"geometry_index": -3, "expected_state": True},
        ],
    }
    assert list(validator.iter_errors(valid)) == []
    for invalid in (
        {**valid, "targets": []},
        {**valid, "targets": valid["targets"] * 33},
        {**valid, "targets": [{"geometry_index": -2, "expected_state": False}]},
        {**valid, "targets": [{"geometry_index": 3, "expected_state": 0}]},
        {**valid, "expected_external_geometry_count": -1},
        {**valid, "unexpected": True},
    ):
        assert list(validator.iter_errors(invalid))
    operations = tuple(
        variant.operation
        for variant in definition.variants
        if variant.operation
        not in {
            "carbon_copy",
            "translate",
            "rotate",
            "scale",
            "offset",
            "symmetry",
            "remove_axis_alignment",
            "convert_to_nurbs",
            "increase_bspline_degree",
            "decrease_bspline_degree",
            "increase_bspline_knot_multiplicity",
            "decrease_bspline_knot_multiplicity",
            "insert_bspline_knot",
            "join_curves",
            "restore_internal_alignment_geometry",
        }
    )
    encoded = json.dumps(
        [definition.provider_schema(operations)],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 14_998
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


@pytest.mark.parametrize(
    ("operation", "encoded_size"),
    (("create_fillet", 1_732), ("create_chamfer", 1_733)),
)
def test_fillet_chamfer_provider_schema_is_closed_bounded_and_exact(
    operation: str,
    encoded_size: int,
) -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema((operation,))
    validator = Draft202012Validator(schema["parameters"])
    common = {
        "operation": operation,
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 12,
        "expected_constraint_count": 4,
        "expected_external_geometry_count": 1,
        "preserve_corner": True,
    }
    corner = {
        **common,
        "target": {
            "form": "corner",
            "geometry_index": 3,
            "position": "end",
        },
    }
    curve_pair = {
        **common,
        "target": {
            "form": "curve_pair",
            "curves": [
                {
                    "geometry_index": 3,
                    "reference_point_mm": {"x": 4.0, "y": 1.0},
                },
                {
                    "geometry_index": 7,
                    "reference_point_mm": {"x": 9.0, "y": 6.0},
                },
            ],
        },
    }
    assert list(validator.iter_errors(corner)) == []
    assert list(validator.iter_errors(curve_pair)) == []
    for invalid in (
        {**corner, "expected_external_geometry_count": -1},
        {**corner, "preserve_corner": 1},
        {**corner, "target": {**corner["target"], "position": "center"}},
        {
            **curve_pair,
            "target": {
                **curve_pair["target"],
                "curves": curve_pair["target"]["curves"][:1],
            },
        },
        {
            **curve_pair,
            "target": {
                **curve_pair["target"],
                "curves": [
                    {
                        **curve_pair["target"]["curves"][0],
                        "reference_point_mm": {"x": 1_000_001.0, "y": 0.0},
                    },
                    curve_pair["target"]["curves"][1],
                ],
            },
        },
        {**corner, "unexpected": True},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == encoded_size
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


@pytest.mark.parametrize(
    ("operation", "encoded_size"),
    (("trim", 1_190), ("split", 1_191)),
)
def test_curve_point_provider_schema_is_closed_bounded_and_exact(
    operation: str,
    encoded_size: int,
) -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema((operation,))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": operation,
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 12,
        "expected_constraint_count": 4,
        "expected_external_geometry_count": 1,
        "target": {
            "geometry_index": 3,
            "reference_point_mm": {"x": 4.0, "y": 1.0},
        },
    }
    assert list(validator.iter_errors(valid)) == []
    for invalid in (
        {**valid, "expected_external_geometry_count": -1},
        {**valid, "target": {**valid["target"], "geometry_index": -1}},
        {
            **valid,
            "target": {
                **valid["target"],
                "reference_point_mm": {"x": 1_000_001.0, "y": 0.0},
            },
        },
        {
            **valid,
            "target": {
                **valid["target"],
                "reference_point_mm": {"x": 4.0, "y": 1.0, "z": 0.0},
            },
        },
        {**valid, "unexpected": True},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == encoded_size
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_extend_provider_schema_is_closed_bounded_and_exact() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("extend",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "extend",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 12,
        "expected_constraint_count": 4,
        "expected_external_geometry_count": 1,
        "target": {
            "geometry_index": 3,
            "endpoint": "start",
            "target_point_mm": {"x": 4.0, "y": 1.0},
        },
    }
    assert list(validator.iter_errors(valid)) == []
    for invalid in (
        {**valid, "expected_external_geometry_count": -1},
        {**valid, "target": {**valid["target"], "geometry_index": -1}},
        {**valid, "target": {**valid["target"], "endpoint": "middle"}},
        {
            **valid,
            "target": {
                **valid["target"],
                "target_point_mm": {"x": 1_000_001.0, "y": 0.0},
            },
        },
        {
            **valid,
            "target": {
                **valid["target"],
                "target_point_mm": {"x": 4.0, "y": 1.0, "z": 0.0},
            },
        },
        {**valid, "unexpected": True},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 1_249
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


@pytest.mark.parametrize(
    ("operation", "encoded_size"),
    (
        ("project_external_geometry", 1_460),
        ("intersect_external_geometry", 1_462),
    ),
)
def test_external_geometry_provider_schema_is_closed_bounded_and_exact(
    operation: str,
    encoded_size: int,
) -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema((operation,))
    validator = Draft202012Validator(schema["parameters"])
    common = {
        "operation": operation,
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 12,
        "expected_constraint_count": 4,
        "expected_external_reference_count": 1,
        "expected_external_geometry_count": 2,
        "role": "defining",
    }
    object_source = {**common, "source": {"object_name": "DatumLine"}}
    element_source = {
        **common,
        "source": {"object_name": "BodyFeature", "subelement": "Edge12"},
        "role": "reference",
    }
    assert list(validator.iter_errors(object_source)) == []
    assert list(validator.iter_errors(element_source)) == []
    for invalid in (
        {**common, "source": {"object_name": "Bad Name"}},
        {
            **element_source,
            "source": {"object_name": "BodyFeature", "subelement": "Edge0"},
        },
        {
            **element_source,
            "source": {"object_name": "BodyFeature", "subelement": "Wire1"},
        },
        {**element_source, "role": "construction"},
        {**element_source, "expected_external_reference_count": -1},
        {**element_source, "expected_external_geometry_count": 1_000_001},
        {**element_source, "unexpected": True},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == encoded_size
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_carbon_copy_provider_schema_is_closed_bounded_and_exact() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("carbon_copy",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "carbon_copy",
        "sketch": {"object_name": "TargetSketch"},
        "expected_geometry_count": 12,
        "expected_constraint_count": 4,
        "expected_external_reference_count": 1,
        "expected_external_geometry_count": 2,
        "source_sketch": {"object_name": "SourceSketch"},
        "expected_source_geometry_count": 8,
        "expected_source_constraint_count": 6,
        "expected_source_external_reference_count": 1,
        "expected_source_external_geometry_count": 2,
        "geometry_mode": "construction",
        "reference_permission": "same_body_aligned",
    }
    assert list(validator.iter_errors(valid)) == []
    for permission in (
        "same_body_aligned",
        "cross_body_aligned",
        "unaligned",
    ):
        assert (
            list(validator.iter_errors({**valid, "reference_permission": permission}))
            == []
        )
    for missing in tuple(key for key in valid if key != "operation"):
        invalid = dict(valid)
        del invalid[missing]
        assert list(validator.iter_errors(invalid))
    for invalid in (
        {**valid, "unexpected": True},
        {**valid, "geometry_mode": "preserve"},
        {**valid, "reference_permission": "automatic"},
        {**valid, "source_sketch": {"object_name": "Bad Name"}},
        {**valid, "expected_source_geometry_count": -1},
        {**valid, "expected_source_external_geometry_count": 1_000_001},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 1_818
    all_operations = tuple(
        variant.operation
        for variant in definition.variants
        if variant.operation
        not in {
            "rotate",
            "scale",
            "offset",
            "symmetry",
            "remove_axis_alignment",
            "convert_to_nurbs",
            "increase_bspline_degree",
            "decrease_bspline_degree",
            "increase_bspline_knot_multiplicity",
            "decrease_bspline_knot_multiplicity",
            "insert_bspline_knot",
            "join_curves",
            "restore_internal_alignment_geometry",
        }
    )
    all_encoded = json.dumps(
        [definition.provider_schema(all_operations)],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(all_encoded) == 17_107
    assert len(all_encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_translate_provider_schema_is_closed_bounded_and_exact() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("translate",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "translate",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 12,
        "expected_constraint_count": 4,
        "expected_external_reference_count": 1,
        "expected_external_geometry_count": 2,
        "geometry_indices": [3, 4, -3],
        "first_translation_mm": {"x": 10.0, "y": -2.0},
        "copy_count": 3,
        "second_translation_mm": {"x": 1.0, "y": 8.0},
        "row_count": 2,
        "constraint_mode": "equalize_dimensions",
    }
    assert list(validator.iter_errors(valid)) == []
    for missing in tuple(key for key in valid if key != "operation"):
        invalid = dict(valid)
        del invalid[missing]
        assert list(validator.iter_errors(invalid))
    for invalid in (
        {**valid, "unexpected": True},
        {**valid, "geometry_indices": []},
        {**valid, "geometry_indices": [3, 3]},
        {**valid, "geometry_indices": list(range(257))},
        {**valid, "geometry_indices": [-1_000_001]},
        {**valid, "first_translation_mm": {"x": 10.0, "y": -2.0, "z": 0.0}},
        {**valid, "copy_count": -1},
        {**valid, "row_count": 0},
        {**valid, "constraint_mode": "automatic"},
        {**valid, "expected_external_reference_count": -1},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 1_793
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_rotate_provider_schema_is_closed_bounded_and_exact() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("rotate",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "rotate",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 12,
        "expected_constraint_count": 4,
        "expected_external_reference_count": 1,
        "expected_external_geometry_count": 2,
        "geometry_indices": [3, 4, -3],
        "center_mm": {"x": 10.0, "y": -2.0},
        "total_angle": {"value": 270.0, "unit": "deg"},
        "copy_count": 3,
        "constraint_mode": "equalize_dimensions",
    }
    assert list(validator.iter_errors(valid)) == []
    for missing in tuple(key for key in valid if key != "operation"):
        invalid = dict(valid)
        del invalid[missing]
        assert list(validator.iter_errors(invalid))
    for invalid in (
        {**valid, "unexpected": True},
        {**valid, "geometry_indices": []},
        {**valid, "geometry_indices": [3, 3]},
        {**valid, "geometry_indices": list(range(257))},
        {**valid, "geometry_indices": [-1_000_001]},
        {**valid, "center_mm": {"x": 10.0, "y": -2.0, "z": 0.0}},
        {**valid, "total_angle": {"value": -360.0, "unit": "deg"}},
        {**valid, "total_angle": {"value": 360.0, "unit": "deg"}},
        {**valid, "total_angle": {"value": 90.0, "unit": "rad"}},
        {**valid, "copy_count": -1},
        {**valid, "constraint_mode": "automatic"},
        {**valid, "expected_external_reference_count": -1},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 1_680
    all_operations = tuple(
        variant.operation
        for variant in definition.variants
        if variant.operation
        not in {
            "scale",
            "offset",
            "symmetry",
            "remove_axis_alignment",
            "convert_to_nurbs",
            "increase_bspline_degree",
            "decrease_bspline_degree",
            "increase_bspline_knot_multiplicity",
            "decrease_bspline_knot_multiplicity",
            "insert_bspline_knot",
            "join_curves",
            "restore_internal_alignment_geometry",
        }
    )
    all_encoded = json.dumps(
        [definition.provider_schema(all_operations)],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(all_encoded) == 17_539
    assert len(all_encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_scale_provider_schema_is_closed_bounded_and_exact() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("scale",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "scale",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 12,
        "expected_constraint_count": 4,
        "expected_external_reference_count": 1,
        "expected_external_geometry_count": 2,
        "geometry_indices": [3, 4, -3],
        "center_mm": {"x": 10.0, "y": -2.0},
        "scale_factor": 1.5,
        "keep_originals": True,
    }
    assert list(validator.iter_errors(valid)) == []
    for missing in tuple(key for key in valid if key != "operation"):
        invalid = dict(valid)
        del invalid[missing]
        assert list(validator.iter_errors(invalid))
    for invalid in (
        {**valid, "unexpected": True},
        {**valid, "geometry_indices": []},
        {**valid, "geometry_indices": [3, 3]},
        {**valid, "geometry_indices": list(range(257))},
        {**valid, "geometry_indices": [-1_000_001]},
        {**valid, "center_mm": {"x": 10.0, "y": -2.0, "z": 0.0}},
        {**valid, "scale_factor": 1.0e-7},
        {**valid, "scale_factor": 1_000_001.0},
        {**valid, "keep_originals": 1},
        {**valid, "expected_external_reference_count": -1},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 1_428
    all_operations = tuple(
        variant.operation
        for variant in definition.variants
        if variant.operation
        not in {
            "offset",
            "symmetry",
            "remove_axis_alignment",
            "convert_to_nurbs",
            "increase_bspline_degree",
            "decrease_bspline_degree",
            "increase_bspline_knot_multiplicity",
            "decrease_bspline_knot_multiplicity",
            "insert_bspline_knot",
            "join_curves",
            "restore_internal_alignment_geometry",
        }
    )
    all_encoded = json.dumps(
        [definition.provider_schema(all_operations)],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(all_encoded) == 17_852
    assert len(all_encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_offset_provider_schema_is_closed_bounded_and_exact() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("offset",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "offset",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 12,
        "expected_constraint_count": 4,
        "expected_external_reference_count": 1,
        "expected_external_geometry_count": 2,
        "geometry_indices": [3, 4, -3],
        "offset_distance": {"value": -2.5, "unit": "mm"},
        "join_type": "intersection",
        "source_mode": "constrain",
    }
    assert list(validator.iter_errors(valid)) == []
    for missing in tuple(key for key in valid if key != "operation"):
        invalid = dict(valid)
        del invalid[missing]
        assert list(validator.iter_errors(invalid))
    for invalid in (
        {**valid, "unexpected": True},
        {**valid, "geometry_indices": []},
        {**valid, "geometry_indices": [3, 3]},
        {**valid, "geometry_indices": list(range(257))},
        {**valid, "geometry_indices": [-1_000_001]},
        {**valid, "offset_distance": {"value": 0.0, "unit": "mm"}},
        {**valid, "offset_distance": {"value": 2.5, "unit": "in"}},
        {**valid, "join_type": "automatic"},
        {**valid, "source_mode": "replace"},
        {**valid, "expected_external_reference_count": -1},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 1_530
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES
    all_operations = tuple(
        variant.operation
        for variant in definition.variants
        if variant.operation
        not in {
            "symmetry",
            "remove_axis_alignment",
            "convert_to_nurbs",
            "increase_bspline_degree",
            "decrease_bspline_degree",
            "increase_bspline_knot_multiplicity",
            "decrease_bspline_knot_multiplicity",
            "insert_bspline_knot",
            "join_curves",
            "restore_internal_alignment_geometry",
        }
    )
    all_encoded = json.dumps(
        [definition.provider_schema(all_operations)],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(all_encoded) == 18_482
    assert len(all_encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_symmetry_provider_schema_is_closed_bounded_and_exact() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("symmetry",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "symmetry",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 12,
        "expected_constraint_count": 4,
        "expected_external_reference_count": 1,
        "expected_external_geometry_count": 2,
        "geometry_indices": [3, 4, -3],
        "reference": {"geometry_index": -2, "position": "whole"},
        "source_mode": "constrain",
    }
    assert list(validator.iter_errors(valid)) == []
    for missing in tuple(key for key in valid if key != "operation"):
        invalid = dict(valid)
        del invalid[missing]
        assert list(validator.iter_errors(invalid))
    for invalid in (
        {**valid, "unexpected": True},
        {**valid, "geometry_indices": []},
        {**valid, "geometry_indices": [3, 3]},
        {**valid, "geometry_indices": list(range(257))},
        {**valid, "geometry_indices": [-1_000_001]},
        {**valid, "reference": {"geometry_index": -2}},
        {**valid, "reference": {"geometry_index": -2, "position": "edge"}},
        {**valid, "reference": {"geometry_index": -2000, "position": "whole"}},
        {**valid, "source_mode": "replace"},
        {**valid, "expected_external_reference_count": -1},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 1_435
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES
    all_operations = tuple(
        variant.operation
        for variant in definition.variants
        if variant.operation
        not in {
            "remove_axis_alignment",
            "convert_to_nurbs",
            "increase_bspline_degree",
            "decrease_bspline_degree",
            "increase_bspline_knot_multiplicity",
            "decrease_bspline_knot_multiplicity",
            "insert_bspline_knot",
            "join_curves",
            "restore_internal_alignment_geometry",
        }
    )
    all_encoded = json.dumps(
        [definition.provider_schema(all_operations)],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(all_encoded) == 18_954
    assert len(all_encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_remove_axis_alignment_provider_schema_is_closed_bounded_and_exact() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("remove_axis_alignment",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "remove_axis_alignment",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 12,
        "expected_constraint_count": 8,
        "expected_external_reference_count": 1,
        "expected_external_geometry_count": 2,
        "geometry_indices": [3, 4, 11],
    }
    assert list(validator.iter_errors(valid)) == []
    for missing in tuple(key for key in valid if key != "operation"):
        invalid = dict(valid)
        del invalid[missing]
        assert list(validator.iter_errors(invalid))
    for invalid in (
        {**valid, "unexpected": True},
        {**valid, "geometry_indices": []},
        {**valid, "geometry_indices": [3, 3]},
        {**valid, "geometry_indices": list(range(257))},
        {**valid, "geometry_indices": [-1]},
        {**valid, "expected_external_geometry_count": -1},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) == 1_068
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES
    all_operations = tuple(
        variant.operation
        for variant in definition.variants
        if variant.operation
        not in {
            "convert_to_nurbs",
            "increase_bspline_degree",
            "decrease_bspline_degree",
            "increase_bspline_knot_multiplicity",
            "decrease_bspline_knot_multiplicity",
            "insert_bspline_knot",
            "join_curves",
            "restore_internal_alignment_geometry",
        }
    )
    all_encoded = json.dumps(
        [definition.provider_schema(all_operations)],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(all_encoded) == 19_165
    assert len(all_encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_partial_geometry_family_remains_completely_unadvertised() -> None:
    registry = build_native_capability_registry()
    surface = RibbonSurface(
        "sketch.edit",
        1,
        (
            RibbonGroup(
                "Geometry",
                (
                    RibbonAction(
                        "Sketcher_CreatePoint",
                        "Point",
                        True,
                        "command",
                    ),
                    RibbonAction(
                        "Sketcher_CreateLine",
                        "Line",
                        True,
                        "command",
                    ),
                    RibbonAction(
                        "Sketcher_CreateArc",
                        "Arc",
                        True,
                        "command",
                    ),
                    RibbonAction(
                        "Sketcher_Dimension",
                        "Dimension",
                        True,
                        "command",
                    ),
                ),
            ),
        ),
    )

    resolved = resolve_native_provider_surface(surface, registry)

    assert registry.definition(SKETCH_GEOMETRY_CAPABILITY_NAME) is not None
    assert registry.implementation(SKETCH_GEOMETRY_CAPABILITY_NAME) is not None
    assert resolved.available is False
    assert resolved.tool_names == ()
    assert resolved.schemas == ()
    assert SKETCH_GEOMETRY_CAPABILITY_NAME in resolved.incomplete_definition_names
