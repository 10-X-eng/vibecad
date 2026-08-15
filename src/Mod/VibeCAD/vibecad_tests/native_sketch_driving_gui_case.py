# SPDX-License-Identifier: LGPL-2.1-or-later

"""Driving/Reference lifecycle case for focused and rolling Sketch GUI gates."""

from __future__ import annotations

import copy
import json
import math
import os
from typing import Any, Callable

import FreeCAD as App
import FreeCADGui as Gui
import Part
import Sketcher

from vibecad_tests.native_sketch_constraint_toggle_gui_support import (
    expression_records as _expression_records,
    selection_state as _selection_state,
    sketch_records as _records,
    solver_issues as _solver_issues,
)
from VibeCADNativeSketchDrivingState import sketch_geometry_metadata


_LENGTH_LINE = 0
_RADIUS_CIRCLE = 1
_POINT = 2
_DIAMETER_CIRCLE = 3
_ANGLE_LINE = 4
_WEIGHT_CIRCLE = 5
_FIRST_RAY = 6
_SECOND_RAY = 7
_INTERFACE = 8
_SPARE_LINE = 9
_DIMENSIONAL_TYPES = (
    "Distance",
    "Radius",
    "DistanceX",
    "DistanceY",
    "Diameter",
    "Angle",
    "Weight",
    "SnellsLaw",
)


def _phase(name: str) -> None:
    os.write(2, f"VIBECAD_NATIVE_SKETCH_DRIVING_PHASE {name}\n".encode("ascii"))


def _arguments(
    sketch: Any,
    targets: list[tuple[int, bool]],
    *,
    geometry_count: int | None = None,
    constraint_count: int | None = None,
    external_count: int = 1,
) -> dict[str, object]:
    return {
        "operation": "toggle_driving_reference",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": (
            int(sketch.GeometryCount) if geometry_count is None else geometry_count
        ),
        "expected_constraint_count": (
            int(sketch.ConstraintCount)
            if constraint_count is None
            else constraint_count
        ),
        "expected_external_geometry_count": external_count,
        "targets": [
            {
                "constraint_index": index,
                "expected_driving": expected,
            }
            for index, expected in targets
        ],
    }


def _add_geometry(
    sketch: Any, geometry: Any, expected: int, construction=False
) -> None:
    assert int(sketch.addGeometry(geometry, construction)) == expected


def _add_constraint(sketch: Any, constraint: Any, expected: int) -> None:
    assert int(sketch.addConstraint(constraint)) == expected


def _reference(constraint_type: str, *arguments: Any) -> Any:
    return Sketcher.Constraint(constraint_type, *arguments, True, False)


