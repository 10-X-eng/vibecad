# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for exact Native Assembly Fixed joints."""

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
from VibeCADNativeAssemblyJointBindings import ASSEMBLY_JOINT_CAPABILITY_NAME
from VibeCADNativeAssemblyJointConnectors import placement_summary
from VibeCADNativeAssemblyJointSchema import assembly_joint_capability_definition
from VibeCADNativeAssemblySnapshot import build_assembly_snapshot
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
    joint_definition = assembly_joint_capability_definition()
    provider_surface = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot.from_surface(surface),
        available=True,
        unavailable_reason="",
        tool_names=("state.read", ASSEMBLY_JOINT_CAPABILITY_NAME),
        schemas=(
            state_definition.provider_schema(("active", "selection")),
            joint_definition.provider_schema(("create_fixed",)),
        ),
        human_only_action_ids=("Assembly_ActivateAssembly",),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(provider_surface)


def _joint_group(assembly):
    groups = [child for child in assembly.Group if child.TypeId == "Assembly::JointGroup"]
    assert len(groups) == 1
    return groups[0]


def _regular_joints(assembly):
    return [
        joint
        for joint in _joint_group(assembly).Group
        if hasattr(joint, "JointType")
        and UtilsAssembly.isTimelineOperationActive(joint)
    ]


def _identity_placement() -> dict:
    return {
        "origin_mm": {"x": 0.0, "y": 0.0, "z": 0.0},
        "rotation": {
            "axis": {"x": 0.0, "y": 0.0, "z": 1.0},
            "angle_degrees": 0.0,
        },
    }


def _offset(z_mm: float, angle_degrees: float) -> dict:
    return {
        "origin_mm": {"x": 0.0, "y": 0.0, "z": z_mm},
        "rotation": {
            "axis": {"x": 0.0, "y": 0.0, "z": 1.0},
            "angle_degrees": angle_degrees,
        },
    }


def _connector(component, *, offset: dict) -> dict:
    return {
        "component": {"object_name": component.Name},
        "element_path": "Face6",
        "anchor_path": "Face6",
        "offset": offset,
        "expected_component_placement": placement_summary(component.Placement),
    }


