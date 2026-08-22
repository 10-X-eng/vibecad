# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact Native Robot trajectories."""

from __future__ import annotations

import __main__
import json
from pathlib import Path
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import Robot  # noqa: F401 - registers Robot document factories
import RobotGui  # noqa: F401 - registers the shipped human commands
from VibeCADCore import get_service
import VibeCADGui as VibeGui
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRobotTrajectory import NativeRobotTrajectoryError
from VibeCADNativeRobotTrajectorySchema import (
    ROBOT_TRAJECTORY_CAPABILITY_NAME,
    robot_trajectory_capability_definition,
)
from VibeCADNativeRobotTrajectoryState import capture_robot_trajectory_state
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface
import VibeCADNativeRobotTrajectoryRuntime as runtime_module


def _process_events(rounds: int = 20) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _select(*objects) -> None:
    Gui.Selection.clearSelection()
    for obj in objects:
        Gui.Selection.addSelection(obj)
    _process_events(8)


def _selection() -> tuple[tuple[object, tuple[str, ...]], ...]:
    return tuple(
        (item.Object, tuple(item.SubElementNames))
        for item in Gui.Selection.getSelectionEx("", 0)
    )


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
    trajectory = robot_trajectory_capability_definition()
    assert state is not None
    operations = (
        "create_trajectory",
        "insert_robot_waypoint",
        "insert_position_waypoint",
    )
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=("state.read", ROBOT_TRAJECTORY_CAPABILITY_NAME),
            schemas=(
                state.provider_schema(("active", "selection")),
                trajectory.provider_schema(operations),
            ),
            human_only_action_ids=("Assembly_ActivateAssembly",),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _reset_defaults() -> None:
    __main__._DefSpeed = "2250 mm/s"
    __main__._DefCont = True
    __main__._DefAcceleration = "3500 mm/s^2"
    __main__._DefOrientation = App.Rotation(App.Vector(0.0, 0.0, 1.0), 25.0)
    __main__._DefDisplacement = App.Vector(1.25, -2.5, 3.75)


def _trajectory_record(document, trajectory):
    state = capture_robot_trajectory_state(document)
    index = state.trajectories.index(trajectory)
    return state, state.records[index]


def _create_arguments() -> dict:
    return {
        "operation": "create_trajectory",
        "label": "Native inspection route",
    }


def _robot_waypoint_arguments(robot, trajectory) -> dict:
    return {
        "operation": "insert_robot_waypoint",
        "trajectory": {"object_name": trajectory.Name},
        "robot": {"object_name": robot.Name},
    }


def _position_waypoint_arguments(trajectory, position) -> dict:
    return {
        "operation": "insert_position_waypoint",
        "trajectory": {"object_name": trajectory.Name},
        "position_mm": {
            "x": position[0],
            "y": position[1],
            "z": position[2],
        },
    }


def _created_parity(data: dict) -> dict:
    return {
        key: data[key]
        for key in (
            "type_id",
            "base",
            "waypoint_count",
            "length_mm",
            "duration_seconds",
            "suppressed",
            "valid",
            "timeline",
            "presentation",
        )
    }


