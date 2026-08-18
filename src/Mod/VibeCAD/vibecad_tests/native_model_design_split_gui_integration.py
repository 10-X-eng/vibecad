# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real GUI/provider lifecycle gate for identity-safe Design Split."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
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


def _close(left: float, right: float, tolerance: float = 1.0e-6) -> bool:
    return abs(float(left) - float(right)) <= tolerance


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


def _split_arguments(
    label: str,
    source_body,
    splitters,
    retained_region_index: int,
) -> dict[str, object]:
    return {
        "operation": "split",
        "label": label,
        "definition": {
            "source_body": {"object_name": source_body.Name},
            "splitters": [
                {
                    "object_name": obj.Name,
                    "subelements": list(subelements),
                }
                for obj, subelements in splitters
            ],
            "retained_region_index": retained_region_index,
        },
    }


def _turn(*definitions) -> NativeTurnSnapshot:
    schemas = []
    action_ids = []
    for definition in definitions:
        operations = tuple(variant.operation for variant in definition.variants)
        schemas.append(definition.provider_schema(operations))
        action_ids.extend(
            action_id for variant in definition.variants for action_id in variant.action_ids
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
        round(float(shape.Volume), 7),
        round(float(shape.Area), 7),
        *(round(float(getattr(bounds, name)), 7) for name in (
            "XMin",
            "XMax",
            "YMin",
            "YMax",
            "ZMin",
            "ZMax",
        )),
    )


def _placement_signature(value) -> tuple[float, ...]:
    return (
        float(value.Base.x),
        float(value.Base.y),
        float(value.Base.z),
        *(float(component) for component in value.Rotation.Q),
    )


def _body_signature(body) -> tuple[object, ...]:
    current = PartGui.resolveModelingObject(body)
    operation = getattr(current, "Operation", None)
    return (
        str(body.VibeCADBodyId),
        body.Tip.Name if body.Tip is not None else None,
        current.Name if current is not None else None,
        str(getattr(current, "BodyStateId", "") or ""),
        operation.Name if operation is not None else None,
        _placement_signature(body.getGlobalPlacement()),
        _shape_signature(body.Shape),
    )


def _publish_shape(document, name: str, shape, *, placement=None):
    document.openTransaction(f"Create {name}")
    try:
        obj = document.addObject("Part::Feature", name)
        obj.Label = name
        obj.Shape = shape
        if placement is not None:
            obj.Placement = placement
        PartDesign.initializeDesignDefinition(obj)
        document.publishProvisionalTimelineOperationBlock(obj, (), ())
        assert document.recompute([obj], True, True) is not False
        PartDesign.finalizeDesignDefinition(obj)
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    assert PartGui.isModelingObjectActive(obj)
    return obj


def _plane(document, name: str, x: float, *, size: float = 30.0):
    return _publish_shape(
        document,
        name,
        Part.makePlane(
            size,
            size,
            App.Vector(x, 20, -10),
            App.Vector(1, 0, 0),
        ),
    )


def _link_groups(values) -> tuple[tuple[object, tuple[str, ...]], ...]:
    result = []
    for value in tuple(values):
        if isinstance(value, tuple):
            obj, raw = value
            names = (raw,) if isinstance(raw, str) else tuple(raw or ())
            names = tuple(str(item) for item in names if str(item))
        else:
            obj, names = value, ()
        result.append((obj, names))
    return tuple(result)


def _task_button(standard_button):
    _process_events()
    for button_box in Gui.getMainWindow().findChildren(QtWidgets.QDialogButtonBox):
        if not button_box.isVisible():
            continue
        button = button_box.button(standard_button)
        if button is not None and button.isVisible() and button.isEnabled():
            return button
    return None


def _finish_task(standard_button) -> None:
    button = _task_button(standard_button)
    assert button is not None
    button.click()
    _process_events(50)
    assert not Gui.Control.activeDialog()


