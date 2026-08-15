# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused fake-host fixture for Native Sketch relationship reads."""

from __future__ import annotations

from types import SimpleNamespace

from vibecad_tests.native_sketch_test_support import (
    FakeConstraint,
    FakeExternalLine,
    FakeLine,
    FakePoint,
    fake_facade,
    install_fake_sketch_host,
)


def _tag(constraint, tag: str, name: str = ""):
    constraint.Tag = tag
    constraint.Name = name
    return constraint


def install_inspect_host(monkeypatch):
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.Geometry = [
        FakeLine(),
        FakeLine(
            SimpleNamespace(x=5.0, y=0.0, z=0.0),
            SimpleNamespace(x=10.0, y=4.0, z=0.0),
        ),
        FakeLine(
            SimpleNamespace(x=0.0, y=8.0, z=0.0),
            SimpleNamespace(x=0.0, y=14.0, z=0.0),
        ),
        FakePoint(SimpleNamespace(x=2.0, y=2.0, z=0.0)),
    ]
    sketch.GeometryFacadeList = [
        fake_facade(sketch.Geometry[index], index) for index in range(3)
    ] + [
        fake_facade(
            sketch.Geometry[3],
            3,
            construction=True,
            internal_type="BSplineControlPoint",
        )
    ]
    for index, facade in enumerate(sketch.GeometryFacadeList):
        facade.Tag = f"inspect-geometry-{index}"
    sketch.GeometryCount = 4
    sketch.ExternalGeo.append(FakeExternalLine("ExternalSource.Edge1"))
    sketch.Constraints = [
        _tag(FakeConstraint("Horizontal", 0), "inspect-constraint-horizontal"),
        _tag(
            FakeConstraint("Coincident", 0, 2, 1, 1),
            "inspect-constraint-coincident",
            "JoinedEndpoint",
        ),
        _tag(FakeConstraint("Distance", 1, 7.5), "inspect-constraint-distance"),
        _tag(FakeConstraint("Vertical", 2), "inspect-constraint-vertical"),
        _tag(
            FakeConstraint("PointOnObject", 0, 1, -3, 0),
            "inspect-constraint-external",
        ),
        _tag(
            FakeConstraint("Group", [3, 0, 0, 0, 1, 0, 2, 0]),
            "inspect-constraint-group",
        ),
        _tag(
            FakeConstraint("InternalAlignment", 3, 1, 1, 0),
            "inspect-constraint-internal",
        ),
        _tag(
            FakeConstraint("DistanceX", 0, 1, -1, 1, 5.0),
            "inspect-constraint-root",
        ),
    ]
    sketch.ConstraintCount = len(sketch.Constraints)
    values = {
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": sketch.GeometryCount,
        "expected_constraint_count": sketch.ConstraintCount,
        "expected_external_geometry_count": 1,
        "selection": [{"geometry_index": 0, "position": "whole"}],
    }
    return document, sketch, context, values
