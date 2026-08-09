# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real FreeCAD lifecycle gate for the Native Design Thickness operation."""

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


def _thickness_arguments(
    label: str,
    thickness_mm: float,
    *,
    direction: str,
    mode: str,
    join: str,
    intersection_handling: bool,
    targets,
) -> dict[str, object]:
    return {
        "operation": "thickness",
        "label": label,
        "selection": {
            "kind": "explicit",
            "targets": [
                {"object_name": body_name, "subelements": list(subelements)}
                for body_name, subelements in targets
            ],
        },
        "thickness_mm": thickness_mm,
        "direction": direction,
        "mode": mode,
        "join": join,
        "intersection_handling": intersection_handling,
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
            "t" * 64,
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
        "value": _quantity(operation.Value),
        "reversed": bool(operation.Reversed),
        "mode": str(operation.Mode),
        "join": str(operation.Join),
        "intersection": bool(operation.Intersection),
        "offsets": list(operation.TargetElementOffsets),
        "elements": list(operation.TargetElements),
        "volumes": [float(shape.Volume) for shape in operation.OutputShapes],
    }


def _assert_operation_contract(
    document,
    response,
    *,
    body_names,
    offsets,
    elements,
    thickness_mm,
    direction,
    mode,
    join,
    intersection_handling,
) -> None:
    operation = document.getObject(response["operation"]["object_name"])
    native_modes = {
        "skin": "Skin",
        "pipe": "Pipe",
        "recto_verso": "RectoVerso",
    }
    native_joins = {"arc": "Arc", "intersection": "Intersection"}
    assert operation.TypeId == "PartDesign::DesignThickness"
    assert operation.ResultOperation == "Modify"
    assert operation.BaseFeature is None
    base = operation.Base
    assert not base or base[0] is None
    assert abs(_quantity(operation.Value) - thickness_mm) < 1.0e-8
    assert bool(operation.Reversed) is (direction == "inward")
    assert str(operation.Mode) == native_modes[mode]
    assert str(operation.Join) == native_joins[join]
    assert bool(operation.Intersection) is intersection_handling
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
        "thickness_mm": thickness_mm,
        "direction": direction,
        "mode": mode,
        "join": join,
        "intersection_handling": intersection_handling,
        "target_count": len(body_names),
        "selected_face_count": len(elements),
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
        document = App.newDocument("NativeModelThicknessGate")
        VibeGui._connect_document_observer()
        _process_events()

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-thickness-gui")
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
        definitions = (
            model_feature_capability_definition(),
            model_dressup_capability_definition(),
        )
        turn = _turn(*definitions)
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
                f"model-thickness-call-{call_number}",
            )
            assert response.get("ok") is succeeds, (arguments, response)
            return response

        before = tuple(obj.Name for obj in document.Objects)
        invalid_schema = _thickness_arguments(
            "Missing Thickness",
            1.0,
            direction="inward",
            mode="skin",
            join="arc",
            intersection_handling=False,
            targets=(("MissingBody", ("Face6",)),),
        )
        del invalid_schema["thickness_mm"]
        failure = native_call("model.dressup", invalid_schema, succeeds=False)
        assert failure["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before

        boxes = {}
        labels = (
            "Shared Inward A",
            "Shared Inward B",
            "Outward",
            "Pipe",
            "Recto Verso",
            "Intersection Join",
            "Intersection Handling",
            "Failure Target",
        )
        for index, label in enumerate(labels):
            response = native_call(
                "model.feature",
                _box_arguments(label, float(index * 25)),
            )
            body_name = response["bodies"][0]["body"]["object_name"]
            boxes[label] = body_name
            assert abs(_volume(document, body_name) - 1000.0) < 1.0e-7

        before = tuple(obj.Name for obj in document.Objects)
        failure = native_call(
            "model.dressup",
            _thickness_arguments(
                "Invalid Atomic Thickness",
                1.0,
                direction="inward",
                mode="skin",
                join="arc",
                intersection_handling=False,
                targets=(
                    (boxes["Shared Inward A"], ("Face6",)),
                    (boxes["Failure Target"], ("Face999",)),
                ),
            ),
            succeeds=False,
        )
        assert failure["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert abs(_volume(document, boxes["Shared Inward A"]) - 1000.0) < 1.0e-7
        assert abs(_volume(document, boxes["Failure Target"]) - 1000.0) < 1.0e-7
        assert document.HasPendingTransaction is False

        records = []
        shared_bodies = (boxes["Shared Inward A"], boxes["Shared Inward B"])
        response = native_call(
            "model.dressup",
            _thickness_arguments(
                "Shared Inward Thickness",
                1.0,
                direction="inward",
                mode="skin",
                join="arc",
                intersection_handling=False,
                targets=tuple((name, ("Face6",)) for name in shared_bodies),
            ),
        )
        _assert_operation_contract(
            document,
            response,
            body_names=shared_bodies,
            offsets=(0, 1, 2),
            elements=("Face6", "Face6"),
            thickness_mm=1.0,
            direction="inward",
            mode="skin",
            join="arc",
            intersection_handling=False,
        )
        after_volumes = tuple(_volume(document, name) for name in shared_bodies)
        assert all(abs(volume - 424.0) < 1.0e-6 for volume in after_volumes)
        operation_name = response["operation"]["object_name"]
        document.undo()
        _process_events()
        assert document.getObject(operation_name) is None
        assert all(
            abs(_volume(document, name) - 1000.0) < 1.0e-7
            for name in shared_bodies
        )
        document.redo()
        _process_events()
        assert document.getObject(operation_name) is not None
        assert all(
            abs(_volume(document, name) - expected) < 1.0e-7
            for name, expected in zip(shared_bodies, after_volumes, strict=True)
        )
        records.append(_operation_record(document, response))

        cases = (
            ("Outward", "outward", "skin", "arc", False),
            ("Pipe", "inward", "pipe", "arc", False),
            ("Recto Verso", "outward", "recto_verso", "arc", False),
            ("Intersection Join", "inward", "skin", "intersection", False),
            ("Intersection Handling", "inward", "skin", "arc", True),
        )
        for label, direction, mode, join, handling in cases:
            body_name = boxes[label]
            response = native_call(
                "model.dressup",
                _thickness_arguments(
                    f"{label} Thickness",
                    1.0,
                    direction=direction,
                    mode=mode,
                    join=join,
                    intersection_handling=handling,
                    targets=((body_name, ("Face6",)),),
                ),
            )
            _assert_operation_contract(
                document,
                response,
                body_names=(body_name,),
                offsets=(0, 1),
                elements=("Face6",),
                thickness_mm=1.0,
                direction=direction,
                mode=mode,
                join=join,
                intersection_handling=handling,
            )
            expected_volume = 564.9262481900961 if direction == "outward" else 424.0
            assert abs(_volume(document, body_name) - expected_volume) < 1.0e-6
            records.append(_operation_record(document, response))

        before = tuple(obj.Name for obj in document.Objects)
        before_volume = _volume(document, boxes["Failure Target"])
        failure = native_call(
            "model.dressup",
            _thickness_arguments(
                "Impossible Thickness",
                100.0,
                direction="inward",
                mode="skin",
                join="arc",
                intersection_handling=False,
                targets=((boxes["Failure Target"], ("Face6",)),),
            ),
            succeeds=False,
        )
        assert failure["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert abs(_volume(document, boxes["Failure Target"]) - before_volume) < 1.0e-7
        assert document.HasPendingTransaction is False

        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-thickness-"))
        save_path = save_directory / "ModelThickness.FCStd"
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
            assert abs(_quantity(operation.Value) - record["value"]) < 1.0e-8
            assert bool(operation.Reversed) is record["reversed"]
            assert str(operation.Mode) == record["mode"]
            assert str(operation.Join) == record["join"]
            assert bool(operation.Intersection) is record["intersection"]
            assert list(operation.TargetElementOffsets) == record["offsets"]
            assert list(operation.TargetElements) == record["elements"]
            assert [float(shape.Volume) for shape in operation.OutputShapes] == record[
                "volumes"
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
            base = operation.Base
            assert not base or base[0] is None
            PartDesign.validateDesign(operation)

        print("VIBECAD_NATIVE_MODEL_THICKNESS_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
