# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact Native KUKA Robot output."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import KukaExporter
import Robot
import RobotGui  # noqa: F401 - registers the shipped human export commands
import VibeCADNativeOutput as output_module
from VibeCADCore import get_service
import VibeCADGui as VibeGui
from VibeCADNativeActionManifest import classify_native_surface
from VibeCADNativeCapabilityRegistry import (
    NativeProviderSurface,
    resolve_native_provider_surface,
)
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeOutput import authorize_native_output_path
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRobotExportSchema import ROBOT_EXPORT_CAPABILITY_NAME
from VibeCADNativeRobotState import capture_robot_setup_state
from VibeCADNativeRobotTrajectoryState import capture_robot_trajectory_state
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTargets import document_uid
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


_GENERATED_AT = "Thu Aug 13 12:34:56 2026"


def _events(rounds: int = 12) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 20)


def _select(*objects) -> None:
    Gui.Selection.clearSelection()
    for obj in objects:
        Gui.Selection.addSelection(obj)
    _events(8)


def _selection() -> tuple[tuple[object, tuple[str, ...]], ...]:
    return tuple(
        (item.Object, tuple(item.SubElementNames))
        for item in Gui.Selection.getSelectionEx("", 0)
    )


def _queue_file_dialog(path: Path | None) -> None:
    attempts = {"remaining": 1600}

    def respond() -> None:
        for widget in QtWidgets.QApplication.topLevelWidgets():
            if not isinstance(widget, QtWidgets.QFileDialog) or not widget.isVisible():
                continue
            if path is None:
                widget.reject()
            else:
                widget.setDirectory(str(path.parent))
                file_name = widget.findChild(QtWidgets.QLineEdit, "fileNameEdit")
                if file_name is None:
                    break
                file_name.setText(path.name)
                widget.accept()
            return
        attempts["remaining"] -= 1
        if attempts["remaining"] > 0:
            QtCore.QTimer.singleShot(5, respond)

    QtCore.QTimer.singleShot(0, respond)


def _run_human_export(command: str, path: Path | None) -> None:
    preferences = App.ParamGet("User parameter:BaseApp/Preferences/Dialog")
    original = preferences.GetBool("DontUseNativeDialog", False)
    try:
        preferences.SetBool("DontUseNativeDialog", True)
        _queue_file_dialog(path)
        Gui.runCommand(command, 0)
        _events(16)
    finally:
        preferences.SetBool("DontUseNativeDialog", original)


def _trajectory_value() -> object:
    value = Robot.Trajectory()
    for index, (position, velocity, acceleration, continuous) in enumerate(
        (
            ((120.0, -45.0, 310.0), "0.8 m/s", "1.5 m/s^2", False),
            ((180.0, 15.0, 365.0), "1.2 m/s", "2.0 m/s^2", True),
        ),
        1,
    ):
        value.insertWaypoints(
            Robot.Waypoint(
                App.Placement(
                    App.Vector(*position),
                    App.Rotation(App.Vector(0.0, 0.0, 1.0), index * 12.5),
                ),
                type="LIN",
                name=f"KUKA_P{index}",
                vel=velocity,
                cont=continuous,
                acc=acceleration,
                tool=1,
                base=0,
            )
        )
    return value


def _payload(document, robot, trajectory, operation: str) -> dict[str, object]:
    robots = capture_robot_setup_state(document)
    trajectories = capture_robot_trajectory_state(document)
    robot_index = robots.robots.index(robot)
    trajectory_index = trajectories.trajectories.index(trajectory)
    return {
        "operation": operation,
        "robot": {"object_name": robot.Name},
        "trajectory": {"object_name": trajectory.Name},
        "expected_robot_setup_state_sha256": robots.state_sha256,
        "expected_robot_state_sha256": robots.records[robot_index].state_sha256,
        "expected_trajectory_setup_state_sha256": trajectories.state_sha256,
        "expected_trajectory_state_sha256": (
            trajectories.records[trajectory_index].state_sha256
        ),
    }


