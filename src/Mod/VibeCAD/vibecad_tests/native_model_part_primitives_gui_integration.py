# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real FreeCAD lifecycle gate for Native standalone Part primitives."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part  # noqa: F401 - registers standalone Part primitive types
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
import VibeCADNativeModelPartRuntime as runtime_module
from VibeCADCore import get_service
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeModelPartSchema import model_part_capability_definition
from VibeCADNativePartPrimitives import prepare_part_primitive
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger


def _process_events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _placement(
    x: float,
    y: float = 0.0,
    z: float = 0.0,
    *,
    axis=(0.0, 0.0, 1.0),
    angle: float = 0.0,
) -> dict[str, object]:
    return {
        "origin_mm": {"x": x, "y": y, "z": z},
        "rotation": {
            "axis": {"x": axis[0], "y": axis[1], "z": axis[2]},
            "angle_degrees": angle,
        },
    }


def _native_placement(value: dict[str, object]) -> App.Placement:
    origin = value["origin_mm"]
    rotation = value["rotation"]
    axis = rotation["axis"]
    return App.Placement(
        App.Vector(origin["x"], origin["y"], origin["z"]),
        App.Rotation(
            App.Vector(axis["x"], axis["y"], axis["z"]),
            rotation["angle_degrees"],
        ),
    )


def _placement_signature(value) -> tuple[float, ...]:
    return (
        float(value.Base.x),
        float(value.Base.y),
        float(value.Base.z),
        *(float(component) for component in value.Rotation.Q),
    )


def _turn() -> NativeTurnSnapshot:
    definition = model_part_capability_definition()
    surface = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot(
            "model",
            1,
            "p" * 64,
            ("Part_Primitives",),
            (),
            (),
        ),
        available=True,
        unavailable_reason="",
        tool_names=(definition.name,),
        schemas=(definition.provider_schema(("primitive",)),),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(surface)


def _cases() -> tuple[dict[str, object], ...]:
    return (
        {
            "label": "Gate Plane",
            "placement": _placement(0.0),
            "definition": {"kind": "plane", "length_mm": 12.0, "width_mm": 8.0},
        },
        {
            "label": "Gate Right Helix",
            "placement": _placement(25.0),
            "definition": {
                "kind": "helix",
                "pitch_mm": 2.0,
                "height_mm": 8.0,
                "radius_mm": 3.0,
                "taper_degrees": 0.0,
                "handedness": "right",
            },
        },
        {
            "label": "Gate Left Tapered Helix",
            "placement": _placement(50.0),
            "definition": {
                "kind": "helix",
                "pitch_mm": 2.5,
                "height_mm": 7.5,
                "radius_mm": 2.5,
                "taper_degrees": 5.0,
                "handedness": "left",
            },
        },
        {
            "label": "Gate Spiral",
            "placement": _placement(75.0),
            "definition": {
                "kind": "spiral",
                "growth_mm": 1.25,
                "rotations": 2.5,
                "radius_mm": 1.0,
            },
        },
        {
            "label": "Gate Circle Arc",
            "placement": _placement(100.0),
            "definition": {
                "kind": "circle",
                "radius_mm": 4.0,
                "start_degrees": 15.0,
                "end_degrees": 270.0,
            },
        },
        {
            "label": "Gate Full Circle",
            "placement": _placement(125.0),
            "definition": {
                "kind": "circle",
                "radius_mm": 3.0,
                "start_degrees": 0.0,
                "end_degrees": 360.0,
            },
        },
        {
            "label": "Gate Ellipse Arc",
            "placement": _placement(150.0),
            "definition": {
                "kind": "ellipse",
                "major_radius_mm": 5.0,
                "minor_radius_mm": 2.5,
                "start_degrees": 20.0,
                "end_degrees": 300.0,
            },
        },
        {
            "label": "Gate Point",
            "placement": _placement(175.0, 2.0, 1.0),
            "definition": {"kind": "point", "x_mm": 1.0, "y_mm": 2.0, "z_mm": 3.0},
        },
        {
            "label": "Gate Line",
            "placement": _placement(200.0, axis=(0.0, 1.0, 0.0), angle=30.0),
            "definition": {
                "kind": "line",
                "start_x_mm": 0.0,
                "start_y_mm": 0.0,
                "start_z_mm": 0.0,
                "end_x_mm": 4.0,
                "end_y_mm": 3.0,
                "end_z_mm": 2.0,
            },
        },
        {
            "label": "Gate Regular Polygon",
            "placement": _placement(225.0, angle=22.5),
            "definition": {
                "kind": "regular_polygon",
                "sides": 7,
                "circumradius_mm": 5.0,
            },
        },
    )


