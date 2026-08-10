# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for exact Native Assembly Ground/Unground."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import JointObject
import UtilsAssembly
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeAssemblyComponents import assembly_components
from VibeCADNativeAssemblyJointBindings import ASSEMBLY_JOINT_CAPABILITY_NAME
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
from VibeCADNativeSurface import (
    NativeSurfaceSnapshot,
    require_frozen_native_surface,
)
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
            joint_definition.provider_schema(("set_grounded",)),
        ),
        human_only_action_ids=("Assembly_ActivateAssembly",),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(provider_surface)


def _joint_group(assembly):
    groups = [
        child
        for child in list(assembly.Group)
        if child.TypeId == "Assembly::JointGroup"
    ]
    assert len(groups) == 1
    return groups[0]


def _grounded_joints(assembly):
    return [
        joint
        for joint in list(_joint_group(assembly).Group)
        if getattr(joint, "ObjectToGround", None) is not None
        and UtilsAssembly.isTimelineOperationActive(joint)
    ]


def _placement_locked(component) -> bool:
    properties = [
        name
        for name in ("Placement", "LinkPlacement")
        if name in component.PropertiesList
    ]
    assert properties
    return all(
        "ReadOnly" in tuple(component.getPropertyStatus(name))
        for name in properties
    )


def _ground_arguments(assembly, components, *, grounded, grounded_count):
    return {
        "operation": "set_grounded",
        "assembly": {"object_name": assembly.Name},
        "targets": [
            {
                "component": {"object_name": component.Name},
                "expected_grounded": not grounded,
            }
            for component in components
        ],
        "grounded": grounded,
        "expected_component_count": 2,
        "expected_grounded_count": grounded_count,
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("AssemblyWorkbench")
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-assembly-grounding-"
        )
        path = Path(temporary.name) / "native-assembly-grounding.FCStd"
        document = App.newDocument("NativeAssemblyGroundingGate")
        document.UndoMode = 1
        source_a = document.addObject("Part::Box", "GroundSourceA")
        source_b = document.addObject("Part::Cylinder", "GroundSourceB")
        document.recompute()
        document.saveAs(str(path))

        Gui.runCommand("Assembly_CreateAssembly")
        _process_events(24)
        assemblies = [
            obj
            for obj in document.Objects
            if obj.TypeId == "Assembly::AssemblyObject"
        ]
        assert len(assemblies) == 1
        assembly = assemblies[0]
        assert Gui.activeDocument().getInEdit() is assembly.ViewObject

        document.openTransaction("Prepare grounding fixture")
        component_a = assembly.newObject("App::Link", "GroundComponentA")
        component_a.LinkedObject = source_a
        component_a.Label = "Grounding component A"
        UtilsAssembly.finalizeInsertedComponentTimeline(component_a)
        component_b = assembly.newObject("App::Link", "GroundComponentB")
        component_b.LinkedObject = source_b
        component_b.Label = "Grounding component B"
        UtilsAssembly.finalizeInsertedComponentTimeline(component_b)
        document.recompute()
        document.commitTransaction()
        _process_events(16)
        assert len(assembly_components(assembly)) == 2
        document.clearUndos()
        Gui.Selection.clearSelection()

        VibeGui._connect_document_observer()
        main_window = Gui.getMainWindow()
        controller = main_window.findChild(
            QtCore.QObject,
            "VibeCADRibbonController",
        )
        assert controller is not None
        _select_assemble_ribbon(main_window)
        surface = read_active_ribbon_surface(controller)
        assert surface.surface_id == "assemble"
        assert "Assembly_ToggleGrounded" in surface.command_ids
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
        ledger.begin_run("native-assembly-grounding-gui")

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

        def native_call(name: str, arguments: dict, *, succeeds: bool = True) -> dict:
            nonlocal call_number
            call_number += 1
            result = dispatcher.call(
                name,
                json.dumps(arguments, separators=(",", ":")),
                f"assembly-grounding-call-{call_number}",
            )
            assert result.get("ok") is succeeds, result
            assert read_active_ribbon_surface(controller).surface_id == "assemble"
            assert Gui.activeDocument().getInEdit() is assembly.ViewObject
            assert not Gui.Selection.getSelection()
            return result

        initial = native_call("state.read", {"operation": "active"})
        active_summary = initial["domain"]["assemblies"][0]
        assert active_summary["counts"] == {
            "components": 2,
            "joints": 0,
            "grounded": 0,
        }
        assert all(
            component["grounded"] is False
            and component["grounded_joint"] is None
            for component in active_summary["components"]
        )
        assert int(document.UndoCount) == 0

        before_invalid = tuple(document.Objects)
        invalid = native_call(
            ASSEMBLY_JOINT_CAPABILITY_NAME,
            {
                **_ground_arguments(
                    assembly,
                    [component_a],
                    grounded=True,
                    grounded_count=1,
                ),
                "expected_component_count": 2,
            },
            succeeds=False,
        )
        assert invalid["error_code"] == "NATIVE_ASSEMBLY_GROUNDING_FAILED"
        assert tuple(document.Objects) == before_invalid
        assert int(document.UndoCount) == 0

        ground_arguments = _ground_arguments(
            assembly,
            [component_a, component_b],
            grounded=True,
            grounded_count=0,
        )
        ground = native_call(ASSEMBLY_JOINT_CAPABILITY_NAME, ground_arguments)
        ground_call_id = f"assembly-grounding-call-{call_number}"
        ground_joint_names = tuple(
            target["grounded_joint"]["object_name"]
            for target in ground["targets"]
        )
        assert ground["grounded"] is True
        assert ground["grounded_count"] == 2
        assert len(ground["receipt"]["created"]) == 2
        assert int(document.UndoCount) == 1
        assert len(_grounded_joints(assembly)) == 2
        assert all(_placement_locked(component) for component in (component_a, component_b))
        assert all(
            isinstance(document.getObject(name).ViewObject.Proxy, JointObject.ViewProviderGroundedJoint)
            for name in ground_joint_names
        )

        replay = dispatcher.call(
            ASSEMBLY_JOINT_CAPABILITY_NAME,
            json.dumps(ground_arguments, separators=(",", ":")),
            ground_call_id,
        )
        assert replay == ground
        assert int(document.UndoCount) == 1
        assert len(_grounded_joints(assembly)) == 2

        document.undo()
        _process_events(20)
        assembly = document.getObject(assembly.Name)
        component_a = document.getObject(component_a.Name)
        component_b = document.getObject(component_b.Name)
        assert not _grounded_joints(assembly)
        assert not _placement_locked(component_a)
        assert not _placement_locked(component_b)
        assert Gui.activeDocument().getInEdit() is assembly.ViewObject

        document.redo()
        _process_events(20)
        assembly = document.getObject(assembly.Name)
        component_a = document.getObject(component_a.Name)
        component_b = document.getObject(component_b.Name)
        assert len(_grounded_joints(assembly)) == 2
        assert _placement_locked(component_a)
        assert _placement_locked(component_b)
        assert Gui.activeDocument().getInEdit() is assembly.ViewObject

        unground = native_call(
            ASSEMBLY_JOINT_CAPABILITY_NAME,
            _ground_arguments(
                assembly,
                [component_a],
                grounded=False,
                grounded_count=2,
            ),
        )
        assert unground["grounded"] is False
        assert unground["grounded_count"] == 1
        assert unground["targets"][0]["grounded_joint"] is None
        assert len(unground["receipt"]["deleted"]) == 1
        assert int(document.UndoCount) == 2
        assert not _placement_locked(component_a)
        assert _placement_locked(component_b)

        document.undo()
        _process_events(20)
        assembly = document.getObject(assembly.Name)
        component_a = document.getObject(component_a.Name)
        component_b = document.getObject(component_b.Name)
        assert len(_grounded_joints(assembly)) == 2
        assert _placement_locked(component_a) and _placement_locked(component_b)

        document.redo()
        _process_events(20)
        assembly_name = assembly.Name
        component_a_name = component_a.Name
        component_b_name = component_b.Name
        component_a = document.getObject(component_a_name)
        component_b = document.getObject(component_b_name)
        assert len(_grounded_joints(assembly)) == 1
        assert not _placement_locked(component_a)
        assert _placement_locked(component_b)

        Gui.activeDocument().resetEdit()
        _process_events(16)
        document.save()
        assert path.is_file()
        document_name = document.Name
        App.closeDocument(document_name)
        document = App.openDocument(str(path))
        App.setActiveDocument(document.Name)
        _process_events(24)

        assembly = document.getObject(assembly_name)
        component_a = document.getObject(component_a_name)
        component_b = document.getObject(component_b_name)
        remaining = _grounded_joints(assembly)
        assert len(remaining) == 1
        assert remaining[0].ObjectToGround is component_b
        assert isinstance(
            remaining[0].ViewObject.Proxy,
            JointObject.ViewProviderGroundedJoint,
        )
        assert assembly.isPartGrounded(component_a) is False
        assert assembly.isPartGrounded(component_b) is True
        assert not _placement_locked(component_a)
        assert _placement_locked(component_b)
        reopened = build_assembly_snapshot(document)
        reopened_summary = next(
            item
            for item in reopened["assemblies"]
            if item["object_name"] == assembly_name
        )
        assert reopened_summary["counts"]["grounded"] == 1
        assert {
            item["object_name"]: item["grounded"]
            for item in reopened_summary["components"]
        } == {component_a_name: False, component_b_name: True}

        print(
            "VIBECAD_NATIVE_ASSEMBLY_GROUNDING_GUI_OK "
            "components=2 ground_batch=2 unground=1 transactions=2 reopen=true",
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
