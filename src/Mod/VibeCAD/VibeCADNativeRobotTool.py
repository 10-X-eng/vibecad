# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact in-place attachment of a Part or VRML tool shape to a Robot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRobotSetup import NativeRobotSetupError
from VibeCADNativeRobotState import (
    NativeRobotStateError,
    RobotSetupState,
    capture_robot_setup_state,
    same_robot_setup_state,
)
from VibeCADNativeRobotToolState import (
    NativeRobotToolStateError,
    RobotToolShapeRecord,
    capture_robot_tool_shape_record,
)
from VibeCADNativeTargets import (
    NativeObjectRef,
    object_identity,
    object_reference,
    read_current_selection,
    resolve_object,
)


@dataclass(frozen=True, slots=True)
class RobotToolShapeSpec:
    robot_ref: NativeObjectRef
    tool_shape_ref: NativeObjectRef
    expected_setup_state_sha256: str
    expected_robot_state_sha256: str
    expected_tool_shape_state_sha256: str


@dataclass(frozen=True, slots=True)
class _TimelineState:
    timeline: Any | None
    operations: tuple[Any, ...]
    visibility: tuple[bool, ...]


@dataclass(frozen=True, slots=True)
class PreparedRobotToolShape:
    spec: RobotToolShapeSpec
    robot: Any
    tool_shape: Any
    setup_state: RobotSetupState
    robot_index: int
    tool_state: RobotToolShapeRecord
    objects_before: tuple[Any, ...]
    selection_before: Mapping[str, Any]
    timeline_before: _TimelineState


def _digest(value: Any, field: str) -> str:
    result = str(value or "")
    if len(result) != 64 or any(
        character not in "0123456789abcdef" for character in result
    ):
        raise NativeRobotSetupError(f"{field} must be one lowercase SHA-256 digest.")
    return result


def prepare_robot_tool_shape_spec(
    document_uid: str,
    values: Mapping[str, Any],
) -> RobotToolShapeSpec:
    expected = {
        "robot",
        "tool_shape",
        "expected_setup_state_sha256",
        "expected_robot_state_sha256",
        "expected_tool_shape_state_sha256",
    }
    if not isinstance(values, Mapping) or set(values) != expected:
        raise NativeRobotSetupError("Robot tool-shape attachment has incorrect fields.")

    def reference(value: Any, label: str) -> NativeObjectRef:
        if not isinstance(value, Mapping) or set(value) != {"object_name"}:
            raise NativeRobotSetupError(f"The Robot {label} target is invalid.")
        return NativeObjectRef(document_uid, str(value["object_name"] or ""))

    return RobotToolShapeSpec(
        reference(values["robot"], "object"),
        reference(values["tool_shape"], "tool-shape"),
        _digest(
            values["expected_setup_state_sha256"],
            "expected_setup_state_sha256",
        ),
        _digest(
            values["expected_robot_state_sha256"],
            "expected_robot_state_sha256",
        ),
        _digest(
            values["expected_tool_shape_state_sha256"],
            "expected_tool_shape_state_sha256",
        ),
    )


def _transaction_open(document: Any) -> bool:
    booked = getattr(document, "getBookedTransactionID", None)
    return bool(
        bool(getattr(document, "HasPendingTransaction", False))
        or (callable(booked) and int(booked() or 0) != 0)
    )


def _timeline_state(document: Any) -> _TimelineState:
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None:
        return _TimelineState(None, (), ())
    if str(getattr(timeline, "TypeId", "") or "") != "App::DocumentTimeline":
        raise NativeRobotSetupError("The active document History is malformed.")
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    visibility = tuple(bool(value) for value in timeline.VisibilityAtEnd)
    if len(operations) != len(visibility):
        raise NativeRobotSetupError("The active document History is malformed.")
    return _TimelineState(timeline, operations, visibility)


def _capture_setup(document: Any) -> RobotSetupState:
    try:
        return capture_robot_setup_state(document)
    except NativeRobotStateError as exc:
        raise NativeRobotSetupError(str(exc)) from exc


def _capture_tool(obj: Any) -> RobotToolShapeRecord:
    try:
        return capture_robot_tool_shape_record(obj)
    except NativeRobotToolStateError as exc:
        raise NativeRobotSetupError(str(exc)) from exc


def preflight_robot_tool_shape(
    document: Any,
    spec: RobotToolShapeSpec,
) -> PreparedRobotToolShape:
    if not isinstance(spec, RobotToolShapeSpec):
        raise TypeError("spec must be a RobotToolShapeSpec")
    if _transaction_open(document):
        raise NativeRobotSetupError(
            "Finish or cancel the open transaction before attaching a Robot tool."
        )
    if bool(getattr(document, "Recomputing", False)) or bool(
        getattr(document, "RecomputePending", False)
    ):
        raise NativeRobotSetupError(
            "Wait for the active document recompute before attaching a Robot tool."
        )
    robot = resolve_object(
        document,
        spec.robot_ref,
        expected_types=("Robot::RobotObject",),
    )
    tool_shape = resolve_object(document, spec.tool_shape_ref)
    tool_state = _capture_tool(tool_shape)
    setup = _capture_setup(document)
    if setup.state_sha256 != spec.expected_setup_state_sha256:
        raise NativeRobotSetupError(
            "The Robot setup state changed; read current Assemble state and retry."
        )
    try:
        robot_index = setup.robots.index(robot)
    except ValueError as exc:
        raise NativeRobotSetupError(
            "The exact Robot target is absent from current setup state."
        ) from exc
    if setup.records[robot_index].state_sha256 != spec.expected_robot_state_sha256:
        raise NativeRobotSetupError(
            "The exact Robot target changed; read current Assemble state and retry."
        )
    if tool_state.state_sha256 != spec.expected_tool_shape_state_sha256:
        raise NativeRobotSetupError(
            "The exact Robot tool shape changed; read current Assemble state and retry."
        )
    return PreparedRobotToolShape(
        spec,
        robot,
        tool_shape,
        setup,
        robot_index,
        tool_state,
        tuple(document.Objects),
        read_current_selection(document),
        _timeline_state(document),
    )


