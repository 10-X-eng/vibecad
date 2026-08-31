# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact main-thread input boundary for isolated CAM postprocessing."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufacturePostConfiguration import (
    MAX_MACHINE_CONFIG_BYTES as MAX_MACHINE_CONFIG_BYTES,
    resolve_post_configuration,
)
from VibeCADNativeManufactureState import (
    capture_other_job_states,
    job_state,
    operation_active_state,
    operation_state,
    other_job_states_are_current,
    resolve_job_target,
    resolve_operation_target,
)
from VibeCADNativeTargets import read_current_selection


MAX_POST_OPERATIONS = 64
MAX_POST_SOURCE_BYTES = 16 * 1024 * 1024
MAX_POST_SNAPSHOT_BYTES = 4 * 1024 * 1024 * 1024
_BINARY_OPEN = getattr(os, "O_BINARY", 0)


@dataclass(frozen=True, slots=True)
class FileIdentity:
    path: Path = field(repr=False, compare=False)
    device: int
    inode: int
    size: int
    modified_ns: int
    sha256: str | None = None


@dataclass(frozen=True, slots=True)
class PostDocumentState:
    objects: tuple[Any, ...] = field(repr=False)
    object_states: tuple[tuple[Any, tuple[str, ...]], ...] = field(repr=False)
    timeline: Any = field(repr=False)
    timeline_operations: tuple[Any, ...] = field(repr=False)
    timeline_visibility: tuple[bool, ...]
    timeline_suppression: tuple[bool, ...]
    timeline_position: int
    selection: Any = field(repr=False)
    visibility: tuple[tuple[Any, bool], ...] = field(repr=False)
    undo_count: int
    redo_count: int
    transaction_id: int
    gui_modified: bool | None
    file_name: str
    label: str


@dataclass(frozen=True, slots=True)
class FrozenPostInput:
    workspace: Any = field(repr=False, compare=False)
    workspace_path: Path = field(repr=False, compare=False)
    snapshot_path: Path = field(repr=False, compare=False)
    snapshot_sha256: str
    snapshot_size: int
    job: Any = field(repr=False, compare=False)
    job_name: str
    job_target: Mapping[str, Any]
    job_before: Mapping[str, Any]
    other_job_states: tuple[tuple[Any, str], ...] = field(
        repr=False,
        compare=False,
    )
    job_operations: tuple[Any, ...] = field(repr=False)
    active_operation_count: int
    command_count: int
    use_machine_flow: bool
    machine_name: str
    machine_config_path: Path | None = field(repr=False, compare=False)
    machine_config_sha256: str | None
    postprocessor_name: str
    postprocessor_source: FileIdentity
    configured_output: str
    freecadcmd: FileIdentity
    child_script: FileIdentity
    document_before: PostDocumentState
    operation_variant: str = "complete_job"
    selected_operations: tuple[Any, ...] = field(
        default=(), repr=False, compare=False
    )
    selected_operation_targets: tuple[Mapping[str, Any], ...] = ()
    selected_operation_names: tuple[str, ...] = ()
    selected_operation_state_sha256: tuple[str, ...] = ()


