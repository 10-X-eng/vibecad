# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real FreeCAD lifecycle gate for profile-driven Native Design features."""

from __future__ import annotations

import json
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


def _profile(name, regions=()):
    return {"object_name": name, "regions": list(regions)}


def _elements(name, *subelements):
    return {"object_name": name, "subelements": list(subelements)}


def _new_body_result():
    return {
        "mode": "new_body",
        "targets": [],
        "destination_component": None,
    }


def _provider_arguments(arguments):
    values = dict(arguments)
    internal_operation = str(values.pop("operation"))
    shared = {
        name: values.pop(name)
        for name in ("label", "profile", "result")
        if name in values
    }
    if internal_operation == "design_helix" and "definition" in values:
        values["parameters"] = values.pop("definition")
    return {
        "operation": "profile",
        **shared,
        "definition": {
            "kind": internal_operation.removeprefix("design_"),
            **values,
        },
    }


def _rectangle(document, name, x1, x2, y1, y2, z=0.0):
    sketch = document.addObject("Sketcher::SketchObject", name)
    PartDesign.initializeDesignDefinition(sketch)
    sketch.addGeometry(
        [
            Part.LineSegment(App.Vector(x1, y1, 0), App.Vector(x2, y1, 0)),
            Part.LineSegment(App.Vector(x2, y1, 0), App.Vector(x2, y2, 0)),
            Part.LineSegment(App.Vector(x2, y2, 0), App.Vector(x1, y2, 0)),
            Part.LineSegment(App.Vector(x1, y2, 0), App.Vector(x1, y1, 0)),
        ],
        False,
    )
    sketch.Placement.Base.z = z
    document.recompute([sketch], True, True)
    PartDesign.finalizeDesignDefinition(sketch)
    return sketch


def _circle(document, name, x, y, radius, z=0.0):
    sketch = document.addObject("Sketcher::SketchObject", name)
    PartDesign.initializeDesignDefinition(sketch)
    sketch.addGeometry(
        Part.Circle(App.Vector(x, y, 0), App.Vector(0, 0, 1), radius),
        False,
    )
    sketch.Placement.Base.z = z
    document.recompute([sketch], True, True)
    PartDesign.finalizeDesignDefinition(sketch)
    return sketch


def _box_body(document, name, origin, lengths):
    return _shape_body(
        document,
        name,
        Part.makeBox(*lengths, App.Vector(*origin)),
    )


def _shape_body(document, name, shape):
    body = document.addObject("PartDesign::Body", name)
    seed = body.newObject("PartDesign::Feature", f"{name}Seed")
    seed.Shape = shape
    return body


def _result_mode_bodies(document):
    factories = {
        "design_extrude": lambda: Part.makeBox(
            10,
            10,
            12,
            App.Vector(-5, -5, -1),
        ),
        "design_revolve": lambda: Part.makeCylinder(
            3,
            8,
            App.Vector(0, -4, 0),
            App.Vector(0, 1, 0),
        ),
        "design_loft": lambda: Part.makeBox(
            8,
            8,
            10,
            App.Vector(-4, -4, -1),
        ),
        "design_sweep": lambda: Part.makeCylinder(
            2,
            10,
            App.Vector(0, 0, -1),
            App.Vector(0, 0, 1),
        ),
        "design_helix": lambda: Part.makeBox(
            12,
            16,
            12,
            App.Vector(-6, -4, -6),
        ),
    }
    return {
        operation: {
            mode: _shape_body(
                document,
                f"{operation.removeprefix('design_').title()}{mode.title()}Body",
                factory(),
            )
            for mode in ("join", "cut", "intersect")
        }
        for operation, factory in factories.items()
    }


def _planar_face_name(shape, axis, coordinate):
    for index, face in enumerate(shape.Faces, 1):
        center = face.CenterOfMass
        if abs(float(getattr(center, axis)) - coordinate) < 1.0e-7:
            return f"Face{index}"
    raise AssertionError(f"No face lies on {axis}={coordinate}")


