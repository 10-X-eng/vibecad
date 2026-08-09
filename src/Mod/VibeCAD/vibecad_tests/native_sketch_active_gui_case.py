# SPDX-License-Identifier: LGPL-2.1-or-later

"""Active/Inactive lifecycle case for focused and rolling Sketch GUI gates."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Callable

import FreeCAD as App
import FreeCADGui as Gui
import Part
import Sketcher

from VibeCADNativeSketchActiveState import sketch_geometry_metadata
from vibecad_tests.native_sketch_constraint_toggle_gui_support import (
    expression_records,
    selection_state,
    sketch_records,
    solver_issues,
)


_HORIZONTAL_LINE = 0
_VERTICAL_LINE = 1
_DISTANCE_LINE = 2
_CIRCLE = 3
_BLOCK_LINE = 4
_GROUP_HANDLE = 5
_GROUP_LINE = 6
_GROUP_POINT = 7
_TEXT_HANDLE = 8
_DUPLICATE_LINE = 9
_SPARE_LINE = 10
_TARGET_TYPES = (
    "Horizontal",
    "Vertical",
    "Distance",
    "Radius",
    "Block",
    "Group",
    "Text",
)


def _phase(name: str) -> None:
    os.write(2, f"VIBECAD_NATIVE_SKETCH_ACTIVE_PHASE {name}\n".encode("ascii"))


def _arguments(
    sketch: Any,
    targets: list[tuple[int, bool]],
    *,
    geometry_count: int | None = None,
    constraint_count: int | None = None,
) -> dict[str, object]:
    return {
        "operation": "toggle_active_inactive",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": (
            int(sketch.GeometryCount) if geometry_count is None else geometry_count
        ),
        "expected_constraint_count": (
            int(sketch.ConstraintCount)
            if constraint_count is None
            else constraint_count
        ),
        "expected_external_geometry_count": 0,
        "targets": [
            {
                "constraint_index": index,
                "expected_active": expected,
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


def _font_path() -> str:
    path = (
        Path(App.getResourceDir())
        / "Mod"
        / "TechDraw"
        / "Resources"
        / "fonts"
        / "osifont-lgpl3fe.ttf"
    )
    assert path.is_file(), path
    return str(path)


def _prepare_fixtures(document: Any, sketch: Any) -> dict[str, int]:
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (0, 0)
    font_path = _font_path()
    document.openTransaction("Prepare Native Sketch Active fixtures")
    try:
        _add_geometry(
            sketch,
            Part.LineSegment(App.Vector(0, 0), App.Vector(10, 0)),
            _HORIZONTAL_LINE,
        )
        _add_geometry(
            sketch,
            Part.LineSegment(App.Vector(20, 0), App.Vector(20, 10)),
            _VERTICAL_LINE,
        )
        _add_geometry(
            sketch,
            Part.LineSegment(App.Vector(30, 0), App.Vector(40, 0)),
            _DISTANCE_LINE,
        )
        _add_geometry(
            sketch,
            Part.Circle(App.Vector(50, 0), App.Vector(0, 0, 1), 4),
            _CIRCLE,
        )
        _add_geometry(
            sketch,
            Part.LineSegment(App.Vector(60, 0), App.Vector(68, 3)),
            _BLOCK_LINE,
        )
        _add_geometry(
            sketch,
            Part.LineSegment(App.Vector(75, -5), App.Vector(75, 5)),
            _GROUP_HANDLE,
            True,
        )
        _add_geometry(
            sketch,
            Part.LineSegment(App.Vector(80, 0), App.Vector(88, 2)),
            _GROUP_LINE,
        )
        _add_geometry(sketch, Part.Point(App.Vector(90, 4)), _GROUP_POINT)
        _add_geometry(
            sketch,
            Part.LineSegment(App.Vector(100, 0), App.Vector(100, 8)),
            _TEXT_HANDLE,
            True,
        )
        _add_geometry(
            sketch,
            Part.LineSegment(App.Vector(110, 0), App.Vector(120, 0)),
            _DUPLICATE_LINE,
        )
        _add_geometry(
            sketch,
            Part.LineSegment(App.Vector(130, 5), App.Vector(140, 5)),
            _SPARE_LINE,
        )

        _add_constraint(sketch, Sketcher.Constraint("Horizontal", _HORIZONTAL_LINE), 0)
        _add_constraint(sketch, Sketcher.Constraint("Vertical", _VERTICAL_LINE), 1)
        _add_constraint(
            sketch,
            Sketcher.Constraint("Distance", _DISTANCE_LINE, 10.0),
            2,
        )
        _add_constraint(sketch, _reference("Radius", _CIRCLE, 4.0), 3)
        _add_constraint(sketch, Sketcher.Constraint("Block", _BLOCK_LINE), 4)
        _add_constraint(
            sketch,
            Sketcher.Constraint(
                "Group",
                [
                    _GROUP_HANDLE,
                    0,
                    _GROUP_LINE,
                    0,
                    _GROUP_POINT,
                    0,
                ],
            ),
            5,
        )
        _add_constraint(
            sketch,
            Sketcher.Constraint(
                "Text",
                [_TEXT_HANDLE, 0],
                "ACTIVE",
                font_path,
                True,
            ),
            6,
        )
        sketch.setTextAndFont(6, "ACTIVE", font_path, True, False)
        _add_constraint(
            sketch,
            Sketcher.Constraint("Horizontal", _DUPLICATE_LINE),
            7,
        )
        _add_constraint(
            sketch,
            Sketcher.Constraint("Horizontal", _DUPLICATE_LINE),
            8,
        )
        sketch.setActive(1, False)
        sketch.setActive(8, False)
        sketch.setVirtualSpace(3, True)
        sketch.renameConstraint(2, "Length")
        sketch.setExpression("Constraints.Length", "10 mm")
        document.recompute()
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    assert solver_issues(sketch) == ((), (), (), ())
    observed_expressions = expression_records(sketch)
    assert observed_expressions == ((".Constraints.Length", "10 mm"),), (
        observed_expressions
    )
    return {
        "horizontal": 0,
        "vertical": 1,
        "distance": 2,
        "radius": 3,
        "block": 4,
        "group": 5,
        "text": 6,
        "duplicate_base": 7,
        "duplicate_inactive": 8,
    }


def exercise_active_case(
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
    before_geometry, before_constraints = sketch_records(sketch)
    before_expressions = expression_records(sketch)
    desired = [
        (indices["horizontal"], False),
        (indices["vertical"], True),
        (indices["distance"], False),
        (indices["radius"], False),
        (indices["block"], False),
        (indices["group"], False),
        (indices["text"], False),
    ]
    diagnosis_before = (
        copy.deepcopy(before_geometry),
        copy.deepcopy(before_constraints),
        before_expressions,
        solver_issues(sketch),
    )
    diagnosis = sketch.diagnoseActiveChanges(desired)
    assert diagnosis["accepted"] is True, diagnosis
    assert diagnosis["constraint_indices"] == [index for index, _state in desired]
    assert diagnosis["active_states"] == [state for _index, state in desired]
    assert diagnosis_before == (
        *sketch_records(sketch),
        expression_records(sketch),
        solver_issues(sketch),
    )
    _phase("diagnostic")

    document.clearUndos()
    invalid_calls = (
        _arguments(sketch, [(indices["horizontal"], True)], geometry_count=10),
        _arguments(sketch, [(indices["horizontal"], False)]),
        _arguments(sketch, [(indices["duplicate_inactive"], False)]),
    )
    for arguments in invalid_calls:
        response = native_call(arguments, succeeds=False)
        assert response["error_code"] in {
            "NATIVE_ARGUMENTS_INVALID",
            "NATIVE_SKETCH_INVALID",
        }
    duplicate = _arguments(
        sketch,
        [(indices["horizontal"], True), (indices["horizontal"], True)],
    )
    assert native_call(duplicate, succeeds=False)["error_code"] == (
        "NATIVE_ARGUMENTS_INVALID"
    )
    assert int(document.UndoCount) == 0
    assert sketch_records(sketch) == (before_geometry, before_constraints)
    assert expression_records(sketch) == before_expressions
    _phase("refusals")

    Gui.Selection.clearSelection(document.Name)
    Gui.Selection.addSelection(document.Name, sketch.Name, "Edge11")
    process_events(8)
    selection = selection_state(document)
    assert selection == ((sketch.Name, ("Edge11",)),)
    arguments = _arguments(
        sketch,
        [
            (index, bool(before_constraints[index]["active"]))
            for index, _state in desired
        ],
    )
    result = native_call(arguments)
    assert result["operation"] == "toggle_active_inactive"
    assert result["diagnosed_degrees_of_freedom"] >= 0
    assert [item["constraint_type"] for item in result["changed_constraints"]] == list(
        _TARGET_TYPES
    )
    assert selection_state(document) == selection
    assert int(document.UndoCount) == 1
    assert document.UndoNames[0] == "Toggle Native Sketch Active/Inactive"
    assert expression_records(sketch) == before_expressions
    assert sketch.getDriving(indices["radius"]) is False
    assert sketch.getVirtualSpace(indices["radius"]) is True
    assert bool(sketch.GeometryFacadeList[_BLOCK_LINE].Blocked) is True
    assert solver_issues(sketch) == ((), (), (), ())
    after_geometry, after_constraints = sketch_records(sketch)
    expected_states = {index: state for index, state in desired}
    assert {
        index: bool(after_constraints[index]["active"]) for index in expected_states
    } == expected_states
    _phase("toggle")

    document.undo()
    process_events(24)
    assert sketch_records(sketch) == (before_geometry, before_constraints)
    assert expression_records(sketch) == before_expressions
    document.redo()
    process_events(24)
    assert sketch_records(sketch) == (after_geometry, after_constraints)
    assert expression_records(sketch) == before_expressions
    assert selection_state(document) == selection
    assert edit_boundary(document, sketch, controller) == boundary
    _phase("undo_redo")

    return {
        "geometry_count": int(sketch.GeometryCount),
        "constraint_count": int(sketch.ConstraintCount),
        "geometry": [json.dumps(item, sort_keys=True) for item in after_geometry],
        "constraints": list(after_constraints),
        "expressions": list(before_expressions),
        "indices": indices,
    }


def verify_reopened_active(sketch: Any, expected: dict[str, Any]) -> None:
    assert int(sketch.GeometryCount) == expected["geometry_count"]
    assert int(sketch.ConstraintCount) == expected["constraint_count"]
    geometry, constraints = sketch_records(sketch)
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
    assert expression_records(sketch) == tuple(expected["expressions"])
    indices = expected["indices"]
    assert sketch.getDriving(indices["radius"]) is False
    assert sketch.getVirtualSpace(indices["radius"]) is True
    assert bool(sketch.GeometryFacadeList[_BLOCK_LINE].Blocked) is True
    assert solver_issues(sketch) == ((), (), (), ())
