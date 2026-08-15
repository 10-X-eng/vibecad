# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import copy
import math
import sys
from types import SimpleNamespace

import pytest

import VibeCADNativeSketchConstraintRuntime as runtime_module
from VibeCADNativeSketchConstraintRuntime import NativeSketchConstraintRuntime
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGroup import (
    create_sketch_group,
    preflight_sketch_group,
    prepare_sketch_group,
    verify_sketch_group,
)
from VibeCADNativeSketchState import serialize_sketch_constraint
from vibecad_tests.native_sketch_test_support import (
    FakeArc,
    FakeBSpline,
    FakeCircle,
    FakeConstraint,
    FakeEllipse,
    FakeEllipticalArc,
    FakeExternalLine,
    FakeHyperbola,
    FakeHyperbolicArc,
    FakeLine,
    FakeParabola,
    FakeParabolicArc,
    FakePoint,
    geometry_target_values,
    install_fake_sketch_host,
)


def _point(x: float, y: float) -> SimpleNamespace:
    return SimpleNamespace(x=x, y=y, z=0.0)


def _line(start=(-2.0, -3.0), end=(4.0, 2.0)) -> FakeLine:
    return FakeLine(_point(*start), _point(*end))


def _bounded(geometry, bounds=(-2.0, -3.0, 4.0, 2.0)):
    box = SimpleNamespace(
        XMin=float(bounds[0]),
        YMin=float(bounds[1]),
        XMax=float(bounds[2]),
        YMax=float(bounds[3]),
    )
    geometry.toShape = lambda: SimpleNamespace(BoundBox=box)
    return geometry


def _element(index: int, position: str = "whole") -> dict[str, object]:
    return {"geometry_index": index, "position": position}


def _values(selection=None, **updates) -> dict[str, object]:
    return geometry_target_values(
        **{
            "expected_geometry_count": 2,
            "expected_external_geometry_count": 0,
            "selection": (
                [_element(0), _element(1)] if selection is None else selection
            ),
            **updates,
        }
    )


def _two_member_host(monkeypatch, first=None, second=None):
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.enablePersistentGeometryTags()
    first = _bounded(first or _line())
    sketch.Geometry[0] = first
    sketch.GeometryFacadeList[0].Geometry = first
    second = _bounded(second or _line((1.0, 1.0), (8.0, 5.0)), (1.0, 1.0, 8.0, 5.0))
    assert sketch.addGeometry(second, False) == 1
    return document, sketch, context


def _prepared(document, context, values):
    return preflight_sketch_group(
        context,
        prepare_sketch_group(document.Uid, values),
    )


def _apply(document, prepared):
    return verify_sketch_group(
        document,
        create_sketch_group(document, prepared),
    )


def test_group_creates_exact_handle_and_ordered_members(monkeypatch) -> None:
    document, sketch, context = _two_member_host(monkeypatch)
    sketch.addConstraint(FakeConstraint("Horizontal", 0))
    before = copy.deepcopy(sketch.Geometry)

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values([_element(1), _element(0)], expected_constraint_count=1),
        ),
    )

    assert result["operation"] == "constrain_group"
    assert result["group_constraint"] == {
        "index": 1,
        "type": "Group",
        "handle_index": 2,
        "member_indices": [1, 0],
        "member_count": 2,
        "ignored_existing_constraint_count": 1,
    }
    assert result["handle"]["construction"] is True
    assert result["handle"]["start_mm"][:2] == [-2.0, -3.0]
    assert result["handle"]["end_mm"][:2] == [-2.0, 5.0]
    assert sketch.Constraints[-1].Elements == ((2, 0), (1, 0), (0, 0))
    assert result["internal_cleanup"]["deleted_geometry_count"] == 0
    for expected, observed in zip(before, sketch.Geometry[:2], strict=True):
        assert vars(expected.StartPoint) == vars(observed.StartPoint)
        assert vars(expected.EndPoint) == vars(observed.EndPoint)