def _setup_inputs(document):
    document.openTransaction("Create Native profile gate inputs")
    extrude = _rectangle(document, "ExtrudeProfile", -3, 3, -2, 2)
    revolve = _rectangle(document, "RevolveProfile", 2, 4, -3, 3)
    loft_lower = _rectangle(document, "LoftLower", -3, 3, -3, 3)
    loft_upper = _rectangle(document, "LoftUpper", -1.5, 1.5, -1.5, 1.5, 8)
    loft_third = _rectangle(document, "LoftThird", -2, 2, -2, 2, 16)
    sweep = _circle(document, "SweepProfile", 0, 0, 1)
    sweep_middle = _circle(document, "SweepMiddle", 0, 0, 1.5, 4)
    sweep_end = _circle(document, "SweepEnd", 0, 0, 0.75, 8)
    helix = _circle(document, "HelixProfile", 2, 0, 0.5)
    path = document.addObject("PartDesign::Feature", "SweepPath")
    path.Shape = Part.makeLine(App.Vector(0, 0, 0), App.Vector(0, 0, 8))
    document.classifyProvisionalTimelineInternalObject(path)
    auxiliary_path = document.addObject("PartDesign::Feature", "SweepAuxiliaryPath")
    auxiliary_path.Shape = Part.makeLine(
        App.Vector(0, 5, 0),
        App.Vector(0, 5, 8),
    )
    document.classifyProvisionalTimelineInternalObject(auxiliary_path)
    limit_profile = _rectangle(document, "ExtrudeLimitProfile", -3, 3, -2, 2, 2)
    limit_shape = document.addObject("PartDesign::Feature", "ExtrudeLimitShape")
    limit_shape.Shape = Part.makeBox(20, 20, 1, App.Vector(-10, -10, 6))
    document.classifyProvisionalTimelineInternalObject(limit_shape)
    first_body = _box_body(
        document,
        "UpToFirstBody",
        (-5, -5, 0),
        (10, 10, 10),
    )
    last_body = _box_body(
        document,
        "UpToLastBody",
        (-5, -5, 0),
        (10, 10, 10),
    )
    revolve_first_body = _box_body(
        document,
        "RevolveUpToFirstBody",
        (1, -4, -4),
        (4, 8, 3),
    )
    revolve_last_body = _box_body(
        document,
        "RevolveUpToLastBody",
        (1, -4, -4),
        (4, 8, 3),
    )
    revolve_face_body = _box_body(
        document,
        "RevolveUpToFaceBody",
        (1, -4, -4),
        (4, 8, 3),
    )
    result_mode_bodies = _result_mode_bodies(document)
    document.recompute()
    document.commitTransaction()
    return {
        "extrude": extrude,
        "revolve": revolve,
        "loft_lower": loft_lower,
        "loft_upper": loft_upper,
        "loft_third": loft_third,
        "sweep": sweep,
        "sweep_middle": sweep_middle,
        "sweep_end": sweep_end,
        "helix": helix,
        "path": path,
        "auxiliary_path": auxiliary_path,
        "limit_profile": limit_profile,
        "limit_shape": limit_shape,
        "limit_face": _planar_face_name(limit_shape.Shape, "z", 6.0),
        "first_body": first_body,
        "last_body": last_body,
        "revolve_limit_face": _planar_face_name(
            revolve_face_body.Shape,
            "z",
            -1.0,
        ),
        "revolve_first_body": revolve_first_body,
        "revolve_last_body": revolve_last_body,
        "revolve_face_body": revolve_face_body,
        "result_mode_bodies": result_mode_bodies,
    }


def _turn(definition):
    variants = tuple(
        variant
        for variant in definition.variants
        if variant.operation == "profile"
    )
    surface = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot(
            "model",
            1,
            "d" * 64,
            tuple(sorted(action for variant in variants for action in variant.action_ids)),
            (),
            (),
        ),
        available=True,
        unavailable_reason="",
        tool_names=(definition.name,),
        schemas=(
            definition.provider_schema(tuple(variant.operation for variant in variants)),
        ),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(surface)