def _require_boundary(document: Any, prepared: PreparedRobotToolShape) -> None:
    if tuple(document.Objects) != prepared.objects_before:
        raise NativeRobotSetupError(
            "The document changed during Robot tool attachment."
        )
    if read_current_selection(document) != prepared.selection_before:
        raise NativeRobotSetupError(
            "The human selection changed during Robot tool attachment."
        )
    if _timeline_state(document) != prepared.timeline_before:
        raise NativeRobotSetupError(
            "Document History changed during Robot tool attachment."
        )
    current_tool = _capture_tool(prepared.tool_shape)
    if (
        current_tool.data != prepared.tool_state.data
        or current_tool.state_sha256 != prepared.tool_state.state_sha256
    ):
        raise NativeRobotSetupError(
            "The exact Robot tool shape changed during attachment."
        )


def robot_tool_shape_is_noop(prepared: PreparedRobotToolShape) -> bool:
    if not isinstance(prepared, PreparedRobotToolShape):
        raise TypeError("prepared must be a PreparedRobotToolShape")
    return getattr(prepared.robot, "ToolShape", None) is prepared.tool_shape


def attach_robot_tool_shape(
    document: Any,
    *,
    prepared: PreparedRobotToolShape,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedRobotToolShape):
        raise TypeError("prepared must be a PreparedRobotToolShape")
    _require_boundary(document, prepared)
    current = _capture_setup(document)
    if not same_robot_setup_state(prepared.setup_state, current):
        raise NativeRobotSetupError(
            "The Robot setup state changed before tool attachment."
        )
    prepared.robot.ToolShape = prepared.tool_shape
    if getattr(prepared.robot, "ToolShape", None) is not prepared.tool_shape:
        raise NativeRobotSetupError(
            "The exact tool shape could not be attached to the Robot."
        )
    return NativeMutationDraft(
        value=prepared,
        recompute_targets=(prepared.robot,),
        changed=(object_identity(prepared.robot),),
    )


def _result(
    prepared: PreparedRobotToolShape,
    state: RobotSetupState,
    *,
    changed: bool,
) -> dict[str, Any]:
    record = state.records[prepared.robot_index]
    return {
        "operation": "add_tool_shape",
        "robot": object_reference(prepared.robot),
        "tool_shape": object_reference(prepared.tool_shape),
        "previous_tool_shape": prepared.setup_state.records[prepared.robot_index].data[
            "tool_shape"
        ],
        "changed": changed,
        "robot_state_sha256": record.state_sha256,
        "setup_state_sha256": state.state_sha256,
    }


def verify_robot_tool_shape_attachment(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value
    if not isinstance(prepared, PreparedRobotToolShape):
        raise TypeError("Robot tool mutation returned an invalid draft.")
    _require_boundary(document, prepared)
    if getattr(prepared.robot, "ToolShape", None) is not prepared.tool_shape:
        raise NativeRobotSetupError(
            "The Robot did not retain the exact requested tool shape."
        )
    state = _capture_setup(document)
    if state.robots != prepared.setup_state.robots:
        raise NativeRobotSetupError(
            "Robot tool attachment changed Robot object identities."
        )
    for index, (before, after) in enumerate(
        zip(prepared.setup_state.records, state.records, strict=True)
    ):
        before_data = dict(before.data)
        after_data = dict(after.data)
        if index == prepared.robot_index:
            before_data.pop("tool_shape", None)
            after_data.pop("tool_shape", None)
        if before_data != after_data:
            raise NativeRobotSetupError(
                "Robot tool attachment changed unrelated Robot state."
            )
    expected_link = state.records[prepared.robot_index].data["tool_shape"]
    if (
        not isinstance(expected_link, Mapping)
        or expected_link.get("document_uid")
        != prepared.tool_state.data["object"]["document_uid"]
        or expected_link.get("object_name")
        != prepared.tool_state.data["object"]["object_name"]
        or expected_link.get("object_id") != prepared.tool_state.data["object_id"]
    ):
        raise NativeRobotSetupError(
            "The Robot tool-shape link does not identify the exact target."
        )
    return _result(prepared, state, changed=True)


def verify_robot_tool_shape_noop(
    document: Any,
    prepared: PreparedRobotToolShape,
) -> dict[str, Any]:
    _require_boundary(document, prepared)
    state = _capture_setup(document)
    if not same_robot_setup_state(prepared.setup_state, state):
        raise NativeRobotSetupError(
            "Robot setup state changed during no-op tool attachment."
        )
    return _result(prepared, state, changed=False)
