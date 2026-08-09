# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact Constraint Group lifecycle for the rolling Native Sketch GUI gate."""

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
_POINT = 1
_CIRCLE = 2
_CIRCULAR_ARC = 3
_ELLIPSE = 4
_ELLIPTICAL_ARC = 5
_HYPERBOLIC_ARC = 6
_PARABOLIC_ARC = 7
_BSPLINE = 8
_CONSTRUCTION_LINE = 9
_SPARE_LINE = 10
_MEMBERS = (
    _BSPLINE,
    _PARABOLIC_ARC,
    _HYPERBOLIC_ARC,
    _ELLIPTICAL_ARC,
    _ELLIPSE,
    _CIRCULAR_ARC,
    _CIRCLE,
    _POINT,
    _LINE,
    _CONSTRUCTION_LINE,
)


def _phase(name: str) -> None:
    os.write(2, f"VIBECAD_NATIVE_SKETCH_GROUP_PHASE {name}\n".encode("ascii"))


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
        "operation": "constrain_group",
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


def _add(sketch: Any, geometry: Any, expected: int, construction=False) -> None:
    assert int(sketch.addGeometry(geometry, construction)) == expected


def _prepare_fixtures(document: Any, sketch: Any) -> None:
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (0, 0)
    document.openTransaction("Prepare Native Sketch Group fixtures")
    try:
        _add(sketch, Part.LineSegment(App.Vector(0, -4), App.Vector(12, -4)), _LINE)
        _add(sketch, Part.Point(App.Vector(18, 7)), _POINT)
        _add(sketch, Part.Circle(App.Vector(30, 0), App.Vector(0, 0, 1), 5), _CIRCLE)
        _add(
            sketch,
            Part.ArcOfCircle(
                Part.Circle(App.Vector(46, 0), App.Vector(0, 0, 1), 6),
                -0.8,
                1.8,
            ),
            _CIRCULAR_ARC,
        )
        _add(sketch, Part.Ellipse(App.Vector(62, 0), 8, 3), _ELLIPSE)
        _add(
            sketch,
            Part.ArcOfEllipse(Part.Ellipse(App.Vector(80, 0), 7, 3), -0.5, 2.1),
            _ELLIPTICAL_ARC,
        )
        _add(
            sketch,
            Part.ArcOfHyperbola(
                Part.Hyperbola(App.Vector(97, 0), 6, 2),
                -0.7,
                0.8,
            ),
            _HYPERBOLIC_ARC,
        )
        _add(
            sketch,
            Part.ArcOfParabola(
                Part.Parabola(
                    App.Vector(116, 0),
                    App.Vector(113, 0),
                    App.Vector(0, 0, 1),
                ),
                -4,
                5,
            ),
            _PARABOLIC_ARC,
        )
        _add(
            sketch,
            Part.BSplineCurve(
                [App.Vector(130, -5), App.Vector(138, 8), App.Vector(148, -2)],
                [3, 3],
                [0.0, 1.0],
                False,
                2,
                [1.0, 1.8, 1.2],
                False,
            ),
            _BSPLINE,
        )
        _add(
            sketch,
            Part.LineSegment(App.Vector(4, 12), App.Vector(16, 16)),
            _CONSTRUCTION_LINE,
            True,
        )
        _add(
            sketch,
            Part.LineSegment(App.Vector(170, -8), App.Vector(180, 6)),
            _SPARE_LINE,
        )
        for parent in (
            _ELLIPSE,
            _ELLIPTICAL_ARC,
            _HYPERBOLIC_ARC,
            _PARABOLIC_ARC,
            _BSPLINE,
        ):
            before = int(sketch.GeometryCount)
            mutation = sketch.exposeInternalGeometry(parent)
            assert isinstance(mutation, dict)
            assert int(sketch.GeometryCount) > before
        assert int(sketch.addConstraint(Sketcher.Constraint("Horizontal", _LINE))) >= 0
        assert int(sketch.addConstraint(Sketcher.Constraint("Block", _CIRCLE))) >= 0
        source = document.getObject("ExternalSource")
        assert source is not None
        sketch.addExternal(source.Name, "Edge1")
        document.recompute()
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise


def _geometry_records(sketch: Any) -> tuple[dict[str, Any], ...]:
    return tuple(
        serialize_sketch_geometry(sketch, index)
        for index in range(int(sketch.GeometryCount))
    )


def _constraint_records(sketch: Any) -> tuple[dict[str, Any], ...]:
    return tuple(
        serialize_sketch_constraint(sketch, index)
        for index in range(int(sketch.ConstraintCount))
    )


def _index_for_tag(sketch: Any, tag: str) -> int:
    matches = [
        index
        for index, record in enumerate(_geometry_records(sketch))
        if record.get("tag") == tag
    ]
    assert len(matches) == 1, (tag, matches)
    return matches[0]


