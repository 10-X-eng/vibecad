# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real FreeCAD lifecycle gate for the Native Design Draft operation."""

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
from VibeCADNativeModelStructureSchema import model_structure_capability_definitions
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


def _box_arguments(
    label: str,
    x: float,
    *,
    component_name: str | None = None,
) -> dict[str, object]:
    destination = (
        {"object_name": component_name} if component_name is not None else None
    )
    return {
        "operation": "primitive",
        "label": label,
        "placement": _placement(x),
        "result": {
            "mode": "new_body",
            "targets": [],
            "destination_component": destination,
        },
        "definition": {
            "kind": "box",
            "length_mm": 10.0,
            "width_mm": 10.0,
            "height_mm": 10.0,
        },
    }


def _automatic_reference() -> dict[str, str]:
    return {"kind": "automatic"}


def _object_reference(object_name: str) -> dict[str, str]:
    return {"kind": "object", "object_name": object_name}


def _subelement_reference(object_name: str, subelement: str) -> dict[str, str]:
    return {
        "kind": "subelement",
        "object_name": object_name,
        "subelement": subelement,
    }


def _draft_arguments(
    label: str,
    angle: float,
    *,
    neutral_plane: dict[str, str],
    pull_direction: dict[str, str],
    reversed_value: bool,
    targets,
) -> dict[str, object]:
    return {
        "operation": "draft",
        "label": label,
        "selection": {
            "kind": "explicit",
            "targets": [
                {"object_name": body_name, "subelements": list(faces)}
                for body_name, faces in targets
            ],
        },
        "angle_degrees": angle,
        "neutral_plane": neutral_plane,
        "pull_direction": pull_direction,
        "reversed": reversed_value,
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
            "d" * 64,
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


def _placement_signature(value) -> tuple[float, ...]:
    return (
        float(value.Base.x),
        float(value.Base.y),
        float(value.Base.z),
        *(float(component) for component in value.Rotation.Q),
    )


def _link_identity(value) -> tuple[str | None, tuple[str, ...]]:
    if not value:
        return None, ()
    if isinstance(value, tuple):
        obj = value[0] if value else None
        raw = value[1] if len(value) > 1 else ()
    else:
        obj = value
        raw = ()
    return (
        str(getattr(obj, "Name", "")) or None,
        tuple(str(item) for item in list(raw or ()) if str(item)),
    )


def _assert_resolved_link(
    reference: dict[str, str],
    actual: tuple[str | None, tuple[str, ...]],
) -> None:
    if reference["kind"] == "automatic":
        assert actual == (None, ())
        return
    assert actual[0]
    assert len(actual[1]) == (1 if reference["kind"] == "subelement" else 0)
    if reference["kind"] == "object":
        assert actual[0] == reference["object_name"]


def _reference_summary(
    reference: dict[str, str],
    resolved: tuple[str | None, tuple[str, ...]],
) -> dict[str, str]:
    if reference["kind"] == "automatic":
        return {"mode": "automatic"}
    result = {"object_name": resolved[0]}
    if resolved[1]:
        result["subelement"] = resolved[1][0]
    return result


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
        "angle": _quantity(operation.Angle),
        "reversed": bool(operation.Reversed),
        "neutral": _link_identity(operation.NeutralPlane),
        "pull": _link_identity(operation.PullDirection),
        "neutral_frame": _placement_signature(operation.NeutralPlaneFrame),
        "pull_frame": _placement_signature(operation.PullDirectionFrame),
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
    angle,
    reversed_value,
    neutral_plane,
    pull_direction,
) -> None:
    operation = document.getObject(response["operation"]["object_name"])
    assert operation.TypeId == "PartDesign::DesignDraft"
    assert operation.ResultOperation == "Modify"
    assert operation.BaseFeature is None
    base = operation.Base
    assert not base or base[0] is None
    assert abs(_quantity(operation.Angle) - angle) < 1.0e-8
    assert bool(operation.Reversed) is reversed_value
    neutral_link = _link_identity(operation.NeutralPlane)
    pull_link = _link_identity(operation.PullDirection)
    _assert_resolved_link(neutral_plane, neutral_link)
    _assert_resolved_link(pull_direction, pull_link)
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
        "angle_degrees": angle,
        "reversed": reversed_value,
        "target_count": len(body_names),
        "selected_face_count": len(elements),
        "neutral_plane": _reference_summary(neutral_plane, neutral_link),
        "pull_direction": _reference_summary(pull_direction, pull_link),
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
        document = App.newDocument("NativeModelDraftGate")
        document.openTransaction("Create Draft gate definitions")
        first_component = document.addObject(
            "PartDesign::Component",
            "DraftReferenceComponent",
        )
        document.classifyProvisionalTimelineInternalObject(first_component)
        second_component = document.addObject(
            "PartDesign::Component",
            "DraftSecondComponent",
        )
        document.classifyProvisionalTimelineInternalObject(second_component)
        first_component.Placement.Base.x = 7.0
        second_component.Placement.Base.x = 27.0
        first_component_name = first_component.Name
        pull_axis = document.addObject("PartDesign::Line", "DraftPullAxis")
        pull_axis.Label = "Draft Pull Axis"
        document.recompute()
        document.commitTransaction()
        VibeGui._connect_document_observer()
        _process_events()

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-draft-gui")
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
        sketch_definition = model_structure_capability_definitions()[1]
        dressup_definition = model_dressup_capability_definition()
        turn = _turn(feature_definition, sketch_definition, dressup_definition)
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
                f"model-draft-call-{call_number}",
            )
            assert response.get("ok") is succeeds, (arguments, response)
            return response

        before = tuple(obj.Name for obj in document.Objects)
        invalid_schema = _draft_arguments(
            "Missing Reverse",
            5.0,
            neutral_plane=_automatic_reference(),
            pull_direction=_automatic_reference(),
            reversed_value=False,
            targets=(("MissingBody", ("Face1",)),),
        )
        del invalid_schema["reversed"]
        failure = native_call("model.dressup", invalid_schema, succeeds=False)
        assert failure["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before

        sketch_response = native_call(
            "model.sketch",
            {
                "operation": "new_sketch",
                "label": "Draft Neutral Sketch",
                "support": {
                    "kind": "base_plane",
                    "plane": "XY",
                    "offset_mm": 0.0,
                },
            },
        )
        neutral_sketch = sketch_response["sketch"]["object_name"]

        boxes = {}
        box_specs = (
            ("Explicit A", 0.0, first_component.Name),
            ("Explicit B", 0.0, second_component.Name),
            ("Automatic", 60.0, None),
            ("Object References", 90.0, None),
            ("Failure Target", 120.0, None),
        )
        for label, x, component_name in box_specs:
            response = native_call(
                "model.feature",
                _box_arguments(label, x, component_name=component_name),
            )
            boxes[label] = response["bodies"][0]["body"]["object_name"]
            assert abs(_volume(document, boxes[label]) - 1000.0) < 1.0e-7

        before = tuple(obj.Name for obj in document.Objects)
        invalid_face = _draft_arguments(
            "Invalid Atomic Draft",
            5.0,
            neutral_plane=_subelement_reference(
                boxes["Explicit A"],
                "Face5",
            ),
            pull_direction=_subelement_reference(
                boxes["Explicit A"],
                "Edge1",
            ),
            reversed_value=False,
            targets=(
                (boxes["Explicit A"], ("Face1",)),
                (boxes["Failure Target"], ("Face999",)),
            ),
        )
        failure = native_call("model.dressup", invalid_face, succeeds=False)
        assert failure["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert abs(_volume(document, boxes["Explicit A"]) - 1000.0) < 1.0e-7
        assert document.HasPendingTransaction is False

        records = []
        explicit_bodies = (boxes["Explicit A"], boxes["Explicit B"])
        explicit_neutral = _subelement_reference(
            boxes["Explicit A"],
            "Face5",
        )
        explicit_pull = _subelement_reference(
            boxes["Explicit A"],
            "Edge1",
        )
        response = native_call(
            "model.dressup",
            _draft_arguments(
                "Shared Exact Draft",
                5.0,
                neutral_plane=explicit_neutral,
                pull_direction=explicit_pull,
                reversed_value=False,
                targets=(
                    (explicit_bodies[0], ("Face1",)),
                    (explicit_bodies[1], ("Face1",)),
                ),
            ),
        )
        _assert_operation_contract(
            document,
            response,
            body_names=explicit_bodies,
            offsets=(0, 1, 2),
            elements=("Face1", "Face1"),
            angle=5.0,
            reversed_value=False,
            neutral_plane=explicit_neutral,
            pull_direction=explicit_pull,
        )
        explicit_operation_name = response["operation"]["object_name"]
        explicit_operation = document.getObject(explicit_operation_name)
        assert abs(explicit_operation.NeutralPlaneFrame.Base.x - 7.0) < 1.0e-8
        assert abs(explicit_operation.PullDirectionFrame.Base.x - 7.0) < 1.0e-8
        explicit_frames = (
            _placement_signature(explicit_operation.NeutralPlaneFrame),
            _placement_signature(explicit_operation.PullDirectionFrame),
        )
        after_volumes = tuple(_volume(document, name) for name in explicit_bodies)
        assert all(abs(volume - 1000.0) > 1.0e-5 for volume in after_volumes)
        assert abs(after_volumes[0] - after_volumes[1]) < 1.0e-7
        document.undo()
        _process_events()
        assert document.getObject(explicit_operation_name) is None
        assert all(
            abs(_volume(document, name) - 1000.0) < 1.0e-7
            for name in explicit_bodies
        )
        document.redo()
        _process_events()
        assert document.getObject(explicit_operation_name) is not None
        assert all(
            abs(_volume(document, name) - expected) < 1.0e-7
            for name, expected in zip(explicit_bodies, after_volumes, strict=True)
        )
        records.append(_operation_record(document, response))

        automatic_body = boxes["Automatic"]
        response = native_call(
            "model.dressup",
            _draft_arguments(
                "Inferred Reversed Draft",
                4.0,
                neutral_plane=_automatic_reference(),
                pull_direction=_automatic_reference(),
                reversed_value=True,
                targets=((automatic_body, ("Face1",)),),
            ),
        )
        _assert_operation_contract(
            document,
            response,
            body_names=(automatic_body,),
            offsets=(0, 1),
            elements=("Face1",),
            angle=4.0,
            reversed_value=True,
            neutral_plane=_automatic_reference(),
            pull_direction=_automatic_reference(),
        )
        records.append(_operation_record(document, response))

        object_body = boxes["Object References"]
        object_neutral = _object_reference(neutral_sketch)
        object_pull = _object_reference(pull_axis.Name)
        response = native_call(
            "model.dressup",
            _draft_arguments(
                "Object Reference Draft",
                3.0,
                neutral_plane=object_neutral,
                pull_direction=object_pull,
                reversed_value=False,
                targets=((object_body, ("Face1",)),),
            ),
        )
        _assert_operation_contract(
            document,
            response,
            body_names=(object_body,),
            offsets=(0, 1),
            elements=("Face1",),
            angle=3.0,
            reversed_value=False,
            neutral_plane=object_neutral,
            pull_direction=object_pull,
        )
        records.append(_operation_record(document, response))

        before = tuple(obj.Name for obj in document.Objects)
        before_volume = _volume(document, boxes["Failure Target"])
        failure = native_call(
            "model.dressup",
            _draft_arguments(
                "Parallel Reference Failure",
                5.0,
                neutral_plane=_subelement_reference(
                    boxes["Failure Target"],
                    "Edge1",
                ),
                pull_direction=_subelement_reference(
                    boxes["Failure Target"],
                    "Edge1",
                ),
                reversed_value=False,
                targets=((boxes["Failure Target"], ("Face1",)),),
            ),
            succeeds=False,
        )
        assert failure["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert abs(
            _volume(document, boxes["Failure Target"]) - before_volume
        ) < 1.0e-7
        assert document.HasPendingTransaction is False

        document.openTransaction("Move Draft reference Component")
        first_component.Placement.Base.x = 57.0
        document.recompute()
        document.commitTransaction()
        moved_operation = document.getObject(explicit_operation_name)
        assert (
            _placement_signature(moved_operation.NeutralPlaneFrame),
            _placement_signature(moved_operation.PullDirectionFrame),
        ) == explicit_frames
        assert all(
            abs(_volume(document, name) - expected) < 1.0e-7
            for name, expected in zip(explicit_bodies, after_volumes, strict=True)
        )

        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-draft-"))
        save_path = save_directory / "ModelDraft.FCStd"
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
            assert abs(_quantity(operation.Angle) - record["angle"]) < 1.0e-8
            assert bool(operation.Reversed) is record["reversed"]
            assert _link_identity(operation.NeutralPlane) == record["neutral"]
            assert _link_identity(operation.PullDirection) == record["pull"]
            assert _placement_signature(operation.NeutralPlaneFrame) == record[
                "neutral_frame"
            ]
            assert _placement_signature(operation.PullDirectionFrame) == record[
                "pull_frame"
            ]
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

        reopened_explicit = document.getObject(explicit_operation_name)
        assert (
            _placement_signature(reopened_explicit.NeutralPlaneFrame),
            _placement_signature(reopened_explicit.PullDirectionFrame),
        ) == explicit_frames
        assert abs(
            document.getObject(first_component_name).Placement.Base.x - 57.0
        ) < 1.0e-8

        print("VIBECAD_NATIVE_MODEL_DRAFT_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
