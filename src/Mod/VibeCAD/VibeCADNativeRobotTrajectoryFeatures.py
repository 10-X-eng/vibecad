# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact edge, dress-up, and compound Robot trajectory features."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
import struct
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRobotToolState import (
    NativeRobotToolStateError,
    RobotToolShapeRecord,
    capture_robot_tool_shape_record,
)
from VibeCADNativeRobotTrajectory import NativeRobotTrajectoryError
from VibeCADNativeRobotTrajectoryFeatureSpecs import (
    CompoundTrajectorySpec,
    DressUpTrajectorySpec,
    EdgeTrajectorySpec,
    PlacementSpec,
)
from VibeCADNativeRobotTrajectoryPresentation import (
    expected_replacement_visibility,
    reconcile_replacement_visibility,
    replacement_presentations,
    trajectory_visibility,
)
from VibeCADNativeRobotTrajectoryState import (
    MAX_TOTAL_WAYPOINTS,
    MAX_TRAJECTORIES,
    MAX_WAYPOINTS_PER_TRAJECTORY,
    NativeRobotTrajectoryStateError,
    RobotTrajectoryState,
    TrajectoryStateRecord,
    capture_robot_trajectory_state,
    robot_placement_summary,
    same_robot_trajectory_state,
)
from VibeCADNativeTargets import (
    object_identity,
    object_reference,
    read_current_selection,
    resolve_object,
)


_FEATURE_TYPES = {
    "edge2_trac": ("Robot::Edge2TracObject", "EdgeTrajectory"),
    "trajectory_dress_up": (
        "Robot::TrajectoryDressUpObject",
        "TrajectoryModifier",
    ),
    "trajectory_compound": ("Robot::TrajectoryCompound", "TrajectorySequence"),
}
_CONTINUITY_PROPERTIES = {
    "unchanged": "DontChange",
    "continuous": "Continues",
    "discontinuous": "Discontinues",
}
_PLACEMENT_PROPERTIES = {
    "unchanged": "DontChange",
    "replace_orientation": "UseOrientation",
    "translate": "AddPosition",
    "rotate": "AddOrintation",
    "transform": "AddPositionAndOrientation",
}
_SUPPORTED_EDGE_CURVES = frozenset({"Line", "LineSegment", "BSplineCurve", "Circle"})


@dataclass(frozen=True, slots=True)
class _TimelineState:
    timeline: Any | None
    operations: tuple[Any, ...]
    visibility: tuple[bool, ...]


@dataclass(frozen=True, slots=True)
class PreparedTrajectoryFeature:
    operation: str
    spec: EdgeTrajectorySpec | DressUpTrajectorySpec | CompoundTrajectorySpec
    trajectory_state: RobotTrajectoryState
    target: Any | None
    target_index: int | None
    sources: tuple[Any, ...]
    source_indices: tuple[int, ...]
    edge_source: Any | None
    edge_source_state: RobotToolShapeRecord | None
    edge_source_visibility: bool | None
    expected_waypoints: tuple[Mapping[str, Any], ...] | None
    objects_before: tuple[Any, ...]
    selection_before: Mapping[str, Any]
    timeline_before: _TimelineState
    old_replaced_sources: tuple[Any, ...]
    replacement_presentations: tuple[Any, ...]


def _transaction_open(document: Any) -> bool:
    booked = getattr(document, "getBookedTransactionID", None)
    return bool(
        bool(getattr(document, "HasPendingTransaction", False))
        or (callable(booked) and int(booked() or 0) != 0)
    )


def _require_clean_document(document: Any) -> None:
    if _transaction_open(document):
        raise NativeRobotTrajectoryError(
            "Finish or cancel the open transaction before changing a trajectory feature."
        )
    if bool(getattr(document, "Recomputing", False)) or bool(
        getattr(document, "RecomputePending", False)
    ):
        raise NativeRobotTrajectoryError(
            "Wait for the active document recompute before changing a trajectory feature."
        )


