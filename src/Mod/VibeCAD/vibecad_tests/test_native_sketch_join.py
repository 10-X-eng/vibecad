# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import copy

import pytest

import VibeCADNativeSketchGeometryRuntime as runtime_module
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryRuntime import NativeSketchGeometryRuntime
from VibeCADNativeSketchJoin import (
    create_sketch_join,
    preflight_sketch_join,
    prepare_sketch_join,
    verify_sketch_join,
)
from vibecad_tests.native_sketch_join_test_support import install_join_host
from vibecad_tests.native_sketch_test_support import FakeCircle, FakeLine


@pytest.mark.parametrize(
    "change",
    (
        lambda value: value.update({"unexpected": True}),
        lambda value: value.update(
            {"first": {"geometry_index": 0, "endpoint": "whole"}}
        ),
        lambda value: value.update(
            {"first": {"geometry_index": True, "endpoint": "end"}}
        ),
        lambda value: value.update(
            {"second": {"geometry_index": 0, "endpoint": "start"}}
        ),
        lambda value: value.update({"expected_external_reference_count": -1}),
    ),
)
def test_join_target_is_closed_and_strict(monkeypatch, change) -> None:
    document, _sketch, _context, values, *_rest = install_join_host(monkeypatch)
    change(values)
    with pytest.raises(NativeSketchError):
        prepare_sketch_join(document.Uid, values)


@pytest.mark.parametrize(("tangent", "continuity"), ((False, "C0"), (True, "C1")))
def test_join_diagnosis_is_pure_and_exact_commit_is_verified(
    monkeypatch,
    tangent: bool,
    continuity: str,
) -> None:
    document, sketch, context, values, calls, commits, _state = install_join_host(
        monkeypatch, tangent=tangent
    )
    before_geometry = copy.deepcopy(sketch.Geometry)
    before_expressions = list(sketch.ExpressionEngine)
    prepared = preflight_sketch_join(context, prepare_sketch_join(document.Uid, values))
    assert len(calls) == 1
    assert sketch.Geometry[0].StartPoint.x == before_geometry[0].StartPoint.x
    assert sketch.ExpressionEngine == before_expressions

    draft = create_sketch_join(document, prepared)
    result = verify_sketch_join(document, draft)
    assert isinstance(draft, NativeMutationDraft)
    assert len(calls) == 2
    assert commits == [(0, 2, 1, 1)]
    assert result["operation"] == "join_curves"
    assert result["continuity"] == continuity
    assert result["joined_geometry"]["kind"] == "b_spline"
    assert result["joined_geometry"]["start_mm"] == [0.0, 0.0, 0.0]
    assert result["joined_geometry"]["end_mm"] == [
        20.0,
        0.0 if tangent else 5.0,
        0.0,
    ]
    assert result["deleted_geometry_indices"] == [0, 1]
    assert result["created_helper_count"] == (
        sketch.Geometry[0].NbPoles + sketch.Geometry[0].NbKnots
    )
    assert sketch.ExpressionEngine == [("Constraints[0]", "12 mm")]
    assert sketch.Constraints[0].Name == "PreservedLength"
    assert sketch.GeometryFacadeList[1].Tag == "unrelated-geometry"


def test_join_refuses_stale_grouped_closed_and_mixed_targets(monkeypatch) -> None:
    document, sketch, context, values, calls, commits, _state = install_join_host(
        monkeypatch
    )
    values["expected_geometry_count"] = 4
    with pytest.raises(NativeSketchError, match="count"):
        preflight_sketch_join(context, prepare_sketch_join(document.Uid, values))
    values["expected_geometry_count"] = 3
    sketch.Geometry[0] = FakeCircle(
        sketch.Geometry[0].StartPoint, sketch.Geometry[0].StartPoint, 2.0
    )
    sketch.GeometryFacadeList[0].Geometry = sketch.Geometry[0]
    with pytest.raises(NativeSketchError, match="open"):
        preflight_sketch_join(context, prepare_sketch_join(document.Uid, values))
    sketch.Geometry[0] = FakeLine()
    sketch.GeometryFacadeList[0].Geometry = sketch.Geometry[0]
    sketch.GeometryFacadeList[1].Construction = True
    with pytest.raises(NativeSketchError, match="construction"):
        preflight_sketch_join(context, prepare_sketch_join(document.Uid, values))
    assert calls == [] and commits == []


