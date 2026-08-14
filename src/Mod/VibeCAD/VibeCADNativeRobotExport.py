# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, bounded, human-authorized Robot program output."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import time
from typing import Any, Callable, Mapping

from VibeCADNativeOutput import (
    NativeOutputAuthorization,
    NativeOutputBundleItem,
    NativeOutputError,
    NativeOutputRequest,
    publish_authorized_output,
    publish_authorized_output_bundle,
)
from VibeCADNativeRobotState import (
    NativeRobotStateError,
    RobotSetupState,
    capture_robot_setup_state,
    same_robot_setup_state,
)
from VibeCADNativeRobotTrajectoryState import (
    MAX_WAYPOINTS_PER_TRAJECTORY,
    NativeRobotTrajectoryStateError,
    RobotTrajectoryState,
    capture_robot_trajectory_state,
    same_robot_trajectory_state,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket, NativeRevisionConflict
from VibeCADNativeTargets import (
    NativeObjectRef,
    read_current_selection,
    resolve_object,
)


NATIVE_ROBOT_EXPORT_FAILED = "NATIVE_ROBOT_EXPORT_FAILED"
MAX_KUKA_SOURCE_BYTES = 8 * 1024 * 1024
_SPEC_FIELDS = frozenset(
    {
        "robot",
        "trajectory",
        "expected_robot_setup_state_sha256",
        "expected_robot_state_sha256",
        "expected_trajectory_setup_state_sha256",
        "expected_trajectory_state_sha256",
    }
)


class NativeRobotExportError(RuntimeError):
    """An exact Robot program could not be published safely."""

    def __init__(
        self,
        message: str,
        *,
        code: str = NATIVE_ROBOT_EXPORT_FAILED,
    ) -> None:
        super().__init__(str(message).strip())
        self.code = str(code)

    def failure(self) -> dict[str, str]:
        return {"error_code": self.code, "message": str(self)}


@dataclass(frozen=True, slots=True)
class RobotExportSpec:
    operation: str
    robot_ref: NativeObjectRef
    trajectory_ref: NativeObjectRef
    expected_robot_setup_state_sha256: str
    expected_robot_state_sha256: str
    expected_trajectory_setup_state_sha256: str
    expected_trajectory_state_sha256: str


@dataclass(frozen=True, slots=True)
class _TimelineState:
    timeline: Any = field(repr=False)
    operations: tuple[Any, ...] = field(repr=False)
    visibility: tuple[bool, ...]
    suppression: tuple[bool, ...]
    position: int


@dataclass(frozen=True, slots=True)
class _DocumentState:
    objects: tuple[Any, ...] = field(repr=False)
    object_states: tuple[tuple[Any, tuple[str, ...]], ...] = field(repr=False)
    timeline: _TimelineState
    selection: Any = field(repr=False)
    visibility: tuple[tuple[Any, bool], ...] = field(repr=False)
    undo_count: int
    redo_count: int
    transaction_id: int
    gui_modified: bool | None


@dataclass(frozen=True, slots=True)
class PreparedRobotExport:
    spec: RobotExportSpec
    robot: Any = field(repr=False)
    trajectory: Any = field(repr=False)
    robot_setup: RobotSetupState = field(repr=False)
    robot_index: int
    trajectory_setup: RobotTrajectoryState = field(repr=False)
    trajectory_index: int
    generated_at: str
    program_name: str
    source: bytes = field(repr=False)
    source_sha256: str
    data: bytes | None = field(repr=False)
    data_sha256: str | None
    document_before: _DocumentState
    output_request: NativeOutputRequest
    data_output_request: NativeOutputRequest | None


def _digest(value: Any, field_name: str) -> str:
    result = str(value or "")
    if len(result) != 64 or any(
        character not in "0123456789abcdef" for character in result
    ):
        raise NativeRobotExportError(
            f"{field_name} must be one lowercase SHA-256 digest.",
            code="NATIVE_ARGUMENTS_INVALID",
        )
    return result


def _reference(document_uid: str, value: Any, label: str) -> NativeObjectRef:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise NativeRobotExportError(
            f"The Robot export {label} target is invalid.",
            code="NATIVE_ARGUMENTS_INVALID",
        )
    name = str(value["object_name"] or "")
    if not name:
        raise NativeRobotExportError(
            f"The Robot export {label} target is empty.",
            code="NATIVE_ARGUMENTS_INVALID",
        )
    return NativeObjectRef(document_uid, name)


def prepare_robot_export_spec(
    document_uid: str,
    operation: str,
    values: Mapping[str, Any],
) -> RobotExportSpec:
    if operation not in {"export_kuka_compact", "export_kuka_full"}:
        raise NativeRobotExportError(
            "The requested Robot export variant is unavailable.",
            code="NATIVE_ARGUMENTS_INVALID",
        )
    if not isinstance(values, Mapping) or set(values) != _SPEC_FIELDS:
        raise NativeRobotExportError(
            "Robot export fields are incorrect.",
            code="NATIVE_ARGUMENTS_INVALID",
        )
    return RobotExportSpec(
        operation=operation,
        robot_ref=_reference(document_uid, values["robot"], "object"),
        trajectory_ref=_reference(
            document_uid,
            values["trajectory"],
            "trajectory",
        ),
        expected_robot_setup_state_sha256=_digest(
            values["expected_robot_setup_state_sha256"],
            "expected_robot_setup_state_sha256",
        ),
        expected_robot_state_sha256=_digest(
            values["expected_robot_state_sha256"],
            "expected_robot_state_sha256",
        ),
        expected_trajectory_setup_state_sha256=_digest(
            values["expected_trajectory_setup_state_sha256"],
            "expected_trajectory_setup_state_sha256",
        ),
        expected_trajectory_state_sha256=_digest(
            values["expected_trajectory_state_sha256"],
            "expected_trajectory_state_sha256",
        ),
    )


def _transaction_id(document: Any) -> int:
    reader = getattr(document, "getBookedTransactionID", None)
    return int(reader() or 0) if callable(reader) else 0


def _transaction_open(document: Any) -> bool:
    return bool(
        bool(getattr(document, "HasPendingTransaction", False))
        or _transaction_id(document) != 0
    )


def _gui_modified(document: Any) -> bool | None:
    try:
        import FreeCADGui as Gui

        gui_document = Gui.getDocument(str(document.Name))
        return None if gui_document is None else bool(gui_document.Modified)
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError):
        return None


