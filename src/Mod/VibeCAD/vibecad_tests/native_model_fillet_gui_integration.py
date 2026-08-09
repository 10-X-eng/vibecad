# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real FreeCAD lifecycle gate for the Native Design Fillet operation."""

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
from VibeCADNativeModelDressupSchema import model_dressup_capability_definition
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


def _placement(x: float):
    return {
        "origin_mm": {"x": x, "y": 0.0, "z": 0.0},
        "rotation": {
            "axis": {"x": 0.0, "y": 0.0, "z": 1.0},
            "angle_degrees": 0.0,
        },
    }


def _new_body_result():
    return {
        "mode": "new_body",
        "targets": [],
        "destination_component": None,
    }


def _box_arguments(label: str, x: float):
    return {
        "operation": "primitive",
        "label": label,
        "placement": _placement(x),
        "result": _new_body_result(),
        "definition": {
            "kind": "box",
            "length_mm": 10.0,
            "width_mm": 10.0,
            "height_mm": 10.0,
        },
    }


def _explicit_fillet(label: str, radius: float, *targets):
    return {
        "operation": "fillet",
        "label": label,
        "selection": {
            "kind": "explicit",
            "targets": [
                {"object_name": body_name, "subelements": list(subelements)}
                for body_name, subelements in targets
            ],
        },
        "radius_mm": radius,
    }


def _all_edges_fillet(label: str, radius: float, *body_names):
    return {
        "operation": "fillet",
        "label": label,
        "selection": {
            "kind": "all_edges",
            "targets": [{"object_name": name} for name in body_names],
        },
        "radius_mm": radius,
    }


def _turn(*definitions):
    schemas = []
    action_ids = []
    for definition in definitions:
        operations = tuple(variant.operation for variant in definition.variants)
        schemas.append(definition.provider_schema(operations))
        action_ids.extend(
            action_id
            for variant in definition.variants
            for action_id in variant.action_ids
        )
    surface = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot(
            "model",
            1,
            "f" * 64,
            tuple(sorted(action_ids)),
            (),
            (),
        ),
        available=True,
        unavailable_reason="",
        tool_names=tuple(definition.name for definition in definitions),
        schemas=tuple(schemas),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(surface)


def _volume(document, body_name: str) -> float:
    return float(document.getObject(body_name).Shape.Volume)


def _operation_record(document, response):
    operation_name = response["operation"]["object_name"]
    operation = document.getObject(operation_name)
    assert operation is not None
    PartDesign.validateDesign(operation)
    return {
        "operation_name": operation_name,
        "operation_id": str(operation.OperationId),
        "body_names": [item["body"]["object_name"] for item in response["bodies"]],
        "body_ids": [
            str(document.getObject(item["body"]["object_name"]).VibeCADBodyId)
            for item in response["bodies"]
        ],
        "input_state_names": [state.Name for state in operation.InputStates],
        "input_operation_ids": [str(state.OperationId) for state in operation.InputStates],
        "output_body_ids": [str(value) for value in operation.OutputBodyIds],
    }


