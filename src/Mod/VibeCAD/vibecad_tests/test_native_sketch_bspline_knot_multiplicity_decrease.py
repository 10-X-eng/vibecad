# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

import VibeCADNativeSketchGeometryRuntime as runtime_module
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeSketchBSplineKnotMultiplicityDecrease import (
    create_bspline_knot_multiplicity_decrease,
    preflight_bspline_knot_multiplicity_decrease,
    prepare_bspline_knot_multiplicity_decrease,
    verify_bspline_knot_multiplicity_decrease,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryRuntime import NativeSketchGeometryRuntime
from vibecad_tests.native_sketch_bspline_knot_multiplicity_test_support import (
    install_multiplicity_host,
    multiplicity_values,
)
from vibecad_tests.native_sketch_test_support import FakeConstraint, FakeLine


def _host(monkeypatch, **kwargs):
    return install_multiplicity_host(monkeypatch, increment=-1, **kwargs)


def test_target_is_closed_zero_based_and_deviation_bounded(monkeypatch) -> None:
    document, _sketch, _context, values, *_rest = _host(monkeypatch)
    spec = prepare_bspline_knot_multiplicity_decrease(document.Uid, values)
    assert (spec.geometry_index, spec.knot_index) == (0, 1)
    assert spec.maximum_deviation_mm == 1.0
    for arguments in (
        {**values, "unexpected": True},
        {**values, "geometry_index": [0]},
        {**values, "geometry_index": True},
        {**values, "geometry_index": -1},
        {**values, "knot_index": True},
        {**values, "knot_index": -1},
        {**values, "maximum_deviation_mm": True},
        {**values, "maximum_deviation_mm": -0.1},
        {**values, "maximum_deviation_mm": float("inf")},
    ):
        with pytest.raises(NativeSketchError):
            prepare_bspline_knot_multiplicity_decrease(document.Uid, arguments)


@pytest.mark.parametrize("exposed", (False, True))
def test_single_multiplicity_knot_is_removed_and_exact_commit_verified(
    monkeypatch, exposed
) -> None:
    document, sketch, context, values, calls, commits, _state = _host(
        monkeypatch, exposed=exposed
    )
    before_counts = (sketch.GeometryCount, sketch.ConstraintCount)
    prepared = preflight_bspline_knot_multiplicity_decrease(
        context, prepare_bspline_knot_multiplicity_decrease(document.Uid, values)
    )
    assert (sketch.GeometryCount, sketch.ConstraintCount) == before_counts
    draft = create_bspline_knot_multiplicity_decrease(document, prepared)
    result = verify_bspline_knot_multiplicity_decrease(document, draft)
    assert isinstance(draft, NativeMutationDraft)
    assert len(calls) == 2 and commits == [(0, 1)]
    assert sketch.Geometry[0].getKnots() == [0.0, 1.0]
    assert sketch.Geometry[0].getMultiplicities() == [4, 4]
    assert sketch.GeometryFacadeList[0].Tag == "spline-root"
    assert sketch.GeometryFacadeList[0].Construction is True
    assert sketch.GeometryFacadeList[0].GeometryLayerId == 7
    assert sketch.ExpressionEngine == [("Constraints[0]", "Spreadsheet.Length")]
    assert result["operation"] == "decrease_bspline_knot_multiplicity"
    assert (result["old_multiplicity"], result["new_multiplicity"]) == (1, 0)
    assert result["measured_deviation_mm"] == 0.0


def test_higher_multiplicity_retains_the_selected_knot(monkeypatch) -> None:
    document, sketch, context, values, calls, commits, _state = _host(
        monkeypatch,
        exposed=True,
        middle_multiplicity=2,
    )
    prepared = preflight_bspline_knot_multiplicity_decrease(
        context, prepare_bspline_knot_multiplicity_decrease(document.Uid, values)
    )
    draft = create_bspline_knot_multiplicity_decrease(document, prepared)
    result = verify_bspline_knot_multiplicity_decrease(document, draft)
    assert len(calls) == 2 and commits == [(0, 1)]
    assert sketch.Geometry[0].getKnots() == [0.0, 0.5, 1.0]
    assert sketch.Geometry[0].getMultiplicities() == [4, 1, 4]
    assert (result["old_multiplicity"], result["new_multiplicity"]) == (2, 1)


def test_refuses_non_spline_and_stale_knot(monkeypatch) -> None:
    document, sketch, context, values, calls, commits, _state = _host(monkeypatch)
    sketch.Geometry[0] = FakeLine()
    sketch.GeometryFacadeList[0].Geometry = sketch.Geometry[0]
    with pytest.raises(NativeSketchError):
        preflight_bspline_knot_multiplicity_decrease(
            context,
            prepare_bspline_knot_multiplicity_decrease(document.Uid, values),
        )
    assert calls == [] and commits == []

    document, _sketch, context, values, calls, commits, _state = _host(monkeypatch)
    with pytest.raises(NativeSketchError, match="current B-spline knot"):
        preflight_bspline_knot_multiplicity_decrease(
            context,
            prepare_bspline_knot_multiplicity_decrease(
                document.Uid, {**values, "knot_index": 99}
            ),
        )
    assert calls == [] and commits == []


def test_refuses_custom_helper_constraints(monkeypatch) -> None:
    document, sketch, context, _values_before, calls, commits, _state = _host(
        monkeypatch, exposed=True
    )
    custom = FakeConstraint("DistanceX", 1, 3, 4.0)
    custom.Tag = "custom-helper-constraint"
    sketch.Constraints.append(custom)
    sketch.ConstraintCount += 1
    with pytest.raises(NativeSketchError, match="custom constraints"):
        preflight_bspline_knot_multiplicity_decrease(
            context,
            prepare_bspline_knot_multiplicity_decrease(
                document.Uid, multiplicity_values(sketch, increment=-1)
            ),
        )
    assert calls == [] and commits == []


@pytest.mark.parametrize(
    ("change", "message"),
    (
        (lambda result, _count: result.update({"unexpected": True}), "incomplete"),
        (
            lambda result, _count: result.update({"new_multiplicity": 1}),
            "representation",
        ),
        (
            lambda result, _count: result.update({"knot_parameter": 0.75}),
            "representation",
        ),
        (lambda result, _count: result.update({"accepted": False}), "solver issue"),
    ),
)
def test_rejects_untrusted_host_diagnostics(monkeypatch, change, message) -> None:
    document, _sketch, context, values, _calls, commits, state = _host(monkeypatch)
    state["mutate_diagnostic"] = change
    with pytest.raises(NativeSketchError, match=message):
        preflight_bspline_knot_multiplicity_decrease(
            context, prepare_bspline_knot_multiplicity_decrease(document.Uid, values)
        )
    assert commits == []


def test_refuses_impure_and_second_diagnostic_drift(monkeypatch) -> None:
    document, sketch, context, values, _calls, commits, state = _host(monkeypatch)

    def impure(_result, count):
        if count == 1:
            sketch.GeometryFacadeList[0].Blocked = True

    state["mutate_diagnostic"] = impure
    with pytest.raises(NativeSketchError, match="changed the live Sketch"):
        preflight_bspline_knot_multiplicity_decrease(
            context, prepare_bspline_knot_multiplicity_decrease(document.Uid, values)
        )
    assert commits == []

    document, _sketch, context, values, _calls, commits, state = _host(monkeypatch)

    def drift(result, count):
        if count == 2:
            result["new_multiplicity"] = 1

    state["mutate_diagnostic"] = drift
    prepared = preflight_bspline_knot_multiplicity_decrease(
        context, prepare_bspline_knot_multiplicity_decrease(document.Uid, values)
    )
    with pytest.raises(NativeSketchError, match="representation"):
        create_bspline_knot_multiplicity_decrease(document, prepared)
    assert commits == []


def test_explicit_deviation_limit_and_final_representation_are_enforced(
    monkeypatch,
) -> None:
    document, _sketch, context, values, _calls, commits, state = _host(monkeypatch)

    def shape_drift(result, _count):
        result["geometry"][0]._shape_shift = 0.1
        result["geometry"][0]._refresh()

    state["mutate_diagnostic"] = shape_drift
    with pytest.raises(NativeSketchError, match="maximum_deviation_mm"):
        preflight_bspline_knot_multiplicity_decrease(
            context,
            prepare_bspline_knot_multiplicity_decrease(
                document.Uid, {**values, "maximum_deviation_mm": 0.01}
            ),
        )
    assert commits == []

    document, sketch, context, values, *_rest = _host(monkeypatch)
    prepared = preflight_bspline_knot_multiplicity_decrease(
        context, prepare_bspline_knot_multiplicity_decrease(document.Uid, values)
    )
    draft = create_bspline_knot_multiplicity_decrease(document, prepared)
    sketch.Geometry[0]._shape_shift = 0.1
    sketch.Geometry[0]._refresh()
    with pytest.raises(NativeSketchError, match="final geometry|representation"):
        verify_bspline_knot_multiplicity_decrease(document, draft)


def test_runtime_routes_one_exact_transaction(monkeypatch) -> None:
    document, _sketch, context, values, calls, commits, _state = _host(monkeypatch)
    captured = {}

    def run_immediate(runtime_context, **kwargs):
        assert runtime_context is context
        captured.update(kwargs)
        return kwargs["verify"](document, kwargs["mutate"](document))

    monkeypatch.setattr(runtime_module, "run_immediate_mutation", run_immediate)
    result = NativeSketchGeometryRuntime(context).mutate_geometry(
        {"operation": "decrease_bspline_knot_multiplicity", **values}, ticket=None
    )
    assert len(calls) == 2 and commits == [(0, 1)]
    assert captured["transaction_name"] == "Decrease Sketch B-Spline Knot Multiplicity"
    assert result["operation"] == "decrease_bspline_knot_multiplicity"