def _timeline_state(document: Any) -> _TimelineState:
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None:
        return _TimelineState(None, (), ())
    if str(getattr(timeline, "TypeId", "") or "") != "App::DocumentTimeline":
        raise NativeRobotTrajectoryError("The active document History is malformed.")
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    visibility = tuple(bool(value) for value in timeline.VisibilityAtEnd)
    if len(operations) != len(visibility):
        raise NativeRobotTrajectoryError("The active document History is malformed.")
    return _TimelineState(timeline, operations, visibility)


def _capture_trajectories(document: Any) -> RobotTrajectoryState:
    try:
        return capture_robot_trajectory_state(document)
    except NativeRobotTrajectoryStateError as exc:
        raise NativeRobotTrajectoryError(str(exc)) from exc


def _capture_edge_source(source: Any) -> RobotToolShapeRecord:
    try:
        return capture_robot_tool_shape_record(source)
    except NativeRobotToolStateError as exc:
        raise NativeRobotTrajectoryError(str(exc)) from exc


def _usable_at_history(obj: Any) -> bool:
    document = getattr(obj, "Document", None)
    reader = getattr(document, "isObjectUsableAtCurrentTimelinePosition", None)
    if not callable(reader):
        return True
    try:
        return bool(reader(obj))
    except (AttributeError, ReferenceError, RuntimeError, TypeError) as exc:
        raise NativeRobotTrajectoryError(
            "A trajectory feature target has unreadable History state."
        ) from exc


def _state_index(state: RobotTrajectoryState, trajectory: Any) -> int:
    try:
        return state.trajectories.index(trajectory)
    except ValueError as exc:
        raise NativeRobotTrajectoryError(
            "An exact trajectory target is absent from current trajectory state."
        ) from exc


def _require_record_usable(record: TrajectoryStateRecord, field: str) -> None:
    if (
        record.data["suppressed"]
        or not record.data["valid"]
        or not record.data["usable_at_history"]
    ):
        raise NativeRobotTrajectoryError(
            f"The exact trajectory {field} is suppressed, invalid, or inactive in History."
        )


def _target(
    document: Any,
    operation: str,
    spec: EdgeTrajectorySpec | DressUpTrajectorySpec | CompoundTrajectorySpec,
    state: RobotTrajectoryState,
) -> tuple[Any | None, int | None]:
    target_spec = spec.target
    if target_spec.mode == "create":
        if len(state.trajectories) >= MAX_TRAJECTORIES:
            raise NativeRobotTrajectoryError(
                "The active document has reached the Native trajectory bound."
            )
        return None, None
    type_id = _FEATURE_TYPES[operation][0]
    target = resolve_object(
        document,
        target_spec.target_ref,
        expected_types=(type_id,),
    )
    if str(getattr(target, "TypeId", "") or "") != type_id:
        raise NativeRobotTrajectoryError(
            "The exact edit target has the wrong trajectory feature type."
        )
    index = _state_index(state, target)
    record = state.records[index]
    if record.state_sha256 != target_spec.expected_target_state_sha256:
        raise NativeRobotTrajectoryError(
            "The exact trajectory edit target changed; read current state and retry."
        )
    _require_record_usable(record, "edit target")
    return target, index


def _trajectory_source(
    document: Any,
    state: RobotTrajectoryState,
    reference: Any,
    expected_digest: str,
) -> tuple[Any, int]:
    source = resolve_object(
        document,
        reference,
        expected_types=("Robot::TrajectoryObject",),
    )
    index = _state_index(state, source)
    record = state.records[index]
    if record.state_sha256 != expected_digest:
        raise NativeRobotTrajectoryError(
            "An exact source trajectory changed; read current state and retry."
        )
    _require_record_usable(record, "source")
    return source, index


