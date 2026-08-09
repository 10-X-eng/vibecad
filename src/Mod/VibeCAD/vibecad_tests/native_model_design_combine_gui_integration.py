# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real provider and GUI lifecycle gate for retained Design Combine."""

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
from VibeCADNativeModelBooleanSchema import model_boolean_capability_definition
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeModelFeatureSchema import model_feature_capability_definition
from VibeCADNativeModelStructureSchema import model_structure_capability_definitions
import VibeCADNativeModelBooleanRuntime as boolean_runtime_module
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
    component: str | None = None,
) -> dict[str, object]:
    return {
        "operation": "primitive",
        "label": label,
        "placement": _placement(*origin),
        "result": {
            "mode": "new_body",
            "targets": [],
            "destination_component": (
                {"object_name": component} if component is not None else None
            ),
        },
        "definition": {
            "kind": "box",
            "length_mm": size[0],
            "width_mm": size[1],
            "height_mm": size[2],
        },
    }


def _combine_arguments(
    label: str,
    mode: str,
    result_body: str,
    tool_bodies: tuple[str, ...],
    *,
    keep_tools: bool,
) -> dict[str, object]:
    return {
        "operation": "combine",
        "label": label,
        "definition": {
            "mode": mode,
            "result_body": {"object_name": result_body},
            "tool_bodies": [
                {"object_name": object_name} for object_name in tool_bodies
            ],
            "keep_tools": keep_tools,
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


def _close(actual: float, expected: float, tolerance: float = 1.0e-7) -> bool:
    return abs(float(actual) - float(expected)) <= tolerance


def _placement_signature(value) -> tuple[float, ...]:
    return (
        float(value.Base.x),
        float(value.Base.y),
        float(value.Base.z),
        *(float(component) for component in value.Rotation.Q),
    )


def _shape_signature(shape) -> tuple[object, ...]:
    if shape is None or shape.isNull():
        return ("Null",)
    bounds = shape.BoundBox
    return (
        str(shape.ShapeType),
        len(shape.Vertexes),
        len(shape.Edges),
        len(shape.Faces),
        len(shape.Solids),
        round(float(shape.Volume), 8),
        round(float(shape.Area), 8),
        *(round(float(value), 8) for value in (
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


def _output_state(body):
    return getattr(getattr(body, "Tip", None), "CurrentState", None)


def _body_signature(body) -> tuple[object, ...]:
    state = _current_state(body)
    operation = getattr(state, "Operation", None)
    return (
        str(body.VibeCADBodyId),
        body.Tip.Name if body.Tip is not None else None,
        state.Name if state is not None else None,
        str(getattr(state, "OperationId", "") or ""),
        operation.Name if operation is not None else None,
        bool(getattr(state, "Present", True)) if state is not None else None,
        _placement_signature(body.getGlobalPlacement()),
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


def _assert_combine(
    document,
    response,
    *,
    mode: str,
    result_body,
    tool_bodies,
    prior_states,
    keep_tools: bool,
    expected_volume: float,
) -> object:
    assert set(response) == {
        "ok",
        "operation",
        "mode",
        "keep_tools",
        "bodies",
        "receipt",
        "assistant_undo_available",
    }
    operation = document.getObject(response["operation"]["object_name"])
    bodies = (result_body, *tool_bodies)
    body_ids = tuple(str(body.VibeCADBodyId) for body in bodies)
    output_count = 1 if keep_tools else len(bodies)
    assert operation.TypeId == "PartDesign::DesignCombine"
    assert operation.ResultOperation == mode.title()
    assert str(operation.ResultBodyId) == body_ids[0]
    assert bool(operation.KeepTools) is keep_tools
    assert operation.BaseFeature is None and operation.Shape.isNull()
    assert operation.getParentGeoFeatureGroup() is None
    assert list(operation.InputStates) == list(prior_states)
    assert tuple(operation.InputBodyIds) == body_ids
    assert tuple(operation.OutputBodyIds) == body_ids[:output_count]
    assert list(operation.OutputPreviousInputIndices) == list(range(output_count))
    assert list(operation.OutputPresence) == [True] + [False] * (output_count - 1)
    assert all(not str(value) for value in operation.OutputComponentIds)
    assert tuple(operation.TargetBodyIds) == body_ids[:output_count]
    assert len(operation.OutputShapes) == output_count
    assert not operation.OutputShapes[0].isNull()
    assert all(shape.isNull() for shape in operation.OutputShapes[1:])
    assert _close(result_body.Shape.Volume, expected_volume)
    assert _shape_signature(result_body.Shape) == _shape_signature(
        operation.OutputShapes[0]
    )
    assert response["mode"] == mode
    assert response["keep_tools"] is keep_tools
    assert [item["body"]["object_name"] for item in response["bodies"]] == [
        body.Name for body in bodies
    ]
    assert [item["role"] for item in response["bodies"]] == [
        "result",
        *(["tool"] * len(tool_bodies)),
    ]
    expected_present = [True] + [keep_tools] * len(tool_bodies)
    assert [item["present"] for item in response["bodies"]] == expected_present
    assert _output_state(result_body).Operation is operation
    assert _output_state(result_body).PreviousState is prior_states[0]
    for index, body in enumerate(tool_bodies, 1):
        current = _current_state(body)
        if keep_tools:
            assert current is prior_states[index]
        else:
            output_state = _output_state(body)
            assert current is None
            assert output_state.Operation is operation
            assert output_state.PreviousState is prior_states[index]
            assert output_state.Present is False and body.Shape.isNull()
    changed = [item["object_name"] for item in response["receipt"]["changed"]]
    assert changed == [body.Name for body in bodies[:output_count]]
    assert [item["object_name"] for item in response["receipt"]["created"]] == [
        operation.Name
    ]
    assert response["receipt"]["deleted"] == []
    assert response["assistant_undo_available"] is True
    timeline = document.getObject("VibeCADTimeline")
    assert timeline is not None and list(timeline.Operations).count(operation) == 1
    PartDesign.validateDesign(operation)
    return operation


def _record(operation, bodies) -> dict[str, object]:
    return {
        "name": operation.Name,
        "operation_id": str(operation.OperationId),
        "mode": str(operation.ResultOperation),
        "result_body_id": str(operation.ResultBodyId),
        "keep_tools": bool(operation.KeepTools),
        "input_names": [state.Name for state in operation.InputStates],
        "input_operation_ids": [str(state.OperationId) for state in operation.InputStates],
        "input_body_ids": list(operation.InputBodyIds),
        "input_frames": [_placement_signature(value) for value in operation.InputFrames],
        "output_body_ids": list(operation.OutputBodyIds),
        "output_frames": [_placement_signature(value) for value in operation.OutputFrames],
        "previous_indices": list(operation.OutputPreviousInputIndices),
        "presence": list(operation.OutputPresence),
        "output_shapes": [_shape_signature(shape) for shape in operation.OutputShapes],
        "body_names": [body.Name for body in bodies],
        "body_ids": [str(body.VibeCADBodyId) for body in bodies],
        "body_shapes": [_shape_signature(body.Shape) for body in bodies],
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeModelDesignCombineGate")
        VibeGui._connect_document_observer()
        _process_events()

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-design-combine-gui")
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
            model_boolean_capability_definition(),
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
                f"model-design-combine-call-{call_number}",
            )
            assert response.get("ok") is succeeds, (arguments, response)
            return response

        def new_box(label, origin, size, component=None):
            response = native_call(
                "model.feature",
                _box_arguments(
                    label,
                    origin=origin,
                    size=size,
                    component=component,
                ),
            )
            return document.getObject(response["bodies"][0]["body"]["object_name"])

        # Freeze the actual human selection roles, defaults, task controls, and cancel.
        human_result = new_box("Human Result", (0, 0, 0), (10, 10, 10))
        human_tool = new_box("Human Tool", (5, 0, 0), (10, 10, 10))
        human_signatures = (_body_signature(human_result), _body_signature(human_tool))
        before_human = tuple(obj.Name for obj in document.Objects)
        Gui.activeView().setActiveObject("pdbody", None)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(human_result)
        Gui.Selection.addSelection(human_tool)
        _process_events()
        assert Gui.isCommandActive("PartDesign_Combine")
        Gui.runCommand("PartDesign_Combine", 0)
        _process_events(50)
        assert Gui.Control.activeDialog()
        provisional = document.ActiveObject
        assert provisional.TypeId == "PartDesign::DesignCombine"
        assert provisional.ResultOperation == "Join"
        assert str(provisional.ResultBodyId) == str(human_result.VibeCADBodyId)
        assert list(provisional.InputStates) == [
            _current_state(human_result),
            _current_state(human_tool),
        ]
        assert not provisional.KeepTools
        main_window = Gui.getMainWindow()
        mode_box = main_window.findChild(QtWidgets.QComboBox, "DesignResultOperation")
        result_box = main_window.findChild(QtWidgets.QComboBox, "DesignResultBody")
        body_list = main_window.findChild(QtWidgets.QListWidget, "DesignBodyList")
        keep_tools = next(
            (
                widget
                for widget in main_window.findChildren(QtWidgets.QCheckBox)
                if widget.isVisible() and widget.text() == "Keep tool Bodies"
            ),
            None,
        )
        assert mode_box is not None and result_box is not None and body_list is not None
        assert keep_tools is not None
        assert [mode_box.itemData(index) for index in range(mode_box.count())] == [
            "Join",
            "Cut",
            "Intersect",
        ]
        assert result_box.currentData() == str(human_result.VibeCADBodyId)
        checked_ids = {
            str(body_list.item(index).data(QtCore.Qt.UserRole) or "")
            for index in range(body_list.count())
            if body_list.item(index).checkState() == QtCore.Qt.Checked
        }
        # The list labels are user-facing; the operation ports remain the role authority.
        assert human_tool.Label in {
            body_list.item(index).text()
            for index in range(body_list.count())
            if body_list.item(index).checkState() == QtCore.Qt.Checked
        }
        assert len(checked_ids) <= 1
        mode_box.setCurrentIndex(mode_box.findData("Cut"))
        keep_tools.setChecked(True)
        _process_events(40)
        assert provisional.ResultOperation == "Cut" and provisional.KeepTools
        _cancel_task()
        assert tuple(obj.Name for obj in document.Objects) == before_human
        assert (_body_signature(human_result), _body_signature(human_tool)) == human_signatures
        assert document.HasPendingTransaction is False

        # Closed schema failures are no-ops.
        invalid = _combine_arguments(
            "Invalid Combine",
            "join",
            human_result.Name,
            (human_tool.Name,),
            keep_tools=False,
        )
        del invalid["definition"]["keep_tools"]
        before = tuple(obj.Name for obj in document.Objects)
        failure = native_call("model.boolean", invalid, succeeds=False)
        assert failure["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before

        # Join consumes tool identities when requested and is exactly undoable.
        join_result = new_box("Join Result", (30, 0, 0), (10, 10, 10))
        join_tool = new_box("Join Tool", (35, 0, 0), (10, 10, 10))
        join_prior = (_current_state(join_result), _current_state(join_tool))
        join_before = (_body_signature(join_result), _body_signature(join_tool))
        join_response = native_call(
            "model.boolean",
            _combine_arguments(
                "Native Join",
                "join",
                join_result.Name,
                (join_tool.Name,),
                keep_tools=False,
            ),
        )
        join_operation = _assert_combine(
            document,
            join_response,
            mode="join",
            result_body=join_result,
            tool_bodies=(join_tool,),
            prior_states=join_prior,
            keep_tools=False,
            expected_volume=1500.0,
        )
        join_after = (_body_signature(join_result), _body_signature(join_tool))
        join_name = join_operation.Name
        document.undo()
        _process_events()
        assert document.getObject(join_name) is None
        assert (_body_signature(join_result), _body_signature(join_tool)) == join_before
        document.redo()
        _process_events()
        join_operation = document.getObject(join_name)
        assert join_operation is not None
        assert (_body_signature(join_result), _body_signature(join_tool)) == join_after
        PartDesign.validateDesign(join_operation)

        # Join can preserve multiple ordered tools.
        kept_result = new_box("Kept Join Result", (60, 0, 0), (10, 10, 10))
        kept_tool_a = new_box("Kept Join Tool A", (65, 0, 0), (10, 10, 10))
        kept_tool_b = new_box("Kept Join Tool B", (72, 0, 0), (6, 10, 10))
        kept_tools_before = (_body_signature(kept_tool_a), _body_signature(kept_tool_b))
        kept_prior = tuple(_current_state(body) for body in (
            kept_result,
            kept_tool_a,
            kept_tool_b,
        ))
        kept_response = native_call(
            "model.boolean",
            _combine_arguments(
                "Native Kept Join",
                "join",
                kept_result.Name,
                (kept_tool_a.Name, kept_tool_b.Name),
                keep_tools=True,
            ),
        )
        kept_operation = _assert_combine(
            document,
            kept_response,
            mode="join",
            result_body=kept_result,
            tool_bodies=(kept_tool_a, kept_tool_b),
            prior_states=kept_prior,
            keep_tools=True,
            expected_volume=1800.0,
        )
        assert (_body_signature(kept_tool_a), _body_signature(kept_tool_b)) == kept_tools_before

        # Cut and Intersect each preserve their exact human mode semantics.
        cut_result = new_box("Cut Result", (90, 0, 0), (10, 10, 10))
        cut_tool = new_box("Cut Tool", (93, 3, 3), (4, 4, 4))
        cut_prior = (_current_state(cut_result), _current_state(cut_tool))
        cut_response = native_call(
            "model.boolean",
            _combine_arguments(
                "Native Cut",
                "cut",
                cut_result.Name,
                (cut_tool.Name,),
                keep_tools=False,
            ),
        )
        cut_operation = _assert_combine(
            document,
            cut_response,
            mode="cut",
            result_body=cut_result,
            tool_bodies=(cut_tool,),
            prior_states=cut_prior,
            keep_tools=False,
            expected_volume=936.0,
        )

        common_result = new_box("Intersect Result", (120, 0, 0), (10, 10, 10))
        common_tool = new_box("Intersect Tool", (125, 0, 0), (10, 10, 10))
        common_prior = (_current_state(common_result), _current_state(common_tool))
        common_tool_before = _body_signature(common_tool)
        common_response = native_call(
            "model.boolean",
            _combine_arguments(
                "Native Intersect",
                "intersect",
                common_result.Name,
                (common_tool.Name,),
                keep_tools=True,
            ),
        )
        common_operation = _assert_combine(
            document,
            common_response,
            mode="intersect",
            result_body=common_result,
            tool_bodies=(common_tool,),
            prior_states=common_prior,
            keep_tools=True,
            expected_volume=500.0,
        )
        assert _body_signature(common_tool) == common_tool_before

        # Global Body frames are honored across distinct Components.
        component_a = native_call(
            "model.structure",
            {
                "operation": "new_component",
                "label": "Combine Component A",
                "parent_component": None,
            },
        )
        component_b = native_call(
            "model.structure",
            {
                "operation": "new_component",
                "label": "Combine Component B",
                "parent_component": None,
            },
        )
        component_a_name = component_a["component"]["object_name"]
        component_b_name = component_b["component"]["object_name"]
        document.getObject(component_a_name).Placement = App.Placement(
            App.Vector(160, 5, 0), App.Rotation()
        )
        document.getObject(component_b_name).Placement = App.Placement(
            App.Vector(170, -5, 0), App.Rotation()
        )
        document.recompute()
        framed_result = new_box(
            "Framed Result",
            (160, 5, 0),
            (10, 10, 10),
            component_a_name,
        )
        framed_tool = new_box(
            "Framed Tool",
            (165, 5, 0),
            (10, 10, 10),
            component_b_name,
        )
        framed_prior = (_current_state(framed_result), _current_state(framed_tool))
        framed_response = native_call(
            "model.boolean",
            _combine_arguments(
                "Framed Native Join",
                "join",
                framed_result.Name,
                (framed_tool.Name,),
                keep_tools=True,
            ),
        )
        framed_operation = _assert_combine(
            document,
            framed_response,
            mode="join",
            result_body=framed_result,
            tool_bodies=(framed_tool,),
            prior_states=framed_prior,
            keep_tools=True,
            expected_volume=1500.0,
        )
        preview = framed_operation.PreviewShape.BoundBox
        assert _close(preview.XMin, 160.0) and _close(preview.XMax, 175.0)
        assert framed_result.getParentGeoFeatureGroup().Name == component_a_name
        assert framed_tool.getParentGeoFeatureGroup().Name == component_b_name

        # Missing, wrong-type, empty, inactive, and failed geometry are no-ops.
        empty = native_call(
            "model.structure",
            {
                "operation": "new_body",
                "label": "Empty Combine Body",
                "component": None,
            },
        )
        empty_name = empty["body"]["object_name"]
        wrong_type = join_operation.Name
        for label, target, expected_error in (
            ("Missing", "MissingCombineBody", "NATIVE_TARGET_INVALID"),
            ("Wrong type", wrong_type, "NATIVE_TARGET_INVALID"),
            ("Empty", empty_name, "NATIVE_MODEL_INVALID"),
        ):
            before = tuple(obj.Name for obj in document.Objects)
            response = native_call(
                "model.boolean",
                _combine_arguments(
                    label,
                    "join",
                    human_result.Name,
                    (target,),
                    keep_tools=True,
                ),
                succeeds=False,
            )
            assert response["error_code"] == expected_error
            assert tuple(obj.Name for obj in document.Objects) == before
            assert document.HasPendingTransaction is False

        inactive_result = new_box("Inactive Result", (200, 0, 0), (5, 5, 5))
        inactive_tool = new_box("Inactive Tool", (202, 0, 0), (5, 5, 5))
        timeline = document.getObject("VibeCADTimeline")
        timeline_end = int(timeline.Position)
        timeline.Position = 0
        _process_events()
        before = tuple(obj.Name for obj in document.Objects)
        inactive_failure = native_call(
            "model.boolean",
            _combine_arguments(
                "Inactive Combine",
                "join",
                inactive_result.Name,
                (inactive_tool.Name,),
                keep_tools=False,
            ),
            succeeds=False,
        )
        assert inactive_failure["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        timeline.Position = timeline_end
        _process_events()

        disjoint_result = new_box("Disjoint Result", (220, 0, 0), (4, 4, 4))
        disjoint_tool = new_box("Disjoint Tool", (240, 0, 0), (4, 4, 4))
        disjoint_before = (_body_signature(disjoint_result), _body_signature(disjoint_tool))
        before = tuple(obj.Name for obj in document.Objects)
        disjoint_failure = native_call(
            "model.boolean",
            _combine_arguments(
                "Disjoint Join",
                "join",
                disjoint_result.Name,
                (disjoint_tool.Name,),
                keep_tools=False,
            ),
            succeeds=False,
        )
        assert disjoint_failure["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert (_body_signature(disjoint_result), _body_signature(disjoint_tool)) == disjoint_before
        assert document.HasPendingTransaction is False

        # A target changed after preflight is rejected and fully rolled back.
        stale_result = new_box("Stale Result", (260, 0, 0), (6, 6, 6))
        stale_tool = new_box("Stale Tool", (263, 0, 0), (6, 6, 6))
        stale_before = (_body_signature(stale_result), _body_signature(stale_tool))
        before = tuple(obj.Name for obj in document.Objects)
        original_create = boolean_runtime_module.create_design_combine

        def change_after_preflight(target_document, **kwargs):
            stale_tool.Placement = App.Placement(App.Vector(1, 0, 0), App.Rotation())
            return original_create(target_document, **kwargs)

        boolean_runtime_module.create_design_combine = change_after_preflight
        try:
            stale_failure = native_call(
                "model.boolean",
                _combine_arguments(
                    "Stale Combine",
                    "join",
                    stale_result.Name,
                    (stale_tool.Name,),
                    keep_tools=True,
                ),
                succeeds=False,
            )
        finally:
            boolean_runtime_module.create_design_combine = original_create
        assert stale_failure["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert (_body_signature(stale_result), _body_signature(stale_tool)) == stale_before

        # A rejected postcondition restores operation and every Body state.
        rollback_result = new_box("Rollback Result", (280, 0, 0), (8, 8, 8))
        rollback_tool = new_box("Rollback Tool", (284, 0, 0), (8, 8, 8))
        rollback_before = (_body_signature(rollback_result), _body_signature(rollback_tool))
        before = tuple(obj.Name for obj in document.Objects)
        original_verify = boolean_runtime_module.verify_design_combine

        def reject_after_creation(_document, _draft):
            raise NativeModelError("Forced Design Combine postcondition failure.")

        boolean_runtime_module.verify_design_combine = reject_after_creation
        try:
            rollback_failure = native_call(
                "model.boolean",
                _combine_arguments(
                    "Rollback Combine",
                    "join",
                    rollback_result.Name,
                    (rollback_tool.Name,),
                    keep_tools=False,
                ),
                succeeds=False,
            )
        finally:
            boolean_runtime_module.verify_design_combine = original_verify
        assert rollback_failure["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert (_body_signature(rollback_result), _body_signature(rollback_tool)) == rollback_before
        assert document.HasPendingTransaction is False

        records = (
            _record(join_operation, (join_result, join_tool)),
            _record(kept_operation, (kept_result, kept_tool_a, kept_tool_b)),
            _record(cut_operation, (cut_result, cut_tool)),
            _record(common_operation, (common_result, common_tool)),
            _record(framed_operation, (framed_result, framed_tool)),
        )
        for record in records:
            operation = document.getObject(record["name"])
            before_shapes = [_shape_signature(shape) for shape in operation.OutputShapes]
            assert document.recompute([operation], True, True) is not False
            assert [_shape_signature(shape) for shape in operation.OutputShapes] == before_shapes
            PartDesign.validateDesign(operation)

        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-design-combine-"))
        save_path = save_directory / "ModelDesignCombine.FCStd"
        document.saveAs(str(save_path))
        saved_name = document.Name
        App.closeDocument(saved_name)
        document = App.openDocument(str(save_path))
        assert document.recompute() is not False
        _process_events()
        for record in records:
            operation = document.getObject(record["name"])
            assert operation is not None
            assert document.recompute([operation], True, True) is not False
            assert str(operation.OperationId) == record["operation_id"]
            assert operation.ResultOperation == record["mode"]
            assert str(operation.ResultBodyId) == record["result_body_id"]
            assert bool(operation.KeepTools) is record["keep_tools"]
            assert [state.Name for state in operation.InputStates] == record["input_names"]
            assert [str(state.OperationId) for state in operation.InputStates] == record["input_operation_ids"]
            assert list(operation.InputBodyIds) == record["input_body_ids"]
            assert [_placement_signature(value) for value in operation.InputFrames] == record["input_frames"]
            assert list(operation.OutputBodyIds) == record["output_body_ids"]
            assert [_placement_signature(value) for value in operation.OutputFrames] == record["output_frames"]
            assert list(operation.OutputPreviousInputIndices) == record["previous_indices"]
            assert list(operation.OutputPresence) == record["presence"]
            assert [_shape_signature(shape) for shape in operation.OutputShapes] == record["output_shapes"]
            assert operation.PreviewShape.isNull()
            assert "Transient" in operation.getPropertyStatus("PreviewShape")
            for body_name, body_id, shape in zip(
                record["body_names"],
                record["body_ids"],
                record["body_shapes"],
                strict=True,
            ):
                body = document.getObject(body_name)
                assert body is not None and str(body.VibeCADBodyId) == body_id
                assert _shape_signature(body.Shape) == shape
            PartDesign.validateDesign(operation)

        print("VIBECAD_NATIVE_MODEL_DESIGN_COMBINE_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
