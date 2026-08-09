# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused real-GUI transaction and FCStd lifecycle gate for Sketch Split."""

from __future__ import annotations

import json
import math
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
from VibeCADNativeSketchGeometryBindings import SKETCH_GEOMETRY_CAPABILITY_NAME
from VibeCADNativeSketchState import (
    serialize_sketch_constraint,
    serialize_sketch_geometry,
)
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface
from vibecad_tests.native_sketch_geometry_gui_support import (
    edit_boundary,
    process_events,
    provider_turn,
)
from vibecad_tests.native_sketch_split_gui_case import verify_reopened_split


def _phase(name: str) -> None:
    os.write(2, f"VIBECAD_NATIVE_SKETCH_SPLIT_PHASE {name}\n".encode("ascii"))


def _arguments(
    sketch,
    point: tuple[float, float],
    *,
    expected_geometry_count: int | None = None,
) -> dict:
    return {
        "operation": "split",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": (
            int(sketch.GeometryCount)
            if expected_geometry_count is None
            else expected_geometry_count
        ),
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_geometry_count": 0,
        "target": {
            "geometry_index": 0,
            "reference_point_mm": {"x": point[0], "y": point[1]},
        },
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


def _make_line_sketch(document):
    sketch = document.addObject("Sketcher::SketchObject", "SplitLine")
    sketch.Label = "Native Split open-line lifecycle"
    assert (
        sketch.addGeometry(
            Part.LineSegment(App.Vector(0, 0), App.Vector(20, 0)),
            True,
        )
        == 0
    )
    return sketch


def _make_circle_sketch(document):
    sketch = document.addObject("Sketcher::SketchObject", "SplitCircle")
    sketch.Label = "Native Split closed-circle lifecycle"
    assert (
        sketch.addGeometry(
            Part.Circle(App.Vector(0, 0), App.Vector(0, 0, 1), 10),
            False,
        )
        == 0
    )
    return sketch


