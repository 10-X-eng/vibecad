# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact Block lifecycle for the rolling Native Sketch GUI gate."""

from __future__ import annotations

import json
import os
from typing import Any, Callable

import FreeCAD as App
import FreeCADGui as Gui
import Part
import Sketcher

from VibeCADNativeSketchState import (
    serialize_sketch_constraint,
    serialize_sketch_geometry,
)


_LINE = 0
_CIRCLE = 1
_CIRCULAR_ARC = 2
_ELLIPSE = 3
_ELLIPTICAL_ARC = 4
_HYPERBOLIC_ARC = 5
_PARABOLIC_ARC = 6
_BSPLINE = 7
_POINT = 8
_NORMAL_TARGETS = tuple(range(8))
_INTERNAL_EDGE_TYPES = frozenset(
    {
        "EllipseMajorDiameter",
        "EllipseMinorDiameter",
        "HyperbolaMajor",
        "HyperbolaMinor",
        "ParabolaFocalAxis",
        "BSplineControlPoint",
    }
)


def _phase(name: str) -> None:
    os.write(2, f"VIBECAD_NATIVE_SKETCH_BLOCK_PHASE {name}\n".encode("ascii"))


def _element(index: int, position: str = "whole") -> dict[str, object]:
    return {"geometry_index": index, "position": position}


def _arguments(
    sketch: Any,
    selection: list[dict[str, object]],
    *,
    geometry_count: int | None = None,
    constraint_count: int | None = None,
) -> dict[str, object]:
    return {
        "operation": "constrain_block",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": (
            int(sketch.GeometryCount) if geometry_count is None else geometry_count
        ),
        "expected_constraint_count": (
            int(sketch.ConstraintCount)
            if constraint_count is None
            else constraint_count
        ),
        "expected_external_geometry_count": 1,
        "selection": selection,
    }


def _selection_state(document: Any) -> tuple:
    return tuple(
        (
            str(item.ObjectName),
            tuple(str(value) for value in item.SubElementNames),
        )
        for item in Gui.Selection.getSelectionEx(document.Name)
    )


def _add(sketch: Any, geometry: Any, expected: int) -> None:
    assert int(sketch.addGeometry(geometry, False)) == expected


def _prepare_fixtures(document: Any, sketch: Any) -> None:
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (0, 0)
    document.openTransaction("Prepare Native Sketch Block fixtures")
    try:
        _add(
            sketch,
            Part.LineSegment(App.Vector(400, 0), App.Vector(412, 0)),
            _LINE,
        )
        _add(
            sketch,
            Part.Circle(App.Vector(430, 0), App.Vector(0, 0, 1), 5),
            _CIRCLE,
        )
        _add(
            sketch,
            Part.ArcOfCircle(
                Part.Circle(App.Vector(455, 0), App.Vector(0, 0, 1), 6),
                0.2,
                2.0,
            ),
            _CIRCULAR_ARC,
        )
        _add(sketch, Part.Ellipse(App.Vector(485, 0), 8, 3), _ELLIPSE)
        _add(
            sketch,
            Part.ArcOfEllipse(Part.Ellipse(App.Vector(515, 0), 7, 3), 0.2, 2.0),
            _ELLIPTICAL_ARC,
        )
        _add(
            sketch,
            Part.ArcOfHyperbola(
                Part.Hyperbola(App.Vector(545, 0), 6, 2),
                -0.8,
                0.9,
            ),
            _HYPERBOLIC_ARC,
        )
        _add(
            sketch,
            Part.ArcOfParabola(
                Part.Parabola(
                    App.Vector(578, 0),
                    App.Vector(575, 0),
                    App.Vector(0, 0, 1),
                ),
                -3,
                4,
            ),
            _PARABOLIC_ARC,
        )
        _add(
            sketch,
            Part.BSplineCurve(
                [App.Vector(605, -3), App.Vector(612, 5), App.Vector(620, -2)],
                [3, 3],
                [0.0, 1.0],
                False,
                2,
                [1.0, 2.0, 1.5],
                False,
            ),
            _BSPLINE,
        )
        _add(sketch, Part.Point(App.Vector(640, 0)), _POINT)
        for parent in (_ELLIPSE, _HYPERBOLIC_ARC, _PARABOLIC_ARC, _BSPLINE):
            geometry_count = int(sketch.GeometryCount)
            mutation = sketch.exposeInternalGeometry(parent)
            assert isinstance(mutation, dict)
            assert int(sketch.GeometryCount) > geometry_count
        assert int(sketch.addConstraint(Sketcher.Constraint("Horizontal", _LINE))) >= 0
        source = document.getObject("ExternalSource")
        assert source is not None
        sketch.addExternal(source.Name, "Edge1")
        document.recompute()
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise


def _internal_edge_indices(sketch: Any) -> tuple[int, ...]:
    return tuple(
        index
        for index in range(int(sketch.GeometryCount))
        if str(sketch.GeometryFacadeList[index].InternalType)
        in _INTERNAL_EDGE_TYPES
    )


def _geometry_records(sketch: Any) -> tuple[dict[str, Any], ...]:
    return tuple(
        serialize_sketch_geometry(sketch, index)
        for index in range(int(sketch.GeometryCount))
    )


def _expected_blocked_records(
    records: tuple[dict[str, Any], ...],
    targets: tuple[int, ...],
) -> tuple[dict[str, Any], ...]:
    selected = set(targets)
    return tuple(
        {**record, "blocked": True} if int(record["index"]) in selected else record
        for record in records
    )