@pytest.mark.parametrize(
    ("change", "message"),
    (
        (lambda result, _count: result.update({"extra": True}), "incomplete"),
        (
            lambda result, _count: result.update({"second_endpoint": 2}),
            "different target",
        ),
        (
            lambda result, _count: result.update({"continuity": 1}),
            "different target",
        ),
        (
            lambda result, _count: result["mutation_receipt"]["geometry"][
                "deleted"
            ].pop(),
            "account",
        ),
        (
            lambda result, _count: result["geometry"][0].__setattr__(
                "StartPoint", result["geometry"][1].StartPoint
            ),
            "joined curve",
        ),
        (
            lambda result, _count: result["constraints"][2].__setattr__(
                "InternalAlignmentIndex", 0
            ),
            "duplicate helper alignment",
        ),
        (
            lambda result, _count: result.update({"accepted": False}),
            "solver issue",
        ),
    ),
)
def test_join_rejects_untrusted_diagnostics(monkeypatch, change, message) -> None:
    document, _sketch, context, values, _calls, commits, state = install_join_host(
        monkeypatch
    )
    state["mutate_diagnostic"] = change
    with pytest.raises(NativeSketchError, match=message):
        preflight_sketch_join(context, prepare_sketch_join(document.Uid, values))
    assert commits == []


def test_join_refuses_impure_or_changed_second_diagnosis(monkeypatch) -> None:
    document, sketch, context, values, _calls, commits, state = install_join_host(
        monkeypatch
    )

    def impure(_result, count):
        if count == 1:
            sketch.GeometryFacadeList[2].Blocked = True

    state["mutate_diagnostic"] = impure
    with pytest.raises(NativeSketchError, match="changed the live Sketch"):
        preflight_sketch_join(context, prepare_sketch_join(document.Uid, values))
    assert commits == []

    document, _sketch, context, values, _calls, commits, state = install_join_host(
        monkeypatch
    )

    def drift(result, count):
        if count == 2:
            result["continuity"] = 1

    state["mutate_diagnostic"] = drift
    prepared = preflight_sketch_join(context, prepare_sketch_join(document.Uid, values))
    with pytest.raises(NativeSketchError, match="different target"):
        create_sketch_join(document, prepared)
    assert commits == []


def test_join_rejects_final_state_drift(monkeypatch) -> None:
    document, sketch, context, values, *_rest = install_join_host(monkeypatch)
    prepared = preflight_sketch_join(context, prepare_sketch_join(document.Uid, values))
    draft = create_sketch_join(document, prepared)
    sketch.Geometry[0].EndPoint.x = 21.0
    with pytest.raises(NativeSketchError, match="final geometry"):
        verify_sketch_join(document, draft)


def test_join_runtime_routes_one_exact_transaction(monkeypatch) -> None:
    document, _sketch, context, values, calls, commits, _state = install_join_host(
        monkeypatch
    )
    captured = {}

    def run_immediate(runtime_context, **kwargs):
        assert runtime_context is context
        captured.update(kwargs)
        return kwargs["verify"](document, kwargs["mutate"](document))

    monkeypatch.setattr(runtime_module, "run_immediate_mutation", run_immediate)
    result = NativeSketchGeometryRuntime(context).mutate_geometry(
        {"operation": "join_curves", **values}, ticket=None
    )
    assert len(calls) == 2 and commits == [(0, 2, 1, 1)]
    assert captured["transaction_name"] == "Join Native Sketch Curves"
    assert result["operation"] == "join_curves"
