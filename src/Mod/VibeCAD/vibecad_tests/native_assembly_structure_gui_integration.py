# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for Native Assembly creation and active-state reads."""

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
            assembly_definition.provider_schema(("create_assembly",)),
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


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
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
        document = App.newDocument("NativeAssemblyStructureGate")
        document.UndoMode = 1
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

        Gui.activeDocument().resetEdit()
        _process_events(16)
        assert Gui.activeDocument().getInEdit() is None
        temp_directory = tempfile.TemporaryDirectory(
            prefix="vibecad-native-assembly-structure-"
        )
        path = Path(temp_directory.name) / "assembly-structure.FCStd"
        document.saveAs(str(path))
        assert path.is_file()
        document_name = document.Name
        App.closeDocument(document_name)
        document = App.openDocument(str(path))
        App.setActiveDocument(document.Name)
        _process_events(24)

        root = document.getObject(root_name)
        nested = document.getObject(nested_name)
        assert root is not None and root.TypeId == "Assembly::AssemblyObject"
        assert nested is not None and nested.TypeId == "Assembly::AssemblyObject"
        assert nested in list(root.Group)
        assert _joints_group(root).Name == root_joints_name
        assert _joints_group(nested).Name == nested_joints_name
        reopened_state = build_assembly_snapshot(document)
        assert reopened_state["assembly_count"] == 2
        assert reopened_state["active_assembly"] is None

        print(
            "VIBECAD_NATIVE_ASSEMBLY_STRUCTURE_GUI_OK "
            "assemblies=2 transactions=2 active_read=true",
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
        if temp_directory is not None:
            temp_directory.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(0, _run)