def _basic_calls(inputs):
    return (
        {
            "operation": "design_extrude",
            "label": "Gate Extrude",
            "profile": _profile(inputs["extrude"].Name),
            "result": _new_body_result(),
            "direction": {"kind": "sketch_normal"},
            "extent": {
                "kind": "one_side",
                "sides": [
                    {
                        "kind": "length",
                        "length_mm": 10.0,
                        "taper_degrees": 0.0,
                    }
                ],
                "reversed": False,
            },
        },
        {
            "operation": "design_revolve",
            "label": "Gate Revolve",
            "profile": _profile(inputs["revolve"].Name),
            "result": _new_body_result(),
            "axis": _elements(inputs["revolve"].Name, "V_Axis"),
            "extent": {
                "kind": "angle",
                "angle_degrees": 360.0,
                "symmetric": False,
                "reversed": False,
            },
        },
        {
            "operation": "design_loft",
            "label": "Gate Loft",
            "profile": _profile(inputs["loft_lower"].Name),
            "result": _new_body_result(),
            "sections": [_profile(inputs["loft_upper"].Name)],
            "ruled": False,
            "closed": False,
        },
        {
            "operation": "design_sweep",
            "label": "Gate Sweep",
            "profile": _profile(inputs["sweep"].Name),
            "result": _new_body_result(),
            "path": _elements(inputs["path"].Name, "Edge1"),
            "options": {
                "spine_tangent": False,
                "orientation": {"kind": "standard"},
                "transition": "transformed",
                "transformation": "constant",
                "sections": [],
            },
        },
        {
            "operation": "design_helix",
            "label": "Gate Helix",
            "profile": _profile(inputs["helix"].Name),
            "result": _new_body_result(),
            "axis": _elements(inputs["helix"].Name, "V_Axis"),
            "definition": {
                "kind": "pitch_height_angle",
                "pitch_mm": 3.0,
                "height_mm": 9.0,
                "angle_degrees": 0.0,
            },
            "left_handed": False,
            "reversed": False,
            "outside": False,
            "tolerance": 0.1,
        },
    )