def test_group_verifies_all_sixteen_members_beyond_compact_state_limit(
    monkeypatch,
) -> None:
    document, sketch, context = _two_member_host(monkeypatch)
    for index in range(2, 16):
        start = (float(index), -float(index))
        end = (float(index + 1), float(index + 1))
        assert sketch.addGeometry(_bounded(_line(start, end)), False) == index

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(
                [_element(index) for index in range(16)],
                expected_geometry_count=16,
            ),
        ),
    )

    expected_elements = ((16, 0),) + tuple((index, 0) for index in range(16))
    assert sketch.Constraints[-1].Elements == expected_elements
    record = serialize_sketch_constraint(sketch, 0)
    assert record["element_count"] == 17
    assert len(record["elements"]) == 8
    assert record["elements_truncated"] is True
    assert result["group_constraint"]["member_count"] == 16
    assert result["group_constraint"]["member_indices"] == list(range(16))


def _member_families() -> tuple[object, ...]:
    return (
        _line(),
        FakePoint(_point(1.0, 2.0)),
        FakeCircle(_point(0.0, 0.0), _point(0.0, 0.0), 2.0),
        FakeArc(FakeCircle(_point(0.0, 0.0), _point(0.0, 0.0), 2.0), 0.2, 1.4),
        FakeEllipse(_point(0.0, 0.0), 4.0, 2.0),
        FakeEllipticalArc(FakeEllipse(_point(0.0, 0.0), 4.0, 2.0), 0.2, 1.4),
        FakeHyperbolicArc(FakeHyperbola(_point(0.0, 0.0), 3.0, 2.0), -0.4, 0.8),
        FakeParabolicArc(
            FakeParabola(_point(2.0, 0.0), _point(0.0, 0.0), None),
            -2.0,
            3.0,
        ),
        FakeBSpline(),
    )


@pytest.mark.parametrize("geometry", _member_families())
def test_group_supports_every_shipped_primary_geometry_family(
    monkeypatch,
    geometry,
) -> None:
    document, _sketch, context = _two_member_host(monkeypatch, first=geometry)

    result = _apply(document, _prepared(document, context, _values()))

    assert result["members"][0]["type_id"] == geometry.TypeId
    assert result["group_constraint"]["member_count"] == 2


def test_group_allows_construction_and_blocked_members(monkeypatch) -> None:
    document, sketch, context = _two_member_host(monkeypatch)
    sketch.GeometryFacadeList[0].Construction = True
    sketch.addConstraint(FakeConstraint("Block", 1))

    result = _apply(
        document,
        _prepared(document, context, _values(expected_constraint_count=1)),
    )

    assert result["members"][0]["construction"] is True
    assert result["members"][1]["blocked"] is True
    assert result["group_constraint"]["ignored_existing_constraint_count"] == 1


def test_group_performs_only_human_command_internal_cleanup(monkeypatch) -> None:
    ellipse = FakeEllipse(_point(0.0, 0.0), 4.0, 2.0)
    document, sketch, context = _two_member_host(monkeypatch, first=ellipse)
    internal = _bounded(_line((-4.0, 0.0), (4.0, 0.0)), (-4.0, 0.0, 4.0, 0.0))
    internal_index = sketch.addGeometry(internal, True)
    sketch.GeometryFacadeList[internal_index].InternalType = "EllipseMajorDiameter"
    sketch.addConstraint(
        FakeConstraint(
            "InternalAlignment::EllipseMajorDiameter",
            internal_index,
            0,
            0,
            0,
        )
    )

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(
                [_element(0), _element(1)],
                expected_geometry_count=3,
                expected_constraint_count=1,
            ),
        ),
    )

    assert result["group_constraint"]["member_indices"] == [0, 1]
    assert result["group_constraint"]["handle_index"] == 2
    assert result["internal_cleanup"] == {
        "deleted_geometry_count": 1,
        "deleted_constraint_count": 1,
        "deleted_geometry_tags": ["fake-geometry-2"],
    }
    assert sketch.Constraints == [sketch.Constraints[0]]
    assert sketch.Constraints[0].Type == "Group"
    assert sketch.Constraints[0].Elements == ((2, 0), (0, 0), (1, 0))