def _arguments(
    assembly,
    first,
    second,
    *,
    label: str,
    reverse: bool,
    joint_count: int,
    second_offset: dict,
) -> dict:
    return {
        "operation": "create_fixed",
        "assembly": {"object_name": assembly.Name},
        "first": _connector(first, offset=_identity_placement()),
        "second": _connector(second, offset=second_offset),
        "label": label,
        "reverse": reverse,
        "expected_component_count": 3,
        "expected_grounded_count": 1,
        "expected_joint_count": joint_count,
        "expected_solve_on_creation": True,
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    preferences = Preferences.preferences()
    prior_solve_preference = preferences.GetBool("SolveInJointCreation", True)
    exit_code = 1
    try:
        preferences.SetBool("SolveInJointCreation", True)
        Gui.activateWorkbench("AssemblyWorkbench")
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-assembly-fixed-joint-"
        )
        path = Path(temporary.name) / "native-assembly-fixed-joint.FCStd"
        document = App.newDocument("NativeAssemblyFixedJointGate")
        document.UndoMode = 1
        sources = []
        for index in range(3):
            source = document.addObject("Part::Box", f"FixedSource{index + 1}")
            source.Length = 12.0
            source.Width = 10.0
            source.Height = 8.0
            sources.append(source)
        document.recompute()
        document.saveAs(str(path))

        Gui.runCommand("Assembly_CreateAssembly")
        _process_events(24)
        assembly = next(
            obj
            for obj in document.Objects
            if obj.TypeId == "Assembly::AssemblyObject"
        )
        assert Gui.activeDocument().getInEdit() is assembly.ViewObject

        document.openTransaction("Prepare Fixed-joint fixture")
        components = []
        for index, source in enumerate(sources):
            component = assembly.newObject("App::Link", f"FixedComponent{index + 1}")
            component.LinkedObject = source
            component.Placement.Base.x = float(index * 35)
            UtilsAssembly.finalizeInsertedComponentTimeline(component)
            components.append(component)
        ground = CommandCreateJoint.createGroundedJointFeature(components[0], assembly)
        JointObject.ensureViewProviderGroundedJoint(ground)
        document.recompute()
        document.commitTransaction()
        _process_events(16)
        document.clearUndos()
        Gui.Selection.clearSelection()

        VibeGui._connect_document_observer()
        main_window = Gui.getMainWindow()
        controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
        assert controller is not None
        _select_assemble_ribbon(main_window)
        surface = read_active_ribbon_surface(controller)
        assert surface.surface_id == "assemble"
        assert "Assembly_CreateJointFixed" in surface.command_ids
        frozen_surface = NativeSurfaceSnapshot.from_surface(surface)

        registry = build_native_capability_registry()
        production = resolve_native_provider_surface(surface, registry)
        assert production.available is False
        assert "assembly.joint" not in production.missing_definition_names
        assert "assembly.joint" not in production.missing_implementation_names
        assert "assembly.joint" not in production.incomplete_definition_names

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-assembly-fixed-joint-gui")

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
            edit_or_task_active=lambda: Gui.Control.activeDialog() is not None,
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
        call_number = 0

        def native_call(arguments: dict, *, succeeds: bool = True) -> dict:
            nonlocal call_number
            call_number += 1
            result = dispatcher.call(
                ASSEMBLY_JOINT_CAPABILITY_NAME,
                json.dumps(arguments, separators=(",", ":")),
                f"assembly-fixed-call-{call_number}",
            )
            assert result.get("ok") is succeeds, result
            assert read_active_ribbon_surface(controller).surface_id == "assemble"
            assert Gui.activeDocument().getInEdit() is assembly.ViewObject
            assert not Gui.Selection.getSelection()
            return result

        initial = dispatcher.call(
            "state.read",
            '{"operation":"active"}',
            "assembly-fixed-state-1",
        )
        assert initial["ok"] is True, initial
        active_summary = initial["domain"]["assemblies"][0]
        assert initial["domain"]["solve_on_joint_creation"] is True
        assert active_summary["counts"] == {
            "components": 3,
            "joints": 0,
            "grounded": 1,
        }
        assert all(component["placement"] for component in active_summary["components"])
        assert all(component["shape"]["faces"] == 6 for component in active_summary["components"])

        invalid_arguments = _arguments(
            assembly,
            components[0],
            components[1],
            label="Invalid stale Fixed",
            reverse=False,
            joint_count=1,
            second_offset=_identity_placement(),
        )
        before_invalid = tuple(document.Objects)
        invalid = native_call(invalid_arguments, succeeds=False)
        assert invalid["error_code"] == "NATIVE_ASSEMBLY_FIXED_JOINT_FAILED"
        assert tuple(document.Objects) == before_invalid
        assert int(document.UndoCount) == 0

        first_arguments = _arguments(
            assembly,
            components[0],
            components[1],
            label="Native Fixed A-B",
            reverse=False,
            joint_count=0,
            second_offset=_offset(2.0, 30.0),
        )
        first_result = native_call(first_arguments)
        first_call_id = f"assembly-fixed-call-{call_number}"
        first_joint_name = first_result["joint"]["object_name"]
        assert first_result["joint_type"] == "Fixed"
        assert first_result["joint_count"] == 1
        assert first_result["grounded_count"] == 1
        assert first_result["solver"]["solver_status"] == 0
        assert len(first_result["receipt"]["created"]) == 1
        assert int(document.UndoCount) == 1
        first_joint = document.getObject(first_joint_name)
        assert isinstance(first_joint.Proxy, JointObject.Joint)
        assert isinstance(first_joint.ViewObject.Proxy, JointObject.ViewProviderJoint)
        assert first_joint.JointType == "Fixed"

        replay = dispatcher.call(
            ASSEMBLY_JOINT_CAPABILITY_NAME,
            json.dumps(first_arguments, separators=(",", ":")),
            first_call_id,
        )
        assert replay == first_result
        assert int(document.UndoCount) == 1
        assert len(_regular_joints(assembly)) == 1

        document.undo()
        _process_events(20)
        assembly = document.getObject(assembly.Name)
        components = [document.getObject(component.Name) for component in components]
        assert document.getObject(first_joint_name) is None
        assert not _regular_joints(assembly)
        assert Gui.activeDocument().getInEdit() is assembly.ViewObject

        document.redo()
        _process_events(20)
        assembly = document.getObject(assembly.Name)
        components = [document.getObject(component.Name) for component in components]
        first_joint = document.getObject(first_joint_name)
        assert first_joint in _regular_joints(assembly)
        assert isinstance(first_joint.ViewObject.Proxy, JointObject.ViewProviderJoint)

        second_arguments = _arguments(
            assembly,
            components[0],
            components[2],
            label="Native Fixed A-C Reversed",
            reverse=True,
            joint_count=1,
            second_offset=_offset(-3.0, -20.0),
        )
        second_result = native_call(second_arguments)
        second_joint_name = second_result["joint"]["object_name"]
        assert second_result["reverse"] is True
        assert second_result["joint_count"] == 2
        assert second_result["solver"]["solver_status"] == 0
        assert int(document.UndoCount) == 2

        document.undo()
        _process_events(20)
        assembly = document.getObject(assembly.Name)
        components = [document.getObject(component.Name) for component in components]
        assert document.getObject(second_joint_name) is None
        assert len(_regular_joints(assembly)) == 1

        document.redo()
        _process_events(20)
        assembly_name = assembly.Name
        component_names = [component.Name for component in components]
        assembly = document.getObject(assembly_name)
        components = [document.getObject(name) for name in component_names]
        assert len(_regular_joints(assembly)) == 2

        Gui.activeDocument().resetEdit()
        _process_events(16)
        document.save()
        document_name = document.Name
        App.closeDocument(document_name)
        document = App.openDocument(str(path))
        App.setActiveDocument(document.Name)
        _process_events(24)

        assembly = document.getObject(assembly_name)
        reopened_joints = _regular_joints(assembly)
        assert {joint.Name for joint in reopened_joints} == {
            first_joint_name,
            second_joint_name,
        }
        for joint in reopened_joints:
            assert joint.JointType == "Fixed"
            assert isinstance(joint.Proxy, JointObject.Joint)
            assert isinstance(joint.ViewObject.Proxy, JointObject.ViewProviderJoint)
            assert UtilsAssembly.isTimelineOperationActive(joint)
            assert joint.Reference1[0].Name in component_names
            assert joint.Reference2[0].Name in component_names
        reopened = build_assembly_snapshot(document)
        reopened_summary = next(
            item
            for item in reopened["assemblies"]
            if item["object_name"] == assembly_name
        )
        assert reopened_summary["counts"] == {
            "components": 3,
            "joints": 2,
            "grounded": 1,
        }
        assert {item["joint_type"] for item in reopened_summary["joints"]} == {
            "Fixed"
        }

        print(
            "VIBECAD_NATIVE_ASSEMBLY_FIXED_JOINT_GUI_OK "
            "components=3 joints=2 reverse=true transactions=2 reopen=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        preferences.SetBool("SolveInJointCreation", prior_solve_preference)
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