def _direct_trajectory_sources(trajectory: Any) -> tuple[Any, ...]:
    type_id = str(getattr(trajectory, "TypeId", "") or "")
    if type_id == "Robot::TrajectoryDressUpObject":
        source = getattr(trajectory, "Source", None)
        return (source,) if source is not None else ()
    if type_id == "Robot::TrajectoryCompound":
        return tuple(getattr(trajectory, "Source", ()) or ())
    return ()


def _would_cycle(source: Any, target: Any) -> bool:
    pending = [source]
    seen = set()
    while pending:
        current = pending.pop()
        if current is target:
            return True
        if current in seen:
            continue
        seen.add(current)
        pending.extend(_direct_trajectory_sources(current))
        if len(seen) > MAX_TRAJECTORIES:
            raise NativeRobotTrajectoryError(
                "The trajectory dependency graph exceeds its exact bound."
            )
    return False


def _placement(spec: PlacementSpec) -> Any:
    import FreeCAD as App

    return App.Placement(
        App.Vector(*spec.origin_mm),
        App.Rotation(App.Vector(*spec.rotation_axis), spec.angle_degrees),
    )


def _float32(value: float) -> float:
    return struct.unpack("!f", struct.pack("!f", value))[0]


def _dress_placement(source: Any, addition: Any, mode: str) -> Any:
    import FreeCAD as App

    if mode == "unchanged":
        return App.Placement(source)
    if mode == "replace_orientation":
        return App.Placement(source.Base, addition.Rotation)
    if mode == "translate":
        return App.Placement(source.Base + addition.Base, source.Rotation)
    if mode == "rotate":
        return App.Placement(
            source.Base,
            source.Rotation.multiply(addition.Rotation),
        )
    return source.multiply(addition)


def _expected_dress_waypoints(
    source: Any,
    record: TrajectoryStateRecord,
    spec: DressUpTrajectorySpec,
) -> tuple[Mapping[str, Any], ...]:
    addition = _placement(spec.placement)
    raw = tuple(source.Trajectory.Waypoints)
    if len(raw) != len(record.waypoints):
        raise NativeRobotTrajectoryError(
            "The source trajectory changed while preparing its dress-up."
        )
    result = []
    for waypoint, state_record in zip(raw, record.waypoints, strict=True):
        data = dict(state_record.data)
        if spec.use_speed:
            data["velocity_mm_per_s"] = _float32(spec.speed_mm_per_s)
        if spec.use_acceleration:
            data["acceleration_mm_per_s2"] = _float32(spec.acceleration_mm_per_s2)
        if spec.continuity_mode != "unchanged":
            data["continuous"] = spec.continuity_mode == "continuous"
        data["placement"] = robot_placement_summary(
            _dress_placement(waypoint.Pos, addition, spec.placement_mode),
            "expected dress-up waypoint",
        )
        result.append(data)
    return tuple(result)


def _unique_name(name: str, used: tuple[str, ...]) -> str:
    clean_name = "".join(
        character if character.isascii() and character.isalnum() else "_"
        for character in name
    )
    if clean_name and clean_name[0].isdigit():
        clean_name = f"_{clean_name[1:]}"
    if clean_name not in used:
        return clean_name
    highest = 0
    for candidate in used:
        if not candidate.startswith(clean_name):
            continue
        suffix = candidate[len(clean_name) :]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"{clean_name}{highest + 1}"


def _expected_compound_waypoints(
    records: tuple[TrajectoryStateRecord, ...],
) -> tuple[Mapping[str, Any], ...]:
    result = []
    used: tuple[str, ...] = ()
    for record in records:
        for waypoint in record.waypoints:
            data = dict(waypoint.data)
            data["index"] = len(result)
            data["name"] = _unique_name(str(data["name"]), used)
            used = (*used, str(data["name"]))
            result.append(data)
    return tuple(result)


