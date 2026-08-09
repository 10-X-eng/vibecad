# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import copy
from types import MethodType, SimpleNamespace

import pytest

import VibeCADNativeSketchGeometryRuntime as runtime_module
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeSketchBSplineKnotMultiplicityIncrease import (
    create_bspline_knot_multiplicity_increase,
    preflight_bspline_knot_multiplicity_increase,
    prepare_bspline_knot_multiplicity_increase,
    verify_bspline_knot_multiplicity_increase,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryRuntime import NativeSketchGeometryRuntime
from vibecad_tests.native_sketch_test_support import (
    FakeBSpline,
    FakeConstraint,
    FakeLine,
    install_fake_sketch_host,
)


class MultiplicityBSpline(FakeBSpline):
    def __init__(self) -> None:
        self._shape_shift = 0.0
        super().__init__(
            [
                SimpleNamespace(x=0.0, y=0.0, z=0.0),
                SimpleNamespace(x=2.0, y=3.0, z=0.0),
                SimpleNamespace(x=5.0, y=-2.0, z=0.0),
                SimpleNamespace(x=8.0, y=2.0, z=0.0),
                SimpleNamespace(x=10.0, y=0.0, z=0.0),
            ],
            [4, 1, 4],
            [0.0, 0.5, 1.0],
            False,
            3,
            [1.0] * 5,
        )

    def value(self, parameter: float):
        value = min(1.0, max(0.0, float(parameter)))
        return SimpleNamespace(
            x=10.0 * value,
            y=6.0 * value * (1.0 - value) * (1.0 - 2.0 * value) + self._shape_shift,
            z=0.0,
        )

    def increased(self, knot_index: int):
        result = copy.deepcopy(self)
        result._multiplicities[knot_index] += 1
        result._poles.insert(3, SimpleNamespace(x=6.5, y=-0.5, z=0.0))
        result._weights.insert(3, 1.0)
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
        "knot_index": 1,
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


def _simulate(sketch, geometry_index, knot_index):
    simulated = copy.deepcopy(sketch)
    before_geometry = [value.Tag for value in simulated.GeometryFacadeList]
    before_constraints = [value.Tag for value in simulated.Constraints]
    source = simulated.Geometry[geometry_index]
    old_multiplicity = source.getMultiplicities()[knot_index]
    old_helpers = set(range(geometry_index + 1, simulated.GeometryCount))
    simulated.Geometry[geometry_index] = source.increased(knot_index)
    simulated.GeometryFacadeList[geometry_index].Geometry = simulated.Geometry[
        geometry_index
    ]
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
            facade.Tag = f"multiplicity-geometry-{index}"
    for index, constraint in enumerate(simulated.Constraints):
        if not getattr(constraint, "Tag", ""):
            constraint.Tag = f"multiplicity-constraint-{index}"
    receipt = {
        "geometry": _collection_receipt(
            before_geometry, [value.Tag for value in simulated.GeometryFacadeList]
        ),
        "constraints": _collection_receipt(
            before_constraints, [value.Tag for value in simulated.Constraints]
        ),
    }
    return simulated, old_multiplicity, exposed, len(old_helpers), receipt


def _diagnostic(sketch, geometry_index, knot_index) -> dict[str, object]:
    simulated, old, exposed, deleted, receipt = _simulate(
        sketch, geometry_index, knot_index
    )
    source = sketch.Geometry[geometry_index]
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
        "geometry_index": geometry_index,
        "knot_index": knot_index,
        "knot_parameter": source.getKnots()[knot_index],
        "degree": source.Degree,
        "old_multiplicity": old,
        "new_multiplicity": old + 1,
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

    def diagnose(self, geometry_index, knot_index):
        calls.append((int(geometry_index), int(knot_index)))
        result = _diagnostic(self, geometry_index, knot_index)
        mutation = state["mutate_diagnostic"]
        if mutation is not None:
            mutation(result, len(calls))
        return result

    def commit(self, geometry_index, knot_index):
        commits.append((int(geometry_index), int(knot_index)))
        simulated, _old, _exposed, _deleted, receipt = _simulate(
            self, geometry_index, knot_index
        )
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

    sketch.diagnoseIncreaseBSplineKnotMultiplicity = MethodType(diagnose, sketch)
    sketch.increaseBSplineKnotMultiplicityExact = MethodType(commit, sketch)
    return calls, commits, state


def _host(monkeypatch, *, exposed=False):
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch._persistent_geometry_tags = True
    spline = MultiplicityBSpline()
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


def test_target_is_closed_and_zero_based(monkeypatch) -> None:
    document, _sketch, _context, values, *_rest = _host(monkeypatch)
    spec = prepare_bspline_knot_multiplicity_increase(document.Uid, values)
    assert (spec.geometry_index, spec.knot_index) == (0, 1)
    for arguments in (
        {**values, "unexpected": True},
        {**values, "geometry_index": [0]},
        {**values, "geometry_index": True},
        {**values, "geometry_index": -1},
        {**values, "knot_index": True},
        {**values, "knot_index": -1},
    ):
        with pytest.raises(NativeSketchError):
            prepare_bspline_knot_multiplicity_increase(document.Uid, arguments)


@pytest.mark.parametrize("exposed", (False, True))
def test_diagnosis_is_pure_and_exact_commit_is_verified(monkeypatch, exposed) -> None:
    document, sketch, context, values, calls, commits, _state = _host(
        monkeypatch, exposed=exposed
    )
    before_counts = (sketch.GeometryCount, sketch.ConstraintCount)
    prepared = preflight_bspline_knot_multiplicity_increase(
        context, prepare_bspline_knot_multiplicity_increase(document.Uid, values)
    )
    assert (sketch.GeometryCount, sketch.ConstraintCount) == before_counts
    draft = create_bspline_knot_multiplicity_increase(document, prepared)
    result = verify_bspline_knot_multiplicity_increase(document, draft)
    assert isinstance(draft, NativeMutationDraft)
    assert len(calls) == 2 and commits == [(0, 1)]
    assert sketch.Geometry[0].getMultiplicities() == [4, 2, 4]
    assert sketch.GeometryFacadeList[0].Tag == "spline-root"
    assert sketch.GeometryFacadeList[0].Construction is True
    assert sketch.GeometryFacadeList[0].GeometryLayerId == 7
    assert sketch.ExpressionEngine == [("Constraints[0]", "Spreadsheet.Length")]
    assert result["operation"] == "increase_bspline_knot_multiplicity"
    assert (result["old_multiplicity"], result["new_multiplicity"]) == (1, 2)
    assert result["measured_deviation_mm"] == 0.0


@pytest.mark.parametrize("geometry,knot", ((FakeLine(), 0), (MultiplicityBSpline(), 0)))
def test_rejects_non_spline_and_maximum_multiplicity(
    monkeypatch, geometry, knot
) -> None:
    document, sketch, context, values, calls, commits, _state = _host(monkeypatch)
    sketch.Geometry[0] = geometry
    sketch.GeometryFacadeList[0].Geometry = geometry
    with pytest.raises(NativeSketchError):
        preflight_bspline_knot_multiplicity_increase(
            context,
            prepare_bspline_knot_multiplicity_increase(
                document.Uid, {**values, "knot_index": knot}
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
        preflight_bspline_knot_multiplicity_increase(
            context,
            prepare_bspline_knot_multiplicity_increase(document.Uid, _values(sketch)),
        )
    assert calls == [] and commits == []


@pytest.mark.parametrize(
    ("change", "message"),
    (
        (lambda result, _count: result.update({"unexpected": True}), "incomplete"),
        (
            lambda result, _count: result.update({"new_multiplicity": 3}),
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
        preflight_bspline_knot_multiplicity_increase(
            context, prepare_bspline_knot_multiplicity_increase(document.Uid, values)
        )
    assert commits == []


def test_refuses_impure_and_second_diagnostic_drift(monkeypatch) -> None:
    document, sketch, context, values, _calls, commits, state = _host(monkeypatch)

    def impure(_result, count):
        if count == 1:
            sketch.GeometryFacadeList[0].Blocked = True

    state["mutate_diagnostic"] = impure
    with pytest.raises(NativeSketchError, match="changed the live Sketch"):
        preflight_bspline_knot_multiplicity_increase(
            context, prepare_bspline_knot_multiplicity_increase(document.Uid, values)
        )
    assert commits == []

    document, _sketch, context, values, _calls, commits, state = _host(monkeypatch)

    def drift(result, count):
        if count == 2:
            result["new_multiplicity"] = 3

    state["mutate_diagnostic"] = drift
    prepared = preflight_bspline_knot_multiplicity_increase(
        context, prepare_bspline_knot_multiplicity_increase(document.Uid, values)
    )
    with pytest.raises(NativeSketchError, match="representation"):
        create_bspline_knot_multiplicity_increase(document, prepared)
    assert commits == []


def test_rejects_shape_drift_and_final_representation_drift(monkeypatch) -> None:
    document, _sketch, context, values, _calls, commits, state = _host(monkeypatch)

    def shape_drift(result, _count):
        result["geometry"][0]._shape_shift = 0.1
        result["geometry"][0]._refresh()

    state["mutate_diagnostic"] = shape_drift
    with pytest.raises(NativeSketchError, match="move the curve"):
        preflight_bspline_knot_multiplicity_increase(
            context, prepare_bspline_knot_multiplicity_increase(document.Uid, values)
        )
    assert commits == []

    document, sketch, context, values, *_rest = _host(monkeypatch)
    prepared = preflight_bspline_knot_multiplicity_increase(
        context, prepare_bspline_knot_multiplicity_increase(document.Uid, values)
    )
    draft = create_bspline_knot_multiplicity_increase(document, prepared)
    sketch.Geometry[0]._shape_shift = 0.1
    sketch.Geometry[0]._refresh()
    with pytest.raises(NativeSketchError, match="final geometry|representation"):
        verify_bspline_knot_multiplicity_increase(document, draft)


def test_runtime_routes_one_exact_transaction(monkeypatch) -> None:
    document, _sketch, context, values, calls, commits, _state = _host(monkeypatch)
    captured = {}

    def run_immediate(runtime_context, **kwargs):
        assert runtime_context is context
        captured.update(kwargs)
        return kwargs["verify"](document, kwargs["mutate"](document))

    monkeypatch.setattr(runtime_module, "run_immediate_mutation", run_immediate)
    result = NativeSketchGeometryRuntime(context).mutate_geometry(
        {"operation": "increase_bspline_knot_multiplicity", **values}, ticket=None
    )
    assert len(calls) == 2 and commits == [(0, 1)]
    assert captured["transaction_name"] == "Increase Sketch B-Spline Knot Multiplicity"
    assert result["operation"] == "increase_bspline_knot_multiplicity"
