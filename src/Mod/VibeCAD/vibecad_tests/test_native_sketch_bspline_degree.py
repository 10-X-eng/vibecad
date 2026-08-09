# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import copy
from types import MethodType, SimpleNamespace

import pytest

import VibeCADNativeSketchGeometryRuntime as runtime_module
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeSketchBSplineDegree import (
    create_bspline_degree,
    preflight_bspline_degree,
    prepare_bspline_degree,
    verify_bspline_degree,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryRuntime import NativeSketchGeometryRuntime
from vibecad_tests.native_sketch_test_support import (
    FakeBSpline,
    FakeConstraint,
    FakeLine,
    install_fake_sketch_host,
)


class DegreeBSpline(FakeBSpline):
    def __init__(self, *, degree: int = 2) -> None:
        points = [
            SimpleNamespace(x=0.0, y=0.0, z=0.0),
            SimpleNamespace(x=1.0, y=2.0, z=0.0),
            SimpleNamespace(x=3.0, y=1.0, z=0.0),
        ]
        super().__init__(
            points,
            [degree + 1, degree + 1],
            [0.0, 1.0],
            False,
            degree,
            [1.0] * len(points),
        )

    def increaseDegree(self, target: int) -> None:
        if int(target) != self._degree + 1 or target > 25:
            raise ValueError("unsupported degree elevation")
        middle = self._point_between(self._poles[-2], self._poles[-1], 0.5)
        self._poles.insert(-1, middle)
        self._weights.insert(-1, 1.0)
        self._multiplicities = [value + 1 for value in self._multiplicities]
        self._degree = int(target)
        self._refresh()

    def value(self, parameter: float):
        span = self.LastParameter - self.FirstParameter
        fraction = (
            0.0 if span == 0.0 else (float(parameter) - self.FirstParameter) / span
        )
        fraction = min(1.0, max(0.0, fraction))
        return self._point_between(self._poles[0], self._poles[-1], fraction)


def _values(sketch, indices=None, **updates) -> dict[str, object]:
    result = {
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": sketch.GeometryCount,
        "expected_constraint_count": sketch.ConstraintCount,
        "expected_external_reference_count": 0,
        "expected_external_geometry_count": 0,
        "geometry_indices": [0] if indices is None else indices,
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
    old_to_new = {str(index): index for index in range(len(before))}
    return {
        "identity": "native_tag",
        "old_to_new": old_to_new,
        "deleted": [],
        "created": [
            {"index": index, "tag": after[index]}
            for index in range(len(before), len(after))
        ],
    }


def _simulate(sketch, geometry_indices):
    simulated = copy.deepcopy(sketch)
    before_geometry = [value.Tag for value in simulated.GeometryFacadeList]
    before_constraints = [value.Tag for value in simulated.Constraints]
    old_degrees = []
    new_degrees = []
    exposed = 0
    for index in geometry_indices:
        spline = simulated.Geometry[index]
        old_degrees.append(int(spline.Degree))
        spline.increaseDegree(spline.Degree + 1)
        simulated.GeometryFacadeList[index].Geometry = spline
        new_degrees.append(int(spline.Degree))
        for constraint in simulated.Constraints:
            if constraint.Type != "InternalAlignment" or constraint.Second != index:
                continue
            helper = simulated.Geometry[constraint.First]
            alignment_index = constraint.InternalAlignmentIndex
            if constraint.AlignmentType == "BSplineControlPoint":
                center = copy.deepcopy(spline.getPoles()[alignment_index])
                helper.Center = center
                helper.Location = center
            elif constraint.AlignmentType == "BSplineKnotPoint":
                point = spline.value(spline.getKnots()[alignment_index])
                helper.X, helper.Y, helper.Z = point.x, point.y, point.z
        exposed += simulated.exposeInternalGeometry(index)["created_count"]
    for index, facade in enumerate(simulated.GeometryFacadeList):
        if not facade.Tag:
            facade.Tag = f"degree-geometry-{index}"
    for index, constraint in enumerate(simulated.Constraints):
        if not getattr(constraint, "Tag", ""):
            constraint.Tag = f"degree-constraint-{index}"
    simulated.GeometryCount = len(simulated.Geometry)
    simulated.ConstraintCount = len(simulated.Constraints)
    after_geometry = [value.Tag for value in simulated.GeometryFacadeList]
    after_constraints = [value.Tag for value in simulated.Constraints]
    receipt = {
        "geometry": _collection_receipt(before_geometry, after_geometry),
        "constraints": _collection_receipt(before_constraints, after_constraints),
    }
    return simulated, old_degrees, new_degrees, exposed, receipt


def _diagnostic(sketch, geometry_indices) -> dict[str, object]:
    simulated, old_degrees, new_degrees, exposed, receipt = _simulate(
        sketch, geometry_indices
    )
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
        "input_geometry_indices": list(geometry_indices),
        "old_degrees": old_degrees,
        "new_degrees": new_degrees,
        "exposed_internal_geometry_count": exposed,
        "geometry_tags": [value.Tag for value in simulated.GeometryFacadeList],
        "constraint_tags": [value.Tag for value in simulated.Constraints],
        "expressions": [],
        "mutation_receipt": receipt,
    }


def _install_degree_host(sketch):
    calls = []
    commits = []
    state = {"mutate_diagnostic": None}

    def diagnose(self, geometry_indices):
        calls.append(tuple(geometry_indices))
        result = _diagnostic(self, geometry_indices)
        mutation = state["mutate_diagnostic"]
        if mutation is not None:
            mutation(result, len(calls))
        return result

    def commit(self, geometry_indices):
        commits.append(tuple(geometry_indices))
        simulated, _old, _new, _exposed, receipt = _simulate(self, geometry_indices)
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

    sketch.diagnoseIncreaseBSplineDegree = MethodType(diagnose, sketch)
    sketch.increaseBSplineDegreeExact = MethodType(commit, sketch)
    return calls, commits, state


def _host(monkeypatch, *, exposed=False, degree=2):
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch._persistent_geometry_tags = True
    spline = DegreeBSpline(degree=degree)
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
            helper.Tag = f"existing-helper-{index}"
        for index, item in enumerate(sketch.Constraints[1:], 1):
            item.Tag = f"existing-constraint-{index}"
    calls, commits, state = _install_degree_host(sketch)
    return document, sketch, context, _values(sketch), calls, commits, state


def _corrupt_multiplicity(result, _count) -> None:
    result["geometry"][0]._multiplicities[0] += 2


def _change_shape(result, _count) -> None:
    curve = result["geometry"][0]
    curve._poles[-1].x += 1.0
    curve._refresh()


def _unconstruct_created_helper(result, _count) -> None:
    result["geometry_metadata"][-1]["Construction"] = False


def _misalign_existing_helper(result, _count) -> None:
    helper = result["geometry"][1]
    point = SimpleNamespace(x=99.0, y=99.0, z=0.0)
    helper.Center = point
    helper.Location = point


def test_degree_target_is_closed_internal_bounded_and_ordered(monkeypatch) -> None:
    document, sketch, _context, values, *_rest = _host(monkeypatch)
    second = sketch.addGeometry(DegreeBSpline(), False)
    spec = prepare_bspline_degree(document.Uid, _values(sketch, [second, 0]))
    assert spec.geometry_indices == (1, 0)
    for arguments in (
        {**values, "unexpected": True},
        {**values, "geometry_indices": []},
        {**values, "geometry_indices": [0, 0]},
        {**values, "geometry_indices": [-3]},
        {**values, "geometry_indices": [True]},
        {**values, "geometry_indices": list(range(257))},
        {**values, "expected_external_geometry_count": -1},
    ):
        with pytest.raises(NativeSketchError):
            prepare_bspline_degree(document.Uid, arguments)


@pytest.mark.parametrize("exposed", (False, True))
def test_degree_diagnosis_is_pure_and_exact_commit_is_verified(
    monkeypatch, exposed
) -> None:
    document, sketch, context, values, calls, commits, _state = _host(
        monkeypatch, exposed=exposed
    )
    before = copy.deepcopy(sketch.Geometry[0].getPoles())
    before_counts = (sketch.GeometryCount, sketch.ConstraintCount)
    prepared = preflight_bspline_degree(
        context, prepare_bspline_degree(document.Uid, values)
    )
    assert (sketch.GeometryCount, sketch.ConstraintCount) == before_counts
    draft = create_bspline_degree(document, prepared)
    result = verify_bspline_degree(document, draft)
    assert isinstance(draft, NativeMutationDraft)
    assert len(calls) == 2 and len(commits) == 1
    assert sketch.Geometry[0].Degree == 3
    assert sketch.GeometryFacadeList[0].Tag == "spline-root"
    assert sketch.GeometryFacadeList[0].Construction is True
    assert sketch.GeometryFacadeList[0].GeometryLayerId == 7
    assert sketch.ExpressionEngine == [("Constraints[0]", "Spreadsheet.Length")]
    assert sketch.Geometry[0].getPoles()[0].x == before[0].x
    assert result["operation"] == "increase_bspline_degree"
    assert result["geometry_indices"] == [0]
    assert result["old_degrees"] == [2]
    assert result["new_degrees"] == [3]
    assert result["exposed_internal_geometry_count"] == (1 if exposed else 6)


@pytest.mark.parametrize(
    ("change", "message"),
    (
        (lambda result, _count: result.update({"unexpected": True}), "incomplete"),
        (lambda result, _count: result.update({"new_degrees": [9]}), "degree"),
        (
            lambda result, _count: result.update(
                {"exposed_internal_geometry_count": 0}
            ),
            "wrong number",
        ),
        (
            lambda result, _count: setattr(result["geometry"][0], "Degree", 9),
            "degree",
        ),
        (_corrupt_multiplicity, "multiplicities"),
        (_change_shape, "shape"),
        (_unconstruct_created_helper, "invalid spline helpers"),
        (lambda result, _count: result.update({"accepted": False}), "solver issue"),
    ),
)
def test_degree_rejects_untrusted_host_diagnostics(
    monkeypatch, change, message
) -> None:
    document, _sketch, context, values, _calls, commits, state = _host(monkeypatch)
    state["mutate_diagnostic"] = change
    with pytest.raises(NativeSketchError, match=message):
        preflight_bspline_degree(context, prepare_bspline_degree(document.Uid, values))
    assert commits == []


def test_degree_rejects_misaligned_existing_helpers(monkeypatch) -> None:
    document, _sketch, context, values, _calls, commits, state = _host(
        monkeypatch, exposed=True
    )
    state["mutate_diagnostic"] = _misalign_existing_helper
    with pytest.raises(NativeSketchError, match="do not represent"):
        preflight_bspline_degree(context, prepare_bspline_degree(document.Uid, values))
    assert commits == []


@pytest.mark.parametrize("invalid", (FakeLine(), DegreeBSpline(degree=25)))
def test_degree_rejects_non_splines_and_max_degree(monkeypatch, invalid) -> None:
    document, sketch, context, values, calls, commits, _state = _host(monkeypatch)
    sketch.Geometry[0] = invalid
    sketch.GeometryFacadeList[0].Geometry = invalid
    with pytest.raises(NativeSketchError):
        preflight_bspline_degree(context, prepare_bspline_degree(document.Uid, values))
    assert calls == []
    assert commits == []


def test_degree_refuses_impure_and_second_diagnostic_drift(monkeypatch) -> None:
    document, sketch, context, values, _calls, commits, state = _host(monkeypatch)

    def impure(_result, count):
        if count == 1:
            sketch.GeometryFacadeList[0].Blocked = True

    state["mutate_diagnostic"] = impure
    with pytest.raises(NativeSketchError, match="changed the live Sketch"):
        preflight_bspline_degree(context, prepare_bspline_degree(document.Uid, values))
    assert commits == []

    document, _sketch, context, values, _calls, commits, state = _host(monkeypatch)

    def drift(result, count):
        if count == 2:
            result["new_degrees"] = [4]

    state["mutate_diagnostic"] = drift
    prepared = preflight_bspline_degree(
        context, prepare_bspline_degree(document.Uid, values)
    )
    with pytest.raises(NativeSketchError, match="degree"):
        create_bspline_degree(document, prepared)
    assert commits == []


def test_degree_final_verifier_rejects_semantic_drift(monkeypatch) -> None:
    document, sketch, context, values, *_rest = _host(monkeypatch)
    prepared = preflight_bspline_degree(
        context, prepare_bspline_degree(document.Uid, values)
    )
    draft = create_bspline_degree(document, prepared)
    sketch.GeometryFacadeList[-1].Construction = False
    with pytest.raises(NativeSketchError, match="final geometry"):
        verify_bspline_degree(document, draft)


def test_geometry_runtime_routes_degree_through_one_exact_transaction(
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
        {"operation": "increase_bspline_degree", **values}, ticket=None
    )
    assert len(calls) == 2 and len(commits) == 1
    assert captured["transaction_name"] == "Increase Sketch B-Spline Degree"
    assert result["operation"] == "increase_bspline_degree"