def _advanced_calls(inputs):
    extrude_base = {
        "profile": _profile(inputs["extrude"].Name),
        "result": _new_body_result(),
    }
    revolve_base = {
        "profile": _profile(inputs["revolve"].Name),
        "result": _new_body_result(),
        "axis": _elements(inputs["revolve"].Name, "V_Axis"),
    }
    sweep_base = {
        "profile": _profile(inputs["sweep"].Name),
        "result": _new_body_result(),
        "path": _elements(inputs["path"].Name, "Edge1"),
    }
    helix_base = {
        "profile": _profile(inputs["helix"].Name),
        "result": _new_body_result(),
        "axis": _elements(inputs["helix"].Name, "V_Axis"),
        "outside": False,
        "tolerance": 0.1,
    }
    return (
        {
            "operation": "design_extrude",
            "label": "Gate Two-Sided Custom Extrude",
            **extrude_base,
            "direction": {
                "kind": "custom_vector",
                "vector": {"x": 0.0, "y": 0.0, "z": 1.0},
                "along_sketch_normal": True,
            },
            "extent": {
                "kind": "two_sides",
                "sides": [
                    {"kind": "length", "length_mm": 6.0, "taper_degrees": 2.0},
                    {"kind": "length", "length_mm": 4.0, "taper_degrees": 0.0},
                ],
                "reversed": False,
            },
        },
        {
            "operation": "design_extrude",
            "label": "Gate Symmetric Reference Extrude",
            **extrude_base,
            "direction": {
                "kind": "reference_axis",
                "target": _elements(inputs["extrude"].Name, "N_Axis"),
                "along_sketch_normal": True,
            },
            "extent": {
                "kind": "symmetric",
                "sides": [
                    {"kind": "length", "length_mm": 8.0, "taper_degrees": 0.0}
                ],
                "reversed": False,
            },
        },
        {
            "operation": "design_revolve",
            "label": "Gate Symmetric Revolve",
            **revolve_base,
            "extent": {
                "kind": "angle",
                "angle_degrees": 180.0,
                "symmetric": True,
                "reversed": False,
            },
        },
        {
            "operation": "design_revolve",
            "label": "Gate Two-Angle Revolve",
            **revolve_base,
            "extent": {
                "kind": "two_angles",
                "angle1_degrees": 120.0,
                "angle2_degrees": 120.0,
                "reversed": False,
            },
        },
        {
            "operation": "design_loft",
            "label": "Gate Ruled Loft",
            "profile": _profile(inputs["loft_lower"].Name),
            "result": _new_body_result(),
            "sections": [_profile(inputs["loft_upper"].Name)],
            "ruled": True,
            "closed": False,
        },
        {
            "operation": "design_loft",
            "label": "Gate Closed Loft",
            "profile": _profile(inputs["loft_lower"].Name),
            "result": _new_body_result(),
            "sections": [
                _profile(inputs["loft_upper"].Name),
                _profile(inputs["loft_third"].Name),
            ],
            "ruled": False,
            "closed": True,
        },
        {
            "operation": "design_sweep",
            "label": "Gate Fixed Linear Sweep",
            **sweep_base,
            "options": {
                "spine_tangent": False,
                "orientation": {"kind": "fixed"},
                "transition": "right_corner",
                "transformation": "linear",
                "sections": [],
            },
        },
        {
            "operation": "design_sweep",
            "label": "Gate Binormal S Sweep",
            **sweep_base,
            "options": {
                "spine_tangent": False,
                "orientation": {
                    "kind": "binormal",
                    "vector": {"x": 1.0, "y": 0.0, "z": 0.0},
                },
                "transition": "round_corner",
                "transformation": "s_shape",
                "sections": [],
            },
        },
        {
            "operation": "design_sweep",
            "label": "Gate Auxiliary Interpolation Sweep",
            **sweep_base,
            "options": {
                "spine_tangent": False,
                "orientation": {
                    "kind": "auxiliary",
                    "spine": _elements(inputs["auxiliary_path"].Name, "Edge1"),
                    "tangent": False,
                    "curvilinear": True,
                },
                "transition": "transformed",
                "transformation": "interpolation",
                "sections": [],
            },
        },
        {
            "operation": "design_sweep",
            "label": "Gate Multisection Sweep",
            **sweep_base,
            "options": {
                "spine_tangent": False,
                "orientation": {"kind": "frenet"},
                "transition": "transformed",
                "transformation": "multisection",
                "sections": [
                    _profile(inputs["sweep_middle"].Name),
                    _profile(inputs["sweep_end"].Name),
                ],
            },
        },
        {
            "operation": "design_helix",
            "label": "Gate Pitch-Turns Helix",
            **helix_base,
            "definition": {
                "kind": "pitch_turns_angle",
                "pitch_mm": 3.0,
                "turns": 3.0,
                "angle_degrees": 0.0,
            },
            "left_handed": True,
            "reversed": False,
        },
        {
            "operation": "design_helix",
            "label": "Gate Height-Turns Helix",
            **helix_base,
            "definition": {
                "kind": "height_turns_angle",
                "height_mm": 9.0,
                "turns": 3.0,
                "angle_degrees": 0.0,
            },
            "left_handed": False,
            "reversed": True,
        },
        {
            "operation": "design_helix",
            "label": "Gate Growth Helix",
            **helix_base,
            "definition": {
                "kind": "height_turns_growth",
                "height_mm": 9.0,
                "turns": 3.0,
                "growth_mm": 0.0,
            },
            "left_handed": False,
            "reversed": False,
        },
    )


