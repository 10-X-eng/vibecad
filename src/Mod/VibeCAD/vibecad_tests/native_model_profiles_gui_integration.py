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
from VibeCADNativeCapabilityRegistry import (
    NativeProviderSurface,
    provider_visible_native_schema,
)
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


def _axis(name, subelement):
    return {
        "kind": "subelement",
        "object_name": name,
        "subelement": subelement,
    }


def _combine(kind, body):
    return {"kind": kind, "bodies": [{"object_name": body.Name}]}


def _feature_call(label, profile, kind, **feature):
    return {
        "label": label,
        "profile": profile,
        "feature": {"kind": kind, **feature},
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
        "extrude": lambda: Part.makeBox(
            10,
            10,
            12,
            App.Vector(-5, -5, -1),
        ),
        "revolve": lambda: Part.makeCylinder(
            3,
            8,
            App.Vector(0, -4, 0),
            App.Vector(0, 1, 0),
        ),
        "loft": lambda: Part.makeBox(
            8,
            8,
            10,
            App.Vector(-4, -4, -1),
        ),
        "sweep": lambda: Part.makeCylinder(
            2,
            10,
            App.Vector(0, 0, -1),
            App.Vector(0, 0, 1),
        ),
        "helix": lambda: Part.makeBox(
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
                f"{operation.title()}{mode.title()}Body",
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
        if variant.operation == "create"
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
            provider_visible_native_schema(
                definition.provider_schema(
                    tuple(variant.operation for variant in variants)
                )
            ),
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
            "label": "Gate Extrude",
            "profile": _profile(inputs["extrude"].Name),
            "feature": {
                "kind": "extrude",
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
                },
            },
        },
        {
            "label": "Gate Revolve",
            "profile": _profile(inputs["revolve"].Name),
            "feature": {
                "kind": "revolve",
                "axis": {"kind": "global_axis", "axis": "Y"},
                "extent": {"kind": "angle", "angle_degrees": 360.0},
            },
        },
        {
            "label": "Gate Loft",
            "profile": _profile(inputs["loft_lower"].Name),
            "feature": {
                "kind": "loft",
                "sections": [_profile(inputs["loft_upper"].Name)],
                "ruled": False,
                "closed": False,
            },
        },
        {
            "label": "Gate Sweep",
            "profile": _profile(inputs["sweep"].Name),
            "feature": {
                "kind": "sweep",
                "path": _elements(inputs["path"].Name, "Edge1"),
                "options": {
                    "spine_tangent": False,
                    "orientation": {"kind": "standard"},
                    "transition": "transformed",
                    "transformation": {"kind": "constant"},
                },
            },
        },
        {
            "label": "Gate Helix",
            "profile": _profile(inputs["helix"].Name),
            "feature": {
                "kind": "helix",
                "axis": {"kind": "global_axis", "axis": "Y"},
                "parameters": {
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
        },
    )


def _advanced_calls(inputs):
    return (
        _feature_call(
            "Gate Two-Sided Custom Extrude",
            _profile(inputs["extrude"].Name),
            "extrude",
            direction={
                "kind": "custom_vector",
                "vector": {"x": 0.0, "y": 0.0, "z": 1.0},
                "along_sketch_normal": True,
            },
            extent={
                "kind": "two_sides",
                "sides": [
                    {"kind": "length", "length_mm": 6.0, "taper_degrees": 2.0},
                    {"kind": "length", "length_mm": 4.0, "taper_degrees": 0.0},
                ],
            },
        ),
        _feature_call(
            "Gate Symmetric Reference Extrude",
            _profile(inputs["extrude"].Name),
            "extrude",
            direction={
                "kind": "reference_axis",
                "target": _elements(inputs["extrude"].Name, "N_Axis"),
                "along_sketch_normal": True,
            },
            extent={
                "kind": "symmetric",
                "sides": [
                    {"kind": "length", "length_mm": 8.0, "taper_degrees": 0.0}
                ],
            },
        ),
        _feature_call(
            "Gate Symmetric Revolve",
            _profile(inputs["revolve"].Name),
            "revolve",
            axis=_axis(inputs["revolve"].Name, "V_Axis"),
            extent={
                "kind": "angle",
                "angle_degrees": 180.0,
                "direction": "symmetric",
            },
        ),
        _feature_call(
            "Gate Two-Angle Revolve",
            _profile(inputs["revolve"].Name),
            "revolve",
            axis=_axis(inputs["revolve"].Name, "V_Axis"),
            extent={
                "kind": "two_angles",
                "angle1_degrees": 120.0,
                "angle2_degrees": 120.0,
            },
        ),
        _feature_call(
            "Gate Ruled Loft",
            _profile(inputs["loft_lower"].Name),
            "loft",
            sections=[_profile(inputs["loft_upper"].Name)],
            ruled=True,
            closed=False,
        ),
        _feature_call(
            "Gate Closed Loft",
            _profile(inputs["loft_lower"].Name),
            "loft",
            sections=[
                _profile(inputs["loft_upper"].Name),
                _profile(inputs["loft_third"].Name),
            ],
            ruled=False,
            closed=True,
        ),
        _feature_call(
            "Gate Fixed Linear Sweep",
            _profile(inputs["sweep"].Name),
            "sweep",
            path=_elements(inputs["path"].Name, "Edge1"),
            options={
                "spine_tangent": False,
                "orientation": {"kind": "fixed"},
                "transition": "right_corner",
                "transformation": {"kind": "linear"},
            },
        ),
        _feature_call(
            "Gate Binormal S Sweep",
            _profile(inputs["sweep"].Name),
            "sweep",
            path=_elements(inputs["path"].Name, "Edge1"),
            options={
                "spine_tangent": False,
                "orientation": {
                    "kind": "binormal",
                    "vector": {"x": 1.0, "y": 0.0, "z": 0.0},
                },
                "transition": "round_corner",
                "transformation": {"kind": "s_shape"},
            },
        ),
        _feature_call(
            "Gate Auxiliary Interpolation Sweep",
            _profile(inputs["sweep"].Name),
            "sweep",
            path=_elements(inputs["path"].Name, "Edge1"),
            options={
                "spine_tangent": False,
                "orientation": {
                    "kind": "auxiliary",
                    "spine": _elements(inputs["auxiliary_path"].Name, "Edge1"),
                    "tangent": False,
                    "curvilinear": True,
                },
                "transition": "transformed",
                "transformation": {"kind": "interpolation"},
            },
        ),
        _feature_call(
            "Gate Multisection Sweep",
            _profile(inputs["sweep"].Name),
            "sweep",
            path=_elements(inputs["path"].Name, "Edge1"),
            options={
                "spine_tangent": False,
                "orientation": {"kind": "frenet"},
                "transition": "transformed",
                "transformation": {
                    "kind": "multisection",
                    "sections": [
                        _profile(inputs["sweep_middle"].Name),
                        _profile(inputs["sweep_end"].Name),
                    ],
                },
            },
        ),
        _feature_call(
            "Gate Pitch-Turns Helix",
            _profile(inputs["helix"].Name),
            "helix",
            axis=_axis(inputs["helix"].Name, "V_Axis"),
            parameters={
                "kind": "pitch_turns_angle",
                "pitch_mm": 3.0,
                "turns": 3.0,
                "angle_degrees": 0.0,
            },
            left_handed=True,
            reversed=False,
            outside=False,
            tolerance=0.1,
        ),
        _feature_call(
            "Gate Height-Turns Helix",
            _profile(inputs["helix"].Name),
            "helix",
            axis=_axis(inputs["helix"].Name, "V_Axis"),
            parameters={
                "kind": "height_turns_angle",
                "height_mm": 9.0,
                "turns": 3.0,
                "angle_degrees": 0.0,
            },
            left_handed=False,
            reversed=True,
            outside=False,
            tolerance=0.1,
        ),
        _feature_call(
            "Gate Growth Helix",
            _profile(inputs["helix"].Name),
            "helix",
            axis=_axis(inputs["helix"].Name, "V_Axis"),
            parameters={
                "kind": "height_turns_growth",
                "height_mm": 9.0,
                "turns": 3.0,
                "growth_mm": 0.0,
            },
            left_handed=False,
            reversed=False,
            outside=False,
            tolerance=0.1,
        ),
    )


def _termination_calls(inputs):
    profile = _profile(inputs["limit_profile"].Name)
    direction = {"kind": "sketch_normal"}

    def extrude(
        label,
        side,
        *,
        combine=None,
        reversed_value=False,
        profile_spec=profile,
    ):
        result = _feature_call(
            label,
            profile_spec,
            "extrude",
            direction=direction,
            extent={
                "kind": "one_side",
                "sides": [side],
                "reversed": reversed_value,
            },
        )
        if combine is not None:
            result["combine"] = combine
        return result

    return (
        extrude(
            "Gate Up-To-First Extrude",
            {"kind": "up_to_first", "offset_mm": 0.0},
            combine=_combine("join", inputs["first_body"]),
        ),
        extrude(
            "Gate Up-To-Last Extrude",
            {"kind": "up_to_last", "offset_mm": 0.0},
            combine=_combine("join", inputs["last_body"]),
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
        ),
        _feature_call(
            "Gate Reversed Extrude",
            _profile(inputs["extrude"].Name),
            "extrude",
            direction=direction,
            extent={
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
        ),
        _feature_call(
            "Gate Reversed Revolve",
            _profile(inputs["revolve"].Name),
            "revolve",
            axis=_axis(inputs["revolve"].Name, "V_Axis"),
            extent={
                "kind": "angle",
                "angle_degrees": 90.0,
                "direction": "reverse",
            },
        ),
        {
            **_feature_call(
                "Gate Up-To-First Revolve",
                _profile(inputs["revolve"].Name),
                "revolve",
                axis=_axis(inputs["revolve"].Name, "V_Axis"),
                extent={"kind": "up_to_first"},
            ),
            "combine": _combine("join", inputs["revolve_first_body"]),
        },
        {
            **_feature_call(
                "Gate Up-To-Last Revolve",
                _profile(inputs["revolve"].Name),
                "revolve",
                axis=_axis(inputs["revolve"].Name, "V_Axis"),
                extent={"kind": "up_to_last"},
            ),
            "combine": _combine("join", inputs["revolve_last_body"]),
        },
        {
            **_feature_call(
                "Gate Up-To-Face Revolve",
                _profile(inputs["revolve"].Name),
                "revolve",
                axis=_axis(inputs["revolve"].Name, "V_Axis"),
                extent={
                    "kind": "up_to_face",
                    "target": _elements(
                        inputs["revolve_face_body"].Name,
                        inputs["revolve_limit_face"],
                    ),
                },
            ),
            "combine": _combine("join", inputs["revolve_face_body"]),
        },
    )


def _result_mode_calls(inputs):
    basic_by_kind = {
        call["feature"]["kind"]: call for call in _basic_calls(inputs)
    }
    calls = []
    for kind, bodies in inputs["result_mode_bodies"].items():
        for mode, body in bodies.items():
            arguments = json.loads(json.dumps(basic_by_kind[kind]))
            arguments["label"] = f"Gate {kind.title()} {mode.title()}"
            arguments["combine"] = _combine(mode, body)
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
        debug_events = []
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state,
            registry=build_native_capability_registry(),
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=lambda: None,
            active_document=lambda: App.ActiveDocument,
            debug_sink=debug_events.append,
        )
        call_number = 0

        def native_call(arguments, *, succeeds=True):
            nonlocal call_number
            call_number += 1
            response = dispatcher.call(
                "model.feature",
                json.dumps(arguments, separators=(",", ":")),
                f"model-profile-call-{call_number}",
            )
            assert response.get("ok") is succeeds, (
                arguments,
                response,
                debug_events[-1:] if not response.get("ok") else [],
            )
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
            expected_mode = arguments.get("combine", {}).get("kind", "new_body")
            assert response["result_mode"] == expected_mode
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
            mode = arguments["combine"]["kind"]
            target_name = arguments["combine"]["bodies"][0]["object_name"]
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
        invalid_loft = json.loads(json.dumps(_basic_calls(inputs)[2]))
        invalid_loft["feature"]["closed"] = True
        invalid_closed = native_call(invalid_loft, succeeds=False)
        assert invalid_closed["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert document.HasPendingTransaction is False

        invalid_sweep = json.loads(json.dumps(_basic_calls(inputs)[3]))
        invalid_sweep["feature"]["path"] = _elements(
            inputs["extrude"].Name,
            "Edge999",
        )
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