def _object_state(obj: Any) -> tuple[str, ...]:
    try:
        return tuple(str(value) for value in obj.State)
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return ()


def _timeline_state(document: Any) -> _TimelineState:
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None:
        return _TimelineState(None, (), (), (), 0)
    if str(getattr(timeline, "TypeId", "") or "") != "App::DocumentTimeline":
        raise NativeRobotExportError("The active document History is malformed.")
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    visibility = tuple(bool(value) for value in timeline.VisibilityAtEnd)
    suppression = tuple(bool(value) for value in timeline.SuppressionAtEnd)
    position = int(timeline.Position)
    if (
        len(operations) != len(visibility)
        or len(operations) != len(suppression)
        or not 0 <= position <= len(operations)
    ):
        raise NativeRobotExportError("The active document History is malformed.")
    return _TimelineState(
        timeline,
        operations,
        visibility,
        suppression,
        position,
    )


def _document_state(document: Any) -> _DocumentState:
    objects = tuple(document.Objects)
    presentation = []
    for obj in objects:
        view = getattr(obj, "ViewObject", None)
        if view is not None and hasattr(view, "Visibility"):
            presentation.append((obj, bool(view.Visibility)))
    return _DocumentState(
        objects=objects,
        object_states=tuple((obj, _object_state(obj)) for obj in objects),
        timeline=_timeline_state(document),
        selection=read_current_selection(document),
        visibility=tuple(presentation),
        undo_count=int(getattr(document, "UndoCount", 0) or 0),
        redo_count=int(getattr(document, "RedoCount", 0) or 0),
        transaction_id=_transaction_id(document),
        gui_modified=_gui_modified(document),
    )


