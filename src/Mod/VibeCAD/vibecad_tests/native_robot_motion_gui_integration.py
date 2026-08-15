# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact Native Robot motion."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import Robot
import RobotGui  # noqa: F401 - registers the shipped human commands
from VibeCADCore import get_service
import VibeCADGui as VibeGui
from VibeCADNativeCapabilityRegistry import (
    NativeProviderSurface,
    resolve_native_provider_surface,
)
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeManufactureSnapshot import build_manufacture_snapshot
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRobotMotion import NativeRobotMotionError
from VibeCADNativeRobotMotionSchema import (
    ROBOT_MOTION_CAPABILITY_NAME,
    robot_motion_capability_definition,
)
from VibeCADNativeRobotState import capture_robot_setup_state
from VibeCADNativeRobotTrajectoryState import capture_robot_trajectory_state
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface
import VibeCADNativeRobotMotionRuntime as runtime_module


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


def _select_ribbon(
    main_window,
    workbench_name: str,
    surface_id: str,
) -> tuple[object, object]:
    controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
    tabs = main_window.findChild(QtWidgets.QTabBar, "VibeCADRibbonTabs")
    assert controller is not None and tabs is not None
    index = next(
        candidate
        for candidate in range(tabs.count())
        if str(tabs.tabData(candidate)) == workbench_name
    )
    tabs.setCurrentIndex(index)
    _process_events(24)
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == surface_id
    return controller, surface


def _focused_turn(
    surface,
    registry,
    operations: tuple[str, ...] = ("set_home_pos", "restore_home_pos", "simulate"),
) -> NativeTurnSnapshot:
    state = registry.definition("state.read")
    motion = robot_motion_capability_definition()
    assert state is not None
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=("state.read", ROBOT_MOTION_CAPABILITY_NAME),
            schemas=(
                state.provider_schema(("active", "selection")),
                motion.provider_schema(operations),
            ),
            human_only_action_ids=("Assembly_ActivateAssembly",),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _trajectory_value() -> object:
    value = Robot.Trajectory()
    for index, x_coordinate in enumerate((1000.0, 1020.0), 1):
        value.insertWaypoints(
            Robot.Waypoint(
                App.Placement(
                    App.Vector(x_coordinate, 0.0, 1200.0),
                    App.Rotation(),
                ),
                type="LIN",
                name=f"P{index}",
                vel="1 m/s",
                cont=False,
                acc="1 m/s^2",
                tool=1,
            )
        )
    return value


def _robot_record(document, robot):
    state = capture_robot_setup_state(document)
    index = state.robots.index(robot)
    return state, state.records[index]


def _trajectory_record(document, trajectory):
    state = capture_robot_trajectory_state(document)
    index = state.trajectories.index(trajectory)
    return state, state.records[index]


def _home_arguments(document, robot, operation: str) -> dict:
    state, record = _robot_record(document, robot)
    return {
        "operation": operation,
        "robot": {"object_name": robot.Name},
        "expected_setup_state_sha256": state.state_sha256,
        "expected_robot_state_sha256": record.state_sha256,
    }


def _simulation_arguments(document, robot, trajectory, times) -> dict:
    robots, robot_record = _robot_record(document, robot)
    trajectories, trajectory_record = _trajectory_record(document, trajectory)
    return {
        "operation": "simulate",
        "robot": {"object_name": robot.Name},
        "trajectory": {"object_name": trajectory.Name},
        "sample_times_s": list(times),
        "expected_setup_state_sha256": robots.state_sha256,
        "expected_robot_state_sha256": robot_record.state_sha256,
        "expected_trajectory_setup_state_sha256": trajectories.state_sha256,
        "expected_trajectory_state_sha256": trajectory_record.state_sha256,
    }


def _axes(record) -> tuple[float, ...]:
    return tuple(record.data["axes_degrees"])


def _home(record) -> tuple[float, ...]:
    return tuple(record.data["home_degrees"])


