# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact Robot creation from two human-authorized definition files."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import io
import math
from typing import Any, Mapping

from VibeCADNativeInput import (
    NativeInputArtifact,
    NativeInputError,
    NativeInputRequest,
)
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeRobotState import (
    MAX_ROBOT_KINEMATIC_BYTES,
    MAX_ROBOT_VRML_BYTES,
    NativeRobotStateError,
    RobotSetupState,
    capture_robot_setup_state,
    same_robot_setup_state,
)
from VibeCADNativeTargets import (
    object_identity,
    object_reference,
    read_current_selection,
)


NATIVE_ROBOT_SETUP_FAILED = "NATIVE_ROBOT_SETUP_FAILED"


class NativeRobotSetupError(NativeMutationError):
    """The exact Robot setup operation could not be completed safely."""

    def __init__(self, message: str) -> None:
        super().__init__(NATIVE_ROBOT_SETUP_FAILED, message)


@dataclass(frozen=True, slots=True)
class RobotCreateSpec:
    label: str
    expected_state_sha256: str
    expected_robot_count: int


@dataclass(frozen=True, slots=True)
class _TimelineState:
    timeline: Any | None
    operations: tuple[Any, ...]
    visibility: tuple[bool, ...]


@dataclass(frozen=True, slots=True)
class RobotCreateBoundary:
    spec: RobotCreateSpec
    state: RobotSetupState
    objects_before: tuple[Any, ...]
    selection_before: Mapping[str, Any]
    timeline_before: _TimelineState


@dataclass(frozen=True, slots=True)
class PreparedRobotCreate:
    boundary: RobotCreateBoundary
    visual: NativeInputArtifact
    kinematics: NativeInputArtifact
    axes: tuple[tuple[float, ...], ...]


def robot_visual_input_request() -> NativeInputRequest:
    return NativeInputRequest(
        purpose="robot_visual_definition",
        title="Select VRML visual definition for Robot",
        allowed_suffixes=(".wrl", ".vrml"),
        name_filter="VRML Files (*.wrl *.vrml)",
        maximum_bytes=MAX_ROBOT_VRML_BYTES,
    )


def robot_kinematic_input_request() -> NativeInputRequest:
    return NativeInputRequest(
        purpose="robot_kinematic_definition",
        title="Select kinematic CSV definition for Robot",
        allowed_suffixes=(".csv",),
        name_filter="CSV Files (*.csv)",
        maximum_bytes=MAX_ROBOT_KINEMATIC_BYTES,
    )


def _label(value: Any) -> str:
    if not isinstance(value, str):
        raise NativeRobotSetupError("A Robot label must be text.")
    result = value.strip()
    if not 1 <= len(result) <= 160 or any(
        ord(character) < 32 or ord(character) == 127 for character in result
    ):
        raise NativeRobotSetupError(
            "A Robot label must contain 1 through 160 printable characters."
        )
    return result


def _digest(value: Any) -> str:
    result = str(value or "")
    if len(result) != 64 or any(
        character not in "0123456789abcdef" for character in result
    ):
        raise NativeRobotSetupError(
            "expected_state_sha256 must be one lowercase SHA-256 digest."
        )
    return result


