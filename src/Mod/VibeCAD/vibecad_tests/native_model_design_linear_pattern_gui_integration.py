# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real FreeCAD lifecycle gate for Native Design Linear Pattern."""

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
from VibeCADNativeModelStructureSchema import model_structure_capability_definitions
from VibeCADNativeModelTransformSchema import model_transform_capability_definition
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


def _placement(x: float, y: float = 0.0, z: float = 0.0) -> dict[str, object]:
    return {
        "origin_mm": {"x": x, "y": y, "z": z},
        "rotation": {
            "axis": {"x": 0.0, "y": 0.0, "z": 1.0},
            "angle_degrees": 0.0,
        },
    }


def _box_arguments(
    label: str,
    *,
    x: float,
    length: float = 10.0,
    width: float = 10.0,
    height: float = 10.0,
    mode: str = "new_body",
    targets=(),
    destination_component: str | None = None,
    y: float = 0.0,
    z: float = 0.0,
) -> dict[str, object]:
    return {
        "operation": "primitive",
        "label": label,
        "placement": _placement(x, y, z),
        "result": {
            "mode": mode,
            "targets": [{"object_name": name} for name in targets],
            "destination_component": (
                {"object_name": destination_component}
                if destination_component is not None
                else None
            ),
        },
        "definition": {
            "kind": "box",
            "length_mm": length,
            "width_mm": width,
            "height_mm": height,
        },
    }


def _body_source(body_name: str) -> dict[str, object]:
    return {"kind": "body", "body": {"object_name": body_name}}


def _feature_source(operation_name: str, *body_names: str) -> dict[str, object]:
    return {
        "kind": "feature",
        "operation": {"object_name": operation_name},
        "targets": [{"object_name": name} for name in body_names],
    }


def _explicit_direction(x: float, y: float, z: float) -> dict[str, object]:
    return {
        "kind": "explicit",
        "vector": {"x": x, "y": y, "z": z},
    }


