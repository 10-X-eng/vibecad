# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for Native Assembly structure operations."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

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
    assembly_definition = assembly_structure_capability_definition()
    provider_surface = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot.from_surface(surface),
        available=True,
        unavailable_reason="",
        tool_names=("state.read", ASSEMBLY_STRUCTURE_CAPABILITY_NAME),
        schemas=(
            state_definition.provider_schema(("active", "selection")),
            assembly_definition.provider_schema(
                ("create_assembly", "insert_component", "create_part")
            ),
        ),
        human_only_action_ids=("Assembly_ActivateAssembly",),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(provider_surface)


def _joints_group(assembly):
    groups = [
        child
        for child in list(assembly.Group)
        if child.TypeId == "Assembly::JointGroup"
    ]
    assert len(groups) == 1
    return groups[0]


def _placement(x: float, y: float, z: float, angle: float = 0.0) -> dict:
    return {
        "origin_mm": {"x": x, "y": y, "z": z},
        "rotation": {
            "axis": {"x": 0.0, "y": 0.0, "z": 1.0},
            "angle_degrees": angle,
        },
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    source_document = None
    temp_directory = None
    exit_code = 1
    preferences = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Assembly")
    prior_one_root_present = "EnforceOneAssemblyRule" in tuple(
        preferences.GetBools()
    )
    prior_one_root = preferences.GetBool("EnforceOneAssemblyRule", True)
    try:
        preferences.SetBool("EnforceOneAssemblyRule", True)
        Gui.activateWorkbench("PartDesignWorkbench")
        temp_directory = tempfile.TemporaryDirectory(
            prefix="vibecad-native-assembly-structure-"
        )
        target_path = Path(temp_directory.name) / "assembly-structure.FCStd"
        source_path = Path(temp_directory.name) / "assembly-source.FCStd"
        document = App.newDocument("NativeAssemblyStructureGate")
        document.UndoMode = 1
        source_box = document.addObject("Part::Box", "SourceBox")
        source_box.Label = "Local source box"
        document.recompute()
        document.saveAs(str(target_path))

        source_document = App.newDocument("NativeAssemblySourceGate")
        source_assembly = source_document.addObject(
            "Assembly::AssemblyObject",
            "SourceAssembly",
        )
        source_assembly.Type = "Assembly"
        source_assembly.Label = "External source assembly"
        source_assembly.newObject("Assembly::JointGroup", "Joints")
        source_component_definition = source_document.addObject(
            "Part::Box",
            "SourceComponentDefinition",
        )
        source_document.openTransaction("Create source Assembly component")
        source_component = source_assembly.newObject(
            "App::Link",
            "SourceComponentOccurrence",
        )
        source_component.LinkedObject = source_component_definition
        source_component.Label = "Source component occurrence"
        import UtilsAssembly

        UtilsAssembly.finalizeInsertedComponentTimeline(source_component)
        source_document.commitTransaction()
        source_document.recompute()
        source_document.saveAs(str(source_path))
        App.setActiveDocument(document.Name)
        VibeGui._connect_document_observer()
        _process_events()

        main_window = Gui.getMainWindow()
        controller = main_window.findChild(
            QtCore.QObject,
            "VibeCADRibbonController",
        )
        assert controller is not None
        _select_assemble_ribbon(main_window)
        surface = read_active_ribbon_surface(controller)
        assert surface.surface_id == "assemble"
        assert "Assembly_CreateAssembly" in surface.command_ids
        frozen_surface = NativeSurfaceSnapshot.from_surface(surface)

        registry = build_native_capability_registry()
        production = resolve_native_provider_surface(surface, registry)
        assert production.available is False
        assert "assembly.structure" in production.incomplete_definition_names

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-assembly-structure-gui")

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
                f"assembly-structure-call-{call_number}",
            )
            assert result.get("ok") is succeeds, result
            assert read_active_ribbon_surface(controller).surface_id == "assemble"
            return result

        initial_state = native_call("state.read", {"operation": "active"})
        assert initial_state["domain"]["active_assembly"] is None
        assert initial_state["domain"]["assembly_count"] == 0
        document.clearUndos()

        before_invalid = tuple(document.Objects)
        native_call(
            ASSEMBLY_STRUCTURE_CAPABILITY_NAME,
            {
                "operation": "create_assembly",
                "label": "Missing expected state",
                "parent_assembly": None,
            },
            succeeds=False,
        )
        assert tuple(document.Objects) == before_invalid
        assert int(document.UndoCount) == 0

        stale = native_call(
            ASSEMBLY_STRUCTURE_CAPABILITY_NAME,
            {
                "operation": "create_assembly",
                "label": "Stale root",
                "parent_assembly": None,
                "expected_assembly_count": 1,
            },
            succeeds=False,
        )
        assert stale["error_code"] == "NATIVE_ASSEMBLY_STRUCTURE_FAILED"
        assert tuple(document.Objects) == before_invalid
        assert int(document.UndoCount) == 0

        root_result = native_call(
            ASSEMBLY_STRUCTURE_CAPABILITY_NAME,
            {
                "operation": "create_assembly",
                "label": "Native Root Assembly",
                "parent_assembly": None,
                "expected_assembly_count": 0,
            },
        )
        root_name = root_result["assembly"]["object_name"]
        root_joints_name = root_result["joint_group"]["object_name"]
        assert root_result["nested"] is False
        assert root_result["active_assembly_unchanged"] is True
        assert int(document.UndoCount) == 1
        assert Gui.activeDocument().getInEdit() is None
        root = document.getObject(root_name)
        assert root.Label == "Native Root Assembly"
        assert _joints_group(root).Name == root_joints_name

        after_root = native_call("state.read", {"operation": "active"})
        assert after_root["domain"]["active_assembly"] is None
        assert after_root["domain"]["assembly_count"] == 1

        document.undo()
        _process_events()
        assert document.getObject(root_name) is None
        assert document.getObject(root_joints_name) is None
        document.redo()
        _process_events()
        root = document.getObject(root_name)
        assert root is not None
        assert _joints_group(root).Name == root_joints_name
        assert Gui.activeDocument().getInEdit() is None

        assert Gui.activeDocument().setEdit(root.Name)
        _process_events(24)
        assert Gui.activeDocument().getInEdit() is root.ViewObject
        active_state = native_call("state.read", {"operation": "active"})
        assert active_state["domain"]["active_assembly"]["object_name"] == root_name
        source_inventory = active_state["domain"]["available_component_sources"]
        local_source = next(
            item for item in source_inventory if item["object_name"] == source_box.Name
        )
        external_source = next(
            item
            for item in source_inventory
            if item["document_uid"] == source_document.Uid
            and item["object_name"] == source_assembly.Name
        )
        assert local_source["subassembly"] is False
        assert external_source["subassembly"] is True

        nested_result = native_call(
            ASSEMBLY_STRUCTURE_CAPABILITY_NAME,
            {
                "operation": "create_assembly",
                "label": "Native Nested Assembly",
                "parent_assembly": {"object_name": root_name},
                "expected_assembly_count": 1,
            },
        )
        nested_call_id = f"assembly-structure-call-{call_number}"
        nested_name = nested_result["assembly"]["object_name"]
        nested_joints_name = nested_result["joint_group"]["object_name"]
        assert nested_result["nested"] is True
        assert nested_result["parent_assembly"]["object_name"] == root_name
        assert nested_result["active_assembly_unchanged"] is True
        assert Gui.activeDocument().getInEdit() is root.ViewObject
        nested = document.getObject(nested_name)
        assert nested in list(root.Group)
        assert _joints_group(nested).Name == nested_joints_name
        assert int(document.UndoCount) == 2

        replay = dispatcher.call(
            ASSEMBLY_STRUCTURE_CAPABILITY_NAME,
            json.dumps(
                {
                    "operation": "create_assembly",
                    "label": "Native Nested Assembly",
                    "parent_assembly": {"object_name": root_name},
                    "expected_assembly_count": 1,
                },
                separators=(",", ":"),
            ),
            nested_call_id,
        )
        assert replay == nested_result
        assert len(build_assembly_snapshot(document)["assemblies"]) == 2
        assert int(document.UndoCount) == 2

        document.undo()
        _process_events()
        assert document.getObject(nested_name) is None
        assert document.getObject(nested_joints_name) is None
        assert Gui.activeDocument().getInEdit() is root.ViewObject
        document.redo()
        _process_events()
        root = document.getObject(root_name)
        nested = document.getObject(nested_name)
        assert nested is not None and nested in list(root.Group)
        assert _joints_group(nested).Name == nested_joints_name

        local_insert = native_call(
            ASSEMBLY_STRUCTURE_CAPABILITY_NAME,
            {
                "operation": "insert_component",
                "assembly": {"object_name": root_name},
                "source": {
                    key: local_source[key]
                    for key in (
                        "document_uid",
                        "document_name",
                        "object_name",
                        "object_id",
                    )
                },
                "label": "Placed local box",
                "placement": _placement(10.0, 20.0, 30.0, 15.0),
                "rigid": None,
                "expected_component_count": 1,
            },
        )
        local_occurrence_name = local_insert["occurrence"]["object_name"]
        local_occurrence = document.getObject(local_occurrence_name)
        assert local_occurrence.TypeId == "App::Link"
        assert local_occurrence.LinkedObject is source_box
        assert local_occurrence.Label == "Placed local box"
        assert local_insert["component_count"] == 2
        assert local_insert["grounded"] is False
        assert Gui.activeDocument().getInEdit() is root.ViewObject
        assert int(document.UndoCount) == 3

        document.undo()
        _process_events()
        assert document.getObject(local_occurrence_name) is None
        document.redo()
        _process_events()
        root = document.getObject(root_name)
        local_occurrence = document.getObject(local_occurrence_name)
        assert local_occurrence is not None and local_occurrence.LinkedObject is source_box
        assert Gui.activeDocument().getInEdit() is root.ViewObject

        subassembly_insert = native_call(
            ASSEMBLY_STRUCTURE_CAPABILITY_NAME,
            {
                "operation": "insert_component",
                "assembly": {"object_name": root_name},
                "source": {
                    key: external_source[key]
                    for key in (
                        "document_uid",
                        "document_name",
                        "object_name",
                        "object_id",
                    )
                },
                "label": "Flexible external module",
                "placement": _placement(-25.0, 5.0, 0.0, -30.0),
                "rigid": False,
                "expected_component_count": 2,
            },
        )
        subassembly_occurrence_name = subassembly_insert["occurrence"]["object_name"]
        subassembly_occurrence = document.getObject(subassembly_occurrence_name)
        assert subassembly_occurrence.TypeId == "Assembly::AssemblyLink"
        assert subassembly_occurrence.LinkedObject is source_assembly
        assert subassembly_occurrence.Rigid is False
        assert subassembly_insert["component_count"] == 3
        assert len(subassembly_insert["receipt"]["created"]) >= 2
        assert all(
            document.getObject(item["object_name"]) is not None
            for item in subassembly_insert["receipt"]["created"]
        )
        assert Gui.activeDocument().getInEdit() is root.ViewObject
        assert int(document.UndoCount) == 4

        document.undo()
        _process_events()
        assert document.getObject(subassembly_occurrence_name) is None
        document.redo()
        _process_events()
        root = document.getObject(root_name)
        subassembly_occurrence = document.getObject(subassembly_occurrence_name)
        assert subassembly_occurrence is not None
        assert subassembly_occurrence.LinkedObject is source_assembly
        assert subassembly_occurrence.Rigid is False

        prior_body = Gui.activeDocument().activeView().getActiveObject("pdbody")
        new_part_arguments = {
            "operation": "create_part",
            "assembly": {"object_name": root_name},
            "label": "Native drive bracket",
            "placement": _placement(2.0, 4.0, 6.0, 45.0),
            "expected_component_count": 3,
        }
        new_part_result = native_call(
            ASSEMBLY_STRUCTURE_CAPABILITY_NAME,
            new_part_arguments,
        )
        new_part_call_id = f"assembly-structure-call-{call_number}"
        part_name = new_part_result["part"]["object_name"]
        body_name = new_part_result["body"]["object_name"]
        part_occurrence_name = new_part_result["occurrence"]["object_name"]
        part = document.getObject(part_name)
        body = document.getObject(body_name)
        part_occurrence = document.getObject(part_occurrence_name)
        assert part.TypeId == "App::Part" and part.Label == "Native drive bracket"
        assert body.TypeId == "PartDesign::Body" and body in list(part.Group)
        assert part_occurrence.LinkedObject is part
        assert part.VibeCADTimelineRole == "operation"
        assert body.VibeCADTimelineRole == "resource"
        assert body.VibeCADTimelineOwner is part
        assert part_occurrence.VibeCADTimelineRole == "resource"
        assert part_occurrence.VibeCADTimelineOwner is part
        assert Gui.activeDocument().activeView().getActiveObject("pdbody") is prior_body
        assert Gui.activeDocument().getInEdit() is root.ViewObject
        assert new_part_result["component_count"] == 4
        assert int(document.UndoCount) == 5

        replay = dispatcher.call(
            ASSEMBLY_STRUCTURE_CAPABILITY_NAME,
            json.dumps(new_part_arguments, separators=(",", ":")),
            new_part_call_id,
        )
        assert replay == new_part_result
        assert int(document.UndoCount) == 5

        document.undo()
        _process_events()
        assert document.getObject(part_name) is None
        assert document.getObject(body_name) is None
        assert document.getObject(part_occurrence_name) is None
        document.redo()
        _process_events()
        root = document.getObject(root_name)
        part = document.getObject(part_name)
        body = document.getObject(body_name)
        part_occurrence = document.getObject(part_occurrence_name)
        assert part is not None and body in list(part.Group)
        assert part_occurrence.LinkedObject is part
        assert Gui.activeDocument().getInEdit() is root.ViewObject

        Gui.activeDocument().resetEdit()
        _process_events(16)
        assert Gui.activeDocument().getInEdit() is None
        document.save()
        source_document.save()
        assert target_path.is_file() and source_path.is_file()
        document_name = document.Name
        App.closeDocument(document_name)
        source_document_name = source_document.Name
        App.closeDocument(source_document_name)
        source_document = App.openDocument(str(source_path))
        document = App.openDocument(str(target_path))
        App.setActiveDocument(document.Name)
        _process_events(24)

        root = document.getObject(root_name)
        nested = document.getObject(nested_name)
        assert root is not None and root.TypeId == "Assembly::AssemblyObject"
        assert nested is not None and nested.TypeId == "Assembly::AssemblyObject"
        assert nested in list(root.Group)
        assert _joints_group(root).Name == root_joints_name
        assert _joints_group(nested).Name == nested_joints_name
        source_box = document.getObject(local_source["object_name"])
        source_assembly = source_document.getObject(external_source["object_name"])
        local_occurrence = document.getObject(local_occurrence_name)
        subassembly_occurrence = document.getObject(subassembly_occurrence_name)
        part = document.getObject(part_name)
        body = document.getObject(body_name)
        part_occurrence = document.getObject(part_occurrence_name)
        assert local_occurrence.LinkedObject is source_box
        assert subassembly_occurrence.LinkedObject is source_assembly
        assert subassembly_occurrence.Rigid is False
        assert body in list(part.Group)
        assert part_occurrence.LinkedObject is part
        assert part_occurrence.VibeCADTimelineOwner is part
        reopened_state = build_assembly_snapshot(document)
        assert reopened_state["assembly_count"] == 2
        assert reopened_state["active_assembly"] is None

        print(
            "VIBECAD_NATIVE_ASSEMBLY_STRUCTURE_GUI_OK "
            "assemblies=2 components=4 transactions=5 active_read=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        if prior_one_root_present:
            preferences.SetBool("EnforceOneAssemblyRule", prior_one_root)
        else:
            preferences.RemBool("EnforceOneAssemblyRule")
        if document is not None:
            try:
                Gui.activeDocument().resetEdit()
            except (AttributeError, RuntimeError):
                pass
            App.closeDocument(document.Name)
        if source_document is not None:
            try:
                App.closeDocument(source_document.Name)
            except (NameError, RuntimeError):
                pass
        if temp_directory is not None:
            temp_directory.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(0, _run)
