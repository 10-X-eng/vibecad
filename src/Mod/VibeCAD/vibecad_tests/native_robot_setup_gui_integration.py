# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI parity and lifecycle gate for Native Robot creation."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import RobotGui  # noqa: F401 - registers the human Robot commands
from VibeCADCore import get_service
import VibeCADGui as VibeGui
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeInput import authorize_native_input_path
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRobotSetup import NativeRobotSetupError
from VibeCADNativeRobotSetupSchema import (
    ROBOT_SETUP_CAPABILITY_NAME,
    robot_setup_capability_definition,
)
from VibeCADNativeRobotState import capture_robot_setup_state
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface
import VibeCADNativeRobotSetupRuntime as runtime_module


def _process_events(rounds: int = 20) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _select_assemble_ribbon(main_window) -> tuple[object, object]:
    controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
    tabs = main_window.findChild(QtWidgets.QTabBar, "VibeCADRibbonTabs")
    assert controller is not None and tabs is not None
    index = next(
        candidate
        for candidate in range(tabs.count())
        if str(tabs.tabData(candidate)) == "AssemblyWorkbench"
    )
    tabs.setCurrentIndex(index)
    _process_events(24)
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "assemble"
    assert "Robot_Create" in surface.command_ids
    return controller, surface


def _queue_file_dialogs(paths: list[Path | None]) -> None:
    pending = list(paths)
    seen: set[int] = set()
    attempts = {"remaining": 1600}

    def respond() -> None:
        for widget in QtWidgets.QApplication.topLevelWidgets():
            if not isinstance(widget, QtWidgets.QFileDialog):
                continue
            if not widget.isVisible() or id(widget) in seen:
                continue
            seen.add(id(widget))
            response = pending.pop(0)
            if response is None:
                widget.reject()
            else:
                widget.setDirectory(str(response.parent))
                file_name = widget.findChild(QtWidgets.QLineEdit, "fileNameEdit")
                if file_name is None:
                    seen.remove(id(widget))
                    break
                file_name.setText(response.name)
                widget.accept()
            if pending:
                QtCore.QTimer.singleShot(5, respond)
            return
        attempts["remaining"] -= 1
        if pending and attempts["remaining"] > 0:
            QtCore.QTimer.singleShot(5, respond)

    QtCore.QTimer.singleShot(0, respond)


def _run_human_create(vrml_path: Path, csv_path: Path):
    assert Gui.isCommandActive("Robot_Create")
    _queue_file_dialogs([vrml_path, csv_path])
    Gui.runCommand("Robot_Create", 0)
    _process_events(30)


def _focused_turn(surface, registry) -> NativeTurnSnapshot:
    state = registry.definition("state.read")
    setup = robot_setup_capability_definition()
    assert state is not None
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=("state.read", ROBOT_SETUP_CAPABILITY_NAME),
            schemas=(
                state.provider_schema(("active", "selection")),
                setup.provider_schema(("create",)),
            ),
            human_only_action_ids=("Assembly_ActivateAssembly",),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _parity_record(data: dict) -> dict:
    return {
        key: value
        for key, value in data.items()
        if key not in {"object", "object_id", "label"}
    }


def _arguments(state, *, label: str = "Native six-axis robot") -> dict:
    return {
        "operation": "create",
        "label": label,
        "expected_state_sha256": state.state_sha256,
        "expected_robot_count": len(state.robots),
    }


