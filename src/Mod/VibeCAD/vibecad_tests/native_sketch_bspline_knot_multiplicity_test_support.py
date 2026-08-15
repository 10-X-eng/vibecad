# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared fake host for Native B-spline knot-mutation tests."""

from __future__ import annotations

import copy
from types import MethodType, SimpleNamespace

from vibecad_tests.native_sketch_test_support import (
    FakeBSpline,
    FakeConstraint,
    install_fake_sketch_host,
)


class MultiplicityBSpline(FakeBSpline):
    def __init__(self, *, middle_multiplicity: int = 1) -> None:
        self._shape_shift = 0.0
        poles = [
            SimpleNamespace(x=0.0, y=0.0, z=0.0),
            SimpleNamespace(x=2.0, y=3.0, z=0.0),
            SimpleNamespace(x=5.0, y=-2.0, z=0.0),
            SimpleNamespace(x=8.0, y=2.0, z=0.0),
            SimpleNamespace(x=10.0, y=0.0, z=0.0),
        ]
        if middle_multiplicity == 2:
            poles.insert(3, SimpleNamespace(x=6.5, y=-0.5, z=0.0))
        super().__init__(
            poles,
            [4, middle_multiplicity, 4],
            [0.0, 0.5, 1.0],
            False,
            3,
            [1.0] * len(poles),
        )

    def value(self, parameter: float):
        value = min(1.0, max(0.0, float(parameter)))
        return SimpleNamespace(
            x=10.0 * value,
            y=6.0 * value * (1.0 - value) * (1.0 - 2.0 * value) + self._shape_shift,
            z=0.0,
        )

    def changed(self, knot_index: int, increment: int):
        result = copy.deepcopy(self)
        new_multiplicity = result._multiplicities[knot_index] + increment
        if new_multiplicity == 0:
            del result._multiplicities[knot_index]
            del result._knots[knot_index]
        else:
            result._multiplicities[knot_index] = new_multiplicity
        if increment > 0:
            result._poles.insert(3, SimpleNamespace(x=6.5, y=-0.5, z=0.0))
            result._weights.insert(3, 1.0)
        else:
            del result._poles[3]
            del result._weights[3]
        result._refresh()
        return result

    def increased(self, knot_index: int):
        return self.changed(knot_index, 1)

    def inserted(self, parameter: float):
        result = copy.deepcopy(self)
        matches = [
            index
            for index, value in enumerate(result._knots)
            if abs(value - parameter) <= 1.0e-7
        ]
        if matches:
            knot_index = matches[0]
            old_multiplicity = result._multiplicities[knot_index]
            result._multiplicities[knot_index] += 1
        else:
            knot_index = next(
                (
                    index
                    for index, value in enumerate(result._knots)
                    if value > parameter
                ),
                len(result._knots),
            )
            old_multiplicity = 0
            result._knots.insert(knot_index, parameter)
            result._multiplicities.insert(knot_index, 1)
        result._poles.insert(3, SimpleNamespace(x=6.0, y=-0.25, z=0.0))
        result._weights.insert(3, 1.0)
        result._refresh()
        return result, knot_index, old_multiplicity


def multiplicity_values(sketch, *, increment: int, **updates) -> dict[str, object]:
    result = {
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": sketch.GeometryCount,
        "expected_constraint_count": sketch.ConstraintCount,
        "expected_external_reference_count": 0,
        "expected_external_geometry_count": 0,
        "geometry_index": 0,
        "knot_index": 1,
    }
    if increment < 0:
        result["maximum_deviation_mm"] = 1.0
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


def _simulate(sketch, geometry_index, knot_index, increment):
    simulated = copy.deepcopy(sketch)
    before_geometry = [value.Tag for value in simulated.GeometryFacadeList]
    before_constraints = [value.Tag for value in simulated.Constraints]
    source = simulated.Geometry[geometry_index]
    old_multiplicity = source.getMultiplicities()[knot_index]
    old_helpers = set(range(geometry_index + 1, simulated.GeometryCount))
    simulated.Geometry[geometry_index] = source.changed(knot_index, increment)
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


def _diagnostic(sketch, geometry_index, knot_index, increment):
    simulated, old, exposed, deleted, receipt = _simulate(
        sketch, geometry_index, knot_index, increment
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
        "new_multiplicity": old + increment,
        "retained_internal_geometry_count": 0,
        "deleted_internal_geometry_count": deleted,
        "exposed_internal_geometry_count": exposed,
        "geometry_tags": [value.Tag for value in simulated.GeometryFacadeList],
        "constraint_tags": [value.Tag for value in simulated.Constraints],
        "expressions": [],
        "mutation_receipt": receipt,
    }