def _robot_count(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= 128:
        raise NativeRobotSetupError(
            "expected_robot_count must be an integer from 0 through 128."
        )
    return value


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


def _transaction_open(document: Any) -> bool:
    booked = getattr(document, "getBookedTransactionID", None)
    return bool(
        bool(getattr(document, "HasPendingTransaction", False))
        or (callable(booked) and int(booked() or 0) != 0)
    )


def preflight_robot_create_boundary(
    document: Any,
    spec: RobotCreateSpec,
) -> RobotCreateBoundary:
    """Freeze exact document state before asking the human to select files."""

    if not isinstance(spec, RobotCreateSpec):
        raise TypeError("spec must be a RobotCreateSpec")
    clean_spec = RobotCreateSpec(
        label=_label(spec.label),
        expected_state_sha256=_digest(spec.expected_state_sha256),
        expected_robot_count=_robot_count(spec.expected_robot_count),
    )
    if _transaction_open(document):
        raise NativeRobotSetupError(
            "Finish or cancel the open transaction before creating a Robot."
        )
    if bool(getattr(document, "Recomputing", False)) or bool(
        getattr(document, "RecomputePending", False)
    ):
        raise NativeRobotSetupError(
            "Wait for the active document recompute before creating a Robot."
        )
    try:
        state = capture_robot_setup_state(document)
    except NativeRobotStateError as exc:
        raise NativeRobotSetupError(str(exc)) from exc
    if (
        len(state.robots) != clean_spec.expected_robot_count
        or state.state_sha256 != clean_spec.expected_state_sha256
    ):
        raise NativeRobotSetupError(
            "The Robot setup state changed; read current Assemble state and retry."
        )
    return RobotCreateBoundary(
        clean_spec,
        state,
        tuple(document.Objects),
        read_current_selection(document),
        _timeline_state(document),
    )


def _parse_visual(artifact: NativeInputArtifact) -> None:
    try:
        value = artifact.read_bytes(maximum_bytes=MAX_ROBOT_VRML_BYTES)
    except NativeInputError as exc:
        raise NativeRobotSetupError(str(exc)) from exc
    header = value[:256].decode("ascii", errors="ignore").lstrip("\ufeff\r\n\t ")
    if not (header.startswith("#VRML V1.0") or header.startswith("#VRML V2.0")):
        raise NativeRobotSetupError(
            "The selected Robot visual definition is not a VRML 1.0 or 2.0 file."
        )


def _parse_kinematics(
    artifact: NativeInputArtifact,
) -> tuple[tuple[float, ...], ...]:
    try:
        encoded = artifact.read_bytes(maximum_bytes=MAX_ROBOT_KINEMATIC_BYTES)
        text = encoded.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise NativeRobotSetupError(
            "The selected Robot kinematic CSV must be UTF-8 text."
        ) from exc
    except NativeInputError as exc:
        raise NativeRobotSetupError(str(exc)) from exc
    try:
        rows = list(csv.reader(io.StringIO(text, newline="")))
    except csv.Error as exc:
        raise NativeRobotSetupError(
            "The selected Robot kinematic CSV is malformed."
        ) from exc
    while rows and not any(value.strip() for value in rows[-1]):
        rows.pop()
    if len(rows) != 7 or not any(value.strip() for value in rows[0]):
        raise NativeRobotSetupError(
            "A Robot kinematic CSV requires one header and exactly six axis rows."
        )
    axes = []
    for index, row in enumerate(rows[1:], start=1):
        if len(row) != 8:
            raise NativeRobotSetupError(
                f"Robot kinematic axis {index} must contain exactly eight values."
            )
        try:
            values = tuple(float(value.strip()) for value in row)
        except (TypeError, ValueError, OverflowError) as exc:
            raise NativeRobotSetupError(
                f"Robot kinematic axis {index} contains a non-numeric value."
            ) from exc
        if not all(math.isfinite(value) for value in values):
            raise NativeRobotSetupError(
                f"Robot kinematic axis {index} contains a non-finite value."
            )
        if values[4] not in {-1.0, 1.0}:
            raise NativeRobotSetupError(
                f"Robot kinematic axis {index} rotation direction must be -1 or 1."
            )
        if values[6] > values[5]:
            raise NativeRobotSetupError(
                f"Robot kinematic axis {index} minimum angle exceeds its maximum."
            )
        if values[7] <= 0.0:
            raise NativeRobotSetupError(
                f"Robot kinematic axis {index} velocity must be greater than zero."
            )
        axes.append(values)
    return tuple(axes)


def finalize_robot_create_preflight(
    document: Any,
    boundary: RobotCreateBoundary,
    visual: NativeInputArtifact,
    kinematics: NativeInputArtifact,
) -> PreparedRobotCreate:
    """Validate both grants and prove nothing changed during human selection."""

    if not isinstance(boundary, RobotCreateBoundary):
        raise TypeError("boundary must be a RobotCreateBoundary")
    if not isinstance(visual, NativeInputArtifact) or not isinstance(
        kinematics,
        NativeInputArtifact,
    ):
        raise TypeError("Robot definition inputs must be NativeInputArtifact values")
    if tuple(document.Objects) != boundary.objects_before:
        raise NativeRobotSetupError(
            "The document changed while Robot definition files were selected."
        )
    if read_current_selection(document) != boundary.selection_before:
        raise NativeRobotSetupError(
            "The human selection changed while Robot definition files were selected."
        )
    if _timeline_state(document) != boundary.timeline_before:
        raise NativeRobotSetupError(
            "The document History changed while Robot definition files were selected."
        )
    try:
        current = capture_robot_setup_state(document)
    except NativeRobotStateError as exc:
        raise NativeRobotSetupError(str(exc)) from exc
    if not same_robot_setup_state(boundary.state, current):
        raise NativeRobotSetupError(
            "The Robot setup state changed while definition files were selected."
        )
    _parse_visual(visual)
    axes = _parse_kinematics(kinematics)
    return PreparedRobotCreate(boundary, visual, kinematics, axes)


def create_robot(
    document: Any,
    *,
    prepared: PreparedRobotCreate,
) -> NativeMutationDraft:
    """Create one human-equivalent Robot operation inside the owned transaction."""

    if not isinstance(prepared, PreparedRobotCreate):
        raise TypeError("prepared must be a PreparedRobotCreate")
    if tuple(document.Objects) != prepared.boundary.objects_before:
        raise NativeRobotSetupError("The document changed before Robot creation.")
    try:
        visual_path = prepared.visual.host_path()
        kinematic_path = prepared.kinematics.host_path()
    except NativeInputError as exc:
        raise NativeRobotSetupError(str(exc)) from exc
    import Robot  # noqa: F401 - loads the Robot document factories

    robot = document.addObject("Robot::RobotObject", "Robot")
    if robot is None or str(getattr(robot, "TypeId", "") or "") != (
        "Robot::RobotObject"
    ):
        raise NativeRobotSetupError("The Robot factory returned the wrong object type.")
    robot.Label = prepared.boundary.spec.label
    robot.RobotVrmlFile = str(visual_path)
    robot.RobotKinematicFile = str(kinematic_path)
    robot.setKinematic([list(row) for row in prepared.axes])
    try:
        prepared.visual.verify_unchanged()
        prepared.kinematics.verify_unchanged()
    except NativeInputError as exc:
        raise NativeRobotSetupError(str(exc)) from exc
    document.publishProvisionalTimelineOperationBlock(robot, (), ())
    return NativeMutationDraft(
        value={"robot": robot, "prepared": prepared},
        recompute_targets=(robot,),
        created=(object_identity(robot),),
    )


def _verify_timeline(document: Any, prepared: PreparedRobotCreate, robot: Any) -> None:
    before = prepared.boundary.timeline_before
    after = _timeline_state(document)
    if after.timeline is None or (
        before.timeline is not None and after.timeline is not before.timeline
    ):
        raise NativeRobotSetupError("Robot creation changed the History identity.")
    if after.operations != (*before.operations, robot):
        raise NativeRobotSetupError(
            "The created Robot is not the exact final History operation."
        )
    if (
        after.visibility[: len(before.visibility)] != before.visibility
        or len(after.visibility) != len(before.visibility) + 1
    ):
        raise NativeRobotSetupError(
            "Robot creation changed unrelated History presentation."
        )


def verify_created_robot(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    robot = draft.value["robot"]
    prepared = draft.value["prepared"]
    boundary = prepared.boundary
    if (
        document.getObject(str(robot.Name)) is not robot
        or str(robot.TypeId) != "Robot::RobotObject"
        or str(robot.Label) != boundary.spec.label
        or not robot.isValid()
        or str(getattr(robot, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(robot, "VibeCADTimelineOwner", None) is not None
        or tuple(getattr(robot, "VibeCADTimelineReplacedInputs", ()) or ())
    ):
        raise NativeRobotSetupError(
            "The created Robot failed its exact object postcondition."
        )
    expected_objects = set(boundary.objects_before)
    expected_objects.add(robot)
    timeline = document.getObject("VibeCADTimeline")
    if boundary.timeline_before.timeline is None:
        expected_objects.add(timeline)
    if timeline is None or set(document.Objects) != expected_objects:
        raise NativeRobotSetupError(
            "Robot creation changed unrelated document objects."
        )
    _verify_timeline(document, prepared, robot)
    if read_current_selection(document) != boundary.selection_before:
        raise NativeRobotSetupError("Robot creation changed the human selection.")
    try:
        prepared.visual.verify_unchanged()
        prepared.kinematics.verify_unchanged()
        state = capture_robot_setup_state(document)
    except (NativeInputError, NativeRobotStateError) as exc:
        raise NativeRobotSetupError(str(exc)) from exc
    if state.robots != (*boundary.state.robots, robot):
        raise NativeRobotSetupError(
            "Robot creation changed unrelated Robot identities."
        )
    if tuple(record.data for record in state.records[:-1]) != tuple(
        record.data for record in boundary.state.records
    ):
        raise NativeRobotSetupError("Robot creation changed an existing Robot.")
    record = state.records[-1]
    visual_summary = prepared.visual.summary()
    kinematic_summary = prepared.kinematics.summary()
    if record.data["definition"] != {
        "visual": {
            "configured": True,
            "size_bytes": visual_summary["size_bytes"],
            "sha256": visual_summary["sha256"],
        },
        "kinematics": {
            "configured": True,
            "size_bytes": kinematic_summary["size_bytes"],
            "sha256": kinematic_summary["sha256"],
        },
    }:
        raise NativeRobotSetupError(
            "The created Robot did not embed the authorized definitions exactly."
        )
    if record.data["axes_degrees"] != [0.0] * 6:
        raise NativeRobotSetupError(
            "The created Robot did not start at its exact zero-axis setup."
        )
    return {
        "robot": object_reference(robot),
        "label": str(robot.Label),
        "definitions": {
            "visual": visual_summary,
            "kinematics": kinematic_summary,
        },
        "robot_state_sha256": record.state_sha256,
        "setup_state_sha256": state.state_sha256,
        "robot_count": len(state.robots),
    }