def _assert_operation_contract(
    document,
    response,
    *,
    body_names,
    offsets,
    elements,
    radius,
    use_all_edges,
):
    operation = document.getObject(response["operation"]["object_name"])
    assert operation.TypeId == "PartDesign::DesignFillet"
    assert operation.ResultOperation == "Modify"
    assert operation.BaseFeature is None
    base = operation.Base
    assert not base or base[0] is None
    assert abs(float(operation.Radius) - radius) < 1.0e-8
    assert bool(operation.UseAllEdges) is use_all_edges
    assert list(operation.TargetElementOffsets) == list(offsets)
    assert list(operation.TargetElements) == list(elements)
    assert tuple(operation.InputBodyIds) == tuple(operation.OutputBodyIds)
    assert list(operation.OutputPreviousInputIndices) == list(range(len(body_names)))
    assert [item["body"]["object_name"] for item in response["bodies"]] == list(
        body_names
    )
    assert [item["object_name"] for item in response["receipt"]["changed"]] == list(
        body_names
    )
    assert len(response["receipt"]["created"]) == 1
    assert response["result_mode"] == "modify"
    assert response["feature"] == {
        "radius_mm": radius,
        "selection_mode": "all_edges" if use_all_edges else "explicit",
        "target_count": len(body_names),
        "selected_reference_count": len(elements),
    }
    assert response["assistant_undo_available"] is True
    assert all(item["solid_count"] == 1 for item in response["bodies"])
    assert all(item["volume_mm3"] > 0.0 for item in response["bodies"])
    assert len(operation.InputStates) == len(body_names)
    assert len(operation.OutputShapes) == len(body_names)
    assert all(
        not shape.isNull() and shape.isValid() and len(shape.Solids) == 1
        for shape in operation.OutputShapes
    )
    PartDesign.validateDesign(operation)


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeModelFilletGate")
        VibeGui._connect_document_observer()
        _process_events()

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-fillet-gui")
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
        feature_definition = model_feature_capability_definition()
        dressup_definition = model_dressup_capability_definition()
        turn = _turn(feature_definition, dressup_definition)
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
                f"model-fillet-call-{call_number}",
            )
            assert response.get("ok") is succeeds, (arguments, response)
            return response

        before = tuple(obj.Name for obj in document.Objects)
        invalid_schema = _all_edges_fillet("Missing Radius", 1.0, "MissingBody")
        del invalid_schema["radius_mm"]
        failure = native_call("model.dressup", invalid_schema, succeeds=False)
        assert failure["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before

        boxes = {}
        for index, label in enumerate(
            (
                "Explicit A",
                "Explicit B",
                "Face Selection",
                "All Edges",
                "Failure Target",
            )
        ):
            response = native_call(
                "model.feature",
                _box_arguments(label, float(index * 25)),
            )
            body_name = response["bodies"][0]["body"]["object_name"]
            boxes[label] = body_name
            assert abs(_volume(document, body_name) - 1000.0) < 1.0e-7

        before = tuple(obj.Name for obj in document.Objects)
        invalid_edge = _explicit_fillet(
            "Invalid Atomic Fillet",
            1.0,
            (boxes["Explicit A"], ("Edge1",)),
            (boxes["Failure Target"], ("Edge999",)),
        )
        failure = native_call("model.dressup", invalid_edge, succeeds=False)
        assert failure["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert abs(_volume(document, boxes["Explicit A"]) - 1000.0) < 1.0e-7
        assert abs(_volume(document, boxes["Failure Target"]) - 1000.0) < 1.0e-7
        assert document.HasPendingTransaction is False

        records = []
        explicit_bodies = (boxes["Explicit A"], boxes["Explicit B"])
        response = native_call(
            "model.dressup",
            _explicit_fillet(
                "Shared Edge Fillet",
                1.0,
                (explicit_bodies[0], ("Edge1",)),
                (explicit_bodies[1], ("Edge1",)),
            ),
        )
        _assert_operation_contract(
            document,
            response,
            body_names=explicit_bodies,
            offsets=(0, 1, 2),
            elements=("Edge1", "Edge1"),
            radius=1.0,
            use_all_edges=False,
        )
        after_volumes = tuple(_volume(document, name) for name in explicit_bodies)
        assert all(volume < 1000.0 for volume in after_volumes)
        operation_name = response["operation"]["object_name"]
        document.undo()
        _process_events()
        assert document.getObject(operation_name) is None
        assert all(abs(_volume(document, name) - 1000.0) < 1.0e-7 for name in explicit_bodies)
        document.redo()
        _process_events()
        assert document.getObject(operation_name) is not None
        assert all(
            abs(_volume(document, name) - expected) < 1.0e-7
            for name, expected in zip(explicit_bodies, after_volumes, strict=True)
        )
        records.append(_operation_record(document, response))

        face_body = boxes["Face Selection"]
        response = native_call(
            "model.dressup",
            _explicit_fillet(
                "Face Boundary Fillet",
                0.5,
                (face_body, ("Face1",)),
            ),
        )
        _assert_operation_contract(
            document,
            response,
            body_names=(face_body,),
            offsets=(0, 1),
            elements=("Face1",),
            radius=0.5,
            use_all_edges=False,
        )
        assert _volume(document, face_body) < 1000.0
        records.append(_operation_record(document, response))

        all_edges_body = boxes["All Edges"]
        response = native_call(
            "model.dressup",
            _all_edges_fillet("All Sharp Edges Fillet", 0.5, all_edges_body),
        )
        _assert_operation_contract(
            document,
            response,
            body_names=(all_edges_body,),
            offsets=(0, 0),
            elements=(),
            radius=0.5,
            use_all_edges=True,
        )
        assert _volume(document, all_edges_body) < 1000.0
        records.append(_operation_record(document, response))

        before = tuple(obj.Name for obj in document.Objects)
        before_volume = _volume(document, boxes["Failure Target"])
        failure = native_call(
            "model.dressup",
            _all_edges_fillet(
                "Impossible Radius",
                100.0,
                boxes["Failure Target"],
            ),
            succeeds=False,
        )
        assert failure["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert abs(_volume(document, boxes["Failure Target"]) - before_volume) < 1.0e-7
        assert document.HasPendingTransaction is False

        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-fillet-"))
        save_path = save_directory / "ModelFillet.FCStd"
        document.saveAs(str(save_path))
        saved_name = document.Name
        App.closeDocument(saved_name)
        document = App.openDocument(str(save_path))
        document.recompute()
        _process_events()

        for record in records:
            operation = document.getObject(record["operation_name"])
            assert operation is not None
            assert str(operation.OperationId) == record["operation_id"]
            assert [body.Name for body in operation.InputStates] == record[
                "input_state_names"
            ]
            assert [str(body.OperationId) for body in operation.InputStates] == record[
                "input_operation_ids"
            ]
            assert [str(value) for value in operation.OutputBodyIds] == record[
                "output_body_ids"
            ]
            for body_name, body_id in zip(
                record["body_names"],
                record["body_ids"],
                strict=True,
            ):
                body = document.getObject(body_name)
                assert body is not None
                assert str(body.VibeCADBodyId) == body_id
                assert len(body.Shape.Solids) == 1
            assert operation.BaseFeature is None
            PartDesign.validateDesign(operation)

        print("VIBECAD_NATIVE_MODEL_FILLET_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
