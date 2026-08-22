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
from VibeCADNativeAssemblyJointBindings import (
    ASSEMBLY_GROUND_CAPABILITY_NAME,
    ASSEMBLY_JOINT_CAPABILITY_NAME,
)
from VibeCADNativeAssemblyGrounding import active_grounded_joints
from VibeCADNativeAssemblyInspectSchema import (
    ASSEMBLY_CONNECTORS_CAPABILITY_NAME,
)
from VibeCADNativeAssemblyStructureBindings import (
    ASSEMBLY_CREATE_CAPABILITY_NAME,
    ASSEMBLY_INSERT_CAPABILITY_NAME,
    ASSEMBLY_NEW_PART_CAPABILITY_NAME,
    ASSEMBLY_RIGIDITY_CAPABILITY_NAME,
)
from VibeCADNativeCapabilityRegistry import (
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


def _active_summary(native_call, assembly_name: str) -> dict:
    state = native_call("state.read", {"operation": "active"})["domain"]
    summary = next(
        item
        for item in state["assemblies"]
        if item["object_name"] == assembly_name and item["active"] is True
    )
    assert "last_solver" not in summary
    assert summary["object_id"] >= 1
    assert summary["diagnosis_state"]["available"] is True
    health = summary["solver_health"]
    assert set(health["conflict_counts"]) == {
        "conflicting",
        "redundant",
        "partially_redundant",
        "malformed",
    }
    assert "remaining_degrees_of_freedom" in health
    assert "maximum_absolute_residual" in health
    assert all(component["object_id"] >= 1 for component in summary["components"])
    assert all(joint["object_id"] >= 1 for joint in summary["joints"])
    return summary


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
        source_box.ViewObject.Visibility = False
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
        assert production.available is True, production.debug_summary()
        assert not production.missing_definition_names
        assert not production.missing_implementation_names
        assert not production.incomplete_definition_names
        assert ASSEMBLY_CREATE_CAPABILITY_NAME in production.tool_names
        assert ASSEMBLY_INSERT_CAPABILITY_NAME in production.tool_names
        assert ASSEMBLY_NEW_PART_CAPABILITY_NAME in production.tool_names
        assert ASSEMBLY_RIGIDITY_CAPABILITY_NAME in production.tool_names
        assert ASSEMBLY_JOINT_CAPABILITY_NAME in production.tool_names
        assert ASSEMBLY_CONNECTORS_CAPABILITY_NAME in production.tool_names
        assert "component.interface" in production.tool_names
        assert "AssemblyContextToggleActive" in production.human_only_action_ids
        assert "AssemblyContextMakeFlexible" not in production.human_only_action_ids
        assert "AssemblyContextMakeRigid" not in production.human_only_action_ids
        schema_bytes = len(
            json.dumps(
                list(production.schemas),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )

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
            edit_or_task_active=lambda: bool(Gui.Control.activeDialog()),
        )
        def new_dispatcher() -> NativeTurnDispatcher:
            current_turn = NativeTurnSnapshot.from_provider_surface(production)
            return NativeTurnDispatcher(
                document=document,
                state=state,
                registry=registry,
                turn=current_turn,
                runtimes=build_native_runtime_bindings(
                    context,
                    current_turn.tool_names,
                ),
                reauthorize_turn=reauthorize,
                active_document=lambda: App.ActiveDocument,
            )

        dispatcher = new_dispatcher()
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
        assert initial_state["domain"].get("active_assembly") is None
        assert initial_state["domain"]["assembly_count"] == 0
        document.clearUndos()

        root_result = native_call(
            ASSEMBLY_CREATE_CAPABILITY_NAME,
            {
                "label": "Native Root Assembly",
            },
        )
        root_name = root_result["assembly"]["object_name"]
        root_joints_name = root_result["joint_group"]["object_name"]
        assert root_result["nested"] is False
        assert root_result["active_assembly_unchanged"] is False
        assert root_result["active_assembly"]["object_name"] == root_name
        assert int(document.UndoCount) == 1
        root = document.getObject(root_name)
        assert root.Label == "Native Root Assembly"
        assert _joints_group(root).Name == root_joints_name
        assert Gui.activeDocument().getInEdit() is root.ViewObject

        after_root = native_call("state.read", {"operation": "active"})
        assert after_root["domain"]["active_assembly"]["object_name"] == root_name
        assert after_root["domain"]["assembly_count"] == 1
        source_inventory = after_root["domain"]["available_component_sources"]
        local_source = next(
            item for item in source_inventory if item["object_name"] == source_box.Name
        )
        external_source = next(
            item
            for item in source_inventory
            if item["document_name"] == source_document.Name
            and item["object_name"] == source_assembly.Name
        )
        assert local_source["subassembly"] is False
        assert external_source["subassembly"] is True

        Gui.activeDocument().resetEdit()
        _process_events()
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
        dispatcher = new_dispatcher()

        assert Gui.activeDocument().setEdit(root.Name)
        _process_events(24)
        assert Gui.activeDocument().getInEdit() is root.ViewObject
        active_state = native_call("state.read", {"operation": "active"})
        assert active_state["domain"]["active_assembly"]["object_name"] == root_name

        nested_result = native_call(
            ASSEMBLY_CREATE_CAPABILITY_NAME,
            {
                "label": "Native Nested Assembly",
                "parent_assembly": {"object_name": root_name},
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
            ASSEMBLY_CREATE_CAPABILITY_NAME,
            json.dumps(
                {
                    "label": "Native Nested Assembly",
                    "parent_assembly": {"object_name": root_name},
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
        dispatcher = new_dispatcher()

        local_insert = native_call(
            ASSEMBLY_INSERT_CAPABILITY_NAME,
            {
                "assembly": {"object_name": root_name},
                "source": {
                    key: local_source[key]
                    for key in ("document_name", "object_name")
                },
                "label": "Placed local box",
                "placement": {},
            },
        )
        local_occurrence_name = local_insert["occurrence"]["object_name"]
        local_occurrence = document.getObject(local_occurrence_name)
        assert local_occurrence.ViewObject.Visibility is True
        assert local_occurrence.ViewObject.getBoundingBox().isValid()
        assert local_occurrence.TypeId == "App::Link"
        assert local_occurrence.LinkedObject is source_box
        assert local_occurrence.Label == "Placed local box"
        assert local_occurrence.Placement.isSame(App.Placement(), 1.0e-12)
        assert local_insert["component_count"] == 2
        assert local_insert["grounded"] is False
        assert Gui.activeDocument().getInEdit() is root.ViewObject
        assert int(document.UndoCount) == 3

        connectors = native_call(
            ASSEMBLY_CONNECTORS_CAPABILITY_NAME,
            {
                "first_component": {"object_name": local_occurrence_name},
                "second_component": {"object_name": nested_name},
                "joint_type": "fixed",
            },
        )
        assert connectors["operation"] == "joint_connector_pairs"
        assert connectors["first_component"]["object_name"] == local_occurrence_name
        assert connectors["second_component"]["object_name"] == nested_name
        assert connectors["pairs"] == []
        invalid_connectors = native_call(
            ASSEMBLY_CONNECTORS_CAPABILITY_NAME,
            {
                "first_component": {"object_name": source_box.Name},
                "second_component": {"object_name": local_occurrence_name},
                "joint_type": "fixed",
            },
            succeeds=False,
        )
        assert "not a component" in invalid_connectors["error"]

        document.undo()
        _process_events()
        assert document.getObject(local_occurrence_name) is None
        document.redo()
        _process_events()
        root = document.getObject(root_name)
        local_occurrence = document.getObject(local_occurrence_name)
        assert local_occurrence is not None and local_occurrence.LinkedObject is source_box
        assert Gui.activeDocument().getInEdit() is root.ViewObject
        dispatcher = new_dispatcher()

        subassembly_insert = native_call(
            ASSEMBLY_INSERT_CAPABILITY_NAME,
            {
                "assembly": {"object_name": root_name},
                "source": {
                    key: external_source[key]
                    for key in ("document_name", "object_name")
                },
                "label": "External module",
                "placement": _placement(-25.0, 5.0, 0.0, -30.0),
            },
        )
        subassembly_occurrence_name = subassembly_insert["occurrence"]["object_name"]
        subassembly_occurrence = document.getObject(subassembly_occurrence_name)
        assert subassembly_occurrence.TypeId == "Assembly::AssemblyLink"
        assert subassembly_occurrence.LinkedObject is source_assembly
        assert subassembly_occurrence.Rigid is True
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
        assert subassembly_occurrence.Rigid is True
        dispatcher = new_dispatcher()

        flexible_insert = native_call(
            ASSEMBLY_RIGIDITY_CAPABILITY_NAME,
            {
                "operation": "make_flexible",
                "assembly": {"object_name": root_name},
                "link": {"object_name": subassembly_occurrence_name},
            },
        )
        assert flexible_insert["operation"] == "make_flexible"
        assert document.getObject(subassembly_occurrence_name).Rigid is False
        assert int(document.UndoCount) == 5

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(local_occurrence)
        selection_before = tuple(
            obj.Name for obj in Gui.Selection.getSelection(document.Name)
        )
        rigid_arguments = {
            "operation": "make_rigid",
            "assembly": {"object_name": root_name},
            "link": {"object_name": subassembly_occurrence_name},
        }
        rigid_result = native_call(
            ASSEMBLY_RIGIDITY_CAPABILITY_NAME,
            rigid_arguments,
        )
        rigid_call_id = f"assembly-structure-call-{call_number}"
        subassembly_occurrence = document.getObject(subassembly_occurrence_name)
        assert subassembly_occurrence.Rigid is True
        assert rigid_result["operation"] == "make_rigid"
        assert rigid_result["rigid"] is True
        assert rigid_result["link"]["object_id"] == subassembly_occurrence.ID
        assert rigid_result["linked_assembly"]["object_id"] == source_assembly.ID
        assert rigid_result["removed_grounding"] == []
        assert rigid_result["selection_unchanged"] is True
        assert tuple(
            obj.Name for obj in Gui.Selection.getSelection(document.Name)
        ) == selection_before
        assert int(document.UndoCount) == 6

        rigid_replay = dispatcher.call(
            ASSEMBLY_RIGIDITY_CAPABILITY_NAME,
            json.dumps(rigid_arguments, separators=(",", ":")),
            rigid_call_id,
        )
        assert rigid_replay == rigid_result
        assert int(document.UndoCount) == 6

        document.undo()
        _process_events()
        root = document.getObject(root_name)
        subassembly_occurrence = document.getObject(subassembly_occurrence_name)
        assert subassembly_occurrence.Rigid is False
        assert not active_grounded_joints(_joints_group(root))
        document.redo()
        _process_events()
        root = document.getObject(root_name)
        subassembly_occurrence = document.getObject(subassembly_occurrence_name)
        assert subassembly_occurrence.Rigid is True
        dispatcher = new_dispatcher()
        ground_result = native_call(
            ASSEMBLY_GROUND_CAPABILITY_NAME,
            {
                "operation": "set_grounded",
                "components": [subassembly_occurrence_name],
            },
        )
        ground_joint_name = ground_result["targets"][0]["grounded_joint"][
            "object_name"
        ]
        assert len(active_grounded_joints(_joints_group(root))) == 1
        assert int(document.UndoCount) == 7

        flexible_result = native_call(
            ASSEMBLY_RIGIDITY_CAPABILITY_NAME,
            {
                "operation": "make_flexible",
                "assembly": {"object_name": root_name},
                "link": {"object_name": subassembly_occurrence_name},
            },
        )
        subassembly_occurrence = document.getObject(subassembly_occurrence_name)
        assert subassembly_occurrence.Rigid is False
        assert flexible_result["operation"] == "make_flexible"
        assert flexible_result["rigid"] is False
        assert [
            item["object_name"] for item in flexible_result["removed_grounding"]
        ] == [ground_joint_name]
        assert document.getObject(ground_joint_name) is None
        assert not active_grounded_joints(_joints_group(root))
        assert any(
            item["object_name"] == ground_joint_name
            for item in flexible_result["receipt"]["deleted"]
        )
        assert int(document.UndoCount) == 8

        document.undo()
        _process_events()
        root = document.getObject(root_name)
        subassembly_occurrence = document.getObject(subassembly_occurrence_name)
        assert subassembly_occurrence.Rigid is True
        assert document.getObject(ground_joint_name) is not None
        assert len(active_grounded_joints(_joints_group(root))) == 1
        document.redo()
        _process_events()
        root = document.getObject(root_name)
        subassembly_occurrence = document.getObject(subassembly_occurrence_name)
        assert subassembly_occurrence.Rigid is False
        assert document.getObject(ground_joint_name) is None
        assert not active_grounded_joints(_joints_group(root))
        dispatcher = new_dispatcher()

        prior_body = Gui.activeDocument().activeView().getActiveObject("pdbody")
        new_part_arguments = {
            "assembly": {"object_name": root_name},
            "label": "Native drive bracket",
            "placement": _placement(2.0, 4.0, 6.0, 45.0),
        }
        new_part_result = native_call(
            ASSEMBLY_NEW_PART_CAPABILITY_NAME,
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
        assert int(document.UndoCount) == 9

        replay = dispatcher.call(
            ASSEMBLY_NEW_PART_CAPABILITY_NAME,
            json.dumps(new_part_arguments, separators=(",", ":")),
            new_part_call_id,
        )
        assert replay == new_part_result
        assert int(document.UndoCount) == 9

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
        assert local_occurrence.ViewObject.Visibility is True
        assert local_occurrence.ViewObject.getBoundingBox().isValid()
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
            f"actions={len(surface.command_ids)} tools={len(production.tool_names)} "
            f"schemas={schema_bytes}B assemblies=2 components=4 transactions=8 "
            "rigid_flexible=true grounding_cleanup=true active_read=true",
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
