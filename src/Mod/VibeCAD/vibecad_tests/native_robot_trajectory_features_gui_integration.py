# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for Robot trajectory feature operations."""

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


_FEATURE_OPERATIONS = (
    "edge2_trac",
    "trajectory_dress_up",
    "trajectory_compound",
)


def _process_events(rounds: int = 20) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _select(*objects) -> None:
    Gui.Selection.clearSelection()
    for obj in objects:
        Gui.Selection.addSelection(obj)
    _process_events(8)


def _select_edge(obj, name: str = "Edge1") -> None:
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(obj, name)
    _process_events(8)


def _selection() -> tuple[tuple[object, tuple[str, ...]], ...]:
    return tuple(
        (item.Object, tuple(item.SubElementNames))
        for item in Gui.Selection.getSelectionEx("", 0)
    )


def _task_button(standard_button):
    _process_events(8)
    for box in Gui.getMainWindow().findChildren(QtWidgets.QDialogButtonBox):
        if not box.isVisible():
            continue
        parent = box.parentWidget()
        while parent is not None:
            if parent.metaObject().className() == "Gui::TaskView::TaskView":
                break
            parent = parent.parentWidget()
        if parent is None:
            continue
        button = box.button(standard_button)
        if button and button.isVisible() and button.isEnabled():
            return button
    return None


def _set_dress_controls(
    *,
    speed_m_per_s: float,
    acceleration_m_per_s2: float,
    continuity_index: int,
) -> None:
    main = Gui.getMainWindow()
    use_speed = main.findChild(QtWidgets.QCheckBox, "checkBoxUseSpeed")
    speed = main.findChild(QtWidgets.QDoubleSpinBox, "doubleSpinBoxSpeed")
    use_accel = main.findChild(QtWidgets.QCheckBox, "checkBoxUseAccel")
    accel = main.findChild(QtWidgets.QDoubleSpinBox, "doubleSpinBoxAccel")
    continuity = main.findChild(QtWidgets.QComboBox, "comboBoxCont")
    orientation = main.findChild(QtWidgets.QComboBox, "comboBoxOrientation")
    assert all(
        value is not None
        for value in (use_speed, speed, use_accel, accel, continuity, orientation)
    )
    use_speed.setChecked(True)
    speed.setValue(speed_m_per_s)
    use_accel.setChecked(True)
    accel.setValue(acceleration_m_per_s2)
    continuity.setCurrentIndex(continuity_index)
    orientation.setCurrentIndex(0)


def _ribbon_surface(workbench_name: str, expected_id: str):
    main = Gui.getMainWindow()
    controller = main.findChild(QtCore.QObject, "VibeCADRibbonController")
    tabs = main.findChild(QtWidgets.QTabBar, "VibeCADRibbonTabs")
    assert controller is not None and tabs is not None
    index = next(
        candidate
        for candidate in range(tabs.count())
        if str(tabs.tabData(candidate)) == workbench_name
    )
    tabs.setCurrentIndex(index)
    _process_events(24)
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == expected_id
    return controller, surface


def _focused_turn(surface, registry) -> NativeTurnSnapshot:
    state = registry.definition("state.read")
    trajectory = robot_trajectory_capability_definition()
    assert state is not None
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=("state.read", ROBOT_TRAJECTORY_CAPABILITY_NAME),
            schemas=(
                state.provider_schema(("active", "selection")),
                trajectory.provider_schema(_FEATURE_OPERATIONS),
            ),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _dispatcher(
    *,
    document,
    controller,
    surface,
    registry,
    state_store,
    ledger,
) -> NativeTurnDispatcher:
    frozen = NativeSurfaceSnapshot.from_surface(surface)

    def reauthorize() -> None:
        require_frozen_native_surface(frozen, controller)

    context = NativeRuntimeContext(
        service=get_service(),
        document=document,
        state=state_store,
        undo_ledger=ledger,
        reauthorize_turn=reauthorize,
        active_document=lambda: App.ActiveDocument,
        active_surface_id=lambda: read_active_ribbon_surface(controller).surface_id,
        edit_or_task_active=lambda: bool(Gui.Control.activeDialog()),
    )
    turn = _focused_turn(surface, registry)
    return NativeTurnDispatcher(
        document=document,
        state=state_store,
        registry=registry,
        turn=turn,
        runtimes=build_native_runtime_bindings(context, turn.tool_names),
        reauthorize_turn=reauthorize,
        active_document=lambda: App.ActiveDocument,
    )


