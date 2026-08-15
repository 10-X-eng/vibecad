# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADNativeSketchArcOverlay as overlay_module
import VibeCADNativeSketchPresentationPreference as preference_module
import VibeCADNativeSketchPresentationState as state_module
from VibeCADNativeSketchArcOverlay import (
    prepare_sketch_arc_overlay,
    set_sketch_arc_overlay,
)
from VibeCADNativeSketchBSplineControlPolygonVisibility import (
    prepare_sketch_bspline_control_polygon_visibility,
    set_sketch_bspline_control_polygon_visibility,
)
from VibeCADNativeSketchBSplineCurvatureCombVisibility import (
    prepare_sketch_bspline_curvature_comb_visibility,
    set_sketch_bspline_curvature_comb_visibility,
)
from VibeCADNativeSketchBSplineKnotMultiplicityVisibility import (
    prepare_sketch_bspline_knot_multiplicity_visibility,
    set_sketch_bspline_knot_multiplicity_visibility,
)
from VibeCADNativeSketchBSplinePoleWeightVisibility import (
    prepare_sketch_bspline_pole_weight_visibility,
    set_sketch_bspline_pole_weight_visibility,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchBSplineDegreeVisibility import (
    prepare_sketch_bspline_degree_visibility,
    set_sketch_bspline_degree_visibility,
)
from VibeCADNativeSketchPresentationRuntime import NativeSketchPresentationRuntime
from vibecad_tests.native_sketch_test_support import (
    FakeArc,
    FakeBSpline,
    FakeCircle,
    FakeLine,
    fake_facade,
    install_fake_sketch_host,
)


class FakeParameterGroup:
    def __init__(
        self,
        visible: bool | None = False,
        key: str = state_module.ARC_OVERLAY_PREFERENCE,
    ) -> None:
        self.visible = visible
        self.key = key
        self.writes: list[bool] = []
        self.ignore_writes = False
        self.raise_after_write = False

    def GetBool(self, key: str, default: bool) -> bool:
        assert key == self.key
        return self.visible if self.visible is not None else default

    def SetBool(self, key: str, value: bool) -> None:
        assert key == self.key
        self.writes.append(value)
        if not self.ignore_writes:
            self.visible = value
        if self.raise_after_write:
            self.raise_after_write = False
            raise RuntimeError("simulated host write failure")


def _install(monkeypatch, *, visible: bool = False):
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    circle = FakeCircle(
        SimpleNamespace(x=2.0, y=3.0, z=0.0),
        SimpleNamespace(x=0.0, y=0.0, z=1.0),
        4.0,
    )
    sketch.Geometry = [FakeArc(circle, 0.0, 1.5), FakeLine()]
    sketch.GeometryFacadeList = [
        fake_facade(geometry, index) for index, geometry in enumerate(sketch.Geometry)
    ]
    sketch.GeometryCount = 2
    group = FakeParameterGroup(visible)
    app = SimpleNamespace(
        ParamGet=lambda path: (
            group if path == state_module.SKETCH_GENERAL_PREFERENCES else None
        )
    )
    monkeypatch.setattr(state_module, "_application", lambda: app)
    values = {
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": 2,
        "expected_constraint_count": 0,
        "expected_external_geometry_count": 0,
        "expected_visible": visible,
        "visible": not visible,
    }
    return document, sketch, context, group, values


def _install_bspline_preference(
    monkeypatch,
    *,
    key: str,
    visible: bool | None = True,
):
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    points = [
        SimpleNamespace(x=0.0, y=0.0, z=0.0),
        SimpleNamespace(x=3.0, y=4.0, z=0.0),
        SimpleNamespace(x=7.0, y=-2.0, z=0.0),
        SimpleNamespace(x=11.0, y=0.0, z=0.0),
    ]
    spline = FakeBSpline(
        points,
        [4, 4],
        [0.0, 1.0],
        False,
        3,
        [1.0, 1.0, 1.0, 1.0],
    )
    sketch.Geometry = [spline, FakeLine()]
    sketch.GeometryFacadeList = [
        fake_facade(geometry, index) for index, geometry in enumerate(sketch.Geometry)
    ]
    sketch.GeometryCount = 2
    group = FakeParameterGroup(visible, key)
    app = SimpleNamespace(
        ParamGet=lambda path: (
            group if path == state_module.SKETCH_GENERAL_PREFERENCES else None
        )
    )
    monkeypatch.setattr(state_module, "_application", lambda: app)
    values = {
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": 2,
        "expected_constraint_count": 0,
        "expected_external_geometry_count": 0,
        "expected_visible": True if visible is None else visible,
        "visible": False,
    }
    return document, sketch, context, group, values


def _install_degree(monkeypatch, *, visible: bool | None = True):
    return _install_bspline_preference(
        monkeypatch,
        key=state_module.BSPLINE_DEGREE_PREFERENCE,
        visible=visible,
    )


def _install_control_polygon(monkeypatch, *, visible: bool | None = True):
    return _install_bspline_preference(
        monkeypatch,
        key=state_module.BSPLINE_CONTROL_POLYGON_PREFERENCE,
        visible=visible,
    )


def _install_curvature_comb(monkeypatch, *, visible: bool | None = True):
    return _install_bspline_preference(
        monkeypatch,
        key=state_module.BSPLINE_CURVATURE_COMB_PREFERENCE,
        visible=visible,
    )


def _install_knot_multiplicity(monkeypatch, *, visible: bool | None = True):
    return _install_bspline_preference(
        monkeypatch,
        key=state_module.BSPLINE_KNOT_MULTIPLICITY_PREFERENCE,
        visible=visible,
    )


def _install_pole_weight(monkeypatch, *, visible: bool | None = True):
    return _install_bspline_preference(
        monkeypatch,
        key=state_module.BSPLINE_POLE_WEIGHT_PREFERENCE,
        visible=visible,
    )


def test_arc_overlay_sets_explicit_state_without_changing_sketch(monkeypatch) -> None:
    document, sketch, context, group, values = _install(monkeypatch)
    spec = prepare_sketch_arc_overlay(document.Uid, values)
    before_geometry = tuple(sketch.Geometry)
    result = set_sketch_arc_overlay(context, spec)

    assert group.visible is True
    assert group.writes == [True]
    assert tuple(sketch.Geometry) == before_geometry
    assert result == {
        "operation": "arc_overlay",
        "sketch": {
            "document_uid": document.Uid,
            "object_name": "Sketch",
            "type_id": "Sketcher::SketchObject",
        },
        "previous_visible": False,
        "visible": True,
        "changed": True,
        "internal_arc_count": 1,
        "external_arc_count": 0,
        "geometry_count": 2,
        "constraint_count": 0,
        "external_geometry_count": 0,
        "geometry_state_sha256": result["geometry_state_sha256"],
        "constraint_state_sha256": result["constraint_state_sha256"],
        "external_geometry_state_sha256": result["external_geometry_state_sha256"],
    }
    assert len(result["geometry_state_sha256"]) == 64
    assert len(result["constraint_state_sha256"]) == 64
    assert len(result["external_geometry_state_sha256"]) == 64


def test_arc_overlay_no_op_does_not_write_preference(monkeypatch) -> None:
    document, _sketch, context, group, values = _install(monkeypatch, visible=True)
    values["visible"] = True
    result = set_sketch_arc_overlay(
        context,
        prepare_sketch_arc_overlay(document.Uid, values),
    )
    assert result["changed"] is False
    assert result["previous_visible"] is True
    assert result["visible"] is True
    assert group.writes == []


@pytest.mark.parametrize(
    "change",
    (
        lambda values: values.update({"unexpected": True}),
        lambda values: values.update({"expected_geometry_count": 3}),
        lambda values: values.update({"expected_constraint_count": 1}),
        lambda values: values.update({"expected_external_geometry_count": 1}),
        lambda values: values.update({"expected_visible": True}),
        lambda values: values.update({"visible": 1}),
    ),
)
def test_arc_overlay_rejects_closed_stale_and_invalid_requests(
    monkeypatch,
    change,
) -> None:
    document, _sketch, context, group, values = _install(monkeypatch)
    change(values)
    with pytest.raises(NativeSketchError):
        set_sketch_arc_overlay(
            context,
            prepare_sketch_arc_overlay(document.Uid, values),
        )
    assert group.writes == []


def test_arc_overlay_rejects_a_write_that_does_not_reach_requested_state(
    monkeypatch,
) -> None:
    document, _sketch, context, group, values = _install(monkeypatch)
    group.ignore_writes = True
    with pytest.raises(NativeSketchError, match="did not reach"):
        set_sketch_arc_overlay(
            context,
            prepare_sketch_arc_overlay(document.Uid, values),
        )
    assert group.visible is False


def test_arc_overlay_rolls_back_when_host_raises_after_changing_preference(
    monkeypatch,
) -> None:
    document, _sketch, context, group, values = _install(monkeypatch)
    group.raise_after_write = True
    with pytest.raises(NativeSketchError, match="could not be changed"):
        set_sketch_arc_overlay(
            context,
            prepare_sketch_arc_overlay(document.Uid, values),
        )
    assert group.visible is False
    assert group.writes == [True, False]


def test_arc_overlay_rolls_back_owned_value_when_sketch_verification_fails(
    monkeypatch,
) -> None:
    document, _sketch, context, group, values = _install(monkeypatch)

    def fail(_prepared):
        raise NativeSketchError("The active Sketch changed during the operation.")

    monkeypatch.setattr(overlay_module, "_verify_unchanged", fail)
    with pytest.raises(NativeSketchError, match="changed"):
        set_sketch_arc_overlay(
            context,
            prepare_sketch_arc_overlay(document.Uid, values),
        )
    assert group.visible is False
    assert group.writes == [True, False]


def test_arc_overlay_does_not_overwrite_a_concurrent_preference_change(
    monkeypatch,
) -> None:
    document, _sketch, context, group, values = _install(monkeypatch)

    def fail(_prepared):
        group.visible = False
        raise NativeSketchError("The active Sketch changed during the operation.")

    monkeypatch.setattr(overlay_module, "_verify_unchanged", fail)
    with pytest.raises(NativeSketchError, match="changed"):
        set_sketch_arc_overlay(
            context,
            prepare_sketch_arc_overlay(document.Uid, values),
        )
    assert group.writes == [True]


def test_sketch_presentation_runtime_is_strict(monkeypatch) -> None:
    _document, _sketch, context, _group, values = _install(monkeypatch)
    runtime = NativeSketchPresentationRuntime(context)
    result = runtime.present({"operation": "arc_overlay", **values})
    assert result["visible"] is True
    with pytest.raises(Exception, match="arguments"):
        runtime.present({"operation": "arc_overlay", **values, "extra": True})
    with pytest.raises(Exception, match="unavailable"):
        runtime.present({"operation": "missing", **values})


def test_bspline_degree_sets_explicit_state_without_changing_sketch(
    monkeypatch,
) -> None:
    document, sketch, context, group, values = _install_degree(monkeypatch)
    before_geometry = tuple(sketch.Geometry)
    result = set_sketch_bspline_degree_visibility(
        context,
        prepare_sketch_bspline_degree_visibility(document.Uid, values),
    )

    assert group.visible is False
    assert group.writes == [False]
    assert tuple(sketch.Geometry) == before_geometry
    assert result == {
        "operation": "bspline_degree",
        "sketch": {
            "document_uid": document.Uid,
            "object_name": "Sketch",
            "type_id": "Sketcher::SketchObject",
        },
        "previous_visible": True,
        "visible": False,
        "changed": True,
        "internal_b_spline_count": 1,
        "external_b_spline_count": 0,
        "geometry_count": 2,
        "constraint_count": 0,
        "external_geometry_count": 0,
        "geometry_state_sha256": result["geometry_state_sha256"],
        "constraint_state_sha256": result["constraint_state_sha256"],
        "external_geometry_state_sha256": result["external_geometry_state_sha256"],
    }
    assert all(
        len(result[key]) == 64
        for key in (
            "geometry_state_sha256",
            "constraint_state_sha256",
            "external_geometry_state_sha256",
        )
    )


def test_bspline_degree_uses_renderer_default_when_key_is_absent(monkeypatch) -> None:
    document, _sketch, context, group, values = _install_degree(
        monkeypatch,
        visible=None,
    )
    result = set_sketch_bspline_degree_visibility(
        context,
        prepare_sketch_bspline_degree_visibility(document.Uid, values),
    )
    assert result["previous_visible"] is True
    assert result["visible"] is False
    assert group.writes == [False]


def test_bspline_degree_no_op_does_not_write_preference(monkeypatch) -> None:
    document, _sketch, context, group, values = _install_degree(monkeypatch)
    values["visible"] = True
    result = set_sketch_bspline_degree_visibility(
        context,
        prepare_sketch_bspline_degree_visibility(document.Uid, values),
    )
    assert result["changed"] is False
    assert result["visible"] is True
    assert group.writes == []


def test_bspline_degree_rejects_a_write_that_does_not_reach_requested_state(
    monkeypatch,
) -> None:
    document, _sketch, context, group, values = _install_degree(monkeypatch)
    group.ignore_writes = True
    with pytest.raises(NativeSketchError, match="did not reach"):
        set_sketch_bspline_degree_visibility(
            context,
            prepare_sketch_bspline_degree_visibility(document.Uid, values),
        )
    assert group.visible is True
    assert group.writes == [False]


def test_bspline_degree_rolls_back_when_host_raises_after_changing_preference(
    monkeypatch,
) -> None:
    document, _sketch, context, group, values = _install_degree(monkeypatch)
    group.raise_after_write = True
    with pytest.raises(NativeSketchError, match="could not be changed"):
        set_sketch_bspline_degree_visibility(
            context,
            prepare_sketch_bspline_degree_visibility(document.Uid, values),
        )
    assert group.visible is True
    assert group.writes == [False, True]


@pytest.mark.parametrize(
    "change",
    (
        lambda values: values.update({"unexpected": True}),
        lambda values: values.update({"expected_geometry_count": 3}),
        lambda values: values.update({"expected_constraint_count": 1}),
        lambda values: values.update({"expected_external_geometry_count": 1}),
        lambda values: values.update({"expected_visible": False}),
        lambda values: values.update({"visible": 0}),
    ),
)
def test_bspline_degree_rejects_closed_stale_and_invalid_requests(
    monkeypatch,
    change,
) -> None:
    document, _sketch, context, group, values = _install_degree(monkeypatch)
    change(values)
    with pytest.raises(NativeSketchError):
        set_sketch_bspline_degree_visibility(
            context,
            prepare_sketch_bspline_degree_visibility(document.Uid, values),
        )
    assert group.writes == []


def test_bspline_degree_rolls_back_when_verification_fails(monkeypatch) -> None:
    document, _sketch, context, group, values = _install_degree(monkeypatch)

    def fail(_prepared):
        raise NativeSketchError("The active Sketch changed during the operation.")

    monkeypatch.setattr(preference_module, "_verify_unchanged", fail)
    with pytest.raises(NativeSketchError, match="changed"):
        set_sketch_bspline_degree_visibility(
            context,
            prepare_sketch_bspline_degree_visibility(document.Uid, values),
        )
    assert group.visible is True
    assert group.writes == [False, True]


def test_bspline_degree_does_not_overwrite_concurrent_preference_change(
    monkeypatch,
) -> None:
    document, _sketch, context, group, values = _install_degree(monkeypatch)

    def fail(_prepared):
        group.visible = True
        raise NativeSketchError("The active Sketch changed during the operation.")

    monkeypatch.setattr(preference_module, "_verify_unchanged", fail)
    with pytest.raises(NativeSketchError, match="changed"):
        set_sketch_bspline_degree_visibility(
            context,
            prepare_sketch_bspline_degree_visibility(document.Uid, values),
        )
    assert group.writes == [False]


def test_presentation_runtime_dispatches_bspline_degree(monkeypatch) -> None:
    _document, _sketch, context, group, values = _install_degree(monkeypatch)
    result = NativeSketchPresentationRuntime(context).present(
        {"operation": "bspline_degree", **values}
    )
    assert result["operation"] == "bspline_degree"
    assert result["visible"] is False
    assert group.writes == [False]


def test_bspline_degree_setter_rejects_another_presentation_spec(monkeypatch) -> None:
    document, _sketch, context, group, values = _install_degree(monkeypatch)
    arc_spec = prepare_sketch_arc_overlay(document.Uid, values)
    with pytest.raises(TypeError, match="exact B-spline degree"):
        set_sketch_bspline_degree_visibility(context, arc_spec)
    assert group.writes == []


def test_bspline_control_polygon_sets_exact_state_without_changing_sketch(
    monkeypatch,
) -> None:
    document, sketch, context, group, values = _install_control_polygon(monkeypatch)
    before_geometry = tuple(sketch.Geometry)
    result = set_sketch_bspline_control_polygon_visibility(
        context,
        prepare_sketch_bspline_control_polygon_visibility(document.Uid, values),
    )

    assert group.visible is False
    assert group.writes == [False]
    assert tuple(sketch.Geometry) == before_geometry
    assert result["operation"] == "bspline_control_polygon"
    assert result["previous_visible"] is True
    assert result["visible"] is False
    assert result["changed"] is True
    assert result["internal_b_spline_count"] == 1
    assert result["external_b_spline_count"] == 0
    assert result["geometry_count"] == 2
    assert result["constraint_count"] == 0
    assert result["external_geometry_count"] == 0
    assert all(
        len(result[key]) == 64
        for key in (
            "geometry_state_sha256",
            "constraint_state_sha256",
            "external_geometry_state_sha256",
        )
    )


def test_bspline_control_polygon_uses_renderer_default_when_key_absent(
    monkeypatch,
) -> None:
    document, _sketch, context, group, values = _install_control_polygon(
        monkeypatch,
        visible=None,
    )
    result = set_sketch_bspline_control_polygon_visibility(
        context,
        prepare_sketch_bspline_control_polygon_visibility(document.Uid, values),
    )
    assert result["previous_visible"] is True
    assert group.writes == [False]


def test_bspline_control_polygon_no_op_does_not_write(monkeypatch) -> None:
    document, _sketch, context, group, values = _install_control_polygon(monkeypatch)
    values["visible"] = True
    result = set_sketch_bspline_control_polygon_visibility(
        context,
        prepare_sketch_bspline_control_polygon_visibility(document.Uid, values),
    )
    assert result["changed"] is False
    assert group.writes == []


@pytest.mark.parametrize(
    "change",
    (
        lambda values: values.update({"unexpected": True}),
        lambda values: values.update({"expected_geometry_count": 3}),
        lambda values: values.update({"expected_constraint_count": 1}),
        lambda values: values.update({"expected_external_geometry_count": 1}),
        lambda values: values.update({"expected_visible": False}),
        lambda values: values.update({"visible": 0}),
    ),
)
def test_bspline_control_polygon_rejects_closed_stale_and_invalid_requests(
    monkeypatch,
    change,
) -> None:
    document, _sketch, context, group, values = _install_control_polygon(monkeypatch)
    change(values)
    with pytest.raises(NativeSketchError):
        set_sketch_bspline_control_polygon_visibility(
            context,
            prepare_sketch_bspline_control_polygon_visibility(document.Uid, values),
        )
    assert group.writes == []


def test_bspline_control_polygon_rolls_back_failed_verification(monkeypatch) -> None:
    document, _sketch, context, group, values = _install_control_polygon(monkeypatch)

    def fail(_prepared):
        raise NativeSketchError("The active Sketch changed during the operation.")

    monkeypatch.setattr(preference_module, "_verify_unchanged", fail)
    with pytest.raises(NativeSketchError, match="changed"):
        set_sketch_bspline_control_polygon_visibility(
            context,
            prepare_sketch_bspline_control_polygon_visibility(document.Uid, values),
        )
    assert group.visible is True
    assert group.writes == [False, True]


def test_presentation_runtime_dispatches_bspline_control_polygon(monkeypatch) -> None:
    _document, _sketch, context, group, values = _install_control_polygon(monkeypatch)
    result = NativeSketchPresentationRuntime(context).present(
        {"operation": "bspline_control_polygon", **values}
    )
    assert result["operation"] == "bspline_control_polygon"
    assert group.writes == [False]


def test_bspline_control_polygon_setter_rejects_degree_spec(monkeypatch) -> None:
    document, _sketch, context, group, values = _install_control_polygon(monkeypatch)
    degree_spec = prepare_sketch_bspline_degree_visibility(document.Uid, values)
    with pytest.raises(TypeError, match="control-polygon"):
        set_sketch_bspline_control_polygon_visibility(context, degree_spec)
    assert group.writes == []


def test_bspline_curvature_comb_sets_exact_state_without_changing_sketch(
    monkeypatch,
) -> None:
    document, sketch, context, group, values = _install_curvature_comb(monkeypatch)
    before_geometry = tuple(sketch.Geometry)
    result = set_sketch_bspline_curvature_comb_visibility(
        context,
        prepare_sketch_bspline_curvature_comb_visibility(document.Uid, values),
    )

    assert group.visible is False
    assert group.writes == [False]
    assert tuple(sketch.Geometry) == before_geometry
    assert result["operation"] == "bspline_curvature_comb"
    assert result["previous_visible"] is True
    assert result["visible"] is False
    assert result["changed"] is True
    assert result["internal_b_spline_count"] == 1
    assert result["external_b_spline_count"] == 0
    assert result["geometry_count"] == 2
    assert result["constraint_count"] == 0
    assert result["external_geometry_count"] == 0
    assert all(
        len(result[key]) == 64
        for key in (
            "geometry_state_sha256",
            "constraint_state_sha256",
            "external_geometry_state_sha256",
        )
    )


def test_bspline_curvature_comb_uses_renderer_default_when_key_absent(
    monkeypatch,
) -> None:
    document, _sketch, context, group, values = _install_curvature_comb(
        monkeypatch,
        visible=None,
    )
    result = set_sketch_bspline_curvature_comb_visibility(
        context,
        prepare_sketch_bspline_curvature_comb_visibility(document.Uid, values),
    )
    assert result["previous_visible"] is True
    assert group.writes == [False]


def test_bspline_curvature_comb_no_op_does_not_write(monkeypatch) -> None:
    document, _sketch, context, group, values = _install_curvature_comb(monkeypatch)
    values["visible"] = True
    result = set_sketch_bspline_curvature_comb_visibility(
        context,
        prepare_sketch_bspline_curvature_comb_visibility(document.Uid, values),
    )
    assert result["changed"] is False
    assert group.writes == []


@pytest.mark.parametrize(
    "change",
    (
        lambda values: values.update({"unexpected": True}),
        lambda values: values.update({"expected_geometry_count": 3}),
        lambda values: values.update({"expected_constraint_count": 1}),
        lambda values: values.update({"expected_external_geometry_count": 1}),
        lambda values: values.update({"expected_visible": False}),
        lambda values: values.update({"visible": 0}),
    ),
)
def test_bspline_curvature_comb_rejects_closed_stale_and_invalid_requests(
    monkeypatch,
    change,
) -> None:
    document, _sketch, context, group, values = _install_curvature_comb(monkeypatch)
    change(values)
    with pytest.raises(NativeSketchError):
        set_sketch_bspline_curvature_comb_visibility(
            context,
            prepare_sketch_bspline_curvature_comb_visibility(document.Uid, values),
        )
    assert group.writes == []


def test_bspline_curvature_comb_rolls_back_failed_verification(monkeypatch) -> None:
    document, _sketch, context, group, values = _install_curvature_comb(monkeypatch)

    def fail(_prepared):
        raise NativeSketchError("The active Sketch changed during the operation.")

    monkeypatch.setattr(preference_module, "_verify_unchanged", fail)
    with pytest.raises(NativeSketchError, match="changed"):
        set_sketch_bspline_curvature_comb_visibility(
            context,
            prepare_sketch_bspline_curvature_comb_visibility(document.Uid, values),
        )
    assert group.visible is True
    assert group.writes == [False, True]


def test_presentation_runtime_dispatches_bspline_curvature_comb(monkeypatch) -> None:
    _document, _sketch, context, group, values = _install_curvature_comb(monkeypatch)
    result = NativeSketchPresentationRuntime(context).present(
        {"operation": "bspline_curvature_comb", **values}
    )
    assert result["operation"] == "bspline_curvature_comb"
    assert group.writes == [False]


def test_bspline_curvature_comb_setter_rejects_degree_spec(monkeypatch) -> None:
    document, _sketch, context, group, values = _install_curvature_comb(monkeypatch)
    degree_spec = prepare_sketch_bspline_degree_visibility(document.Uid, values)
    with pytest.raises(TypeError, match="curvature-comb"):
        set_sketch_bspline_curvature_comb_visibility(context, degree_spec)
    assert group.writes == []


def test_bspline_knot_multiplicity_sets_exact_state_without_changing_sketch(
    monkeypatch,
) -> None:
    document, sketch, context, group, values = _install_knot_multiplicity(monkeypatch)
    before_geometry = tuple(sketch.Geometry)
    result = set_sketch_bspline_knot_multiplicity_visibility(
        context,
        prepare_sketch_bspline_knot_multiplicity_visibility(document.Uid, values),
    )

    assert group.visible is False
    assert group.writes == [False]
    assert tuple(sketch.Geometry) == before_geometry
    assert result["operation"] == "bspline_knot_multiplicity"
    assert result["previous_visible"] is True
    assert result["visible"] is False
    assert result["changed"] is True
    assert result["internal_b_spline_count"] == 1
    assert result["external_b_spline_count"] == 0
    assert result["geometry_count"] == 2
    assert result["constraint_count"] == 0
    assert result["external_geometry_count"] == 0
    assert all(
        len(result[key]) == 64
        for key in (
            "geometry_state_sha256",
            "constraint_state_sha256",
            "external_geometry_state_sha256",
        )
    )


def test_bspline_knot_multiplicity_uses_renderer_default_when_key_absent(
    monkeypatch,
) -> None:
    document, _sketch, context, group, values = _install_knot_multiplicity(
        monkeypatch,
        visible=None,
    )
    result = set_sketch_bspline_knot_multiplicity_visibility(
        context,
        prepare_sketch_bspline_knot_multiplicity_visibility(document.Uid, values),
    )
    assert result["previous_visible"] is True
    assert group.writes == [False]


def test_bspline_knot_multiplicity_no_op_does_not_write(monkeypatch) -> None:
    document, _sketch, context, group, values = _install_knot_multiplicity(monkeypatch)
    values["visible"] = True
    result = set_sketch_bspline_knot_multiplicity_visibility(
        context,
        prepare_sketch_bspline_knot_multiplicity_visibility(document.Uid, values),
    )
    assert result["changed"] is False
    assert group.writes == []


@pytest.mark.parametrize(
    "change",
    (
        lambda values: values.update({"unexpected": True}),
        lambda values: values.update({"expected_geometry_count": 3}),
        lambda values: values.update({"expected_constraint_count": 1}),
        lambda values: values.update({"expected_external_geometry_count": 1}),
        lambda values: values.update({"expected_visible": False}),
        lambda values: values.update({"visible": 0}),
    ),
)
def test_bspline_knot_multiplicity_rejects_closed_stale_and_invalid_requests(
    monkeypatch,
    change,
) -> None:
    document, _sketch, context, group, values = _install_knot_multiplicity(monkeypatch)
    change(values)
    with pytest.raises(NativeSketchError):
        set_sketch_bspline_knot_multiplicity_visibility(
            context,
            prepare_sketch_bspline_knot_multiplicity_visibility(
                document.Uid,
                values,
            ),
        )
    assert group.writes == []


def test_bspline_knot_multiplicity_rolls_back_failed_verification(
    monkeypatch,
) -> None:
    document, _sketch, context, group, values = _install_knot_multiplicity(monkeypatch)

    def fail(_prepared):
        raise NativeSketchError("The active Sketch changed during the operation.")

    monkeypatch.setattr(preference_module, "_verify_unchanged", fail)
    with pytest.raises(NativeSketchError, match="changed"):
        set_sketch_bspline_knot_multiplicity_visibility(
            context,
            prepare_sketch_bspline_knot_multiplicity_visibility(
                document.Uid,
                values,
            ),
        )
    assert group.visible is True
    assert group.writes == [False, True]


def test_presentation_runtime_dispatches_bspline_knot_multiplicity(
    monkeypatch,
) -> None:
    _document, _sketch, context, group, values = _install_knot_multiplicity(monkeypatch)
    result = NativeSketchPresentationRuntime(context).present(
        {"operation": "bspline_knot_multiplicity", **values}
    )
    assert result["operation"] == "bspline_knot_multiplicity"
    assert group.writes == [False]


def test_bspline_knot_multiplicity_setter_rejects_degree_spec(monkeypatch) -> None:
    document, _sketch, context, group, values = _install_knot_multiplicity(monkeypatch)
    degree_spec = prepare_sketch_bspline_degree_visibility(document.Uid, values)
    with pytest.raises(TypeError, match="knot-multiplicity"):
        set_sketch_bspline_knot_multiplicity_visibility(context, degree_spec)
    assert group.writes == []


def test_bspline_pole_weight_sets_exact_state_without_changing_sketch(
    monkeypatch,
) -> None:
    document, sketch, context, group, values = _install_pole_weight(monkeypatch)
    before_geometry = tuple(sketch.Geometry)
    result = set_sketch_bspline_pole_weight_visibility(
        context,
        prepare_sketch_bspline_pole_weight_visibility(document.Uid, values),
    )

    assert group.visible is False
    assert group.writes == [False]
    assert tuple(sketch.Geometry) == before_geometry
    assert result["operation"] == "bspline_pole_weight"
    assert result["previous_visible"] is True
    assert result["visible"] is False
    assert result["changed"] is True
    assert result["internal_b_spline_count"] == 1
    assert result["external_b_spline_count"] == 0
    assert result["geometry_count"] == 2
    assert result["constraint_count"] == 0
    assert result["external_geometry_count"] == 0
    assert all(
        len(result[key]) == 64
        for key in (
            "geometry_state_sha256",
            "constraint_state_sha256",
            "external_geometry_state_sha256",
        )
    )


def test_bspline_pole_weight_uses_renderer_default_when_key_absent(
    monkeypatch,
) -> None:
    document, _sketch, context, group, values = _install_pole_weight(
        monkeypatch,
        visible=None,
    )
    result = set_sketch_bspline_pole_weight_visibility(
        context,
        prepare_sketch_bspline_pole_weight_visibility(document.Uid, values),
    )
    assert result["previous_visible"] is True
    assert group.writes == [False]


def test_bspline_pole_weight_no_op_does_not_write(monkeypatch) -> None:
    document, _sketch, context, group, values = _install_pole_weight(monkeypatch)
    values["visible"] = True
    result = set_sketch_bspline_pole_weight_visibility(
        context,
        prepare_sketch_bspline_pole_weight_visibility(document.Uid, values),
    )
    assert result["changed"] is False
    assert group.writes == []


@pytest.mark.parametrize(
    "change",
    (
        lambda values: values.update({"unexpected": True}),
        lambda values: values.update({"expected_geometry_count": 3}),
        lambda values: values.update({"expected_constraint_count": 1}),
        lambda values: values.update({"expected_external_geometry_count": 1}),
        lambda values: values.update({"expected_visible": False}),
        lambda values: values.update({"visible": 0}),
    ),
)
def test_bspline_pole_weight_rejects_closed_stale_and_invalid_requests(
    monkeypatch,
    change,
) -> None:
    document, _sketch, context, group, values = _install_pole_weight(monkeypatch)
    change(values)
    with pytest.raises(NativeSketchError):
        set_sketch_bspline_pole_weight_visibility(
            context,
            prepare_sketch_bspline_pole_weight_visibility(document.Uid, values),
        )
    assert group.writes == []


def test_bspline_pole_weight_rolls_back_failed_verification(monkeypatch) -> None:
    document, _sketch, context, group, values = _install_pole_weight(monkeypatch)

    def fail(_prepared):
        raise NativeSketchError("The active Sketch changed during the operation.")

    monkeypatch.setattr(preference_module, "_verify_unchanged", fail)
    with pytest.raises(NativeSketchError, match="changed"):
        set_sketch_bspline_pole_weight_visibility(
            context,
            prepare_sketch_bspline_pole_weight_visibility(document.Uid, values),
        )
    assert group.visible is True
    assert group.writes == [False, True]


def test_presentation_runtime_dispatches_bspline_pole_weight(monkeypatch) -> None:
    _document, _sketch, context, group, values = _install_pole_weight(monkeypatch)
    result = NativeSketchPresentationRuntime(context).present(
        {"operation": "bspline_pole_weight", **values}
    )
    assert result["operation"] == "bspline_pole_weight"
    assert group.writes == [False]


def test_bspline_pole_weight_setter_rejects_degree_spec(monkeypatch) -> None:
    document, _sketch, context, group, values = _install_pole_weight(monkeypatch)
    degree_spec = prepare_sketch_bspline_degree_visibility(document.Uid, values)
    with pytest.raises(TypeError, match="pole-weight"):
        set_sketch_bspline_pole_weight_visibility(context, degree_spec)
    assert group.writes == []