def _angles_equivalent(left, right, tolerance: float = 1.0e-3) -> bool:
    return all(
        abs(((float(first) - float(second) + 180.0) % 360.0) - 180.0) <= tolerance
        for first, second in zip(left, right, strict=True)
    )


def _close_task_dialog(main_window) -> None:
    boxes = main_window.findChildren(QtWidgets.QDialogButtonBox)
    close = next(
        (
            box.button(QtWidgets.QDialogButtonBox.Close)
            for box in reversed(boxes)
            if box.button(QtWidgets.QDialogButtonBox.Close) is not None
        ),
        None,
    )
    assert close is not None
    close.click()
    _process_events(16)
    assert not Gui.Control.activeDialog()


def _human_preview_axes(main_window) -> tuple[float, ...]:
    values = []
    for axis in range(1, 7):
        field = main_window.findChild(QtWidgets.QLineEdit, f"lineEdit_Axis{axis}")
        assert field is not None
        values.append(float(field.text().replace("°", "")))
    return tuple(values)


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("AssemblyWorkbench")
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-robot-motion-")
        document_path = Path(temporary.name) / "native-robot-motion.FCStd"

        document = App.newDocument("NativeRobotMotionGate")
        document.UndoMode = 1
        human_robot = document.addObject("Robot::RobotObject", "HumanRobot")
        native_robot = document.addObject("Robot::RobotObject", "NativeRobot")
        trajectory = document.addObject("Robot::TrajectoryObject", "Trajectory")
        trajectory.Trajectory = _trajectory_value()
        sentinel = document.addObject("Part::Feature", "SelectionSentinel")
        sentinel.Shape = Part.makeBox(8.0, 9.0, 10.0)
        initial_axes = (12.0, -15.0, 18.0, 6.0, -9.0, 3.0)
        for robot in (human_robot, native_robot):
            for axis, value in enumerate(initial_axes, 1):
                setattr(robot, f"Axis{axis}", value)
        assert document.recompute(None, True, True) is not False
        assert human_robot.isValid() and native_robot.isValid()
        trajectory_state, trajectory_record = _trajectory_record(document, trajectory)
        assert len(trajectory_record.waypoints) == 2
        assert trajectory_record.data["duration_seconds"] > 0.0

        _select(human_robot)
        human_undo = int(document.UndoCount)
        Gui.runCommand("Robot_SetHomePos", 0)
        _process_events(12)
        _, human_after_set = _robot_record(document, human_robot)
        assert _home(human_after_set) == _axes(human_after_set)
        assert int(document.UndoCount) == human_undo + 1

        VibeGui._connect_document_observer()
        controller, surface = _select_ribbon(
            Gui.getMainWindow(),
            "AssemblyWorkbench",
            "assemble",
        )
        frozen = NativeSurfaceSnapshot.from_surface(surface)
        registry = build_native_capability_registry()
        definition = registry.definition(ROBOT_MOTION_CAPABILITY_NAME)
        assert definition is not None
        production_provider = resolve_native_provider_surface(surface, registry)
        assert (
            ROBOT_MOTION_CAPABILITY_NAME
            not in production_provider.incomplete_definition_names
        )
        assert (
            ROBOT_MOTION_CAPABILITY_NAME
            not in production_provider.missing_definition_names
        )
        assert (
            ROBOT_MOTION_CAPABILITY_NAME
            not in production_provider.missing_implementation_names
        )
        assert tuple(variant.operation for variant in definition.variants) == (
            "set_home_pos",
            "restore_home_pos",
            "simulate",
        )
        assert definition.variants[-1].surface_ids == frozenset(
            {"assemble", "manufacture"}
        )
        provider_schema = json.dumps(
            definition.provider_schema(
                ("set_home_pos", "restore_home_pos", "simulate")
            ),
            sort_keys=True,
        ).casefold()
        for forbidden in (
            "file_path",
            "directory",
            "runcommand",
            "workbench",
            "selection",
            "dialog",
            "command_id",
        ):
            assert forbidden not in provider_schema

        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-robot-motion-gui")

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
        call_index = 0

        def call(
            arguments: dict,
            *,
            succeeds: bool = True,
            call_id: str = "",
            selected_dispatcher: NativeTurnDispatcher | None = None,
        ) -> dict:
            nonlocal call_index
            call_index += 1
            selection_before = _selection()
            result = (selected_dispatcher or dispatcher).call(
                ROBOT_MOTION_CAPABILITY_NAME,
                json.dumps(arguments, separators=(",", ":")),
                call_id or f"native-robot-motion-{call_index}",
            )
            assert result.get("ok") is succeeds, result
            assert _selection() == selection_before
            assert not document.HasPendingTransaction
            return result

        _select(sentinel)
        set_arguments = _home_arguments(document, native_robot, "set_home_pos")
        state_before_failure = capture_robot_setup_state(document)
        initial_native_home = _home(
            state_before_failure.records[
                state_before_failure.robots.index(native_robot)
            ]
        )
        failure_undo = int(document.UndoCount)
        original_verify = runtime_module.verify_robot_home

        def reject_verification(_document, _draft):
            raise NativeRobotMotionError("Forced Robot home verifier failure.")

        runtime_module.verify_robot_home = reject_verification
        try:
            rolled_back = call(set_arguments, succeeds=False)
        finally:
            runtime_module.verify_robot_home = original_verify
        assert rolled_back["error_code"] == "NATIVE_ROBOT_MOTION_FAILED", rolled_back
        assert capture_robot_setup_state(document) == state_before_failure
        assert int(document.UndoCount) == failure_undo

        stale = call(
            {**set_arguments, "expected_robot_state_sha256": "0" * 64},
            succeeds=False,
        )
        assert stale["error_code"] == "NATIVE_ROBOT_MOTION_FAILED"
        assert capture_robot_setup_state(document) == state_before_failure
        assert int(document.UndoCount) == failure_undo

        set_call_id = "native-robot-set-home-idempotence"
        set_result = call(set_arguments, call_id=set_call_id)
        _, native_after_set = _robot_record(document, native_robot)
        assert _home(native_after_set) == _home(human_after_set)
        assert set_result["changed"] is True
        assert "receipt" in set_result
        undo_after_set = int(document.UndoCount)
        assert call(set_arguments, call_id=set_call_id) == set_result
        assert int(document.UndoCount) == undo_after_set

        noop_revision = state_store.current_revision(str(document.Uid))
        noop_undo = int(document.UndoCount)
        no_op = call(_home_arguments(document, native_robot, "set_home_pos"))
        assert no_op["changed"] is False and "receipt" not in no_op
        assert int(document.UndoCount) == noop_undo
        assert state_store.current_revision(str(document.Uid)) == noop_revision

        document.undo()
        _process_events(12)
        _, native_after_undo = _robot_record(document, native_robot)
        assert _home(native_after_undo) == initial_native_home
        document.redo()
        _process_events(12)
        _, native_after_redo = _robot_record(document, native_robot)
        assert _home(native_after_redo) == _home(human_after_set)

        moved_axes = (47.0, -5.0, 22.0, -11.0, 8.0, 14.0)
        for robot in (human_robot, native_robot):
            for axis, value in enumerate(moved_axes, 1):
                setattr(robot, f"Axis{axis}", value)
        assert document.recompute(None, True, True) is not False
        native_moved_axes = _axes(_robot_record(document, native_robot)[1])

        _select(human_robot)
        Gui.runCommand("Robot_RestoreHomePos", 0)
        _process_events(12)
        _, human_after_restore = _robot_record(document, human_robot)
        assert _axes(human_after_restore) == _home(human_after_restore)

        _select(sentinel)
        restore_arguments = _home_arguments(
            document,
            native_robot,
            "restore_home_pos",
        )
        restore_result = call(
            restore_arguments,
            call_id="native-robot-restore-home-idempotence",
        )
        _, native_after_restore = _robot_record(document, native_robot)
        assert _axes(native_after_restore) == _axes(human_after_restore)
        assert native_after_restore.data["tcp"] == human_after_restore.data["tcp"]
        assert restore_result["changed"] is True
        restore_undo = int(document.UndoCount)
        restore_replay = call(
            restore_arguments,
            call_id="native-robot-restore-home-idempotence",
        )
        assert restore_replay == restore_result
        assert int(document.UndoCount) == restore_undo
        document.undo()
        _process_events(12)
        _, restored_undo_record = _robot_record(document, native_robot)
        assert _angles_equivalent(_axes(restored_undo_record), native_moved_axes)
        document.redo()
        _process_events(12)
        _, restored_redo_record = _robot_record(document, native_robot)
        assert _axes(restored_redo_record) == _home(restored_redo_record)

        main_window = Gui.getMainWindow()
        human_axes_before = _axes(human_after_restore)
        objects_before_preview = tuple(document.Objects)
        preview_undo = int(document.UndoCount)
        _select(human_robot, trajectory)
        Gui.runCommand("Robot_Simulate", 0)
        _process_events(16)
        assert Gui.Control.activeDialog()
        slider = main_window.findChild(QtWidgets.QSlider, "timeSlider")
        assert slider is not None
        slider.setValue(500)
        _process_events(12)
        human_preview = _human_preview_axes(main_window)
        assert _axes(_robot_record(document, human_robot)[1]) == human_axes_before
        assert tuple(document.Objects) == objects_before_preview
        assert int(document.UndoCount) == preview_undo
        assert not document.HasPendingTransaction
        _close_task_dialog(main_window)

        _select(sentinel)
        trajectory_state, trajectory_record = _trajectory_record(document, trajectory)
        duration = float(trajectory_record.data["duration_seconds"])
        simulation_arguments = _simulation_arguments(
            document,
            native_robot,
            trajectory,
            (0.0, duration * 0.5, duration),
        )
        simulation_revision = state_store.current_revision(str(document.Uid))
        simulation_undo = int(document.UndoCount)
        setup_before_simulation = capture_robot_setup_state(document)
        trajectories_before_simulation = capture_robot_trajectory_state(document)
        simulation = call(
            simulation_arguments,
            call_id="native-robot-simulation-idempotence",
        )
        assert simulation["changed"] is False
        assert simulation["preview_only"] is True
        assert "receipt" not in simulation
        assert len(simulation["samples"]) == 3
        native_preview = tuple(simulation["samples"][1]["axes_degrees"])
        assert all(
            abs(human - native) <= 0.051
            for human, native in zip(human_preview, native_preview, strict=True)
        )
        assert int(document.UndoCount) == simulation_undo
        assert state_store.current_revision(str(document.Uid)) == simulation_revision
        assert capture_robot_setup_state(document) == setup_before_simulation
        assert (
            capture_robot_trajectory_state(document) == trajectories_before_simulation
        )
        assert not Gui.Control.activeDialog()
        assert (
            call(
                simulation_arguments,
                call_id="native-robot-simulation-idempotence",
            )
            == simulation
        )

        stale_simulation = call(
            {
                **simulation_arguments,
                "expected_trajectory_state_sha256": "0" * 64,
            },
            succeeds=False,
        )
        assert stale_simulation["error_code"] == "NATIVE_ROBOT_MOTION_FAILED"
        assert capture_robot_setup_state(document) == setup_before_simulation
        assert (
            capture_robot_trajectory_state(document) == trajectories_before_simulation
        )
        assert int(document.UndoCount) == simulation_undo
        assert state_store.current_revision(str(document.Uid)) == simulation_revision

        manufacture_controller, manufacture_surface = _select_ribbon(
            Gui.getMainWindow(),
            "CAMWorkbench",
            "manufacture",
        )
        manufacture_provider = resolve_native_provider_surface(
            manufacture_surface,
            registry,
        )
        assert ROBOT_MOTION_CAPABILITY_NAME not in (
            manufacture_provider.missing_definition_names
        )
        assert ROBOT_MOTION_CAPABILITY_NAME not in (
            manufacture_provider.missing_implementation_names
        )
        assert ROBOT_MOTION_CAPABILITY_NAME not in (
            manufacture_provider.incomplete_definition_names
        )
        manufacture_frozen = NativeSurfaceSnapshot.from_surface(manufacture_surface)

        def reauthorize_manufacture() -> None:
            require_frozen_native_surface(manufacture_frozen, manufacture_controller)

        manufacture_context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state_store,
            undo_ledger=ledger,
            reauthorize_turn=reauthorize_manufacture,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(
                manufacture_controller
            ).surface_id,
            edit_or_task_active=lambda: bool(Gui.Control.activeDialog()),
        )
        manufacture_turn = _focused_turn(
            manufacture_surface,
            registry,
            ("simulate",),
        )
        manufacture_dispatcher = NativeTurnDispatcher(
            document=document,
            state=state_store,
            registry=registry,
            turn=manufacture_turn,
            runtimes=build_native_runtime_bindings(
                manufacture_context,
                manufacture_turn.tool_names,
            ),
            reauthorize_turn=reauthorize_manufacture,
            active_document=lambda: App.ActiveDocument,
        )
        manufacture_simulation = call(
            simulation_arguments,
            call_id="native-robot-manufacture-simulation",
            selected_dispatcher=manufacture_dispatcher,
        )
        assert manufacture_simulation["samples"] == simulation["samples"]
        assert manufacture_simulation["changed"] is False
        assert manufacture_simulation["preview_only"] is True
        assert int(document.UndoCount) == simulation_undo
        assert state_store.current_revision(str(document.Uid)) == simulation_revision

        manufacture = build_manufacture_snapshot(document)
        assert manufacture["robot_setup"]["available"] is True
        assert manufacture["robot_setup"]["robot_count"] == 2
        assert manufacture["robot_trajectories"]["available"] is True
        assert manufacture["robot_trajectories"]["trajectory_count"] == 1

        human_name = human_robot.Name
        native_name = native_robot.Name
        trajectory_name = trajectory.Name
        human_sha256 = _robot_record(document, human_robot)[1].state_sha256
        native_sha256 = _robot_record(document, native_robot)[1].state_sha256
        trajectory_sha256 = trajectory_state.records[
            trajectory_state.trajectories.index(trajectory)
        ].state_sha256
        document.saveAs(str(document_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(document_path))
        App.setActiveDocument(document.Name)
        assert document.recompute(None, True, True) is not False
        _process_events(20)
        restored_robots = capture_robot_setup_state(document)
        restored_trajectories = capture_robot_trajectory_state(document)
        restored_human = document.getObject(human_name)
        restored_native = document.getObject(native_name)
        restored_trajectory = document.getObject(trajectory_name)
        assert (
            restored_robots.records[
                restored_robots.robots.index(restored_human)
            ].state_sha256
            == human_sha256
        )
        assert (
            restored_robots.records[
                restored_robots.robots.index(restored_native)
            ].state_sha256
            == native_sha256
        )
        assert (
            restored_trajectories.records[
                restored_trajectories.trajectories.index(restored_trajectory)
            ].state_sha256
            == trajectory_sha256
        )

        print(
            "VIBECAD_NATIVE_ROBOT_MOTION_GUI_OK "
            "human_set_home_parity=true human_restore_home_parity=true "
            "human_simulation_parity=true exact_targets=true stale_noop=true "
            "rollback=true verified_noop=true idempotent=true undo_redo=true "
            "preview_only=true manufacture_surface=true reopen=true "
            "selection_preserved=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        Gui.Selection.clearSelection()
        Gui.Selection.clearPreselection()
        if Gui.Control.activeDialog():
            try:
                Gui.Control.closeDialog()
            except RuntimeError:
                pass
        if document is not None:
            try:
                App.closeDocument(document.Name)
            except (AttributeError, RuntimeError):
                pass
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(0, _run)