def _trajectory_value(offset: float) -> object:
    value = Robot.Trajectory()
    for index, x_coordinate in enumerate((1000.0 + offset, 1020.0 + offset), 1):
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


def _add_trajectory(document, name: str, offset: float):
    trajectory = document.addObject("Robot::TrajectoryObject", name)
    trajectory.Trajectory = _trajectory_value(offset)
    return trajectory


def _record(document, trajectory):
    state = capture_robot_trajectory_state(document)
    index = state.trajectories.index(trajectory)
    return state, state.records[index]


def _feature_parity(record) -> dict:
    feature = dict(record.data["feature"])
    if feature["kind"] == "edge":
        source = dict(feature.pop("source"))
        feature["subelements"] = source["subelements"]
    elif feature["kind"] == "dress_up":
        feature.pop("source")
    elif feature["kind"] == "compound":
        feature["source_count"] = len(feature.pop("sources"))
    return {
        "type_id": record.data["type_id"],
        "base": record.data["base"],
        "feature": feature,
        "waypoints": [dict(value.data) for value in record.waypoints],
        "waypoint_count": record.data["waypoint_count"],
        "length_mm": record.data["length_mm"],
        "duration_seconds": record.data["duration_seconds"],
        "suppressed": record.data["suppressed"],
        "valid": record.data["valid"],
        "history_role": record.data["timeline"]["role"],
        "replacement_count": len(record.data["timeline"]["replaced_inputs"]),
        "visible": record.data["presentation"]["visible"],
    }


def _identity_placement() -> dict:
    return {
        "origin_mm": {"x": 0.0, "y": 0.0, "z": 0.0},
        "rotation": {
            "axis": {"x": 0.0, "y": 0.0, "z": 1.0},
            "angle_degrees": 0.0,
        },
    }


def _edge_arguments(
    document,
    source,
    *,
    mode: str,
    target=None,
    segmentation: float,
    use_rotation: bool,
) -> dict:
    del document
    return {
        "operation": "edge2_trac",
        "mode": mode,
        "target": None if target is None else {"object_name": target.Name},
        "source": {"object_name": source.Name},
        "edges": ["Edge1"],
        "segmentation_mm": segmentation,
        "use_rotation": use_rotation,
    }


def _dress_arguments(
    document,
    source,
    *,
    mode: str,
    target=None,
    speed: float,
    acceleration: float,
    continuity: str,
) -> dict:
    del document
    return {
        "operation": "trajectory_dress_up",
        "mode": mode,
        "target": None if target is None else {"object_name": target.Name},
        "source": {"object_name": source.Name},
        "use_speed": True,
        "speed_mm_per_s": speed,
        "use_acceleration": True,
        "acceleration_mm_per_s2": acceleration,
        "continuity_mode": continuity,
        "placement": _identity_placement(),
        "placement_mode": "unchanged",
    }


def _compound_arguments(
    document,
    sources,
    *,
    mode: str,
    target=None,
) -> dict:
    del document
    return {
        "operation": "trajectory_compound",
        "mode": mode,
        "target": None if target is None else {"object_name": target.Name},
        "sources": [
            {
                "trajectory": {"object_name": source.Name},
            }
            for source in sources
        ],
    }


