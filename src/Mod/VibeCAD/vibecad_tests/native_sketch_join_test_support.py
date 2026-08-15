# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused fake host for exact Native Sketch Join Curves tests."""

from __future__ import annotations

import copy
from types import MethodType, SimpleNamespace

from vibecad_tests.native_sketch_test_support import (
    FakeBSpline,
    FakeCircle,
    FakeConstraint,
    FakeLine,
    fake_facade,
    install_fake_sketch_host,
)


def point(x: float, y: float) -> SimpleNamespace:
    return SimpleNamespace(x=float(x), y=float(y), z=0.0)


def join_values(sketch) -> dict[str, object]:
    return {
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": sketch.GeometryCount,
        "expected_constraint_count": sketch.ConstraintCount,
        "expected_external_reference_count": 0,
        "expected_external_geometry_count": 0,
        "first": {"geometry_index": 0, "endpoint": "end"},
        "second": {"geometry_index": 1, "endpoint": "start"},
    }


def _tagged(value, tag: str):
    result = copy.deepcopy(value)
    result.Tag = tag
    return result


def _metadata(
    geometry_id: int,
    *,
    internal_type: str = "None",
) -> dict[str, object]:
    return {
        "Id": geometry_id,
        "Construction": internal_type != "None",
        "Blocked": False,
        "InternalType": internal_type,
        "GeometryLayerId": 0,
    }


def _joined_geometry(*, tangent: bool, prefix: str):
    degree = 2 if tangent else 1
    knots = [0.0, 1.0] if tangent else [0.0, 1.0, 2.0]
    multiplicities = [3, 3] if tangent else [2, 1, 2]
    root = FakeBSpline(
        [point(0, 0), point(10, 0), point(20, 0 if tangent else 5)],
        multiplicities,
        knots,
        False,
        degree,
        [1.0, 1.0, 1.0],
    )
    root.Tag = f"{prefix}-geometry-root"
    helper_types = ["BSplineControlPoint"] * root.NbPoles + [
        "BSplineKnotPoint"
    ] * root.NbKnots
    helpers = []
    for index, internal_type in enumerate(helper_types):
        if internal_type == "BSplineControlPoint":
            item = point(index, index)
            item = SimpleNamespace(
                TypeId="Part::GeomPoint",
                X=item.x,
                Y=item.y,
                Z=item.z,
            )
        else:
            item = FakeCircle(point(index, index), point(0, 0), 1.0)
        item.Tag = f"{prefix}-geometry-helper-{index}"
        helpers.append((item, internal_type))
    return root, helpers


def final_state(sketch, *, prefix: str, tangent: bool):
    root, helpers = _joined_geometry(tangent=tangent, prefix=prefix)
    retained = _tagged(sketch.Geometry[2], sketch.GeometryFacadeList[2].Tag)
    geometry = [root, retained, *(item for item, _kind in helpers)]
    metadata = [_metadata(200), _metadata(102)] + [
        _metadata(201 + index, internal_type=internal_type)
        for index, (_item, internal_type) in enumerate(helpers)
    ]
    retained_constraint = copy.deepcopy(sketch.Constraints[1])
    retained_constraint.First = 1
    retained_constraint.Tag = sketch.Constraints[1].Tag
    constraints = [retained_constraint]
    role_counts = {"BSplineControlPoint": 0, "BSplineKnotPoint": 0}
    for index, (_item, internal_type) in enumerate(helpers, start=2):
        role = role_counts[internal_type]
        role_counts[internal_type] += 1
        constraint = FakeConstraint(
            f"InternalAlignment::{internal_type}", index, 3, 0, role
        )
        constraint.Tag = f"{prefix}-constraint-{internal_type}-{role}"
        constraints.append(constraint)
    receipt = {
        "geometry": {
            "identity": "native_tag",
            "old_to_new": {"2": 1},
            "deleted": [
                {"index": index, "tag": sketch.GeometryFacadeList[index].Tag}
                for index in (0, 1)
            ],
            "created": [
                {"index": 0, "tag": root.Tag},
                *[
                    {"index": index, "tag": item.Tag}
                    for index, (item, _kind) in enumerate(helpers, start=2)
                ],
            ],
        },
        "constraints": {
            "identity": "native_tag",
            "old_to_new": {"1": 0},
            "deleted": [{"index": 0, "tag": sketch.Constraints[0].Tag}],
            "created": [
                {"index": index, "tag": constraint.Tag}
                for index, constraint in enumerate(constraints[1:], start=1)
            ],
        },
    }
    return geometry, metadata, constraints, receipt