@pytest.mark.parametrize(
    "selection",
    (
        [_element(-1), _element(0)],
        [_element(-2), _element(0)],
        [_element(-3), _element(0)],
        [_element(0, "start"), _element(1)],
    ),
)
def test_group_refuses_axes_external_and_point_positions(monkeypatch, selection) -> None:
    document, sketch, context = _two_member_host(monkeypatch)
    expected_external = 0
    if selection[0]["geometry_index"] == -3:
        sketch.ExternalGeo.append(FakeExternalLine("Support.Edge1"))
        expected_external = 1
    with pytest.raises(NativeSketchError, match="whole internal|position"):
        _prepared(
            document,
            context,
            _values(selection, expected_external_geometry_count=expected_external),
        )


def test_group_refuses_internal_alignment_members(monkeypatch) -> None:
    document, sketch, context = _two_member_host(monkeypatch)
    sketch.GeometryFacadeList[0].InternalType = "EllipseMajorDiameter"
    with pytest.raises(NativeSketchError, match="internal-alignment"):
        _prepared(document, context, _values())


def test_group_refuses_existing_group_handles_members_and_nesting(monkeypatch) -> None:
    document, sketch, context = _two_member_host(monkeypatch)
    third = sketch.addGeometry(_bounded(_line((3.0, -1.0), (5.0, 4.0))), False)
    fourth = sketch.addGeometry(_bounded(_line((6.0, -2.0), (8.0, 3.0))), False)
    sketch.addConstraint(FakeConstraint("Group", [0, 0, 1, 0, third, 0]))

    with pytest.raises(NativeSketchError, match="cannot nest existing group handle"):
        _prepared(
            document,
            context,
            _values(
                [_element(0), _element(fourth)],
                expected_geometry_count=4,
                expected_constraint_count=1,
            ),
        )
    with pytest.raises(NativeSketchError, match="group handle 0"):
        _prepared(
            document,
            context,
            _values(
                [_element(1), _element(fourth)],
                expected_geometry_count=4,
                expected_constraint_count=1,
            ),
        )


@pytest.mark.parametrize(
    "selection",
    (
        [_element(0)],
        [_element(index) for index in range(17)],
        [_element(0), _element(0)],
        [{"geometry_index": 0}, _element(1)],
        [_element(0) | {"extra": True}, _element(1)],
    ),
)
def test_group_refuses_unbounded_duplicate_or_open_selection(monkeypatch, selection) -> None:
    document, _sketch, context = _two_member_host(monkeypatch)
    with pytest.raises(NativeSketchError, match="two through|distinct|incorrect fields"):
        _prepared(document, context, _values(selection))


@pytest.mark.parametrize(
    "updates",
    (
        {"expected_geometry_count": 1},
        {"expected_constraint_count": 1},
        {"expected_external_geometry_count": 1},
    ),
)
def test_group_refuses_stale_counts(monkeypatch, updates) -> None:
    document, _sketch, context = _two_member_host(monkeypatch)
    with pytest.raises(NativeSketchError, match="count changed|count does not match"):
        _prepared(document, context, _values(**updates))


def test_group_refuses_solver_issues_and_invalid_bounds(monkeypatch) -> None:
    document, sketch, context = _two_member_host(monkeypatch)
    sketch.RedundantConstraints = [0]
    with pytest.raises(NativeSketchError, match="without current solver issues"):
        _prepared(document, context, _values())

    sketch.RedundantConstraints = []
    for geometry in sketch.Geometry:
        _bounded(geometry, (0.0, 2.0, 5.0, 2.0))
    with pytest.raises(NativeSketchError, match="zero-height"):
        _prepared(document, context, _values())

    _bounded(sketch.Geometry[0], (0.0, 0.0, math.inf, 2.0))
    with pytest.raises(NativeSketchError, match="not finite"):
        _prepared(document, context, _values())


