# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real FreeCAD lifecycle gate for Native Design primitive operations."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import PartDesign
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeModelFeatureSchema import model_feature_capability_definition
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
):
    return {
        "origin_mm": {"x": x, "y": y, "z": z},
        "rotation": {
            "axis": {"x": axis[0], "y": axis[1], "z": axis[2]},
            "angle_degrees": angle,
        },
    }


def _result(mode="new_body", targets=(), destination_component=None):
    return {
        "mode": mode,
        "targets": [{"object_name": name} for name in targets],
        "destination_component": (
            {"object_name": destination_component}
            if destination_component is not None
            else None
        ),
    }


def _provider_arguments(arguments):
    values = dict(arguments)
    internal_operation = str(values.pop("operation"))
    shared = {
        name: values.pop(name)
        for name in ("label", "placement", "result")
        if name in values
    }
    return {
        "operation": "primitive",
        **shared,
        "definition": {
            "kind": internal_operation.removeprefix("design_"),
            **values,
        },
    }


def _turn(definition):
    operations = tuple(variant.operation for variant in definition.variants)
    surface = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot(
            "model",
            1,
            "c" * 64,
            tuple(
                sorted(
                    action
                    for variant in definition.variants
                    for action in variant.action_ids
                )
            ),
            (),
            (),
        ),
        available=True,
        unavailable_reason="",
        tool_names=(definition.name,),
        schemas=(definition.provider_schema(operations),),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(surface)


