# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact Native Assembly playback control."""

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
import VibeCADNativeAssemblyPlayback as playback_module
from VibeCADCore import get_service
from VibeCADNativeAssemblyPlayback import (
    active_native_assembly_playback_summary,
    owns_active_native_assembly_playback,
)
from VibeCADNativeAssemblyPlaybackBindings import (
    ASSEMBLY_PLAYBACK_CAPABILITY_NAME,
)
from VibeCADNativeAssemblyPlaybackSchema import (
    assembly_playback_capability_definition,
)
from VibeCADNativeAssemblyStructureBindings import (
    ASSEMBLY_MOTION_STUDY_CAPABILITY_NAME,
)
from VibeCADNativeAssemblyStructureSchema import (
    assembly_motion_study_capability_definition,
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
    structure = assembly_motion_study_capability_definition()
    playback = assembly_playback_capability_definition()
    assert state is not None
    provider = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot.from_surface(surface),
        available=True,
        unavailable_reason="",
        tool_names=(
            "state.read",
            ASSEMBLY_MOTION_STUDY_CAPABILITY_NAME,
            ASSEMBLY_PLAYBACK_CAPABILITY_NAME,
        ),
        schemas=(
            state.provider_schema(("active", "selection")),
            structure.provider_schema(("create_simulation",)),
            playback.provider_schema(
                ("show", "seek", "step", "play", "pause", "close")
            ),
        ),
        human_only_action_ids=("Assembly_ActivateAssembly",),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(provider)


def _assembly_summary(state: dict, assembly_name: str) -> dict:
    return next(
        item
        for item in state["domain"]["assemblies"]
        if item["object_name"] == assembly_name
    )


def _camera_without_dynamic_clipping(camera: str) -> str:
    return "\n".join(
        line
        for line in str(camera).splitlines()
        if "nearDistance" not in line and "farDistance" not in line
    )


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("AssemblyWorkbench")
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-assembly-playback-"
        )
        path = Path(temporary.name) / "native-assembly-playback.FCStd"
        document = App.newDocument("NativeAssemblyPlaybackGate")
        document.UndoMode = 1

        sources = []
        for index in range(2):
            source = document.addObject("Part::Feature", f"PlaybackSource{index + 1}")
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
        document.openTransaction("Prepare Assembly playback mechanism")
        try:
            components = []
            for index, source in enumerate(sources):
                component = assembly.newObject(
                    "App::Link",
                    f"PlaybackOccurrence{index + 1}",
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
                "PlaybackCylindricalJoint",
            )
            drive.Label = "Playback cylindrical actuator"
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

        VibeGui._connect_document_observer()
        main_window = Gui.getMainWindow()
        controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
        assert controller is not None
        _select_assemble_ribbon(main_window)
        surface = read_active_ribbon_surface(controller)
        frozen = NativeSurfaceSnapshot.from_surface(surface)

        registry = build_native_capability_registry()
        playback_definition = registry.definition(ASSEMBLY_PLAYBACK_CAPABILITY_NAME)
        assert playback_definition is not None
        assert tuple(variant.operation for variant in playback_definition.variants) == (
            "show",
            "seek",
            "step",
            "play",
            "pause",
            "close",
        )
        assert all(
            variant.transaction_behavior == "presentation"
            and variant.background_required is False
            for variant in playback_definition.variants
        )
        assert registry.implementation(ASSEMBLY_PLAYBACK_CAPABILITY_NAME) is not None

        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-assembly-playback-gui")

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

        call_number = 0

        def call(
            tool_name: str,
            arguments: dict,
            *,
            succeeds: bool = True,
            call_id: str = "",
        ) -> dict:
            nonlocal call_number
            call_number += 1
            result = dispatcher.call(
                tool_name,
                json.dumps(arguments, separators=(",", ":")),
                call_id or f"assembly-playback-call-{call_number}",
            )
            assert result.get("ok") is succeeds, result
            assert Gui.activeDocument().getInEdit() is assembly.ViewObject
            return result

        initial = call("state.read", {"operation": "active"})
        summary = _assembly_summary(initial, assembly.Name)
        assert summary["artifacts"]["simulations"] == 0
        create_arguments = {
            "operation": "create_simulation",
            "label": "Native playback cycle",
            "time_start_seconds": 0.0,
            "time_end_seconds": 1.0,
            "output_time_step_seconds": 0.1,
            "global_error_tolerance": 1.0e-6,
            "frames_per_second": 30,
            "motions": [
                {
                    "joint": {"object_name": drive.Name},
                    "motion_type": "linear",
                    "linear_speed_mm_per_second": 8.0,
                }
            ],
        }
        created = call(ASSEMBLY_MOTION_STUDY_CAPABILITY_NAME, create_arguments)
        assert created["verified"] is True
        assert created["frame_count"] >= 11
        simulation_name = created["simulation"]["object_name"]
        simulation = document.getObject(simulation_name)
        assert simulation is not None
        document.save()
        _process_events(12)
        # Establish a clean presentation baseline explicitly.  Assembly edit
        # contextual-panel refreshes can dirty the GUI document independently
        # of the just-saved App document; playback must preserve the baseline
        # we actually hand it.
        Gui.getDocument(document.Name).Modified = False

        current = call("state.read", {"operation": "active"})
        current_summary = _assembly_summary(current, assembly.Name)
        assert current_summary["artifacts"]["simulations"] == 1
        assert current_summary["playback"] == {"active": False}

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(components[1])
        selection_before = Gui.Selection.getSelection()
        placements_before = {
            component.Name: App.Placement(component.Placement)
            for component in components
        }
        visibility_before = {
            component.Name: bool(component.ViewObject.Visibility)
            for component in components
        }
        view = Gui.activeDocument().activeView()
        camera_before = str(view.getCamera())
        objects_before = tuple(document.Objects)
        undo_before = int(document.UndoCount)

        open_arguments = {
            "operation": "show",
            "simulation": {"object_name": simulation_name},
        }
        off_grid = {**open_arguments, "time_seconds": 0.15}
        failure = call(
            ASSEMBLY_PLAYBACK_CAPABILITY_NAME,
            off_grid,
            succeeds=False,
        )
        assert failure["error_code"] == "NATIVE_ASSEMBLY_PLAYBACK_FAILED"
        assert Gui.Control.activeTaskDialog() is None

        revision_before_playback = state_store.current_revision(str(document.Uid))
        opened = call(ASSEMBLY_PLAYBACK_CAPABILITY_NAME, open_arguments)
        assert opened["verified"] is True
        playback_id = opened["playback_id"]
        assert len(playback_id) == 32
        assert opened["frame"] == 1
        assert opened["time_seconds"] == 0.0
        assert opened["playing"] is False
        assert opened["direction"] == "paused"
        assert opened["frame_count"] >= 11
        assert Gui.Control.activeTaskDialog() is not None
        assert owns_active_native_assembly_playback(document)
        assert Gui.Selection.getSelection() == selection_before
        assert tuple(document.Objects) == objects_before
        assert int(document.UndoCount) == undo_before

        during = call("state.read", {"operation": "active"})
        during_summary = _assembly_summary(during, assembly.Name)
        assert during_summary["playback"]["playback_id"] == playback_id
        assert during_summary["playback"]["simulation"]["object_name"] == (
            simulation_name
        )

        blocked_mutation = call(
            ASSEMBLY_MOTION_STUDY_CAPABILITY_NAME,
            create_arguments,
            succeeds=False,
        )
        assert blocked_mutation["error_code"] == "NATIVE_RUNTIME_GUARD_FAILED"
        assert tuple(document.Objects) == objects_before
        assert int(document.UndoCount) == undo_before

        control_base = {"playback_id": playback_id}
        seeked = call(
            ASSEMBLY_PLAYBACK_CAPABILITY_NAME,
            {**control_base, "operation": "seek", "time_seconds": 1.0},
        )
        assert seeked["time_seconds"] == 1.0
        assert seeked["playing"] is False
        assert any(
            not component.Placement.isSame(
                placements_before[component.Name],
                1.0e-9,
            )
            for component in components
        )

        stepped = call(
            ASSEMBLY_PLAYBACK_CAPABILITY_NAME,
            {**control_base, "operation": "step", "direction": "backward"},
        )
        assert stepped["frame"] == seeked["frame"] - 1
        replayed_step = call(
            ASSEMBLY_PLAYBACK_CAPABILITY_NAME,
            {**control_base, "operation": "step", "direction": "forward"},
        )
        assert replayed_step["frame"] == seeked["frame"]

        playing = call(
            ASSEMBLY_PLAYBACK_CAPABILITY_NAME,
            {**control_base, "operation": "play", "direction": "backward"},
        )
        assert playing["playing"] is True
        assert playing["direction"] == "backward"
        paused = call(
            ASSEMBLY_PLAYBACK_CAPABILITY_NAME,
            {**control_base, "operation": "pause"},
        )
        assert paused["playing"] is False
        assert paused["direction"] == "paused"

        frame_before_save = paused["frame"]
        document.save()
        _process_events(20)
        active_after_save = active_native_assembly_playback_summary(assembly)
        assert active_after_save["active"] is True
        assert active_after_save["frame"] == frame_before_save
        assert Gui.getDocument(document.Name).Modified is False

        close_arguments = {**control_base, "operation": "close"}
        close_call_id = "assembly-playback-close"
        closed = call(
            ASSEMBLY_PLAYBACK_CAPABILITY_NAME,
            close_arguments,
            call_id=close_call_id,
        )
        assert closed["closed"] is True
        assert closed["verified"] is True
        assert closed["playing"] is False
        assert Gui.Control.activeTaskDialog() is None
        assert not owns_active_native_assembly_playback(document)
        assert Gui.Selection.getSelection() == selection_before
        assert all(
            component.Placement.isSame(
                placements_before[component.Name],
                1.0e-9,
            )
            for component in components
        )
        assert all(
            bool(component.ViewObject.Visibility) == visibility_before[component.Name]
            for component in components
        )
        assert _camera_without_dynamic_clipping(view.getCamera()) == (
            _camera_without_dynamic_clipping(camera_before)
        )
        assert Gui.getDocument(document.Name).Modified is False
        assert tuple(document.Objects) == objects_before
        assert int(document.UndoCount) == undo_before

        close_replay = call(
            ASSEMBLY_PLAYBACK_CAPABILITY_NAME,
            close_arguments,
            call_id=close_call_id,
        )
        assert close_replay == closed
        missing = call(
            ASSEMBLY_PLAYBACK_CAPABILITY_NAME,
            close_arguments,
            succeeds=False,
        )
        assert missing["error_code"] == "NATIVE_ASSEMBLY_PLAYBACK_FAILED"

        # Saving while playback is open establishes a new clean baseline even
        # when the GUI document was dirty when Native opened the player.
        Gui.getDocument(document.Name).Modified = True
        dirty_opened = call(
            ASSEMBLY_PLAYBACK_CAPABILITY_NAME,
            {**open_arguments, "time_seconds": 0.3},
        )
        dirty_control = {
            "playback_id": dirty_opened["playback_id"],
        }
        document.save()
        _process_events(20)
        assert (
            active_native_assembly_playback_summary(assembly)["frame"]
            == (dirty_opened["frame"])
        )
        dirty_closed = call(
            ASSEMBLY_PLAYBACK_CAPABILITY_NAME,
            {**dirty_control, "operation": "close"},
        )
        assert dirty_closed["closed"] is True
        assert Gui.getDocument(document.Name).Modified is False

        reopened = call(
            ASSEMBLY_PLAYBACK_CAPABILITY_NAME,
            {**open_arguments, "mode": "forward", "time_seconds": 0.2},
        )
        assert reopened["playing"] is True
        assert reopened["direction"] == "forward"
        Gui.Control.activeTaskDialog().reject()
        _process_events(20)
        assert Gui.Control.activeTaskDialog() is None
        assert str(document.Uid) not in playback_module._SESSIONS
        assert not owns_active_native_assembly_playback(document)
        assert active_native_assembly_playback_summary(assembly) == {"active": False}
        assert all(
            component.Placement.isSame(
                placements_before[component.Name],
                1.0e-9,
            )
            for component in components
        )
        assert Gui.Selection.getSelection() == selection_before
        assert Gui.getDocument(document.Name).Modified is False
        assert (
            state_store.current_revision(str(document.Uid))
            == revision_before_playback
        )

        print(
            "VIBECAD_NATIVE_ASSEMBLY_PLAYBACK_GUI_OK "
            "generated=true seek=true step=true bidirectional=true pause=true "
            "mutation_blocked=true save_baseline=true dirty_save_clean=true "
            "manual_close=true "
            "idempotent=true restored=true selection_preserved=true "
            "revision_stable=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        if Gui.Control.activeTaskDialog() is not None:
            try:
                Gui.Control.activeTaskDialog().reject()
                _process_events(8)
            except (AttributeError, RuntimeError):
                pass
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