def _human_features(document, edge_source, sources):
    timeline_before = document.getObject("VibeCADTimeline")
    operations_before = (
        () if timeline_before is None else tuple(timeline_before.Operations)
    )

    _select_edge(edge_source)
    Gui.runCommand("Robot_Edge2Trac", 0)
    _process_events(12)
    assert Gui.Control.activeDialog()
    ok = _task_button(QtWidgets.QDialogButtonBox.Ok)
    assert ok is not None
    ok.click()
    _process_events(12)
    edge = next(
        obj
        for obj in document.Objects
        if obj.TypeId == "Robot::Edge2TracObject" and obj not in operations_before
    )
    _, edge_initial = _record(document, edge)
    assert edge.Label == "EdgeTrajectory"
    assert edge.ViewObject.doubleClicked()
    _process_events(10)
    edge.SegValue = 1.25
    edge.UseRotation = True
    ok = _task_button(QtWidgets.QDialogButtonBox.Ok)
    assert ok is not None
    ok.click()
    _process_events(12)
    _, edge_edited = _record(document, edge)

    _select(sources[0])
    Gui.runCommand("Robot_TrajectoryDressUp", 0)
    _process_events(10)
    _set_dress_controls(
        speed_m_per_s=2.0,
        acceleration_m_per_s2=3.0,
        continuity_index=1,
    )
    ok = _task_button(QtWidgets.QDialogButtonBox.Ok)
    assert ok is not None
    ok.click()
    _process_events(12)
    modifier = next(
        obj
        for obj in document.Objects
        if obj.TypeId == "Robot::TrajectoryDressUpObject"
    )
    _, dress_initial = _record(document, modifier)
    assert modifier.Label == "TrajectoryModifier"
    assert modifier.ViewObject.doubleClicked()
    _process_events(10)
    _set_dress_controls(
        speed_m_per_s=3.2,
        acceleration_m_per_s2=4.4,
        continuity_index=2,
    )
    ok = _task_button(QtWidgets.QDialogButtonBox.Ok)
    assert ok is not None
    ok.click()
    _process_events(12)
    _, dress_edited = _record(document, modifier)

    _select(sources[0], sources[1])
    Gui.runCommand("Robot_TrajectoryCompound", 0)
    _process_events(10)
    ok = _task_button(QtWidgets.QDialogButtonBox.Ok)
    assert ok is not None
    ok.click()
    _process_events(12)
    sequence = next(
        obj for obj in document.Objects if obj.TypeId == "Robot::TrajectoryCompound"
    )
    _, compound_initial = _record(document, sequence)
    assert sequence.Label == "TrajectorySequence"
    assert sequence.ViewObject.doubleClicked()
    _process_events(10)
    _select(sources[1], sources[2])
    ok = _task_button(QtWidgets.QDialogButtonBox.Ok)
    assert ok is not None
    ok.click()
    _process_events(12)
    _, compound_edited = _record(document, sequence)

    timeline = document.getObject("VibeCADTimeline")
    assert tuple(timeline.Operations) == (
        *operations_before,
        edge,
        modifier,
        sequence,
    )
    assert sources[0].Visibility is False
    assert sources[1].Visibility is False
    assert sources[2].Visibility is False
    return {
        "edge_initial": edge_initial,
        "edge_edited": edge_edited,
        "dress_initial": dress_initial,
        "dress_edited": dress_edited,
        "compound_initial": compound_initial,
        "compound_edited": compound_edited,
        "operations": tuple(timeline.Operations),
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("AssemblyWorkbench")
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-robot-trajectory-features-"
        )
        document_path = Path(temporary.name) / "trajectory-features.FCStd"
        document = App.newDocument("NativeRobotTrajectoryFeaturesGate")
        document.UndoMode = 1

        human_sources = tuple(
            _add_trajectory(document, f"HumanSource{index}", offset)
            for index, offset in enumerate((0.0, 40.0, 80.0), 1)
        )
        native_sources = tuple(
            _add_trajectory(document, f"NativeSource{index}", offset)
            for index, offset in enumerate((0.0, 40.0, 80.0), 1)
        )
        human_edge_source = document.addObject("Part::Feature", "HumanRouteWire")
        human_edge_source.Shape = Part.makeLine(
            App.Vector(0.0, 0.0, 0.0),
            App.Vector(25.0, 0.0, 0.0),
        )
        native_edge_source = document.addObject("Part::Feature", "NativeRouteWire")
        native_edge_source.Shape = Part.makeLine(
            App.Vector(0.0, 0.0, 0.0),
            App.Vector(25.0, 0.0, 0.0),
        )
        bounded_source = document.addObject("Part::Feature", "BoundedRouteWire")
        bounded_source.Shape = Part.Wire(Part.makeCircle(100.0))
        sentinel = document.addObject("Part::Feature", "SelectionSentinel")
        sentinel.Shape = Part.makeBox(4.0, 5.0, 6.0)
        assert document.recompute(None, True, True) is not False

        human = _human_features(document, human_edge_source, human_sources)
        document.clearUndos()
        _select(sentinel)

        VibeGui._connect_document_observer()
        controller, assemble_surface = _ribbon_surface("AssemblyWorkbench", "assemble")
        registry = build_native_capability_registry()
        definition = registry.definition(ROBOT_TRAJECTORY_CAPABILITY_NAME)
        assert definition is not None
        assert tuple(value.operation for value in definition.variants[-3:]) == (
            _FEATURE_OPERATIONS
        )
        provider_schema = json.dumps(
            definition.provider_schema(_FEATURE_OPERATIONS), sort_keys=True
        ).casefold()
        for forbidden in (
            "file_path",
            "directory",
            "runcommand",
            "workbench",
            "selection",
            "preselection",
            "command_id",
        ):
            assert forbidden not in provider_schema

        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-robot-trajectory-features-gui")
        dispatcher = _dispatcher(
            document=document,
            controller=controller,
            surface=assemble_surface,
            registry=registry,
            state_store=state_store,
            ledger=ledger,
        )
        call_index = 0

        def call(
            arguments: dict,
            *,
            succeeds: bool = True,
            call_id: str = "",
            selected_dispatcher=None,
        ) -> dict:
            nonlocal call_index
            call_index += 1
            before_selection = _selection()
            result = (selected_dispatcher or dispatcher).call(
                ROBOT_TRAJECTORY_CAPABILITY_NAME,
                json.dumps(arguments, separators=(",", ":")),
                call_id or f"native-robot-trajectory-feature-{call_index}",
            )
            assert result.get("ok") is succeeds, result
            assert _selection() == before_selection
            assert not document.HasPendingTransaction
            return result

        baseline_state = capture_robot_trajectory_state(document)
        baseline_objects = tuple(document.Objects)
        baseline_history = tuple(document.VibeCADTimeline.Operations)
        baseline_undo = int(document.UndoCount)

        bounded = _edge_arguments(
            document,
            bounded_source,
            mode="create",
            segmentation=0.1,
            use_rotation=False,
        )
        bounded_result = call(bounded, succeeds=False)
        assert "bound" in json.dumps(bounded_result).casefold()
        assert capture_robot_trajectory_state(document) == baseline_state
        assert tuple(document.Objects) == baseline_objects
        assert tuple(document.VibeCADTimeline.Operations) == baseline_history
        assert int(document.UndoCount) == baseline_undo

        edge_create = _edge_arguments(
            document,
            native_edge_source,
            mode="create",
            segmentation=0.5,
            use_rotation=False,
        )
        original_verifier = runtime_module.verify_trajectory_feature

        def reject_verifier(_document, _draft):
            raise NativeRobotTrajectoryError("Forced trajectory feature rejection.")

        runtime_module.verify_trajectory_feature = reject_verifier
        try:
            rolled_back = call(edge_create, succeeds=False)
        finally:
            runtime_module.verify_trajectory_feature = original_verifier
        assert rolled_back["error_code"] == "NATIVE_ROBOT_TRAJECTORY_FAILED"
        assert capture_robot_trajectory_state(document) == baseline_state
        assert tuple(document.Objects) == baseline_objects
        assert tuple(document.VibeCADTimeline.Operations) == baseline_history
        assert int(document.UndoCount) == baseline_undo

        edge_call_id = "native-edge-feature-idempotent"
        edge_result = call(edge_create, call_id=edge_call_id)
        native_edge = document.getObject(edge_result["trajectory"]["object_name"])
        assert native_edge is not None
        _, native_edge_initial = _record(document, native_edge)
        assert _feature_parity(native_edge_initial) == _feature_parity(
            human["edge_initial"]
        )
        undo_after_edge = int(document.UndoCount)
        assert call(edge_create, call_id=edge_call_id) == edge_result
        assert int(document.UndoCount) == undo_after_edge

        edge_edit = _edge_arguments(
            document,
            native_edge_source,
            mode="edit",
            target=native_edge,
            segmentation=1.25,
            use_rotation=True,
        )
        call(edge_edit)
        _, native_edge_edited = _record(document, native_edge)
        assert _feature_parity(native_edge_edited) == _feature_parity(
            human["edge_edited"]
        )

        edge_noop_arguments = _edge_arguments(
            document,
            native_edge_source,
            mode="edit",
            target=native_edge,
            segmentation=1.25,
            use_rotation=True,
        )
        noop_undo = int(document.UndoCount)
        noop_revision = state_store.current_revision(document.Uid)
        noop_receipts = tuple(state_store.snapshot(document.Uid)["recent_receipts"])
        edge_noop = call(edge_noop_arguments)
        assert edge_noop["changed"] is False and "receipt" not in edge_noop
        assert int(document.UndoCount) == noop_undo
        assert state_store.current_revision(document.Uid) == noop_revision
        assert tuple(state_store.snapshot(document.Uid)["recent_receipts"]) == (
            noop_receipts
        )

        dress_create = _dress_arguments(
            document,
            native_sources[0],
            mode="create",
            speed=2000.0,
            acceleration=3000.0,
            continuity="continuous",
        )
        dress_result = call(dress_create)
        native_dress = document.getObject(dress_result["trajectory"]["object_name"])
        assert native_dress is not None
        _, native_dress_initial = _record(document, native_dress)
        assert _feature_parity(native_dress_initial) == _feature_parity(
            human["dress_initial"]
        )
        assert native_sources[0].Visibility is False

        dress_edit = _dress_arguments(
            document,
            native_sources[0],
            mode="edit",
            target=native_dress,
            speed=3200.0,
            acceleration=4400.0,
            continuity="discontinuous",
        )
        call(dress_edit)
        _, native_dress_edited = _record(document, native_dress)
        assert _feature_parity(native_dress_edited) == _feature_parity(
            human["dress_edited"]
        )

        cycle = _dress_arguments(
            document,
            native_dress,
            mode="edit",
            target=native_dress,
            speed=3200.0,
            acceleration=4400.0,
            continuity="discontinuous",
        )
        before_cycle = capture_robot_trajectory_state(document)
        cycle_undo = int(document.UndoCount)
        cycle_result = call(cycle, succeeds=False)
        assert "cycle" in json.dumps(cycle_result).casefold()
        assert capture_robot_trajectory_state(document) == before_cycle
        assert int(document.UndoCount) == cycle_undo

        compound_create = _compound_arguments(
            document,
            native_sources[:2],
            mode="create",
        )
        compound_result = call(compound_create)
        native_compound = document.getObject(
            compound_result["trajectory"]["object_name"]
        )
        assert native_compound is not None
        _, native_compound_initial = _record(document, native_compound)
        assert _feature_parity(native_compound_initial) == _feature_parity(
            human["compound_initial"]
        )

        compound_edit = _compound_arguments(
            document,
            native_sources[1:],
            mode="edit",
            target=native_compound,
        )
        call(compound_edit)
        final_state, native_compound_edited = _record(document, native_compound)
        assert _feature_parity(native_compound_edited) == _feature_parity(
            human["compound_edited"]
        )
        assert native_sources[0].Visibility is False
        assert native_sources[1].Visibility is False
        assert native_sources[2].Visibility is False

        controller, manufacture_surface = _ribbon_surface("CAMWorkbench", "manufacture")
        manufacture_provider = resolve_native_provider_surface(
            manufacture_surface,
            registry,
        )
        assert ROBOT_TRAJECTORY_CAPABILITY_NAME not in (
            manufacture_provider.missing_definition_names
        )
        assert ROBOT_TRAJECTORY_CAPABILITY_NAME not in (
            manufacture_provider.missing_implementation_names
        )
        assert ROBOT_TRAJECTORY_CAPABILITY_NAME not in (
            manufacture_provider.incomplete_definition_names
        )
        manufacture_dispatcher = _dispatcher(
            document=document,
            controller=controller,
            surface=manufacture_surface,
            registry=registry,
            state_store=state_store,
            ledger=ledger,
        )
        manufacture_snapshot = build_manufacture_snapshot(document)
        snapshot_json = json.dumps(manufacture_snapshot, sort_keys=True)
        assert manufacture_snapshot["robot_tool_shapes"]["available"] is True
        assert manufacture_snapshot["robot_trajectories"]["available"] is True
        assert str(temporary.name) not in snapshot_json

        edge_noop = _edge_arguments(
            document,
            native_edge_source,
            mode="edit",
            target=native_edge,
            segmentation=1.25,
            use_rotation=True,
        )
        dress_noop = _dress_arguments(
            document,
            native_sources[0],
            mode="edit",
            target=native_dress,
            speed=3200.0,
            acceleration=4400.0,
            continuity="discontinuous",
        )
        compound_noop = _compound_arguments(
            document,
            native_sources[1:],
            mode="edit",
            target=native_compound,
        )
        before_manufacture_noop = int(document.UndoCount)
        for arguments in (edge_noop, dress_noop, compound_noop):
            manufacture_result = call(
                arguments,
                selected_dispatcher=manufacture_dispatcher,
            )
            assert manufacture_result["changed"] is False
            assert "receipt" not in manufacture_result
        assert int(document.UndoCount) == before_manufacture_noop

        final_operations = tuple(document.VibeCADTimeline.Operations)
        assert final_operations == (
            *human["operations"],
            native_edge,
            native_dress,
            native_compound,
        )
        compound_name = native_compound.Name
        final_compound_sha = native_compound_edited.state_sha256
        document.undo()
        _process_events(14)
        _, compound_after_undo = _record(document, native_compound)
        assert compound_after_undo.state_sha256 != final_compound_sha
        document.redo()
        _process_events(14)
        _, compound_after_redo = _record(document, native_compound)
        assert compound_after_redo.state_sha256 == final_compound_sha

        final_state = capture_robot_trajectory_state(document)
        final_digests = {
            trajectory.Name: record.state_sha256
            for trajectory, record in zip(
                final_state.trajectories,
                final_state.records,
                strict=True,
            )
        }
        final_setup_digest = final_state.state_sha256
        final_operation_names = tuple(
            operation.Name for operation in document.VibeCADTimeline.Operations
        )
        assert _selection() == ((sentinel, ()),)
        document.saveAs(str(document_path))
        old_name = document.Name
        App.closeDocument(old_name)
        document = App.openDocument(str(document_path))
        App.setActiveDocument(document.Name)
        assert document.recompute(None, True, True) is not False
        _process_events(24)
        restored = capture_robot_trajectory_state(document)
        assert restored.state_sha256 == final_setup_digest
        assert {
            trajectory.Name: record.state_sha256
            for trajectory, record in zip(
                restored.trajectories,
                restored.records,
                strict=True,
            )
        } == final_digests
        assert (
            tuple(operation.Name for operation in document.VibeCADTimeline.Operations)
            == final_operation_names
        )
        assert document.getObject(compound_name) is not None

        print(
            "VIBECAD_NATIVE_ROBOT_TRAJECTORY_FEATURES_GUI_OK "
            "human_edge_parity=true human_dress_up_parity=true "
            "human_compound_parity=true exact_history=true exact_targets=true "
            "manufacture_surface=true cycle_noop=true bounded=true "
            "rollback=true verified_noop=true idempotent=true undo_redo=true "
            "reopen=true selection_preserved=true",
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
