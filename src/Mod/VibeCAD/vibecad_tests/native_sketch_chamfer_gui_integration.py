# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused real-GUI transaction and FCStd lifecycle gate for Sketch Chamfer."""

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
from vibecad_tests.native_sketch_chamfer_gui_case import verify_reopened_chamfer
from vibecad_tests.native_sketch_geometry_gui_support import (
    edit_boundary,
    process_events,
)


def _phase(name: str) -> None:
    os.write(2, f"VIBECAD_NATIVE_SKETCH_CHAMFER_PHASE {name}\n".encode("ascii"))


def _corner_arguments(revision: str) -> dict:
    return {
        "operation": "create_chamfer",
        "revision": revision,
        "target": {
            "form": "corner",
            "geometry_index": 0,
            "position": "end",
        },
        "distance_mm": 2.0,
        "preserve_corner": True,
    }


def _pair_arguments(revision: str) -> dict:
    return {
        "operation": "create_chamfer",
        "revision": revision,
        "target": {
            "form": "curve_pair",
            "curves": [
                {
                    "geometry_index": 0,
                    "reference_point_mm": {"x": 18.0, "y": 0.0},
                },
                {
                    "geometry_index": 1,
                    "reference_point_mm": {"x": 20.0, "y": 2.0},
                },
            ],
        },
        "distance_mm": 2.0,
        "preserve_corner": False,
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


def _verify_reopened_pair(sketch, expected: dict[str, tuple[dict, ...]]) -> None:
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (4, 4)
    observed = _records(sketch)
    assert observed["constraints"] == expected["constraints"]
    tags: set[str] = set()
    for saved, reopened in zip(expected["geometry"], observed["geometry"], strict=True):
        saved = dict(saved)
        reopened = dict(reopened)
        assert saved.pop("tag", "")
        tag = str(reopened.pop("tag", "") or "")
        assert tag and tag not in tags
        tags.add(tag)
        assert reopened == saved
    assert serialize_sketch_geometry(sketch, 2)["construction"] is True
    assert serialize_sketch_geometry(sketch, 3)["construction"] is True


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeSketchChamferGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        corner_sketch = document.addObject("Sketcher::SketchObject", "ChamferCorner")
        corner_sketch.Label = "Native Chamfer corner lifecycle"
        assert (
            corner_sketch.addGeometry(
                Part.LineSegment(App.Vector(0, 0), App.Vector(20, 0)), False
            )
            == 0
        )
        assert (
            corner_sketch.addGeometry(
                Part.LineSegment(App.Vector(20, 0), App.Vector(20, 15)), False
            )
            == 1
        )
        assert (
            corner_sketch.addConstraint(Sketcher.Constraint("Coincident", 0, 2, 1, 1))
            == 0
        )
        pair_sketch = document.addObject("Sketcher::SketchObject", "ChamferPair")
        pair_sketch.Label = "Native Chamfer curve-pair lifecycle"
        assert (
            pair_sketch.addGeometry(
                Part.LineSegment(App.Vector(0, 0), App.Vector(20, 0)), True
            )
            == 0
        )
        assert (
            pair_sketch.addGeometry(
                Part.LineSegment(App.Vector(20, 0), App.Vector(20, 15)), True
            )
            == 1
        )
        document.recompute()
        document.clearUndos()
        process_events(16)
        _phase("document")

        main_window = Gui.getMainWindow()
        controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
        assert controller is not None
        assert Gui.activeDocument().setEdit(corner_sketch.Name)
        process_events(24)
        live_surface = read_active_ribbon_surface(controller)
        assert live_surface.surface_id == "sketch.edit"
        frozen_surface = NativeSurfaceSnapshot.from_surface(live_surface)
        boundary = edit_boundary(document, corner_sketch, controller)
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
        ledger.begin_run("native-sketch-chamfer-gui")

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

        registry = build_native_capability_registry()

        def dispatcher_for(surface):
            provider = resolve_native_provider_surface(surface, registry)
            assert provider.available is True
            turn = NativeTurnSnapshot.from_provider_surface(provider)
            return NativeTurnDispatcher(
                document=document,
                state=state,
                registry=registry,
                turn=turn,
                runtimes=build_native_runtime_bindings(context, turn.tool_names),
                reauthorize_turn=reauthorize,
                active_document=lambda: App.ActiveDocument,
            )

        dispatcher = dispatcher_for(live_surface)
        active_sketch = corner_sketch

        def native_call(arguments, *, succeeds=True, call_id="chamfer-call"):
            response = dispatcher.call(
                "sketch.chamfer",
                json.dumps(arguments, separators=(",", ":")),
                call_id,
            )
            assert response.get("ok") is succeeds, response
            assert edit_boundary(document, active_sketch, controller) == boundary
            return response

        diagnosis_before = (
            int(corner_sketch.GeometryCount),
            int(corner_sketch.ConstraintCount),
            int(document.UndoCount),
        )
        diagnosis = corner_sketch.diagnoseChamfer(0, 2, 2.0, True)
        assert diagnosis["accepted"] is True
        assert diagnosis["radius_mm"] == 2.0
        assert diagnosis["support_arc_geometry_index"] == 2
        assert diagnosis["corner_geometry_index"] == 3
        assert diagnosis["chamfer_geometry_index"] == 4
        assert diagnosis_before == (
            int(corner_sketch.GeometryCount),
            int(corner_sketch.ConstraintCount),
            int(document.UndoCount),
        )
        Gui.Selection.clearSelection(document.Name)
        Gui.Selection.addSelection(document.Name, corner_sketch.Name, "Edge2")
        process_events(8)
        selected = _selection(document)
        response = native_call(_corner_arguments(sketch_revision(corner_sketch)))
        assert response["operation"] == "create_chamfer"
        assert response["form"] == "corner"
        assert response["geometry_count"] == 5
        assert response["constraint_count"] == 6
        assert response["chamfer"]["kind"] == "line"
        assert response["preserved_corner"]["kind"] == "point"
        assert _selection(document) == selected
        assert int(document.UndoCount) == 1
        assert document.UndoNames[0] == "Create Native Sketch Chamfer"
        corner_records = _records(corner_sketch)
        document.undo()
        process_events(16)
        assert (
            int(corner_sketch.GeometryCount),
            int(corner_sketch.ConstraintCount),
        ) == (
            2,
            1,
        )
        document.redo()
        process_events(16)
        assert _records(corner_sketch) == corner_records
        _phase("corner")

        Gui.Selection.clearSelection(document.Name)
        Gui.activeDocument().resetEdit()
        process_events(16)
        assert Gui.activeDocument().setEdit(pair_sketch.Name)
        process_events(24)
        live_surface = read_active_ribbon_surface(controller)
        frozen_surface = NativeSurfaceSnapshot.from_surface(live_surface)
        dispatcher = dispatcher_for(live_surface)
        active_sketch = pair_sketch
        boundary = edit_boundary(document, pair_sketch, controller)

        pair_before = (
            int(pair_sketch.GeometryCount),
            int(pair_sketch.ConstraintCount),
            int(document.UndoCount),
        )
        pair_diagnosis = pair_sketch.diagnoseChamfer(
            0,
            1,
            App.Vector(18, 0),
            App.Vector(20, 2),
            2.0,
            False,
        )
        assert pair_diagnosis["corner_geometry_index"] is None
        assert pair_diagnosis["chamfer_geometry_index"] == 3
        assert pair_diagnosis["construction"] is True
        assert pair_before == (
            int(pair_sketch.GeometryCount),
            int(pair_sketch.ConstraintCount),
            int(document.UndoCount),
        )
        Gui.Selection.addSelection(document.Name, pair_sketch.Name, "Edge1")
        pair_selection = _selection(document)
        pair_response = native_call(
            _pair_arguments(sketch_revision(pair_sketch)),
            call_id="chamfer-pair",
        )
        assert pair_response["form"] == "curve_pair"
        assert pair_response["geometry_count"] == 4
        assert pair_response["constraint_count"] == 4
        assert pair_response["construction"] is True
        assert pair_response["chamfer"]["construction"] is True
        assert "preserved_corner" not in pair_response
        assert _selection(document) == pair_selection
        assert int(document.UndoCount) == 2
        assert document.UndoNames[0] == "Create Native Sketch Chamfer"
        pair_records = _records(pair_sketch)
        document.undo()
        process_events(16)
        assert (int(pair_sketch.GeometryCount), int(pair_sketch.ConstraintCount)) == (
            2,
            0,
        )
        document.redo()
        process_events(16)
        assert _records(pair_sketch) == pair_records
        _phase("pair")

        Gui.Selection.clearSelection(document.Name)
        Gui.activeDocument().resetEdit()
        process_events(16)
        save_path = (
            Path(tempfile.mkdtemp(prefix="vibecad-native-sketch-chamfer-"))
            / "NativeSketchChamfer.FCStd"
        )
        document.saveAs(str(save_path))
        document_name = document.Name
        App.closeDocument(document_name)
        document = App.openDocument(str(save_path))
        document.recompute()
        VibeGui._connect_document_observer()
        process_events(16)
        reopened_corner = document.getObject("ChamferCorner")
        reopened_pair = document.getObject("ChamferPair")
        assert reopened_corner is not None and reopened_pair is not None
        verify_reopened_chamfer(reopened_corner, corner_records)
        _verify_reopened_pair(reopened_pair, pair_records)
        print(
            "VIBECAD_NATIVE_SKETCH_CHAMFER_GUI_OK "
            "corner_geometry=5 corner_constraints=6 "
            "pair_geometry=4 pair_constraints=4 targets=2",
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