def _prepare_fixtures(document: Any, sketch: Any) -> dict[str, int]:
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (0, 0)
    document.openTransaction("Prepare Native Sketch Driving fixtures")
    try:
        _add_geometry(
            sketch,
            Part.LineSegment(App.Vector(0, 0), App.Vector(10, 0)),
            _LENGTH_LINE,
        )
        _add_geometry(
            sketch,
            Part.Circle(App.Vector(25, 0), App.Vector(0, 0, 1), 5),
            _RADIUS_CIRCLE,
        )
        _add_geometry(sketch, Part.Point(App.Vector(40, 8)), _POINT)
        _add_geometry(
            sketch,
            Part.Circle(App.Vector(55, 0), App.Vector(0, 0, 1), 4),
            _DIAMETER_CIRCLE,
        )
        _add_geometry(
            sketch,
            Part.LineSegment(App.Vector(70, 0), App.Vector(78, 8)),
            _ANGLE_LINE,
        )
        _add_geometry(
            sketch,
            Part.Circle(App.Vector(90, 0), App.Vector(0, 0, 1), 2),
            _WEIGHT_CIRCLE,
            True,
        )
        _add_geometry(
            sketch,
            Part.LineSegment(App.Vector(100, -10), App.Vector(110, 0)),
            _FIRST_RAY,
        )
        _add_geometry(
            sketch,
            Part.LineSegment(App.Vector(110, 0), App.Vector(120, -10)),
            _SECOND_RAY,
        )
        _add_geometry(
            sketch,
            Part.LineSegment(App.Vector(110, -15), App.Vector(110, 15)),
            _INTERFACE,
            True,
        )
        _add_geometry(
            sketch,
            Part.LineSegment(App.Vector(135, 5), App.Vector(145, 5)),
            _SPARE_LINE,
        )

        _add_constraint(sketch, Sketcher.Constraint("Distance", _LENGTH_LINE, 10.0), 0)
        _add_constraint(
            sketch,
            _reference("Radius", _RADIUS_CIRCLE, 5.0),
            1,
        )
        _add_constraint(
            sketch,
            _reference("DistanceX", _POINT, 1, 40.0),
            2,
        )
        _add_constraint(
            sketch,
            _reference("DistanceY", _POINT, 1, 8.0),
            3,
        )
        _add_constraint(
            sketch,
            _reference("Diameter", _DIAMETER_CIRCLE, 8.0),
            4,
        )
        _add_constraint(
            sketch,
            _reference("Angle", _ANGLE_LINE, math.pi / 4.0),
            5,
        )
        _add_constraint(
            sketch,
            _reference("Weight", _WEIGHT_CIRCLE, 2.0),
            6,
        )
        _add_constraint(
            sketch,
            Sketcher.Constraint("Coincident", _FIRST_RAY, 2, _SECOND_RAY, 1),
            7,
        )
        _add_constraint(
            sketch,
            Sketcher.Constraint("PointOnObject", _FIRST_RAY, 2, _INTERFACE),
            8,
        )
        _add_constraint(
            sketch,
            _reference(
                "SnellsLaw",
                _FIRST_RAY,
                2,
                _SECOND_RAY,
                1,
                _INTERFACE,
                1.0,
            ),
            9,
        )
        _add_constraint(
            sketch,
            Sketcher.Constraint("Horizontal", _SPARE_LINE),
            10,
        )
        source = document.getObject("ExternalSource")
        assert source is not None
        sketch.addExternal(source.Name, "Edge1")
        assert len(sketch.ExternalGeometry) == 1
        _add_constraint(
            sketch,
            _reference("Distance", -3, 40.0),
            11,
        )
        _add_constraint(
            sketch,
            _reference("Distance", _LENGTH_LINE, 10.0),
            12,
        )
        sketch.setActive(1, False)
        sketch.setVirtualSpace(4, True)
        sketch.renameConstraint(0, "DrivenLength")
        sketch.setExpression("Constraints.DrivenLength", "10 mm")
        document.recompute()
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    assert _solver_issues(sketch) == ((), (), (), ())
    assert _expression_records(sketch)
    return {
        "distance": 0,
        "radius": 1,
        "distance_x": 2,
        "distance_y": 3,
        "diameter": 4,
        "angle": 5,
        "weight": 6,
        "snells_law": 9,
        "nondimensional": 10,
        "external_only": 11,
        "redundant": 12,
    }


