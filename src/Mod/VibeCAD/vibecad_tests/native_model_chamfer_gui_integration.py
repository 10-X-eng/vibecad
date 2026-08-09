# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real FreeCAD lifecycle gate for the Native Design Chamfer operation."""

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


def _placement(x: float) -> dict[str, object]:
    return {
        "origin_mm": {"x": x, "y": 0.0, "z": 0.0},
        "rotation": {
            "axis": {"x": 0.0, "y": 0.0, "z": 1.0},
            "angle_degrees": 0.0,
        },
    }


def _box_arguments(label: str, x: float) -> dict[str, object]:
    return {
        "operation": "primitive",
        "label": label,
        "placement": _placement(x),
        "result": {
            "mode": "new_body",
            "targets": [],
            "destination_component": None,
        },
        "definition": {
            "kind": "box",
            "length_mm": 10.0,
            "width_mm": 10.0,
            "height_mm": 10.0,
        },
    }


def _equal_distance(size: float) -> dict[str, object]:
    return {"kind": "equal_distance", "size_mm": size}


def _two_distances(
    size: float,
    second_size: float,
    *,
    flip: bool,
) -> dict[str, object]:
    return {
        "kind": "two_distances",
        "size_mm": size,
        "second_size_mm": second_size,
        "flip_direction": flip,
    }


def _distance_angle(
    size: float,
    angle: float,
    *,
    flip: bool,
) -> dict[str, object]:
    return {
        "kind": "distance_angle",
        "size_mm": size,
        "angle_degrees": angle,
        "flip_direction": flip,
    }


def _explicit_chamfer(label: str, definition: dict[str, object], *targets):
    return {
        "operation": "chamfer",
        "label": label,
        "selection": {
            "kind": "explicit",
            "targets": [
                {"object_name": body_name, "subelements": list(subelements)}
                for body_name, subelements in targets
            ],
        },
        "definition": definition,
    }


def _all_edges_chamfer(
    label: str,
    definition: dict[str, object],
    *body_names,
):
    return {
        "operation": "chamfer",
        "label": label,
        "selection": {
            "kind": "all_edges",
            "targets": [{"object_name": name} for name in body_names],
        },
        "definition": definition,
    }


def _turn(*definitions) -> NativeTurnSnapshot:
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
            "c" * 64,
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


def _quantity(value) -> float:
    return float(getattr(value, "Value", value))


def _operation_record(document, response) -> dict[str, object]:
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
        "chamfer_type": str(operation.ChamferType),
        "size": _quantity(operation.Size),
        "second_size": _quantity(operation.Size2),
        "angle": _quantity(operation.Angle),
        "flip": bool(operation.FlipDirection),
        "use_all_edges": bool(operation.UseAllEdges),
        "offsets": list(operation.TargetElementOffsets),
        "elements": list(operation.TargetElements),
    }