def _assert_split(
    document,
    response,
    *,
    source,
    source_state,
    splitters,
    retained_region_index: int,
    source_volume: float,
):
    assert set(response) == {
        "ok",
        "operation",
        "source_body",
        "splitter_count",
        "retained_region_index",
        "regions",
        "receipt",
        "assistant_undo_available",
    }
    operation = document.getObject(response["operation"]["object_name"])
    bodies = tuple(
        document.getObject(region["body"]["object_name"])
        for region in response["regions"]
    )
    body_ids = tuple(str(body.VibeCADBodyId) for body in bodies)
    output_shapes = tuple(operation.OutputShapes)
    source_frame = source.getGlobalPlacement()
    assert operation.TypeId == "PartDesign::DesignSplit"
    assert operation.ResultOperation == "Split"
    assert str(operation.SourceBodyId) == str(source.VibeCADBodyId)
    assert operation.RetainedRegionChosen
    assert operation.BaseFeature is None and operation.Shape.isNull()
    assert operation.getParentGeoFeatureGroup() is None
    assert operation.InputStates[0] is source_state
    assert _link_groups(operation.Splitters) == tuple(splitters)
    assert len(operation.SplitterFrames) == len(splitters)
    assert len(operation.RegionWitnesses) == len(bodies)
    assert tuple(operation.OutputBodyIds) == body_ids
    assert tuple(operation.OutputPreviousInputIndices) == (0,) + (-1,) * (len(bodies) - 1)
    assert tuple(operation.OutputPresence) == (True,) * len(bodies)
    assert tuple(operation.TargetBodyIds) == body_ids
    assert tuple(operation.TargetFrames) == tuple(operation.OutputFrames)
    assert all(frame == source_frame for frame in operation.OutputFrames)
    assert len(output_shapes) == len(bodies)
    assert operation.PreviewShape.isValid() and len(operation.PreviewShape.Solids) == len(bodies)
    assert bodies[0] is source
    assert response["source_body"]["object_name"] == source.Name
    assert response["splitter_count"] == len(splitters)
    assert response["retained_region_index"] == retained_region_index
    assert [region["retains_source_identity"] for region in response["regions"]] == [
        True,
        *([False] * (len(bodies) - 1)),
    ]
    assert all(
        _shape_signature(body.Shape) == _shape_signature(output_shapes[index])
        for index, body in enumerate(bodies)
    )
    assert _close(sum(body.Shape.Volume for body in bodies), source_volume)
    assert all(body.getParentGeoFeatureGroup() is source.getParentGeoFeatureGroup() for body in bodies)
    assert PartGui.resolveModelingObject(source).PreviousState is source_state
    created_names = [item["object_name"] for item in response["receipt"]["created"]]
    expected_created = sorted([operation.Name, *(body.Name for body in bodies[1:])])
    assert created_names == expected_created, (created_names, expected_created)
    assert [item["object_name"] for item in response["receipt"]["changed"]] == [source.Name]
    assert response["receipt"]["replaced"] == []
    assert response["assistant_undo_available"] is True, (
        response,
        int(document.UndoCount),
        tuple(document.UndoNames),
    )
    PartDesign.validateDesign(operation)
    return operation, bodies


