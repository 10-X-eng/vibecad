# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused real-GUI and FCStd lifecycle gate for Sketch Delete Geometry."""

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
from VibeCADEditState import active_edit_object
from VibeCADNativeCapabilityRegistry import resolve_native_provider_surface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSketchCleanupSchema import SKETCH_DELETE_CAPABILITY_NAME
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface
from vibecad_tests.native_sketch_delete_geometry_gui_case import (
    exercise_delete_geometry_case,
    verify_reopened_delete_geometry,
)
from vibecad_tests.native_sketch_geometry_gui_support import (
    edit_boundary,
    process_events,
    provider_turn,
)


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeSketchDeleteGeometryGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        sketch = document.addObject("Sketcher::SketchObject", "DeleteGeometrySketch")
        sketch.Label = "Native Delete Geometry lifecycle"
        document.recompute()
        assert Gui.activeDocument().setEdit(sketch.Name)
        process_events(24)

        main_window = Gui.getMainWindow()
        controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
        assert controller is not None
        surface = read_active_ribbon_surface(controller)
        assert surface.surface_id == "sketch.edit"
        frozen_surface = NativeSurfaceSnapshot.from_surface(surface)
        boundary = edit_boundary(document, sketch, controller)
        production = resolve_native_provider_surface(
            surface,
            build_native_capability_registry(),
        )
        assert production.available is True
        assert SKETCH_DELETE_CAPABILITY_NAME in production.tool_names

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-sketch-delete-geometry-gui")

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

        def native_call(arguments, *, succeeds=True, call_id="delete-geometry"):
            response = dispatcher.call(
                SKETCH_DELETE_CAPABILITY_NAME,
                json.dumps(arguments, separators=(",", ":")),
                call_id,
            )
            assert response.get("ok") is succeeds, response
            assert edit_boundary(document, sketch, controller) == boundary
            return response

        expected = exercise_delete_geometry_case(
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
        save_path = Path(
            tempfile.mkdtemp(prefix="vibecad-native-sketch-delete-")
        ) / "NativeSketchDeleteGeometry.FCStd"
        document.saveAs(str(save_path))
        document_name = document.Name
        sketch_name = sketch.Name
        App.closeDocument(document_name)
        document = App.openDocument(str(save_path))
        document.recompute()
        VibeGui._connect_document_observer()
        process_events(16)
        reopened = document.getObject(sketch_name)
        assert reopened is not None
        verify_reopened_delete_geometry(reopened, expected)
        print(
            "VIBECAD_NATIVE_SKETCH_DELETE_GEOMETRY_GUI_OK "
            "deleted_geometry=1 deleted_constraints=1 undo_redo reopen",
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