def _edge_output_bound(source: Any, edges: tuple[str, ...], segmentation: float) -> int:
    shape = getattr(source, "Shape", None)
    get_element = getattr(shape, "getElement", None)
    if not callable(get_element):
        raise NativeRobotTrajectoryError(
            "The exact edge source has no readable Part shape."
        )
    waypoint_bound = 0
    for name in edges:
        try:
            edge = get_element(name)
            length = float(edge.Length)
            curve_name = type(edge.Curve).__name__
        except (AttributeError, ReferenceError, RuntimeError, TypeError) as exc:
            raise NativeRobotTrajectoryError(
                f"The exact source edge {name} is unavailable."
            ) from exc
        if (
            str(getattr(edge, "ShapeType", "") or "") != "Edge"
            or not math.isfinite(length)
            or length <= 0.0
        ):
            raise NativeRobotTrajectoryError(
                f"The exact source edge {name} is degenerate."
            )
        if curve_name not in _SUPPORTED_EDGE_CURVES:
            raise NativeRobotTrajectoryError(
                f"The exact source edge {name} has unsupported {curve_name} geometry."
            )
        waypoint_bound += (
            2
            if curve_name in {"Line", "LineSegment"}
            else max(1, math.ceil(length / segmentation)) + 2
        )
        if waypoint_bound > MAX_WAYPOINTS_PER_TRAJECTORY:
            raise NativeRobotTrajectoryError(
                "The requested edge trajectory exceeds the bounded waypoint count."
            )
    return waypoint_bound


def _require_total_bound(
    state: RobotTrajectoryState,
    target_index: int | None,
    output_bound: int,
) -> None:
    previous = 0 if target_index is None else len(state.records[target_index].waypoints)
    if output_bound > MAX_WAYPOINTS_PER_TRAJECTORY or (
        state.waypoint_count - previous + output_bound > MAX_TOTAL_WAYPOINTS
    ):
        raise NativeRobotTrajectoryError(
            "The requested feature output exceeds the bounded trajectory state."
        )


def preflight_trajectory_feature(
    document: Any,
    operation: str,
    spec: EdgeTrajectorySpec | DressUpTrajectorySpec | CompoundTrajectorySpec,
) -> PreparedTrajectoryFeature:
    if operation not in _FEATURE_TYPES:
        raise NativeRobotTrajectoryError(
            "The requested Robot trajectory feature is unavailable."
        )
    _require_clean_document(document)
    state = _capture_trajectories(document)
    if state.state_sha256 != spec.expected_trajectory_setup_state_sha256:
        raise NativeRobotTrajectoryError(
            "Trajectory state changed; read current state and retry."
        )
    target, target_index = _target(document, operation, spec, state)
    sources: tuple[Any, ...] = ()
    source_indices: tuple[int, ...] = ()
    edge_source = None
    edge_source_state = None
    edge_source_visibility = None
    expected_waypoints = None
    output_bound = 0
    if isinstance(spec, EdgeTrajectorySpec):
        edge_source = resolve_object(
            document,
            spec.source_ref,
            expected_types=("Part::Feature",),
        )
        edge_source_state = _capture_edge_source(edge_source)
        edge_source_visibility = trajectory_visibility(edge_source)
        if (
            edge_source_state.data["geometry"].get("kind") != "part"
            or edge_source_state.data["geometry"].get("null") is not False
            or edge_source_state.state_sha256 != spec.expected_source_state_sha256
            or not _usable_at_history(edge_source)
        ):
            raise NativeRobotTrajectoryError(
                "The exact edge source changed, is empty, or is inactive in History."
            )
        output_bound = _edge_output_bound(
            edge_source,
            spec.edges,
            spec.segmentation_mm,
        )
    elif isinstance(spec, DressUpTrajectorySpec):
        source, source_index = _trajectory_source(
            document,
            state,
            spec.source_ref,
            spec.expected_source_state_sha256,
        )
        if target is not None and _would_cycle(source, target):
            raise NativeRobotTrajectoryError(
                "The requested trajectory dress-up would create a dependency cycle."
            )
        sources = (source,)
        source_indices = (source_index,)
        expected_waypoints = _expected_dress_waypoints(
            source,
            state.records[source_index],
            spec,
        )
        output_bound = len(expected_waypoints)
    elif isinstance(spec, CompoundTrajectorySpec):
        resolved = tuple(
            _trajectory_source(
                document,
                state,
                source.trajectory_ref,
                source.expected_state_sha256,
            )
            for source in spec.sources
        )
        sources = tuple(item[0] for item in resolved)
        source_indices = tuple(item[1] for item in resolved)
        if target is not None and any(
            _would_cycle(source, target) for source in sources
        ):
            raise NativeRobotTrajectoryError(
                "The requested trajectory sequence would create a dependency cycle."
            )
        source_records = tuple(state.records[index] for index in source_indices)
        expected_waypoints = _expected_compound_waypoints(source_records)
        output_bound = len(expected_waypoints)
        if output_bound == 0:
            raise NativeRobotTrajectoryError(
                "A trajectory sequence requires at least one source waypoint."
            )
    _require_total_bound(state, target_index, output_bound)
    old_replaced_sources = (
        ()
        if target is None
        else tuple(getattr(target, "VibeCADTimelineReplacedInputs", ()) or ())
    )
    affected_presentations = (
        ()
        if operation == "edge2_trac"
        else replacement_presentations(old_replaced_sources, sources)
    )
    return PreparedTrajectoryFeature(
        operation,
        spec,
        state,
        target,
        target_index,
        sources,
        source_indices,
        edge_source,
        edge_source_state,
        edge_source_visibility,
        expected_waypoints,
        tuple(document.Objects),
        read_current_selection(document),
        _timeline_state(document),
        old_replaced_sources,
        affected_presentations,
    )


