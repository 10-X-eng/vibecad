# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real FreeCAD lifecycle gate for the Native Design Hole capability."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import time
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
from VibeCADNativeModelCatalogSchema import model_catalog_capability_definition
from VibeCADNativeModelHoleSchema import model_hole_capability_definition
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


def _finalize_sketch(document, sketch) -> None:
    document.recompute([sketch], True, True)
    PartDesign.finalizeDesignDefinition(sketch)


def _sketch(document, name, geometries, z=10.0):
    sketch = document.addObject("Sketcher::SketchObject", name)
    PartDesign.initializeDesignDefinition(sketch)
    sketch.addGeometry(list(geometries), False)
    sketch.Placement.Base.z = z
    _finalize_sketch(document, sketch)
    return sketch


def _circle(x, y=0.0, radius=1.0):
    return Part.Circle(
        App.Vector(x, y, 0),
        App.Vector(0, 0, 1),
        radius,
    )


def _arc(x, y=0.0, radius=1.0):
    return Part.ArcOfCircle(
        App.Vector(x + radius, y, 0),
        App.Vector(x, y + radius, 0),
        App.Vector(x - radius, y, 0),
    )


def _body(document, name, x, *, z=0.0, width=16.0):
    body = document.addObject("PartDesign::Body", name)
    seed = body.newObject("PartDesign::Feature", f"{name}InitialState")
    seed.Shape = Part.makeBox(
        width,
        16.0,
        10.0,
        App.Vector(x - width / 2.0, -8.0, z),
    )
    return body, seed


def _single_input(
    document,
    key,
    x,
    geometries,
    base_profile,
    *,
    body_z=0.0,
    sketch_z=10.0,
    width=16.0,
):
    body, seed = _body(document, f"{key}Body", x, z=body_z, width=width)
    profile = _sketch(
        document,
        f"{key}Profile",
        geometries,
        sketch_z,
    )
    return {
        "profile": profile,
        "base_profile": base_profile,
        "bodies": (body,),
        "input_states": (seed,),
    }


def _setup_inputs(document):
    document.openTransaction("Create Native Hole gate inputs")
    cases = {}
    x = 0.0

    def add(key, geometries, base_profile, **kwargs):
        nonlocal x
        cases[key] = _single_input(
            document,
            key,
            x,
            geometries(x),
            base_profile,
            **kwargs,
        )
        x += 30.0

    add("Plain", lambda center: [_circle(center)], "circles_and_arcs")
    add("ThroughAll", lambda center: [Part.Point(App.Vector(center, 0, 0))], "points")
    add(
        "Counterbore",
        lambda center: [
            _circle(center - 7.0),
            _arc(center),
            Part.Point(App.Vector(center + 7.0, 0, 0)),
        ],
        "points_circles_and_arcs",
        width=30.0,
    )
    add("Countersink", lambda center: [_circle(center)], "circles_and_arcs")
    add("Counterdrill", lambda center: [_circle(center)], "circles_and_arcs")
    add("CatalogBore", lambda center: [_circle(center)], "circles_and_arcs")
    add("CatalogSink", lambda center: [_circle(center)], "circles_and_arcs")
    add("TapDrill", lambda center: [_circle(center)], "circles_and_arcs")
    add("CosmeticDepth", lambda center: [_circle(center)], "circles_and_arcs")
    add("CosmeticDin", lambda center: [_circle(center)], "circles_and_arcs")
    add("Modeled", lambda center: [_circle(center)], "circles_and_arcs")
    add(
        "Reversed",
        lambda center: [_circle(center)],
        "circles_and_arcs",
        body_z=10.0,
        sketch_z=10.0,
    )

    first, first_seed = _body(document, "MultiFirstBody", x)
    second, second_seed = _body(document, "MultiSecondBody", x + 20.0)
    profile = _sketch(
        document,
        "MultiProfile",
        [_circle(x), _circle(x + 20.0)],
    )
    cases["Multi"] = {
        "profile": profile,
        "base_profile": "circles_and_arcs",
        "bodies": (first, second),
        "input_states": (first_seed, second_seed),
    }
    document.recompute()
    document.commitTransaction()
    return cases


def _head(kind="none", **values):
    return {"kind": kind, **values}


def _depth(kind="dimension", depth_mm=8.0):
    result = {"kind": kind}
    if kind == "dimension":
        result["depth_mm"] = depth_mm
    return result