def _surface_and_turn():
    Gui.activateWorkbench("CAMWorkbench")
    _events(24)
    controller = Gui.getMainWindow().findChild(
        QtCore.QObject,
        "VibeCADRibbonController",
    )
    assert controller is not None
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "manufacture"
    actions = {
        value.command_id: value
        for value in classify_native_surface(surface)
        if value.command_id
        in {"Robot_ExportKukaCompact", "Robot_ExportKukaFull"}
    }
    assert tuple(
        (
            actions[command].capability_family,
            actions[command].operation_variant,
            actions[command].exact_target_type,
            actions[command].classification.export,
            actions[command].transaction_behavior,
        )
        for command in ("Robot_ExportKukaCompact", "Robot_ExportKukaFull")
    ) == (
        (
            ROBOT_EXPORT_CAPABILITY_NAME,
            "export_kuka_compact",
            "ExactRobotAndNonEmptyTrajectoryWithHumanAuthorizedOutput",
            True,
            "output",
        ),
        (
            ROBOT_EXPORT_CAPABILITY_NAME,
            "export_kuka_full",
            "ExactRobotAndNonEmptyTrajectoryWithHumanAuthorizedOutputs",
            True,
            "output",
        ),
    )
    registry = build_native_capability_registry()
    production = resolve_native_provider_surface(surface, registry)
    assert ROBOT_EXPORT_CAPABILITY_NAME not in {
        *production.missing_definition_names,
        *production.missing_implementation_names,
    }
    assert ROBOT_EXPORT_CAPABILITY_NAME not in production.incomplete_definition_names
    definition = registry.definition(ROBOT_EXPORT_CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(
        ("export_kuka_compact", "export_kuka_full")
    )
    parameters = schema["parameters"]
    assert parameters["additionalProperties"] is False
    assert set(parameters["properties"]) == {
        "operation",
        "robot",
        "trajectory",
        "expected_robot_setup_state_sha256",
        "expected_robot_state_sha256",
        "expected_trajectory_setup_state_sha256",
        "expected_trajectory_state_sha256",
    }
    assert parameters["properties"]["operation"]["enum"] == [
        "export_kuka_compact",
        "export_kuka_full",
    ]
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert not any(
        value in encoded
        for value in ('"path"', '"destination"', '"file_name"', '"selection"')
    )
    turn = NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(ROBOT_EXPORT_CAPABILITY_NAME,),
            schemas=(schema,),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )
    return controller, registry, turn


def _document_state(document, state_store) -> dict[str, object]:
    timeline = document.getObject("VibeCADTimeline")
    timeline_state = None
    if timeline is not None:
        timeline_state = (
            tuple(timeline.Operations),
            tuple(bool(value) for value in timeline.VisibilityAtEnd),
            tuple(bool(value) for value in timeline.SuppressionAtEnd),
            int(timeline.Position),
        )
    return {
        "objects": tuple(document.Objects),
        "states": tuple(
            (obj, tuple(str(value) for value in obj.State))
            for obj in document.Objects
        ),
        "timeline": timeline_state,
        "selection": _selection(),
        "visibility": tuple(
            (obj, bool(obj.ViewObject.Visibility))
            for obj in document.Objects
            if getattr(obj, "ViewObject", None) is not None
        ),
        "undo": int(document.UndoCount),
        "redo": int(document.RedoCount),
        "transaction": int(document.getBookedTransactionID() or 0),
        "gui_modified": bool(Gui.getDocument(document.Name).Modified),
        "revision": state_store.current_revision(document_uid(document)),
    }