def _install_host(sketch, increment):
    calls = []
    commits = []
    state = {"mutate_diagnostic": None}

    def diagnose(self, geometry_index, knot_index):
        calls.append((int(geometry_index), int(knot_index)))
        result = _diagnostic(self, geometry_index, knot_index, increment)
        mutation = state["mutate_diagnostic"]
        if mutation is not None:
            mutation(result, len(calls))
        return result

    def commit(self, geometry_index, knot_index):
        commits.append((int(geometry_index), int(knot_index)))
        simulated, _old, _exposed, _deleted, receipt = _simulate(
            self, geometry_index, knot_index, increment
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

    direction = "Increase" if increment > 0 else "Decrease"
    setattr(
        sketch,
        f"diagnose{direction}BSplineKnotMultiplicity",
        MethodType(diagnose, sketch),
    )
    setattr(
        sketch,
        f"{direction.lower()}BSplineKnotMultiplicityExact",
        MethodType(commit, sketch),
    )
    return calls, commits, state


def install_multiplicity_host(
    monkeypatch,
    *,
    increment: int,
    exposed: bool = False,
    middle_multiplicity: int = 1,
):
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch._persistent_geometry_tags = True
    spline = MultiplicityBSpline(middle_multiplicity=middle_multiplicity)
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
    calls, commits, state = _install_host(sketch, increment)
    return (
        document,
        sketch,
        context,
        multiplicity_values(sketch, increment=increment),
        calls,
        commits,
        state,
    )


def insertion_values(sketch, **updates) -> dict[str, object]:
    result = {
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": sketch.GeometryCount,
        "expected_constraint_count": sketch.ConstraintCount,
        "expected_external_reference_count": 0,
        "expected_external_geometry_count": 0,
        "geometry_index": 0,
        "parameter": 0.25,
    }
    result.update(updates)
    return result


def _simulate_insertion(sketch, geometry_index, parameter):
    simulated = copy.deepcopy(sketch)
    before_geometry = [value.Tag for value in simulated.GeometryFacadeList]
    before_constraints = [value.Tag for value in simulated.Constraints]
    source = simulated.Geometry[geometry_index]
    inserted, knot_index, old_multiplicity = source.inserted(parameter)
    old_helpers = set(range(geometry_index + 1, simulated.GeometryCount))
    simulated.Geometry[geometry_index] = inserted
    simulated.GeometryFacadeList[geometry_index].Geometry = inserted
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
            facade.Tag = f"insertion-geometry-{index}"
    for index, constraint in enumerate(simulated.Constraints):
        if not getattr(constraint, "Tag", ""):
            constraint.Tag = f"insertion-constraint-{index}"
    receipt = {
        "geometry": _collection_receipt(
            before_geometry, [value.Tag for value in simulated.GeometryFacadeList]
        ),
        "constraints": _collection_receipt(
            before_constraints, [value.Tag for value in simulated.Constraints]
        ),
    }
    return simulated, knot_index, old_multiplicity, exposed, len(old_helpers), receipt


def _insertion_diagnostic(sketch, geometry_index, parameter):
    simulated, knot_index, old, exposed, deleted, receipt = _simulate_insertion(
        sketch, geometry_index, parameter
    )
    inserted = simulated.Geometry[geometry_index]
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
        "requested_parameter": parameter,
        "knot_index": knot_index,
        "knot_parameter": inserted.getKnots()[knot_index],
        "degree": inserted.Degree,
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


def install_insertion_host(monkeypatch, *, exposed: bool = False):
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
    calls = []
    commits = []
    state = {"mutate_diagnostic": None}

    def diagnose(self, geometry_index, parameter):
        calls.append((int(geometry_index), float(parameter)))
        result = _insertion_diagnostic(self, geometry_index, parameter)
        mutation = state["mutate_diagnostic"]
        if mutation is not None:
            mutation(result, len(calls))
        return result

    def commit(self, geometry_index, parameter):
        commits.append((int(geometry_index), float(parameter)))
        simulated, _index, _old, _exposed, _deleted, receipt = _simulate_insertion(
            self, geometry_index, parameter
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

    sketch.diagnoseInsertBSplineKnot = MethodType(diagnose, sketch)
    sketch.insertBSplineKnotExact = MethodType(commit, sketch)
    return (
        document,
        sketch,
        context,
        insertion_values(sketch),
        calls,
        commits,
        state,
    )
