# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for exact Native Assembly solver execution."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import JointObject
import Preferences
import UtilsAssembly
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeAssemblySnapshot import build_assembly_snapshot
from VibeCADNativeAssemblyStructureBindings import (
    ASSEMBLY_STRUCTURE_CAPABILITY_NAME,
)
from VibeCADNativeAssemblyStructureSchema import (
    assembly_structure_capability_definition,
)
from VibeCADNativeCapabilityRegistry import (
    NativeProviderSurface,
    resolve_native_provider_surface,
)
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
        (
            candidate
            for candidate in range(tabs.count())
            if str(tabs.tabData(candidate)) == "AssemblyWorkbench"
        ),
        -1,
    )
    assert index >= 0
    tabs.setCurrentIndex(index)
    _process_events(24)
    assert Gui.activeWorkbench().name() == "AssemblyWorkbench"


def _focused_turn(surface, registry) -> NativeTurnSnapshot:
    state_definition = registry.definition("state.read")
    assert state_definition is not None
    structure = assembly_structure_capability_definition()
    provider_surface = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot.from_surface(surface),
        available=True,
        unavailable_reason="",
        tool_names=("state.read", ASSEMBLY_STRUCTURE_CAPABILITY_NAME),
        schemas=(
            state_definition.provider_schema(("active", "selection")),
            structure.provider_schema(("solve_assembly",)),
        ),
        human_only_action_ids=("Assembly_ActivateAssembly",),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(provider_surface)


def _joint_group(assembly):
    groups = [
        child for child in assembly.Group if child.TypeId == "Assembly::JointGroup"
    ]
    assert len(groups) == 1
    return groups[0]


def _solve_arguments(summary: dict) -> dict:
    solver_state = summary["solver_state"]
    assert solver_state["available"] is True
    return {
        "operation": "solve_assembly",
        "assembly": {"object_name": summary["object_name"]},
        "expected_solver_state_sha256": solver_state["state_sha256"],
        "expected_component_count": summary["counts"]["components"],
        "expected_grounded_count": summary["counts"]["grounded"],
        "expected_joint_count": summary["counts"]["joints"],
    }