def _document_matches(document: Any, before: _DocumentState) -> bool:
    try:
        return bool(
            tuple(document.Objects) == before.objects
            and all(_object_state(obj) == state for obj, state in before.object_states)
            and _timeline_state(document) == before.timeline
            and read_current_selection(document) == before.selection
            and all(
                bool(obj.ViewObject.Visibility) == visible
                for obj, visible in before.visibility
            )
            and int(getattr(document, "UndoCount", 0) or 0) == before.undo_count
            and int(getattr(document, "RedoCount", 0) or 0) == before.redo_count
            and _transaction_id(document) == before.transaction_id
            and not _transaction_open(document)
            and _gui_modified(document) == before.gui_modified
        )
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _capture_robot_state(document: Any) -> RobotSetupState:
    try:
        return capture_robot_setup_state(document)
    except NativeRobotStateError as exc:
        raise NativeRobotExportError(str(exc)) from exc


def _capture_trajectory_state(document: Any) -> RobotTrajectoryState:
    try:
        return capture_robot_trajectory_state(document)
    except NativeRobotTrajectoryStateError as exc:
        raise NativeRobotExportError(str(exc)) from exc


def _exact_index(
    values: tuple[Any, ...],
    records: tuple[Any, ...],
    target: Any,
    expected_digest: str,
    label: str,
) -> int:
    try:
        index = values.index(target)
    except ValueError as exc:
        raise NativeRobotExportError(
            f"The exact Robot export {label} is absent from current state."
        ) from exc
    if records[index].state_sha256 != expected_digest:
        raise NativeRobotExportError(
            f"The exact Robot export {label} changed; read current state and retry."
        )
    return index


def _renderer(
    operation: str,
) -> tuple[Callable[..., Any], Callable[[Any], str]]:
    try:
        from KukaExporter import ProgramName, RenderCompactSub, RenderFullSub

        if operation == "export_kuka_compact":
            return RenderCompactSub, ProgramName
        if operation == "export_kuka_full":
            return RenderFullSub, ProgramName
    except (ImportError, AttributeError) as exc:
        raise NativeRobotExportError(
            "The installed KUKA exporter is unavailable."
        ) from exc
    raise NativeRobotExportError(
        "The requested KUKA export variant is unavailable.",
        code="NATIVE_ARGUMENTS_INVALID",
    )


def _render(
    operation: str,
    renderer: Callable[..., Any],
    robot: Any,
    trajectory: Any,
    generated_at: str,
) -> tuple[bytes, bytes | None]:
    try:
        value = renderer(robot, trajectory, generated_at=generated_at)
        if operation == "export_kuka_compact" and isinstance(value, str):
            text_values = (value,)
        elif (
            operation == "export_kuka_full"
            and isinstance(value, tuple)
            and len(value) == 2
            and all(isinstance(item, str) for item in value)
        ):
            text_values = value
        else:
            raise TypeError("KUKA renderer returned an invalid output set")
        encoded = tuple(item.encode("utf-8") for item in text_values)
    except NativeRobotExportError:
        raise
    except Exception as exc:
        raise NativeRobotExportError(
            "The exact Robot trajectory could not be rendered as KRL."
        ) from exc
    if any(not item or len(item) > MAX_KUKA_SOURCE_BYTES for item in encoded):
        raise NativeRobotExportError(
            "A KRL output is empty or exceeds the 8 MiB per-file bound."
        )
    return encoded[0], None if len(encoded) == 1 else encoded[1]


