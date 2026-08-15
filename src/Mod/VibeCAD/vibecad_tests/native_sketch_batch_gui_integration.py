# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI atomicity, solver, undo, and FCStd gate for Native Sketch batch."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets
import Sketcher  # noqa: F401 - registers Sketcher document types

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADEditState import active_edit_object
from VibeCADNativeCapabilityRegistry import (
    MAX_NATIVE_SCHEMAS_JSON_BYTES,
    resolve_native_provider_surface,
)
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSketchBatchBindings import SKETCH_BATCH_CAPABILITY_NAME
from VibeCADNativeSketchState import serialize_sketch_diagnostics
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface
from vibecad_tests.native_sketch_batch_test_support import (
    constrained_rectangle_arguments,
)
from vibecad_tests.native_sketch_geometry_gui_support import (
    edit_boundary,
    process_events,
)


def _mixed_batch_arguments(sketch) -> dict:
    def point(geometry_ref: str, position: str) -> dict:
        return {
            "geometry_ref": geometry_ref,
            "position": position,
        }

    def line(ref: str, start: tuple[float, float], end: tuple[float, float]) -> dict:
        return {
            "ref": ref,
            "kind": "line",
            "construction": False,
            "start_mm": {"x": start[0], "y": start[1]},
            "end_mm": {"x": end[0], "y": end[1]},
        }

    return {
        "operation": "create",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": 4,
        "expected_constraint_count": 11,
        "geometry": [
            line("parallel_a", (0.0, 40.0), (10.0, 40.0)),
            line("parallel_b", (0.0, 46.0), (8.0, 48.0)),
            line("perpendicular", (15.0, 40.0), (17.0, 50.0)),
            line("equal_length", (20.0, 40.0), (24.0, 40.0)),
            line("angle_a", (30.0, 40.0), (38.0, 40.0)),
            line("angle_b", (30.0, 45.0), (37.0, 52.0)),
            {
                "ref": "circle",
                "kind": "circle",
                "construction": False,
                "center_mm": {"x": 50.0, "y": 45.0},
                "radius_mm": 3.0,
            },
            {
                "ref": "arc",
                "kind": "arc",
                "construction": False,
                "center_mm": {"x": 65.0, "y": 45.0},
                "radius_mm": 4.0,
                "start_angle_degrees": 0.0,
                "sweep_angle_degrees": 120.0,
            },
            {
                "ref": "point",
                "kind": "point",
                "construction": False,
                "position_mm": {"x": 80.0, "y": 45.0},
            },
        ],
        "constraints": [
            {
                "ref": "parallel",
                "kind": "parallel",
                "first_geometry_ref": "parallel_a",
                "second_geometry_ref": "parallel_b",
            },
            {
                "ref": "perpendicular",
                "kind": "perpendicular",
                "first_geometry_ref": "parallel_a",
                "second_geometry_ref": "perpendicular",
            },
            {
                "ref": "equal",
                "kind": "equal",
                "first_geometry_ref": "parallel_a",
                "second_geometry_ref": "equal_length",
            },
            {
                "ref": "angle",
                "kind": "angle",
                "first_geometry_ref": "angle_a",
                "second_geometry_ref": "angle_b",
                "value_degrees": 45.0,
            },
            {
                "ref": "radius",
                "kind": "radius",
                "geometry_ref": "circle",
                "value_mm": 4.0,
            },
            {
                "ref": "diameter",
                "kind": "diameter",
                "geometry_ref": "arc",
                "value_mm": 10.0,
            },
            {
                "ref": "distance",
                "kind": "distance",
                "first": {"origin": True},
                "second": point("point", "point"),
                "value_mm": 100.0,
            },
        ],
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeSketchBatchGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        sketch = document.addObject("Sketcher::SketchObject", "BatchSketch")
        sketch.Label = "One-call constrained Native profile"
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

        registry = build_native_capability_registry()
        production = resolve_native_provider_surface(surface, registry)
        assert production.available is True
        assert SKETCH_BATCH_CAPABILITY_NAME in production.tool_names
        schema_bytes = len(
            json.dumps(
                list(production.schemas),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        assert schema_bytes <= MAX_NATIVE_SCHEMAS_JSON_BYTES

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-sketch-batch-gui")

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
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )

        def native_call(arguments, call_id: str, *, succeeds: bool) -> dict:
            response = dispatcher.call(
                SKETCH_BATCH_CAPABILITY_NAME,
                json.dumps(arguments, separators=(",", ":")),
                call_id,
            )
            assert response.get("ok") is succeeds, response
            assert edit_boundary(document, sketch, controller) == boundary
            return response

        invalid_ref = constrained_rectangle_arguments(sketch)
        invalid_ref["constraints"][0]["first"]["geometry_ref"] = "missing"
        before_invalid = (
            int(sketch.GeometryCount),
            int(sketch.ConstraintCount),
            int(document.UndoCount),
            int(document.RedoCount),
        )
        invalid = native_call(invalid_ref, "batch-invalid-ref", succeeds=False)
        assert invalid["error_code"] == "NATIVE_SKETCH_INVALID"
        assert "unknown geometry" in invalid["error"]
        assert before_invalid == (
            int(sketch.GeometryCount),
            int(sketch.ConstraintCount),
            int(document.UndoCount),
            int(document.RedoCount),
        )

        redundant = constrained_rectangle_arguments(sketch)
        redundant["constraints"].append(
            {
                "ref": "duplicate_bottom_horizontal",
                "kind": "horizontal",
                "geometry_ref": "bottom",
            }
        )
        rejected = native_call(redundant, "batch-redundant", succeeds=False)
        assert rejected["error_code"] == "NATIVE_SKETCH_INVALID"
        assert "redundant" in rejected["error"]
        assert before_invalid == (
            int(sketch.GeometryCount),
            int(sketch.ConstraintCount),
            int(document.UndoCount),
            int(document.RedoCount),
        )

        arguments = constrained_rectangle_arguments(sketch)
        undo_before = int(document.UndoCount)
        response = native_call(arguments, "batch-success", succeeds=True)
        assert response["geometry_count"] == 4
        assert response["constraint_count"] == 11
        assert response["degenerate_geometry_refs"] == []
        assert [item["local_ref"] for item in response["geometry_refs"]] == [
            "bottom",
            "right",
            "top",
            "left",
        ]
        assert [item["geometry_index"] for item in response["geometry_refs"]] == [
            0,
            1,
            2,
            3,
        ]
        assert all("geometry_id" in item for item in response["geometry_refs"])
        assert [
            item["constraint_index"] for item in response["constraint_refs"]
        ] == list(range(11))
        assert response["profile"]["closed_profile"] is True
        assert response["profile"]["closed_wire_count"] == 1
        assert response["profile"]["open_wire_count"] == 0
        assert response["solver"]["degrees_of_freedom"] == 0
        assert response["solver"]["fully_constrained"] is True
        assert response["solver"]["conflicting_constraints"] == []
        assert response["solver"]["redundant_constraints"] == []
        assert response["solver"]["partially_redundant_constraints"] == []
        assert response["solver"]["malformed_constraints"] == []
        assert response["solver"]["valid"] is True
        assert response["assistant_undo_available"] is True
        assert len(response["receipt"]["changed"]) == 1
        assert response["receipt"]["changed"][0]["object_name"] == sketch.Name
        assert int(document.UndoCount) == undo_before + 1
        assert document.UndoNames[0] == "Create Native Sketch Batch"

        duplicate = native_call(arguments, "batch-success", succeeds=True)
        assert duplicate == response
        assert int(sketch.GeometryCount) == 4
        assert int(sketch.ConstraintCount) == 11
        assert int(document.UndoCount) == undo_before + 1

        document.undo()
        process_events(16)
        assert active_edit_object() is sketch
        assert int(sketch.GeometryCount) == 0
        assert int(sketch.ConstraintCount) == 0
        document.redo()
        process_events(16)
        assert active_edit_object() is sketch
        assert int(sketch.GeometryCount) == 4
        assert int(sketch.ConstraintCount) == 11
        diagnostics = serialize_sketch_diagnostics(sketch)
        assert diagnostics["profile"]["closed_profile"] is True
        assert diagnostics["solver"]["degrees_of_freedom"] == 0
        assert diagnostics["solver"]["fully_constrained"] is True
        assert edit_boundary(document, sketch, controller) == boundary

        mixed_undo_before = int(document.UndoCount)
        mixed = native_call(
            _mixed_batch_arguments(sketch),
            "batch-mixed-catalog",
            succeeds=True,
        )
        assert mixed["geometry_count"] == 13
        assert mixed["constraint_count"] == 18
        assert [item["kind"] for item in mixed["geometry_refs"]] == [
            "line",
            "line",
            "line",
            "line",
            "line",
            "line",
            "circle",
            "arc",
            "point",
        ]
        assert [item["type"] for item in mixed["constraint_refs"]] == [
            "Parallel",
            "Perpendicular",
            "Equal",
            "Angle",
            "Radius",
            "Diameter",
            "Distance",
        ]
        assert mixed["solver"]["conflicting_constraints"] == []
        assert mixed["solver"]["redundant_constraints"] == []
        assert mixed["solver"]["malformed_constraints"] == []
        assert int(document.UndoCount) == mixed_undo_before + 1
        assert document.UndoNames[0] == "Create Native Sketch Batch"
        document.undo()
        process_events(16)
        assert int(sketch.GeometryCount) == 4
        assert int(sketch.ConstraintCount) == 11
        diagnostics = serialize_sketch_diagnostics(sketch)
        assert diagnostics["profile"]["closed_profile"] is True
        assert diagnostics["solver"]["degrees_of_freedom"] == 0

        Gui.activeDocument().resetEdit()
        process_events(16)
        save_path = (
            Path(tempfile.mkdtemp(prefix="vibecad-native-sketch-batch-"))
            / "NativeSketchBatch.FCStd"
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
        assert int(sketch.GeometryCount) == 4
        assert int(sketch.ConstraintCount) == 11
        reopened = serialize_sketch_diagnostics(sketch)
        assert reopened["profile"]["closed_profile"] is True
        assert reopened["solver"]["degrees_of_freedom"] == 0
        assert reopened["solver"]["fully_constrained"] is True
        print(
            "VIBECAD_NATIVE_SKETCH_BATCH_GUI_OK "
            f"schema_bytes={schema_bytes} profile_mutations=1 "
            "catalog_mutations=1 geometry=4 constraints=11",
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
