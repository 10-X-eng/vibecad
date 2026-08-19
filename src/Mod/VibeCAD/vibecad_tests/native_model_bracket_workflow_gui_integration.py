# SPDX-License-Identifier: LGPL-2.1-or-later

"""Clean-profile end-to-end gate for the complete Native Model bracket flow."""

from __future__ import annotations

import json
import math
from pathlib import Path
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
import PartDesign
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeModelSnapshot import build_model_snapshot
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeSessionFactory import create_native_session_execution
from VibeCADNativeSurface import SURFACE_CHANGED
from VibeCADNativeTurn import freeze_native_turn
from VibeCADRibbonSurface import read_active_ribbon_surface


def _process_events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _new_execution(service, controller):
    registry = build_native_capability_registry()
    turn = freeze_native_turn(controller, registry)
    assert turn.surface.surface_id == "model"
    schemas = list(turn.provider_schemas)
    execution = create_native_session_execution(
        service=service,
        expected_surface={
            "kind": "turn_start_snapshot",
            "frozen": True,
            "engine": "native",
            "domain": "model",
            "surface_id": turn.surface.modeling_surface_id,
            "tool_names": list(turn.tool_names),
            "schema_count": len(schemas),
            "schema_sha256": turn.schema_sha256,
        },
        expected_schemas=schemas,
        registry=registry,
        controller=controller,
    )
    assert execution.turn.surface.authorization_token == turn.surface.authorization_token
    return execution


def _extrude_arguments(sketch_name: str, component_name: str):
    return {
        "label": "Bracket Base Extrude",
        "profile": {"object_name": sketch_name},
        "destination_component": {"object_name": component_name},
        "feature": {
            "kind": "extrude",
            "direction": {"kind": "sketch_normal"},
            "extent": {
                "kind": "one_side",
                "sides": [
                    {
                        "kind": "length",
                        "length_mm": 8.0,
                        "taper_degrees": 0.0,
                    }
                ],
            },
        },
    }


def _hole_arguments(sketch_name: str, body_name: str):
    return {
        "operation": "hole",
        "label": "Bracket Mounting Hole",
        "profile": {"object_name": sketch_name},
        "base_profile": "circles_and_arcs",
        "hole_type": {"kind": "plain", "diameter_mm": 6.0},
        "head": {"kind": "none"},
        "depth": {"kind": "through_all"},
        "drill_point": {"kind": "flat"},
        "taper": {"kind": "straight"},
        "reversed": False,
        "targets": [{"object_name": body_name}],
    }


def _pattern_arguments(hole_name: str, body_name: str):
    return {
        "operation": "pattern",
        "label": "Bracket Hole Pattern",
        "source": {
            "kind": "feature",
            "operation": {"object_name": hole_name},
            "targets": [{"object_name": body_name}],
        },
        "definition": {
            "kind": "linear",
            "direction": {
                "kind": "explicit",
                "vector": {"x": 1.0, "y": 0.0, "z": 0.0},
            },
            "spacing_mm": 20.0,
            "occurrences": 3,
            "centered": False,
        },
    }