def _require_pre_mutation_boundary(
    document: Any,
    prepared: PreparedTrajectoryFeature,
) -> None:
    if tuple(document.Objects) != prepared.objects_before:
        raise NativeRobotTrajectoryError(
            "Document objects changed before the trajectory feature mutation."
        )
    if read_current_selection(document) != prepared.selection_before:
        raise NativeRobotTrajectoryError(
            "The human selection changed before the trajectory feature mutation."
        )
    if _timeline_state(document) != prepared.timeline_before:
        raise NativeRobotTrajectoryError(
            "Document History changed before the trajectory feature mutation."
        )
    if not same_robot_trajectory_state(
        prepared.trajectory_state,
        _capture_trajectories(document),
    ):
        raise NativeRobotTrajectoryError(
            "Trajectory state changed before the feature mutation."
        )
    if prepared.edge_source_state is not None and (
        _capture_edge_source(prepared.edge_source) != prepared.edge_source_state
        or trajectory_visibility(prepared.edge_source)
        != prepared.edge_source_visibility
    ):
        raise NativeRobotTrajectoryError(
            "The exact edge source changed before the feature mutation."
        )


def _same_placement(first: Any, second: Any) -> bool:
    try:
        return bool(first.isSame(second, 1.0e-12))
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return robot_placement_summary(first, "first placement") == (
            robot_placement_summary(second, "second placement")
        )