def _assembly_summary(state: dict, assembly_name: str) -> dict:
    return next(
        item
        for item in state["domain"]["assemblies"]
        if item["object_name"] == assembly_name
    )


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    preferences = Preferences.preferences()
    prior_solve_preference = preferences.GetBool("SolveInJointCreation", True)
    prior_recompute_preference = preferences.GetBool("SolveOnRecompute", True)
    exit_code = 1
    try:
        preferences.SetBool("SolveInJointCreation", False)
        preferences.SetBool("SolveOnRecompute", False)
        Gui.activateWorkbench("AssemblyWorkbench")
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-assembly-solve-")
        path = Path(temporary.name) / "native-assembly-solve.FCStd"
        document = App.newDocument("NativeAssemblySolveGate")
        document.UndoMode = 1
        sources = []
        for index in range(2):
            source = document.addObject("Part::Box", f"SolveSource{index + 1}")
            source.Length = 12.0
            source.Width = 10.0
            source.Height = 8.0
            sources.append(source)
        document.recompute()
        document.saveAs(str(path))

        Gui.runCommand("Assembly_CreateAssembly")
        _process_events(24)
        assembly = next(
            obj for obj in document.Objects if obj.TypeId == "Assembly::AssemblyObject"
        )
        assert Gui.activeDocument().getInEdit() is assembly.ViewObject
        document.openTransaction("Prepare unsolved Assembly")
        components = []
        for index, source in enumerate(sources):
            component = assembly.newObject("App::Link", f"SolveComponent{index + 1}")
            component.LinkedObject = source
            component.Placement.Base.x = float(index * 40)
            UtilsAssembly.finalizeInsertedComponentTimeline(component)
            components.append(component)
        document.recompute()
        document.commitTransaction()
        document.clearUndos()
        _process_events(16)

        VibeGui._connect_document_observer()
        main_window = Gui.getMainWindow()
        controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
        assert controller is not None
        _select_assemble_ribbon(main_window)
        surface = read_active_ribbon_surface(controller)
        assert surface.surface_id == "assemble"
        assert "Assembly_SolveAssembly" in surface.command_ids
        frozen_surface = NativeSurfaceSnapshot.from_surface(surface)

        registry = build_native_capability_registry()
        production = resolve_native_provider_surface(surface, registry)
        assert (
            ASSEMBLY_STRUCTURE_CAPABILITY_NAME
            not in production.missing_definition_names
        )
        assert (
            ASSEMBLY_STRUCTURE_CAPABILITY_NAME
            not in production.missing_implementation_names
        )
        structure = registry.definition(ASSEMBLY_STRUCTURE_CAPABILITY_NAME)
        assert structure is not None
        solve_variant = next(
            variant
            for variant in structure.variants
            if variant.operation == "solve_assembly"
        )
        assert solve_variant.action_ids == frozenset({"Assembly_SolveAssembly"})
        assert registry.implementation(ASSEMBLY_STRUCTURE_CAPABILITY_NAME) is not None

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-assembly-solve-gui")

        def reauthorize() -> None:
            require_frozen_native_surface(frozen_surface, controller)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state,
            undo_ledger=ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(controller).surface_id,
            edit_or_task_active=lambda: bool(Gui.Control.activeDialog()),
        )
        turn = _focused_turn(surface, registry)
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )

        initial = dispatcher.call(
            "state.read",
            '{"operation":"active"}',
            "assembly-solve-state-unfixed",
        )
        assert initial["ok"] is True, initial
        initial_summary = _assembly_summary(initial, assembly.Name)
        assert initial_summary["counts"] == {
            "components": 2,
            "joints": 0,
            "grounded": 0,
        }
        assert initial_summary["solver_state"]["placement_object_count"] == 2
        before_free_objects = tuple(document.Objects)
        before_free_placements = tuple(component.Placement for component in components)
        free_result = dispatcher.call(
            ASSEMBLY_STRUCTURE_CAPABILITY_NAME,
            json.dumps(_solve_arguments(initial_summary), separators=(",", ":")),
            "assembly-solve-free-motion",
        )
        assert free_result["ok"] is True, free_result
        assert free_result["solver"]["solver_status"] == 0
        assert free_result["solver"]["remaining_degrees_of_freedom"] > 0
        assert free_result["moved_object_count"] == 0
        assert free_result["assistant_undo_available"] is False
        assert tuple(document.Objects) == before_free_objects
        assert all(
            component.Placement.isSame(before, 1.0e-9)
            for component, before in zip(components, before_free_placements)
        )
        assert int(document.UndoCount) == 0
        assert not document.HasPendingTransaction
        assert int(document.getBookedTransactionID()) == 0

        for property_name in ("Placement", "LinkPlacement"):
            if property_name in components[0].PropertiesList:
                components[0].setPropertyStatus(property_name, "ReadOnly")
        repair_state = dispatcher.call(
            "state.read",
            '{"operation":"active"}',
            "assembly-solve-state-grounding-repair",
        )
        assert repair_state["ok"] is True, repair_state
        repair_summary = _assembly_summary(repair_state, assembly.Name)
        assert repair_summary["counts"]["grounded"] == 0
        repair_result = dispatcher.call(
            ASSEMBLY_STRUCTURE_CAPABILITY_NAME,
            json.dumps(_solve_arguments(repair_summary), separators=(",", ":")),
            "assembly-solve-grounding-repair",
        )
        assert repair_result["ok"] is True, repair_result
        assert repair_result["moved_object_count"] == 0
        assert repair_result["grounded_count"] == 1, repair_result
        assert len(repair_result["grounding_repairs"]) == 1
        repair = repair_result["grounding_repairs"][0]
        assert repair["component"]["object_name"] == components[0].Name
        ground = document.getObject(repair["joint"]["object_name"])
        assert ground is not None and ground.ObjectToGround is components[0]
        assert repair_result["assistant_undo_available"] is True
        assert int(document.UndoCount) == 1

        document.openTransaction("Prepare constrained solve")
        joint = _joint_group(assembly).newObject("App::FeaturePython", "FixedJoint")
        JointObject.Joint(joint, 0)
        JointObject.ensureViewProviderJoint(joint)
        joint.Proxy.setJointConnectors(
            joint,
            [
                [components[0], ["Face6", "Face6"]],
                [components[1], ["Face6", "Face6"]],
            ],
        )
        components[1].Placement.Base.x += 55.0
        document.recompute()
        document.commitTransaction()
        _process_events(20)
        document.clearUndos()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(components[1])
        _process_events(8)
        misaligned = components[1].Placement

        current = dispatcher.call(
            "state.read",
            '{"operation":"active"}',
            "assembly-solve-state-constrained",
        )
        assert current["ok"] is True, current
        current_summary = _assembly_summary(current, assembly.Name)
        assert current_summary["counts"] == {
            "components": 2,
            "joints": 1,
            "grounded": 1,
        }
        stale_arguments = _solve_arguments(current_summary)
        stale_arguments["expected_solver_state_sha256"] = initial_summary[
            "solver_state"
        ]["state_sha256"]
        stale = dispatcher.call(
            ASSEMBLY_STRUCTURE_CAPABILITY_NAME,
            json.dumps(stale_arguments, separators=(",", ":")),
            "assembly-solve-stale-placement",
        )
        assert stale["ok"] is False, stale
        assert stale["error_code"] == "NATIVE_ASSEMBLY_SOLVE_FAILED"
        assert components[1].Placement.isSame(misaligned, 1.0e-9)
        assert int(document.UndoCount) == 0

        arguments = _solve_arguments(current_summary)
        encoded = json.dumps(arguments, separators=(",", ":"))
        result = dispatcher.call(
            ASSEMBLY_STRUCTURE_CAPABILITY_NAME,
            encoded,
            "assembly-solve-exact",
        )
        assert result["ok"] is True, result
        solved = components[1].Placement
        assert not solved.isSame(misaligned, 1.0e-9)
        assert components[0].Placement.isSame(
            before_free_placements[0],
            1.0e-9,
        )
        assert result["moved_object_count"] == 1
        assert len(result["placement_changes"]) == 1
        assert (
            result["placement_changes"][0]["object"]["object_name"]
            == components[1].Name
        )
        assert (
            result["placement_state_before_sha256"]
            == arguments["expected_solver_state_sha256"]
        )
        assert (
            result["placement_state_after_sha256"]
            != result["placement_state_before_sha256"]
        )
        assert result["grounded_placements_unchanged"] is True
        assert result["grounding_repairs"] == []
        assert result["solver"]["solver_status"] == 0
        assert result["active_assembly_unchanged"] is True
        assert result["selection_unchanged"] is True
        assert result["assistant_undo_available"] is True
        assert [item["object_name"] for item in result["receipt"]["changed"]] == [
            components[1].Name
        ]
        assert int(document.UndoCount) == 1
        assert Gui.Selection.getSelection() == [components[1]]
        assert Gui.activeDocument().getInEdit() is assembly.ViewObject
        assert not document.HasPendingTransaction
        assert int(document.getBookedTransactionID()) == 0

        replay = dispatcher.call(
            ASSEMBLY_STRUCTURE_CAPABILITY_NAME,
            encoded,
            "assembly-solve-exact",
        )
        assert replay == result
        assert int(document.UndoCount) == 1
        assert components[1].Placement.isSame(solved, 1.0e-9)

        assembly_name = assembly.Name
        first_name = components[0].Name
        second_name = components[1].Name
        joint_name = joint.Name
        ground_name = ground.Name
        document.undo()
        _process_events(20)
        assembly = document.getObject(assembly_name)
        first = document.getObject(first_name)
        second = document.getObject(second_name)
        assert second.Placement.isSame(misaligned, 1.0e-9)
        assert first.Placement.isSame(before_free_placements[0], 1.0e-9)
        assert document.getObject(joint_name) is joint
        assert document.getObject(ground_name) is ground

        document.redo()
        _process_events(20)
        assembly = document.getObject(assembly_name)
        first = document.getObject(first_name)
        second = document.getObject(second_name)
        assert second.Placement.isSame(solved, 1.0e-9)
        assert first.Placement.isSame(before_free_placements[0], 1.0e-9)
        assert assembly.isValid()

        Gui.Selection.clearSelection()
        Gui.activeDocument().resetEdit()
        _process_events(16)
        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(path))
        App.setActiveDocument(document.Name)
        _process_events(24)

        assembly = document.getObject(assembly_name)
        first = document.getObject(first_name)
        second = document.getObject(second_name)
        assert assembly is not None and assembly.isValid()
        assert first.Placement.isSame(before_free_placements[0], 1.0e-9)
        assert second.Placement.isSame(solved, 1.0e-9)
        assert document.getObject(joint_name) is not None
        assert document.getObject(ground_name) is not None
        reopened = build_assembly_snapshot(document)
        reopened_summary = next(
            item
            for item in reopened["assemblies"]
            if item["object_name"] == assembly_name
        )
        assert reopened_summary["counts"] == {
            "components": 2,
            "joints": 1,
            "grounded": 1,
        }
        assert (
            reopened_summary["solver_state"]["state_sha256"]
            == result["placement_state_after_sha256"]
        )

        print(
            "VIBECAD_NATIVE_ASSEMBLY_SOLVE_GUI_OK "
            "components=2 joints=1 grounded=1 moved=1 free_motion=true "
            "grounding_repair=true stale_noop=true selection=true transactions=2 "
            "undo_redo=true reopen=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        preferences.SetBool("SolveInJointCreation", prior_solve_preference)
        preferences.SetBool("SolveOnRecompute", prior_recompute_preference)
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
