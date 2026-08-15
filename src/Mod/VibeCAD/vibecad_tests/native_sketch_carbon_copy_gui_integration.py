# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused real-GUI transaction and FCStd gate for Sketch Carbon Copy."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets
import Sketcher

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADEditState import active_edit_object
from VibeCADNativeCapabilityRegistry import resolve_native_provider_surface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSketchGeometryBindings import SKETCH_GEOMETRY_CAPABILITY_NAME
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface
from vibecad_tests.native_sketch_carbon_copy_gui_case import (
    exercise_carbon_copy_case,
    verify_reopened_carbon_copy,
)
from vibecad_tests.native_sketch_geometry_gui_support import (
    edit_boundary,
    process_events,
    provider_turn,
)


def _selection(document) -> tuple:
    return tuple(
        (str(item.ObjectName), tuple(str(name) for name in item.SubElementNames))
        for item in Gui.Selection.getSelectionEx(document.Name)
    )


def _phase(name: str) -> None:
    os.write(2, f"VIBECAD_NATIVE_SKETCH_CARBON_COPY_PHASE {name}\n".encode("ascii"))


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeSketchCarbonCopyGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        support = document.addObject("Part::Feature", "CarbonCopySupport")
        support.Label = "Carbon Copy source support"
        support.Shape = Part.makeLine(
            App.Vector(-8.0, 12.0, 0.0),
            App.Vector(8.0, 12.0, 0.0),
        )
        source = document.addObject("Sketcher::SketchObject", "CarbonCopySource")
        source.Label = "Exact Carbon Copy source"
        source.addGeometry(
            Part.LineSegment(
                App.Vector(-5.0, 2.0, 0.0),
                App.Vector(5.0, 2.0, 0.0),
            ),
            False,
        )
        source.addConstraint(Sketcher.Constraint("Distance", 0, 10.0))
        source.addExternal(support.Name, "Edge1", False, False)
        sketch = document.addObject("Sketcher::SketchObject", "CarbonCopySketch")
        sketch.Label = "Native Carbon Copy lifecycle"
        document.recompute()
        document.clearUndos()
        process_events(16)

        main_window = Gui.getMainWindow()
        controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
        assert controller is not None
        assert Gui.activeDocument().setEdit(sketch.Name)
        process_events(24)
        surface = read_active_ribbon_surface(controller)
        assert surface.surface_id == "sketch.edit"
        assert active_edit_object() is sketch
        frozen_surface = NativeSurfaceSnapshot.from_surface(surface)
        boundary = edit_boundary(document, sketch, controller)
        production = resolve_native_provider_surface(
            surface,
            build_native_capability_registry(),
        )
        assert production.available is False
        assert "Sketcher_CarbonCopy" in surface.command_ids
        assert "Sketcher_Translate" in surface.command_ids
        assert "Sketcher_Rotate" in surface.command_ids
        assert production.missing_action_ids == ()
        assert SKETCH_GEOMETRY_CAPABILITY_NAME in production.incomplete_definition_names
        assert production.schemas == ()

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(document.Name, source.Name)
        process_events(8)
        selection = _selection(document)
        assert selection == ((source.Name, ()),)

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-sketch-carbon-copy-gui")

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
            edit_or_task_active=lambda: active_edit_object() is not None,
        )
        turn = provider_turn(surface)
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state,
            registry=build_native_capability_registry(),
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )
        call_number = 0

        def native_call(arguments, *, succeeds=True, call_id=None):
            nonlocal call_number
            call_number += 1
            response = dispatcher.call(
                SKETCH_GEOMETRY_CAPABILITY_NAME,
                json.dumps(arguments, separators=(",", ":")),
                call_id or f"carbon-copy-focused-{call_number}",
            )
            assert response.get("ok") is succeeds, response
            assert edit_boundary(document, sketch, controller) == boundary
            assert _selection(document) == selection
            return response

        _phase("carbon-copy")
        expected = exercise_carbon_copy_case(
            sketch=sketch,
            document=document,
            native_call=native_call,
            process_events=process_events,
            edit_boundary=edit_boundary,
            boundary=boundary,
            controller=controller,
        )

        Gui.activeDocument().resetEdit()
        process_events(16)
        save_path = Path(tempfile.mkdtemp(prefix="vibecad-native-carbon-copy-")) / (
            "NativeSketchCarbonCopy.FCStd"
        )
        document.saveAs(str(save_path))
        saved_name = document.Name
        App.closeDocument(saved_name)
        document = App.openDocument(str(save_path))
        document.recompute()
        VibeGui._connect_document_observer()
        process_events(16)

        reopened = document.getObject("CarbonCopySketch")
        assert reopened is not None
        assert Gui.activeDocument().setEdit(reopened.Name)
        process_events(24)
        assert read_active_ribbon_surface(controller).surface_id == "sketch.edit"
        verify_reopened_carbon_copy(reopened, expected)
        _phase("complete")
        print(
            "VIBECAD_NATIVE_SKETCH_CARBON_COPY_GUI_OK "
            "carbon_copy stale duplicate undo redo reopen selection",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=__import__("sys").__stderr__)
    finally:
        if Gui.activeDocument() and Gui.activeDocument().getInEdit():
            Gui.activeDocument().resetEdit()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