def _raw_feature_matches(prepared: PreparedTrajectoryFeature, target: Any) -> bool:
    spec = prepared.spec
    if isinstance(spec, EdgeTrajectorySpec):
        try:
            source, edges = target.Source
            return bool(
                source is prepared.edge_source
                and tuple(edges) == spec.edges
                and math.isclose(float(target.SegValue), spec.segmentation_mm)
                and bool(target.UseRotation) is spec.use_rotation
                and not tuple(
                    getattr(target, "VibeCADTimelineReplacedInputs", ()) or ()
                )
            )
        except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
            return False
    if isinstance(spec, DressUpTrajectorySpec):
        try:
            return bool(
                target.Source is prepared.sources[0]
                and bool(target.UseSpeed) is spec.use_speed
                and math.isclose(float(target.Speed), spec.speed_mm_per_s)
                and bool(target.UseAcceleration) is spec.use_acceleration
                and math.isclose(
                    float(target.Acceleration),
                    spec.acceleration_mm_per_s2,
                )
                and str(target.ContType) == _CONTINUITY_PROPERTIES[spec.continuity_mode]
                and str(target.AddType) == _PLACEMENT_PROPERTIES[spec.placement_mode]
                and _same_placement(target.PosAdd, _placement(spec.placement))
                and tuple(getattr(target, "VibeCADTimelineReplacedInputs", ()) or ())
                == prepared.sources
            )
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            return False
    try:
        return bool(
            tuple(target.Source) == prepared.sources
            and tuple(getattr(target, "VibeCADTimelineReplacedInputs", ()) or ())
            == prepared.sources
        )
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def trajectory_feature_is_noop(prepared: PreparedTrajectoryFeature) -> bool:
    if not isinstance(prepared, PreparedTrajectoryFeature):
        raise TypeError("prepared must be a PreparedTrajectoryFeature")
    if prepared.target is None or not _raw_feature_matches(prepared, prepared.target):
        return False
    record = prepared.trajectory_state.records[prepared.target_index]
    if prepared.expected_waypoints is None:
        return bool(record.waypoints)
    if any(
        trajectory_visibility(presentation) != expected
        for presentation, expected in expected_replacement_visibility(
            prepared,
            prepared.target,
        )
    ):
        return False
    return tuple(dict(item.data) for item in record.waypoints) == tuple(
        dict(item) for item in prepared.expected_waypoints
    )


def _configure(prepared: PreparedTrajectoryFeature, target: Any) -> None:
    spec = prepared.spec
    if isinstance(spec, EdgeTrajectorySpec):
        target.Source = (prepared.edge_source, list(spec.edges))
        target.SegValue = spec.segmentation_mm
        target.UseRotation = spec.use_rotation
        return
    if isinstance(spec, DressUpTrajectorySpec):
        target.Source = prepared.sources[0]
        target.Speed = spec.speed_mm_per_s
        target.UseSpeed = spec.use_speed
        target.Acceleration = spec.acceleration_mm_per_s2
        target.UseAcceleration = spec.use_acceleration
        target.ContType = _CONTINUITY_PROPERTIES[spec.continuity_mode]
        target.PosAdd = _placement(spec.placement)
        target.AddType = _PLACEMENT_PROPERTIES[spec.placement_mode]
        return
    target.Source = list(prepared.sources)


def _set_replaced_inputs(prepared: PreparedTrajectoryFeature, target: Any) -> None:
    if prepared.operation == "edge2_trac":
        return
    import PartGui

    if not PartGui.setModelingReplacedInputs(target, prepared.sources):
        raise NativeRobotTrajectoryError(
            "The trajectory feature could not publish its exact replaced inputs."
        )


def mutate_trajectory_feature(
    document: Any,
    *,
    prepared: PreparedTrajectoryFeature,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedTrajectoryFeature):
        raise TypeError("prepared must be a PreparedTrajectoryFeature")
    _require_pre_mutation_boundary(document, prepared)
    type_id, base_name = _FEATURE_TYPES[prepared.operation]
    target = prepared.target
    created = target is None
    if created:
        import Robot  # noqa: F401 - loads Robot document factories

        target = document.addObject(type_id, base_name)
        if target is None or str(getattr(target, "TypeId", "") or "") != type_id:
            raise NativeRobotTrajectoryError(
                "The Robot trajectory feature factory returned the wrong object type."
            )
    _configure(prepared, target)
    if created:
        document.publishProvisionalTimelineOperationBlock(target, (), ())
        target.Visibility = True
    _set_replaced_inputs(prepared, target)
    visibility_after = reconcile_replacement_visibility(prepared, target)
    identities = (object_identity(target),)
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "target": target,
            "visibility_after": visibility_after,
        },
        recompute_targets=(target,),
        created=identities if created else (),
        changed=() if created else identities,
        replaced=tuple(object_identity(source) for source in prepared.sources),
    )