def _error(message: str, code: str = "NATIVE_ARGUMENTS_INVALID", **repair: Any) -> None:
    raise NativeManufactureError(
        message,
        error_code=code,
        repair=repair or None,
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


def _document_state(document: Any) -> PostDocumentState:
    objects = tuple(document.Objects)
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None or str(getattr(timeline, "TypeId", "")) != "App::DocumentTimeline":
        _error(
            "CAM postprocessing requires a valid document History.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    visibility_at_end = tuple(bool(value) for value in timeline.VisibilityAtEnd)
    suppression_at_end = tuple(bool(value) for value in timeline.SuppressionAtEnd)
    position = int(timeline.Position)
    if (
        len(operations) != len(visibility_at_end)
        or len(operations) != len(suppression_at_end)
        or not 0 <= position <= len(operations)
    ):
        _error(
            "CAM postprocessing found malformed document History state.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    presentation = []
    for obj in objects:
        view = getattr(obj, "ViewObject", None)
        if view is not None and hasattr(view, "Visibility"):
            presentation.append((obj, bool(view.Visibility)))
    return PostDocumentState(
        objects=objects,
        object_states=tuple((obj, _object_state(obj)) for obj in objects),
        timeline=timeline,
        timeline_operations=operations,
        timeline_visibility=visibility_at_end,
        timeline_suppression=suppression_at_end,
        timeline_position=position,
        selection=read_current_selection(document),
        visibility=tuple(presentation),
        undo_count=int(getattr(document, "UndoCount", 0) or 0),
        redo_count=int(getattr(document, "RedoCount", 0) or 0),
        transaction_id=_transaction_id(document),
        gui_modified=_gui_modified(document),
        file_name=str(getattr(document, "FileName", "") or ""),
        label=str(getattr(document, "Label", "") or ""),
    )


def _state_matches(document: Any, before: PostDocumentState) -> bool:
    try:
        return bool(
            tuple(document.Objects) == before.objects
            and all(_object_state(obj) == state for obj, state in before.object_states)
            and document.getObject("VibeCADTimeline") is before.timeline
            and tuple(before.timeline.Operations) == before.timeline_operations
            and tuple(bool(value) for value in before.timeline.VisibilityAtEnd)
            == before.timeline_visibility
            and tuple(bool(value) for value in before.timeline.SuppressionAtEnd)
            == before.timeline_suppression
            and int(before.timeline.Position) == before.timeline_position
            and read_current_selection(document) == before.selection
            and all(bool(obj.ViewObject.Visibility) == value for obj, value in before.visibility)
            and int(getattr(document, "UndoCount", 0) or 0) == before.undo_count
            and int(getattr(document, "RedoCount", 0) or 0) == before.redo_count
            and _transaction_id(document) == before.transaction_id
            and not _transaction_open(document)
            and _gui_modified(document) == before.gui_modified
            and str(getattr(document, "FileName", "") or "") == before.file_name
            and str(getattr(document, "Label", "") or "") == before.label
        )
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _hash_file(path: Path, maximum_bytes: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > maximum_bytes:
                    _error(
                        "A required CAM postprocessing file exceeds its safety bound.",
                        "NATIVE_MANUFACTURE_POST_LIMIT",
                    )
                digest.update(chunk)
    except NativeManufactureError:
        raise
    except OSError as exc:
        raise NativeManufactureError(
            "A required CAM postprocessing file could not be read.",
            error_code="NATIVE_MANUFACTURE_POST_UNAVAILABLE",
        ) from exc
    if size <= 0:
        _error(
            "A required CAM postprocessing file is empty.",
            "NATIVE_MANUFACTURE_POST_UNAVAILABLE",
        )
    return size, digest.hexdigest()


def _file_identity(
    path: Path,
    *,
    executable: bool,
    hash_limit: int | None,
) -> FileIdentity:
    try:
        resolved = path.resolve(strict=True)
        value = resolved.stat()
    except OSError as exc:
        raise NativeManufactureError(
            "A required CAM postprocessing runtime file is unavailable.",
            error_code="NATIVE_MANUFACTURE_POST_UNAVAILABLE",
        ) from exc
    if not stat.S_ISREG(value.st_mode) or (executable and not os.access(resolved, os.X_OK)):
        _error(
            "A required CAM postprocessing runtime is not a usable regular file.",
            "NATIVE_MANUFACTURE_POST_UNAVAILABLE",
        )
    digest = None
    if hash_limit is not None:
        size, digest = _hash_file(resolved, hash_limit)
        try:
            after = resolved.stat()
        except OSError as exc:
            raise NativeManufactureError(
                "A required CAM postprocessing runtime disappeared while it was inspected.",
                error_code="NATIVE_MANUFACTURE_POST_UNAVAILABLE",
            ) from exc
        if (
            size != int(value.st_size)
            or (
                int(after.st_dev),
                int(after.st_ino),
                int(after.st_size),
                int(after.st_mtime_ns),
            )
            != (
                int(value.st_dev),
                int(value.st_ino),
                int(value.st_size),
                int(value.st_mtime_ns),
            )
        ):
            _error(
                "A required CAM postprocessing runtime changed while it was inspected.",
                "NATIVE_MANUFACTURE_POST_UNAVAILABLE",
            )
    return FileIdentity(
        resolved,
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_size),
        int(value.st_mtime_ns),
        digest,
    )


def validate_file_identity(identity: FileIdentity, *, executable: bool = False) -> None:
    current = _file_identity(
        identity.path,
        executable=executable,
        hash_limit=(MAX_POST_SOURCE_BYTES if identity.sha256 is not None else None),
    )
    if (
        current.device,
        current.inode,
        current.size,
        current.modified_ns,
        current.sha256,
    ) != (
        identity.device,
        identity.inode,
        identity.size,
        identity.modified_ns,
        identity.sha256,
    ):
        _error(
            "A required CAM postprocessing runtime changed after preflight.",
            "NATIVE_MANUFACTURE_POST_UNAVAILABLE",
        )


def _freecadcmd() -> FileIdentity:
    import FreeCAD

    names = (
        ("FreeCADCmd.exe", "freecadcmd.exe")
        if sys.platform == "win32"
        else ("FreeCADCmd", "freecadcmd")
    )
    root = Path(str(FreeCAD.getHomePath())) / "bin"
    for name in names:
        candidate = root / name
        if candidate.is_file():
            return _file_identity(candidate, executable=True, hash_limit=None)
    _error(
        "The fixed windowless FreeCADCmd runtime is unavailable.",
        "NATIVE_MANUFACTURE_POST_UNAVAILABLE",
    )
    raise AssertionError("unreachable")


def _write_private(path: Path, data: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | _BINARY_OPEN,
        0o600,
    )
    try:
        offset = 0
        while offset < len(data):
            offset += os.write(descriptor, data[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _post_configuration(
    job: Any,
    workspace_path: Path,
) -> tuple[bool, str, str, Path | None, str | None, str]:
    configuration = resolve_post_configuration(job)
    machine_path = None
    machine_sha = None
    if configuration.use_machine_flow:
        machine_data = configuration.machine_data
        if machine_data is None:
            _error(
                "The exact Job's configured machine could not be frozen.",
                "NATIVE_MANUFACTURE_POST_MACHINE_INVALID",
            )
        machine_dir = workspace_path / "Machines"
        machine_dir.mkdir(mode=0o700)
        machine_path = machine_dir / "FrozenMachine.fcm"
        _write_private(machine_path, machine_data)
        machine_sha = hashlib.sha256(machine_data).hexdigest()
    return (
        configuration.use_machine_flow,
        configuration.machine_name,
        configuration.postprocessor_name,
        machine_path,
        machine_sha,
        configuration.configured_output,
    )


def _resolve_selected_operations(
    document: Any,
    exact_job: Any,
    values: list[Mapping[str, Any]],
) -> tuple[
    tuple[Any, ...],
    tuple[Mapping[str, Any], ...],
    tuple[str, ...],
    tuple[str, ...],
]:
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_POST_OPERATIONS:
        _error(
            "Selected-operation postprocessing requires one through 64 exact operations."
        )
    group = tuple(getattr(getattr(exact_job, "Operations", None), "Group", ()) or ())
    positions = {id(operation): index for index, operation in enumerate(group)}
    if len(positions) != len(group):
        _error(
            "The exact CAM Job contains duplicate operation identities.",
            "NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
        )
    selected = []
    targets = []
    states = []
    for value in values:
        if not isinstance(value, Mapping) or set(value) != {
            "object_name",
            "expected_state_sha256",
        }:
            _error(
                "Each selected CAM operation target must contain only object_name "
                "and expected_state_sha256."
            )
        operation, current = resolve_operation_target(document, value)
        name = str(current["object_name"])
        expected = str(current["state_sha256"])
        if id(operation) not in positions:
            _error(
                f"Selected CAM operation {name!r} is not a direct entry of the exact Job.",
                "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )
        selected.append(operation)
        targets.append(
            {
                "object_name": name,
                "expected_state_sha256": expected,
            }
        )
        states.append(expected)
    if len({id(operation) for operation in selected}) != len(selected):
        _error("Selected CAM operations must be distinct.")
    selected_positions = tuple(positions[id(operation)] for operation in selected)
    if selected_positions != tuple(sorted(selected_positions)):
        _error(
            "Selected CAM operations must be supplied in their current Job order.",
            repair_order=[str(operation.Name) for operation in group],
        )
    return (
        tuple(selected),
        tuple(targets),
        tuple(str(operation.Name) for operation in selected),
        tuple(states),
    )


def _validate_post_operations(
    document: Any,
    operations: tuple[Any, ...],
    *,
    selected_only: bool,
) -> int:
    usable = getattr(document, "isObjectUsableAtCurrentTimelinePosition", None)
    command_count = 0
    for operation in operations:
        if not operation_active_state(operation):
            _error(
                f"CAM operation {operation.Name!r} is inactive and cannot be postprocessed.",
                "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )
        if not callable(usable) or not bool(usable(operation)):
            _error(
                f"Active CAM operation {operation.Name!r} is not usable at the current History position.",
                "NATIVE_MANUFACTURE_HISTORY_TARGET_INACTIVE",
            )
        commands = tuple(getattr(getattr(operation, "Path", None), "Commands", ()) or ())
        if not commands:
            _error(
                f"Active CAM operation {operation.Name!r} has no generated toolpath.",
                "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
            )
        command_count += len(commands)
        if command_count > 1_000_000:
            scope = "selected operations" if selected_only else "complete Job"
            _error(
                f"The {scope} exceed the one-million-command postprocessing bound.",
                "NATIVE_MANUFACTURE_POST_LIMIT",
            )
    return command_count


def _preflight_post(
    document: Any,
    *,
    job: Mapping[str, Any],
    selected_targets: list[Mapping[str, Any]] | None,
) -> FrozenPostInput:
    """Freeze one exact post request and private FCStd snapshot on the document thread."""

    if _transaction_open(document):
        _error(
            "Finish or cancel the open transaction before postprocessing.",
            "NATIVE_MANUFACTURE_TRANSACTION_CONFLICT",
        )
    if bool(getattr(document, "Recomputing", False)) or bool(
        getattr(document, "RecomputePending", False)
    ):
        _error(
            "Wait for the active document recompute before postprocessing.",
            "NATIVE_MANUFACTURE_POST_UNAVAILABLE",
        )
    exact_job, before = resolve_job_target(document, job)
    other_job_states = capture_other_job_states(document, (exact_job,))
    operations = tuple(getattr(getattr(exact_job, "Operations", None), "Group", ()) or ())
    if not 1 <= len(operations) <= MAX_POST_OPERATIONS:
        _error(
            "CAM postprocessing requires a Job with one through 64 direct operations.",
            "NATIVE_MANUFACTURE_POST_LIMIT",
        )
    if selected_targets is None:
        operation_variant = "complete_job"
        selected = ()
        normalized_targets = ()
        selected_names = ()
        selected_states = ()
        posted = tuple(
            operation for operation in operations if operation_active_state(operation)
        )
        if not posted:
            _error(
                "The exact Job has no active operation to postprocess.",
                "NATIVE_MANUFACTURE_POST_EMPTY",
            )
    else:
        operation_variant = "selected_operations"
        (
            selected,
            normalized_targets,
            selected_names,
            selected_states,
        ) = _resolve_selected_operations(document, exact_job, selected_targets)
        posted = selected
    command_count = _validate_post_operations(
        document,
        posted,
        selected_only=selected_targets is not None,
    )

    document_before = _document_state(document)
    workspace = tempfile.TemporaryDirectory(prefix="vibecad-native-post-")
    workspace_path = Path(workspace.name)
    try:
        os.chmod(workspace_path, 0o700)
        (
            use_machine_flow,
            machine_name,
            postprocessor_name,
            machine_path,
            machine_sha,
            configured_output,
        ) = _post_configuration(exact_job, workspace_path)
        from Path.Post.Processor import PostProcessorFactory

        source_name = PostProcessorFactory.resolve_post_processor_path(postprocessor_name)
        if not source_name:
            _error(
                f"Configured postprocessor {postprocessor_name!r} is unavailable.",
                "NATIVE_MANUFACTURE_POST_PROCESSOR_MISSING",
            )
        if not PostProcessorFactory.is_modern_post_processor(source_name, postprocessor_name):
            _error(
                "Native assistance supports only modern class-based CAM postprocessors; "
                "configure a modern processor for this Job or machine.",
                "NATIVE_MANUFACTURE_POST_PROCESSOR_UNSUPPORTED",
                configured_postprocessor=postprocessor_name,
            )
        post_source = _file_identity(
            Path(source_name),
            executable=False,
            hash_limit=MAX_POST_SOURCE_BYTES,
        )
        child_script = _file_identity(
            Path(__file__).with_name("VibeCADNativeManufacturePostChild.py"),
            executable=False,
            hash_limit=MAX_POST_SOURCE_BYTES,
        )
        command = _freecadcmd()
        snapshot_path = workspace_path / "snapshot.FCStd"
        try:
            result = document.saveCopy(str(snapshot_path))
        except Exception as exc:
            raise NativeManufactureError(
                "The exact CAM document could not be copied for isolated postprocessing.",
                error_code="NATIVE_MANUFACTURE_POST_SNAPSHOT_FAILED",
            ) from exc
        if result is False or not snapshot_path.is_file():
            _error(
                "The exact CAM document copy was not created.",
                "NATIVE_MANUFACTURE_POST_SNAPSHOT_FAILED",
            )
        os.chmod(snapshot_path, 0o600)
        snapshot_size, snapshot_sha = _hash_file(snapshot_path, MAX_POST_SNAPSHOT_BYTES)
        if not _state_matches(document, document_before):
            _error(
                "Creating the private CAM snapshot changed the live document or UI state.",
                "NATIVE_MANUFACTURE_STATE_INVALID",
            )
        current = job_state(exact_job)
        if current["state_sha256"] != before["state_sha256"]:
            _error(
                "The exact CAM Job changed while its private snapshot was created.",
                "NATIVE_MANUFACTURE_STATE_STALE",
            )
        if not other_job_states_are_current(document, other_job_states):
            _error(
                "Another CAM setup changed while the private snapshot was created.",
                "NATIVE_MANUFACTURE_STATE_STALE",
            )
        if any(
            operation_state(operation).get("state_sha256") != expected
            for operation, expected in zip(
                selected,
                selected_states,
                strict=True,
            )
        ):
            _error(
                "A selected CAM operation changed while its private snapshot was created.",
                "NATIVE_MANUFACTURE_STATE_STALE",
            )
        return FrozenPostInput(
            workspace=workspace,
            workspace_path=workspace_path,
            snapshot_path=snapshot_path,
            snapshot_sha256=snapshot_sha,
            snapshot_size=snapshot_size,
            job=exact_job,
            job_name=str(exact_job.Name),
            job_target=dict(job),
            job_before=before,
            other_job_states=other_job_states,
            job_operations=operations,
            active_operation_count=len(posted),
            command_count=command_count,
            use_machine_flow=use_machine_flow,
            machine_name=machine_name,
            machine_config_path=machine_path,
            machine_config_sha256=machine_sha,
            postprocessor_name=postprocessor_name,
            postprocessor_source=post_source,
            configured_output=configured_output,
            freecadcmd=command,
            child_script=child_script,
            document_before=document_before,
            operation_variant=operation_variant,
            selected_operations=selected,
            selected_operation_targets=normalized_targets,
            selected_operation_names=selected_names,
            selected_operation_state_sha256=selected_states,
        )
    except Exception:
        workspace.cleanup()
        raise


def preflight_post(document: Any, *, job: Mapping[str, Any]) -> FrozenPostInput:
    """Freeze a complete exact Job and private FCStd snapshot."""

    return _preflight_post(document, job=job, selected_targets=None)


def preflight_selected_post(
    document: Any,
    *,
    job: Mapping[str, Any],
    operations: list[Mapping[str, Any]],
) -> FrozenPostInput:
    """Freeze an exact ordered Job-operation subset and private FCStd snapshot."""

    return _preflight_post(document, job=job, selected_targets=operations)


def validate_post_source(document: Any, frozen: FrozenPostInput) -> None:
    if not isinstance(frozen, FrozenPostInput):
        raise TypeError("frozen must be a FrozenPostInput")
    exact_job, current = resolve_job_target(document, frozen.job_target)
    group = tuple(getattr(getattr(exact_job, "Operations", None), "Group", ()) or ())
    selected_matches = True
    if frozen.operation_variant == "selected_operations":
        selected_matches = bool(
            len(frozen.selected_operations)
            == len(frozen.selected_operation_state_sha256)
            == frozen.active_operation_count
            and all(operation in group for operation in frozen.selected_operations)
            and all(
                operation_state(operation).get("state_sha256") == expected
                and operation_active_state(operation)
                for operation, expected in zip(
                    frozen.selected_operations,
                    frozen.selected_operation_state_sha256,
                    strict=True,
                )
            )
        )
    elif frozen.operation_variant != "complete_job":
        selected_matches = False
    if (
        exact_job is not frozen.job
        or current["state_sha256"] != frozen.job_before["state_sha256"]
        or group != frozen.job_operations
        or not other_job_states_are_current(document, frozen.other_job_states)
        or not selected_matches
        or not _state_matches(document, frozen.document_before)
    ):
        _error(
            "The exact CAM Job, document, History, or human UI state changed during postprocessing.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    validate_file_identity(frozen.freecadcmd, executable=True)
    validate_file_identity(frozen.child_script)
    validate_file_identity(frozen.postprocessor_source)


def cleanup_post(frozen: FrozenPostInput) -> None:
    if isinstance(frozen, FrozenPostInput):
        frozen.workspace.cleanup()
