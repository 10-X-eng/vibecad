# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for Robot tool and waypoint configuration."""

from __future__ import annotations

import __main__
import json
from pathlib import Path
import tempfile
import traceback
import zipfile

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import Robot  # noqa: F401 - registers the Robot document factory
import RobotGui  # noqa: F401 - registers the human Robot commands
from VibeCADCore import get_service
import VibeCADGui as VibeGui
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRobotDefaultsState import capture_robot_waypoint_defaults
from VibeCADNativeRobotSetup import NativeRobotSetupError
from VibeCADNativeRobotSetupSchema import (
    ROBOT_SETUP_CAPABILITY_NAME,
    robot_setup_capability_definition,
)
from VibeCADNativeRobotState import capture_robot_setup_state
from VibeCADNativeRobotToolState import (
    capture_robot_tool_shape_inventory,
    capture_robot_tool_shape_record,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface
import VibeCADNativeRobotDefaults as defaults_module
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
    return controller, surface


def _focused_turn(surface, registry) -> NativeTurnSnapshot:
    state = registry.definition("state.read")
    setup = robot_setup_capability_definition()
    assert state is not None
    operations = (
        "add_tool_shape",
        "set_default_orientation",
        "set_default_values",
    )
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=("state.read", ROBOT_SETUP_CAPABILITY_NAME),
            schemas=(
                state.provider_schema(("active", "selection")),
                setup.provider_schema(operations),
            ),
            human_only_action_ids=("Assembly_ActivateAssembly",),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _select(*objects) -> None:
    Gui.Selection.clearSelection()
    for obj in objects:
        Gui.Selection.addSelection(obj)
    _process_events(8)


def _queue_default_value_dialogs() -> None:
    values = {
        "Set default speed": "2 m/s",
        "Set default continuity": "True",
        "Set default acceleration": "3 m/s^2",
    }
    completed: set[str] = set()
    attempts = {"remaining": 1000}

    def respond() -> None:
        for widget in QtWidgets.QApplication.topLevelWidgets():
            if not isinstance(widget, QtWidgets.QInputDialog) or not widget.isVisible():
                continue
            title = widget.windowTitle()
            if title in completed or title not in values:
                continue
            value = values[title]
            combo = widget.findChild(QtWidgets.QComboBox)
            if combo is None:
                widget.setTextValue(value)
            else:
                index = combo.findText(value)
                assert index >= 0
                combo.setCurrentIndex(index)
            completed.add(title)
            widget.accept()
            QtCore.QTimer.singleShot(5, respond)
            return
        attempts["remaining"] -= 1
        if len(completed) < len(values) and attempts["remaining"] > 0:
            QtCore.QTimer.singleShot(5, respond)

    QtCore.QTimer.singleShot(0, respond)


def _queue_orientation_dialog() -> None:
    fields = {
        "xPos": "11.25 mm",
        "yPos": "-7.5 mm",
        "zPos": "3.75 mm",
        "xAxis": "0",
        "yAxis": "0",
        "zAxis": "1",
        "angle": "30 deg",
    }
    attempts = {"remaining": 1000}

    def respond() -> None:
        for widget in QtWidgets.QApplication.topLevelWidgets():
            if (
                not isinstance(widget, QtWidgets.QDialog)
                or isinstance(
                    widget,
                    (
                        QtWidgets.QFileDialog,
                        QtWidgets.QInputDialog,
                        QtWidgets.QMessageBox,
                    ),
                )
                or not widget.isVisible()
            ):
                continue
            rotation_input = widget.findChild(QtWidgets.QComboBox, "rotationInput")
            if rotation_input is None:
                continue
            rotation_input.setCurrentIndex(0)
            for name, value in fields.items():
                spin = widget.findChild(QtWidgets.QAbstractSpinBox, name)
                assert spin is not None, name
                editor = spin.lineEdit()
                assert editor is not None, name
                editor.setText(value)
                spin.interpretText()
            widget.accept()
            return
        attempts["remaining"] -= 1
        if attempts["remaining"] > 0:
            QtCore.QTimer.singleShot(5, respond)

    QtCore.QTimer.singleShot(0, respond)


def _reset_defaults() -> None:
    __main__._DefSpeed = "1 m/s"
    __main__._DefCont = False
    __main__._DefAcceleration = "1 m/s^2"
    __main__._DefOrientation = App.Rotation()
    __main__._DefDisplacement = App.Vector(0.0, 0.0, 0.0)


def _tool_arguments(robot, tool_shape) -> dict:
    return {
        "operation": "add_tool_shape",
        "robot": {"object_name": robot.Name},
        "tool_shape": {"object_name": tool_shape.Name},
    }


def _archive_contains(path: Path, needle: bytes) -> bool:
    with zipfile.ZipFile(path) as archive:
        return any(needle in archive.read(name) for name in archive.namelist())


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("AssemblyWorkbench")
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-robot-configuration-"
        )
        root = Path(temporary.name)
        document_path = root / "native-robot-configuration.FCStd"

        document = App.newDocument("NativeRobotConfigurationGate")
        document.UndoMode = 1
        robot = document.addObject("Robot::RobotObject", "Robot")
        robot.Label = "Configuration robot"
        robot.Base = App.Placement(
            App.Vector(2.5, -1.25, 4.75),
            App.Rotation(App.Vector(1.0, 0.0, 0.0), 17.0),
        )
        human_tool = document.addObject("Part::Feature", "HumanTool")
        human_tool.Label = "Human tool shape"
        human_tool.Shape = Part.makeCylinder(4.0, 15.0)
        native_tool = document.addObject("Part::Feature", "NativeTool")
        native_tool.Label = "Native tool shape"
        native_tool.Shape = Part.makeBox(8.0, 6.0, 12.0)
        native_tool.Placement = App.Placement(
            App.Vector(1.0, 2.0, 3.0),
            App.Rotation(App.Vector(0.0, 1.0, 0.0), 12.0),
        )
        vrml_path = root / "native-tool.wrl"
        vrml_path.write_text(
            "#VRML V2.0 utf8\nShape { geometry Box { size 4 6 8 } }\n",
            encoding="utf-8",
        )
        vrml_tool = document.addObject("App::VRMLObject", "VrmlTool")
        vrml_tool.Label = "Native VRML tool shape"
        vrml_tool.VrmlFile = str(vrml_path)
        assert document.recompute(None, True, True) is not False
        document.saveAs(str(document_path))

        inventory = capture_robot_tool_shape_inventory(document)
        vrml_record = next(
            record for record in inventory.records if record.tool_shape is vrml_tool
        )
        provider_record = json.dumps(vrml_record.summary(), sort_keys=True)
        assert str(root) not in provider_record
        assert "identity_sha256" not in provider_record

        _select(robot, human_tool)
        assert Gui.isCommandActive("Robot_AddToolShape")
        human_undo = int(document.UndoCount)
        Gui.runCommand("Robot_AddToolShape", 0)
        _process_events(16)
        assert robot.ToolShape is human_tool
        assert int(document.UndoCount) == human_undo + 1
        document.undo()
        _process_events(12)
        assert robot.ToolShape is None
        document.redo()
        _process_events(12)
        assert robot.ToolShape is human_tool
        document.undo()
        _process_events(12)
        assert robot.ToolShape is None

        document.clearUndos()
        objects_before_defaults = tuple(document.Objects)
        _queue_default_value_dialogs()
        Gui.runCommand("Robot_SetDefaultValues", 0)
        _process_events(20)
        human_motion = capture_robot_waypoint_defaults().data["motion"]
        assert human_motion == {
            "speed_mm_per_s": 2000.0,
            "continuous": True,
            "acceleration_mm_per_s2": 3000.0,
        }
        _reset_defaults()
        _queue_orientation_dialog()
        Gui.runCommand("Robot_SetDefaultOrientation", 0)
        _process_events(20)
        human_orientation = capture_robot_waypoint_defaults().data["orientation"]
        assert human_orientation["displacement_mm"] == [11.25, -7.5, 3.75]
        assert App.Rotation(*human_orientation["quaternion_xyzw"]).isSame(
            App.Rotation(App.Vector(0.0, 0.0, 1.0), 30.0),
            1.0e-12,
        )
        assert tuple(document.Objects) == objects_before_defaults
        assert int(document.UndoCount) == 0
        assert not document.HasPendingTransaction
        _reset_defaults()

        VibeGui._connect_document_observer()
        main_window = Gui.getMainWindow()
        controller, surface = _select_assemble_ribbon(main_window)
        frozen = NativeSurfaceSnapshot.from_surface(surface)
        registry = build_native_capability_registry()
        definition = registry.definition(ROBOT_SETUP_CAPABILITY_NAME)
        assert definition is not None
        assert tuple(variant.operation for variant in definition.variants[:4]) == (
            "create",
            "add_tool_shape",
            "set_default_orientation",
            "set_default_values",
        )

        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-robot-configuration-gui")

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

        def refresh_dispatcher() -> None:
            nonlocal turn, dispatcher
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
            objects_before = tuple(document.Objects)
            result = dispatcher.call(
                ROBOT_SETUP_CAPABILITY_NAME,
                json.dumps(arguments, separators=(",", ":")),
                call_id or f"native-robot-configuration-{call_index}",
            )
            assert result.get("ok") is succeeds, result
            assert tuple(document.Objects) == objects_before
            assert (
                tuple(
                    (item.Object, tuple(item.SubElementNames))
                    for item in Gui.Selection.getSelectionEx("", 0)
                )
                == selection_before
            )
            assert not document.HasPendingTransaction
            return result

        _select(human_tool)
        tool_arguments = _tool_arguments(robot, native_tool)
        original_tool_verifier = runtime_module.verify_robot_tool_shape_attachment

        def reject_tool(_document, _draft):
            raise NativeRobotSetupError("Forced Robot tool verifier rejection.")

        runtime_module.verify_robot_tool_shape_attachment = reject_tool
        try:
            rolled_back = call(tool_arguments, succeeds=False)
        finally:
            runtime_module.verify_robot_tool_shape_attachment = original_tool_verifier
        assert rolled_back["error_code"] == "NATIVE_ROBOT_SETUP_FAILED"
        assert robot.ToolShape is None
        assert int(document.UndoCount) == 0

        tool_call_id = "native-robot-tool-success"
        attached = call(tool_arguments, call_id=tool_call_id)
        assert attached["changed"] is True
        assert attached["tool_shape"]["object_name"] == native_tool.Name
        assert attached["previous_tool_shape"] is None
        assert len(attached["receipt"]["changed"]) == 1
        assert attached["assistant_undo_available"] is True
        assert robot.ToolShape is native_tool
        assert int(document.UndoCount) == 1
        assert call(tool_arguments, call_id=tool_call_id) == attached
        assert int(document.UndoCount) == 1

        no_op = call(_tool_arguments(robot, native_tool))
        assert no_op["changed"] is False
        assert "receipt" not in no_op
        assert int(document.UndoCount) == 1
        document.undo()
        _process_events(12)
        assert robot.ToolShape is None
        document.redo()
        _process_events(12)
        assert robot.ToolShape is native_tool
        refresh_dispatcher()

        vrml_attached = call(_tool_arguments(robot, vrml_tool))
        assert vrml_attached["changed"] is True
        assert vrml_attached["tool_shape"]["object_name"] == vrml_tool.Name
        assert vrml_attached["previous_tool_shape"]["object_name"] == native_tool.Name
        assert robot.ToolShape is vrml_tool
        assert int(document.UndoCount) == 2
        document.undo()
        _process_events(12)
        assert robot.ToolShape is native_tool
        document.redo()
        _process_events(12)
        assert robot.ToolShape is vrml_tool
        refresh_dispatcher()

        document.save()
        undo_count_before_defaults = int(document.UndoCount)
        defaults_file_baseline = document_path.read_bytes()
        revision_before_defaults = state_store.current_revision(document.Uid)
        receipts_before_defaults = tuple(
            state_store.snapshot(document.Uid)["recent_receipts"]
        )
        motion_arguments = {
            "operation": "set_default_values",
            "speed_mm_per_s": 2000.0,
            "continuous": True,
            "acceleration_mm_per_s2": 3000.0,
        }

        motion = call(motion_arguments)
        assert motion["scope"] == "application_session"
        assert motion["changed"] is True
        assert "receipt" not in motion
        assert motion["waypoint_defaults"]["motion"] == human_motion
        assert capture_robot_waypoint_defaults().data["motion"] == human_motion
        assert state_store.current_revision(document.Uid) == revision_before_defaults
        assert tuple(state_store.snapshot(document.Uid)["recent_receipts"]) == (
            receipts_before_defaults
        )

        orientation_arguments = {
            "operation": "set_default_orientation",
            "placement": {
                "origin_mm": {"x": 11.25, "y": -7.5, "z": 3.75},
                "rotation": {
                    "axis": {"x": 0.0, "y": 0.0, "z": 1.0},
                    "angle_degrees": 30.0,
                },
            },
        }
        orientation = call(orientation_arguments)
        assert orientation["changed"] is True
        assert (
            orientation["waypoint_defaults"]["orientation"]["displacement_mm"]
            == human_orientation["displacement_mm"]
        )
        assert App.Rotation(
            *orientation["waypoint_defaults"]["orientation"]["quaternion_xyzw"]
        ).isSame(
            App.Rotation(*human_orientation["quaternion_xyzw"]),
            1.0e-12,
        )
        assert "receipt" not in orientation

        no_op_orientation = call(orientation_arguments)
        assert no_op_orientation["changed"] is False
        assert "receipt" not in no_op_orientation

        before_rollback = capture_robot_waypoint_defaults()
        original_boundary_verifier = defaults_module._require_document_boundary

        def reject_defaults(exact_context, boundary):
            original_boundary_verifier(exact_context, boundary)
            raise NativeRobotSetupError("Forced Robot defaults verifier rejection.")

        defaults_module._require_document_boundary = reject_defaults
        try:
            defaults_rollback = call(
                {
                    "operation": "set_default_values",
                    "speed_mm_per_s": 4321.125,
                    "continuous": False,
                    "acceleration_mm_per_s2": 8765.375,
                },
                succeeds=False,
            )
        finally:
            defaults_module._require_document_boundary = original_boundary_verifier
        assert defaults_rollback["error_code"] == "NATIVE_ROBOT_SETUP_FAILED"
        assert capture_robot_waypoint_defaults() == before_rollback
        assert int(document.UndoCount) == undo_count_before_defaults

        document.save()
        assert document_path.read_bytes() == defaults_file_baseline
        for forbidden in (
            b"_DefSpeed",
            b"_DefAcceleration",
            b"4321.125",
            b"8765.375",
        ):
            assert not _archive_contains(document_path, forbidden)

        robot_name = robot.Name
        part_tool_name = native_tool.Name
        part_tool_state = capture_robot_tool_shape_record(native_tool)
        tool_name = vrml_tool.Name
        tool_state = capture_robot_tool_shape_record(vrml_tool)
        configured_robot = capture_robot_setup_state(document).records[0]
        _reset_defaults()
        App.closeDocument(document.Name)
        document = App.openDocument(str(document_path))
        App.setActiveDocument(document.Name)
        assert document.recompute(None, True, True) is not False
        _process_events(24)
        restored_robot = document.getObject(robot_name)
        restored_part_tool = document.getObject(part_tool_name)
        restored_tool = document.getObject(tool_name)
        assert restored_robot.ToolShape is restored_tool
        restored_setup = capture_robot_setup_state(document)
        assert restored_setup.records[0].state_sha256 == configured_robot.state_sha256
        restored_tool_state = capture_robot_tool_shape_record(restored_tool)
        assert restored_tool_state.state_sha256 == tool_state.state_sha256, (
            tool_state.data,
            restored_tool_state.data,
        )
        assert (
            capture_robot_tool_shape_record(restored_part_tool).state_sha256
            == part_tool_state.state_sha256
        )
        restored_defaults = capture_robot_waypoint_defaults()
        assert restored_defaults.data == {
            "orientation": {
                "displacement_mm": [0.0, 0.0, 0.0],
                "quaternion_xyzw": [0.0, 0.0, 0.0, 1.0],
            },
            "motion": {
                "speed_mm_per_s": 1000.0,
                "continuous": False,
                "acceleration_mm_per_s2": 1000.0,
            },
        }

        print(
            "VIBECAD_NATIVE_ROBOT_CONFIGURATION_GUI_OK "
            "human_tool_parity=true exact_targets=true "
            "rollback=true idempotent=true undo_redo=true reopen=true "
            "vrml=true "
            "human_defaults_parity=true session_only=true document_unchanged=true "
            "defaults_rollback=true selection_preserved=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        _reset_defaults()
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
