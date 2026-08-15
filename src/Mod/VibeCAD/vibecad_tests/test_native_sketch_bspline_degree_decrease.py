# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import copy
from types import MethodType, SimpleNamespace

import pytest

import VibeCADNativeSketchGeometryRuntime as runtime_module
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeSketchBSplineDegreeDecrease import (
    create_bspline_degree_decrease,
    preflight_bspline_degree_decrease,
    prepare_bspline_degree_decrease,
    verify_bspline_degree_decrease,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryRuntime import NativeSketchGeometryRuntime
from vibecad_tests.native_sketch_test_support import (
    FakeBSpline,
    FakeConstraint,
    FakeLine,
    install_fake_sketch_host,
)


class ReducibleBSpline(FakeBSpline):
    def __init__(self, *, degree: int = 3) -> None:
        points = [
            SimpleNamespace(x=0.0, y=0.0, z=0.0),
            SimpleNamespace(x=1.0, y=2.0, z=0.0),
            SimpleNamespace(x=3.0, y=-1.0, z=0.0),
            SimpleNamespace(x=4.0, y=1.0, z=0.0),
        ]
        if degree == 1:
            points = [points[0], points[-1]]
            multiplicities = [2, 2]
        else:
            multiplicities = [degree + 1, degree + 1]
        super().__init__(
            points,
            multiplicities,
            [0.0, 1.0],
            False,
            degree,
            [1.0] * len(points),
        )

    def reduced(self):
        if self.Degree <= 1:
            raise ValueError("degree cannot be reduced")
        result = copy.deepcopy(self)
        result._degree = self.Degree - 1
        result._poles = [
            copy.deepcopy(self._poles[0]),
            SimpleNamespace(x=1.1, y=1.4, z=0.0),
            SimpleNamespace(x=2.8, y=-0.2, z=0.0),
            copy.deepcopy(self._poles[-1]),
        ]
        result._knots = [0.0, 0.5, 1.0]
        result._multiplicities = [3, 1, 3]
        result._weights = [1.0] * len(result._poles)
        result._refresh()
        return result


def _values(sketch, **updates) -> dict[str, object]:
    result = {
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": sketch.GeometryCount,
        "expected_constraint_count": sketch.ConstraintCount,
        "expected_external_reference_count": 0,
        "expected_external_geometry_count": 0,
        "geometry_index": 0,
        "maximum_deviation_mm": 10.0,
    }
    result.update(updates)
    return result


def _metadata(facade) -> dict[str, object]:
    return {
        "Id": int(facade.Id),
        "Construction": bool(facade.Construction),
        "Blocked": bool(facade.Blocked),
        "InternalType": str(facade.InternalType),
        "GeometryLayerId": int(facade.GeometryLayerId),
    }


def _collection_receipt(before, after) -> dict[str, object]:
    remaining = {tag: index for index, tag in enumerate(after)}
    old_to_new = {}
    deleted = []
    for index, tag in enumerate(before):
        mapped = remaining.pop(tag, None)
        if mapped is None:
            deleted.append({"index": index, "tag": tag})
        else:
            old_to_new[str(index)] = mapped
    return {
        "identity": "native_tag",
        "old_to_new": old_to_new,
        "deleted": deleted,
        "created": [{"index": index, "tag": tag} for tag, index in remaining.items()],
    }


def _constraint_geometry(constraint) -> set[int]:
    return {
        int(getattr(constraint, field, -2000))
        for field in ("First", "Second", "Third")
        if int(getattr(constraint, field, -2000)) >= 0
    }


def _simulate(sketch, geometry_index):
    simulated = copy.deepcopy(sketch)
    before_geometry = [value.Tag for value in simulated.GeometryFacadeList]
    before_constraints = [value.Tag for value in simulated.Constraints]
    old_degree = int(simulated.Geometry[geometry_index].Degree)
    old_helpers = set(range(geometry_index + 1, simulated.GeometryCount))
    simulated.Geometry[geometry_index] = simulated.Geometry[geometry_index].reduced()
    root_facade = simulated.GeometryFacadeList[geometry_index]
    root_facade.Geometry = simulated.Geometry[geometry_index]
    simulated.Geometry = simulated.Geometry[: geometry_index + 1]
    simulated.GeometryFacadeList = simulated.GeometryFacadeList[: geometry_index + 1]
    simulated.Constraints = [
        item
        for item in simulated.Constraints
        if not _constraint_geometry(item) & old_helpers
    ]
    simulated.GeometryCount = len(simulated.Geometry)
    simulated.ConstraintCount = len(simulated.Constraints)
    exposed = simulated.exposeInternalGeometry(geometry_index)["created_count"]
    for index, facade in enumerate(simulated.GeometryFacadeList):
        if not facade.Tag:
            facade.Tag = f"decrease-geometry-{index}"
    for index, constraint in enumerate(simulated.Constraints):
        if not getattr(constraint, "Tag", ""):
            constraint.Tag = f"decrease-constraint-{index}"
    after_geometry = [value.Tag for value in simulated.GeometryFacadeList]
    after_constraints = [value.Tag for value in simulated.Constraints]
    receipt = {
        "geometry": _collection_receipt(before_geometry, after_geometry),
        "constraints": _collection_receipt(before_constraints, after_constraints),
    }
    return simulated, old_degree, exposed, len(old_helpers), receipt


def _diagnostic(sketch, geometry_index) -> dict[str, object]:
    simulated, old_degree, exposed, deleted, receipt = _simulate(sketch, geometry_index)
    return {
        "accepted": True,
        "degrees_of_freedom": simulated.DoF,
        "solver_status": 0,
        "conflicting_constraint_indices": [],
        "redundant_constraint_indices": [],
        "partially_redundant_constraint_indices": [],
        "malformed_constraint_indices": [],
        "geometry_count": simulated.GeometryCount,
        "constraint_count": simulated.ConstraintCount,
        "geometry": simulated.Geometry,
        "geometry_metadata": [
            _metadata(value) for value in simulated.GeometryFacadeList
        ],
        "constraints": simulated.Constraints,
        "external_reference_count": 0,
        "external_references": [],
        "external_geometry_count": 0,
        "external_geometry": [],
        "external_geometry_metadata": [],
        "input_geometry_index": geometry_index,
        "output_geometry_index": geometry_index,
        "old_degree": old_degree,
        "new_degree": old_degree - 1,
        "retained_internal_geometry_count": 0,
        "deleted_internal_geometry_count": deleted,
        "exposed_internal_geometry_count": exposed,
        "geometry_tags": [value.Tag for value in simulated.GeometryFacadeList],
        "constraint_tags": [value.Tag for value in simulated.Constraints],
        "expressions": [],
        "mutation_receipt": receipt,
    }


def _install_host(sketch):
    calls = []
    commits = []
    state = {"mutate_diagnostic": None}

    def diagnose(self, geometry_index):
        calls.append(int(geometry_index))
        result = _diagnostic(self, geometry_index)
        mutation = state["mutate_diagnostic"]
        if mutation is not None:
            mutation(result, len(calls))
        return result

    def commit(self, geometry_index):
        commits.append(int(geometry_index))
        simulated, _old, _exposed, _deleted, receipt = _simulate(self, geometry_index)
        for field in (
            "Geometry",
            "GeometryFacadeList",
            "Constraints",
            "GeometryCount",
            "ConstraintCount",
            "DoF",
        ):
            setattr(self, field, getattr(simulated, field))
        return receipt

    sketch.diagnoseDecreaseBSplineDegree = MethodType(diagnose, sketch)
    sketch.decreaseBSplineDegreeExact = MethodType(commit, sketch)
    return calls, commits, state


def _host(monkeypatch, *, exposed=False, degree=3):
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch._persistent_geometry_tags = True
    spline = ReducibleBSpline(degree=degree)
    sketch.Geometry[0] = spline
    facade = sketch.GeometryFacadeList[0]
    facade.Geometry = spline
    facade.Tag = "spline-root"
    facade.Construction = True
    facade.GeometryLayerId = 7
    constraint = FakeConstraint("Distance", 0, 5.0)
    constraint.Tag = "distance-0"
    sketch.Constraints = [constraint]
    sketch.ConstraintCount = 1
    sketch.ExpressionEngine = [("Constraints[0]", "Spreadsheet.Length")]
    if exposed:
        sketch.exposeInternalGeometry(0)
        for index, helper in enumerate(sketch.GeometryFacadeList[1:], 1):
            helper.Tag = f"old-helper-{index}"
        for index, item in enumerate(sketch.Constraints[1:], 1):
            item.Tag = f"old-constraint-{index}"
    calls, commits, state = _install_host(sketch)
    return document, sketch, context, _values(sketch), calls, commits, state


def test_decrease_target_is_closed_scalar_and_tolerance_bounded(monkeypatch) -> None:
    document, _sketch, _context, values, *_rest = _host(monkeypatch)
    spec = prepare_bspline_degree_decrease(document.Uid, values)
    assert spec.geometry_index == 0
    assert spec.maximum_deviation_mm == 10.0
    for arguments in (
        {**values, "unexpected": True},
        {**values, "geometry_index": [0]},
        {**values, "geometry_index": True},
        {**values, "geometry_index": -1},
        {**values, "maximum_deviation_mm": True},
        {**values, "maximum_deviation_mm": -1.0},
        {**values, "maximum_deviation_mm": float("inf")},
    ):
        with pytest.raises(NativeSketchError):
            prepare_bspline_degree_decrease(document.Uid, arguments)


@pytest.mark.parametrize("exposed", (False, True))
def test_decrease_diagnosis_is_pure_and_exact_commit_is_verified(
    monkeypatch, exposed
) -> None:
    document, sketch, context, values, calls, commits, _state = _host(
        monkeypatch, exposed=exposed
    )
    before_counts = (sketch.GeometryCount, sketch.ConstraintCount)
    prepared = preflight_bspline_degree_decrease(
        context, prepare_bspline_degree_decrease(document.Uid, values)
    )
    assert (sketch.GeometryCount, sketch.ConstraintCount) == before_counts
    draft = create_bspline_degree_decrease(document, prepared)
    result = verify_bspline_degree_decrease(document, draft)
    assert isinstance(draft, NativeMutationDraft)
    assert len(calls) == 2 and commits == [0]
    assert sketch.Geometry[0].Degree == 2
    assert sketch.GeometryFacadeList[0].Tag == "spline-root"
    assert sketch.GeometryFacadeList[0].Construction is True
    assert sketch.GeometryFacadeList[0].GeometryLayerId == 7
    assert sketch.ExpressionEngine == [("Constraints[0]", "Spreadsheet.Length")]
    assert result["operation"] == "decrease_bspline_degree"
    assert result["geometry_index"] == 0
    assert (result["old_degree"], result["new_degree"]) == (3, 2)
    assert 0.0 < result["measured_deviation_mm"] < 10.0


def test_decrease_refuses_loss_above_explicit_limit(monkeypatch) -> None:
    document, _sketch, context, values, _calls, commits, _state = _host(monkeypatch)
    with pytest.raises(NativeSketchError, match="maximum_deviation_mm"):
        preflight_bspline_degree_decrease(
            context,
            prepare_bspline_degree_decrease(
                document.Uid, {**values, "maximum_deviation_mm": 0.0}
            ),
        )
    assert commits == []


@pytest.mark.parametrize("invalid", (FakeLine(), ReducibleBSpline(degree=1)))
def test_decrease_rejects_non_splines_and_linear_splines(monkeypatch, invalid) -> None:
    document, sketch, context, values, calls, commits, _state = _host(monkeypatch)
    sketch.Geometry[0] = invalid
    sketch.GeometryFacadeList[0].Geometry = invalid
    with pytest.raises(NativeSketchError):
        preflight_bspline_degree_decrease(
            context, prepare_bspline_degree_decrease(document.Uid, values)
        )
    assert calls == []
    assert commits == []


def test_decrease_refuses_custom_helper_constraints(monkeypatch) -> None:
    document, sketch, context, _values_before, calls, commits, _state = _host(
        monkeypatch, exposed=True
    )
    helper = 1
    custom = FakeConstraint("DistanceX", helper, 3, 4.0)
    custom.Tag = "custom-helper-constraint"
    sketch.Constraints.append(custom)
    sketch.ConstraintCount += 1
    with pytest.raises(NativeSketchError, match="custom constraints"):
        preflight_bspline_degree_decrease(
            context,
            prepare_bspline_degree_decrease(document.Uid, _values(sketch)),
        )
    assert calls == []
    assert commits == []


def test_decrease_refuses_expressions_on_helper_constraints(monkeypatch) -> None:
    document, sketch, context, _values_before, calls, commits, _state = _host(
        monkeypatch, exposed=True
    )
    sketch.ExpressionEngine.append(("Constraints[1]", "Spreadsheet.Weight"))
    with pytest.raises(NativeSketchError, match="expressions"):
        preflight_bspline_degree_decrease(
            context,
            prepare_bspline_degree_decrease(document.Uid, _values(sketch)),
        )
    assert calls == []
    assert commits == []


def test_decrease_rejects_diagnostic_that_deletes_unrelated_geometry(
    monkeypatch,
) -> None:
    document, sketch, context, _values_before, _calls, commits, _state = _host(
        monkeypatch, exposed=True
    )
    line = sketch.addGeometry(FakeLine(), False)
    sketch.GeometryFacadeList[line].Tag = "unrelated-line"
    with pytest.raises(NativeSketchError, match="durable geometry identities"):
        preflight_bspline_degree_decrease(
            context,
            prepare_bspline_degree_decrease(document.Uid, _values(sketch)),
        )
    assert commits == []


@pytest.mark.parametrize(
    ("change", "message"),
    (
        (lambda result, _count: result.update({"unexpected": True}), "incomplete"),
        (lambda result, _count: result.update({"new_degree": 9}), "degree"),
        (
            lambda result, _count: result.update(
                {"exposed_internal_geometry_count": 0}
            ),
            "identities",
        ),
        (
            lambda result, _count: setattr(result["geometry"][0], "Degree", 9),
            "degree",
        ),
        (lambda result, _count: result.update({"accepted": False}), "solver issue"),
    ),
)
def test_decrease_rejects_untrusted_host_diagnostics(
    monkeypatch, change, message
) -> None:
    document, _sketch, context, values, _calls, commits, state = _host(monkeypatch)
    state["mutate_diagnostic"] = change
    with pytest.raises(NativeSketchError, match=message):
        preflight_bspline_degree_decrease(
            context, prepare_bspline_degree_decrease(document.Uid, values)
        )
    assert commits == []


def test_decrease_refuses_impure_and_second_diagnostic_drift(monkeypatch) -> None:
    document, sketch, context, values, _calls, commits, state = _host(monkeypatch)

    def impure(_result, count):
        if count == 1:
            sketch.GeometryFacadeList[0].Blocked = True

    state["mutate_diagnostic"] = impure
    with pytest.raises(NativeSketchError, match="changed the live Sketch"):
        preflight_bspline_degree_decrease(
            context, prepare_bspline_degree_decrease(document.Uid, values)
        )
    assert commits == []

    document, _sketch, context, values, _calls, commits, state = _host(monkeypatch)

    def drift(result, count):
        if count == 2:
            result["new_degree"] = 1

    state["mutate_diagnostic"] = drift
    prepared = preflight_bspline_degree_decrease(
        context, prepare_bspline_degree_decrease(document.Uid, values)
    )
    with pytest.raises(NativeSketchError, match="degree"):
        create_bspline_degree_decrease(document, prepared)
    assert commits == []


def test_decrease_accepts_fresh_created_tags_across_diagnosis_and_commit(
    monkeypatch,
) -> None:
    document, _sketch, context, values, calls, commits, state = _host(monkeypatch)

    def fresh_created_tags(result, count):
        if count != 2:
            return
        for collection, tags_field in (
            ("geometry", "geometry_tags"),
            ("constraints", "constraint_tags"),
        ):
            for item in result["mutation_receipt"][collection]["created"]:
                tag = f"second-{collection}-{item['index']}"
                item["tag"] = tag
                result[tags_field][item["index"]] = tag

    state["mutate_diagnostic"] = fresh_created_tags
    prepared = preflight_bspline_degree_decrease(
        context, prepare_bspline_degree_decrease(document.Uid, values)
    )
    draft = create_bspline_degree_decrease(document, prepared)
    result = verify_bspline_degree_decrease(document, draft)
    assert len(calls) == 2 and commits == [0]
    assert result["new_degree"] == 2


def test_decrease_final_verifier_rejects_semantic_drift(monkeypatch) -> None:
    document, sketch, context, values, *_rest = _host(monkeypatch)
    prepared = preflight_bspline_degree_decrease(
        context, prepare_bspline_degree_decrease(document.Uid, values)
    )
    draft = create_bspline_degree_decrease(document, prepared)
    sketch.GeometryFacadeList[-1].Construction = False
    with pytest.raises(NativeSketchError, match="final geometry"):
        verify_bspline_degree_decrease(document, draft)


def test_decrease_final_verifier_rejects_curve_representation_drift(
    monkeypatch,
) -> None:
    document, sketch, context, values, *_rest = _host(monkeypatch)
    prepared = preflight_bspline_degree_decrease(
        context, prepare_bspline_degree_decrease(document.Uid, values)
    )
    draft = create_bspline_degree_decrease(document, prepared)
    sketch.Geometry[0]._poles[1].x += 0.25
    sketch.Geometry[0]._refresh()
    with pytest.raises(NativeSketchError, match="final geometry|representation"):
        verify_bspline_degree_decrease(document, draft)


def test_geometry_runtime_routes_decrease_through_one_exact_transaction(
    monkeypatch,
) -> None:
    document, _sketch, context, values, calls, commits, _state = _host(monkeypatch)
    captured = {}

    def run_immediate(runtime_context, **kwargs):
        assert runtime_context is context
        captured.update(kwargs)
        return kwargs["verify"](document, kwargs["mutate"](document))

    monkeypatch.setattr(runtime_module, "run_immediate_mutation", run_immediate)
    result = NativeSketchGeometryRuntime(context).mutate_geometry(
        {"operation": "decrease_bspline_degree", **values}, ticket=None
    )
    assert len(calls) == 2 and commits == [0]
    assert captured["transaction_name"] == "Decrease Sketch B-Spline Degree"
    assert result["operation"] == "decrease_bspline_degree"