def _property_value(obj, name: str):
    value = getattr(obj, name)
    if name in {"LocalCoord", "Style"}:
        return str(value)
    return float(getattr(value, "Value", value))


def _assert_native_parameters(obj, parameters: dict[str, object]) -> None:
    enum_values = {
        "LocalCoord": {0: "Right-handed", 1: "Left-handed"},
        "Style": {1: "New style"},
    }
    for name, expected in parameters.items():
        actual = _property_value(obj, name)
        if name in enum_values:
            assert actual == enum_values[name][int(expected)]
        else:
            assert abs(actual - float(expected)) < 1.0e-8, (name, actual, expected)


def _assert_live_human_choices(document) -> None:
    before = tuple(obj.Name for obj in document.Objects)
    Gui.runCommand("Part_Primitives", 0)
    _process_events(32)
    combo_boxes = [
        combo
        for combo in Gui.getMainWindow().findChildren(QtWidgets.QComboBox)
        if combo.objectName() == "PrimitiveTypeCB"
    ]
    assert len(combo_boxes) == 1
    combo = combo_boxes[0]
    choices = tuple(combo.itemText(index) for index in range(combo.count()))
    assert choices == (
        "Plane",
        "Helix",
        "Spiral",
        "Circle",
        "Ellipse",
        "Point",
        "Line",
        "Regular polygon",
    )
    Gui.Control.closeDialog()
    _process_events(16)
    assert tuple(obj.Name for obj in document.Objects) == before


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeModelPartPrimitivesGate")
        VibeGui._connect_document_observer()
        _process_events()
        _assert_live_human_choices(document)

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-part-primitives-gui")
        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state,
            undo_ledger=ledger,
            reauthorize_turn=lambda: None,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: "model",
            edit_or_task_active=lambda: False,
        )
        turn = _turn()
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state,
            registry=build_native_capability_registry(),
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=lambda: None,
            active_document=lambda: App.ActiveDocument,
        )
        call_number = 0

        def native_call(arguments, *, succeeds=True):
            nonlocal call_number
            call_number += 1
            response = dispatcher.call(
                "model.part",
                json.dumps(arguments, separators=(",", ":")),
                f"model-part-primitive-call-{call_number}",
            )
            assert response.get("ok") is succeeds, response
            return response

        before_invalid = tuple(obj.Name for obj in document.Objects)
        invalid_schema = native_call(
            {
                "operation": "primitive",
                "label": "Missing Placement",
                "definition": {"kind": "plane", "length_mm": 2.0, "width_mm": 2.0},
            },
            succeeds=False,
        )
        assert invalid_schema["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before_invalid

        records = []
        for case in _cases():
            arguments = {"operation": "primitive", **case}
            response = native_call(arguments)
            kind = case["definition"]["kind"]
            metric = "area_mm2" if kind == "plane" else "length_mm"
            expected_keys = {
                "ok",
                "object",
                "primitive_kind",
                "shape_type",
                "vertex_count",
                "edge_count",
                "face_count",
                "receipt",
                "assistant_undo_available",
            }
            if kind != "point":
                expected_keys.add(metric)
            assert set(response) == expected_keys, response
            assert response["primitive_kind"] == kind
            assert response["assistant_undo_available"] is True
            assert len(response["receipt"]["created"]) == 1
            assert response["receipt"]["changed"] == []
            assert response["receipt"]["deleted"] == []
            if kind != "point":
                assert response[metric] > 0.0

            object_name = response["object"]["object_name"]
            obj = document.getObject(object_name)
            assert obj is not None
            assert obj.Label == case["label"]
            assert obj.Placement == _native_placement(case["placement"])
            assert obj.getParentGeoFeatureGroup() is None
            assert obj.VibeCADTimelineRole == "operation"
            assert str(obj.VibeCADDefinitionId)
            assert str(obj.DesignId)
            spec = prepare_part_primitive(case["definition"])
            assert obj.TypeId == spec.type_id
            _assert_native_parameters(obj, spec.parameters)

            document.undo()
            _process_events()
            assert document.getObject(object_name) is None
            document.redo()
            _process_events()
            obj = document.getObject(object_name)
            assert obj is not None and obj.TypeId == spec.type_id
            assert obj.VibeCADTimelineRole == "operation"

            records.append(
                {
                    "name": object_name,
                    "label": case["label"],
                    "placement": _placement_signature(
                        _native_placement(case["placement"])
                    ),
                    "type_id": spec.type_id,
                    "kind": kind,
                    "parameters": spec.parameters,
                    "definition_id": str(obj.VibeCADDefinitionId),
                    "design_id": str(obj.DesignId),
                    "shape_type": response["shape_type"],
                    "topology": (
                        response["vertex_count"],
                        response["edge_count"],
                        response["face_count"],
                    ),
                }
            )

        assert records[1]["parameters"]["LocalCoord"] == 0
        assert records[2]["parameters"]["LocalCoord"] == 1
        assert records[4]["topology"][0] == 2
        assert records[5]["topology"][0] == 1

        invalid_definitions = (
            {
                "kind": "line",
                "start_x_mm": 1.0,
                "start_y_mm": 1.0,
                "start_z_mm": 1.0,
                "end_x_mm": 1.0,
                "end_y_mm": 1.0,
                "end_z_mm": 1.0,
            },
            {
                "kind": "ellipse",
                "major_radius_mm": 2.0,
                "minor_radius_mm": 3.0,
                "start_degrees": 0.0,
                "end_degrees": 180.0,
            },
        )
        for index, definition in enumerate(invalid_definitions):
            before = tuple(obj.Name for obj in document.Objects)
            failure = native_call(
                {
                    "operation": "primitive",
                    "label": f"Invalid Primitive {index}",
                    "placement": _placement(0.0),
                    "definition": definition,
                },
                succeeds=False,
            )
            assert failure["error_code"] == "NATIVE_MODEL_INVALID"
            assert tuple(obj.Name for obj in document.Objects) == before
            assert document.HasPendingTransaction is False

        before = tuple(obj.Name for obj in document.Objects)
        original_verify = runtime_module.verify_part_primitive

        def reject_after_creation(_document, _draft):
            raise NativeModelError("Forced exact postcondition failure.")

        runtime_module.verify_part_primitive = reject_after_creation
        try:
            rollback = native_call(
                {
                    "operation": "primitive",
                    "label": "Rollback Plane",
                    "placement": _placement(250.0),
                    "definition": {
                        "kind": "plane",
                        "length_mm": 3.0,
                        "width_mm": 2.0,
                    },
                },
                succeeds=False,
            )
        finally:
            runtime_module.verify_part_primitive = original_verify
        assert rollback["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert document.HasPendingTransaction is False

        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-part-primitives-"))
        save_path = save_directory / "ModelPartPrimitives.FCStd"
        document.saveAs(str(save_path))
        saved_name = document.Name
        App.closeDocument(saved_name)
        document = App.openDocument(str(save_path))
        document.recompute()
        _process_events()

        for record in records:
            obj = document.getObject(record["name"])
            assert obj is not None and obj.TypeId == record["type_id"]
            assert obj.Label == record["label"]
            assert all(
                abs(actual - expected) < 1.0e-10
                for actual, expected in zip(
                    _placement_signature(obj.Placement),
                    record["placement"],
                    strict=True,
                )
            )
            assert obj.getParentGeoFeatureGroup() is None
            assert obj.VibeCADTimelineRole == "operation"
            assert str(obj.VibeCADDefinitionId) == record["definition_id"]
            assert str(obj.DesignId) == record["design_id"]
            _assert_native_parameters(obj, record["parameters"])
            shape = obj.Shape
            assert not shape.isNull() and shape.isValid()
            assert shape.ShapeType == record["shape_type"]
            assert (
                len(shape.Vertexes),
                len(shape.Edges),
                len(shape.Faces),
            ) == record["topology"]

        print("VIBECAD_NATIVE_MODEL_PART_PRIMITIVES_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