def _record_without_visibility(record: TrajectoryStateRecord) -> dict[str, Any]:
    data = deepcopy(dict(record.data))
    data["presentation"].pop("visible", None)
    return {
        "data": data,
        "waypoints": [dict(item.data) for item in record.waypoints],
    }


def _target_stable_metadata(record: TrajectoryStateRecord) -> dict[str, Any]:
    data = deepcopy(dict(record.data))
    for name in (
        "feature",
        "waypoint_count",
        "waypoints_state_sha256",
        "length_mm",
        "duration_seconds",
    ):
        data.pop(name, None)
    data["timeline"].pop("replaced_inputs", None)
    data["presentation"].pop("visible", None)
    return data


def _verify_timeline(
    document: Any,
    prepared: PreparedTrajectoryFeature,
    target: Any,
) -> None:
    before = prepared.timeline_before
    after = _timeline_state(document)
    created = prepared.target is None
    if after.timeline is None or (
        before.timeline is not None and after.timeline is not before.timeline
    ):
        raise NativeRobotTrajectoryError(
            "The trajectory feature changed the History identity."
        )
    expected_operations = (*before.operations, target) if created else before.operations
    if after.operations != expected_operations:
        raise NativeRobotTrajectoryError(
            "The trajectory feature changed unrelated History operations."
        )
    allowed = set(prepared.old_replaced_sources) | set(prepared.sources)
    for index, operation in enumerate(before.operations):
        if (
            operation not in allowed
            and after.visibility[index] != before.visibility[index]
        ):
            raise NativeRobotTrajectoryError(
                "The trajectory feature changed unrelated History presentation."
            )
    if created and (
        len(after.visibility) != len(before.visibility) + 1 or not after.visibility[-1]
    ):
        raise NativeRobotTrajectoryError(
            "The created trajectory feature is not visible at the end of History."
        )
    if not created and len(after.visibility) != len(before.visibility):
        raise NativeRobotTrajectoryError(
            "Trajectory feature editing changed the History size."
        )


def _verify_record_isolation(
    prepared: PreparedTrajectoryFeature,
    state: RobotTrajectoryState,
    target_index: int,
) -> None:
    allowed_visibility = set(prepared.replacement_presentations)
    before_by_object = {
        trajectory: record
        for trajectory, record in zip(
            prepared.trajectory_state.trajectories,
            prepared.trajectory_state.records,
            strict=True,
        )
    }
    for index, (trajectory, after) in enumerate(
        zip(state.trajectories, state.records, strict=True)
    ):
        before = before_by_object.get(trajectory)
        if before is None or index == target_index:
            continue
        if trajectory in allowed_visibility:
            if _record_without_visibility(before) != _record_without_visibility(after):
                raise NativeRobotTrajectoryError(
                    "The trajectory feature changed a source beyond visibility."
                )
        elif before.state_sha256 != after.state_sha256:
            raise NativeRobotTrajectoryError(
                "The trajectory feature changed an unrelated trajectory."
            )