def _linear_arguments(
    label: str,
    source: dict[str, object],
    direction: dict[str, object],
    *,
    spacing_mm: float,
    occurrences: int,
    centered: bool,
) -> dict[str, object]:
    return {
        "operation": "pattern",
        "label": label,
        "source": source,
        "definition": {
            "kind": "linear",
            "direction": direction,
            "spacing_mm": spacing_mm,
            "occurrences": occurrences,
            "centered": centered,
        },
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
            "l" * 64,
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


def _reference_identity(value) -> tuple[str | None, tuple[str, ...]]:
    if not value:
        return None, ()
    obj, names = value if isinstance(value, tuple) else (value, ())
    return (
        str(getattr(obj, "Name", "")) or None,
        tuple(str(name) for name in list(names or ()) if str(name)),
    )


def _record(document, response) -> dict[str, object]:
    operation = document.getObject(response["operation"]["object_name"])
    PartDesign.validateDesign(operation)
    return {
        "operation_name": operation.Name,
        "operation_id": str(operation.OperationId),
        "pattern_source": str(operation.PatternSource),
        "source_operation": (
            operation.SourceOperation.Name if operation.SourceOperation is not None else None
        ),
        "input_state_names": [state.Name for state in operation.InputStates],
        "input_operation_ids": [str(state.OperationId) for state in operation.InputStates],
        "input_body_ids": [str(value) for value in operation.InputBodyIds],
        "output_body_ids": [str(value) for value in operation.OutputBodyIds],
        "previous_indices": list(operation.OutputPreviousInputIndices),
        "reference": _reference_identity(operation.DirectionReference),
        "reference_frame": _placement_signature(operation.DirectionReferenceFrame),
        "direction": tuple(float(value) for value in operation.Direction),
        "spacing": _quantity(operation.Spacing),
        "occurrences": int(operation.Occurrences),
        "centered": bool(operation.Centered),
        "result_operation": str(operation.ResultOperation),
        "body_names": [item["body"]["object_name"] for item in response["bodies"]],
        "body_ids": [
            str(document.getObject(item["body"]["object_name"]).VibeCADBodyId)
            for item in response["bodies"]
        ],
        "volumes": [float(shape.Volume) for shape in operation.OutputShapes],
    }


def _assert_body_pattern(
    document,
    response,
    source_name: str,
    *,
    occurrences: int,
    component_name: str | None = None,
) -> None:
    operation = document.getObject(response["operation"]["object_name"])
    source = document.getObject(source_name)
    outputs = [document.getObject(item["body"]["object_name"]) for item in response["bodies"]]
    output_ids = [str(body.VibeCADBodyId) for body in outputs]
    generated = occurrences - 1
    component = document.getObject(component_name) if component_name else None
    assert operation.TypeId == "PartDesign::DesignLinearPattern"
    assert operation.PatternSource == "Body"
    assert operation.SourceOperation is None
    assert operation.ResultOperation == "New Bodies"
    assert int(operation.GeneratedOccurrenceCount) == generated
    assert list(operation.InputBodyIds) == [str(source.VibeCADBodyId)]
    assert list(operation.OutputBodyIds) == output_ids
    assert list(operation.OutputPreviousInputIndices) == [-1] * generated
    assert len(set((str(source.VibeCADBodyId), *output_ids))) == occurrences
    assert len(outputs) == generated
    assert all(body.getParentGeoFeatureGroup() is component for body in outputs)
    assert len(response["receipt"]["created"]) == occurrences
    assert [item["object_name"] for item in response["receipt"]["changed"]] == (
        [component_name] if component_name else []
    )
    assert response["result_mode"] == "new_body"
    assert response["definition"]["generated_occurrence_count"] == generated
    assert response["assistant_undo_available"] is True
    assert all(not body.Shape.isNull() and len(body.Shape.Solids) == 1 for body in outputs)
    assert operation.BaseFeature is None
    PartDesign.validateDesign(operation)


def _assert_feature_pattern(
    document,
    response,
    source_operation_name: str,
    body_names,
    result_mode: str,
) -> None:
    operation = document.getObject(response["operation"]["object_name"])
    bodies = [document.getObject(name) for name in body_names]
    assert operation.PatternSource == "Feature"
    assert operation.SourceOperation is document.getObject(source_operation_name)
    assert operation.ResultOperation == result_mode.title()
    assert list(operation.InputBodyIds) == [str(body.VibeCADBodyId) for body in bodies]
    assert list(operation.OutputBodyIds) == [str(body.VibeCADBodyId) for body in bodies]
    assert list(operation.OutputPreviousInputIndices) == list(range(len(bodies)))
    assert [item["body"]["object_name"] for item in response["bodies"]] == list(
        body_names
    )
    assert [item["object_name"] for item in response["receipt"]["changed"]] == list(
        body_names
    )
    assert len(response["receipt"]["created"]) == 1
    assert response["result_mode"] == result_mode
    assert operation.BaseFeature is None
    PartDesign.validateDesign(operation)


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeModelDesignLinearPatternGate")
        VibeGui._connect_document_observer()
        _process_events()

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-design-linear-pattern-gui")
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
        structure, sketch, _open_sketch, _validation = (
            model_structure_capability_definitions()
        )
        definitions = (
            model_feature_capability_definition(),
            structure,
            sketch,
            model_transform_capability_definition(),
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
                f"model-design-linear-pattern-call-{call_number}",
            )
            assert response.get("ok") is succeeds, (arguments, response)
            return response

        before = tuple(obj.Name for obj in document.Objects)
        invalid = _linear_arguments(
            "Invalid Schema",
            _body_source("MissingBody"),
            _explicit_direction(1.0, 0.0, 0.0),
            spacing_mm=10.0,
            occurrences=3,
            centered=False,
        )
        del invalid["definition"]["spacing_mm"]
        failure = native_call("model.transform", invalid, succeeds=False)
        assert failure["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before

        source_response = native_call(
            "model.feature",
            _box_arguments("Linear Body Source", x=3.0),
        )
        source_body = source_response["bodies"][0]["body"]["object_name"]
        before = tuple(obj.Name for obj in document.Objects)
        failure = native_call(
            "model.transform",
            _linear_arguments(
                "Invalid Edge Pattern",
                _body_source(source_body),
                {
                    "kind": "subelement",
                    "object_name": source_body,
                    "subelement": "Edge999",
                },
                spacing_mm=10.0,
                occurrences=3,
                centered=False,
            ),
            succeeds=False,
        )
        assert failure["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert document.HasPendingTransaction is False

        records = []
        response = native_call(
            "model.transform",
            _linear_arguments(
                "Forward Body Pattern",
                _body_source(source_body),
                _explicit_direction(1.0, 0.0, 0.0),
                spacing_mm=20.0,
                occurrences=4,
                centered=False,
            ),
        )
        _assert_body_pattern(document, response, source_body, occurrences=4)
        output_names = [item["body"]["object_name"] for item in response["bodies"]]
        assert [
            round(document.getObject(name).Shape.BoundBox.XMin, 7)
            for name in output_names
        ] == [23.0, 43.0, 63.0]
        operation_name = response["operation"]["object_name"]
        output_volumes = [_volume(document, name) for name in output_names]
        document.undo()
        _process_events()
        assert document.getObject(operation_name) is None
        assert all(document.getObject(name) is None for name in output_names)
        document.redo()
        _process_events()
        assert all(
            abs(_volume(document, name) - volume) < 1.0e-7
            for name, volume in zip(output_names, output_volumes, strict=True)
        )
        records.append(_record(document, response))

        response = native_call(
            "model.transform",
            _linear_arguments(
                "Centered Body Pattern",
                _body_source(source_body),
                _explicit_direction(1.0, 0.0, 0.0),
                spacing_mm=20.0,
                occurrences=4,
                centered=True,
            ),
        )
        _assert_body_pattern(document, response, source_body, occurrences=4)
        assert [
            round(document.getObject(item["body"]["object_name"]).Shape.BoundBox.XMin, 7)
            for item in response["bodies"]
        ] == [23.0, -17.0, 43.0]
        assert response["definition"]["centered"] is True
        records.append(_record(document, response))

        sketch_response = native_call(
            "model.sketch",
            {
                "operation": "create_on_base_plane",
                "label": "Linear Direction Sketch",
                "plane": "XY",
                "offset_mm": 0.0,
            },
        )
        sketch_name = sketch_response["sketch"]["object_name"]
        sketch_object = document.getObject(sketch_name)
        sketch_object.addGeometry(
            Part.LineSegment(App.Vector(0.0, 0.0, 0.0), App.Vector(0.0, 10.0, 0.0)),
            True,
        )
        document.recompute([sketch_object], True, True)
        assert int(sketch_object.AxisCount) == 1
        directions = [
            {"kind": "object", "object_name": sketch_name},
            {
                "kind": "subelement",
                "object_name": sketch_name,
                "subelement": "H_Axis",
            },
            {
                "kind": "subelement",
                "object_name": sketch_name,
                "subelement": "Axis0",
            },
        ]
        for index, direction in enumerate(directions):
            response = native_call(
                "model.transform",
                _linear_arguments(
                    f"Referenced Body Pattern {index + 1}",
                    _body_source(source_body),
                    direction,
                    spacing_mm=15.0,
                    occurrences=2,
                    centered=False,
                ),
            )
            _assert_body_pattern(document, response, source_body, occurrences=2)
            operation = document.getObject(response["operation"]["object_name"])
            assert operation.DirectionReference[0] is not None
            records.append(_record(document, response))

        component_response = native_call(
            "model.structure",
            {
                "operation": "new_component",
                "label": "Moved Linear Component",
                "parent_component": None,
            },
        )
        component_name = component_response["component"]["object_name"]
        component = document.getObject(component_name)
        component.Placement = App.Placement(
            App.Vector(40.0, 5.0, 2.0),
            App.Rotation(),
        )
        document.recompute()
        component_source_response = native_call(
            "model.feature",
            _box_arguments(
                "Component Linear Source",
                x=3.0,
                destination_component=component_name,
            ),
        )
        component_source = component_source_response["bodies"][0]["body"][
            "object_name"
        ]
        response = native_call(
            "model.transform",
            _linear_arguments(
                "Component Edge Pattern",
                _body_source(component_source),
                {
                    "kind": "subelement",
                    "object_name": component_source,
                    "subelement": "Edge1",
                },
                spacing_mm=12.0,
                occurrences=2,
                centered=False,
            ),
        )
        _assert_body_pattern(
            document,
            response,
            component_source,
            occurrences=2,
            component_name=component_name,
        )
        operation = document.getObject(response["operation"]["object_name"])
        assert _placement_signature(operation.DirectionReferenceFrame) == (
            40.0,
            5.0,
            2.0,
            0.0,
            0.0,
            0.0,
            1.0,
        )
        records.append(_record(document, response))

        targets = []
        for label in ("First Linear Target", "Second Linear Target"):
            target_response = native_call(
                "model.feature",
                _box_arguments(label, x=0.0, length=8.0),
            )
            targets.append(target_response["bodies"][0]["body"]["object_name"])
        tool_response = native_call(
            "model.feature",
            _box_arguments("Reusable Linear Additive Tool", x=0.0, length=4.0),
        )
        tool_operation = tool_response["operation"]["object_name"]
        before_volumes = [_volume(document, name) for name in targets]
        response = native_call(
            "model.transform",
            _linear_arguments(
                "Two Body Feature Linear Pattern",
                _feature_source(tool_operation, *targets),
                _explicit_direction(1.0, 0.0, 0.0),
                spacing_mm=6.0,
                occurrences=2,
                centered=False,
            ),
        )
        _assert_feature_pattern(document, response, tool_operation, targets, "join")
        assert all(
            _volume(document, name) > before
            for name, before in zip(targets, before_volumes, strict=True)
        )
        records.append(_record(document, response))

        cut_target_response = native_call(
            "model.feature",
            _box_arguments("Linear Cut Target", x=0.0, length=30.0),
        )
        cut_target = cut_target_response["bodies"][0]["body"]["object_name"]
        cut_source = native_call(
            "model.feature",
            _box_arguments(
                "Reusable Linear Cut Tool",
                x=4.0,
                y=3.0,
                z=3.0,
                length=2.0,
                width=4.0,
                height=4.0,
                mode="cut",
                targets=(cut_target,),
            ),
        )
        cut_operation = cut_source["operation"]["object_name"]
        before_cut_pattern = _volume(document, cut_target)
        response = native_call(
            "model.transform",
            _linear_arguments(
                "Subtractive Feature Linear Pattern",
                _feature_source(cut_operation, cut_target),
                _explicit_direction(1.0, 0.0, 0.0),
                spacing_mm=10.0,
                occurrences=3,
                centered=False,
            ),
        )
        _assert_feature_pattern(document, response, cut_operation, (cut_target,), "cut")
        assert int(document.getObject(response["operation"]["object_name"]).GeneratedOccurrenceCount) == 2
        assert _volume(document, cut_target) < before_cut_pattern
        records.append(_record(document, response))

        failure_target_response = native_call(
            "model.feature",
            _box_arguments("Linear Failure Target", x=0.0),
        )
        failure_target = failure_target_response["bodies"][0]["body"]["object_name"]
        remote_source = native_call(
            "model.feature",
            _box_arguments("Remote Linear Tool", x=100.0, length=5.0),
        )
        remote_operation = remote_source["operation"]["object_name"]
        before_objects = tuple(obj.Name for obj in document.Objects)
        before_volume = _volume(document, failure_target)
        failure = native_call(
            "model.transform",
            _linear_arguments(
                "Disconnected Feature Linear Pattern",
                _feature_source(remote_operation, failure_target),
                _explicit_direction(1.0, 0.0, 0.0),
                spacing_mm=10.0,
                occurrences=2,
                centered=False,
            ),
            succeeds=False,
        )
        assert failure["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before_objects
        assert abs(_volume(document, failure_target) - before_volume) < 1.0e-7
        assert document.HasPendingTransaction is False

        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-linear-pattern-"))
        save_path = save_directory / "ModelDesignLinearPattern.FCStd"
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
            assert str(operation.PatternSource) == record["pattern_source"]
            assert (
                operation.SourceOperation.Name
                if operation.SourceOperation is not None
                else None
            ) == record["source_operation"]
            assert [state.Name for state in operation.InputStates] == record[
                "input_state_names"
            ]
            assert [str(state.OperationId) for state in operation.InputStates] == record[
                "input_operation_ids"
            ]
            assert [str(value) for value in operation.InputBodyIds] == record[
                "input_body_ids"
            ]
            assert [str(value) for value in operation.OutputBodyIds] == record[
                "output_body_ids"
            ]
            assert list(operation.OutputPreviousInputIndices) == record[
                "previous_indices"
            ]
            assert _reference_identity(operation.DirectionReference) == record["reference"]
            assert _placement_signature(operation.DirectionReferenceFrame) == record[
                "reference_frame"
            ]
            assert tuple(float(value) for value in operation.Direction) == record[
                "direction"
            ]
            assert abs(_quantity(operation.Spacing) - record["spacing"]) < 1.0e-8
            assert int(operation.Occurrences) == record["occurrences"]
            assert bool(operation.Centered) is record["centered"]
            assert operation.ResultOperation == record["result_operation"]
            assert int(operation.GeneratedOccurrenceCount) == record["occurrences"] - 1
            assert operation.BaseFeature is None
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
                assert not body.Shape.isNull() and len(body.Shape.Solids) == 1
            PartDesign.validateDesign(operation)

        print("VIBECAD_NATIVE_MODEL_DESIGN_LINEAR_PATTERN_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
