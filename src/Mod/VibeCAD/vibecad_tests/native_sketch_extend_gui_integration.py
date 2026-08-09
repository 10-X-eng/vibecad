# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused real-GUI transaction and FCStd lifecycle gate for Sketch Extend."""

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
from vibecad_tests.native_sketch_extend_gui_case import verify_reopened_extend
from vibecad_tests.native_sketch_geometry_gui_support import (
    edit_boundary,
    process_events,
    provider_turn,
)


def _phase(name: str) -> None:
    os.write(2, f"VIBECAD_NATIVE_SKETCH_EXTEND_PHASE {name}\n".encode("ascii"))


def _arguments(
    sketch,
    point: tuple[float, float],
    endpoint: str,
    *,
    expected_geometry_count: int | None = None,
) -> dict:
    return {
        "operation": "extend",
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
            "endpoint": endpoint,
            "target_point_mm": {"x": point[0], "y": point[1]},
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
    sketch = document.addObject("Sketcher::SketchObject", "ExtendLine")
    sketch.Label = "Native Extend line-start lifecycle"
    assert (
        sketch.addGeometry(
            Part.LineSegment(App.Vector(0, 0), App.Vector(20, 0)),
            True,
        )
        == 0
    )
    return sketch


def _make_arc_sketch(document):
    sketch = document.addObject("Sketcher::SketchObject", "ExtendArc")
    sketch.Label = "Native Extend arc-end lifecycle"
    circle = Part.Circle(App.Vector(0, 0), App.Vector(0, 0, 1), 10)
    assert sketch.addGeometry(Part.ArcOfCircle(circle, 0.0, 1.0), True) == 0
    return sketch


def _assert_point(observed: dict, expected: tuple[float, float]) -> None:
    assert math.isclose(float(observed["x"]), expected[0], rel_tol=0.0, abs_tol=1.0e-8)
    assert math.isclose(float(observed["y"]), expected[1], rel_tol=0.0, abs_tol=1.0e-8)


def _verify_reopened_exact(sketch, expected) -> None:
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (1, 0)
    observed = _records(sketch)
    assert observed["constraints"] == expected["constraints"]
    saved = dict(expected["geometry"][0])
    reopened = dict(observed["geometry"][0])
    assert saved.pop("tag", "")
    assert reopened.pop("tag", "")
    assert reopened == saved


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeSketchExtendGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        selection_anchor = document.addObject("Part::Feature", "SelectionAnchor")
        selection_anchor.Shape = Part.makeLine(App.Vector(-5, -10), App.Vector(25, -10))
        arc_angle = 0.75
        arc_point = (10.0 * math.cos(arc_angle), 10.0 * math.sin(arc_angle))
        cases = (
            (
                _make_line_sketch(document),
                (-5.0, 3.0),
                "start",
                1,
                "extended",
                5.0,
                (-5.0, 0.0),
            ),
            (
                _make_arc_sketch(document),
                arc_point,
                "end",
                2,
                "shortened",
                -0.25,
                arc_point,
            ),
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
        ledger.begin_run("native-sketch-extend-gui")
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
            endpoint,
            endpoint_value,
            outcome,
            increment,
            new_endpoint,
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
            assert "Sketcher_Extend" not in production.missing_action_ids
            assert SKETCH_GEOMETRY_CAPABILITY_NAME in (
                production.incomplete_definition_names
            )

            def native_call(arguments, *, succeeds=True, suffix="call"):
                response = dispatcher.call(
                    SKETCH_GEOMETRY_CAPABILITY_NAME,
                    json.dumps(arguments, separators=(",", ":")),
                    f"extend-{case_number}-{suffix}",
                )
                assert response.get("ok") is succeeds, response
                assert edit_boundary(document, active_sketch, controller) == boundary
                return response

            before = _records(sketch)
            undo_before = int(document.UndoCount)
            diagnosis = sketch.diagnoseExtend(
                0,
                App.Vector(point[0], point[1], 0.0),
                endpoint_value,
            )
            assert diagnosis["accepted"] is True
            assert diagnosis["input_endpoint"] == endpoint
            assert math.isclose(
                float(diagnosis["extension_increment"]),
                increment,
                rel_tol=0.0,
                abs_tol=1.0e-10,
            )
            assert diagnosis["geometry_count"] == 1
            assert diagnosis["constraint_count"] == 0
            geometry_receipt = diagnosis["mutation_receipt"]["geometry"]
            assert geometry_receipt["old_to_new"] == {"0": 0}
            assert geometry_receipt["deleted"] == []
            assert geometry_receipt["created"] == []
            assert _records(sketch) == before
            assert int(document.UndoCount) == undo_before

            stale = native_call(
                _arguments(
                    sketch,
                    point,
                    endpoint,
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
            response = native_call(_arguments(sketch, point, endpoint))
            assert response["operation"] == "extend"
            assert response["outcome"] == outcome
            assert response["geometry_index"] == 0
            assert response["endpoint"] == endpoint
            _assert_point(response["target_point_mm"], point)
            _assert_point(response["new_endpoint_mm"], new_endpoint)
            assert response["changed_geometry_indices"] == [0]
            assert (response["geometry_count"], response["constraint_count"]) == (
                1,
                0,
            )
            assert serialize_sketch_geometry(sketch, 0)["construction"] is True
            assert _selection(document) == selected
            assert int(document.UndoCount) == undo_before + 1
            assert document.UndoNames[0] == "Extend Native Sketch Geometry"
            records = _records(sketch)
            expected_records[sketch.Name] = records

            document.undo()
            process_events(16)
            undone = _records(sketch)
            assert undone == before, {"before": before, "after_undo": undone}
            document.redo()
            process_events(16)
            redone = _records(sketch)
            assert redone == records, {"expected": records, "after_redo": redone}
            assert edit_boundary(document, sketch, controller) == boundary
            _phase(outcome)

        Gui.Selection.clearSelection(document.Name)
        Gui.activeDocument().resetEdit()
        process_events(16)
        save_path = Path(tempfile.mkdtemp(prefix="vibecad-native-sketch-extend-")) / (
            "NativeSketchExtend.FCStd"
        )
        document.saveAs(str(save_path))
        document_name = document.Name
        App.closeDocument(document_name)
        document = App.openDocument(str(save_path))
        document.recompute()
        VibeGui._connect_document_observer()
        process_events(16)

        reopened_line = document.getObject("ExtendLine")
        reopened_arc = document.getObject("ExtendArc")
        assert reopened_line is not None
        assert reopened_arc is not None
        verify_reopened_extend(reopened_line, expected_records["ExtendLine"])
        _verify_reopened_exact(reopened_arc, expected_records["ExtendArc"])
        arc_record = serialize_sketch_geometry(reopened_arc, 0)
        assert arc_record["kind"] == "circular_arc"
        assert arc_record["construction"] is True
        assert math.isclose(
            float(arc_record["first_parameter"]),
            0.0,
            rel_tol=0.0,
            abs_tol=1.0e-12,
        )
        assert math.isclose(
            float(arc_record["last_parameter"]),
            arc_angle,
            rel_tol=0.0,
            abs_tol=1.0e-12,
        )
        print(
            "VIBECAD_NATIVE_SKETCH_EXTEND_GUI_OK line=extended arc=shortened targets=2",
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
