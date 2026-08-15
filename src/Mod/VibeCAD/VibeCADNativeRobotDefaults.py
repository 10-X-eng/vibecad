# SPDX-License-Identifier: LGPL-2.1-or-later

"""Stale-safe session mutation for Robot waypoint defaults."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeRobotDefaultsState import (
    MAX_ROBOT_MOTION_VALUE,
    NativeRobotDefaultsStateError,
    RobotWaypointDefaultsState,
    capture_robot_waypoint_defaults,
    robot_defaults_namespace,
)
from VibeCADNativeRobotSetup import NativeRobotSetupError
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeTargets import read_current_selection


_DEFAULT_NAMES = (
    "_DefSpeed",
    "_DefCont",
    "_DefAcceleration",
    "_DefOrientation",
    "_DefDisplacement",
)


@dataclass(frozen=True, slots=True)
class RobotOrientationDefaultsSpec:
    expected_state_sha256: str
    displacement_mm: tuple[float, float, float]
    rotation_axis: tuple[float, float, float]
    angle_degrees: float


@dataclass(frozen=True, slots=True)
class RobotMotionDefaultsSpec:
    expected_state_sha256: str
    speed_mm_per_s: float
    continuous: bool
    acceleration_mm_per_s2: float


@dataclass(frozen=True, slots=True)
class _DocumentBoundary:
    objects: tuple[Any, ...]
    selection: Mapping[str, Any]
    timeline: Any | None
    operations: tuple[Any, ...]
    visibility: tuple[bool, ...]
    undo_count: int
    structural_revision: int


def _digest(value: Any) -> str:
    result = str(value or "")
    if len(result) != 64 or any(
        character not in "0123456789abcdef" for character in result
    ):
        raise NativeRobotSetupError(
            "expected_defaults_state_sha256 must be one lowercase SHA-256 digest."
        )
    return result


def _number(
    value: Any,
    label: str,
    *,
    minimum: float,
    maximum: float,
    exclusive_minimum: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NativeRobotSetupError(f"Robot {label} must be a finite number.")
    result = float(value)
    valid_minimum = result > minimum if exclusive_minimum else result >= minimum
    if not math.isfinite(result) or not valid_minimum or result > maximum:
        raise NativeRobotSetupError(f"Robot {label} is outside its supported range.")
    return 0.0 if result == 0.0 else result


def _vector(value: Any, label: str) -> tuple[float, float, float]:
    if not isinstance(value, Mapping) or set(value) != {"x", "y", "z"}:
        raise NativeRobotSetupError(f"Robot {label} must be an exact XYZ vector.")
    return tuple(
        _number(
            value[axis],
            f"{label} {axis}",
            minimum=-1_000_000.0,
            maximum=1_000_000.0,
        )
        for axis in ("x", "y", "z")
    )


def prepare_robot_orientation_defaults(
    values: Mapping[str, Any],
) -> RobotOrientationDefaultsSpec:
    if not isinstance(values, Mapping) or set(values) != {
        "expected_defaults_state_sha256",
        "placement",
    }:
        raise NativeRobotSetupError("Robot orientation-default fields are incorrect.")
    placement = values["placement"]
    if not isinstance(placement, Mapping) or set(placement) != {
        "origin_mm",
        "rotation",
    }:
        raise NativeRobotSetupError(
            "Robot orientation defaults require one exact placement."
        )
    rotation = placement["rotation"]
    if not isinstance(rotation, Mapping) or set(rotation) != {
        "axis",
        "angle_degrees",
    }:
        raise NativeRobotSetupError(
            "Robot orientation defaults require one exact axis-angle rotation."
        )
    axis = _vector(rotation["axis"], "rotation axis")
    magnitude = math.sqrt(sum(component * component for component in axis))
    if magnitude < 1.0e-12:
        raise NativeRobotSetupError("Robot orientation rotation axis must be nonzero.")
    return RobotOrientationDefaultsSpec(
        _digest(values["expected_defaults_state_sha256"]),
        _vector(placement["origin_mm"], "displacement"),
        tuple(component / magnitude for component in axis),
        _number(
            rotation["angle_degrees"],
            "orientation angle",
            minimum=-360.0,
            maximum=360.0,
        ),
    )


def prepare_robot_motion_defaults(
    values: Mapping[str, Any],
) -> RobotMotionDefaultsSpec:
    if not isinstance(values, Mapping) or set(values) != {
        "expected_defaults_state_sha256",
        "speed_mm_per_s",
        "continuous",
        "acceleration_mm_per_s2",
    }:
        raise NativeRobotSetupError("Robot motion-default fields are incorrect.")
    if type(values["continuous"]) is not bool:
        raise NativeRobotSetupError("Robot waypoint continuity must be true or false.")
    return RobotMotionDefaultsSpec(
        _digest(values["expected_defaults_state_sha256"]),
        _number(
            values["speed_mm_per_s"],
            "waypoint speed",
            minimum=0.0,
            maximum=MAX_ROBOT_MOTION_VALUE,
            exclusive_minimum=True,
        ),
        values["continuous"],
        _number(
            values["acceleration_mm_per_s2"],
            "waypoint acceleration",
            minimum=0.0,
            maximum=MAX_ROBOT_MOTION_VALUE,
            exclusive_minimum=True,
        ),
    )


def _transaction_open(document: Any) -> bool:
    booked = getattr(document, "getBookedTransactionID", None)
    return bool(
        bool(getattr(document, "HasPendingTransaction", False))
        or (callable(booked) and int(booked() or 0) != 0)
    )


def _document_boundary(context: NativeRuntimeContext) -> _DocumentBoundary:
    document = context.document
    if _transaction_open(document):
        raise NativeRobotSetupError(
            "Finish or cancel the open transaction before changing Robot defaults."
        )
    timeline = document.getObject("VibeCADTimeline")
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    visibility = tuple(
        bool(value) for value in (getattr(timeline, "VisibilityAtEnd", ()) or ())
    )
    if timeline is not None and len(operations) != len(visibility):
        raise NativeRobotSetupError("The active document History is malformed.")
    return _DocumentBoundary(
        tuple(document.Objects),
        read_current_selection(document),
        timeline,
        operations,
        visibility,
        int(getattr(document, "UndoCount", 0) or 0),
        context.state.current_revision(context.document_uid),
    )


def _require_document_boundary(
    context: NativeRuntimeContext,
    boundary: _DocumentBoundary,
) -> None:
    document = context.document
    timeline = document.getObject("VibeCADTimeline")
    if (
        tuple(document.Objects) != boundary.objects
        or read_current_selection(document) != boundary.selection
        or timeline is not boundary.timeline
        or tuple(getattr(timeline, "Operations", ()) or ()) != boundary.operations
        or tuple(
            bool(value) for value in (getattr(timeline, "VisibilityAtEnd", ()) or ())
        )
        != boundary.visibility
        or int(getattr(document, "UndoCount", 0) or 0) != boundary.undo_count
        or context.state.current_revision(context.document_uid)
        != boundary.structural_revision
        or _transaction_open(document)
    ):
        raise NativeRobotSetupError(
            "The document changed during the Robot session-default operation."
        )


def _raw_defaults(namespace: Any) -> dict[str, Any]:
    try:
        return {name: getattr(namespace, name) for name in _DEFAULT_NAMES}
    except AttributeError as exc:
        raise NativeRobotSetupError(
            "Robot waypoint defaults are not initialized in this session."
        ) from exc


def _capture(
    namespace: Any,
    application: Any,
) -> RobotWaypointDefaultsState:
    try:
        return capture_robot_waypoint_defaults(
            namespace=namespace,
            application=application,
        )
    except NativeRobotDefaultsStateError as exc:
        raise NativeRobotSetupError(str(exc)) from exc


def _desired_state(
    raw: Mapping[str, Any],
    updates: Mapping[str, Any],
    application: Any,
) -> RobotWaypointDefaultsState:
    return _capture({**dict(raw), **dict(updates)}, application)


def _restore_owned(
    namespace: Any,
    original: Mapping[str, Any],
    written: Mapping[str, Any],
) -> None:
    for name, value in written.items():
        if (
            getattr(namespace, name, None) is value
            or getattr(namespace, name, None) == value
        ):
            setattr(namespace, name, original[name])


def _apply_defaults(
    context: NativeRuntimeContext,
    *,
    operation: str,
    expected_state_sha256: str,
    updates: Mapping[str, Any],
    application: Any,
    namespace: Any,
) -> dict[str, Any]:
    context.guard()
    boundary = _document_boundary(context)
    original = _raw_defaults(namespace)
    before = _capture(namespace, application)
    if before.state_sha256 != expected_state_sha256:
        raise NativeRobotSetupError(
            "Robot waypoint defaults changed; read current Assemble state and retry."
        )
    desired = _desired_state(original, updates, application)
    if desired.data == before.data:
        context.guard()
        _require_document_boundary(context, boundary)
        if _capture(namespace, application) != before:
            raise NativeRobotSetupError(
                "Robot waypoint defaults changed during no-op verification."
            )
        return {
            "operation": operation,
            "scope": "application_session",
            "changed": False,
            "previous_state_sha256": before.state_sha256,
            "waypoint_defaults": before.summary(),
        }

    written: dict[str, Any] = {}
    try:
        for name, value in updates.items():
            setattr(namespace, name, value)
            written[name] = value
        context.guard()
        _require_document_boundary(context, boundary)
        after = _capture(namespace, application)
        if after.data != desired.data:
            raise NativeRobotSetupError(
                "Robot waypoint defaults did not reach the requested state."
            )
    except Exception:
        _restore_owned(namespace, original, written)
        raise
    return {
        "operation": operation,
        "scope": "application_session",
        "changed": True,
        "previous_state_sha256": before.state_sha256,
        "waypoint_defaults": after.summary(),
    }


def set_robot_orientation_defaults(
    context: NativeRuntimeContext,
    spec: RobotOrientationDefaultsSpec,
) -> dict[str, Any]:
    if not isinstance(spec, RobotOrientationDefaultsSpec):
        raise TypeError("spec must be a RobotOrientationDefaultsSpec")
    import FreeCAD as App

    namespace = robot_defaults_namespace()
    axis = App.Vector(*spec.rotation_axis)
    updates = {
        "_DefOrientation": App.Rotation(axis, spec.angle_degrees),
        "_DefDisplacement": App.Vector(*spec.displacement_mm),
    }
    return _apply_defaults(
        context,
        operation="set_default_orientation",
        expected_state_sha256=spec.expected_state_sha256,
        updates=updates,
        application=App,
        namespace=namespace,
    )


def set_robot_motion_defaults(
    context: NativeRuntimeContext,
    spec: RobotMotionDefaultsSpec,
) -> dict[str, Any]:
    if not isinstance(spec, RobotMotionDefaultsSpec):
        raise TypeError("spec must be a RobotMotionDefaultsSpec")
    import FreeCAD as App

    namespace = robot_defaults_namespace()
    updates = {
        "_DefSpeed": f"{spec.speed_mm_per_s:.17g} mm/s",
        "_DefCont": spec.continuous,
        "_DefAcceleration": f"{spec.acceleration_mm_per_s2:.17g} mm/s^2",
    }
    return _apply_defaults(
        context,
        operation="set_default_values",
        expected_state_sha256=spec.expected_state_sha256,
        updates=updates,
        application=App,
        namespace=namespace,
    )