def _waypoint_parity(data: dict) -> dict:
    return {
        key: data[key]
        for key in (
            "type",
            "placement",
            "velocity_mm_per_s",
            "acceleration_mm_per_s2",
            "continuous",
            "tool",
            "base",
        )
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("AssemblyWorkbench")
        _reset_defaults()
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-robot-trajectory-"
        )
        document_path = Path(temporary.name) / "native-robot-trajectory.FCStd"

        document = App.newDocument("NativeRobotTrajectoryGate")
        document.UndoMode = 1
        robot = document.addObject("Robot::RobotObject", "Robot")
        robot.Label = "Waypoint robot"
        sentinel = document.addObject("Part::Feature", "SelectionSentinel")
        sentinel.Shape = Part.makeCylinder(3.0, 9.0)
        source = document.addObject("Part::Feature", "PreselectionSource")
        source.Shape = Part.makeBox(20.0, 20.0, 20.0)
        assert document.recompute(None, True, True) is not False
        assert robot.isValid()

        objects_before = tuple(document.Objects)
        undo_before = int(document.UndoCount)
        Gui.runCommand("Robot_CreateTrajectory", 0)
        _process_events(16)
        human_candidates = [
            obj
            for obj in document.Objects
            if obj not in objects_before and obj.TypeId == "Robot::TrajectoryObject"
        ]
        assert len(human_candidates) == 1
        human = human_candidates[0]
        assert int(document.UndoCount) == undo_before + 1
        human_state, human_empty = _trajectory_record(document, human)
        assert not human_empty.waypoints
        timeline = document.getObject("VibeCADTimeline")
        assert timeline is not None
        human_operations = tuple(timeline.Operations)
        assert human_operations[-1] is human
        assert human_operations.count(human) == 1

        _select(robot, human)
        assert Gui.isCommandActive("Robot_InsertWaypoint")
        Gui.runCommand("Robot_InsertWaypoint", 0)
        _process_events(12)
        _, human_after_robot = _trajectory_record(document, human)
        assert len(human_after_robot.waypoints) == 1

        _select(human)
        point = (4.5, 6.25, -1.75)
        Gui.Selection.setPreselection(source, "Face1", *point)
        assert Gui.isCommandActive("Robot_InsertWaypointPreselect")
        Gui.runCommand("Robot_InsertWaypointPreselect", 0)
        _process_events(12)
        _, human_complete = _trajectory_record(document, human)
        assert len(human_complete.waypoints) == 2
        expected_position = tuple(
            point[index] + (1.25, -2.5, 3.75)[index] for index in range(3)
        )
        assert (
            tuple(human_complete.waypoints[-1].data["placement"]["position_mm"])
            == expected_position
        )
        Gui.Selection.clearPreselection()
        document.clearUndos()

        VibeGui._connect_document_observer()
        controller, surface = _select_assemble_ribbon(Gui.getMainWindow())
        frozen = NativeSurfaceSnapshot.from_surface(surface)
        registry = build_native_capability_registry()
        definition = registry.definition(ROBOT_TRAJECTORY_CAPABILITY_NAME)
        assert definition is not None
        assert tuple(variant.operation for variant in definition.variants[:3]) == (
            "create_trajectory",
            "insert_robot_waypoint",
            "insert_position_waypoint",
        )
        provider_schema = json.dumps(
            definition.provider_schema(
                (
                    "create_trajectory",
                    "insert_robot_waypoint",
                    "insert_position_waypoint",
                )
            ),
            sort_keys=True,
        ).casefold()
        for forbidden in (
            "file_path",
            "directory",
            "runcommand",
            "workbench",
            "preselection",
            "command_id",
        ):
            assert forbidden not in provider_schema

        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-robot-trajectory-gui")

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
            selection_before = _selection()
            result = dispatcher.call(
                ROBOT_TRAJECTORY_CAPABILITY_NAME,
                json.dumps(arguments, separators=(",", ":")),
                call_id or f"native-robot-trajectory-{call_index}",
            )
            assert result.get("ok") is succeeds, result
            assert _selection() == selection_before
            assert not document.HasPendingTransaction
            return result

        _select(sentinel)
        create_arguments = _create_arguments()
        create_call_id = "native-trajectory-create-idempotence"
        created = call(create_arguments, call_id=create_call_id)
        native = document.getObject(created["trajectory"]["object_name"])
        assert native is not None
        assert tuple(timeline.Operations) == (*human_operations, native)
        _, native_empty = _trajectory_record(document, native)
        assert _created_parity(native_empty.data) == _created_parity(human_empty.data)
        assert int(document.UndoCount) == 1

        replay = call(create_arguments, call_id=create_call_id)
        assert replay == created
        assert int(document.UndoCount) == 1
        assert tuple(timeline.Operations) == (*human_operations, native)

        native_name = native.Name
        document.undo()
        _process_events(16)
        assert document.getObject(native_name) is None
        assert tuple(timeline.Operations) == human_operations
        document.redo()
        _process_events(16)
        native = document.getObject(native_name)
        assert native is not None
        assert tuple(timeline.Operations) == (*human_operations, native)
        refresh_dispatcher()

        robot_arguments = _robot_waypoint_arguments(robot, native)
        before_failure = capture_robot_trajectory_state(document)
        failure_undo = int(document.UndoCount)
        original_verify = runtime_module.verify_appended_waypoint

        def reject_verification(_document, _draft):
            raise NativeRobotTrajectoryError("Forced trajectory verifier failure.")

        runtime_module.verify_appended_waypoint = reject_verification
        try:
            rolled_back = call(robot_arguments, succeeds=False)
        finally:
            runtime_module.verify_appended_waypoint = original_verify
        assert rolled_back["error_code"] == "NATIVE_ROBOT_TRAJECTORY_FAILED"
        assert capture_robot_trajectory_state(document) == before_failure
        assert int(document.UndoCount) == failure_undo

        robot_call_id = "native-trajectory-robot-waypoint-idempotence"
        inserted_robot = call(robot_arguments, call_id=robot_call_id)
        native_after_robot_state, native_after_robot = _trajectory_record(
            document, native
        )
        assert len(native_after_robot.waypoints) == 1
        assert _waypoint_parity(native_after_robot.waypoints[0].data) == (
            _waypoint_parity(human_complete.waypoints[0].data)
        )
        undo_after_robot = int(document.UndoCount)
        assert inserted_robot["waypoint_count"] == 1
        assert call(robot_arguments, call_id=robot_call_id) == inserted_robot
        assert int(document.UndoCount) == undo_after_robot
        document.undo()
        _process_events(12)
        _, after_undo = _trajectory_record(document, native)
        assert not after_undo.waypoints
        document.redo()
        _process_events(12)
        _, after_redo = _trajectory_record(document, native)
        assert after_redo.state_sha256 == native_after_robot.state_sha256
        refresh_dispatcher()

        position_arguments = _position_waypoint_arguments(native, point)
        position_call_id = "native-trajectory-position-waypoint-idempotence"
        inserted_position = call(position_arguments, call_id=position_call_id)
        final_state, native_complete = _trajectory_record(document, native)
        assert len(native_complete.waypoints) == 2
        assert _waypoint_parity(native_complete.waypoints[1].data) == (
            _waypoint_parity(human_complete.waypoints[1].data)
        )
        undo_after_position = int(document.UndoCount)
        assert inserted_position["waypoint_count"] == 2
        assert call(position_arguments, call_id=position_call_id) == inserted_position
        assert int(document.UndoCount) == undo_after_position
        assert tuple(timeline.Operations) == (*human_operations, native)
        assert _selection() == ((sentinel, ()),)

        human_name = human.Name
        human_operation_names = tuple(item.Name for item in human_operations)
        native_final_sha256 = native_complete.state_sha256
        human_final_sha256 = human_complete.state_sha256
        final_setup_sha256 = final_state.state_sha256
        document.saveAs(str(document_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(document_path))
        App.setActiveDocument(document.Name)
        assert document.recompute(None, True, True) is not False
        _process_events(24)
        restored = capture_robot_trajectory_state(document)
        restored_human = document.getObject(human_name)
        restored_native = document.getObject(native_name)
        assert restored.trajectories == (restored_human, restored_native)
        assert restored.records[0].state_sha256 == human_final_sha256
        assert restored.records[1].state_sha256 == native_final_sha256
        assert restored.state_sha256 == final_setup_sha256
        assert tuple(document.VibeCADTimeline.Operations) == (
            *(document.getObject(name) for name in human_operation_names),
            restored_native,
        )
        assert all(record.data["valid"] for record in restored.records)

        print(
            "VIBECAD_NATIVE_ROBOT_TRAJECTORY_GUI_OK "
            "human_create_parity=true exact_history=true exact_targets=true "
            "human_robot_waypoint_parity=true human_position_waypoint_parity=true "
            "provider_preselection=false rollback=true "
            "idempotent=true undo_redo=true reopen=true selection_preserved=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        Gui.Selection.clearSelection()
        Gui.Selection.clearPreselection()
        if document is not None:
            try:
                App.closeDocument(document.Name)
            except (AttributeError, RuntimeError):
                pass
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(0, _run)