def _assert_operation_contract(
    document,
    response,
    *,
    body_names,
    offsets,
    elements,
    definition,
    use_all_edges,
) -> None:
    operation = document.getObject(response["operation"]["object_name"])
    expected_types = {
        "equal_distance": "Equal distance",
        "two_distances": "Two distances",
        "distance_angle": "Distance and Angle",
    }
    kind = definition["kind"]
    assert operation.TypeId == "PartDesign::DesignChamfer"
    assert operation.ResultOperation == "Modify"
    assert operation.BaseFeature is None
    base = operation.Base
    assert not base or base[0] is None
    assert str(operation.ChamferType) == expected_types[kind]
    assert abs(_quantity(operation.Size) - definition["size_mm"]) < 1.0e-8
    if kind == "two_distances":
        assert abs(
            _quantity(operation.Size2) - definition["second_size_mm"]
        ) < 1.0e-8
    if kind == "distance_angle":
        assert abs(
            _quantity(operation.Angle) - definition["angle_degrees"]
        ) < 1.0e-8
    expected_flip = bool(definition.get("flip_direction", False))
    assert bool(operation.FlipDirection) is expected_flip
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
    expected_feature = {
        "definition": kind,
        "size_mm": definition["size_mm"],
        "flip_direction": expected_flip,
        "selection_mode": "all_edges" if use_all_edges else "explicit",
        "target_count": len(body_names),
        "selected_reference_count": len(elements),
    }
    if kind == "two_distances":
        expected_feature["second_size_mm"] = definition["second_size_mm"]
    if kind == "distance_angle":
        expected_feature["angle_degrees"] = definition["angle_degrees"]
    assert response["feature"] == expected_feature
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
        document = App.newDocument("NativeModelChamferGate")
        VibeGui._connect_document_observer()
        _process_events()

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-chamfer-gui")
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
                f"model-chamfer-call-{call_number}",
            )
            assert response.get("ok") is succeeds, (arguments, response)
            return response

        before = tuple(obj.Name for obj in document.Objects)
        invalid_schema = _all_edges_chamfer(
            "Missing Size",
            {"kind": "equal_distance"},
            "MissingBody",
        )
        failure = native_call("model.dressup", invalid_schema, succeeds=False)
        assert failure["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before

        boxes = {}
        for index, label in enumerate(
            (
                "Equal A",
                "Equal B",
                "Face Selection",
                "Two Distance",
                "Distance Angle",
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
        invalid_edge = _explicit_chamfer(
            "Invalid Atomic Chamfer",
            _equal_distance(1.0),
            (boxes["Equal A"], ("Edge1",)),
            (boxes["Failure Target"], ("Edge999",)),
        )
        failure = native_call("model.dressup", invalid_edge, succeeds=False)
        assert failure["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert abs(_volume(document, boxes["Equal A"]) - 1000.0) < 1.0e-7
        assert abs(_volume(document, boxes["Failure Target"]) - 1000.0) < 1.0e-7
        assert document.HasPendingTransaction is False

        records = []
        equal_bodies = (boxes["Equal A"], boxes["Equal B"])
        equal_definition = _equal_distance(1.0)
        response = native_call(
            "model.dressup",
            _explicit_chamfer(
                "Shared Equal Chamfer",
                equal_definition,
                (equal_bodies[0], ("Edge1",)),
                (equal_bodies[1], ("Edge1",)),
            ),
        )
        _assert_operation_contract(
            document,
            response,
            body_names=equal_bodies,
            offsets=(0, 1, 2),
            elements=("Edge1", "Edge1"),
            definition=equal_definition,
            use_all_edges=False,
        )
        after_volumes = tuple(_volume(document, name) for name in equal_bodies)
        assert all(volume < 1000.0 for volume in after_volumes)
        operation_name = response["operation"]["object_name"]
        document.undo()
        _process_events()
        assert document.getObject(operation_name) is None
        assert all(
            abs(_volume(document, name) - 1000.0) < 1.0e-7
            for name in equal_bodies
        )
        document.redo()
        _process_events()
        assert document.getObject(operation_name) is not None
        assert all(
            abs(_volume(document, name) - expected) < 1.0e-7
            for name, expected in zip(equal_bodies, after_volumes, strict=True)
        )
        records.append(_operation_record(document, response))

        face_body = boxes["Face Selection"]
        face_definition = _equal_distance(0.5)
        response = native_call(
            "model.dressup",
            _explicit_chamfer(
                "Face Boundary Chamfer",
                face_definition,
                (face_body, ("Face1",)),
            ),
        )
        _assert_operation_contract(
            document,
            response,
            body_names=(face_body,),
            offsets=(0, 1),
            elements=("Face1",),
            definition=face_definition,
            use_all_edges=False,
        )
        records.append(_operation_record(document, response))

        two_body = boxes["Two Distance"]
        two_definition = _two_distances(1.0, 2.0, flip=True)
        response = native_call(
            "model.dressup",
            _explicit_chamfer(
                "Two Distance Chamfer",
                two_definition,
                (two_body, ("Edge1",)),
            ),
        )
        _assert_operation_contract(
            document,
            response,
            body_names=(two_body,),
            offsets=(0, 1),
            elements=("Edge1",),
            definition=two_definition,
            use_all_edges=False,
        )
        records.append(_operation_record(document, response))

        angle_body = boxes["Distance Angle"]
        angle_definition = _distance_angle(1.0, 35.0, flip=True)
        response = native_call(
            "model.dressup",
            _explicit_chamfer(
                "Distance Angle Chamfer",
                angle_definition,
                (angle_body, ("Edge1",)),
            ),
        )
        _assert_operation_contract(
            document,
            response,
            body_names=(angle_body,),
            offsets=(0, 1),
            elements=("Edge1",),
            definition=angle_definition,
            use_all_edges=False,
        )
        records.append(_operation_record(document, response))

        all_edges_body = boxes["All Edges"]
        all_edges_definition = _equal_distance(0.5)
        response = native_call(
            "model.dressup",
            _all_edges_chamfer(
                "All Edges Chamfer",
                all_edges_definition,
                all_edges_body,
            ),
        )
        _assert_operation_contract(
            document,
            response,
            body_names=(all_edges_body,),
            offsets=(0, 0),
            elements=(),
            definition=all_edges_definition,
            use_all_edges=True,
        )
        records.append(_operation_record(document, response))

        before = tuple(obj.Name for obj in document.Objects)
        before_volume = _volume(document, boxes["Failure Target"])
        failure = native_call(
            "model.dressup",
            _all_edges_chamfer(
                "Impossible Chamfer",
                _equal_distance(100.0),
                boxes["Failure Target"],
            ),
            succeeds=False,
        )
        assert failure["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert abs(
            _volume(document, boxes["Failure Target"]) - before_volume
        ) < 1.0e-7
        assert document.HasPendingTransaction is False

        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-chamfer-"))
        save_path = save_directory / "ModelChamfer.FCStd"
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
            assert [state.Name for state in operation.InputStates] == record[
                "input_state_names"
            ]
            assert [str(state.OperationId) for state in operation.InputStates] == record[
                "input_operation_ids"
            ]
            assert [str(value) for value in operation.OutputBodyIds] == record[
                "output_body_ids"
            ]
            assert str(operation.ChamferType) == record["chamfer_type"]
            assert abs(_quantity(operation.Size) - record["size"]) < 1.0e-8
            assert abs(
                _quantity(operation.Size2) - record["second_size"]
            ) < 1.0e-8
            assert abs(_quantity(operation.Angle) - record["angle"]) < 1.0e-8
            assert bool(operation.FlipDirection) is record["flip"]
            assert bool(operation.UseAllEdges) is record["use_all_edges"]
            assert list(operation.TargetElementOffsets) == record["offsets"]
            assert list(operation.TargetElements) == record["elements"]
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
            base = operation.Base
            assert not base or base[0] is None
            PartDesign.validateDesign(operation)

        print("VIBECAD_NATIVE_MODEL_CHAMFER_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