def _plain(diameter_mm=3.0):
    return {"kind": "plain", "diameter_mm": diameter_mm}


def _threaded(kind, *, depth_kind, custom_clearance=None, direction="right"):
    thread_depth = {"kind": depth_kind}
    if depth_kind == "dimension":
        thread_depth["depth_mm"] = 6.0
    result = {
        "kind": kind,
        "standard": "ISOMetricProfile",
        "size": "M4x0.7",
        "thread_class": "6H",
        "direction": direction,
        "thread_depth": thread_depth,
    }
    if kind == "threaded_modeled":
        result["custom_clearance_mm"] = custom_clearance
    return result


def _arguments(
    case,
    label,
    *,
    hole_type=None,
    head=None,
    depth=None,
    drill_point=None,
    taper=None,
    reversed=False,
):
    return {
        "operation": "hole",
        "label": label,
        "profile": {"object_name": case["profile"].Name},
        "base_profile": case["base_profile"],
        "hole_type": hole_type or _plain(),
        "head": head or _head(),
        "depth": depth or _depth(),
        "drill_point": drill_point or {"kind": "flat"},
        "taper": taper or {"kind": "straight"},
        "reversed": reversed,
        "targets": [
            {"object_name": body.Name}
            for body in case["bodies"]
        ],
    }


def _hole_cases(inputs):
    return (
        (
            "Plain",
            _arguments(inputs["Plain"], "Gate Plain Hole"),
            1,
        ),
        (
            "ThroughAll",
            _arguments(
                inputs["ThroughAll"],
                "Gate Through-All Point Hole",
                depth=_depth("through_all"),
            ),
            1,
        ),
        (
            "Counterbore",
            _arguments(
                inputs["Counterbore"],
                "Gate Mixed Counterbore",
                hole_type=_plain(2.5),
                head=_head("counterbore", diameter_mm=5.0, depth_mm=2.0),
                drill_point={
                    "kind": "angled",
                    "angle_degrees": 118.0,
                    "depth_reference": "full_diameter",
                },
                taper={"kind": "tapered", "angle_degrees": 89.0},
            ),
            3,
        ),
        (
            "Countersink",
            _arguments(
                inputs["Countersink"],
                "Gate Countersink",
                head=_head(
                    "countersink",
                    diameter_mm=7.0,
                    angle_degrees=100.0,
                ),
                drill_point={
                    "kind": "angled",
                    "angle_degrees": 125.0,
                    "depth_reference": "tip",
                },
            ),
            1,
        ),
        (
            "Counterdrill",
            _arguments(
                inputs["Counterdrill"],
                "Gate Counterdrill",
                head=_head(
                    "counterdrill",
                    diameter_mm=7.0,
                    depth_mm=2.5,
                    angle_degrees=90.0,
                ),
            ),
            1,
        ),
        (
            "CatalogBore",
            _arguments(
                inputs["CatalogBore"],
                "Gate Catalog Counterbore Override",
                hole_type={
                    "kind": "clearance",
                    "standard": "ISOMetricProfile",
                    "size": "M6x1.0",
                    "fit": "Medium",
                },
                head=_head(
                    "catalog",
                    designation="ISO 4762",
                    override={
                        "kind": "counterbore",
                        "diameter_mm": 12.0,
                        "depth_mm": 4.0,
                    },
                ),
            ),
            1,
        ),
        (
            "CatalogSink",
            _arguments(
                inputs["CatalogSink"],
                "Gate Catalog Countersink",
                hole_type={
                    "kind": "clearance",
                    "standard": "ISOMetricProfile",
                    "size": "M6x1.0",
                    "fit": "Fine",
                },
                head=_head(
                    "catalog",
                    designation="ISO 10642",
                    override=None,
                ),
            ),
            1,
        ),
        (
            "TapDrill",
            _arguments(
                inputs["TapDrill"],
                "Gate Tap Drill",
                hole_type={
                    "kind": "tap_drill",
                    "standard": "ISOMetricProfile",
                    "size": "M4x0.7",
                },
            ),
            1,
        ),
        (
            "CosmeticDepth",
            _arguments(
                inputs["CosmeticDepth"],
                "Gate Cosmetic Thread Hole Depth",
                hole_type=_threaded(
                    "threaded_cosmetic",
                    depth_kind="hole_depth",
                ),
            ),
            1,
        ),
        (
            "CosmeticDin",
            _arguments(
                inputs["CosmeticDin"],
                "Gate Cosmetic Thread DIN76",
                hole_type=_threaded(
                    "threaded_cosmetic",
                    depth_kind="tapped_din76",
                    direction="left",
                ),
            ),
            1,
        ),
        (
            "Modeled",
            _arguments(
                inputs["Modeled"],
                "Gate Modeled Thread",
                hole_type=_threaded(
                    "threaded_modeled",
                    depth_kind="dimension",
                    custom_clearance=-0.02,
                    direction="left",
                ),
            ),
            2,
        ),
        (
            "Reversed",
            _arguments(
                inputs["Reversed"],
                "Gate Reversed Hole",
                reversed=True,
            ),
            1,
        ),
        (
            "Multi",
            _arguments(
                inputs["Multi"],
                "Gate Exact Multi-Body Hole",
                depth=_depth("through_all"),
            ),
            2,
        ),
    )