def exercise_block_case(
    *,
    sketch: Any,
    document: Any,
    native_call: Callable[..., dict],
    process_events: Callable[[int], None],
    edit_boundary: Callable[..., tuple],
    boundary: tuple,
    controller: Any,
) -> dict[str, Any]:
    _prepare_fixtures(document, sketch)
    process_events(24)
    internal_targets = _internal_edge_indices(sketch)
    assert len(internal_targets) == 8, internal_targets
    targets = (*_NORMAL_TARGETS, *internal_targets)
    assert len(targets) == 16 and len(set(targets)) == 16
    initial_constraint_count = int(sketch.ConstraintCount)
    before = _geometry_records(sketch)
    assert not any(record["blocked"] for record in before)
    _phase("fixtures")

    diagnostic_constraints = [
        Sketcher.Constraint("Block", index) for index in targets
    ]
    diagnostic = sketch.diagnoseBlockConstraints(diagnostic_constraints)
    assert diagnostic["accepted"] is True, diagnostic
    assert diagnostic["first_proposed_constraint_index"] == initial_constraint_count
    assert diagnostic["proposed_constraint_count"] == len(targets)
    assert _geometry_records(sketch) == before
    assert int(sketch.ConstraintCount) == initial_constraint_count
    _phase("copied_diagnostic")

    undo_before_failures = int(document.UndoCount)
    invalid_calls = (
        _arguments(sketch, [_element(_POINT)]),
        _arguments(sketch, [_element(-1)]),
        _arguments(sketch, [_element(-3)]),
        _arguments(
            sketch,
            [_element(_LINE)],
            geometry_count=int(sketch.GeometryCount) - 1,
        ),
    )
    for arguments in invalid_calls:
        assert native_call(arguments, succeeds=False)["error_code"] == (
            "NATIVE_SKETCH_INVALID"
        )
    wrong_position = _arguments(sketch, [_element(_LINE, "start")])
    assert native_call(wrong_position, succeeds=False)["error_code"] == (
        "NATIVE_ARGUMENTS_INVALID"
    )
    assert int(document.UndoCount) == undo_before_failures
    assert _geometry_records(sketch) == before
    _phase("refusals")

    Gui.Selection.clearSelection(document.Name)
    Gui.Selection.addSelection(document.Name, sketch.Name, "Edge2")
    process_events(8)
    selection = _selection_state(document)
    result = native_call(_arguments(sketch, [_element(index) for index in targets]))
    assert result["operation"] == "constrain_block"
    assert len(result["constraints"]) == len(targets)
    assert [
        item["references"][0]["geometry_index"] for item in result["constraints"]
    ] == list(targets)
    assert [item["index"] for item in result["constraints"]] == list(
        range(initial_constraint_count, initial_constraint_count + len(targets))
    )
    assert {item["index"] for item in result["frozen_geometry"]} == set(targets)
    assert all(item["blocked"] is True for item in result["frozen_geometry"])
    expected_after = _expected_blocked_records(before, targets)
    assert _geometry_records(sketch) == expected_after
    assert _selection_state(document) == selection
    assert document.UndoNames[0] == "Create Native Sketch Block"
    _phase("block")

    document.undo()
    process_events(24)
    assert int(sketch.ConstraintCount) == initial_constraint_count
    assert _geometry_records(sketch) == before
    document.redo()
    process_events(24)
    assert int(sketch.ConstraintCount) == initial_constraint_count + len(targets)
    assert _geometry_records(sketch) == expected_after
    assert _selection_state(document) == selection
    _phase("undo_redo")

    undo_before_duplicate = int(document.UndoCount)
    duplicate = native_call(
        _arguments(sketch, [_element(targets[0])]),
        succeeds=False,
    )
    assert duplicate["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == undo_before_duplicate
    assert _geometry_records(sketch) == expected_after
    assert edit_boundary(document, sketch, controller) == boundary
    _phase("duplicate")

    return {
        "geometry_count": int(sketch.GeometryCount),
        "constraint_count": int(sketch.ConstraintCount),
        "initial_constraint_count": initial_constraint_count,
        "targets": list(targets),
        "geometries": [json.dumps(item, sort_keys=True) for item in expected_after],
        "constraints": [
            serialize_sketch_constraint(sketch, index)
            for index in range(int(sketch.ConstraintCount))
        ],
    }


def verify_reopened_block(sketch: Any, expected: dict[str, Any]) -> None:
    assert int(sketch.GeometryCount) == expected["geometry_count"]
    assert int(sketch.ConstraintCount) == expected["constraint_count"]
    expected_geometry = tuple(json.loads(item) for item in expected["geometries"])
    for index, record in enumerate(expected_geometry):
        observed = serialize_sketch_geometry(sketch, index)
        for key, value in record.items():
            if key != "tag":
                assert observed[key] == value, (index, key, value, observed[key])
        assert observed["tag"]
    for index, record in enumerate(expected["constraints"]):
        assert serialize_sketch_constraint(sketch, index) == record
    for index in expected["targets"]:
        assert bool(sketch.GeometryFacadeList[index].Blocked)
    for index in range(
        expected["initial_constraint_count"],
        expected["constraint_count"],
    ):
        assert str(sketch.Constraints[index].Type) == "Block"
    assert not tuple(sketch.ConflictingConstraints)
    assert not tuple(sketch.RedundantConstraints)
    assert not tuple(sketch.MalformedConstraints)