def _verify_target(
    prepared: PreparedTrajectoryFeature,
    target: Any,
    record: TrajectoryStateRecord,
) -> None:
    type_id, _base_name = _FEATURE_TYPES[prepared.operation]
    if (
        str(getattr(target, "TypeId", "") or "") != type_id
        or not target.isValid()
        or bool(target.Suppressed)
        or not _usable_at_history(target)
        or str(getattr(target, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(target, "VibeCADTimelineOwner", None) is not None
        or not _raw_feature_matches(prepared, target)
    ):
        raise NativeRobotTrajectoryError(
            "The trajectory feature failed its exact object postcondition."
        )
    if prepared.target is None:
        if str(target.Label) != str(target.Name):
            raise NativeRobotTrajectoryError(
                "The created trajectory feature has an incorrect durable label."
            )
        if record.data["presentation"]["visible"] is not True:
            raise NativeRobotTrajectoryError(
                "The created trajectory feature is not durably visible."
            )
    else:
        before = prepared.trajectory_state.records[prepared.target_index]
        if _target_stable_metadata(before) != _target_stable_metadata(record):
            raise NativeRobotTrajectoryError(
                "Trajectory feature editing changed unrelated target metadata."
            )
    if prepared.expected_waypoints is not None:
        if tuple(dict(item.data) for item in record.waypoints) != tuple(
            dict(item) for item in prepared.expected_waypoints
        ):
            raise NativeRobotTrajectoryError(
                "The trajectory feature output does not match its exact source semantics."
            )
    elif not record.waypoints:
        raise NativeRobotTrajectoryError(
            "The edge trajectory did not produce any waypoints."
        )


def _result(
    prepared: PreparedTrajectoryFeature,
    target: Any,
    state: RobotTrajectoryState,
    target_index: int,
    *,
    changed: bool,
) -> dict[str, Any]:
    record = state.records[target_index]
    result = {
        "operation": prepared.operation,
        "mode": prepared.spec.target.mode,
        "changed": changed,
        "trajectory": object_reference(target),
        "feature": dict(record.data["feature"]),
        "waypoint_count": len(record.waypoints),
        "trajectory_state_sha256": record.state_sha256,
        "trajectory_setup_state_sha256": state.state_sha256,
    }
    if prepared.edge_source is not None:
        result["source"] = object_reference(prepared.edge_source)
    else:
        result["sources"] = [object_reference(source) for source in prepared.sources]
    return result


def verify_trajectory_feature(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    target = draft.value["target"]
    if not isinstance(prepared, PreparedTrajectoryFeature):
        raise TypeError("Trajectory feature mutation returned an invalid draft.")
    if read_current_selection(document) != prepared.selection_before:
        raise NativeRobotTrajectoryError(
            "The trajectory feature changed the human selection."
        )
    expected_objects = set(prepared.objects_before)
    if prepared.target is None:
        expected_objects.add(target)
        if prepared.timeline_before.timeline is None:
            expected_objects.add(document.getObject("VibeCADTimeline"))
    if None in expected_objects or set(document.Objects) != expected_objects:
        raise NativeRobotTrajectoryError(
            "The trajectory feature changed unrelated document objects."
        )
    if prepared.edge_source_state is not None and (
        _capture_edge_source(prepared.edge_source) != prepared.edge_source_state
        or trajectory_visibility(prepared.edge_source)
        != prepared.edge_source_visibility
    ):
        raise NativeRobotTrajectoryError(
            "The trajectory feature changed its exact edge source."
        )
    _verify_timeline(document, prepared, target)
    if any(
        trajectory_visibility(presentation) != visible
        for presentation, visible in draft.value["visibility_after"]
    ):
        raise NativeRobotTrajectoryError(
            "The trajectory feature has incorrect replacement presentation."
        )
    state = _capture_trajectories(document)
    expected_trajectories = (
        (*prepared.trajectory_state.trajectories, target)
        if prepared.target is None
        else prepared.trajectory_state.trajectories
    )
    if state.trajectories != expected_trajectories:
        raise NativeRobotTrajectoryError(
            "The trajectory feature changed trajectory object identities."
        )
    target_index = state.trajectories.index(target)
    _verify_record_isolation(prepared, state, target_index)
    _verify_target(prepared, target, state.records[target_index])
    return _result(prepared, target, state, target_index, changed=True)


def verify_trajectory_feature_noop(
    document: Any,
    prepared: PreparedTrajectoryFeature,
) -> dict[str, Any]:
    _require_pre_mutation_boundary(document, prepared)
    if prepared.target is None or not trajectory_feature_is_noop(prepared):
        raise NativeRobotTrajectoryError(
            "The requested trajectory feature edit is not a verified no-op."
        )
    return _result(
        prepared,
        prepared.target,
        prepared.trajectory_state,
        prepared.target_index,
        changed=False,
    )
