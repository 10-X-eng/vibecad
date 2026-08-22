# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact Native Assembly simulation creation."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import CommandCreateJoint
import JointObject
import Part
import Preferences
import UtilsAssembly
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeAssemblySimulationState import (
    capture_assembly_simulation_state,
)
from VibeCADNativeAssemblyStructureBindings import (
    ASSEMBLY_STRUCTURE_CAPABILITY_NAME,
)
from VibeCADNativeAssemblyStructureSchema import (
    assembly_structure_capability_definition,
)
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


def _process_events(rounds: int = 20) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _select_assemble_ribbon(main_window) -> None:
    tabs = main_window.findChild(QtWidgets.QTabBar, "VibeCADRibbonTabs")
    assert tabs is not None
    index = next(
        candidate
        for candidate in range(tabs.count())
        if str(tabs.tabData(candidate)) == "AssemblyWorkbench"
    )
    tabs.setCurrentIndex(index)
    _process_events(24)
    assert Gui.activeWorkbench().name() == "AssemblyWorkbench"


def _focused_turn(surface, registry) -> NativeTurnSnapshot:
    state = registry.definition("state.read")
    structure = assembly_structure_capability_definition()
    assert state is not None
    provider = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot.from_surface(surface),
        available=True,
        unavailable_reason="",
        tool_names=("state.read", ASSEMBLY_STRUCTURE_CAPABILITY_NAME),
        schemas=(
            state.provider_schema(("active", "selection")),
            structure.provider_schema(("create_simulation",)),
        ),
        human_only_action_ids=("Assembly_ActivateAssembly",),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(provider)


def _simulation_arguments(
    *,
    label: str,
    motions: list[dict],
    time_end_seconds: float = 1.0,
    output_time_step_seconds: float = 0.05,
) -> dict:
    return {
        "operation": "create_simulation",
        "label": label,
        "time_start_seconds": 0.0,
        "time_end_seconds": time_end_seconds,
        "output_time_step_seconds": output_time_step_seconds,
        "global_error_tolerance": 1.0e-6,
        "frames_per_second": 30,
        "motions": motions,
    }