def exercise_group_case(
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
    before_geometry = _geometry_records(sketch)
    before_constraints = _constraint_records(sketch)
    member_tags = [str(before_geometry[index]["tag"]) for index in _MEMBERS]
    spare_tag = str(before_geometry[_SPARE_LINE]["tag"])
    internal_index = next(
        index
        for index, record in enumerate(before_geometry)
        if record.get("internal_type")
    )
    assert bool(sketch.GeometryFacadeList[_CIRCLE].Blocked)
    _phase("fixtures")

    undo_before_failures = int(document.UndoCount)
    invalid_calls = (
        _arguments(sketch, [_element(_LINE)]),
        _arguments(sketch, [_element(-1), _element(_LINE)]),
        _arguments(sketch, [_element(-3), _element(_LINE)]),
        _arguments(sketch, [_element(internal_index), _element(_LINE)]),
        _arguments(
            sketch,
            [_element(_LINE), _element(_POINT)],
            geometry_count=int(sketch.GeometryCount) - 1,
        ),
    )
    for arguments in invalid_calls:
        response = native_call(arguments, succeeds=False)
        assert response["error_code"] in {
            "NATIVE_ARGUMENTS_INVALID",
            "NATIVE_SKETCH_INVALID",
        }
    wrong_position = _arguments(
        sketch,
        [_element(_LINE, "start"), _element(_POINT)],
    )
    assert native_call(wrong_position, succeeds=False)["error_code"] == (
        "NATIVE_ARGUMENTS_INVALID"
    )
    assert int(document.UndoCount) == undo_before_failures
    assert _geometry_records(sketch) == before_geometry
    assert _constraint_records(sketch) == before_constraints
    _phase("refusals")

    Gui.Selection.clearSelection(document.Name)
    Gui.Selection.addSelection(document.Name, sketch.Name, "Edge11")
    process_events(8)
    selection = _selection_state(document)
    result = native_call(_arguments(sketch, [_element(index) for index in _MEMBERS]))
    assert result["operation"] == "constrain_group"
    group = result["group_constraint"]
    assert group["member_count"] == len(_MEMBERS)
    assert group["ignored_existing_constraint_count"] >= 2
    assert [item["tag"] for item in result["members"]] == member_tags
    assert result["handle"]["construction"] is True
    assert result["internal_cleanup"]["deleted_geometry_count"] > 0
    assert result["internal_cleanup"]["deleted_constraint_count"] > 0
    handle_tag = str(result["handle"]["tag"])
    handle_index = _index_for_tag(sketch, handle_tag)
    current_member_indices = [_index_for_tag(sketch, tag) for tag in member_tags]
    group_constraint = sketch.Constraints[int(group["index"])]
    assert str(group_constraint.Type) == "Group"
    assert tuple(tuple(int(value) for value in item) for item in group_constraint.Elements) == (
        (handle_index, 0),
        *((index, 0) for index in current_member_indices),
    )
    assert bool(sketch.GeometryFacadeList[_index_for_tag(sketch, member_tags[6])].Blocked)
    assert _selection_state(document) == selection
    assert document.UndoNames[0] == "Create Native Sketch Constraint Group"
    assert not tuple(sketch.ConflictingConstraints)
    assert not tuple(sketch.RedundantConstraints)
    assert not tuple(sketch.MalformedConstraints)
    after_geometry = _geometry_records(sketch)
    after_constraints = _constraint_records(sketch)
    _phase("group")

    document.undo()
    process_events(24)
    assert _geometry_records(sketch) == before_geometry
    assert _constraint_records(sketch) == before_constraints
    document.redo()
    process_events(24)
    assert _geometry_records(sketch) == after_geometry
    assert _constraint_records(sketch) == after_constraints
    assert _selection_state(document) == selection
    _phase("undo_redo")

    undo_before_nested = int(document.UndoCount)
    handle_index = _index_for_tag(sketch, handle_tag)
    spare_index = _index_for_tag(sketch, spare_tag)
    for target in (current_member_indices[0], handle_index):
        response = native_call(
            _arguments(sketch, [_element(target), _element(spare_index)]),
            succeeds=False,
        )
        assert response["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == undo_before_nested
    assert _geometry_records(sketch) == after_geometry
    assert _constraint_records(sketch) == after_constraints
    assert edit_boundary(document, sketch, controller) == boundary
    _phase("nested_refusal")

    return {
        "geometry_count": int(sketch.GeometryCount),
        "constraint_count": int(sketch.ConstraintCount),
        "member_tags": member_tags,
        "handle_tag": handle_tag,
        "spare_tag": spare_tag,
        "geometries": [json.dumps(item, sort_keys=True) for item in after_geometry],
        "constraints": list(after_constraints),
        "cleanup": dict(result["internal_cleanup"]),
    }


def verify_reopened_group(sketch: Any, expected: dict[str, Any]) -> None:
    assert int(sketch.GeometryCount) == expected["geometry_count"]
    assert int(sketch.ConstraintCount) == expected["constraint_count"]
    expected_geometry = tuple(json.loads(item) for item in expected["geometries"])
    observed_tags = set()
    for index, record in enumerate(expected_geometry):
        observed = serialize_sketch_geometry(sketch, index)
        for key, value in record.items():
            if key != "tag":
                assert observed[key] == value, (index, key, value, observed[key])
        assert observed["tag"] and observed["tag"] not in observed_tags
        observed_tags.add(observed["tag"])
    for index, record in enumerate(expected["constraints"]):
        assert serialize_sketch_constraint(sketch, index) == record
    group_indices = [
        index
        for index, constraint in enumerate(sketch.Constraints)
        if str(constraint.Type) == "Group"
    ]
    assert len(group_indices) == 1
    group = sketch.Constraints[group_indices[0]]
    assert len(group.Elements) == len(expected["member_tags"]) + 1
    handle_index = int(group.Elements[0][0])
    assert bool(sketch.GeometryFacadeList[handle_index].Construction)
    assert not tuple(sketch.ConflictingConstraints)
    assert not tuple(sketch.RedundantConstraints)
    assert not tuple(sketch.MalformedConstraints)
