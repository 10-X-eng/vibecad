# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact Native control of the shipped Assembly simulation player."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
import secrets
import threading
from typing import Any, Callable

from VibeCADNativeAssemblyJointConnectors import placement_is_same
from VibeCADNativeAssemblySimulationState import (
    AssemblySimulationState,
    capture_assembly_simulation_state,
)
from VibeCADNativeAssemblySolveState import AssemblySolverState
from VibeCADNativeAssemblyState import read_active_assembly, same_assembly
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeTargets import (
    NativeObjectRef,
    object_reference,
    read_current_selection,
    resolve_object,
)


NATIVE_ASSEMBLY_PLAYBACK_FAILED = "NATIVE_ASSEMBLY_PLAYBACK_FAILED"
_PLAYBACK_ID = re.compile(r"^[0-9a-f]{32}$")
_PLAYBACK_MODES = frozenset({"hold", "forward", "backward"})
_PLAYBACK_DIRECTIONS = frozenset({"forward", "backward"})
MAX_PLAYBACK_DOCUMENT_OBJECTS = 100_000


class NativeAssemblyPlaybackError(RuntimeError):
    """One exact Assembly playback request failed without seizing another task."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": NATIVE_ASSEMBLY_PLAYBACK_FAILED,
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class AssemblyPlaybackOpenSpec:
    simulation_ref: NativeObjectRef
    expected_simulation_state_sha256: str
    time_seconds: float
    mode: str


@dataclass(frozen=True, slots=True)
class AssemblyPlaybackControlSpec:
    simulation_ref: NativeObjectRef
    playback_id: str
    time_seconds: float | None = None
    direction: str | None = None


@dataclass(slots=True)
class _PlaybackSession:
    playback_id: str
    document: Any
    assembly: Any
    simulation: Any
    panel: Any
    dialog: Any
    form: Any
    state_before: AssemblySimulationState
    solver_before: AssemblySolverState
    document_objects_before: tuple[Any, ...]
    visibility_before: tuple[tuple[Any, str, int, bool], ...]
    camera_before: str
    modified_before: bool


_SESSIONS: dict[str, _PlaybackSession] = {}
_SESSIONS_LOCK = threading.RLock()


def _forget_session(document_uid: str, playback_id: str) -> None:
    """Drop only the exact session identified by one destroyed player form."""

    with _SESSIONS_LOCK:
        session = _SESSIONS.get(str(document_uid or ""))
        if session is not None and session.playback_id == str(playback_id or ""):
            _SESSIONS.pop(str(document_uid or ""), None)


def _finite(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise NativeAssemblyPlaybackError(f"{field} must be a finite number.")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise NativeAssemblyPlaybackError(f"{field} must be a finite number.") from exc
    if not math.isfinite(result) or not -1_000_000.0 <= result <= 1_000_000.0:
        raise NativeAssemblyPlaybackError(
            f"{field} must be a finite number from -1000000 through 1000000."
        )
    return result


def _digest(value: Any) -> str:
    result = str(value or "")
    if len(result) != 64 or any(
        character not in "0123456789abcdef" for character in result
    ):
        raise NativeAssemblyPlaybackError(
            "expected_simulation_state_sha256 must be one lowercase SHA-256 digest."
        )
    return result


def _playback_id(value: Any) -> str:
    result = str(value or "")
    if not _PLAYBACK_ID.fullmatch(result):
        raise NativeAssemblyPlaybackError(
            "playback_id must be the exact active Native playback identifier."
        )
    return result


def _active_task_dialog() -> Any | None:
    import FreeCADGui as Gui

    return Gui.Control.activeTaskDialog()


def _process_events() -> None:
    import FreeCADGui as Gui
    from PySide import QtCore, QtWidgets

    Gui.updateGui()
    QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _open_player(simulation: Any, time_seconds: float) -> Any:
    from CommandCreateSimulation import openSimulation

    return openSimulation(
        simulation,
        autoplay=False,
        time_seconds=time_seconds,
    )


def _task_widgets(dialog: Any) -> tuple[Any, ...]:
    if dialog is None:
        return ()
    try:
        pending = list(dialog.getDialogContent())
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return ()
    result = []
    seen: set[int] = set()
    while pending:
        widget = pending.pop()
        identity = id(widget)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(widget)
        children = getattr(widget, "children", None)
        if callable(children):
            try:
                pending.extend(children())
            except (ReferenceError, RuntimeError, TypeError):
                pass
    return tuple(result)


def _document_graph(document: Any) -> tuple[Any, ...]:
    result = tuple(getattr(document, "Objects", ()) or ())
    if len(result) > MAX_PLAYBACK_DOCUMENT_OBJECTS:
        raise NativeAssemblyPlaybackError(
            f"The document exceeds the {MAX_PLAYBACK_DOCUMENT_OBJECTS}-object playback bound."
        )
    return result


def _visibility_state(
    state: AssemblySimulationState,
) -> tuple[tuple[Any, str, int, bool], ...]:
    result = []
    for obj in (*state.components, *state.grounded_joints, *state.regular_joints):
        view = getattr(obj, "ViewObject", None)
        if view is None:
            continue
        result.append((obj, str(obj.Name), int(obj.ID), bool(view.Visibility)))
    return tuple(result)


def _active_camera(document: Any) -> str:
    try:
        import FreeCADGui as Gui

        gui_document = Gui.getDocument(str(document.Name))
        view = gui_document.activeView() if gui_document is not None else None
        if view is None:
            raise RuntimeError
        return str(view.getCamera())
    except (AttributeError, ReferenceError, RuntimeError, TypeError) as exc:
        raise NativeAssemblyPlaybackError(
            "The exact Assembly playback view has no readable camera."
        ) from exc


def _camera_without_dynamic_clipping(camera: str) -> str:
    """Exclude Coin clipping planes that the renderer recalculates per frame."""

    return "\n".join(
        line
        for line in str(camera).splitlines()
        if "nearDistance" not in line and "farDistance" not in line
    )


def _camera_is_same(first: str, second: str) -> bool:
    return _camera_without_dynamic_clipping(first) == (
        _camera_without_dynamic_clipping(second)
    )


def _gui_modified(document: Any) -> bool:
    try:
        import FreeCADGui as Gui

        gui_document = Gui.getDocument(str(document.Name))
        if gui_document is None:
            raise RuntimeError
        return bool(gui_document.Modified)
    except (AttributeError, ReferenceError, RuntimeError, TypeError) as exc:
        raise NativeAssemblyPlaybackError(
            "The exact Assembly playback document has no GUI state."
        ) from exc


def _transaction_open(document: Any) -> bool:
    booked = getattr(document, "getBookedTransactionID", None)
    return bool(
        bool(getattr(document, "HasPendingTransaction", False))
        or (callable(booked) and int(booked() or 0) != 0)
    )


def _simulation_record(
    state: AssemblySimulationState, simulation: Any
) -> dict[str, Any]:
    try:
        index = state.simulations.index(simulation)
    except ValueError as exc:
        raise NativeAssemblyPlaybackError(
            "The exact simulation is not active in the human Assembly History."
        ) from exc
    return state.simulation_records[index]


def _grid_frame(
    simulation: Any,
    time_seconds: Any,
    *,
    frame_count: int | None = None,
) -> tuple[int, float]:
    requested = _finite(time_seconds, "time_seconds")
    try:
        start = float(simulation.aTimeStart.Value)
        end = float(simulation.bTimeEnd.Value)
        step = float(simulation.cTimeStepOutput.Value)
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
        raise NativeAssemblyPlaybackError(
            "The exact simulation has invalid playback parameters."
        ) from exc
    if not all(math.isfinite(value) for value in (start, end, step)) or step <= 0:
        raise NativeAssemblyPlaybackError(
            "The exact simulation has invalid playback parameters."
        )
    maximum = end
    if frame_count is not None:
        if frame_count < 2:
            raise NativeAssemblyPlaybackError(
                "The exact simulation generated fewer than two frames."
            )
        maximum = start + (frame_count - 2) * step
    tolerance = max(1.0e-12, abs(step) * 1.0e-9)
    if requested < start - tolerance or requested > maximum + tolerance:
        raise NativeAssemblyPlaybackError(
            f"time_seconds must be on the generated range {start:g} through {maximum:g}."
        )
    offset = (requested - start) / step
    interval = int(round(offset))
    exact = start + interval * step
    if not math.isclose(requested, exact, rel_tol=0.0, abs_tol=tolerance):
        raise NativeAssemblyPlaybackError(
            "time_seconds must lie exactly on the simulation output-time grid."
        )
    frame = interval + 1
    if frame_count is not None and not 1 <= frame < frame_count:
        raise NativeAssemblyPlaybackError(
            "time_seconds does not identify one generated solver frame."
        )
    return frame, exact


def _same_solver_state(
    expected: AssemblySolverState, current: AssemblySolverState
) -> bool:
    if len(expected.records) != len(current.records):
        return False
    for before, after in zip(expected.records, current.records, strict=True):
        if (
            before.obj is not after.obj
            or int(before.obj.ID) != int(after.obj.ID)
            or str(before.obj.TypeId) != str(after.obj.TypeId)
            or not placement_is_same(before.placement, after.placement)
            or before.placement_locks != after.placement_locks
        ):
            return False
    return True


def _session_is_live(session: _PlaybackSession) -> bool:
    try:
        active_dialog = _active_task_dialog()
        return (
            session.document.getObject(str(session.assembly.Name)) is session.assembly
            and session.document.getObject(str(session.simulation.Name))
            is session.simulation
            # activeTaskDialog() intentionally returns a new Python wrapper on
            # every call.  The Qt form is the stable identity of the exact C++
            # task, so prove ownership through the active dialog's contents.
            and active_dialog is not None
            and any(widget is session.form for widget in _task_widgets(active_dialog))
            and session.panel.form is session.form
            and bool(session.panel.playback_only)
            and bool(session.panel._ownsLiveTaskContext())
        )
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _session_for_document(document: Any) -> _PlaybackSession | None:
    uid = str(getattr(document, "Uid", "") or "")
    with _SESSIONS_LOCK:
        session = _SESSIONS.get(uid)
        if session is None:
            return None
        if session.document is document and _session_is_live(session):
            return session
        _forget_session(uid, session.playback_id)
        return None


def owns_active_native_assembly_playback(document: Any) -> bool:
    """Return whether the active task is the exact player opened by Native."""

    return _session_for_document(document) is not None


def _status(session: _PlaybackSession, operation: str) -> dict[str, Any]:
    try:
        frame = int(session.form.frameSlider.value())
        frame_count = int(session.assembly.numberOfFrames())
        playing = bool(session.panel.animationTimer.isActive())
        direction = (
            "forward"
            if playing and int(session.panel.direction) >= 0
            else "backward"
            if playing
            else "paused"
        )
        from CommandCreateSimulation import _simulationFrameTime

        displayed_time = _simulationFrameTime(session.simulation, frame)
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
        raise NativeAssemblyPlaybackError(
            "The exact Native simulation player became unavailable."
        ) from exc
    return {
        "operation": operation,
        "playback_id": session.playback_id,
        "simulation": object_reference(session.simulation),
        "assembly": object_reference(session.assembly),
        "frame": frame,
        "frame_count": frame_count,
        "time_seconds": None if displayed_time is None else float(displayed_time),
        "playing": playing,
        "direction": direction,
        "kinematics_generated": frame_count >= 2,
        "restoration_pending": True,
    }


def active_native_assembly_playback_summary(assembly: Any) -> dict[str, Any]:
    document = getattr(assembly, "Document", None)
    session = _session_for_document(document) if document is not None else None
    if session is None or session.assembly is not assembly:
        return {"active": False}
    result = _status(session, "status")
    result.pop("operation", None)
    result["active"] = True
    return result


def _validate_open(
    context: NativeRuntimeContext,
    spec: AssemblyPlaybackOpenSpec,
) -> tuple[AssemblySimulationState, Any, Any, int, float]:
    if not isinstance(spec, AssemblyPlaybackOpenSpec):
        raise TypeError("spec must be an AssemblyPlaybackOpenSpec")
    if not isinstance(spec.simulation_ref, NativeObjectRef):
        raise TypeError("spec.simulation_ref must be a NativeObjectRef")
    context.guard()
    if _active_task_dialog() is not None:
        raise NativeAssemblyPlaybackError(
            "Close the active task before opening an Assembly simulation."
        )
    if _session_for_document(context.document) is not None:
        raise NativeAssemblyPlaybackError(
            "Close the active Native Assembly playback before opening another."
        )
    if _transaction_open(context.document):
        raise NativeAssemblyPlaybackError(
            "Finish or cancel the active document transaction before playback."
        )
    if bool(getattr(context.document, "Recomputing", False)) or bool(
        getattr(context.document, "RecomputePending", False)
    ):
        raise NativeAssemblyPlaybackError(
            "Wait for the exact Assembly document to finish recomputing."
        )
    mode = str(spec.mode or "")
    if mode not in _PLAYBACK_MODES:
        raise NativeAssemblyPlaybackError("mode must be hold, forward, or backward.")
    expected_digest = _digest(spec.expected_simulation_state_sha256)
    simulation = resolve_object(
        context.document,
        spec.simulation_ref,
        expected_types=("App::FeaturePython",),
    )
    assembly = read_active_assembly(context.document)
    if assembly is None:
        raise NativeAssemblyPlaybackError(
            "Activate the exact owning Assembly before opening its simulation."
        )
    try:
        state = capture_assembly_simulation_state(assembly)
    except Exception as exc:
        raise NativeAssemblyPlaybackError(str(exc)) from exc
    if state.state_sha256 != expected_digest:
        raise NativeAssemblyPlaybackError(
            "The Assembly simulation state changed; read current Assemble state and retry."
        )
    _simulation_record(state, simulation)
    proxy = getattr(simulation, "Proxy", None)
    getter = getattr(proxy, "getAssembly", None)
    if not callable(getter) or getter(simulation) is not assembly:
        raise NativeAssemblyPlaybackError(
            "The exact simulation has no valid native Assembly owner."
        )
    frame, exact_time = _grid_frame(simulation, spec.time_seconds)
    return state, assembly, simulation, frame, exact_time


def open_native_assembly_playback(
    context: NativeRuntimeContext,
    spec: AssemblyPlaybackOpenSpec,
    *,
    opener: Callable[[Any, float], Any] = _open_player,
    event_pump: Callable[[], None] = _process_events,
) -> dict[str, Any]:
    """Generate frames and open one exact read-only native player."""

    if not isinstance(context, NativeRuntimeContext):
        raise TypeError("context must be a NativeRuntimeContext")
    state, assembly, simulation, expected_frame, exact_time = _validate_open(
        context,
        spec,
    )
    document = context.document
    graph_before = _document_graph(document)
    solver_before = state.solver_state
    visibility_before = _visibility_state(state)
    camera_before = _active_camera(document)
    modified_before = _gui_modified(document)
    selection_before = read_current_selection(document)
    panel = None
    dialog = None
    playback_id = ""
    try:
        panel = opener(simulation, exact_time)
        event_pump()
        dialog = _active_task_dialog()
        frame_count = int(assembly.numberOfFrames())
        if (
            panel is None
            or dialog is None
            or not bool(getattr(panel, "playback_only", False))
            or getattr(panel, "assembly", None) is not assembly
            or getattr(panel, "simFeaturePy", None) is not simulation
            or bool(getattr(panel, "document_was_modified", None)) != modified_before
            or not bool(panel._ownsLiveTaskContext())
            or int(panel.form.frameSlider.value()) != expected_frame
            or frame_count < 2
            or _document_graph(document) != graph_before
            or _transaction_open(document)
            or read_current_selection(document) != selection_before
        ):
            raise NativeAssemblyPlaybackError(
                "The native player failed its exact launch postcondition."
            )
        current = capture_assembly_simulation_state(assembly)
        if (
            current.components != state.components
            or current.grounded_joints != state.grounded_joints
            or current.regular_joints != state.regular_joints
            or current.simulation_records != state.simulation_records
            or not same_assembly(assembly, read_active_assembly(document))
        ):
            raise NativeAssemblyPlaybackError(
                "Simulation generation changed the durable Assembly graph."
            )
        playback_id = secrets.token_hex(16)
        session = _PlaybackSession(
            playback_id=playback_id,
            document=document,
            assembly=assembly,
            simulation=simulation,
            panel=panel,
            dialog=dialog,
            form=panel.form,
            state_before=state,
            solver_before=solver_before,
            document_objects_before=graph_before,
            visibility_before=visibility_before,
            camera_before=camera_before,
            modified_before=modified_before,
        )
        with _SESSIONS_LOCK:
            _SESSIONS[context.document_uid] = session
        # Task teardown (including document deletion) must not retain the
        # document, panel, or solver graph through the process-global registry.
        panel.form.destroyed.connect(
            lambda _object=None, uid=context.document_uid, token=playback_id: (
                _forget_session(uid, token)
            )
        )
        if spec.mode == "forward":
            panel.animationTimerStartForward()
        elif spec.mode == "backward":
            panel.animationTimerStartBackward()
        event_pump()
        return _status(session, "open")
    except Exception as exc:
        if dialog is None:
            dialog = _active_task_dialog()
        if dialog is not None:
            try:
                dialog.reject()
                event_pump()
            except (AttributeError, ReferenceError, RuntimeError):
                pass
        _forget_session(context.document_uid, playback_id)
        if isinstance(exc, NativeAssemblyPlaybackError):
            raise
        raise NativeAssemblyPlaybackError(
            "The exact Assembly simulation could not be opened."
        ) from exc


def _require_session(
    context: NativeRuntimeContext,
    spec: AssemblyPlaybackControlSpec,
) -> _PlaybackSession:
    if not isinstance(spec, AssemblyPlaybackControlSpec):
        raise TypeError("spec must be an AssemblyPlaybackControlSpec")
    if not isinstance(spec.simulation_ref, NativeObjectRef):
        raise TypeError("spec.simulation_ref must be a NativeObjectRef")
    context.guard(allow_owned_playback=True)
    playback_id = _playback_id(spec.playback_id)
    simulation = resolve_object(
        context.document,
        spec.simulation_ref,
        expected_types=("App::FeaturePython",),
    )
    session = _session_for_document(context.document)
    if (
        session is None
        or session.playback_id != playback_id
        or session.simulation is not simulation
    ):
        raise NativeAssemblyPlaybackError(
            "The exact Native Assembly playback is no longer active."
        )
    if _document_graph(context.document) != session.document_objects_before:
        raise NativeAssemblyPlaybackError(
            "The Assembly document graph changed during playback; close the player."
        )
    try:
        current = capture_assembly_simulation_state(session.assembly)
    except Exception as exc:
        raise NativeAssemblyPlaybackError(
            "The durable Assembly simulation graph changed during playback."
        ) from exc
    if (
        current.components != session.state_before.components
        or current.grounded_joints != session.state_before.grounded_joints
        or current.regular_joints != session.state_before.regular_joints
        or current.simulation_records != session.state_before.simulation_records
        or not same_assembly(
            session.assembly,
            read_active_assembly(context.document),
        )
    ):
        raise NativeAssemblyPlaybackError(
            "The durable Assembly simulation graph changed during playback."
        )
    return session


def control_native_assembly_playback(
    context: NativeRuntimeContext,
    operation: str,
    spec: AssemblyPlaybackControlSpec,
    *,
    event_pump: Callable[[], None] = _process_events,
) -> dict[str, Any]:
    """Seek, step, play, pause, or close one exact Native-owned player."""

    if not isinstance(context, NativeRuntimeContext):
        raise TypeError("context must be a NativeRuntimeContext")
    clean_operation = str(operation or "")
    if clean_operation not in {"seek", "step", "play", "pause", "close"}:
        raise NativeAssemblyPlaybackError("Unknown Assembly playback operation.")
    session = _require_session(context, spec)
    panel = session.panel
    if clean_operation == "seek":
        frame, _exact_time = _grid_frame(
            session.simulation,
            spec.time_seconds,
            frame_count=int(session.assembly.numberOfFrames()),
        )
        panel.stopAnimation()
        panel.setFrameValue(frame)
    elif clean_operation == "step":
        direction = str(spec.direction or "")
        if direction not in _PLAYBACK_DIRECTIONS:
            raise NativeAssemblyPlaybackError("direction must be forward or backward.")
        if direction == "forward":
            panel.stepForward()
        else:
            panel.stepBackward()
    elif clean_operation == "play":
        direction = str(spec.direction or "")
        if direction not in _PLAYBACK_DIRECTIONS:
            raise NativeAssemblyPlaybackError("direction must be forward or backward.")
        if direction == "forward":
            panel.animationTimerStartForward()
        else:
            panel.animationTimerStartBackward()
    elif clean_operation == "pause":
        panel.stopAnimation()
    else:
        return _close_playback(session, event_pump=event_pump)
    event_pump()
    if not _session_is_live(session):
        raise NativeAssemblyPlaybackError(
            "The exact Native Assembly player closed during playback control."
        )
    return _status(session, clean_operation)


def _close_playback(
    session: _PlaybackSession,
    *,
    event_pump: Callable[[], None],
) -> dict[str, Any]:
    selection_before_close = read_current_selection(session.document)
    try:
        session.panel.stopAnimation()
        session.dialog.reject()
        event_pump()
    finally:
        _forget_session(
            str(getattr(session.document, "Uid", "") or ""),
            session.playback_id,
        )
    try:
        current = capture_assembly_simulation_state(session.assembly)
        camera_restored = _camera_is_same(
            _active_camera(session.document),
            session.camera_before,
        )
        visibility_restored = all(
            session.document.getObject(name) is obj
            and int(obj.ID) == object_id
            and bool(obj.ViewObject.Visibility) is visible
            for obj, name, object_id, visible in session.visibility_before
        )
        checks = {
            "task": _active_task_dialog() is None,
            "document graph": (
                _document_graph(session.document) == session.document_objects_before
            ),
            "components": current.components == session.state_before.components,
            "grounded joints": (
                current.grounded_joints == session.state_before.grounded_joints
            ),
            "joints": current.regular_joints == session.state_before.regular_joints,
            "simulation records": (
                current.simulation_records == session.state_before.simulation_records
            ),
            "placements": _same_solver_state(
                session.solver_before,
                current.solver_state,
            ),
            "selection": (
                read_current_selection(session.document) == selection_before_close
            ),
            "visibility": visibility_restored,
            "camera": camera_restored,
            "document modified state": (
                _gui_modified(session.document)
                == bool(session.panel.document_was_modified)
            ),
        }
    except Exception as exc:
        raise NativeAssemblyPlaybackError(
            "The Assembly player closed but its exact presentation could not be verified."
        ) from exc
    failed = [name for name, succeeded in checks.items() if not succeeded]
    if failed:
        raise NativeAssemblyPlaybackError(
            "The Assembly player did not restore its exact launch "
            f"presentation: {', '.join(failed)}."
        )
    return {
        "operation": "close",
        "playback_id": session.playback_id,
        "simulation": object_reference(session.simulation),
        "assembly": object_reference(session.assembly),
        "closed": True,
        "playing": False,
        "restored": [
            "placements",
            "visibility",
            "selection",
            "camera",
            "document_modified_state",
        ],
        "document_graph_unchanged": True,
    }
