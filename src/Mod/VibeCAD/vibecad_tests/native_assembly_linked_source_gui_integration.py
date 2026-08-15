# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI gate for exact Native Assembly linked-source reading."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import UtilsAssembly
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeAssemblyInspectSchema import (
    ASSEMBLY_INSPECT_CAPABILITY_NAME,
    assembly_inspect_capability_definition,
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
from VibeCADNativeTargets import read_current_selection
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


def _process_events(rounds: int = 20) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _select_assemble_ribbon(main_window) -> tuple[object, object]:
    controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
    tabs = main_window.findChild(QtWidgets.QTabBar, "VibeCADRibbonTabs")
    assert controller is not None and tabs is not None
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
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "assemble"
    return controller, surface


def _focused_turn(surface, registry) -> NativeTurnSnapshot:
    state = registry.definition("state.read")
    inspect = assembly_inspect_capability_definition()
    assert state is not None
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=("state.read", ASSEMBLY_INSPECT_CAPABILITY_NAME),
            schemas=(
                state.provider_schema(("active", "selection")),
                inspect.provider_schema(("linked_source",)),
            ),
            human_only_action_ids=("Assembly_ActivateAssembly",),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _selection_objects() -> tuple[object, ...]:
    return tuple(
        entry.Object
        for entry in tuple(Gui.Selection.getSelectionEx() or ())
        if getattr(entry, "Object", None) is not None
    )


def _activate_object_view(obj) -> None:
    gui_document = Gui.getDocument(obj.Document.Name)
    assert gui_document is not None
    App.setActiveDocument(obj.Document.Name)
    _process_events(16)
    assert App.ActiveDocument is obj.Document


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    target = None
    source_document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("AssemblyWorkbench")
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-assembly-linked-source-"
        )
        source_path = Path(temporary.name) / "linked-source.FCStd"
        target_path = Path(temporary.name) / "linked-target.FCStd"

        source_document = App.newDocument("NativeLinkedSourceGate")
        source_document.UndoMode = 1
        source = source_document.addObject(
            "Assembly::AssemblyObject",
            "SourceAssembly",
        )
        source.Type = "Assembly"
        source.Label = "Durable source Assembly"
        source.newObject("Assembly::JointGroup", "Joints")
        source_document.recompute()
        source_document.saveAs(str(source_path))

        target = App.newDocument("NativeLinkedTargetGate")
        target.UndoMode = 1
        target_assembly = target.addObject(
            "Assembly::AssemblyObject",
            "TargetAssembly",
        )
        target_assembly.Type = "Assembly"
        target_assembly.newObject("Assembly::JointGroup", "Joints")
        target.recompute()
        target.saveAs(str(target_path))
        target.openTransaction("Create linked Assembly occurrence")
        link = target_assembly.newObject(
            "Assembly::AssemblyLink",
            "LinkedSubassembly",
        )
        link.LinkedObject = source
        link.Label = "Source Assembly occurrence"
        link.Rigid = False
        UtilsAssembly.finalizeInsertedComponentTimeline(link)
        target.recompute()
        target.commitTransaction()
        target.saveAs(str(target_path))
        source_name = source.Name
        link_name = link.Name

        App.closeDocument(target.Name)
        target = None
        App.closeDocument(source_document.Name)
        source_document = None
        source_document = App.openDocument(str(source_path))
        target = App.openDocument(str(target_path))
        source = source_document.getObject(source_name)
        target_assembly = target.getObject("TargetAssembly")
        link = target.getObject(link_name)
        assert source is not None and target_assembly is not None and link is not None
        assert link.LinkedObject is source
        assert UtilsAssembly.isTimelineOperationActive(link)
        assert UtilsAssembly.isTimelineOperationActive(source)
        _activate_object_view(link)

        VibeGui._connect_document_observer()
        main_window = Gui.getMainWindow()
        controller, surface = _select_assemble_ribbon(main_window)
        command = Gui.Command.get("Assembly_LinkSelectLinked")
        assert command is not None
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(link)
        _process_events(12)
        assert Gui.isCommandActive("Assembly_LinkSelectLinked")

        Gui.runCommand("Assembly_LinkSelectLinked")
        _process_events(20)
        assert _selection_objects() == (source,)
        assert App.ActiveDocument is source_document
        human_navigation = True

        _activate_object_view(link)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(link)
        _process_events(12)
        assert _selection_objects() == (link,)

        registry = build_native_capability_registry()
        definition = registry.definition(ASSEMBLY_INSPECT_CAPABILITY_NAME)
        assert definition is not None
        variant = definition.variants[0]
        assert variant.action_ids == frozenset({"Assembly_LinkSelectLinked"})
        assert variant.transaction_behavior == "none"
        assert registry.implementation(ASSEMBLY_INSPECT_CAPABILITY_NAME) is not None
        production = resolve_native_provider_surface(surface, registry)
        assert ASSEMBLY_INSPECT_CAPABILITY_NAME not in (
            production.missing_definition_names
        )
        assert ASSEMBLY_INSPECT_CAPABILITY_NAME not in (
            production.missing_implementation_names
        )
        assert ASSEMBLY_INSPECT_CAPABILITY_NAME not in (
            production.incomplete_definition_names
        )

        frozen = NativeSurfaceSnapshot.from_surface(surface)
        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-assembly-linked-source-gui")

        def reauthorize() -> None:
            require_frozen_native_surface(frozen, controller)

        context = NativeRuntimeContext(
            service=service,
            document=target,
            state=state,
            undo_ledger=ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(controller).surface_id,
            edit_or_task_active=lambda: bool(Gui.Control.activeDialog()),
        )
        turn = _focused_turn(surface, registry)
        dispatcher = NativeTurnDispatcher(
            document=target,
            state=state,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )

        mdi_area = main_window.findChild(QtWidgets.QMdiArea)
        assert mdi_area is not None
        objects_before = tuple(target.Objects)
        source_objects_before = tuple(source_document.Objects)
        selection_before = read_current_selection(target)
        global_selection_before = _selection_objects()
        target_undo_before = int(target.UndoCount)
        source_undo_before = int(source_document.UndoCount)
        target_transaction_before = int(target.getBookedTransactionID())
        source_transaction_before = int(source_document.getBookedTransactionID())
        target_touched_before = bool(target.isTouched())
        source_touched_before = bool(source_document.isTouched())
        subwindow_before = mdi_area.activeSubWindow()
        edit_before = Gui.activeDocument().getInEdit()
        task_before = Gui.Control.activeTaskDialog()

        result = dispatcher.call(
            ASSEMBLY_INSPECT_CAPABILITY_NAME,
            json.dumps(
                {
                    "operation": "linked_source",
                    "link": {"object_name": link.Name},
                },
                separators=(",", ":"),
            ),
            "assembly-linked-source-read",
        )
        assert result["ok"] is True, result
        assert result["assembly_link"]["object_name"] == link.Name
        assert result["assembly_link"]["object_id"] == int(link.ID)
        assert result["linked_assembly"]["object_name"] == source.Name
        assert result["linked_assembly"]["object_id"] == int(source.ID)
        assert result["linked_assembly"]["document_uid"] == str(source_document.Uid)
        assert result["source_is_external"] is True
        assert result["rigid"] is False
        assert result["selected_subelements"] == []
        assert result["selection_unchanged"] is True
        assert result["active_document_unchanged"] is True
        assert result["document_graph_unchanged"] is True

        assert App.ActiveDocument is target
        assert mdi_area.activeSubWindow() is subwindow_before
        assert Gui.activeDocument().getInEdit() is edit_before
        assert Gui.Control.activeTaskDialog() is task_before
        assert tuple(target.Objects) == objects_before
        assert tuple(source_document.Objects) == source_objects_before
        assert read_current_selection(target) == selection_before
        assert _selection_objects() == global_selection_before == (link,)
        assert int(target.UndoCount) == target_undo_before
        assert int(source_document.UndoCount) == source_undo_before
        assert int(target.getBookedTransactionID()) == target_transaction_before == 0
        assert (
            int(source_document.getBookedTransactionID())
            == (source_transaction_before)
            == 0
        )
        assert bool(target.isTouched()) is target_touched_before
        assert bool(source_document.isTouched()) is source_touched_before

        Gui.Selection.clearSelection()
        empty = dispatcher.call(
            ASSEMBLY_INSPECT_CAPABILITY_NAME,
            '{"operation":"linked_source","link":{"object_name":"%s"}}' % link.Name,
            "assembly-linked-source-empty-selection",
        )
        assert empty["ok"] is False, empty
        assert empty["error_code"] == "NATIVE_ASSEMBLY_INSPECT_FAILED"
        assert tuple(target.Objects) == objects_before
        assert tuple(source_document.Objects) == source_objects_before
        assert int(target.UndoCount) == target_undo_before

        Gui.Selection.addSelection(link)
        Gui.Selection.addSelection(target_assembly)
        multiple = dispatcher.call(
            ASSEMBLY_INSPECT_CAPABILITY_NAME,
            '{"operation":"linked_source","link":{"object_name":"%s"}}' % link.Name,
            "assembly-linked-source-multiple-selection",
        )
        assert multiple["ok"] is False, multiple
        assert multiple["error_code"] == "NATIVE_ASSEMBLY_INSPECT_FAILED"
        assert tuple(target.Objects) == objects_before
        assert tuple(source_document.Objects) == source_objects_before
        assert int(target.UndoCount) == target_undo_before

        print(
            "VIBECAD_NATIVE_ASSEMBLY_LINKED_SOURCE_GUI_OK "
            f"human_navigation={str(human_navigation).lower()} "
            "external=true read_only=true exact_selection=true stale_noop=true "
            "reopen=true selection_unchanged=true active_document_unchanged=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        Gui.Selection.clearSelection()
        if target is not None:
            App.closeDocument(target.Name)
        if source_document is not None:
            App.closeDocument(source_document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(0, _run)