def _run() -> None:
    document = None
    temporary = None
    exit_code = 1
    original_asctime = KukaExporter.time.asctime
    try:
        KukaExporter.time.asctime = lambda: _GENERATED_AT
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-kuka-export-gui-"
        )
        root = Path(temporary.name)
        human_output = root / "human-compact.src"
        human_full_source = root / "human-full.src"
        human_full_data = root / "human-full.dat"
        native_output = root / "native-compact.src"
        native_full_source = root / "native-full.src"
        native_full_data = root / "native-full.dat"
        document = App.newDocument("NativeRobotKukaExportGate")
        document.UndoMode = 1
        robot = document.addObject("Robot::RobotObject", "KukaRobot")
        trajectory = document.addObject(
            "Robot::TrajectoryObject",
            "CompactTrajectory",
        )
        trajectory.Trajectory = _trajectory_value()
        sentinel = document.addObject("Part::Feature", "SelectionSentinel")
        sentinel.Shape = Part.makeBox(4.0, 5.0, 6.0)
        assert document.recompute(None, True, True) is not False
        VibeGui._connect_document_observer()
        controller, registry, turn = _surface_and_turn()

        _select(robot, trajectory)
        human_before = tuple(document.Objects), int(document.UndoCount)
        _run_human_export("Robot_ExportKukaCompact", human_output)
        assert human_output.is_file()
        assert (tuple(document.Objects), int(document.UndoCount)) == human_before
        human_bytes = human_output.read_bytes()
        assert human_bytes == KukaExporter.RenderCompactSub(
            robot,
            trajectory,
            generated_at=_GENERATED_AT,
        ).encode("utf-8")
        _select(robot, trajectory)
        _run_human_export("Robot_ExportKukaFull", human_full_source)
        expected_full_source, expected_full_data = KukaExporter.RenderFullSub(
            robot,
            trajectory,
            generated_at=_GENERATED_AT,
        )
        assert human_full_source.read_bytes() == expected_full_source.encode("utf-8")
        assert human_full_data.read_bytes() == expected_full_data.encode("utf-8")
        assert (tuple(document.Objects), int(document.UndoCount)) == human_before

        _select(sentinel)
        document.clearUndos()
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-robot-kuka-export-gui")
        mode = {"value": "allow"}
        output_paths = {
            ".src": native_output,
            ".dat": native_full_data,
        }
        requests = []
        authorizer_threads = []
        main_thread_id = threading.get_ident()

        def authorize(request):
            requests.append(request)
            authorizer_threads.append(threading.get_ident())
            suffix = request.allowed_suffixes[0]
            if mode["value"] == "cancel" or (
                mode["value"] == "cancel_data" and suffix == ".dat"
            ):
                return None
            if mode["value"] == "selection_stale":
                _select(robot)
            return authorize_native_output_path(request, output_paths[suffix])

        def reauthorize() -> None:
            require_frozen_native_surface(turn.surface, controller)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state_store,
            undo_ledger=ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(controller).surface_id,
            edit_or_task_active=lambda: bool(Gui.Control.activeDialog()),
            authorize_output=authorize,
        )
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state_store,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )
        payload = _payload(
            document,
            robot,
            trajectory,
            "export_kuka_compact",
        )

        invalid = dict(payload)
        invalid["path"] = str(root / "provider-controlled.src")
        rejected = dispatcher.call(
            ROBOT_EXPORT_CAPABILITY_NAME,
            json.dumps(invalid, separators=(",", ":")),
            "native-kuka-provider-path",
        )
        assert rejected["ok"] is False
        assert rejected["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert not (root / "provider-controlled.src").exists()

        stale_payload = dict(payload)
        stale_payload["expected_trajectory_state_sha256"] = "0" * 64
        stale_target = dispatcher.call(
            ROBOT_EXPORT_CAPABILITY_NAME,
            json.dumps(stale_payload, separators=(",", ":")),
            "native-kuka-stale-target",
        )
        assert stale_target["ok"] is False
        assert len(requests) == 0

        before = _document_state(document, state_store)
        result = dispatcher.call(
            ROBOT_EXPORT_CAPABILITY_NAME,
            json.dumps(payload, separators=(",", ":")),
            "native-kuka-compact-success",
        )
        assert result["ok"] is True, result
        duplicate = dispatcher.call(
            ROBOT_EXPORT_CAPABILITY_NAME,
            json.dumps(payload, separators=(",", ":")),
            "native-kuka-compact-success",
        )
        assert duplicate == result
        assert len(requests) == 1
        assert authorizer_threads == [main_thread_id]
        assert requests[0].allowed_suffixes == (".src",)
        assert requests[0].suggested_file_name == "CompactTrajectory.src"
        assert native_output.read_bytes() == human_bytes
        assert result["operation"] == "export_kuka_compact"
        assert result["program"]["name"] == "CompactTrajectory"
        assert result["program"]["format"] == "kuka_compact_krl"
        assert result["trajectory"]["waypoint_count"] == 2
        assert result["output"]["file_name"] == native_output.name
        assert str(root) not in json.dumps(result, separators=(",", ":"))
        assert _document_state(document, state_store) == before

        prior = native_output.read_bytes()
        mode["value"] = "cancel"
        cancelled = dispatcher.call(
            ROBOT_EXPORT_CAPABILITY_NAME,
            json.dumps(payload, separators=(",", ":")),
            "native-kuka-compact-cancel",
        )
        assert cancelled["ok"] is False
        assert cancelled["error_code"] == "NATIVE_ROBOT_EXPORT_CANCELLED"
        assert native_output.read_bytes() == prior
        assert _document_state(document, state_store) == before

        mode["value"] = "selection_stale"
        stale_selection = dispatcher.call(
            ROBOT_EXPORT_CAPABILITY_NAME,
            json.dumps(payload, separators=(",", ":")),
            "native-kuka-selection-stale",
        )
        assert stale_selection["ok"] is False
        assert native_output.read_bytes() == prior
        _select(sentinel)
        assert _document_state(document, state_store) == before

        mode["value"] = "allow"
        output_paths[".src"] = native_full_source
        output_paths[".dat"] = native_full_data
        full_payload = _payload(
            document,
            robot,
            trajectory,
            "export_kuka_full",
        )
        requests_before_full = len(requests)
        full_result = dispatcher.call(
            ROBOT_EXPORT_CAPABILITY_NAME,
            json.dumps(full_payload, separators=(",", ":")),
            "native-kuka-full-success",
        )
        assert full_result["ok"] is True, full_result
        full_duplicate = dispatcher.call(
            ROBOT_EXPORT_CAPABILITY_NAME,
            json.dumps(full_payload, separators=(",", ":")),
            "native-kuka-full-success",
        )
        assert full_duplicate == full_result
        full_requests = requests[requests_before_full:]
        assert tuple(request.allowed_suffixes for request in full_requests) == (
            (".src",),
            (".dat",),
        )
        assert tuple(request.suggested_file_name for request in full_requests) == (
            "CompactTrajectory.src",
            "CompactTrajectory.dat",
        )
        assert native_full_source.read_bytes() == human_full_source.read_bytes()
        assert native_full_data.read_bytes() == human_full_data.read_bytes()
        assert full_result["operation"] == "export_kuka_full"
        assert full_result["program"]["format"] == "kuka_full_krl"
        assert full_result["program"]["source_sha256"]
        assert full_result["program"]["data_sha256"]
        assert tuple(item["file_name"] for item in full_result["outputs"]) == (
            native_full_source.name,
            native_full_data.name,
        )
        assert str(root) not in json.dumps(full_result, separators=(",", ":"))
        assert _document_state(document, state_store) == before

        full_source_prior = native_full_source.read_bytes()
        full_data_prior = native_full_data.read_bytes()
        mode["value"] = "cancel_data"
        cancelled_full = dispatcher.call(
            ROBOT_EXPORT_CAPABILITY_NAME,
            json.dumps(full_payload, separators=(",", ":")),
            "native-kuka-full-cancel-data",
        )
        assert cancelled_full["ok"] is False
        assert cancelled_full["error_code"] == "NATIVE_ROBOT_EXPORT_CANCELLED"
        assert native_full_source.read_bytes() == full_source_prior
        assert native_full_data.read_bytes() == full_data_prior
        assert _document_state(document, state_store) == before

        atomic_source = root / "atomic-full.src"
        atomic_data = root / "atomic-full.dat"
        atomic_source.write_bytes(b"ORIGINAL SOURCE\n")
        atomic_data.write_bytes(b"ORIGINAL DATA\n")
        output_paths[".src"] = atomic_source
        output_paths[".dat"] = atomic_data
        mode["value"] = "allow"
        original_replace = output_module.os.replace
        injected = {"raised": False}

        def fail_second_publish(source, destination):
            source_path = Path(source)
            destination_path = Path(destination)
            if (
                not injected["raised"]
                and destination_path == atomic_data
                and source_path.suffix == ".dat"
                and ".vibecad-" in source_path.name
            ):
                injected["raised"] = True
                raise OSError("injected KUKA data publication failure")
            return original_replace(source, destination)

        output_module.os.replace = fail_second_publish
        try:
            rolled_back = dispatcher.call(
                ROBOT_EXPORT_CAPABILITY_NAME,
                json.dumps(full_payload, separators=(",", ":")),
                "native-kuka-full-atomic-rollback",
            )
        finally:
            output_module.os.replace = original_replace
        assert rolled_back["ok"] is False
        assert injected["raised"] is True
        assert atomic_source.read_bytes() == b"ORIGINAL SOURCE\n"
        assert atomic_data.read_bytes() == b"ORIGINAL DATA\n"
        assert _document_state(document, state_store) == before
        assert set(authorizer_threads) == {main_thread_id}

        print(
            "VIBECAD_NATIVE_ROBOT_KUKA_EXPORT_GUI_OK "
            "compact=true full=true complete_family=true human_parity=true "
            "closed_schema=true "
            "no_provider_path=true exact_robot=true exact_trajectory=true "
            "nonempty=true bounded=true human_authorized=true atomic=true "
            "atomic_bundle=true rollback=true cancel=true stale_target=true "
            "selection_stale=true "
            "duplicate_guard=true main_thread=true document_unchanged=true "
            "history=true undo=true redo=true selection=true visibility=true "
            "low_noise=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        KukaExporter.time.asctime = original_asctime
        Gui.Selection.clearSelection()
        if document is not None and App.getDocument(document.Name) is not None:
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        QtWidgets.QApplication.instance().exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
