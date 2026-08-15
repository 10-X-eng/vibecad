# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused real-GUI gate for B-spline curvature-comb visibility."""

from __future__ import annotations

import json
import os
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
from VibeCADNativeSketchPresentationBindings import (
    SKETCH_PRESENTATION_CAPABILITY_NAME,
)
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface
from vibecad_tests.native_sketch_bspline_curvature_comb_visibility_gui_case import (
    exercise_bspline_curvature_comb_visibility_case,
    verify_reopened_bspline_curvature_comb_visibility,
)
from vibecad_tests.native_sketch_geometry_gui_support import (
    edit_boundary,
    process_events,
    provider_turn,
)


def _phase(name: str) -> None:
    os.write(2, f"VIBECAD_NATIVE_SKETCH_BSPLINE_COMB_PHASE {name}\n".encode())


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeSketchBSplineCombGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        sketch = document.addObject(
            "Sketcher::SketchObject",
            "BSplineCurvatureCombViewSketch",
        )
        sketch.Label = "Native B-spline curvature-comb presentation lifecycle"
        document.recompute()
        document.clearUndos()
        process_events(16)

        controller = Gui.getMainWindow().findChild(
            QtCore.QObject,
            "VibeCADRibbonController",
        )
        assert controller is not None
        assert Gui.activeDocument().setEdit(sketch.Name)
        process_events(24)
        surface = read_active_ribbon_surface(controller)
        assert surface.surface_id == "sketch.edit"
        assert active_edit_object() is sketch
        assert "Sketcher_BSplineComb" in surface.command_ids
        frozen_surface = NativeSurfaceSnapshot.from_surface(surface)
        boundary = edit_boundary(document, sketch, controller)
        production = resolve_native_provider_surface(
            surface,
            build_native_capability_registry(),
        )
        assert production.available is False
        assert production.missing_action_ids == ()
        assert SKETCH_PRESENTATION_CAPABILITY_NAME in (
            production.incomplete_definition_names
        )
        assert SKETCH_PRESENTATION_CAPABILITY_NAME not in (
            production.missing_definition_names
        )
        assert production.schemas == ()

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-sketch-bspline-curvature-comb-gui")

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
                SKETCH_PRESENTATION_CAPABILITY_NAME,
                json.dumps(arguments, separators=(",", ":")),
                call_id or f"bspline-curvature-comb-focused-{call_number}",
            )
            assert response.get("ok") is succeeds, response
            assert edit_boundary(document, sketch, controller) == boundary
            return response

        _phase("presentation_parity")
        expected = exercise_bspline_curvature_comb_visibility_case(
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
            Path(tempfile.mkdtemp(prefix="vibecad-native-bspline-comb-"))
            / "NativeSketchBSplineCurvatureComb.FCStd"
        )
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        document.recompute()
        VibeGui._connect_document_observer()
        process_events(16)
        reopened = document.getObject("BSplineCurvatureCombViewSketch")
        assert reopened is not None
        assert Gui.activeDocument().setEdit(reopened.Name)
        process_events(24)
        verify_reopened_bspline_curvature_comb_visibility(reopened, expected)
        _phase("complete")
        print(
            "VIBECAD_NATIVE_SKETCH_BSPLINE_COMB_GUI_OK "
            "explicit stale no-op human-parity exact-topology read-only reopen",
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