def _require_usable_record(record: Any, label: str) -> None:
    data = record.data
    if not bool(data.get("valid", False)) or bool(data.get("suppressed", False)):
        raise NativeRobotExportError(
            f"The exact Robot export {label} must be valid and unsuppressed."
        )
    if label == "trajectory" and not bool(data.get("usable_at_history", False)):
        raise NativeRobotExportError(
            "The exact Robot trajectory is unavailable at the current History position."
        )


def preflight_robot_export(
    context: NativeRuntimeContext,
    spec: RobotExportSpec,
    *,
    renderer: Callable[..., Any] | None = None,
    program_name_reader: Callable[[Any], str] | None = None,
    generated_at: str | None = None,
) -> PreparedRobotExport:
    if not isinstance(context, NativeRuntimeContext):
        raise TypeError("context must be a NativeRuntimeContext")
    if not isinstance(spec, RobotExportSpec):
        raise TypeError("spec must be a RobotExportSpec")
    context.guard()
    document = context.document
    if _transaction_open(document):
        raise NativeRobotExportError(
            "Finish or cancel the open transaction before exporting Robot KRL."
        )
    if bool(getattr(document, "Recomputing", False)) or bool(
        getattr(document, "RecomputePending", False)
    ):
        raise NativeRobotExportError(
            "Wait for the active document recompute before exporting Robot KRL."
        )
    robot = resolve_object(
        document,
        spec.robot_ref,
        expected_types=("Robot::RobotObject",),
    )
    trajectory = resolve_object(document, spec.trajectory_ref)
    robot_setup = _capture_robot_state(document)
    trajectory_setup = _capture_trajectory_state(document)
    if robot_setup.state_sha256 != spec.expected_robot_setup_state_sha256:
        raise NativeRobotExportError(
            "The Robot setup changed; read current Manufacture state and retry."
        )
    if trajectory_setup.state_sha256 != spec.expected_trajectory_setup_state_sha256:
        raise NativeRobotExportError(
            "The trajectory setup changed; read current Manufacture state and retry."
        )
    robot_index = _exact_index(
        robot_setup.robots,
        robot_setup.records,
        robot,
        spec.expected_robot_state_sha256,
        "Robot",
    )
    trajectory_index = _exact_index(
        trajectory_setup.trajectories,
        trajectory_setup.records,
        trajectory,
        spec.expected_trajectory_state_sha256,
        "trajectory",
    )
    robot_record = robot_setup.records[robot_index]
    trajectory_record = trajectory_setup.records[trajectory_index]
    _require_usable_record(robot_record, "Robot")
    _require_usable_record(trajectory_record, "trajectory")
    waypoint_count = len(trajectory_record.waypoints)
    if not 1 <= waypoint_count <= MAX_WAYPOINTS_PER_TRAJECTORY:
        raise NativeRobotExportError(
            "KUKA export requires one through 4,096 exact waypoints."
        )
    default_renderer, default_name_reader = _renderer(spec.operation)
    render = renderer or default_renderer
    read_name = program_name_reader or default_name_reader
    timestamp = str(generated_at if generated_at is not None else time.asctime())
    before = _document_state(document)
    try:
        program_name = str(read_name(trajectory))
    except Exception as exc:
        raise NativeRobotExportError(
            "The KUKA program name could not be derived from the trajectory."
        ) from exc
    if not program_name or len(program_name) > 24:
        raise NativeRobotExportError("The KUKA program name is invalid.")
    source, data = _render(spec.operation, render, robot, trajectory, timestamp)
    if (
        not _document_matches(document, before)
        or not same_robot_setup_state(robot_setup, _capture_robot_state(document))
        or not same_robot_trajectory_state(
            trajectory_setup,
            _capture_trajectory_state(document),
        )
    ):
        raise NativeRobotExportError(
            "Rendering KRL changed the Robot, trajectory, or document state."
        )
    full = spec.operation == "export_kuka_full"
    return PreparedRobotExport(
        spec=spec,
        robot=robot,
        trajectory=trajectory,
        robot_setup=robot_setup,
        robot_index=robot_index,
        trajectory_setup=trajectory_setup,
        trajectory_index=trajectory_index,
        generated_at=timestamp,
        program_name=program_name,
        source=source,
        source_sha256=hashlib.sha256(source).hexdigest(),
        data=data,
        data_sha256=None if data is None else hashlib.sha256(data).hexdigest(),
        document_before=before,
        output_request=NativeOutputRequest(
            purpose=(
                "robot_kuka_full_source_export"
                if full
                else "robot_kuka_compact_export"
            ),
            title=(
                "Export Full KUKA Program Source"
                if full
                else "Export Compact KUKA Program"
            ),
            suggested_file_name=f"{program_name}.src",
            allowed_suffixes=(".src",),
            name_filter="KRL source (*.src)",
            maximum_bytes=MAX_KUKA_SOURCE_BYTES,
        ),
        data_output_request=(
            NativeOutputRequest(
                purpose="robot_kuka_full_data_export",
                title="Export Full KUKA Program Data",
                suggested_file_name=f"{program_name}.dat",
                allowed_suffixes=(".dat",),
                name_filter="KRL data (*.dat)",
                maximum_bytes=MAX_KUKA_SOURCE_BYTES,
            )
            if full
            else None
        ),
    )


