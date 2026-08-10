# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact bounded robot-definition state for Assemble Native tools."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Mapping

from VibeCADNativeInput import NativeInputError, inspect_native_input_file
from VibeCADNativeTargets import object_reference


MAX_ROBOTS = 128
MAX_VISIBLE_ROBOTS = 32
MAX_ROBOT_VRML_BYTES = 512 * 1024 * 1024
MAX_ROBOT_KINEMATIC_BYTES = 1024 * 1024


class NativeRobotStateError(RuntimeError):
    """The active document's exact robot state cannot be represented safely."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_ROBOT_STATE_FAILED",
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class RobotStateRecord:
    robot: Any
    data: Mapping[str, Any]
    state_sha256: str

    def summary(self) -> dict[str, Any]:
        return {**dict(self.data), "state_sha256": self.state_sha256}


@dataclass(frozen=True, slots=True)
class RobotSetupState:
    robots: tuple[Any, ...]
    records: tuple[RobotStateRecord, ...]
    state_sha256: str

    def summary(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "available": True,
            "state_sha256": self.state_sha256,
            "robot_count": len(self.robots),
            "robots": [
                record.summary() for record in self.records[:MAX_VISIBLE_ROBOTS]
            ],
        }
        if len(self.records) > MAX_VISIBLE_ROBOTS:
            result["robots_truncated"] = True
        return result


def _finite(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise NativeRobotStateError(f"A robot returned malformed {field} state.")
    try:
        result = float(getattr(value, "Value", value))
    except (TypeError, ValueError, OverflowError) as exc:
        raise NativeRobotStateError(
            f"A robot returned malformed {field} state."
        ) from exc
    if not math.isfinite(result):
        raise NativeRobotStateError(f"A robot returned non-finite {field} state.")
    if result == 0.0:
        return 0.0
    # FCStd stores placements at lower precision than an in-memory double.
    # Canonicalize only the sub-picometre tail so a normal save/reopen does not
    # make an unchanged Robot look stale.
    return round(result, 15)


def _placement(value: Any, field: str) -> dict[str, list[float]]:
    try:
        base = value.Base
        quaternion = tuple(value.Rotation.Q)
        result = {
            "position_mm": [
                _finite(base.x, field),
                _finite(base.y, field),
                _finite(base.z, field),
            ],
            "quaternion_xyzw": [_finite(component, field) for component in quaternion],
        }
    except (AttributeError, ReferenceError, RuntimeError, TypeError) as exc:
        raise NativeRobotStateError(
            f"A robot returned malformed {field} placement."
        ) from exc
    if len(result["quaternion_xyzw"]) != 4:
        raise NativeRobotStateError(f"A robot returned malformed {field} placement.")
    return result


def _linked_object(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    document = getattr(value, "Document", None)
    name = str(getattr(value, "Name", "") or "")
    uid = str(getattr(document, "Uid", "") or "")
    if not uid or not name:
        raise NativeRobotStateError("A robot tool-shape link is not durable.")
    return {
        "document_uid": uid,
        "object_name": name,
        "object_id": int(getattr(value, "ID", -1)),
        "type_id": str(getattr(value, "TypeId", "") or ""),
    }


def _definition_file(
    robot: Any, property_name: str, maximum_bytes: int
) -> dict[str, Any]:
    try:
        value = str(getattr(robot, property_name) or "")
        return inspect_native_input_file(value, maximum_bytes=maximum_bytes)
    except (AttributeError, NativeInputError, OSError, RuntimeError) as exc:
        raise NativeRobotStateError(
            f"Robot {property_name} content is unavailable."
        ) from exc


def _robot_record(robot: Any) -> RobotStateRecord:
    if (
        str(getattr(robot, "TypeId", "") or "") != "Robot::RobotObject"
        or getattr(robot, "Document", None) is None
        or not str(getattr(robot, "Name", "") or "")
    ):
        raise NativeRobotStateError("A robot object is not durably attached.")
    home_values = tuple(getattr(robot, "Home", ()) or ())
    if len(home_values) > 6:
        raise NativeRobotStateError("A robot home position has more than six axes.")
    owner = getattr(robot, "VibeCADTimelineOwner", None)
    replaced = tuple(getattr(robot, "VibeCADTimelineReplacedInputs", ()) or ())
    view = getattr(robot, "ViewObject", None)
    data: dict[str, Any] = {
        "object": object_reference(robot),
        "object_id": int(getattr(robot, "ID", -1)),
        "label": str(getattr(robot, "Label", "") or "")[:160],
        "definition": {
            "visual": _definition_file(
                robot,
                "RobotVrmlFile",
                MAX_ROBOT_VRML_BYTES,
            ),
            "kinematics": _definition_file(
                robot,
                "RobotKinematicFile",
                MAX_ROBOT_KINEMATIC_BYTES,
            ),
        },
        "axes_degrees": [
            _finite(getattr(robot, f"Axis{index}"), f"Axis{index}")
            for index in range(1, 7)
        ],
        "home_degrees": [_finite(value, "Home") for value in home_values],
        "base": _placement(robot.Base, "Base"),
        "tool": _placement(robot.Tool, "Tool"),
        "tool_base": _placement(robot.ToolBase, "ToolBase"),
        "tcp": _placement(robot.Tcp, "Tcp"),
        "tool_shape": _linked_object(getattr(robot, "ToolShape", None)),
        "error": str(getattr(robot, "Error", "") or "")[:512],
        "suppressed": bool(getattr(robot, "Suppressed", False)),
        "valid": bool(robot.isValid()),
        "timeline": {
            "role": str(getattr(robot, "VibeCADTimelineRole", "") or ""),
            "owner": _linked_object(owner),
            "replaced_inputs": [_linked_object(value) for value in replaced],
        },
        "presentation": {
            "visible": None if view is None else bool(view.Visibility),
            "manipulator": (
                None
                if view is None or not hasattr(view, "Manipulator")
                else bool(view.Manipulator)
            ),
        },
    }
    encoded = json.dumps(
        data,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return RobotStateRecord(robot, data, hashlib.sha256(encoded).hexdigest())


def capture_robot_setup_state(document: Any) -> RobotSetupState:
    """Capture every live Robot object without returning any filesystem path."""

    robots = tuple(
        obj
        for obj in tuple(getattr(document, "Objects", ()) or ())
        if str(getattr(obj, "TypeId", "") or "") == "Robot::RobotObject"
    )
    if len(robots) > MAX_ROBOTS:
        raise NativeRobotStateError(
            f"The active document exceeds the {MAX_ROBOTS}-robot Native bound."
        )
    records = tuple(_robot_record(robot) for robot in robots)
    encoded = json.dumps(
        [record.summary() for record in records],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return RobotSetupState(
        robots,
        records,
        hashlib.sha256(encoded).hexdigest(),
    )


def same_robot_setup_state(first: RobotSetupState, second: RobotSetupState) -> bool:
    return bool(
        isinstance(first, RobotSetupState)
        and isinstance(second, RobotSetupState)
        and first.robots == second.robots
        and tuple(record.data for record in first.records)
        == tuple(record.data for record in second.records)
        and first.state_sha256 == second.state_sha256
    )
