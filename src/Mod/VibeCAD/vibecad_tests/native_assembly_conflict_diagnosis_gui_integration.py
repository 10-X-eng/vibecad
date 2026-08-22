# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI gate for exact Native Assembly conflict diagnosis."""

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
import Preferences
import UtilsAssembly
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeAssemblyDiagnosisBindings import (
    ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME,
)
from VibeCADNativeAssemblyDiagnosisSchema import (
    assembly_diagnosis_capability_definition,
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
    diagnosis = assembly_diagnosis_capability_definition()
    provider_surface = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot.from_surface(surface),
        available=True,
        unavailable_reason="",
        tool_names=("state.read", ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME),
        schemas=(
            state_definition.provider_schema(("active", "selection")),
            diagnosis.provider_schema(("select_conflicting_constraints",)),
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


def _create_distance_joint(group, components, first, second, distance, name):
    joint = group.newObject("App::FeaturePython", name)
    JointObject.Joint(joint, 5)
    JointObject.ensureViewProviderJoint(joint)
    joint.Distance = float(distance)
    joint.Proxy.setJointConnectors(
        joint,
        [
            [components[first], ["Vertex1", "Vertex1"]],
            [components[second], ["Vertex1", "Vertex1"]],
        ],
    )
    return joint


def _assembly_summary(state: dict, assembly_name: str) -> dict:
    return next(
        item
        for item in state["domain"]["assemblies"]
        if item["object_name"] == assembly_name
    )


def _arguments(assembly_name: str, *, offset: int, limit: int) -> dict:
    return {
        "operation": "select_conflicting_constraints",
        "assembly": {"object_name": assembly_name},
        "offset": offset,
        "limit": limit,
    }


def _dispatcher(document, surface, registry, controller, run_id):
    frozen_surface = NativeSurfaceSnapshot.from_surface(surface)
    service = get_service()
    service.select_modeling_engine("native")
    state = service.native_document_state_store()
    ledger = NativeAssistantUndoLedger()
    ledger.begin_run(run_id)

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
    return NativeTurnDispatcher(
        document=document,
        state=state,
        registry=registry,
        turn=turn,
        runtimes=build_native_runtime_bindings(context, turn.tool_names),
        reauthorize_turn=reauthorize,
        active_document=lambda: App.ActiveDocument,
    )


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    preferences = Preferences.preferences()
    prior_solve_creation = preferences.GetBool("SolveInJointCreation", True)
    prior_solve_recompute = preferences.GetBool("SolveOnRecompute", True)
    exit_code = 1
    try:
        preferences.SetBool("SolveInJointCreation", False)
        preferences.SetBool("SolveOnRecompute", False)
        Gui.activateWorkbench("AssemblyWorkbench")
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-assembly-conflict-diagnosis-"
        )
        path = Path(temporary.name) / "native-assembly-conflicts.FCStd"
        document = App.newDocument("NativeAssemblyConflictDiagnosisGate")
        document.UndoMode = 1
        sources = []
        for index in range(3):
            source = document.addObject("Part::Box", f"ConflictSource{index}")
            source.Length = 10.0
            source.Width = 10.0
            source.Height = 10.0
            sources.append(source)
        document.recompute()
        document.saveAs(str(path))

        Gui.runCommand("Assembly_CreateAssembly")
        _process_events(24)
        assembly = next(
            obj for obj in document.Objects if obj.TypeId == "Assembly::AssemblyObject"
        )
        assert Gui.activeDocument().getInEdit() is assembly.ViewObject
        document.openTransaction("Prepare conflicting Assembly")
        components = []
        for index, (source, position) in enumerate(
            zip(
                sources,
                (App.Vector(0, 0, 0), App.Vector(3, 0, 0), App.Vector(0, 4, 0)),
            )
        ):
            component = assembly.newObject("App::Link", f"ConflictComponent{index}")
            component.LinkedObject = source
            component.Placement.Base = position
            UtilsAssembly.finalizeInsertedComponentTimeline(component)
            components.append(component)
        ground = CommandCreateJoint.createGroundedJointFeature(components[0], assembly)
        JointObject.ensureViewProviderGroundedJoint(ground)
        group = _joint_group(assembly)
        joints = (
            _create_distance_joint(group, components, 0, 1, 3.0, "DistanceAB"),
            _create_distance_joint(group, components, 1, 2, 7.1, "DistanceBC"),
            _create_distance_joint(group, components, 0, 2, 4.0, "DistanceAC"),
        )
        document.recompute()
        document.commitTransaction()
        _process_events(16)
        solver_code = int(assembly.solve(False))
        document.recompute()
        diagnostics = assembly.getSolverDiagnostics()
        expected_conflicts = list(diagnostics["conflicting_joints"])
        assert solver_code == -1
        assert diagnostics["has_conflicts"] is True
        assert set(expected_conflicts) == {joint.Name for joint in joints}
        assert all(
            float(item["maximum_absolute_residual"])
            > float(diagnostics["residual_tolerance"])
            for item in diagnostics["joints"]
            if item["joint"] in expected_conflicts
        )
        document.clearUndos()

        Gui.Selection.clearSelection()
        Gui.runCommand("Assembly_SelectConflictingConstraints")
        _process_events(12)
        human_selected = [obj.Name for obj in Gui.Selection.getSelection()]
        assert human_selected == expected_conflicts
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(components[2])
        _process_events(8)

        VibeGui._connect_document_observer()
        main_window = Gui.getMainWindow()
        controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
        assert controller is not None
        _select_assemble_ribbon(main_window)
        surface = read_active_ribbon_surface(controller)
        assert surface.surface_id == "assemble"
        assert "Assembly_SelectConflictingConstraints" in surface.command_ids

        registry = build_native_capability_registry()
        production = resolve_native_provider_surface(surface, registry)
        assert (
            ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME
            not in production.missing_definition_names
        )
        assert (
            ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME
            not in production.missing_implementation_names
        )
        assert (
            ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME not in production.incomplete_definition_names
        )
        definition = registry.definition(ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME)
        assert definition is not None
        variant = definition.variants[0]
        assert variant.action_ids == frozenset(
            {"Assembly_SelectConflictingConstraints"}
        )
        assert registry.implementation(ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME) is not None

        dispatcher = _dispatcher(
            document,
            surface,
            registry,
            controller,
            "native-assembly-conflict-diagnosis-gui",
        )
        current = dispatcher.call(
            "state.read",
            '{"operation":"active"}',
            "assembly-conflict-state",
        )
        assert current["ok"] is True, current
        summary = _assembly_summary(current, assembly.Name)
        assert summary["counts"] == {
            "components": 3,
            "joints": 3,
            "grounded": 1,
        }
        assert summary["solver"]["conflicts"]["conflicting"] == 3

        before_objects = tuple(document.Objects)
        before_placements = tuple(component.Placement for component in components)
        before_selection = tuple(Gui.Selection.getSelection())
        stale_arguments = _arguments(assembly.Name, offset=0, limit=2)
        stale_arguments["expected_diagnosis_state_sha256"] = "0" * 64
        stale = dispatcher.call(
            ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME,
            json.dumps(stale_arguments, separators=(",", ":")),
            "assembly-conflict-stale",
        )
        assert stale["ok"] is False, stale
        assert stale["error_code"] == "NATIVE_ARGUMENTS_INVALID"

        first_arguments = _arguments(assembly.Name, offset=0, limit=2)
        first_encoded = json.dumps(first_arguments, separators=(",", ":"))
        first = dispatcher.call(
            ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME,
            first_encoded,
            "assembly-conflict-first-page",
        )
        assert first["ok"] is True, first
        second = dispatcher.call(
            ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME,
            json.dumps(
                _arguments(assembly.Name, offset=2, limit=2),
                separators=(",", ":"),
            ),
            "assembly-conflict-second-page",
        )
        assert second["ok"] is True, second
        returned_names = [
            item["joint"]["object_name"]
            for result in (first, second)
            for item in result["conflicting_joints"]
        ]
        assert returned_names == expected_conflicts
        assert first["returned_count"] == 2
        assert first["next_offset"] == 2
        assert second["returned_count"] == 1
        assert "next_offset" not in second
        assert all(
            item["maximum_absolute_residual"] > first["residual_tolerance"]
            and item["violating_constraint_count"] >= 1
            and item["first"]["component"]["object_name"]
            and item["second"]["component"]["object_name"]
            for result in (first, second)
            for item in result["conflicting_joints"]
        )
        replay = dispatcher.call(
            ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME,
            first_encoded,
            "assembly-conflict-first-page",
        )
        assert replay == first
        assert tuple(document.Objects) == before_objects
        assert all(
            component.Placement.isSame(before, 1.0e-9)
            for component, before in zip(components, before_placements)
        )
        assert tuple(Gui.Selection.getSelection()) == before_selection
        assert int(document.UndoCount) == 0
        assert not document.HasPendingTransaction
        assert int(document.getBookedTransactionID()) == 0
        assert Gui.activeDocument().getInEdit() is assembly.ViewObject

        assembly_name = assembly.Name
        component_names = tuple(component.Name for component in components)
        joint_names = tuple(joint.Name for joint in joints)
        Gui.Selection.clearSelection()
        Gui.activeDocument().resetEdit()
        _process_events(16)
        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(path))
        App.setActiveDocument(document.Name)
        _process_events(24)
        assembly = document.getObject(assembly_name)
        components = tuple(document.getObject(name) for name in component_names)
        assert all(component is not None for component in components)
        assert all(document.getObject(name) is not None for name in joint_names)
        assert Gui.activeDocument().setEdit(assembly.Name)
        _process_events(20)
        assert Gui.activeDocument().getInEdit() is assembly.ViewObject
        reopened_code = int(assembly.solve(False))
        document.recompute()
        reopened_diagnostics = assembly.getSolverDiagnostics()
        assert reopened_code == -1
        assert reopened_diagnostics["conflicting_joints"] == expected_conflicts
        document.clearUndos()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(components[1])
        _process_events(8)

        _select_assemble_ribbon(main_window)
        reopened_surface = read_active_ribbon_surface(controller)
        reopened_dispatcher = _dispatcher(
            document,
            reopened_surface,
            registry,
            controller,
            "native-assembly-conflict-diagnosis-reopen",
        )
        reopened_state = reopened_dispatcher.call(
            "state.read",
            '{"operation":"active"}',
            "assembly-conflict-reopened-state",
        )
        assert reopened_state["ok"] is True, reopened_state
        reopened_result = reopened_dispatcher.call(
            ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME,
            json.dumps(
                _arguments(assembly.Name, offset=0, limit=32),
                separators=(",", ":"),
            ),
            "assembly-conflict-reopened-read",
        )
        assert reopened_result["ok"] is True, reopened_result
        assert [
            item["joint"]["object_name"]
            for item in reopened_result["conflicting_joints"]
        ] == expected_conflicts
        assert Gui.Selection.getSelection() == [components[1]]
        assert int(document.UndoCount) == 0

        print(
            "VIBECAD_NATIVE_ASSEMBLY_CONFLICT_DIAGNOSIS_GUI_OK "
            "components=3 joints=3 conflicts=3 solver_status=-1 human_match=true "
            "pagination=true removed_state_field=true selection=true "
            "transactions=0 reopen=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        preferences.SetBool("SolveInJointCreation", prior_solve_creation)
        preferences.SetBool("SolveOnRecompute", prior_solve_recompute)
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