def test_group_requires_bounds_and_persistent_tags(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    second = sketch.addGeometry(_line((0.0, 1.0), (1.0, 2.0)), False)
    assert second == 1
    with pytest.raises(NativeSketchError, match="persistent tags"):
        _prepared(document, context, _values())

    sketch.enablePersistentGeometryTags()
    with pytest.raises(NativeSketchError, match="no finite shape bounds"):
        _prepared(document, context, _values())


def test_group_refuses_preflight_drift(monkeypatch) -> None:
    document, sketch, context = _two_member_host(monkeypatch)
    prepared = _prepared(document, context, _values())
    sketch.Geometry[0].EndPoint.x += 1.0

    with pytest.raises(NativeSketchError, match="after Group preflight"):
        create_sketch_group(document, prepared)


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (lambda sketch: setattr(sketch.Geometry[0].EndPoint, "x", 99.0), "changed existing"),
        (
            lambda sketch: setattr(sketch.GeometryFacadeList[-1], "Construction", False),
            "construction handle changed",
        ),
        (lambda sketch: sketch.addConstraint(FakeConstraint("SnellsLaw", 0)), "constraints beyond"),
    ),
)
def test_group_refuses_wrong_postconditions(monkeypatch, mutation, message) -> None:
    document, sketch, context = _two_member_host(monkeypatch)
    draft = create_sketch_group(document, _prepared(document, context, _values()))
    mutation(sketch)

    with pytest.raises(NativeSketchError, match=message):
        verify_sketch_group(document, draft)


def test_group_refuses_unrelated_cleanup_deletion(monkeypatch) -> None:
    ellipse = FakeEllipse(_point(0.0, 0.0), 4.0, 2.0)
    document, sketch, context = _two_member_host(monkeypatch, first=ellipse)

    def wrong_cleanup(_parent_index):
        sketch._delete_group_cleanup_geometry(1)

    monkeypatch.setattr(sketch, "deleteUnusedInternalGeometry", wrong_cleanup)
    draft = create_sketch_group(document, _prepared(document, context, _values()))
    with pytest.raises(NativeSketchError, match="deleted unrelated"):
        verify_sketch_group(document, draft)


def test_group_constructs_exact_host_elements(monkeypatch) -> None:
    document, sketch, context = _two_member_host(monkeypatch)
    captured = []
    constraint_factory = sys.modules["Sketcher"].Constraint

    def capture(constraint_type, *arguments):
        captured.append((constraint_type, copy.deepcopy(arguments)))
        return constraint_factory(constraint_type, *arguments)

    monkeypatch.setattr(sys.modules["Sketcher"], "Constraint", capture)
    _apply(
        document,
        _prepared(
            document,
            context,
            _values([_element(1), _element(0)]),
        ),
    )

    assert captured == [("Group", ([2, 0, 1, 0, 0, 0],))]
    assert sketch.Constraints[-1].Elements == ((2, 0), (1, 0), (0, 0))


def test_constraint_runtime_routes_group_through_one_exact_transaction(
    monkeypatch,
) -> None:
    document, _sketch, context = _two_member_host(monkeypatch)
    captured = {}

    def run_immediate(runtime_context, **values):
        assert runtime_context is context
        captured.update(values)
        draft = values["mutate"](document)
        return values["verify"](document, draft)

    monkeypatch.setattr(runtime_module, "run_immediate_mutation", run_immediate)
    result = NativeSketchConstraintRuntime(context).mutate_constraint(
        {"operation": "constrain_group", **_values()},
        ticket=None,
    )

    assert captured["transaction_name"] == "Create Native Sketch Constraint Group"
    assert result["operation"] == "constrain_group"