def _turn(hole_definition, catalog_definition):
    definitions = (hole_definition, catalog_definition)
    surface = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot(
            "model",
            1,
            "e" * 64,
            ("PartDesign_Hole",),
            (),
            (),
        ),
        available=True,
        unavailable_reason="",
        tool_names=tuple(definition.name for definition in definitions),
        schemas=tuple(
            definition.provider_schema(
                tuple(variant.operation for variant in definition.variants)
            )
            for definition in definitions
        ),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(surface)


def _current_state(body):
    tip = body.Tip
    return getattr(tip, "CurrentState", tip)


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    exit_code = 1
    modeled_duration = None
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeModelHoleGate")
        VibeGui._connect_document_observer()
        inputs = _setup_inputs(document)
        _process_events()

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-hole-gui")
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
        turn = _turn(
            model_hole_capability_definition(),
            model_catalog_capability_definition(),
        )
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

        def native_call(tool_name, arguments, *, succeeds=True):
            nonlocal call_number
            call_number += 1
            response = dispatcher.call(
                tool_name,
                json.dumps(arguments, separators=(",", ":")),
                f"model-hole-call-{call_number}",
            )
            assert response.get("ok") is succeeds, (arguments, response)
            return response

        names_before_catalog = tuple(obj.Name for obj in document.Objects)
        summary = native_call(
            "model.catalog",
            {"operation": "hole_threads", "standard": None},
        )
        assert len(summary["standards"]) == 10
        assert tuple(obj.Name for obj in document.Objects) == names_before_catalog
        metric = native_call(
            "model.catalog",
            {
                "operation": "hole_threads",
                "standard": "ISOMetricProfile",
            },
        )
        assert len(metric["sizes"]) == 40
        assert "6H" in metric["classes"]
        assert metric["fits"] == ["Medium", "Fine", "Coarse"]
        heads = {item["designation"]: item for item in metric["heads"]}
        assert heads["ISO 4762"]["kind"] == "counterbore"
        assert "M6x1.0" in heads["ISO 4762"]["supported_sizes"]
        assert heads["ISO 10642"]["kind"] == "countersink"
        assert tuple(obj.Name for obj in document.Objects) == names_before_catalog

        invalid = deepcopy(_hole_cases(inputs)[0][1])
        del invalid["head"]
        before_invalid = tuple(obj.Name for obj in document.Objects)
        failure = native_call("model.hole", invalid, succeeds=False)
        assert failure["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before_invalid

        unavailable_head = deepcopy(_hole_cases(inputs)[5][1])
        unavailable_head["hole_type"]["size"] = "M1x0.25"
        before_volume = inputs["CatalogBore"]["bodies"][0].Shape.Volume
        failure = native_call("model.hole", unavailable_head, succeeds=False)
        assert failure["error_code"] == "NATIVE_MODEL_INVALID"
        assert "no definition" in failure["error"]
        assert tuple(obj.Name for obj in document.Objects) == before_invalid
        assert inputs["CatalogBore"]["bodies"][0].Shape.Volume == before_volume
        assert document.HasPendingTransaction is False

        records = []
        for key, arguments, expected_cutters in _hole_cases(inputs):
            case = inputs[key]
            bodies = case["bodies"]
            before_volumes = [float(body.Shape.Volume) for body in bodies]
            started = time.monotonic()
            response = native_call("model.hole", arguments)
            duration = time.monotonic() - started
            if arguments["hole_type"]["kind"] == "threaded_modeled":
                modeled_duration = duration
                assert modeled_duration < 60.0

            assert response["result_mode"] == "cut"
            assert response["assistant_undo_available"] is True
            assert response["feature"]["hole_type"] == arguments["hole_type"]["kind"]
            assert response["feature"]["depth_type"] == arguments["depth"]["kind"]
            assert response["feature"]["cutter_solid_count"] == expected_cutters, (
                key,
                response["feature"],
            )
            assert len(response["receipt"]["created"]) == 1
            assert [
                item["object_name"] for item in response["receipt"]["changed"]
            ] == [body.Name for body in bodies]
            assert [
                item["body"]["object_name"] for item in response["bodies"]
            ] == [body.Name for body in bodies]
            assert len(json.dumps(response, separators=(",", ":"))) < 4096

            operation_name = response["operation"]["object_name"]
            operation = document.getObject(operation_name)
            assert operation.TypeId == "PartDesign::DesignHole"
            assert operation.ResultOperation == "Cut"
            assert operation.BaseFeature is None
            assert operation.getParentGeoFeatureGroup() is None
            assert operation.Profile[0] is case["profile"]
            assert case["profile"].getParentGeoFeatureGroup() is None
            assert tuple(operation.InputStates) == case["input_states"]
            assert tuple(operation.OutputBodyIds) == tuple(operation.InputBodyIds)
            assert tuple(operation.OutputPreviousInputIndices) == tuple(
                range(len(bodies))
            )
            assert len(operation.OutputShapes) == len(bodies)
            assert all(
                float(body.Shape.Volume) < before
                for body, before in zip(bodies, before_volumes)
            )
            PartDesign.validateDesign(operation)

            record = {
                "key": key,
                "operation_name": operation_name,
                "operation_id": str(operation.OperationId),
                "profile_name": case["profile"].Name,
                "input_state_names": [state.Name for state in operation.InputStates],
                "output_body_ids": [str(value) for value in operation.OutputBodyIds],
                "bodies": [
                    {
                        "name": body.Name,
                        "body_id": str(body.VibeCADBodyId),
                        "state_id": str(_current_state(body).BodyStateId),
                        "volume": float(body.Shape.Volume),
                        "faces": len(body.Shape.Faces),
                    }
                    for body in bodies
                ],
                "cutter_faces": len(operation.AddSubShape.Faces),
            }
            records.append(record)

            if key == "Plain":
                document.undo()
                _process_events()
                assert document.getObject(operation_name) is None
                assert abs(bodies[0].Shape.Volume - before_volumes[0]) < 1.0e-7
                document.redo()
                _process_events()
                operation = document.getObject(operation_name)
                assert operation is not None
                assert abs(bodies[0].Shape.Volume - record["bodies"][0]["volume"]) < 1.0e-7
                PartDesign.validateDesign(operation)

        by_key = {record["key"]: record for record in records}
        cosmetic = by_key["CosmeticDepth"]
        modeled = by_key["Modeled"]
        assert abs(
            cosmetic["bodies"][0]["volume"]
            - modeled["bodies"][0]["volume"]
        ) > 1.0e-5
        assert modeled["bodies"][0]["faces"] > cosmetic["bodies"][0]["faces"]
        assert modeled["cutter_faces"] > cosmetic["cutter_faces"]

        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-hole-"))
        save_path = save_directory / "ModelHole.FCStd"
        document.saveAs(str(save_path))
        saved_name = document.Name
        App.closeDocument(saved_name)
        document = App.openDocument(str(save_path))
        document.recompute()
        _process_events()
        for record in records:
            operation = document.getObject(record["operation_name"])
            profile = document.getObject(record["profile_name"])
            assert operation is not None and profile is not None
            assert str(operation.OperationId) == record["operation_id"]
            assert operation.Profile[0] is profile
            assert operation.BaseFeature is None
            assert [state.Name for state in operation.InputStates] == record[
                "input_state_names"
            ]
            assert [str(value) for value in operation.OutputBodyIds] == record[
                "output_body_ids"
            ]
            for body_record in record["bodies"]:
                body = document.getObject(body_record["name"])
                assert body is not None
                assert str(body.VibeCADBodyId) == body_record["body_id"]
                assert str(_current_state(body).BodyStateId) == body_record["state_id"]
                assert abs(float(body.Shape.Volume) - body_record["volume"]) < 1.0e-6
            PartDesign.validateDesign(operation)

        print(
            "VIBECAD_NATIVE_MODEL_HOLE_GUI_OK "
            f"modeled_thread_seconds={modeled_duration:.3f}",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