def require_current_robot_export_ticket(
    context: NativeRuntimeContext,
    ticket: NativeCallTicket,
) -> None:
    if not isinstance(ticket, NativeCallTicket):
        raise TypeError("ticket must be a NativeCallTicket")
    revision = context.state.current_revision(context.document_uid)
    if revision != ticket.expected_revision:
        raise NativeRevisionConflict(ticket.expected_revision, revision)


def verify_robot_export_source(
    context: NativeRuntimeContext,
    prepared: PreparedRobotExport,
    *,
    renderer: Callable[..., Any] | None = None,
) -> None:
    if not isinstance(prepared, PreparedRobotExport):
        raise TypeError("prepared must be a PreparedRobotExport")
    context.guard()
    document = context.document
    robot = resolve_object(
        document,
        prepared.spec.robot_ref,
        expected_types=("Robot::RobotObject",),
    )
    trajectory = resolve_object(document, prepared.spec.trajectory_ref)
    robots = _capture_robot_state(document)
    trajectories = _capture_trajectory_state(document)
    if (
        robot is not prepared.robot
        or trajectory is not prepared.trajectory
        or not same_robot_setup_state(prepared.robot_setup, robots)
        or not same_robot_trajectory_state(prepared.trajectory_setup, trajectories)
        or not _document_matches(document, prepared.document_before)
    ):
        raise NativeRobotExportError(
            "The exact Robot, trajectory, document, or human UI state changed during output."
        )
    default_renderer, _name_reader = _renderer(prepared.spec.operation)
    source, data = _render(
        prepared.spec.operation,
        renderer or default_renderer,
        robot,
        trajectory,
        prepared.generated_at,
    )
    if (
        source != prepared.source
        or data != prepared.data
        or not _document_matches(document, prepared.document_before)
    ):
        raise NativeRobotExportError(
            "The exact KRL content changed during output."
        )