def diagnostic(sketch, *, tangent: bool, prefix: str = "diagnostic"):
    geometry, metadata, constraints, receipt = final_state(
        sketch, prefix=prefix, tangent=tangent
    )
    return {
        "accepted": True,
        "degrees_of_freedom": 5,
        "solver_status": 0,
        "conflicting_constraint_indices": [],
        "redundant_constraint_indices": [],
        "partially_redundant_constraint_indices": [],
        "malformed_constraint_indices": [],
        "first_geometry_index": 0,
        "first_endpoint": 2,
        "second_geometry_index": 1,
        "second_endpoint": 1,
        "continuity": 1 if tangent else 0,
        "external_geometry_count": 0,
        "mutation_receipt": receipt,
        "geometry_count": len(geometry),
        "constraint_count": len(constraints),
        "geometry": geometry,
        "geometry_metadata": metadata,
        "constraints": constraints,
    }


def _apply_state(sketch, geometry, metadata, constraints) -> None:
    sketch.Geometry = geometry
    sketch.GeometryFacadeList = []
    for index, (item, details) in enumerate(zip(geometry, metadata, strict=True)):
        facade = fake_facade(
            item,
            index,
            construction=bool(details["Construction"]),
            internal_type=str(details["InternalType"]),
        )
        facade.Id = int(details["Id"])
        facade.Tag = str(item.Tag)
        sketch.GeometryFacadeList.append(facade)
    sketch.Constraints = constraints
    sketch.GeometryCount = len(geometry)
    sketch.ConstraintCount = len(constraints)
    sketch.ExpressionEngine = [("Constraints[0]", "12 mm")]
    sketch.DoF = 5


def install_join_host(monkeypatch, *, tangent: bool = False):
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.Geometry[0] = FakeLine(point(0, 0), point(10, 0))
    sketch.GeometryFacadeList[0].Geometry = sketch.Geometry[0]
    sketch.GeometryFacadeList[0].Tag = "source-first"
    second = FakeLine(point(10, 0), point(20, 0 if tangent else 5))
    unrelated = FakeLine(point(0, 10), point(12, 10))
    sketch.addGeometry(second, False)
    sketch.addGeometry(unrelated, False)
    sketch.GeometryFacadeList[1].Tag = "source-second"
    sketch.GeometryFacadeList[2].Tag = "unrelated-geometry"
    join_constraint = FakeConstraint("Tangent" if tangent else "Coincident", 0, 2, 1, 1)
    join_constraint.Tag = "source-join-constraint"
    unrelated_constraint = FakeConstraint("Distance", 2, 12.0)
    unrelated_constraint.Tag = "unrelated-constraint"
    unrelated_constraint.Name = "PreservedLength"
    sketch.Constraints = [join_constraint, unrelated_constraint]
    sketch.ConstraintCount = 2
    sketch.ExpressionEngine = [("Constraints[1]", "12 mm")]
    sketch.DoF = 5
    calls = []
    commits = []
    state = {"mutate_diagnostic": None, "mutate_commit": None}

    def diagnose(self, first, first_endpoint, second_index, second_endpoint):
        calls.append((first, first_endpoint, second_index, second_endpoint))
        result = diagnostic(self, tangent=tangent)
        mutate = state["mutate_diagnostic"]
        if callable(mutate):
            mutate(result, len(calls))
        return result

    def commit(self, first, first_endpoint, second_index, second_endpoint):
        commits.append((first, first_endpoint, second_index, second_endpoint))
        geometry, metadata, constraints, receipt = final_state(
            self, prefix="actual", tangent=tangent
        )
        _apply_state(self, geometry, metadata, constraints)
        mutate = state["mutate_commit"]
        if callable(mutate):
            mutate(self, receipt)
        return receipt

    sketch.diagnoseJoinCurves = MethodType(diagnose, sketch)
    sketch.joinCurvesExact = MethodType(commit, sketch)
    return document, sketch, context, join_values(sketch), calls, commits, state