def _termination_calls(inputs):
    profile = _profile(inputs["limit_profile"].Name)
    direction = {"kind": "sketch_normal"}

    def result(mode, body=None):
        return {
            "mode": mode,
            "targets": [] if body is None else [{"object_name": body.Name}],
            "destination_component": None,
        }

    def extrude(
        label,
        side,
        result_spec,
        *,
        reversed_value=False,
        profile_spec=profile,
    ):
        return {
            "operation": "design_extrude",
            "label": label,
            "profile": profile_spec,
            "result": result_spec,
            "direction": direction,
            "extent": {
                "kind": "one_side",
                "sides": [side],
                "reversed": reversed_value,
            },
        }

    return (
        extrude(
            "Gate Up-To-First Extrude",
            {"kind": "up_to_first", "offset_mm": 0.0},
            result("join", inputs["first_body"]),
        ),
        extrude(
            "Gate Up-To-Last Extrude",
            {"kind": "up_to_last", "offset_mm": 0.0},
            result("join", inputs["last_body"]),
        ),
        extrude(
            "Gate Up-To-Face Extrude",
            {
                "kind": "up_to_face",
                "target": _elements(
                    inputs["limit_shape"].Name,
                    inputs["limit_face"],
                ),
                "offset_mm": 0.0,
            },
            result("new_body"),
        ),
        extrude(
            "Gate Up-To-Shape Extrude",
            {
                "kind": "up_to_shape",
                "target": _elements(
                    inputs["limit_shape"].Name,
                    inputs["limit_face"],
                ),
                "offset_mm": 0.0,
            },
            result("new_body"),
        ),
        {
            "operation": "design_extrude",
            "label": "Gate Reversed Extrude",
            "profile": _profile(inputs["extrude"].Name),
            "result": result("new_body"),
            "direction": direction,
            "extent": {
                "kind": "one_side",
                "sides": [
                    {
                        "kind": "length",
                        "length_mm": 5.0,
                        "taper_degrees": 0.0,
                    }
                ],
                "reversed": True,
            },
        },
        {
            "operation": "design_revolve",
            "label": "Gate Reversed Revolve",
            "profile": _profile(inputs["revolve"].Name),
            "result": result("new_body"),
            "axis": _elements(inputs["revolve"].Name, "V_Axis"),
            "extent": {
                "kind": "angle",
                "angle_degrees": 90.0,
                "symmetric": False,
                "reversed": True,
            },
        },
        {
            "operation": "design_revolve",
            "label": "Gate Up-To-First Revolve",
            "profile": _profile(inputs["revolve"].Name),
            "result": result("join", inputs["revolve_first_body"]),
            "axis": _elements(inputs["revolve"].Name, "V_Axis"),
            "extent": {"kind": "up_to_first", "reversed": False},
        },
        {
            "operation": "design_revolve",
            "label": "Gate Up-To-Last Revolve",
            "profile": _profile(inputs["revolve"].Name),
            "result": result("join", inputs["revolve_last_body"]),
            "axis": _elements(inputs["revolve"].Name, "V_Axis"),
            "extent": {"kind": "up_to_last"},
        },
        {
            "operation": "design_revolve",
            "label": "Gate Up-To-Face Revolve",
            "profile": _profile(inputs["revolve"].Name),
            "result": result("join", inputs["revolve_face_body"]),
            "axis": _elements(inputs["revolve"].Name, "V_Axis"),
            "extent": {
                "kind": "up_to_face",
                "target": _elements(
                    inputs["revolve_face_body"].Name,
                    inputs["revolve_limit_face"],
                ),
                "reversed": False,
            },
        },
    )


