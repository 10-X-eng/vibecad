# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused real-GUI transaction and FCStd lifecycle gate for Sketch Block."""

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

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADEditState import active_edit_object
from VibeCADNativeCapabilityRegistry import resolve_native_provider_surface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSketchConstraintBindings import (
    SKETCH_CONSTRAINT_CAPABILITY_NAME,
)
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface
from vibecad_tests.native_sketch_block_gui_case import (
    exercise_block_case,
    verify_reopened_block,
)
from vibecad_tests.native_sketch_geometry_gui_support import (
    edit_boundary,
    process_events,
    provider_turn,
)


def _phase(name: str) -> None:
    os.write(2, f"VIBECAD_NATIVE_SKETCH_BLOCK_PHASE {name}\n".encode("ascii"))


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeSketchBlockGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        sketch = document.addObject("Sketcher::SketchObject", "BlockSketch")
        sketch.Label = "Native Block lifecycle"
        source = document.addObject("Part::Feature", "ExternalSource")
        source.Shape = Part.makeLine(App.Vector(-20, -30), App.Vector(20, -30))
        document.recompute()
        document.clearUndos()
        process_events(16)
        _phase("document")

        main_window = Gui.getMainWindow()
        controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
        assert controller is not None
        assert Gui.activeDocument().setEdit(sketch.Name)
        process_events(24)
        live_surface = read_active_ribbon_surface(controller)
        assert live_surface.surface_id == "sketch.edit"
        assert active_edit_object() is sketch
        frozen_surface = NativeSurfaceSnapshot.from_surface(live_surface)
        boundary = edit_boundary(document, sketch, controller)
        _phase("edit")

        production = resolve_native_provider_surface(
            live_surface,
            build_native_capability_registry(),
        )
        assert production.available is False
        assert production.tool_names == ()
        assert SKETCH_CONSTRAINT_CAPABILITY_NAME in (
            production.incomplete_definition_names
        )
        _phase("production")

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-sketch-block-gui")

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
        turn = provider_turn(live_surface)
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
                SKETCH_CONSTRAINT_CAPABILITY_NAME,
                json.dumps(arguments, separators=(",", ":")),
                call_id or f"sketch-block-call-{call_number}",
            )
            assert response.get("ok") is succeeds, response
            assert edit_boundary(document, sketch, controller) == boundary
            return response

        expected = exercise_block_case(
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
        save_path = (
            Path(tempfile.mkdtemp(prefix="vibecad-native-sketch-block-"))
            / "NativeSketchBlock.FCStd"
        )
        document.saveAs(str(save_path))
        saved_name = document.Name
        sketch_name = sketch.Name
        App.closeDocument(saved_name)
        document = App.openDocument(str(save_path))
        document.recompute()
        VibeGui._connect_document_observer()
        process_events(16)
        sketch = document.getObject(sketch_name)
        assert sketch is not None
        assert Gui.activeDocument().setEdit(sketch.Name)
        process_events(24)
        assert read_active_ribbon_surface(controller).surface_id == "sketch.edit"
        verify_reopened_block(sketch, expected)
        print(
            "VIBECAD_NATIVE_SKETCH_BLOCK_GUI_OK "
            f"geometry={expected['geometry_count']} "
            f"constraints={expected['constraint_count']} targets=16",
            flush=True,
        )
        _phase("complete")
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
