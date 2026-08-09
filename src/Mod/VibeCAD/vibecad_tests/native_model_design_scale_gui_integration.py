# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real VibeCAD GUI and provider lifecycle gate for Body-aware Design Scale."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import PartDesign
import PartGui
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeModelFeatureSchema import model_feature_capability_definition
from VibeCADNativeModelStructureSchema import model_structure_capability_definitions
from VibeCADNativeModelTransformSchema import model_transform_capability_definition
import VibeCADNativeModelTransformRuntime as transform_runtime_module
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


def _placement(x: float, y: float, z: float) -> dict[str, object]:
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
    origin: tuple[float, float, float],
    size: tuple[float, float, float],
    destination_component: str | None = None,
) -> dict[str, object]:
    return {
        "operation": "primitive",
        "label": label,
        "placement": _placement(*origin),
        "result": {
            "mode": "new_body",
            "targets": [],
            "destination_component": (
                {"object_name": destination_component}
                if destination_component is not None
                else None
            ),
        },
        "definition": {
            "kind": "box",
            "length_mm": size[0],
            "width_mm": size[1],
            "height_mm": size[2],
        },
    }


def _scale_arguments(
    label: str,
    targets,
    *,
    factors: tuple[float, ...],
    center: tuple[float, float, float],
) -> dict[str, object]:
    definition: dict[str, object] = {
        "kind": "uniform" if len(factors) == 1 else "non_uniform",
        "center_mm": dict(zip(("x", "y", "z"), center, strict=True)),
    }
    if len(factors) == 1:
        definition["factor"] = factors[0]
    else:
        definition.update(
            dict(zip(("x_factor", "y_factor", "z_factor"), factors, strict=True))
        )
    return {
        "operation": "scale",
        "label": label,
        "targets": [{"object_name": name} for name in targets],
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
            "s" * 64,
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


def _close(actual: float, expected: float, tolerance: float = 1.0e-7) -> bool:
    return abs(float(actual) - float(expected)) <= tolerance


def _quantity(value) -> float:
    return float(getattr(value, "Value", value))


def _placement_signature(value) -> tuple[float, ...]:
    return (
        float(value.Base.x),
        float(value.Base.y),
        float(value.Base.z),
        *(float(component) for component in value.Rotation.Q),
    )


def _shape_signature(shape) -> tuple[object, ...]:
    bounds = shape.BoundBox
    return (
        str(shape.ShapeType),
        len(shape.Vertexes),
        len(shape.Edges),
        len(shape.Faces),
        len(shape.Solids),
        round(float(shape.Volume), 9),
        round(float(shape.Area), 9),
        *(round(float(value), 9) for value in (
            bounds.XMin,
            bounds.YMin,
            bounds.ZMin,
            bounds.XMax,
            bounds.YMax,
            bounds.ZMax,
        )),
    )


def _current_state(body):
    return PartGui.resolveModelingObject(body)


def _body_signature(body) -> tuple[object, ...]:
    state = _current_state(body)
    operation = getattr(state, "Operation", None)
    return (
        str(body.VibeCADBodyId),
        body.Tip.Name if body.Tip is not None else None,
        state.Name if state is not None else None,
        str(getattr(state, "OperationId", "") or ""),
        operation.Name if operation is not None else None,
        _shape_signature(body.Shape),
    )


def _task_button(standard_button):
    _process_events()
    for button_box in Gui.getMainWindow().findChildren(QtWidgets.QDialogButtonBox):
        if not button_box.isVisible():
            continue
        button = button_box.button(standard_button)
        if button is not None and button.isVisible() and button.isEnabled():
            return button
    return None


def _cancel_task() -> None:
    button = _task_button(QtWidgets.QDialogButtonBox.Cancel)
    assert button is not None
    button.click()
    _process_events(50)
    assert not Gui.Control.activeDialog()


def _assert_bounds(shape, expected: tuple[float, ...]) -> None:
    bounds = shape.BoundBox
    actual = (
        bounds.XMin,
        bounds.YMin,
        bounds.ZMin,
        bounds.XMax,
        bounds.YMax,
        bounds.ZMax,
    )
    assert all(_close(value, target) for value, target in zip(actual, expected, strict=True))


def _assert_scale_contract(
    document,
    response,
    *,
    body_names,
    prior_states,
    uniform: bool,
    factors: tuple[float, float, float],
    uniform_factor: float,
    center: tuple[float, float, float],
) -> None:
    assert set(response) == {
        "ok",
        "operation",
        "result_mode",
        "bodies",
        "feature",
        "receipt",
        "assistant_undo_available",
    }
    operation = document.getObject(response["operation"]["object_name"])
    bodies = [document.getObject(name) for name in body_names]
    body_ids = [str(body.VibeCADBodyId) for body in bodies]
    assert operation.TypeId == "PartDesign::DesignScale"
    assert operation.ResultOperation == "Modify"
    assert operation.getParentGeoFeatureGroup() is None
    assert operation.BaseFeature is None
    assert operation.Shape.isNull()
    assert bool(operation.Uniform) is uniform
    assert _close(_quantity(operation.UniformScale), uniform_factor)
    assert all(
        _close(_quantity(getattr(operation, f"{axis}Scale")), expected)
        for axis, expected in zip(("X", "Y", "Z"), factors, strict=True)
    )
    assert all(
        _close(getattr(operation.Center, axis), expected)
        for axis, expected in zip(("x", "y", "z"), center, strict=True)
    )
    assert list(operation.InputStates) == list(prior_states)
    assert list(operation.InputBodyIds) == body_ids
    assert list(operation.OutputBodyIds) == body_ids
    assert list(operation.OutputPreviousInputIndices) == list(range(len(bodies)))
    assert all(not str(value) for value in operation.OutputComponentIds)
    assert [_placement_signature(value) for value in operation.InputFrames] == [
        _placement_signature(value) for value in operation.OutputFrames
    ]
    assert list(operation.OutputPresence) == [True] * len(bodies)
    assert len(operation.OutputShapes) == len(bodies)
    assert all(
        not shape.isNull() and shape.isValid() and len(shape.Solids) == 1
        for shape in operation.OutputShapes
    )
    assert [item["body"]["object_name"] for item in response["bodies"]] == list(
        body_names
    )
    assert all(item["present"] is True for item in response["bodies"])
    assert all(item["solid_count"] == 1 for item in response["bodies"])
    assert response["result_mode"] == "modify"
    assert response["feature"] == {
        "mode": "uniform" if uniform else "non_uniform",
        "uniform_factor": uniform_factor if uniform else None,
        "axis_factors": None if uniform else list(factors),
        "center_mm": dict(zip(("x", "y", "z"), center, strict=True)),
        "target_count": len(bodies),
    }
    assert [item["object_name"] for item in response["receipt"]["created"]] == [
        operation.Name
    ]
    assert [item["object_name"] for item in response["receipt"]["changed"]] == list(
        body_names
    )
    assert response["receipt"]["deleted"] == []
    assert response["assistant_undo_available"] is True
    assert all(operation not in body.Group for body in bodies)
    assert all(_current_state(body).Operation is operation for body in bodies)
    assert not any(obj.TypeId == "Part::Scale" for obj in document.Objects)
    timeline = document.getObject("VibeCADTimeline")
    assert timeline is not None and list(timeline.Operations).count(operation) == 1
    PartDesign.validateDesign(operation)


def _operation_record(document, response) -> dict[str, object]:
    operation = document.getObject(response["operation"]["object_name"])
    body_names = [item["body"]["object_name"] for item in response["bodies"]]
    return {
        "operation_name": operation.Name,
        "operation_id": str(operation.OperationId),
        "uniform": bool(operation.Uniform),
        "uniform_factor": _quantity(operation.UniformScale),
        "axis_factors": tuple(
            _quantity(getattr(operation, f"{axis}Scale")) for axis in "XYZ"
        ),
        "center": tuple(float(getattr(operation.Center, axis)) for axis in "xyz"),
        "input_state_names": [state.Name for state in operation.InputStates],
        "input_operation_ids": [str(state.OperationId) for state in operation.InputStates],
        "input_body_ids": list(operation.InputBodyIds),
        "output_body_ids": list(operation.OutputBodyIds),
        "previous_indices": list(operation.OutputPreviousInputIndices),
        "input_frames": [_placement_signature(value) for value in operation.InputFrames],
        "output_frames": [_placement_signature(value) for value in operation.OutputFrames],
        "presence": list(operation.OutputPresence),
        "output_shapes": [_shape_signature(shape) for shape in operation.OutputShapes],
        "body_names": body_names,
        "body_ids": [
            str(document.getObject(name).VibeCADBodyId) for name in body_names
        ],
        "body_shapes": [
            _shape_signature(document.getObject(name).Shape) for name in body_names
        ],
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeModelDesignScaleGate")
        VibeGui._connect_document_observer()
        _process_events()

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-design-scale-gui")
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
        structure, _sketch, _validation = model_structure_capability_definitions()
        definitions = (
            model_feature_capability_definition(),
            structure,
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
                f"model-design-scale-call-{call_number}",
            )
            assert response.get("ok") is succeeds, (arguments, response)
            return response

        before = tuple(obj.Name for obj in document.Objects)
        invalid = _scale_arguments(
            "Invalid Schema",
            ("MissingBody",),
            factors=(2.0,),
            center=(0.0, 0.0, 0.0),
        )
        del invalid["definition"]["factor"]
        failure = native_call("model.transform", invalid, succeeds=False)
        assert failure["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before

        source_specs = (
            ("Uniform Scale A", (10.0, 1.0, 2.0), (4.0, 3.0, 2.0)),
            ("Uniform Scale B", (-4.0, -2.0, 1.0), (2.0, 4.0, 3.0)),
        )
        uniform_bodies = []
        for label, origin, size in source_specs:
            response = native_call(
                "model.feature",
                _box_arguments(label, origin=origin, size=size),
            )
            uniform_bodies.append(response["bodies"][0]["body"]["object_name"])
        source_objects = [document.getObject(name) for name in uniform_bodies]
        source_signatures = [_body_signature(body) for body in source_objects]

        Gui.activeView().setActiveObject("pdbody", source_objects[0])
        Gui.Selection.clearSelection()
        for body in source_objects:
            Gui.Selection.addSelection(body)
        _process_events()
        assert Gui.isCommandActive("PartDesign_Scale")
        before_dialog_objects = tuple(obj.Name for obj in document.Objects)
        Gui.runCommand("PartDesign_Scale", 0)
        _process_events(50)
        assert Gui.Control.activeDialog()
        provisional = document.ActiveObject
        assert provisional.TypeId == "PartDesign::DesignScale"
        assert list(provisional.OutputBodyIds) == [
            str(body.VibeCADBodyId) for body in source_objects
        ]
        main_window = Gui.getMainWindow()
        uniform_toggle = main_window.findChild(
            QtWidgets.QCheckBox,
            "DesignScaleUniform",
        )
        uniform_factor = main_window.findChild(
            QtWidgets.QDoubleSpinBox,
            "DesignScaleUniformFactor",
        )
        axis_factors = [
            main_window.findChild(QtWidgets.QDoubleSpinBox, f"DesignScale{axis}Factor")
            for axis in "XYZ"
        ]
        center_values = [
            main_window.findChild(QtWidgets.QDoubleSpinBox, f"DesignScaleCenter{axis}")
            for axis in "XYZ"
        ]
        target_list = main_window.findChild(QtWidgets.QListWidget, "DesignBodyList")
        assert uniform_toggle is not None and uniform_factor is not None
        assert all(widget is not None for widget in (*axis_factors, *center_values))
        assert target_list is not None and target_list.isEnabled()
        assert uniform_toggle.isChecked()
        assert uniform_factor.isEnabled() and _close(uniform_factor.value(), 1.0)
        assert all(not widget.isEnabled() and _close(widget.value(), 1.0) for widget in axis_factors)
        assert all(_close(widget.value(), 0.0) for widget in center_values)
        assert all(
            _close(widget.minimum(), 1.0e-6)
            and _close(widget.maximum(), 1.0e6)
            for widget in (uniform_factor, *axis_factors)
        )
        assert all(
            _close(widget.minimum(), -1.0e9)
            and _close(widget.maximum(), 1.0e9)
            for widget in center_values
        )
        checked_labels = {
            target_list.item(index).text()
            for index in range(target_list.count())
            if target_list.item(index).checkState() == QtCore.Qt.Checked
        }
        assert checked_labels == {body.Label for body in source_objects}
        uniform_toggle.setChecked(False)
        _process_events()
        assert not uniform_factor.isEnabled()
        assert all(widget.isEnabled() for widget in axis_factors)
        for widget, value in zip(axis_factors, (2.0, 3.0, 4.0), strict=True):
            widget.setValue(value)
        for widget, value in zip(center_values, (1.0, 2.0, 3.0), strict=True):
            widget.setValue(value)
        _process_events(60)
        assert bool(provisional.Uniform) is False
        assert [_quantity(getattr(provisional, f"{axis}Scale")) for axis in "XYZ"] == [
            2.0,
            3.0,
            4.0,
        ]
        assert len(provisional.OutputShapes) == 2
        _cancel_task()
        assert tuple(obj.Name for obj in document.Objects) == before_dialog_objects
        assert [_body_signature(body) for body in source_objects] == source_signatures
        assert document.HasPendingTransaction is False

        prior_states = tuple(_current_state(body) for body in source_objects)
        prior_state_shapes = tuple(_shape_signature(state.Shape) for state in prior_states)
        uniform_response = native_call(
            "model.transform",
            _scale_arguments(
                "Two Body Uniform Scale",
                uniform_bodies,
                factors=(2.0,),
                center=(5.0, 1.0, 2.0),
            ),
        )
        _assert_scale_contract(
            document,
            uniform_response,
            body_names=uniform_bodies,
            prior_states=prior_states,
            uniform=True,
            factors=(1.0, 1.0, 1.0),
            uniform_factor=2.0,
            center=(5.0, 1.0, 2.0),
        )
        _assert_bounds(document.getObject(uniform_bodies[0]).Shape, (15.0, 1.0, 2.0, 23.0, 7.0, 6.0))
        _assert_bounds(document.getObject(uniform_bodies[1]).Shape, (-13.0, -5.0, 0.0, -9.0, 3.0, 6.0))
        assert all(_close(document.getObject(name).Shape.Volume, 192.0) for name in uniform_bodies)
        assert tuple(_shape_signature(state.Shape) for state in prior_states) == prior_state_shapes
        uniform_record = _operation_record(document, uniform_response)
        scaled_signatures = [_body_signature(document.getObject(name)) for name in uniform_bodies]
        operation_name = uniform_record["operation_name"]
        document.undo()
        _process_events()
        assert document.getObject(operation_name) is None
        assert [_body_signature(document.getObject(name)) for name in uniform_bodies] == source_signatures
        document.redo()
        _process_events()
        restored_operation = document.getObject(operation_name)
        assert restored_operation is not None
        assert str(restored_operation.OperationId) == uniform_record["operation_id"]
        assert [_body_signature(document.getObject(name)) for name in uniform_bodies] == scaled_signatures
        PartDesign.validateDesign(restored_operation)

        non_uniform_source = native_call(
            "model.feature",
            _box_arguments(
                "Non-uniform Scale Body",
                origin=(2.0, 3.0, 4.0),
                size=(5.0, 2.0, 1.0),
            ),
        )
        non_uniform_body = non_uniform_source["bodies"][0]["body"]["object_name"]
        non_uniform_object = document.getObject(non_uniform_body)
        non_uniform_prior = (_current_state(non_uniform_object),)
        non_uniform_response = native_call(
            "model.transform",
            _scale_arguments(
                "Design Axis Scale",
                (non_uniform_body,),
                factors=(2.0, 3.0, 4.0),
                center=(1.0, 1.0, 1.0),
            ),
        )
        _assert_scale_contract(
            document,
            non_uniform_response,
            body_names=(non_uniform_body,),
            prior_states=non_uniform_prior,
            uniform=False,
            factors=(2.0, 3.0, 4.0),
            uniform_factor=1.0,
            center=(1.0, 1.0, 1.0),
        )
        _assert_bounds(non_uniform_object.Shape, (3.0, 7.0, 13.0, 13.0, 13.0, 17.0))
        assert _close(non_uniform_object.Shape.Volume, 240.0)
        non_uniform_record = _operation_record(document, non_uniform_response)

        component_result = native_call(
            "model.structure",
            {
                "operation": "new_component",
                "label": "Moved Scale Component",
                "parent_component": None,
            },
        )
        component_name = component_result["component"]["object_name"]
        component = document.getObject(component_name)
        component.Placement = App.Placement(
            App.Vector(40.0, 5.0, 2.0),
            App.Rotation(),
        )
        document.recompute()
        component_source = native_call(
            "model.feature",
            _box_arguments(
                "Component Scale Body",
                origin=(44.0, 7.0, 3.0),
                size=(3.0, 2.0, 2.0),
                destination_component=component_name,
            ),
        )
        component_body_name = component_source["bodies"][0]["body"]["object_name"]
        component_body = document.getObject(component_body_name)
        assert component_body.getParentGeoFeatureGroup() is component
        component_prior = (_current_state(component_body),)
        component_response = native_call(
            "model.transform",
            _scale_arguments(
                "Component Frame Scale",
                (component_body_name,),
                factors=(2.0,),
                center=(40.0, 5.0, 2.0),
            ),
        )
        _assert_scale_contract(
            document,
            component_response,
            body_names=(component_body_name,),
            prior_states=component_prior,
            uniform=True,
            factors=(1.0, 1.0, 1.0),
            uniform_factor=2.0,
            center=(40.0, 5.0, 2.0),
        )
        component_operation = document.getObject(
            component_response["operation"]["object_name"]
        )
        expected_frame = _placement_signature(component.Placement)
        assert [_placement_signature(value) for value in component_operation.InputFrames] == [
            expected_frame
        ]
        assert [_placement_signature(value) for value in component_operation.OutputFrames] == [
            expected_frame
        ]
        _assert_bounds(component_body.Shape, (8.0, 4.0, 2.0, 14.0, 8.0, 6.0))
        _assert_bounds(component_operation.PreviewShape, (48.0, 9.0, 4.0, 54.0, 13.0, 8.0))
        assert _close(component_body.Shape.Volume, 96.0)
        component_record = _operation_record(document, component_response)

        empty_result = native_call(
            "model.structure",
            {
                "operation": "new_body",
                "label": "Empty Scale Body",
                "component": None,
            },
        )
        empty_body = empty_result["body"]["object_name"]
        wrong_type = non_uniform_response["operation"]["object_name"]
        semantic_failures = (
            ("Missing Scale Body", "DeletedScaleBody", "NATIVE_TARGET_INVALID"),
            ("Wrong Scale Type", wrong_type, "NATIVE_TARGET_INVALID"),
            ("Empty Scale Body", empty_body, "NATIVE_MODEL_INVALID"),
        )
        for label, target, error_code in semantic_failures:
            before_objects = tuple(obj.Name for obj in document.Objects)
            response = native_call(
                "model.transform",
                _scale_arguments(label, (target,), factors=(2.0,), center=(0.0, 0.0, 0.0)),
                succeeds=False,
            )
            assert response["error_code"] == error_code
            assert tuple(obj.Name for obj in document.Objects) == before_objects
            assert document.HasPendingTransaction is False

        invalid_calls = []
        for factor in (True, 0.0, 1.0e7):
            invalid_calls.append(
                _scale_arguments(
                    f"Invalid Factor {factor}",
                    (non_uniform_body,),
                    factors=(factor,),
                    center=(0.0, 0.0, 0.0),
                )
            )
        extra = _scale_arguments(
            "Mixed Scale Controls",
            (non_uniform_body,),
            factors=(2.0,),
            center=(0.0, 0.0, 0.0),
        )
        extra["definition"]["x_factor"] = 3.0
        invalid_calls.append(extra)
        for arguments in invalid_calls:
            before_objects = tuple(obj.Name for obj in document.Objects)
            response = native_call("model.transform", arguments, succeeds=False)
            assert response["error_code"] == "NATIVE_ARGUMENTS_INVALID"
            assert tuple(obj.Name for obj in document.Objects) == before_objects
            assert document.HasPendingTransaction is False

        inactive_source = native_call(
            "model.feature",
            _box_arguments(
                "Inactive Scale Candidate",
                origin=(50.0, 0.0, 0.0),
                size=(2.0, 2.0, 2.0),
            ),
        )
        inactive_body_name = inactive_source["bodies"][0]["body"]["object_name"]
        inactive_body = document.getObject(inactive_body_name)
        timeline = document.getObject("VibeCADTimeline")
        timeline_end = int(timeline.Position)
        timeline.Position = 0
        _process_events()
        assert int(timeline.Position) == 0
        # Bodies remain selectable structural identities before their first
        # state, but they are not valid Scale operands until one solid state
        # exists at the current History position.
        assert PartGui.isModelingObjectActive(inactive_body)
        assert _current_state(inactive_body) is None
        before_objects = tuple(obj.Name for obj in document.Objects)
        inactive_failure = native_call(
            "model.transform",
            _scale_arguments(
                "Inactive History Scale",
                (inactive_body_name,),
                factors=(2.0,),
                center=(0.0, 0.0, 0.0),
            ),
            succeeds=False,
        )
        assert inactive_failure["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before_objects
        timeline.Position = timeline_end
        _process_events()
        assert PartGui.isModelingObjectActive(inactive_body)
        assert _current_state(inactive_body) is not None

        rollback_source = native_call(
            "model.feature",
            _box_arguments(
                "Rollback Scale Body",
                origin=(80.0, 0.0, 0.0),
                size=(3.0, 4.0, 5.0),
            ),
        )
        rollback_body_name = rollback_source["bodies"][0]["body"]["object_name"]
        rollback_body = document.getObject(rollback_body_name)
        before_objects = tuple(obj.Name for obj in document.Objects)
        before_signature = _body_signature(rollback_body)
        original_verify = transform_runtime_module.verify_design_operation

        def reject_after_creation(_document, _draft):
            raise NativeModelError("Forced exact Design Scale postcondition failure.")

        transform_runtime_module.verify_design_operation = reject_after_creation
        try:
            rollback = native_call(
                "model.transform",
                _scale_arguments(
                    "Rollback Design Scale",
                    (rollback_body_name,),
                    factors=(2.0,),
                    center=(0.0, 0.0, 0.0),
                ),
                succeeds=False,
            )
        finally:
            transform_runtime_module.verify_design_operation = original_verify
        assert rollback["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before_objects
        assert _body_signature(rollback_body) == before_signature
        assert document.HasPendingTransaction is False

        records = (uniform_record, non_uniform_record, component_record)
        for record in records:
            operation = document.getObject(record["operation_name"])
            before_shapes = [_shape_signature(shape) for shape in operation.OutputShapes]
            assert document.recompute([operation], True, True) is not False
            assert [_shape_signature(shape) for shape in operation.OutputShapes] == before_shapes
            PartDesign.validateDesign(operation)

        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-design-scale-"))
        save_path = save_directory / "ModelDesignScale.FCStd"
        document.saveAs(str(save_path))
        saved_name = document.Name
        App.closeDocument(saved_name)
        document = App.openDocument(str(save_path))
        assert document.recompute() is not False
        _process_events()

        for record in records:
            operation = document.getObject(record["operation_name"])
            assert operation is not None
            assert str(operation.OperationId) == record["operation_id"]
            assert bool(operation.Uniform) is record["uniform"]
            assert _close(_quantity(operation.UniformScale), record["uniform_factor"])
            assert tuple(_quantity(getattr(operation, f"{axis}Scale")) for axis in "XYZ") == record["axis_factors"]
            assert tuple(float(getattr(operation.Center, axis)) for axis in "xyz") == record["center"]
            assert [state.Name for state in operation.InputStates] == record["input_state_names"]
            assert [str(state.OperationId) for state in operation.InputStates] == record["input_operation_ids"]
            assert list(operation.InputBodyIds) == record["input_body_ids"]
            assert list(operation.OutputBodyIds) == record["output_body_ids"]
            assert list(operation.OutputPreviousInputIndices) == record["previous_indices"]
            assert [_placement_signature(value) for value in operation.InputFrames] == record["input_frames"]
            assert [_placement_signature(value) for value in operation.OutputFrames] == record["output_frames"]
            assert list(operation.OutputPresence) == record["presence"]
            assert [_shape_signature(shape) for shape in operation.OutputShapes] == record["output_shapes"]
            for body_name, body_id, shape_signature in zip(
                record["body_names"],
                record["body_ids"],
                record["body_shapes"],
                strict=True,
            ):
                body = document.getObject(body_name)
                assert body is not None
                assert str(body.VibeCADBodyId) == body_id
                assert _shape_signature(body.Shape) == shape_signature
                assert _current_state(body).Operation is operation
                if operation.Name == component_record["operation_name"]:
                    assert body.getParentGeoFeatureGroup().Name == component_name
            assert operation.BaseFeature is None and operation.Shape.isNull()
            PartDesign.validateDesign(operation)

        print("VIBECAD_NATIVE_MODEL_DESIGN_SCALE_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