def export_robot_program(
    context: NativeRuntimeContext,
    prepared: PreparedRobotExport,
    authorization: NativeOutputAuthorization,
    ticket: NativeCallTicket,
    *,
    data_authorization: NativeOutputAuthorization | None = None,
    renderer: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(prepared, PreparedRobotExport):
        raise TypeError("prepared must be a PreparedRobotExport")

    def guard() -> None:
        require_current_robot_export_ticket(context, ticket)
        verify_robot_export_source(context, prepared, renderer=renderer)

    def writer(content: bytes) -> Callable[[str], None]:
        def write(path: str) -> None:
            with open(path, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())

        return write

    def validate_source(path: Path) -> None:
        try:
            source = path.read_bytes()
            text = source.decode("utf-8")
            waypoint_count = len(
                prepared.trajectory_setup.records[
                    prepared.trajectory_index
                ].waypoints
            )
            compact = prepared.spec.operation == "export_kuka_compact"
            motion_count = (
                text.count("\nLIN {") if compact else text.count("\nLIN XP")
            )
            if (
                source != prepared.source
                or not text.startswith("&ACCESS RVP\n")
                or f"DEF {prepared.program_name}( )\n" not in text
                or motion_count != waypoint_count
                or not text.endswith("\nEND\n")
            ):
                raise ValueError("KRL source content changed")
        except Exception as exc:
            raise NativeRobotExportError(
                "The generated KRL source failed exact validation."
            ) from exc

    def validate_data(path: Path) -> None:
        try:
            if prepared.data is None:
                raise ValueError("full KRL data is absent")
            data = path.read_bytes()
            text = data.decode("utf-8")
            waypoint_count = len(
                prepared.trajectory_setup.records[
                    prepared.trajectory_index
                ].waypoints
            )
            if (
                data != prepared.data
                or not text.startswith("&ACCESS RVP\n")
                or f"DEFDAT {prepared.program_name} PUBLIC\n" not in text
                or text.count("\nDECL E6POS XP") != waypoint_count
                or not text.endswith("\nENDDAT\n")
            ):
                raise ValueError("KRL data content changed")
        except Exception as exc:
            raise NativeRobotExportError(
                "The generated KRL data failed exact validation."
            ) from exc

    try:
        if prepared.spec.operation == "export_kuka_compact":
            if prepared.data is not None or prepared.data_output_request is not None:
                raise NativeRobotExportError(
                    "Compact KRL unexpectedly produced a data artifact."
                )
            artifacts = (
                publish_authorized_output(
                    prepared.output_request,
                    authorization,
                    writer=writer(prepared.source),
                    guard=guard,
                    validator=validate_source,
                    temporary_suffix=".src",
                ),
            )
        else:
            if (
                prepared.data is None
                or prepared.data_output_request is None
                or data_authorization is None
            ):
                raise NativeRobotExportError(
                    "Full KRL requires separately authorized source and data outputs."
                )
            artifacts = publish_authorized_output_bundle(
                (
                    NativeOutputBundleItem(
                        request=prepared.output_request,
                        authorization=authorization,
                        writer=writer(prepared.source),
                        validator=validate_source,
                        temporary_suffix=".src",
                    ),
                    NativeOutputBundleItem(
                        request=prepared.data_output_request,
                        authorization=data_authorization,
                        writer=writer(prepared.data),
                        validator=validate_data,
                        temporary_suffix=".dat",
                    ),
                ),
                guard=guard,
            )
    except NativeOutputError as exc:
        raise NativeRobotExportError(str(exc), code=exc.code) from exc
    robot_record = prepared.robot_setup.records[prepared.robot_index]
    trajectory_record = prepared.trajectory_setup.records[prepared.trajectory_index]
    compact = prepared.spec.operation == "export_kuka_compact"
    result = {
        "operation": prepared.spec.operation,
        "robot": {
            "object_name": str(prepared.robot.Name),
            "state_sha256": robot_record.state_sha256,
        },
        "trajectory": {
            "object_name": str(prepared.trajectory.Name),
            "state_sha256": trajectory_record.state_sha256,
            "waypoint_count": len(trajectory_record.waypoints),
        },
        "program": {
            "name": prepared.program_name,
            "format": "kuka_compact_krl" if compact else "kuka_full_krl",
            "source_sha256": prepared.source_sha256,
            "data_sha256": prepared.data_sha256,
        },
        "document_unchanged": True,
        "history_unchanged": True,
        "selection_unchanged": True,
        "visibility_unchanged": True,
    }
    if compact:
        result["output"] = artifacts[0].summary()
        result["program"]["sha256"] = result["program"].pop("source_sha256")
        result["program"].pop("data_sha256")
    else:
        result["outputs"] = [artifact.summary() for artifact in artifacts]
    return result