def _result_mode_calls(inputs):
    basic_by_operation = {
        call["operation"]: call
        for call in _basic_calls(inputs)
    }
    calls = []
    for operation, bodies in inputs["result_mode_bodies"].items():
        for mode, body in bodies.items():
            arguments = json.loads(json.dumps(basic_by_operation[operation]))
            feature = operation.removeprefix("design_").title()
            arguments["label"] = f"Gate {feature} {mode.title()}"
            arguments["result"] = {
                "mode": mode,
                "targets": [{"object_name": body.Name}],
                "destination_component": None,
            }
            calls.append(arguments)
    return tuple(calls)


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeModelProfilesGate")
        VibeGui._connect_document_observer()
        inputs = _setup_inputs(document)
        _process_events()

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-profiles-gui")
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
            provider_arguments = _provider_arguments(arguments)
            response = dispatcher.call(
                "model.feature",
                json.dumps(provider_arguments, separators=(",", ":")),
                f"model-profile-call-{call_number}",
            )
            assert response.get("ok") is succeeds, (arguments, response)
            return response

        before = tuple(obj.Name for obj in document.Objects)
        invalid_schema = dict(_basic_calls(inputs)[0])
        del invalid_schema["profile"]
        failure = native_call(invalid_schema, succeeds=False)
        assert failure["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before

        records = []
        for arguments in _basic_calls(inputs):
            response = native_call(arguments)
            assert response["result_mode"] == "new_body"
            assert response["assistant_undo_available"] is True
            assert len(response["receipt"]["created"]) == 2
            assert response["bodies"][0]["solid_count"] == 1
            assert response["bodies"][0]["volume_mm3"] > 0.0
            operation_name = response["operation"]["object_name"]
            body_name = response["bodies"][0]["body"]["object_name"]
            operation_id = str(document.getObject(operation_name).OperationId)
            body_id = str(document.getObject(body_name).VibeCADBodyId)
            document.undo()
            _process_events()
            assert document.getObject(operation_name) is None
            assert document.getObject(body_name) is None
            document.redo()
            _process_events()
            operation = document.getObject(operation_name)
            body = document.getObject(body_name)
            assert operation is not None and body is not None
            PartDesign.validateDesign(operation)
            records.append((operation_name, operation_id, body_name, body_id))

        for arguments in _advanced_calls(inputs):
            response = native_call(arguments)
            assert response["result_mode"] == "new_body"
            assert response["bodies"][0]["solid_count"] == 1
            operation_name = response["operation"]["object_name"]
            body_name = response["bodies"][0]["body"]["object_name"]
            operation = document.getObject(operation_name)
            body = document.getObject(body_name)
            PartDesign.validateDesign(operation)
            records.append(
                (
                    operation_name,
                    str(operation.OperationId),
                    body_name,
                    str(body.VibeCADBodyId),
                )
            )

        for arguments in _termination_calls(inputs):
            response = native_call(arguments)
            assert response["result_mode"] == arguments["result"]["mode"]
            assert response["bodies"][0]["solid_count"] == 1
            operation_name = response["operation"]["object_name"]
            body_name = response["bodies"][0]["body"]["object_name"]
            operation = document.getObject(operation_name)
            body = document.getObject(body_name)
            PartDesign.validateDesign(operation)
            records.append(
                (
                    operation_name,
                    str(operation.OperationId),
                    body_name,
                    str(body.VibeCADBodyId),
                )
            )

        for arguments in _result_mode_calls(inputs):
            response = native_call(arguments)
            mode = arguments["result"]["mode"]
            target_name = arguments["result"]["targets"][0]["object_name"]
            assert response["result_mode"] == mode
            assert response["bodies"][0]["body"]["object_name"] == target_name
            assert response["bodies"][0]["solid_count"] == 1
            assert response["bodies"][0]["volume_mm3"] > 0.0
            assert len(response["receipt"]["created"]) == 1
            operation_name = response["operation"]["object_name"]
            operation = document.getObject(operation_name)
            body = document.getObject(target_name)
            PartDesign.validateDesign(operation)
            records.append(
                (
                    operation_name,
                    str(operation.OperationId),
                    target_name,
                    str(body.VibeCADBodyId),
                )
            )

        before = tuple(obj.Name for obj in document.Objects)
        invalid_loft = dict(_basic_calls(inputs)[2])
        invalid_loft["closed"] = True
        invalid_closed = native_call(invalid_loft, succeeds=False)
        assert invalid_closed["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert document.HasPendingTransaction is False

        invalid_sweep = dict(_basic_calls(inputs)[3])
        invalid_sweep["path"] = _elements(inputs["extrude"].Name, "Edge999")
        invalid_path = native_call(invalid_sweep, succeeds=False)
        assert invalid_path["error_code"] == "NATIVE_TARGET_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before

        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-profiles-"))
        save_path = save_directory / "ModelProfiles.FCStd"
        document.saveAs(str(save_path))
        saved_name = document.Name
        App.closeDocument(saved_name)
        document = App.openDocument(str(save_path))
        document.recompute()
        _process_events()
        for operation_name, operation_id, body_name, body_id in records:
            operation = document.getObject(operation_name)
            body = document.getObject(body_name)
            assert operation is not None and body is not None
            assert str(operation.OperationId) == operation_id
            assert str(body.VibeCADBodyId) == body_id
            PartDesign.validateDesign(operation)

        print("VIBECAD_NATIVE_MODEL_PROFILES_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
