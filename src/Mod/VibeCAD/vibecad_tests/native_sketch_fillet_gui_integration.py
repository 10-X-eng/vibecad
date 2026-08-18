# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused real-GUI transaction and FCStd lifecycle gate for Sketch Fillet."""

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
from VibeCADNativeSketchRevision import sketch_revision
from VibeCADNativeSketchState import (
    serialize_sketch_constraint,
    serialize_sketch_geometry,
)
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface
from vibecad_tests.native_sketch_geometry_gui_support import (
    edit_boundary,
    process_events,
)
from vibecad_tests.native_sketch_fillet_gui_case import verify_reopened_fillet


def _phase(name: str) -> None:
    os.write(2, f"VIBECAD_NATIVE_SKETCH_FILLET_PHASE {name}\n".encode("ascii"))


def _arguments(revision: str, *, position: str = "end") -> dict:
    return {
        "operation": "create_fillet",
        "revision": revision,
        "target": {
            "form": "corner",
            "geometry_index": 0,
            "position": position,
        },
        "radius_mm": 2.0,
        "preserve_corner": True,
    }


def _selection(document) -> tuple:
    return tuple(
        (str(item.ObjectName), tuple(str(name) for name in item.SubElementNames))
        for item in Gui.Selection.getSelectionEx(document.Name)
    )


def _records(sketch) -> dict[str, tuple[dict, ...]]:
    return {
        "geometry": tuple(
            serialize_sketch_geometry(sketch, index)
            for index in range(int(sketch.GeometryCount))
        ),
        "constraints": tuple(
            serialize_sketch_constraint(sketch, index)
            for index in range(int(sketch.ConstraintCount))
        ),
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeSketchFilletGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        sketch = document.addObject("Sketcher::SketchObject", "FilletSketch")
        sketch.Label = "Native Fillet lifecycle"
        assert (
            sketch.addGeometry(
                Part.LineSegment(App.Vector(0, 0), App.Vector(20, 0)), False
            )
            == 0
        )
        assert (
            sketch.addGeometry(
                Part.LineSegment(App.Vector(20, 0), App.Vector(20, 15)), False
            )
            == 1
        )
        assert sketch.addConstraint(Sketcher.Constraint("Coincident", 0, 2, 1, 1)) == 0
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
        frozen_surface = NativeSurfaceSnapshot.from_surface(live_surface)
        boundary = edit_boundary(document, sketch, controller)
        production = resolve_native_provider_surface(
            live_surface, build_native_capability_registry()
        )
        assert production.available is True
        assert production.missing_action_ids == ()
        assert production.incomplete_definition_names == ()
        _phase("surface")

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-sketch-fillet-gui")

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
        turn = NativeTurnSnapshot.from_provider_surface(production)
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state,
            registry=build_native_capability_registry(),
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )

        def native_call(arguments, *, succeeds=True, call_id="fillet-call"):
            response = dispatcher.call(
                "sketch.fillet",
                json.dumps(arguments, separators=(",", ":")),
                call_id,
            )
            assert response.get("ok") is succeeds, response
            assert edit_boundary(document, sketch, controller) == boundary
            return response

        diagnosis_before = (
            int(sketch.GeometryCount),
            int(sketch.ConstraintCount),
            int(document.UndoCount),
            tuple(item.Type for item in sketch.Constraints),
        )
        diagnosis = sketch.diagnoseFillet(0, 2, 2.0, True)
        assert diagnosis["accepted"] is True
        assert diagnosis["radius_mm"] == 2.0
        assert diagnosis_before == (
            int(sketch.GeometryCount),
            int(sketch.ConstraintCount),
            int(document.UndoCount),
            tuple(item.Type for item in sketch.Constraints),
        )
        Gui.Selection.clearSelection(document.Name)
        Gui.Selection.addSelection(document.Name, sketch.Name, "Edge2")
        process_events(8)
        selected = _selection(document)
        assert selected == ((sketch.Name, ("Edge2",)),)

        response = native_call(_arguments(sketch_revision(sketch)))
        assert response["operation"] == "create_fillet"
        assert response["form"] == "corner"
        assert response["trimmed"] is True
        assert response["preserve_corner"] is True
        assert response["geometry_count"] == 4
        assert response["constraint_count"] == 4
        assert response["fillet"]["kind"] == "circular_arc"
        assert response["preserved_corner"]["kind"] == "point"
        assert _selection(document) == selected
        assert int(document.UndoCount) == 1
        assert document.UndoNames[0] == "Create Native Sketch Fillet"
        assert serialize_sketch_geometry(sketch, 2)["kind"] == "circular_arc"
        assert serialize_sketch_geometry(sketch, 3)["construction"] is True
        _phase("mutation")

        Gui.Selection.clearSelection(document.Name)
        document.undo()
        process_events(16)
        assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (2, 1)
        document.redo()
        process_events(16)
        assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (4, 4)
        assert edit_boundary(document, sketch, controller) == boundary
        expected_records = _records(sketch)

        Gui.activeDocument().resetEdit()
        process_events(16)
        save_path = (
            Path(tempfile.mkdtemp(prefix="vibecad-native-sketch-fillet-"))
            / "NativeSketchFillet.FCStd"
        )
        document.saveAs(str(save_path))
        document_name = document.Name
        sketch_name = sketch.Name
        App.closeDocument(document_name)
        document = App.openDocument(str(save_path))
        document.recompute()
        VibeGui._connect_document_observer()
        process_events(16)
        sketch = document.getObject(sketch_name)
        assert sketch is not None
        assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (4, 4)
        verify_reopened_fillet(sketch, expected_records)
        assert serialize_sketch_geometry(sketch, 2)["kind"] == "circular_arc"
        assert serialize_sketch_geometry(sketch, 3)["construction"] is True
        print(
            "VIBECAD_NATIVE_SKETCH_FILLET_GUI_OK geometry=4 constraints=4 targets=2",
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
