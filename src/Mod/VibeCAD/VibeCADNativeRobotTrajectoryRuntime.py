# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Robot trajectory operations."""

from __future__ import annotations

from functools import partial
import math
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRobotIntent import expand_robot_trajectory_intent
from VibeCADNativeRobotTrajectory import (
    NativeRobotTrajectoryError,
    append_waypoint,
    create_trajectory,
    preflight_position_waypoint,
    preflight_robot_waypoint,
    preflight_trajectory_create,
    prepare_position_waypoint_spec,
    prepare_robot_waypoint_spec,
    prepare_trajectory_create_spec,
    verify_appended_waypoint,
    verify_created_trajectory,
)
from VibeCADNativeRobotTrajectoryFeatures import (
    mutate_trajectory_feature,
    preflight_trajectory_feature,
    trajectory_feature_is_noop,
    verify_trajectory_feature,
    verify_trajectory_feature_noop,
)
from VibeCADNativeRobotTrajectoryFeatureSpecs import (
    prepare_compound_trajectory_spec,
    prepare_dress_up_trajectory_spec,
    prepare_edge_trajectory_spec,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket, NativeRevisionConflict


_FEATURE_ARGUMENTS = {
    "edge2_trac": frozenset(
        {
            "mode",
            "target",
            "source",
            "edges",
            "segmentation_mm",
            "use_rotation",
        }
    ),
    "trajectory_dress_up": frozenset(
        {
            "mode",
            "target",
            "source",
            "use_speed",
            "speed_mm_per_s",
            "use_acceleration",
            "acceleration_mm_per_s2",
            "continuity_mode",
            "placement",
            "placement_mode",
        }
    ),
    "trajectory_compound": frozenset(
        {
            "mode",
            "target",
            "sources",
        }
    ),
}
_FEATURE_PREPARERS = {
    "edge2_trac": prepare_edge_trajectory_spec,
    "trajectory_dress_up": prepare_dress_up_trajectory_spec,
    "trajectory_compound": prepare_compound_trajectory_spec,
}
_FEATURE_TITLES = {
    "edge2_trac": "Robot Edge Trajectory",
    "trajectory_dress_up": "Robot Trajectory Modifier",
    "trajectory_compound": "Robot Trajectory Sequence",
}
_PATH_MOTION_FIELDS = frozenset(
    {
        "path",
        "speed_limit_mm_per_s",
        "remove_speed_limit",
        "acceleration_limit_mm_per_s2",
        "remove_acceleration_limit",
        "continuity_mode",
        "placement",
        "placement_mode",
    }
)
_CONTINUITY_FROM_PROPERTY = {
    "DontChange": "unchanged",
    "Continues": "continuous",
    "Discontinues": "discontinuous",
}
_PLACEMENT_MODE_FROM_PROPERTY = {
    "DontChange": "unchanged",
    "UseOrientation": "replace_orientation",
    "AddPosition": "translate",
    "AddOrintation": "rotate",
    "AddPositionAndOrientation": "transform",
}
_MISSING = object()


def _identity_placement_request() -> dict[str, Any]:
    return {
        "origin_mm": {"x": 0.0, "y": 0.0, "z": 0.0},
        "rotation": {
            "axis": {"x": 0.0, "y": 0.0, "z": 1.0},
            "angle_degrees": 0.0,
        },
    }


def _clean_float(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeRobotTrajectoryError(f"{field} is not readable.") from exc
    if not math.isfinite(result):
        raise NativeRobotTrajectoryError(f"{field} is not finite.")
    return 0.0 if result == 0.0 else result


def _placement_request(value: Any) -> dict[str, Any]:
    try:
        base = value.Base
        quaternion = tuple(float(component) for component in value.Rotation.Q)
    except (AttributeError, TypeError, ValueError) as exc:
        raise NativeRobotTrajectoryError(
            "The existing path motion placement is unreadable."
        ) from exc
    if len(quaternion) != 4 or any(not math.isfinite(value) for value in quaternion):
        raise NativeRobotTrajectoryError(
            "The existing path motion rotation is unreadable."
        )
    x, y, z, w = quaternion
    magnitude = math.sqrt(x * x + y * y + z * z + w * w)
    if magnitude < 1.0e-12:
        raise NativeRobotTrajectoryError(
            "The existing path motion rotation is invalid."
        )
    x, y, z, w = (component / magnitude for component in (x, y, z, w))
    vector_magnitude = math.sqrt(x * x + y * y + z * z)
    if vector_magnitude < 1.0e-12:
        axis = (0.0, 0.0, 1.0)
        angle_degrees = 0.0
    else:
        axis = (
            x / vector_magnitude,
            y / vector_magnitude,
            z / vector_magnitude,
        )
        angle_degrees = math.degrees(2.0 * math.atan2(vector_magnitude, w))
    return {
        "origin_mm": {
            "x": _clean_float(base.x, "Path motion X placement"),
            "y": _clean_float(base.y, "Path motion Y placement"),
            "z": _clean_float(base.z, "Path motion Z placement"),
        },
        "rotation": {
            "axis": dict(zip("xyz", axis, strict=True)),
            "angle_degrees": _clean_float(
                angle_degrees,
                "Path motion rotation angle",
            ),
        },
    }


def _require_robot_path(target: Any, field: str) -> Any:
    derived = getattr(target, "isDerivedFrom", None)
    if target is None or not callable(derived) or not bool(
        derived("Robot::TrajectoryObject")
    ):
        raise NativeRobotTrajectoryError(f"{field} is not a Robot path.")
    return target


def _robot_path(document: Any, value: Any, field: str) -> Any:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise NativeRobotTrajectoryError(f"{field} must identify one Robot path.")
    name = str(value.get("object_name") or "")
    target = document.getObject(name) if name else None
    return _require_robot_path(target, field)


def _expand_path_motion_request(
    document: Any,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve create/update semantics from one exact Robot path reference."""

    if not isinstance(values, Mapping):
        raise NativeRobotTrajectoryError("Path motion arguments must be an object.")
    if "path" not in values or not set(values).issubset(_PATH_MOTION_FIELDS):
        raise NativeRobotTrajectoryError("Path motion arguments have incorrect fields.")
    requested = dict(values)
    path = _robot_path(document, requested.pop("path"), "path")
    path_reference = {"object_name": str(path.Name)}
    if str(getattr(path, "TypeId", "") or "") == "Robot::TrajectoryDressUpObject":
        source = _require_robot_path(
            getattr(path, "Source", None),
            "Path motion source",
        )
        defaults = {
            "use_speed": bool(path.UseSpeed),
            "speed_mm_per_s": _clean_float(path.Speed, "Path motion speed"),
            "use_acceleration": bool(path.UseAcceleration),
            "acceleration_mm_per_s2": _clean_float(
                path.Acceleration,
                "Path motion acceleration",
            ),
            "continuity_mode": _CONTINUITY_FROM_PROPERTY.get(str(path.ContType)),
            "placement": _placement_request(path.PosAdd),
            "placement_mode": _PLACEMENT_MODE_FROM_PROPERTY.get(str(path.AddType)),
        }
        if defaults["continuity_mode"] is None or defaults["placement_mode"] is None:
            raise NativeRobotTrajectoryError(
                "The existing path motion settings are unsupported."
            )
        mode = "edit"
        target = path_reference
        source_reference = {"object_name": str(source.Name)}
    else:
        defaults = {
            "use_speed": False,
            "speed_mm_per_s": 1000.0,
            "use_acceleration": False,
            "acceleration_mm_per_s2": 1000.0,
            "continuity_mode": "unchanged",
            "placement": _identity_placement_request(),
            "placement_mode": "unchanged",
        }
        mode = "create"
        target = None
        source_reference = path_reference
    speed_limit = requested.pop("speed_limit_mm_per_s", _MISSING)
    remove_speed_limit = requested.pop("remove_speed_limit", _MISSING)
    if speed_limit is not _MISSING and remove_speed_limit is not _MISSING:
        raise NativeRobotTrajectoryError(
            "Path motion cannot set and remove the speed limit together."
        )
    if speed_limit is not _MISSING:
        defaults["use_speed"] = True
        defaults["speed_mm_per_s"] = speed_limit
    elif remove_speed_limit is not _MISSING:
        if remove_speed_limit is not True:
            raise NativeRobotTrajectoryError("remove_speed_limit must be true.")
        defaults["use_speed"] = False
    acceleration_limit = requested.pop("acceleration_limit_mm_per_s2", _MISSING)
    remove_acceleration_limit = requested.pop(
        "remove_acceleration_limit",
        _MISSING,
    )
    if (
        acceleration_limit is not _MISSING
        and remove_acceleration_limit is not _MISSING
    ):
        raise NativeRobotTrajectoryError(
            "Path motion cannot set and remove the acceleration limit together."
        )
    if acceleration_limit is not _MISSING:
        defaults["use_acceleration"] = True
        defaults["acceleration_mm_per_s2"] = acceleration_limit
    elif remove_acceleration_limit is not _MISSING:
        if remove_acceleration_limit is not True:
            raise NativeRobotTrajectoryError(
                "remove_acceleration_limit must be true."
            )
        defaults["use_acceleration"] = False
    return {
        "mode": mode,
        "target": target,
        "source": source_reference,
        **defaults,
        **requested,
    }


def _require_current_ticket(
    context: NativeRuntimeContext,
    ticket: NativeCallTicket,
) -> None:
    if not isinstance(ticket, NativeCallTicket):
        raise TypeError("ticket must be a NativeCallTicket")
    current = context.state.current_revision(context.document_uid)
    if current != ticket.expected_revision:
        raise NativeRevisionConflict(ticket.expected_revision, current)


class NativeRobotTrajectoryRuntime:
    """Mutate trajectories only on an exact frozen Robot-capable surface."""

    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def set_path_motion(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        self._context.guard()
        _require_current_ticket(self._context, ticket)
        values = _expand_path_motion_request(self._context.document, arguments)
        return self.mutate_trajectory(
            {"operation": "trajectory_dress_up", **values},
            ticket=ticket,
        )

    def mutate_trajectory(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(
            arguments,
            {
                "create_trajectory": frozenset(
                    {"label"}
                ),
                "insert_robot_waypoint": frozenset(
                    {"trajectory", "robot"}
                ),
                "insert_position_waypoint": frozenset(
                    {"trajectory", "position_mm"}
                ),
                **_FEATURE_ARGUMENTS,
            },
        )
        self._context.guard()
        _require_current_ticket(self._context, ticket)
        values = expand_robot_trajectory_intent(
            self._context.document,
            self._context.document_uid,
            operation,
            values,
        )
        if operation in _FEATURE_ARGUMENTS:
            spec = _FEATURE_PREPARERS[operation](
                self._context.document_uid,
                values,
            )
            prepared = preflight_trajectory_feature(
                self._context.document,
                operation,
                spec,
            )
            if trajectory_feature_is_noop(prepared):
                return verify_trajectory_feature_noop(
                    self._context.document,
                    prepared,
                )
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name=(
                    f"{spec.target.mode.title()} Native {_FEATURE_TITLES[operation]}"
                ),
                mutate=partial(mutate_trajectory_feature, prepared=prepared),
                verify=verify_trajectory_feature,
            )
        if operation == "create_trajectory":
            prepared = preflight_trajectory_create(
                self._context.document,
                prepare_trajectory_create_spec(values),
            )
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Robot Trajectory",
                mutate=partial(create_trajectory, prepared=prepared),
                verify=verify_created_trajectory,
            )
        if operation == "insert_robot_waypoint":
            prepared = preflight_robot_waypoint(
                self._context.document,
                prepare_robot_waypoint_spec(self._context.document_uid, values),
            )
        elif operation == "insert_position_waypoint":
            prepared = preflight_position_waypoint(
                self._context.document,
                prepare_position_waypoint_spec(self._context.document_uid, values),
            )
        else:
            raise NativeRobotTrajectoryError(
                "The requested Robot trajectory operation is not implemented."
            )
        return run_immediate_mutation(
            self._context,
            ticket=ticket,
            transaction_name="Insert Native Robot Waypoint",
            mutate=partial(append_waypoint, prepared=prepared),
            verify=verify_appended_waypoint,
        )