def _rectangle_geometry():
    return [
        Part.LineSegment(App.Vector(-30, -15, 0), App.Vector(30, -15, 0)),
        Part.LineSegment(App.Vector(30, -15, 0), App.Vector(30, 15, 0)),
        Part.LineSegment(App.Vector(30, 15, 0), App.Vector(-30, 15, 0)),
        Part.LineSegment(App.Vector(-30, 15, 0), App.Vector(-30, -15, 0)),
    ]


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    executions = []
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeModelBracketWorkflowGate")
        VibeGui._connect_document_observer()
        _process_events()

        main_window = Gui.getMainWindow()
        controller = main_window.findChild(
            QtCore.QObject,
            "VibeCADRibbonController",
        )
        assert controller is not None
        assert read_active_ribbon_surface(controller).surface_id == "model"

        service = get_service()
        service.select_modeling_engine("native")
        call_number = 0

        def native_call(execution, tool_name, arguments, *, succeeds=True):
            nonlocal call_number
            call_number += 1
            response = execution.dispatcher.call(
                tool_name,
                json.dumps(arguments, separators=(",", ":")),
                f"model-bracket-workflow-call-{call_number}",
            )
            assert response.get("ok") is succeeds, (arguments, response)
            return response

        def begin_model_turn():
            execution = _new_execution(service, controller)
            executions.append(execution)
            return execution

        def human_edits_sketch(execution, sketch, geometries):
            assert Gui.activeDocument().setEdit(sketch.Name)
            _process_events()
            assert Gui.activeWorkbench().name() == "SketcherWorkbench"
            assert read_active_ribbon_surface(controller).surface_id == "sketch.edit"

            blocked = native_call(
                execution,
                "sketch.validate",
                {
                    "operation": "validate_sketch",
                    "target": {"object_name": sketch.Name},
                },
                succeeds=False,
            )
            assert blocked["error_code"] == SURFACE_CHANGED
            assert blocked["current_surface"] == "sketch.edit"

            sketch.addGeometry(list(geometries), False)
            document.recompute([sketch], True, True)
            assert sketch.isValid()
            Gui.activeDocument().resetEdit()
            _process_events()
            assert Gui.activeWorkbench().name() == "PartDesignWorkbench"
            assert read_active_ribbon_surface(controller).surface_id == "model"

            still_blocked = native_call(
                execution,
                "sketch.validate",
                {
                    "operation": "validate_sketch",
                    "target": {"object_name": sketch.Name},
                },
                succeeds=False,
            )
            assert still_blocked["error_code"] == SURFACE_CHANGED
            PartDesign.validateDesign(sketch)
            execution.close()

        first_turn = begin_model_turn()
        component_result = native_call(
            first_turn,
            "model.structure",
            {
                "operation": "new_component",
                "label": "Native Mounting Bracket",
                "parent_component": None,
            },
        )
        component_name = component_result["component"]["object_name"]
        base_sketch_result = native_call(
            first_turn,
            "model.sketch",
            {
                "operation": "create_on_base_plane",
                "label": "Bracket Base Profile",
                "plane": "XY",
                "offset_mm": 0.0,
            },
        )
        base_sketch_name = base_sketch_result["sketch"]["object_name"]
        base_sketch = document.getObject(base_sketch_name)
        assert base_sketch_result["entered_edit_mode"] is False
        assert base_sketch_result["next_step"] == {
            "tool": "sketch.open",
            "arguments": {
                "operation": "open",
                "sketch": {"object_name": base_sketch_name},
            },
        }
        human_edits_sketch(first_turn, base_sketch, _rectangle_geometry())

        second_turn = begin_model_turn()
        base_summary = next(
            item
            for item in build_model_snapshot(document)["sketches"]
            if item["object_name"] == base_sketch_name
        )
        assert base_summary["valid"] is True
        assert base_summary["solid_feature_ready"] is True
        assert base_summary["profile"] == {
            "wire_count": 1,
            "closed_wire_count": 1,
            "open_wire_count": 0,
            "edge_count": 4,
        }
        base_readiness = native_call(
            second_turn,
            "sketch.validate",
            {
                "operation": "validate_sketch",
                "target": {"object_name": base_sketch_name},
            },
        )
        assert base_readiness["solid_feature_ready"] is True
        assert base_readiness["profile"]["closed_wire_count"] == 1

        extrude_result = native_call(
            second_turn,
            "model.feature",
            _extrude_arguments(base_sketch_name, component_name),
        )
        extrude_name = extrude_result["operation"]["object_name"]
        body_name = extrude_result["bodies"][0]["body"]["object_name"]
        body = document.getObject(body_name)
        component = document.getObject(component_name)
        assert body in list(component.Group)
        assert abs(float(body.Shape.Volume) - 14_400.0) < 1.0e-6

        hole_sketch_result = native_call(
            second_turn,
            "model.sketch",
            {
                "operation": "create_on_base_plane",
                "label": "Bracket Hole Profile",
                "plane": "XY",
                "offset_mm": 8.0,
            },
        )
        hole_sketch_name = hole_sketch_result["sketch"]["object_name"]
        hole_sketch = document.getObject(hole_sketch_name)
        human_edits_sketch(
            second_turn,
            hole_sketch,
            [Part.Circle(App.Vector(-20, 0, 0), App.Vector(0, 0, 1), 3.0)],
        )

        final_turn = begin_model_turn()
        hole_readiness = native_call(
            final_turn,
            "sketch.validate",
            {
                "operation": "validate_sketch",
                "target": {"object_name": hole_sketch_name},
            },
        )
        assert hole_readiness["solid_feature_ready"] is True
        before_hole_volume = float(body.Shape.Volume)
        hole_result = native_call(
            final_turn,
            "model.hole",
            _hole_arguments(hole_sketch_name, body_name),
        )
        hole_name = hole_result["operation"]["object_name"]
        after_hole_volume = float(body.Shape.Volume)
        assert hole_result["result_mode"] == "cut"
        one_hole_volume = math.pi * 3.0**2 * 8.0
        assert abs(after_hole_volume - (before_hole_volume - one_hole_volume)) < 1.0e-6

        pattern_result = native_call(
            final_turn,
            "model.transform",
            _pattern_arguments(hole_name, body_name),
        )
        pattern_name = pattern_result["operation"]["object_name"]
        pattern = document.getObject(pattern_name)
        final_volume = float(body.Shape.Volume)
        assert pattern_result["result_mode"] == "cut"
        assert int(pattern.GeneratedOccurrenceCount) == 2
        assert pattern.SourceOperation is document.getObject(hole_name)
        assert abs(final_volume - (before_hole_volume - 3.0 * one_hole_volume)) < 1.0e-6
        PartDesign.validateDesign(document.getObject(extrude_name))
        PartDesign.validateDesign(document.getObject(hole_name))
        PartDesign.validateDesign(pattern)

        document.undo()
        _process_events()
        assert document.getObject(pattern_name) is None
        assert abs(float(body.Shape.Volume) - after_hole_volume) < 1.0e-6
        document.redo()
        _process_events()
        assert document.getObject(pattern_name) is not None
        assert abs(float(body.Shape.Volume) - final_volume) < 1.0e-6

        body_id = str(body.VibeCADBodyId)
        operation_ids = {
            name: str(document.getObject(name).OperationId)
            for name in (extrude_name, hole_name, pattern_name)
        }
        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-bracket-"))
        save_path = save_directory / "NativeBracket.FCStd"
        document.saveAs(str(save_path))
        saved_name = document.Name
        final_turn.close()
        App.closeDocument(saved_name)
        document = App.openDocument(str(save_path))
        document.recompute()
        _process_events()

        reopened_body = document.getObject(body_name)
        reopened_component = document.getObject(component_name)
        assert str(reopened_body.VibeCADBodyId) == body_id
        assert reopened_body in list(reopened_component.Group)
        assert abs(float(reopened_body.Shape.Volume) - final_volume) < 1.0e-6
        for name, operation_id in operation_ids.items():
            operation = document.getObject(name)
            assert str(operation.OperationId) == operation_id
            PartDesign.validateDesign(operation)
        assert Gui.activeDocument().getInEdit() is None
        assert not bool(Gui.Control.activeDialog())

        print("VIBECAD_NATIVE_MODEL_BRACKET_WORKFLOW_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        for execution in executions:
            try:
                execution.close()
            except Exception:
                pass
        if Gui.activeDocument() and Gui.activeDocument().getInEdit():
            Gui.activeDocument().resetEdit()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
