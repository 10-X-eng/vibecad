# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

import VibeCADNativeSketchGeometryRuntime as runtime_module
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeSketchBSplineKnotInsertion import (
    create_bspline_knot_insertion,
    preflight_bspline_knot_insertion,
    prepare_bspline_knot_insertion,
    verify_bspline_knot_insertion,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryRuntime import NativeSketchGeometryRuntime
from vibecad_tests.native_sketch_bspline_knot_multiplicity_test_support import (
    install_insertion_host,
    insertion_values,
)
from vibecad_tests.native_sketch_test_support import FakeConstraint, FakeLine


def test_target_is_closed_and_parameter_is_strict(monkeypatch) -> None:
    document, _sketch, _context, values, *_rest = install_insertion_host(monkeypatch)
    spec = prepare_bspline_knot_insertion(document.Uid, values)
    assert (spec.geometry_index, spec.parameter) == (0, 0.25)
    for arguments in (
        {**values, "unexpected": True},
        {**values, "geometry_index": [0]},
        {**values, "geometry_index": True},
        {**values, "geometry_index": -1},
        {**values, "parameter": True},
        {**values, "parameter": float("nan")},
        {**values, "parameter": float("inf")},
    ):
        with pytest.raises(NativeSketchError):
            prepare_bspline_knot_insertion(document.Uid, arguments)


@pytest.mark.parametrize(("exposed", "parameter"), ((False, 0.25), (True, 0.5)))
def test_diagnosis_is_pure_and_exact_commit_is_verified(
    monkeypatch, exposed, parameter
) -> None:
    document, sketch, context, values, calls, commits, _state = install_insertion_host(
        monkeypatch, exposed=exposed
    )
    values["parameter"] = parameter
    before_counts = sketch.GeometryCount, sketch.ConstraintCount
    prepared = preflight_bspline_knot_insertion(
        context, prepare_bspline_knot_insertion(document.Uid, values)
    )
    assert (sketch.GeometryCount, sketch.ConstraintCount) == before_counts
    draft = create_bspline_knot_insertion(document, prepared)
    result = verify_bspline_knot_insertion(document, draft)
    assert isinstance(draft, NativeMutationDraft)
    assert len(calls) == 2 and commits == [(0, parameter)]
    assert sketch.GeometryFacadeList[0].Tag == "spline-root"
    assert sketch.GeometryFacadeList[0].Construction is True
    assert sketch.GeometryFacadeList[0].GeometryLayerId == 7
    assert sketch.ExpressionEngine == [("Constraints[0]", "Spreadsheet.Length")]
    assert result["operation"] == "insert_bspline_knot"
    assert result["requested_parameter"] == parameter
    assert result["new_multiplicity"] == result["old_multiplicity"] + 1
    assert result["measured_displacement_mm"] == 0.0


def test_rejects_non_spline_domain_and_stale_state(monkeypatch) -> None:
    document, sketch, context, values, calls, commits, _state = install_insertion_host(
        monkeypatch
    )
    for updates in (
        {"parameter": -0.01},
        {"parameter": 1.01},
        {"expected_geometry_count": values["expected_geometry_count"] + 1},
    ):
        with pytest.raises(NativeSketchError):
            preflight_bspline_knot_insertion(
                context,
                prepare_bspline_knot_insertion(document.Uid, {**values, **updates}),
            )
    sketch.Geometry[0] = FakeLine()
    sketch.GeometryFacadeList[0].Geometry = sketch.Geometry[0]
    with pytest.raises(NativeSketchError, match="B-spline"):
        preflight_bspline_knot_insertion(
            context, prepare_bspline_knot_insertion(document.Uid, values)
        )
    assert calls == [] and commits == []


def test_refuses_custom_helper_constraints(monkeypatch) -> None:
    document, sketch, context, _values, calls, commits, _state = install_insertion_host(
        monkeypatch, exposed=True
    )
    custom = FakeConstraint("DistanceX", 1, 3, 4.0)
    custom.Tag = "custom-helper-constraint"
    sketch.Constraints.append(custom)
    sketch.ConstraintCount += 1
    with pytest.raises(NativeSketchError, match="custom constraints"):
        preflight_bspline_knot_insertion(
            context,
            prepare_bspline_knot_insertion(document.Uid, insertion_values(sketch)),
        )
    assert calls == [] and commits == []


@pytest.mark.parametrize(
    ("change", "message"),
    (
        (lambda result, _count: result.update({"unexpected": True}), "incomplete"),
        (lambda result, _count: result.update({"knot_index": 2}), "knot"),
        (
            lambda result, _count: result.update({"requested_parameter": 0.75}),
            "different parameter",
        ),
        (
            lambda result, _count: result.update({"new_multiplicity": 2}),
            "representation",
        ),
        (lambda result, _count: result.update({"accepted": False}), "solver issue"),
    ),
)
def test_rejects_untrusted_host_diagnostics(monkeypatch, change, message) -> None:
    document, _sketch, context, values, _calls, commits, state = install_insertion_host(
        monkeypatch
    )
    state["mutate_diagnostic"] = change
    with pytest.raises(NativeSketchError, match=message):
        preflight_bspline_knot_insertion(
            context, prepare_bspline_knot_insertion(document.Uid, values)
        )
    assert commits == []


def test_refuses_impure_second_diagnostic_and_shape_drift(monkeypatch) -> None:
    document, sketch, context, values, _calls, commits, state = install_insertion_host(
        monkeypatch
    )

    def impure(_result, count):
        if count == 1:
            sketch.GeometryFacadeList[0].Blocked = True

    state["mutate_diagnostic"] = impure
    with pytest.raises(NativeSketchError, match="changed the live Sketch"):
        preflight_bspline_knot_insertion(
            context, prepare_bspline_knot_insertion(document.Uid, values)
        )
    assert commits == []

    document, _sketch, context, values, _calls, commits, state = install_insertion_host(
        monkeypatch
    )

    def drift(result, count):
        if count == 2:
            result["new_multiplicity"] = 2

    state["mutate_diagnostic"] = drift
    prepared = preflight_bspline_knot_insertion(
        context, prepare_bspline_knot_insertion(document.Uid, values)
    )
    with pytest.raises(NativeSketchError, match="representation"):
        create_bspline_knot_insertion(document, prepared)
    assert commits == []

    document, _sketch, context, values, _calls, commits, state = install_insertion_host(
        monkeypatch
    )

    def shape_drift(result, _count):
        result["geometry"][0]._shape_shift = 0.1
        result["geometry"][0]._refresh()

    state["mutate_diagnostic"] = shape_drift
    with pytest.raises(NativeSketchError, match="move the curve"):
        preflight_bspline_knot_insertion(
            context, prepare_bspline_knot_insertion(document.Uid, values)
        )
    assert commits == []


def test_final_representation_drift_is_rejected(monkeypatch) -> None:
    document, sketch, context, values, *_rest = install_insertion_host(monkeypatch)
    prepared = preflight_bspline_knot_insertion(
        context, prepare_bspline_knot_insertion(document.Uid, values)
    )
    draft = create_bspline_knot_insertion(document, prepared)
    sketch.Geometry[0]._shape_shift = 0.1
    sketch.Geometry[0]._refresh()
    with pytest.raises(NativeSketchError, match="final geometry|representation"):
        verify_bspline_knot_insertion(document, draft)


def test_runtime_routes_one_exact_transaction(monkeypatch) -> None:
    document, _sketch, context, values, calls, commits, _state = install_insertion_host(
        monkeypatch
    )
    captured = {}

    def run_immediate(runtime_context, **kwargs):
        assert runtime_context is context
        captured.update(kwargs)
        return kwargs["verify"](document, kwargs["mutate"](document))

    monkeypatch.setattr(runtime_module, "run_immediate_mutation", run_immediate)
    result = NativeSketchGeometryRuntime(context).mutate_geometry(
        {"operation": "insert_bspline_knot", **values}, ticket=None
    )
    assert len(calls) == 2 and commits == [(0, 0.25)]
    assert captured["transaction_name"] == "Insert Sketch B-Spline Knot"
    assert result["operation"] == "insert_bspline_knot"