def exercise_driving_case(
    *,
    sketch: Any,
    document: Any,
    native_call: Callable[..., dict],
    process_events: Callable[[int], None],
    edit_boundary: Callable[..., tuple],
    boundary: tuple,
    controller: Any,
) -> dict[str, Any]:
    indices = _prepare_fixtures(document, sketch)
    process_events(24)
    before_geometry, before_constraints = _records(sketch)
    before_expressions = _expression_records(sketch)
    desired = [
        (indices["distance"], False),
        (indices["radius"], True),
        (indices["distance_x"], True),
        (indices["distance_y"], True),
        (indices["diameter"], True),
        (indices["angle"], True),
        (indices["weight"], True),
        (indices["snells_law"], True),
    ]
    diagnosis_before = (
        copy.deepcopy(before_geometry),
        copy.deepcopy(before_constraints),
        before_expressions,
        _solver_issues(sketch),
    )
    diagnosis = sketch.diagnoseDrivingChanges(desired)
    assert diagnosis["accepted"] is True, diagnosis
    assert diagnosis["constraint_indices"] == [index for index, _state in desired]
    assert diagnosis["driving_states"] == [state for _index, state in desired]
    assert diagnosis_before == (
        *_records(sketch),
        _expression_records(sketch),
        _solver_issues(sketch),
    )
    _phase("diagnostic")

    document.clearUndos()
    invalid_calls = (
        _arguments(sketch, [(indices["distance"], False)], geometry_count=9),
        _arguments(sketch, [(indices["distance"], False)]),
        _arguments(sketch, [(indices["nondimensional"], True)]),
        _arguments(sketch, [(indices["external_only"], False)]),
        _arguments(
            sketch,
            [(indices["radius"], False), (indices["redundant"], False)],
        ),
    )
    for arguments in invalid_calls:
        response = native_call(arguments, succeeds=False)
        assert response["error_code"] in {
            "NATIVE_ARGUMENTS_INVALID",
            "NATIVE_SKETCH_INVALID",
        }
    duplicate = _arguments(
        sketch,
        [(indices["radius"], False), (indices["radius"], False)],
    )
    assert native_call(duplicate, succeeds=False)["error_code"] == (
        "NATIVE_ARGUMENTS_INVALID"
    )
    assert int(document.UndoCount) == 0
    assert _records(sketch) == (before_geometry, before_constraints)
    assert _expression_records(sketch) == before_expressions
    _phase("refusals")

    Gui.Selection.clearSelection(document.Name)
    Gui.Selection.addSelection(document.Name, sketch.Name, "Edge10")
    process_events(8)
    selection = _selection_state(document)
    assert selection == ((sketch.Name, ("Edge10",)),)
    arguments = _arguments(
        sketch,
        [(index, bool(before_constraints[index]["driving"])) for index, _ in desired],
    )
    result = native_call(arguments)
    assert result["operation"] == "toggle_driving_reference"
    assert result["diagnosed_degrees_of_freedom"] >= 0
    assert [item["constraint_type"] for item in result["changed_constraints"]] == list(
        _DIMENSIONAL_TYPES
    )
    assert result["changed_constraints"][0]["expression_removed"] is True
    assert all(
        not item["expression_removed"] for item in result["changed_constraints"][1:]
    )
    assert _selection_state(document) == selection
    assert int(document.UndoCount) == 1
    assert document.UndoNames[0] == "Toggle Native Sketch Driving/Reference"
    assert sketch.getActive(indices["radius"]) is False
    assert sketch.getVirtualSpace(indices["diameter"]) is True
    assert _expression_records(sketch) == ()
    assert _solver_issues(sketch) == ((), (), (), ())
    after_geometry, after_constraints = _records(sketch)
    expected_states = {index: state for index, state in desired}
    assert {
        index: bool(after_constraints[index]["driving"]) for index in expected_states
    } == expected_states
    _phase("toggle")

    document.undo()
    process_events(24)
    assert _records(sketch) == (before_geometry, before_constraints)
    assert _expression_records(sketch) == before_expressions
    document.redo()
    process_events(24)
    assert _records(sketch) == (after_geometry, after_constraints)
    assert _expression_records(sketch) == ()
    assert _selection_state(document) == selection
    assert edit_boundary(document, sketch, controller) == boundary
    _phase("undo_redo")

    return {
        "geometry_count": int(sketch.GeometryCount),
        "constraint_count": int(sketch.ConstraintCount),
        "geometry": [json.dumps(item, sort_keys=True) for item in after_geometry],
        "constraints": list(after_constraints),
        "indices": indices,
    }


def verify_reopened_driving(sketch: Any, expected: dict[str, Any]) -> None:
    assert int(sketch.GeometryCount) == expected["geometry_count"]
    assert int(sketch.ConstraintCount) == expected["constraint_count"]
    geometry, constraints = _records(sketch)
    observed_geometry = tuple(json.dumps(item, sort_keys=True) for item in geometry)
    expected_metadata = [
        json.loads(item)
        for item in sketch_geometry_metadata(tuple(expected["geometry"]))
    ]
    observed_metadata = [
        json.loads(item) for item in sketch_geometry_metadata(observed_geometry)
    ]
    observed_tags = set()
    for before, after in zip(expected_metadata, observed_metadata, strict=True):
        before.pop("tag", None)
        tag = str(after.pop("tag", "") or "")
        assert tag and tag not in observed_tags
        observed_tags.add(tag)
        assert after == before
    assert constraints == tuple(expected["constraints"])
    assert _expression_records(sketch) == ()
    indices = expected["indices"]
    assert sketch.getActive(indices["radius"]) is False
    assert sketch.getVirtualSpace(indices["diameter"]) is True
    assert _solver_issues(sketch) == ((), (), (), ())