def _record(operation, bodies) -> dict[str, object]:
    return {
        "name": operation.Name,
        "label": str(operation.Label),
        "operation_id": str(operation.OperationId),
        "source_body_id": str(operation.SourceBodyId),
        "input_names": [state.Name for state in operation.InputStates],
        "input_body_ids": list(operation.InputBodyIds),
        "input_frames": [_placement_signature(value) for value in operation.InputFrames],
        "splitters": [(obj.Name, list(names)) for obj, names in _link_groups(operation.Splitters)],
        "splitter_frames": [_placement_signature(value) for value in operation.SplitterFrames],
        "witnesses": [(value.x, value.y, value.z) for value in operation.RegionWitnesses],
        "body_names": [body.Name for body in bodies],
        "body_ids": [str(body.VibeCADBodyId) for body in bodies],
        "body_states": [PartGui.resolveModelingObject(body).Name for body in bodies],
        "body_shapes": [_shape_signature(body.Shape) for body in bodies],
        "output_shapes": [_shape_signature(shape) for shape in operation.OutputShapes],
        "output_frames": [_placement_signature(value) for value in operation.OutputFrames],
        "component_ids": list(operation.OutputComponentIds),
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temp_directory = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeModelDesignSplitGate")
        VibeGui._connect_document_observer()
        _process_events()

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-design-split-gui")
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
        structure, _sketch, _open_sketch, _validation = (
            model_structure_capability_definitions()
        )
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
                f"model-design-split-call-{call_number}",
            )
            assert response.get("ok") is succeeds, (arguments, response)
            return response

        def new_box(label, origin, size=(10.0, 10.0, 10.0), component=None):
            response = native_call(
                "model.feature",
                _box_arguments(label, origin=origin, size=size, component=component),
            )
            return document.getObject(response["bodies"][0]["body"]["object_name"])

        # Freeze the actual task controls, definition editing, region choice, accept, and cancel.
        human_source = new_box("Human Split Source", (0, 0, 0))
        human_plane = _plane(document, "HumanSplitPlane", 5.0)
        human_second = _plane(document, "HumanSecondSplitPlane", 7.0)
        source_state = PartGui.resolveModelingObject(human_source)
        Gui.activeView().setActiveObject("pdbody", None)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(human_source)
        Gui.Selection.addSelection(human_plane)
        _process_events()
        assert Gui.isCommandActive("PartDesign_Split")
        Gui.runCommand("PartDesign_Split", 0)
        _process_events(50)
        assert Gui.Control.activeDialog()
        human_operation = next(
            obj for obj in document.Objects if obj.TypeId == "PartDesign::DesignSplit"
        )
        assert human_operation.ResultOperation == "Split"
        assert str(human_operation.SourceBodyId) == str(human_source.VibeCADBodyId)
        assert tuple(human_operation.InputStates) == (source_state,)
        assert not human_operation.RetainedRegionChosen
        assert tuple(human_operation.OutputBodyIds) == ()
        main_window = Gui.getMainWindow()
        source_selector = main_window.findChild(QtWidgets.QComboBox, "DesignResultBody")
        region_selector = main_window.findChild(
            QtWidgets.QComboBox,
            "DesignSplitRetainedRegion",
        )
        definition_list = main_window.findChild(QtWidgets.QListWidget, "DesignBodyList")
        add_definition = main_window.findChild(
            QtWidgets.QPushButton,
            "DesignSplitAddDefinitions",
        )
        remove_definition = main_window.findChild(
            QtWidgets.QPushButton,
            "DesignSplitRemoveDefinitions",
        )
        assert all(
            item is not None
            for item in (
                source_selector,
                region_selector,
                definition_list,
                add_definition,
                remove_definition,
            )
        )
        assert not source_selector.isEnabled()
        assert source_selector.currentData() == str(human_source.VibeCADBodyId)
        assert region_selector.count() == 3 and region_selector.currentIndex() == 0
        assert definition_list.count() == 1
        Gui.Selection.addSelection(human_second)
        add_definition.click()
        _process_events(50)
        assert definition_list.count() == 2 and region_selector.count() == 4
        definition_list.setCurrentRow(1)
        _process_events()
        assert remove_definition.isEnabled()
        remove_definition.click()
        _process_events(50)
        assert definition_list.count() == 1 and region_selector.count() == 3
        region_selector.setCurrentIndex(2)
        _process_events(50)
        assert human_operation.RetainedRegionChosen
        assert len(human_operation.OutputBodyIds) == 2
        assert str(human_operation.OutputBodyIds[0]) == str(human_source.VibeCADBodyId)
        assert tuple(human_operation.OutputPreviousInputIndices) == (0, -1)
        _finish_task(QtWidgets.QDialogButtonBox.Ok)
        assert len(human_operation.OutputShapes) == 2
        assert human_source.Shape.BoundBox.XMin >= 5.0 - 1.0e-6
        PartDesign.validateDesign(human_operation)

        cancel_source = new_box("Cancelled Split Source", (20, 0, 0))
        cancel_plane = _plane(document, "CancelledSplitPlane", 25.0)
        cancel_signature = _body_signature(cancel_source)
        before_cancel = tuple(obj.Name for obj in document.Objects)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(cancel_source)
        Gui.Selection.addSelection(cancel_plane)
        _process_events()
        Gui.runCommand("PartDesign_Split", 0)
        _process_events(50)
        assert Gui.Control.activeDialog()
        _finish_task(QtWidgets.QDialogButtonBox.Cancel)
        assert tuple(obj.Name for obj in document.Objects) == before_cancel
        assert _body_signature(cancel_source) == cancel_signature
        assert not document.HasPendingTransaction

        # Closed schema and exact-target failures are no-ops.
        invalid = _split_arguments(
            "Invalid Split",
            cancel_source,
            ((cancel_plane, ()),),
            0,
        )
        del invalid["definition"]["retained_region_index"]
        before = tuple(obj.Name for obj in document.Objects)
        response = native_call("model.boolean", invalid, succeeds=False)
        assert response["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before

        empty = native_call(
            "model.structure",
            {
                "operation": "new_body",
                "label": "Empty Split Body",
                "component": None,
            },
        )
        empty_body = document.getObject(empty["body"]["object_name"])
        outside_plane = _plane(document, "OutsideSplitPlane", 1000.0)
        wire = _publish_shape(
            document,
            "WireSplitDefinition",
            Part.makeLine(App.Vector(0, 0, 0), App.Vector(10, 0, 0)),
        )
        for arguments, expected_code in (
            (
                {
                    "operation": "split",
                    "label": "Missing Source",
                    "definition": {
                        "source_body": {"object_name": "MissingSplitBody"},
                        "splitters": [
                            {"object_name": cancel_plane.Name, "subelements": []}
                        ],
                        "retained_region_index": 0,
                    },
                },
                "NATIVE_TARGET_INVALID",
            ),
            (
                _split_arguments(
                    "Empty Source",
                    empty_body,
                    ((cancel_plane, ()),),
                    0,
                ),
                "NATIVE_MODEL_INVALID",
            ),
            (
                _split_arguments(
                    "Self Split",
                    cancel_source,
                    ((cancel_source, ()),),
                    0,
                ),
                "NATIVE_MODEL_INVALID",
            ),
            (
                _split_arguments(
                    "Wire Split",
                    cancel_source,
                    ((wire, ()),),
                    0,
                ),
                "NATIVE_MODEL_INVALID",
            ),
            (
                _split_arguments(
                    "No Division",
                    cancel_source,
                    ((outside_plane, ()),),
                    0,
                ),
                "NATIVE_MODEL_INVALID",
            ),
            (
                _split_arguments(
                    "Missing Region",
                    cancel_source,
                    ((cancel_plane, ()),),
                    9,
                ),
                "NATIVE_MODEL_INVALID",
            ),
        ):
            before = tuple(obj.Name for obj in document.Objects)
            failure = native_call("model.boolean", arguments, succeeds=False)
            assert failure["error_code"] == expected_code, (arguments, failure)
            assert tuple(obj.Name for obj in document.Objects) == before
            assert not document.HasPendingTransaction

        # Index zero retains the left region and is one exact undo/redo step.
        left_source = new_box("Native Left Source", (40, 0, 0))
        left_plane = _plane(document, "NativeLeftPlane", 45.0)
        left_state = PartGui.resolveModelingObject(left_source)
        left_before = _body_signature(left_source)
        left_response = native_call(
            "model.boolean",
            _split_arguments(
                "Native Left Split",
                left_source,
                ((left_plane, ()),),
                0,
            ),
        )
        left_operation, left_bodies = _assert_split(
            document,
            left_response,
            source=left_source,
            source_state=left_state,
            splitters=((left_plane, ()),),
            retained_region_index=0,
            source_volume=1000.0,
        )
        assert left_source.Shape.BoundBox.XMax <= 45.0 + 1.0e-6
        left_after = tuple(_body_signature(body) for body in left_bodies)
        left_name = left_operation.Name
        created_body_name = left_bodies[1].Name
        document.undo()
        _process_events()
        assert document.getObject(left_name) is None
        assert document.getObject(created_body_name) is None
        assert _body_signature(left_source) == left_before
        document.redo()
        _process_events()
        left_operation = document.getObject(left_name)
        left_bodies = tuple(
            document.getObject(name)
            for name in (left_source.Name, created_body_name)
        )
        assert tuple(_body_signature(body) for body in left_bodies) == left_after
        PartDesign.validateDesign(left_operation)

        # Index one retains the right region; two definitions create three stable Bodies.
        right_source = new_box("Native Right Source", (60, 0, 0))
        right_plane = _plane(document, "NativeRightPlane", 65.0)
        right_state = PartGui.resolveModelingObject(right_source)
        right_response = native_call(
            "model.boolean",
            _split_arguments(
                "Native Right Split",
                right_source,
                ((right_plane, ()),),
                1,
            ),
        )
        right_operation, right_bodies = _assert_split(
            document,
            right_response,
            source=right_source,
            source_state=right_state,
            splitters=((right_plane, ()),),
            retained_region_index=1,
            source_volume=1000.0,
        )
        assert right_source.Shape.BoundBox.XMin >= 65.0 - 1.0e-6

        triple_source = new_box("Native Triple Source", (80, 0, 0))
        triple_a = _plane(document, "NativeTriplePlaneA", 83.0)
        triple_b = _plane(document, "NativeTriplePlaneB", 87.0)
        triple_state = PartGui.resolveModelingObject(triple_source)
        triple_response = native_call(
            "model.boolean",
            _split_arguments(
                "Native Triple Split",
                triple_source,
                ((triple_a, ()), (triple_b, ())),
                1,
            ),
        )
        triple_operation, triple_bodies = _assert_split(
            document,
            triple_response,
            source=triple_source,
            source_state=triple_state,
            splitters=((triple_a, ()), (triple_b, ())),
            retained_region_index=1,
            source_volume=1000.0,
        )
        assert len(triple_bodies) == 3

        # One exact subelement and one Body-backed solid definition both follow saved frames.
        face_source = new_box("Native Face Source", (110, 0, 0))
        face_compound = _publish_shape(
            document,
            "FaceSplitDefinitions",
            Part.makeCompound(
                [
                    Part.makePlane(30, 30, App.Vector(113, 20, -10), App.Vector(1, 0, 0)),
                    Part.makePlane(30, 30, App.Vector(117, 20, -10), App.Vector(1, 0, 0)),
                ]
            ),
        )
        face_state = PartGui.resolveModelingObject(face_source)
        face_response = native_call(
            "model.boolean",
            _split_arguments(
                "Native Face Split",
                face_source,
                ((face_compound, ("Face2",)),),
                0,
            ),
        )
        face_operation, face_bodies = _assert_split(
            document,
            face_response,
            source=face_source,
            source_state=face_state,
            splitters=((face_compound, ("Face2",)),),
            retained_region_index=0,
            source_volume=1000.0,
        )
        assert len(face_bodies) == 2

        tool_source = new_box("Native Tool Source", (130, 0, 0))
        tool_body = new_box("Native Split Tool Body", (135, 0, 0), (10, 10, 10))
        tool_state = PartGui.resolveModelingObject(tool_body)
        tool_signature = _body_signature(tool_body)
        tool_source_state = PartGui.resolveModelingObject(tool_source)
        tool_response = native_call(
            "model.boolean",
            _split_arguments(
                "Native Body Tool Split",
                tool_source,
                ((tool_body, ()),),
                0,
            ),
        )
        tool_operation, tool_bodies = _assert_split(
            document,
            tool_response,
            source=tool_source,
            source_state=tool_source_state,
            splitters=((tool_state, ()),),
            retained_region_index=0,
            source_volume=1000.0,
        )
        assert tuple(tool_operation.InputStates) == (tool_source_state, tool_state)
        assert _body_signature(tool_body) == tool_signature

        # A moved definition after preflight and a rejected verifier both roll back atomically.
        stale_source = new_box("Stale Split Source", (160, 0, 0))
        stale_plane = _plane(document, "StaleSplitPlane", 165.0)
        stale_source_before = _body_signature(stale_source)
        stale_plane_before = _shape_signature(stale_plane.Shape)
        before = tuple(obj.Name for obj in document.Objects)
        original_create = boolean_runtime_module.create_design_split

        def change_after_preflight(target_document, **kwargs):
            stale_plane.Placement.Base.x = 1.0
            return original_create(target_document, **kwargs)

        boolean_runtime_module.create_design_split = change_after_preflight
        try:
            stale_failure = native_call(
                "model.boolean",
                _split_arguments(
                    "Stale Split",
                    stale_source,
                    ((stale_plane, ()),),
                    0,
                ),
                succeeds=False,
            )
        finally:
            boolean_runtime_module.create_design_split = original_create
        assert stale_failure["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert _body_signature(stale_source) == stale_source_before
        assert _shape_signature(stale_plane.Shape) == stale_plane_before

        rollback_source = new_box("Rollback Split Source", (180, 0, 0))
        rollback_plane = _plane(document, "RollbackSplitPlane", 185.0)
        rollback_before = _body_signature(rollback_source)
        before = tuple(obj.Name for obj in document.Objects)
        original_verify = boolean_runtime_module.verify_design_split

        def reject_after_creation(_document, _draft):
            raise NativeModelError("Forced Design Split postcondition failure.")

        boolean_runtime_module.verify_design_split = reject_after_creation
        try:
            rollback_failure = native_call(
                "model.boolean",
                _split_arguments(
                    "Rollback Split",
                    rollback_source,
                    ((rollback_plane, ()),),
                    0,
                ),
                succeeds=False,
            )
        finally:
            boolean_runtime_module.verify_design_split = original_verify
        assert rollback_failure["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert _body_signature(rollback_source) == rollback_before
        assert not document.HasPendingTransaction

        # Current-History state is required.
        inactive_source = new_box("Inactive Split Source", (200, 0, 0))
        inactive_plane = _plane(document, "InactiveSplitPlane", 205.0)
        timeline = document.getObject("VibeCADTimeline")
        timeline_end = int(timeline.Position)
        timeline.Position = 0
        _process_events()
        before = tuple(obj.Name for obj in document.Objects)
        inactive = native_call(
            "model.boolean",
            _split_arguments(
                "Inactive Split",
                inactive_source,
                ((inactive_plane, ()),),
                0,
            ),
            succeeds=False,
        )
        assert inactive["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        timeline.Position = timeline_end
        _process_events()

        records = (
            _record(left_operation, left_bodies),
            _record(right_operation, right_bodies),
            _record(triple_operation, triple_bodies),
            _record(face_operation, face_bodies),
            _record(tool_operation, tool_bodies),
        )
        for record in records:
            operation = document.getObject(record["name"])
            before_shapes = [_shape_signature(shape) for shape in operation.OutputShapes]
            assert document.recompute([operation], True, True) is not False
            assert [_shape_signature(shape) for shape in operation.OutputShapes] == before_shapes
            PartDesign.validateDesign(operation)

        temp_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-design-split-"))
        save_path = temp_directory / "ModelDesignSplit.FCStd"
        document.saveAs(str(save_path))
        document_name = document.Name
        App.closeDocument(document_name)
        document = App.openDocument(str(save_path))
        assert document.recompute() is not False
        _process_events()
        for record in records:
            operation = document.getObject(record["name"])
            assert operation is not None and operation.TypeId == "PartDesign::DesignSplit"
            assert str(operation.Label) == record["label"]
            assert str(operation.OperationId) == record["operation_id"]
            assert str(operation.SourceBodyId) == record["source_body_id"]
            assert [state.Name for state in operation.InputStates] == record["input_names"]
            assert list(operation.InputBodyIds) == record["input_body_ids"]
            assert [_placement_signature(value) for value in operation.InputFrames] == record[
                "input_frames"
            ]
            assert [(obj.Name, list(names)) for obj, names in _link_groups(operation.Splitters)] == record[
                "splitters"
            ]
            assert [_placement_signature(value) for value in operation.SplitterFrames] == record[
                "splitter_frames"
            ]
            assert [(value.x, value.y, value.z) for value in operation.RegionWitnesses] == record[
                "witnesses"
            ]
            assert [_shape_signature(shape) for shape in operation.OutputShapes] == record[
                "output_shapes"
            ]
            assert [_placement_signature(value) for value in operation.OutputFrames] == record[
                "output_frames"
            ]
            assert list(operation.OutputComponentIds) == record["component_ids"]
            assert operation.PreviewShape.isNull()
            assert "Transient" in operation.getPropertyStatus("PreviewShape")
            for body_name, body_id, state_name, shape in zip(
                record["body_names"],
                record["body_ids"],
                record["body_states"],
                record["body_shapes"],
                strict=True,
            ):
                body = document.getObject(body_name)
                assert body is not None and str(body.VibeCADBodyId) == body_id
                assert PartGui.resolveModelingObject(body).Name == state_name
                assert _shape_signature(body.Shape) == shape
            PartDesign.validateDesign(operation)

        print("VIBECAD_NATIVE_MODEL_DESIGN_SPLIT_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        Gui.Selection.clearSelection()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temp_directory is not None:
            shutil.rmtree(temp_directory, ignore_errors=True)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