def _write_definition_files(root: Path) -> tuple[Path, Path, Path, Path]:
    vrml_path = root / "human selected robot.wrl"
    vrml_path.write_text(
        "#VRML V2.0 utf8\n"
        "Transform { children [ Shape { geometry Box { size 100 100 100 } } ] }\n",
        encoding="utf-8",
    )
    csv_path = root / "human selected kinematics.csv"
    csv_path.write_text(
        "a,alpha,d,theta,rotation,max,min,velocity\n"
        "500,-90,1045,0,-1,185,-185,156\n"
        "1300,0,0,0,1,35,-155,156\n"
        "55,90,0,-90,1,154,-130,156\n"
        "0,-90,-1025,0,1,350,-350,330\n"
        "0,90,0,0,1,130,-130,330\n"
        "0,180,-300,0,1,350,-350,615\n",
        encoding="utf-8",
    )
    malformed_path = root / "malformed kinematics.csv"
    malformed_path.write_text(
        "a,alpha,d,theta,rotation,max,min,velocity\n"
        + "\n".join(["0,0,0,0,0,180,-180,100"] * 6)
        + "\n",
        encoding="utf-8",
    )
    drift_path = root / "drifting kinematics.csv"
    drift_path.write_bytes(csv_path.read_bytes())
    return vrml_path, csv_path, malformed_path, drift_path


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("AssemblyWorkbench")
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-robot-setup-")
        root = Path(temporary.name)
        document_path = root / "native-robot-setup.FCStd"
        vrml_path, csv_path, malformed_path, drift_path = _write_definition_files(root)

        document = App.newDocument("NativeRobotSetupGate")
        document.UndoMode = 1
        document.saveAs(str(document_path))
        _run_human_create(vrml_path, csv_path)
        human_state = capture_robot_setup_state(document)
        assert len(human_state.robots) == 1
        human = human_state.robots[0]
        human_record = human_state.records[0]
        assert str(human.VibeCADTimelineRole) == "operation"
        assert tuple(document.VibeCADTimeline.Operations) == (human,)
        human_name = str(human.Name)
        document.save()

        VibeGui._connect_document_observer()
        main_window = Gui.getMainWindow()
        controller, surface = _select_assemble_ribbon(main_window)
        frozen = NativeSurfaceSnapshot.from_surface(surface)
        registry = build_native_capability_registry()
        definition = registry.definition(ROBOT_SETUP_CAPABILITY_NAME)
        assert definition is not None
        assert registry.implementation(ROBOT_SETUP_CAPABILITY_NAME) is not None
        assert definition.variants[0].action_ids == frozenset({"Robot_Create"})
        schema = definition.provider_schema(("create",))
        assert set(schema["parameters"]["oneOf"][0]["properties"]) == {
            "operation",
            "label",
            "expected_state_sha256",
            "expected_robot_count",
        }

        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-robot-setup-gui")
        selected_paths = {
            "robot_visual_definition": vrml_path,
            "robot_kinematic_definition": csv_path,
        }
        authorization_count = {"value": 0}
        cancel_purpose = {"value": ""}
        drift_purpose = {"value": ""}

        def authorizer(request):
            authorization_count["value"] += 1
            if request.purpose == cancel_purpose["value"]:
                return None
            path = selected_paths[request.purpose]
            authorization = authorize_native_input_path(request, path)
            if request.purpose == drift_purpose["value"]:
                path.write_bytes(path.read_bytes() + b"\n")
            return authorization

        def reauthorize() -> None:
            require_frozen_native_surface(frozen, controller)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state_store,
            undo_ledger=ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(controller).surface_id,
            edit_or_task_active=lambda: bool(Gui.Control.activeDialog()),
            authorize_input=authorizer,
        )
        turn = _focused_turn(surface, registry)
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state_store,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )
        call_index = 0

        def call(arguments: dict, *, succeeds: bool = True, call_id: str = "") -> dict:
            nonlocal call_index
            call_index += 1
            selection_before = tuple(
                (item.Object, tuple(item.SubElementNames))
                for item in Gui.Selection.getSelectionEx("", 0)
            )
            active_task = Gui.Control.activeTaskDialog()
            result = dispatcher.call(
                ROBOT_SETUP_CAPABILITY_NAME,
                json.dumps(arguments, separators=(",", ":")),
                call_id or f"native-robot-setup-{call_index}",
            )
            assert result.get("ok") is succeeds, result
            assert Gui.Control.activeTaskDialog() is active_task
            assert (
                tuple(
                    (item.Object, tuple(item.SubElementNames))
                    for item in Gui.Selection.getSelectionEx("", 0)
                )
                == selection_before
            )
            return result

        document.clearUndos()
        before = capture_robot_setup_state(document)
        arguments = _arguments(before)
        names_before = tuple(obj.Name for obj in document.Objects)
        history_before = tuple(document.VibeCADTimeline.Operations)
        selection_before = tuple(Gui.Selection.getSelection())

        auth_before = authorization_count["value"]
        stale = call(
            {**arguments, "expected_state_sha256": "0" * 64},
            succeeds=False,
        )
        assert stale["error_code"] == "NATIVE_ROBOT_SETUP_FAILED"
        assert authorization_count["value"] == auth_before

        cancel_purpose["value"] = "robot_visual_definition"
        cancelled = call(arguments, succeeds=False)
        cancel_purpose["value"] = ""
        assert cancelled["error_code"] == "NATIVE_ROBOT_SETUP_FAILED"

        selected_paths["robot_kinematic_definition"] = malformed_path
        malformed = call(arguments, succeeds=False)
        selected_paths["robot_kinematic_definition"] = csv_path
        assert malformed["error_code"] == "NATIVE_ROBOT_SETUP_FAILED"
        assert "rotation direction" in malformed["error"]

        selected_paths["robot_kinematic_definition"] = drift_path
        drift_purpose["value"] = "robot_kinematic_definition"
        drifted = call(arguments, succeeds=False)
        drift_purpose["value"] = ""
        selected_paths["robot_kinematic_definition"] = csv_path
        assert drifted["error_code"] == "NATIVE_ROBOT_SETUP_FAILED"
        assert "changed after authorization" in drifted["error"]

        assert tuple(obj.Name for obj in document.Objects) == names_before
        assert tuple(document.VibeCADTimeline.Operations) == history_before
        assert tuple(Gui.Selection.getSelection()) == selection_before
        assert int(document.UndoCount) == 0
        assert not document.HasPendingTransaction

        original_verifier = runtime_module.verify_created_robot

        def reject_verification(_document, _draft):
            raise NativeRobotSetupError("Forced Robot verifier rejection.")

        runtime_module.verify_created_robot = reject_verification
        try:
            rolled_back = call(arguments, succeeds=False)
        finally:
            runtime_module.verify_created_robot = original_verifier
        assert rolled_back["error_code"] == "NATIVE_ROBOT_SETUP_FAILED"
        assert tuple(obj.Name for obj in document.Objects) == names_before
        assert tuple(document.VibeCADTimeline.Operations) == history_before
        assert int(document.UndoCount) == 0
        assert not document.HasPendingTransaction

        call_id = "native-robot-create-success"
        created = call(arguments, call_id=call_id)
        assert set(created) == {
            "ok",
            "robot",
            "label",
            "definitions",
            "robot_state_sha256",
            "setup_state_sha256",
            "robot_count",
            "receipt",
            "assistant_undo_available",
        }
        assert created["robot_count"] == 2
        assert created["label"] == arguments["label"]
        assert created["assistant_undo_available"] is True
        assert len(created["receipt"]["created"]) == 1
        assert all("path" not in key.casefold() for key in created["definitions"])
        native_name = created["robot"]["object_name"]
        native = document.getObject(native_name)
        assert native is not None
        assert tuple(document.VibeCADTimeline.Operations) == (human, native)
        after = capture_robot_setup_state(document)
        assert after.state_sha256 == created["setup_state_sha256"]
        assert after.records[-1].state_sha256 == created["robot_state_sha256"]
        native_parity = _parity_record(after.records[-1].data)
        human_parity = _parity_record(human_record.data)
        assert native_parity == human_parity, (human_parity, native_parity)
        assert int(document.UndoCount) == 1

        replay = call(arguments, call_id=call_id)
        assert replay == created
        assert int(document.UndoCount) == 1

        document.undo()
        _process_events(20)
        assert document.getObject(native_name) is None
        assert tuple(document.VibeCADTimeline.Operations) == (human,)
        assert int(document.UndoCount) == 0
        document.redo()
        _process_events(20)
        native = document.getObject(native_name)
        assert native is not None
        assert tuple(document.VibeCADTimeline.Operations) == (human, native)
        assert int(document.UndoCount) == 1

        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(document_path))
        App.setActiveDocument(document.Name)
        assert document.recompute(None, True, True) is not False
        _process_events(30)
        restored = capture_robot_setup_state(document)
        assert len(restored.robots) == 2
        assert document.getObject(human_name) is restored.robots[0]
        assert document.getObject(native_name) is restored.robots[1]
        assert restored.records[-1].state_sha256 == created["robot_state_sha256"], (
            after.records[-1].data,
            restored.records[-1].data,
        )
        assert restored.state_sha256 == created["setup_state_sha256"]
        assert tuple(document.VibeCADTimeline.Operations) == restored.robots
        assert all(record.data["valid"] for record in restored.records)

        print(
            "VIBECAD_NATIVE_ROBOT_SETUP_GUI_OK "
            "human_parity=true human_input_authority=true provider_paths=false "
            "exact_history=true exact_state=true malformed_noop=true "
            "input_drift_noop=true stale_noop=true cancel_noop=true "
            "rollback=true idempotent=true undo_redo=true reopen=true "
            "selection_preserved=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        Gui.Selection.clearSelection()
        if document is not None:
            try:
                App.closeDocument(document.Name)
            except (AttributeError, RuntimeError):
                pass
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(0, _run)
