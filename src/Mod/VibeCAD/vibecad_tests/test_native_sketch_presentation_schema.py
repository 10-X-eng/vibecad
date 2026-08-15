# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json

from jsonschema import Draft202012Validator

from VibeCADNativeCapabilityRegistry import (
    MAX_NATIVE_SCHEMAS_JSON_BYTES,
    resolve_native_provider_surface,
)
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeSketchPresentationSchema import (
    sketch_presentation_capability_definition,
)
from VibeCADRibbonSurface import RibbonAction, RibbonGroup, RibbonSurface


def test_arc_overlay_schema_is_closed_bounded_and_exact() -> None:
    definition = sketch_presentation_capability_definition()
    schema = definition.provider_schema(("arc_overlay",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "arc_overlay",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 2,
        "expected_constraint_count": 1,
        "expected_external_geometry_count": 0,
        "expected_visible": False,
        "visible": True,
    }
    assert list(validator.iter_errors(valid)) == []
    for missing in tuple(key for key in valid if key != "operation"):
        invalid = dict(valid)
        del invalid[missing]
        assert list(validator.iter_errors(invalid))
    for invalid in (
        {**valid, "unexpected": True},
        {**valid, "sketch": {"object_name": "Sketch", "label": "Sketch"}},
        {**valid, "expected_geometry_count": -1},
        {**valid, "expected_constraint_count": 1_000_001},
        {**valid, "expected_external_geometry_count": True},
        {**valid, "expected_visible": 0},
        {**valid, "visible": "true"},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_bspline_degree_schema_is_closed_bounded_and_exact() -> None:
    definition = sketch_presentation_capability_definition()
    schema = definition.provider_schema(("bspline_degree",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "bspline_degree",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 4,
        "expected_constraint_count": 2,
        "expected_external_geometry_count": 1,
        "expected_visible": True,
        "visible": False,
    }
    assert list(validator.iter_errors(valid)) == []
    for missing in tuple(key for key in valid if key != "operation"):
        invalid = dict(valid)
        del invalid[missing]
        assert list(validator.iter_errors(invalid))
    for invalid in (
        {**valid, "unexpected": True},
        {**valid, "sketch": {"object_name": "Sketch", "label": "Sketch"}},
        {**valid, "expected_geometry_count": 1_000_001},
        {**valid, "expected_constraint_count": -1},
        {**valid, "expected_external_geometry_count": False},
        {**valid, "expected_visible": 1},
        {**valid, "visible": "false"},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_bspline_control_polygon_schema_is_closed_bounded_and_exact() -> None:
    definition = sketch_presentation_capability_definition()
    schema = definition.provider_schema(("bspline_control_polygon",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "bspline_control_polygon",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 1,
        "expected_constraint_count": 0,
        "expected_external_geometry_count": 0,
        "expected_visible": True,
        "visible": False,
    }
    assert list(validator.iter_errors(valid)) == []
    for missing in tuple(key for key in valid if key != "operation"):
        invalid = dict(valid)
        del invalid[missing]
        assert list(validator.iter_errors(invalid))
    for invalid in (
        {**valid, "unexpected": True},
        {**valid, "sketch": {"object_name": "Sketch", "label": "Sketch"}},
        {**valid, "expected_geometry_count": -1},
        {**valid, "expected_constraint_count": 1_000_001},
        {**valid, "expected_external_geometry_count": True},
        {**valid, "expected_visible": 1},
        {**valid, "visible": "false"},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_bspline_curvature_comb_schema_is_closed_bounded_and_exact() -> None:
    definition = sketch_presentation_capability_definition()
    schema = definition.provider_schema(("bspline_curvature_comb",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "bspline_curvature_comb",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 1,
        "expected_constraint_count": 0,
        "expected_external_geometry_count": 0,
        "expected_visible": True,
        "visible": False,
    }
    assert list(validator.iter_errors(valid)) == []
    for missing in tuple(key for key in valid if key != "operation"):
        invalid = dict(valid)
        del invalid[missing]
        assert list(validator.iter_errors(invalid))
    for invalid in (
        {**valid, "unexpected": True},
        {**valid, "sketch": {"object_name": "Sketch", "label": "Sketch"}},
        {**valid, "expected_geometry_count": -1},
        {**valid, "expected_constraint_count": 1_000_001},
        {**valid, "expected_external_geometry_count": True},
        {**valid, "expected_visible": 1},
        {**valid, "visible": "false"},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_bspline_knot_multiplicity_schema_is_closed_bounded_and_exact() -> None:
    definition = sketch_presentation_capability_definition()
    schema = definition.provider_schema(("bspline_knot_multiplicity",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "bspline_knot_multiplicity",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 1,
        "expected_constraint_count": 0,
        "expected_external_geometry_count": 0,
        "expected_visible": True,
        "visible": False,
    }
    assert list(validator.iter_errors(valid)) == []
    for missing in tuple(key for key in valid if key != "operation"):
        invalid = dict(valid)
        del invalid[missing]
        assert list(validator.iter_errors(invalid))
    for invalid in (
        {**valid, "unexpected": True},
        {**valid, "sketch": {"object_name": "Sketch", "label": "Sketch"}},
        {**valid, "expected_geometry_count": -1},
        {**valid, "expected_constraint_count": 1_000_001},
        {**valid, "expected_external_geometry_count": True},
        {**valid, "expected_visible": 1},
        {**valid, "visible": "false"},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_bspline_pole_weight_schema_is_closed_bounded_and_exact() -> None:
    definition = sketch_presentation_capability_definition()
    schema = definition.provider_schema(("bspline_pole_weight",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "bspline_pole_weight",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 1,
        "expected_constraint_count": 0,
        "expected_external_geometry_count": 0,
        "expected_visible": True,
        "visible": False,
    }
    assert list(validator.iter_errors(valid)) == []
    for missing in tuple(key for key in valid if key != "operation"):
        invalid = dict(valid)
        del invalid[missing]
        assert list(validator.iter_errors(invalid))
    for invalid in (
        {**valid, "unexpected": True},
        {**valid, "sketch": {"object_name": "Sketch", "label": "Sketch"}},
        {**valid, "expected_geometry_count": -1},
        {**valid, "expected_constraint_count": 1_000_001},
        {**valid, "expected_external_geometry_count": True},
        {**valid, "expected_visible": 1},
        {**valid, "visible": "false"},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_presentation_variants_match_only_their_live_human_actions() -> None:
    definition = sketch_presentation_capability_definition()
    assert definition.name == "sketch.presentation"
    assert definition.primary_classification == "view"
    assert tuple(variant.operation for variant in definition.variants) == (
        "align_view_to_sketch",
        "section_view",
        "arc_overlay",
        "bspline_degree",
        "bspline_control_polygon",
        "bspline_curvature_comb",
        "bspline_knot_multiplicity",
        "bspline_pole_weight",
    )
    assert tuple(variant.action_ids for variant in definition.variants) == (
        frozenset({"Sketcher_ViewSketch"}),
        frozenset({"Sketcher_ViewSection"}),
        frozenset({"Sketcher_ArcOverlay"}),
        frozenset({"Sketcher_BSplineDegree"}),
        frozenset({"Sketcher_BSplinePolygon"}),
        frozenset({"Sketcher_BSplineComb"}),
        frozenset({"Sketcher_BSplineKnotMultiplicity"}),
        frozenset({"Sketcher_BSplinePoleWeight"}),
    )
    assert tuple(variant.exact_target_type for variant in definition.variants) == (
        "ActiveSketchExactViewAlignment",
        "ActiveSketchExactSectionViewState",
        *("ActiveSketchExactPresentationState",) * 6,
    )
    for variant in definition.variants:
        assert variant.surface_ids == frozenset({"sketch.edit"})
        assert variant.transaction_behavior == "presentation"
        assert variant.background_required is False


def test_surface_missing_view_actions_remains_completely_unadvertised() -> None:
    registry = build_native_capability_registry()
    surface = RibbonSurface(
        "sketch.edit",
        1,
        (
            RibbonGroup(
                "Visual",
                (
                    RibbonAction(
                        "Sketcher_ArcOverlay",
                        "Toggle Circular Helper for Arcs",
                        True,
                        "command",
                    ),
                    RibbonAction(
                        "Sketcher_BSplineDegree",
                        "Toggle B-spline Degree",
                        True,
                        "command",
                    ),
                    RibbonAction(
                        "Sketcher_BSplinePolygon",
                        "Toggle B-spline Control Polygon",
                        True,
                        "command",
                    ),
                    RibbonAction(
                        "Sketcher_BSplineComb",
                        "Toggle B-spline Curvature Comb",
                        True,
                        "command",
                    ),
                    RibbonAction(
                        "Sketcher_BSplineKnotMultiplicity",
                        "Toggle B-spline Knot Multiplicity",
                        True,
                        "command",
                    ),
                    RibbonAction(
                        "Sketcher_BSplinePoleWeight",
                        "Toggle B-spline Pole Weight",
                        True,
                        "command",
                    ),
                    RibbonAction(
                        "Sketcher_RestoreInternalAlignmentGeometry",
                        "Restore Internal Alignment Geometry",
                        True,
                        "command",
                    ),
                    RibbonAction(
                        "Sketcher_SwitchVirtualSpace",
                        "Toggle Virtual Space",
                        True,
                        "command",
                    ),
                ),
            ),
        ),
    )

    resolved = resolve_native_provider_surface(surface, registry)

    assert resolved.available is False
    assert resolved.tool_names == ()
    assert resolved.schemas == ()
    assert resolved.missing_definition_names == ()
    assert resolved.missing_implementation_names == ()
    assert resolved.incomplete_definition_names == ()
    assert "Sketcher_ViewSketch" in resolved.missing_action_ids
    assert "Sketcher_ViewSection" in resolved.missing_action_ids
