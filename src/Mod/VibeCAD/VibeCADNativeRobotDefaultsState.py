# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact process-session defaults used by Robot waypoint commands."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import sys
from typing import Any, Mapping


MAX_ROBOT_MOTION_VALUE = 1_000_000_000.0


class NativeRobotDefaultsStateError(RuntimeError):
    """The Robot waypoint defaults cannot be represented safely."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_ROBOT_DEFAULTS_STATE_FAILED",
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class RobotWaypointDefaultsState:
    data: Mapping[str, Any]
    state_sha256: str

    def summary(self) -> dict[str, Any]:
        return {
            "available": True,
            "scope": "application_session",
            "durable": False,
            **dict(self.data),
            "state_sha256": self.state_sha256,
        }


def robot_defaults_namespace() -> Any:
    namespace = sys.modules.get("__main__")
    if namespace is None:
        raise NativeRobotDefaultsStateError(
            "The Robot waypoint-default session is unavailable."
        )
    return namespace


def _application() -> Any:
    import FreeCAD as App

    return App


def _value(namespace: Any, name: str) -> Any:
    if isinstance(namespace, Mapping):
        if name not in namespace:
            raise NativeRobotDefaultsStateError(
                "Robot waypoint defaults are not initialized in this session."
            )
        return namespace[name]
    try:
        return getattr(namespace, name)
    except AttributeError as exc:
        raise NativeRobotDefaultsStateError(
            "Robot waypoint defaults are not initialized in this session."
        ) from exc


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise NativeRobotDefaultsStateError(f"The Robot {label} default is malformed.")
    try:
        result = float(getattr(value, "Value", value))
    except (TypeError, ValueError, OverflowError) as exc:
        raise NativeRobotDefaultsStateError(
            f"The Robot {label} default is malformed."
        ) from exc
    if not math.isfinite(result):
        raise NativeRobotDefaultsStateError(f"The Robot {label} default is non-finite.")
    return 0.0 if result == 0.0 else result


def _motion_quantity(
    value: Any,
    unit: str,
    label: str,
    application: Any,
) -> float:
    try:
        converted = application.Units.Quantity(str(value)).getValueAs(unit)
    except Exception as exc:
        raise NativeRobotDefaultsStateError(
            f"The Robot {label} default must use {unit} dimensions."
        ) from exc
    result = _finite(converted, label)
    if not 0.0 < result <= MAX_ROBOT_MOTION_VALUE:
        raise NativeRobotDefaultsStateError(
            f"The Robot {label} default is outside its supported range."
        )
    return result


def _orientation(rotation: Any, displacement: Any) -> dict[str, Any]:
    try:
        quaternion = tuple(rotation.Q)
        position = (displacement.x, displacement.y, displacement.z)
    except (AttributeError, ReferenceError, RuntimeError, TypeError) as exc:
        raise NativeRobotDefaultsStateError(
            "The Robot orientation defaults are malformed."
        ) from exc
    if len(quaternion) != 4:
        raise NativeRobotDefaultsStateError(
            "The Robot orientation default has a malformed quaternion."
        )
    clean_quaternion = [_finite(value, "orientation") for value in quaternion]
    norm = math.sqrt(sum(value * value for value in clean_quaternion))
    if not math.isclose(norm, 1.0, rel_tol=0.0, abs_tol=1.0e-9):
        raise NativeRobotDefaultsStateError(
            "The Robot orientation default is not a unit rotation."
        )
    return {
        "displacement_mm": [_finite(value, "displacement") for value in position],
        "quaternion_xyzw": clean_quaternion,
    }


def capture_robot_waypoint_defaults(
    *,
    namespace: Any | None = None,
    application: Any | None = None,
) -> RobotWaypointDefaultsState:
    """Read the actual globals consumed by the two waypoint creation commands."""

    target = robot_defaults_namespace() if namespace is None else namespace
    app = _application() if application is None else application
    continuity = _value(target, "_DefCont")
    if type(continuity) is not bool:
        raise NativeRobotDefaultsStateError(
            "The Robot continuity default must be true or false."
        )
    data = {
        "orientation": _orientation(
            _value(target, "_DefOrientation"),
            _value(target, "_DefDisplacement"),
        ),
        "motion": {
            "speed_mm_per_s": _motion_quantity(
                _value(target, "_DefSpeed"),
                "mm/s",
                "speed",
                app,
            ),
            "continuous": continuity,
            "acceleration_mm_per_s2": _motion_quantity(
                _value(target, "_DefAcceleration"),
                "mm/s^2",
                "acceleration",
                app,
            ),
        },
    }
    encoded = json.dumps(
        data,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return RobotWaypointDefaultsState(data, hashlib.sha256(encoded).hexdigest())