def _primitive_cases(component_name):
    return (
        (
            "design_box",
            "Gate Box",
            _placement(0.0),
            _result(destination_component=component_name),
            {"length_mm": 10.0, "width_mm": 8.0, "height_mm": 6.0},
        ),
        (
            "design_cylinder",
            "Gate Cylinder",
            _placement(30.0),
            _result(),
            {"radius_mm": 5.0, "height_mm": 10.0, "sweep_degrees": 360.0},
        ),
        (
            "design_sphere",
            "Gate Sphere",
            _placement(60.0),
            _result(),
            {
                "radius_mm": 5.0,
                "latitude_start_degrees": -90.0,
                "latitude_end_degrees": 90.0,
                "sweep_degrees": 360.0,
            },
        ),
        (
            "design_cone",
            "Gate Cone",
            _placement(90.0),
            _result(),
            {
                "radius1_mm": 5.0,
                "radius2_mm": 2.0,
                "height_mm": 9.0,
                "sweep_degrees": 360.0,
            },
        ),
        (
            "design_ellipsoid",
            "Gate Ellipsoid",
            _placement(120.0),
            _result(),
            {
                "radius_x_mm": 5.0,
                "radius_y_mm": 4.0,
                "radius_z_mm": 3.0,
                "latitude_start_degrees": -90.0,
                "latitude_end_degrees": 90.0,
                "sweep_degrees": 360.0,
            },
        ),
        (
            "design_torus",
            "Gate Torus",
            _placement(150.0),
            _result(),
            {
                "major_radius_mm": 6.0,
                "minor_radius_mm": 2.0,
                "section_start_degrees": -180.0,
                "section_end_degrees": 180.0,
                "sweep_degrees": 360.0,
            },
        ),
        (
            "design_prism",
            "Gate Prism",
            _placement(180.0),
            _result(),
            {"sides": 6, "circumradius_mm": 5.0, "height_mm": 8.0},
        ),
        (
            "design_wedge",
            "Gate Wedge",
            _placement(210.0),
            _result(),
            {
                "xmin_mm": 0.0,
                "ymin_mm": 0.0,
                "zmin_mm": 0.0,
                "x2min_mm": 2.0,
                "z2min_mm": 2.0,
                "xmax_mm": 10.0,
                "ymax_mm": 7.0,
                "zmax_mm": 8.0,
                "x2max_mm": 8.0,
                "z2max_mm": 6.0,
            },
        ),
        (
            "design_tube",
            "Gate Tube",
            _placement(240.0, axis=(0.0, 1.0, 0.0), angle=30.0),
            _result(),
            {"outer_radius_mm": 5.0, "inner_radius_mm": 3.0, "height_mm": 8.0},
        ),
    )


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeModelPrimitivesGate")
        VibeGui._connect_document_observer()
        document.openTransaction("Create primitive gate Component")
        component = document.addObject("PartDesign::Component", "GateComponent")
        document.classifyProvisionalTimelineInternalObject(component)
        component.Label = "Gate Component"
        document.recompute()
        document.commitTransaction()
        _process_events()

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-primitives-gui")
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
        definition = model_feature_capability_definition()
        turn = _turn(definition)
        registry = build_native_capability_registry()
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state,
            registry=registry,
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
                "model.feature",
                json.dumps(_provider_arguments(arguments), separators=(",", ":")),
                f"model-primitive-call-{call_number}",
            )
            assert response.get("ok") is succeeds, response
            return response

        def undo_redo_created(operation_name, body_name):
            document.undo()
            _process_events()
            assert document.getObject(operation_name) is None
            assert document.getObject(body_name) is None
            document.redo()
            _process_events()
            assert document.getObject(operation_name) is not None
            assert document.getObject(body_name) is not None

        before_invalid = tuple(obj.Name for obj in document.Objects)
        invalid_schema = native_call(
            {
                "operation": "design_box",
                "label": "Missing Placement",
                "result": _result(),
                "length_mm": 1.0,
                "width_mm": 1.0,
                "height_mm": 1.0,
            },
            succeeds=False,
        )
        assert invalid_schema["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before_invalid

        records = {}
        for operation, label, placement, result, geometry in _primitive_cases(
            component.Name
        ):
            response = native_call(
                {
                    "operation": operation,
                    "label": label,
                    "placement": placement,
                    "result": result,
                    **geometry,
                }
            )
            assert response["result_mode"] == "new_body"
            assert response["assistant_undo_available"] is True
            assert len(response["receipt"]["created"]) == 2
            assert len(response["bodies"]) == 1
            assert response["bodies"][0]["solid_count"] == 1
            assert response["bodies"][0]["volume_mm3"] > 0.0
            operation_name = response["operation"]["object_name"]
            body_name = response["bodies"][0]["body"]["object_name"]
            undo_redo_created(operation_name, body_name)
            native_operation = document.getObject(operation_name)
            body = document.getObject(body_name)
            PartDesign.validateDesign(native_operation)
            records[operation] = {
                "operation_name": operation_name,
                "operation_id": str(native_operation.OperationId),
                "body_name": body_name,
                "body_id": str(body.VibeCADBodyId),
            }

        box_body = document.getObject(records["design_box"]["body_name"])
        cylinder_body = document.getObject(records["design_cylinder"]["body_name"])
        sphere_body = document.getObject(records["design_sphere"]["body_name"])
        assert box_body.getParentGeoFeatureGroup() is component
        cylinder_x_bounds = (
            float(cylinder_body.Shape.BoundBox.XMin),
            float(cylinder_body.Shape.BoundBox.XMax),
        )
        cylinder_operation = document.getObject(
            records["design_cylinder"]["operation_name"]
        )
        assert cylinder_operation.Placement == App.Placement(
            App.Vector(30.0, 0.0, 0.0),
            App.Rotation(),
        )
        assert abs(float(cylinder_body.Shape.CenterOfMass.x) - 30.0) < 1.0e-7
        assert abs(cylinder_x_bounds[0] - 25.0) < 0.01, cylinder_x_bounds
        assert abs(cylinder_x_bounds[1] - 35.0) < 0.01, cylinder_x_bounds
        sphere_x_bounds = (
            float(sphere_body.Shape.BoundBox.XMin),
            float(sphere_body.Shape.BoundBox.XMax),
        )
        sphere_operation = document.getObject(
            records["design_sphere"]["operation_name"]
        )
        assert sphere_operation.Placement == App.Placement(
            App.Vector(60.0, 0.0, 0.0),
            App.Rotation(),
        )
        assert abs(float(sphere_body.Shape.CenterOfMass.x) - 60.0) < 1.0e-7
        assert abs(sphere_x_bounds[0] - 55.0) < 0.01, sphere_x_bounds
        assert abs(sphere_x_bounds[1] - 65.0) < 0.01, sphere_x_bounds
        assert document.getObject(records["design_tube"]["operation_name"]).Placement == App.Placement(
            App.Vector(240.0, 0.0, 0.0),
            App.Rotation(App.Vector(0.0, 1.0, 0.0), 30.0),
        )

        modifiers = (
            (
                "join",
                "design_box",
                records["design_box"]["body_name"],
                _placement(5.0),
                {"length_mm": 10.0, "width_mm": 8.0, "height_mm": 6.0},
            ),
            (
                "cut",
                "design_box",
                records["design_cylinder"]["body_name"],
                _placement(30.0, -6.0, 0.0),
                {"length_mm": 6.0, "width_mm": 12.0, "height_mm": 10.0},
            ),
            (
                "intersect",
                "design_box",
                records["design_sphere"]["body_name"],
                _placement(60.0, -6.0, -6.0),
                {"length_mm": 6.0, "width_mm": 12.0, "height_mm": 12.0},
            ),
        )
        modifier_records = []
        for mode, operation, body_name, placement, geometry in modifiers:
            body = document.getObject(body_name)
            before_volume = float(body.Shape.Volume)
            response = native_call(
                {
                    "operation": operation,
                    "label": f"Gate {mode.title()}",
                    "placement": placement,
                    "result": _result(mode, (body_name,)),
                    **geometry,
                }
            )
            operation_name = response["operation"]["object_name"]
            after_volume = float(document.getObject(body_name).Shape.Volume)
            assert response["result_mode"] == mode
            assert len(response["receipt"]["created"]) == 1
            assert [item["object_name"] for item in response["receipt"]["changed"]] == [
                body_name
            ]
            assert after_volume > before_volume if mode == "join" else after_volume < before_volume
            assert after_volume > 0.0
            document.undo()
            _process_events()
            assert document.getObject(operation_name) is None
            assert abs(document.getObject(body_name).Shape.Volume - before_volume) < 1.0e-7
            document.redo()
            _process_events()
            assert document.getObject(operation_name) is not None
            assert abs(document.getObject(body_name).Shape.Volume - after_volume) < 1.0e-7
            native_operation = document.getObject(operation_name)
            PartDesign.validateDesign(native_operation)
            modifier_records.append(
                (operation_name, str(native_operation.OperationId), body_name)
            )

        for invalid in (
            {
                "operation": "design_box",
                "label": "Invalid Result",
                "placement": _placement(0.0),
                "result": _result("new_body", (records["design_box"]["body_name"],)),
                "length_mm": 2.0,
                "width_mm": 2.0,
                "height_mm": 2.0,
            },
            {
                "operation": "design_tube",
                "label": "Invalid Tube",
                "placement": _placement(0.0),
                "result": _result(),
                "outer_radius_mm": 4.0,
                "inner_radius_mm": 4.0,
                "height_mm": 8.0,
            },
        ):
            before = tuple(obj.Name for obj in document.Objects)
            failure = native_call(invalid, succeeds=False)
            assert failure["error_code"] == "NATIVE_MODEL_INVALID"
            assert tuple(obj.Name for obj in document.Objects) == before
            assert document.HasPendingTransaction is False

        before = tuple(obj.Name for obj in document.Objects)
        wrong_target = native_call(
            {
                "operation": "design_box",
                "label": "Wrong Target",
                "placement": _placement(0.0),
                "result": _result("join", (component.Name,)),
                "length_mm": 2.0,
                "width_mm": 2.0,
                "height_mm": 2.0,
            },
            succeeds=False,
        )
        assert wrong_target["error_code"] == "NATIVE_TARGET_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before

        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-primitives-"))
        save_path = save_directory / "ModelPrimitives.FCStd"
        document.saveAs(str(save_path))
        saved_name = document.Name
        App.closeDocument(saved_name)
        document = App.openDocument(str(save_path))
        document.recompute()
        _process_events()
        for record in records.values():
            operation = document.getObject(record["operation_name"])
            body = document.getObject(record["body_name"])
            assert operation is not None and body is not None
            assert str(operation.OperationId) == record["operation_id"]
            assert str(body.VibeCADBodyId) == record["body_id"]
            PartDesign.validateDesign(operation)
        for operation_name, operation_id, body_name in modifier_records:
            operation = document.getObject(operation_name)
            assert operation is not None and document.getObject(body_name) is not None
            assert str(operation.OperationId) == operation_id
            PartDesign.validateDesign(operation)

        print("VIBECAD_NATIVE_MODEL_PRIMITIVES_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