def _verify_reopened_exact(sketch, expected, counts) -> None:
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == counts
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


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeSketchSplitGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        selection_anchor = document.addObject("Part::Feature", "SelectionAnchor")
        selection_anchor.Shape = Part.makeLine(App.Vector(-5, -10), App.Vector(25, -10))
        cases = (
            (_make_line_sketch(document), (8.0, 0.0), "split", (2, 1), [0, 1]),
            (_make_circle_sketch(document), (10.0, 0.0), "opened", (1, 0), [0]),
        )
        document.recompute()
        document.clearUndos()
        process_events(16)
        _phase("document")

        main_window = Gui.getMainWindow()
        controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
        assert controller is not None
        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-sketch-split-gui")
        frozen_surface = None
        active_sketch = None
        boundary = None

        def reauthorize() -> None:
            assert frozen_surface is not None
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

        def dispatcher_for(surface):
            turn = provider_turn(surface)
            return NativeTurnDispatcher(
                document=document,
                state=state,
                registry=build_native_capability_registry(),
                turn=turn,
                runtimes=build_native_runtime_bindings(context, turn.tool_names),
                reauthorize_turn=reauthorize,
                active_document=lambda: App.ActiveDocument,
            )

        expected_records = {}
        for case_number, (
            sketch,
            point,
            outcome,
            final_counts,
            replacement_indices,
        ) in enumerate(cases, start=1):
            if Gui.activeDocument().getInEdit():
                Gui.activeDocument().resetEdit()
                process_events(16)
            assert Gui.activeDocument().setEdit(sketch.Name)
            process_events(24)
            live_surface = read_active_ribbon_surface(controller)
            assert live_surface.surface_id == "sketch.edit"
            frozen_surface = NativeSurfaceSnapshot.from_surface(live_surface)
            active_sketch = sketch
            boundary = edit_boundary(document, sketch, controller)
            dispatcher = dispatcher_for(live_surface)
            production = resolve_native_provider_surface(
                live_surface,
                build_native_capability_registry(),
            )
            assert production.available is False
            assert "Sketcher_Split" not in production.missing_action_ids
            assert SKETCH_GEOMETRY_CAPABILITY_NAME in (
                production.incomplete_definition_names
            )

            def native_call(arguments, *, succeeds=True, suffix="call"):
                response = dispatcher.call(
                    SKETCH_GEOMETRY_CAPABILITY_NAME,
                    json.dumps(arguments, separators=(",", ":")),
                    f"split-{case_number}-{suffix}",
                )
                assert response.get("ok") is succeeds, response
                assert edit_boundary(document, active_sketch, controller) == boundary
                return response

            before = _records(sketch)
            undo_before = int(document.UndoCount)
            diagnosis = sketch.diagnoseSplit(0, App.Vector(*point, 0.0))
            assert diagnosis["accepted"] is True
            assert diagnosis["reference_point_mm"] == [point[0], point[1]]
            assert diagnosis["external_geometry_count"] == 0
            assert _records(sketch) == before
            assert int(document.UndoCount) == undo_before

            stale = native_call(
                _arguments(
                    sketch,
                    point,
                    expected_geometry_count=int(sketch.GeometryCount) + 1,
                ),
                succeeds=False,
                suffix="stale",
            )
            assert stale["error_code"] == "NATIVE_SKETCH_INVALID"
            assert int(document.UndoCount) == undo_before

            Gui.Selection.clearSelection(document.Name)
            Gui.Selection.addSelection(document.Name, selection_anchor.Name, "Edge1")
            process_events(8)
            selected = _selection(document)
            assert selected == ((selection_anchor.Name, ("Edge1",)),)
            response = native_call(_arguments(sketch, point))
            assert response["operation"] == "split"
            assert response["outcome"] == outcome
            assert response["deleted_geometry_indices"] == [0]
            assert response["replacement_geometry_indices"] == replacement_indices
            assert (
                response["geometry_count"],
                response["constraint_count"],
            ) == final_counts
            assert _selection(document) == selected
            assert int(document.UndoCount) == undo_before + 1
            assert document.UndoNames[0] == "Split Native Sketch Geometry"
            records = _records(sketch)
            expected_records[sketch.Name] = records

            document.undo()
            process_events(16)
            assert _records(sketch) == before
            document.redo()
            process_events(16)
            assert _records(sketch) == records
            assert edit_boundary(document, sketch, controller) == boundary
            _phase(outcome)

        Gui.Selection.clearSelection(document.Name)
        Gui.activeDocument().resetEdit()
        process_events(16)
        save_path = Path(tempfile.mkdtemp(prefix="vibecad-native-sketch-split-")) / (
            "NativeSketchSplit.FCStd"
        )
        document.saveAs(str(save_path))
        document_name = document.Name
        App.closeDocument(document_name)
        document = App.openDocument(str(save_path))
        document.recompute()
        VibeGui._connect_document_observer()
        process_events(16)

        reopened_line = document.getObject("SplitLine")
        reopened_circle = document.getObject("SplitCircle")
        assert reopened_line is not None
        assert reopened_circle is not None
        verify_reopened_split(reopened_line, expected_records["SplitLine"])
        _verify_reopened_exact(
            reopened_circle,
            expected_records["SplitCircle"],
            (1, 0),
        )
        circle_record = serialize_sketch_geometry(reopened_circle, 0)
        assert circle_record["kind"] == "circular_arc"
        assert circle_record["periodic"] is False
        assert circle_record["first_parameter"] == 0.0
        assert math.isclose(
            circle_record["last_parameter"],
            2.0 * math.pi,
            rel_tol=0.0,
            abs_tol=1.0e-12,
        )
        print(
            "VIBECAD_NATIVE_SKETCH_SPLIT_GUI_OK line=2/1 circle=1/0 targets=2",
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