def _history_block(document, simulation) -> list:
    timeline = document.getObject("VibeCADTimeline")
    assert timeline is not None
    operations = list(timeline.Operations)
    visibility = list(timeline.VisibilityAtEnd)
    block = [*simulation.Group, simulation]
    start = operations.index(simulation.Group[0])
    assert operations[start : start + len(block)] == block
    assert all(
        bool(visibility[start + index]) == bool(obj.Visibility)
        for index, obj in enumerate(block)
    )
    return block


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("AssemblyWorkbench")
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-assembly-simulation-"
        )
        path = Path(temporary.name) / "native-assembly-simulation.FCStd"
        document = App.newDocument("NativeAssemblySimulationGate")
        document.UndoMode = 1

        sources = []
        for index in range(2):
            source = document.addObject("Part::Feature", f"SimulationSource{index + 1}")
            source.Shape = Part.makeBox(20.0, 12.0, 8.0)
            sources.append(source)
        document.recompute()
        document.saveAs(str(path))

        Gui.runCommand("Assembly_CreateAssembly")
        _process_events(24)
        assembly = next(
            obj for obj in document.Objects if obj.TypeId == "Assembly::AssemblyObject"
        )
        assert Gui.activeDocument().getInEdit() is assembly.ViewObject

        old_solve_preference = Preferences.preferences().GetBool(
            "SolveInJointCreation",
            True,
        )
        Preferences.preferences().SetBool("SolveInJointCreation", False)
        document.openTransaction("Prepare Assembly simulation mechanism")
        try:
            components = []
            for index, source in enumerate(sources):
                component = assembly.newObject(
                    "App::Link",
                    f"SimulationOccurrence{index + 1}",
                )
                component.LinkedObject = source
                component.Placement.Base.x = float(index * 40)
                UtilsAssembly.finalizeInsertedComponentTimeline(component)
                components.append(component)

            ground = CommandCreateJoint.createGroundedJointFeature(
                components[0],
                assembly,
            )
            JointObject.ensureViewProviderGroundedJoint(ground)
            document.finalizeProvisionalTimelineOperationBlock(ground, [ground])

            joint_group = UtilsAssembly.getJointGroup(assembly)
            drive = joint_group.newObject(
                "App::FeaturePython",
                "SimulationCylindricalJoint",
            )
            drive.Label = "Cylindrical actuator"
            JointObject.Joint(drive, 2)
            JointObject.ensureViewProviderJoint(drive)
            drive.Proxy.setJointConnectors(
                drive,
                [
                    [components[0], ["", ""]],
                    [components[1], ["", ""]],
                ],
            )
            document.finalizeProvisionalTimelineOperationBlock(drive, [drive])
            document.recompute()
            document.commitTransaction()
        except Exception:
            document.abortTransaction()
            raise
        finally:
            Preferences.preferences().SetBool(
                "SolveInJointCreation",
                old_solve_preference,
            )
        _process_events(16)
        baseline = {
            component.Name: App.Placement(component.Placement)
            for component in components
        }

        VibeGui._connect_document_observer()
        main_window = Gui.getMainWindow()
        controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
        assert controller is not None
        _select_assemble_ribbon(main_window)
        surface = read_active_ribbon_surface(controller)
        assert surface.surface_id == "assemble"
        assert "Assembly_CreateSimulation" in surface.command_ids
        frozen = NativeSurfaceSnapshot.from_surface(surface)

        registry = build_native_capability_registry()
        structure = registry.definition(ASSEMBLY_STRUCTURE_CAPABILITY_NAME)
        assert structure is not None
        variant = next(
            value
            for value in structure.variants
            if value.operation == "create_simulation"
        )
        assert variant.action_ids == frozenset({"Assembly_CreateSimulation"})
        assert variant.transaction_behavior == "document"
        assert variant.background_required is False
        assert registry.implementation(ASSEMBLY_STRUCTURE_CAPABILITY_NAME) is not None

        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-assembly-simulation-gui")

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
        def new_dispatcher() -> NativeTurnDispatcher:
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

        dispatcher = new_dispatcher()

        call_number = 0

        def call(arguments: dict, *, succeeds: bool = True, call_id: str = "") -> dict:
            nonlocal call_number
            call_number += 1
            task_before = Gui.Control.activeTaskDialog()
            result = dispatcher.call(
                ASSEMBLY_STRUCTURE_CAPABILITY_NAME,
                json.dumps(arguments, separators=(",", ":")),
                call_id or f"assembly-simulation-call-{call_number}",
            )
            assert result.get("ok") is succeeds, result
            assert Gui.activeDocument().getInEdit() is assembly.ViewObject
            assert Gui.Control.activeTaskDialog() is task_before
            return result

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(components[1])
        document.clearUndos()

        initial = dispatcher.call(
            "state.read",
            '{"operation":"active"}',
            "assembly-simulation-state-initial",
        )
        assert initial["ok"] is True, initial
        first_arguments = _simulation_arguments(
            label="Native cylindrical cycle",
            motions=[
                {
                    "joint": {"object_name": drive.Name},
                    "motion_type": "angular",
                    "angular_speed_degrees_per_second": 90.0,
                },
                {
                    "joint": {"object_name": drive.Name},
                    "motion_type": "linear",
                    "linear_speed_mm_per_second": 8.0,
                },
            ],
        )
        malformed = dict(first_arguments)
        malformed["unexpected"] = True
        before_objects = tuple(document.Objects)
        failure = call(malformed, succeeds=False)
        assert failure["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(document.Objects) == before_objects
        assert int(document.UndoCount) == 0

        first_call_id = "assembly-simulation-create-first"
        first = call(first_arguments, call_id=first_call_id)
        assert first["verified"] is True
        assert first["label"] == "Native cylindrical cycle"
        assert first["simulation_count"] == 1
        assert first["motion_count"] == 2
        assert first["angular_motion_count"] == 1
        assert first["linear_motion_count"] == 1
        assert first["planned_output_interval_count"] == 20
        assert first["frame_count"] >= 21
        assert first["assistant_undo_available"] is True
        assert Gui.Selection.getSelection() == [components[1]]
        assert all(
            component.Placement.isSame(baseline[component.Name], 1.0e-9)
            for component in components
        )
        assert int(document.UndoCount) == 1

        group_name = first["simulation_group"]["object_name"]
        first_name = first["simulation"]["object_name"]
        first_simulation = document.getObject(first_name)
        first_motion_names = [motion.Name for motion in first_simulation.Group]
        assert [motion.MotionType for motion in first_simulation.Group] == [
            "Angular",
            "Linear",
        ]
        assert [motion.Joint[0] for motion in first_simulation.Group] == [drive, drive]
        assert [motion.Formula for motion in first_simulation.Group] == [
            "initialValue + (90*pi/180)*time",
            "initialValue + 8*time",
        ]
        _history_block(document, first_simulation)

        replay = call(first_arguments, call_id=first_call_id)
        assert replay == first
        assert int(document.UndoCount) == 1

        document.undo()
        _process_events(20)
        assert document.getObject(group_name) is None
        assert document.getObject(first_name) is None
        assert all(document.getObject(name) is None for name in first_motion_names)
        assert Gui.Selection.getSelection() == [components[1]]
        document.redo()
        _process_events(20)
        first_simulation = document.getObject(first_name)
        assert first_simulation is not None
        assert [motion.Name for motion in first_simulation.Group] == first_motion_names
        assert all(
            motion.VibeCADTimelineOwner is first_simulation
            for motion in first_simulation.Group
        )
        _history_block(document, first_simulation)
        dispatcher = new_dispatcher()

        after_first = dispatcher.call(
            "state.read",
            '{"operation":"active"}',
            "assembly-simulation-state-after-first",
        )
        assert after_first["ok"] is True, after_first

        second_arguments = _simulation_arguments(
            label="Native linear service cycle",
            motions=[
                {
                    "joint": {"object_name": drive.Name},
                    "motion_type": "linear",
                    "formula": "initialValue - 4*time",
                }
            ],
            time_end_seconds=2.0,
            output_time_step_seconds=0.1,
        )
        second = call(second_arguments)
        assert second["simulation_group"]["object_name"] == group_name
        assert second["simulation_count"] == 2
        assert second["motion_count"] == 1
        assert second["angular_motion_count"] == 0
        assert second["linear_motion_count"] == 1
        assert second["planned_output_interval_count"] == 20
        assert int(document.UndoCount) == 2
        second_name = second["simulation"]["object_name"]
        second_simulation = document.getObject(second_name)
        second_motion_name = second_simulation.Group[0].Name
        assert second_simulation.Group[0].MotionType == "Linear"
        assert second_simulation.Group[0].Formula == "initialValue - 4*time"
        _history_block(document, second_simulation)
        assert all(
            component.Placement.isSame(baseline[component.Name], 1.0e-9)
            for component in components
        )

        document.undo()
        _process_events(16)
        assert document.getObject(second_name) is None
        assert document.getObject(second_motion_name) is None
        assert document.getObject(first_name) is not None
        document.redo()
        _process_events(16)
        second_simulation = document.getObject(second_name)
        assert second_simulation is not None
        _history_block(document, second_simulation)

        assembly_name = assembly.Name
        component_names = [component.Name for component in components]
        drive_name = drive.Name
        Gui.Selection.clearSelection()
        Gui.activeDocument().resetEdit()
        _process_events(16)
        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(path))
        App.setActiveDocument(document.Name)
        _process_events(24)

        assembly = document.getObject(assembly_name)
        group = document.getObject(group_name)
        first_simulation = document.getObject(first_name)
        second_simulation = document.getObject(second_name)
        drive = document.getObject(drive_name)
        assert assembly is not None and group in list(assembly.Group)
        assert list(group.Group) == [first_simulation, second_simulation]
        assert type(first_simulation.Proxy).__name__ == "Simulation"
        assert type(first_simulation.ViewObject.Proxy).__name__ == (
            "ViewProviderSimulation"
        )
        assert type(second_simulation.Proxy).__name__ == "Simulation"
        assert [type(motion.Proxy).__name__ for motion in first_simulation.Group] == [
            "Motion",
            "Motion",
        ]
        assert all(
            type(motion.ViewObject.Proxy).__name__ == "ViewProviderMotion"
            for simulation in (first_simulation, second_simulation)
            for motion in simulation.Group
        )
        assert all(
            motion.VibeCADTimelineOwner is simulation
            and motion.VibeCADTimelineRole == "resource"
            and motion.Joint[0] is drive
            for simulation in (first_simulation, second_simulation)
            for motion in simulation.Group
        )
        assert first_simulation.VibeCADTimelineEditCommand == (
            "Assembly_EditHistoryOperation"
        )
        assert second_simulation.VibeCADTimelineEditCommand == (
            "Assembly_EditHistoryOperation"
        )
        assert all(
            document.getObject(name).Placement.isSame(baseline[name], 1.0e-9)
            for name in component_names
        )
        _history_block(document, first_simulation)
        _history_block(document, second_simulation)
        restored = capture_assembly_simulation_state(assembly)
        assert restored.simulation_group is group
        assert restored.simulations == (first_simulation, second_simulation)
        assert len(restored.simulation_records[0]["motions"]) == 2
        assert len(restored.simulation_records[1]["motions"]) == 1
        assert len(restored.eligible_joints) == 1
        assert restored.eligible_joints[0].obj is drive

        print(
            "VIBECAD_NATIVE_ASSEMBLY_SIMULATION_GUI_OK "
            "simulations=2 motions=3 cylindrical_dual_motion=true "
            "kinematics_verified=true idempotent=true "
            "undo_redo=true reopen=true placements_unchanged=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        if document is not None:
            try:
                Gui.activeDocument().resetEdit()
            except (AttributeError, RuntimeError):
                pass
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(0, _run)
