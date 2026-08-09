# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real GUI/provider lifecycle gate for Native Model Part Defeaturing."""

from __future__ import annotations

import json
import math
from pathlib import Path
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
import VibeCADNativeModelPartRuntime as runtime_module
from VibeCADCore import get_service
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeModelPartSchema import model_part_capability_definition
from VibeCADNativePartDefeature import (
    create_part_defeature,
    preflight_part_defeature,
    prepare_part_defeature,
)
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


def _publish_shape(
    document,
    name,
    shape,
    *,
    placement=None,
    visible=True,
):
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
        obj.Visibility = visible
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    assert PartGui.isModelingObjectActive(obj)
    return obj


def _body_with_shape(document, name, shape):
    document.openTransaction(f"Create {name}")
    try:
        body = document.addObject("PartDesign::Body", name)
        seed = body.newObject("PartDesign::Feature", f"{name}Seed")
        seed.Label = f"{name} Seed"
        seed.Shape = shape
        assert document.recompute([seed, body], True, True) is not False
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    body.Visibility = True
    assert PartGui.isModelingObjectActive(body)
    return body


def _drilled_box(
    x: float,
    *,
    holes: tuple[tuple[float, float], ...] = ((6.0, 6.0),),
):
    shape = Part.makeBox(12, 12, 8, App.Vector(x, 0, 0))
    for y, x_offset in holes:
        shape = shape.cut(
            Part.makeCylinder(
                2,
                8,
                App.Vector(x + x_offset, y, 0),
            )
        )
    return shape


def _cylindrical_faces(shape) -> tuple[str, ...]:
    return tuple(
        f"Face{index}"
        for index, face in enumerate(shape.Faces, start=1)
        if isinstance(face.Surface, Part.Cylinder)
    )


def _volume_center(shape):
    solids = tuple(shape.Solids)
    total_volume = sum(float(solid.Volume) for solid in solids)
    assert solids and total_volume > 0.0
    center = App.Vector()
    for solid in solids:
        center += solid.CenterOfMass * (float(solid.Volume) / total_volume)
    return center


def _shape_signature(shape) -> tuple[object, ...]:
    bounds = shape.BoundBox
    center = _volume_center(shape)
    return (
        str(shape.ShapeType),
        len(shape.Vertexes),
        len(shape.Edges),
        len(shape.Faces),
        len(shape.Solids),
        float(shape.Volume),
        float(shape.Area),
        float(bounds.XMin),
        float(bounds.XMax),
        float(bounds.YMin),
        float(bounds.YMax),
        float(bounds.ZMin),
        float(bounds.ZMax),
        float(center.x),
        float(center.y),
        float(center.z),
    )


def _assert_signature(actual, expected) -> None:
    assert actual[:5] == expected[:5]
    for left, right in zip(actual[5:], expected[5:], strict=True):
        assert math.isclose(left, right, rel_tol=1.0e-9, abs_tol=1.0e-7)


def _select(*selections) -> None:
    Gui.Selection.clearSelection()
    for selection in selections:
        if isinstance(selection, tuple):
            obj, subelements = selection
            for subelement in tuple(subelements):
                Gui.Selection.addSelection(obj, subelement)
        else:
            Gui.Selection.addSelection(selection)
    _process_events(4)


def _turn() -> NativeTurnSnapshot:
    definition = model_part_capability_definition()
    schema = definition.provider_schema(("defeature",))
    surface = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot(
            "model",
            1,
            "f" * 64,
            ("Part_Defeaturing",),
            (),
            (),
        ),
        available=True,
        unavailable_reason="",
        tool_names=("model.part",),
        schemas=(schema,),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(surface)


def _dispatcher(document) -> NativeTurnDispatcher:
    service = get_service()
    service.select_modeling_engine("native")
    state = service.native_document_state_store()
    ledger = NativeAssistantUndoLedger()
    ledger.begin_run("native-model-part-defeature-gui")
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
    turn = _turn()
    return NativeTurnDispatcher(
        document=document,
        state=state,
        registry=build_native_capability_registry(),
        turn=turn,
        runtimes=build_native_runtime_bindings(context, turn.tool_names),
        reauthorize_turn=lambda: None,
        active_document=lambda: App.ActiveDocument,
    )


def _arguments(label, sources):
    return {
        "operation": "defeature",
        "label": label,
        "definition": {
            "sources": [
                {
                    "object_name": obj.Name,
                    "faces": list(faces),
                }
                for obj, faces in sources
            ]
        },
    }


def _assert_result(
    document,
    response,
    *,
    sources,
    faces,
    label,
    replaced,
    durable_replaced=None,
):
    if durable_replaced is None:
        durable_replaced = replaced
    assert set(response) == {
        "ok",
        "root",
        "source_count",
        "result_count",
        "resource_count",
        "removed_face_count",
        "shape_types",
        "total_face_count",
        "total_area_mm2",
        "total_volume_mm3",
        "receipt",
        "assistant_undo_available",
    }
    root = document.getObject(response["root"]["object_name"])
    timeline = document.getObject("VibeCADTimeline")
    results = tuple(
        obj
        for obj in timeline.Operations
        if obj is root or getattr(obj, "VibeCADTimelineOwner", None) is root
    )
    assert len(results) == len(sources)
    assert results[-1] is root
    expected_labels = tuple(
        f"{label[: max(1, 160 - len(f' — output {index}'))]} — output {index}"
        if index < len(sources)
        else label
        for index in range(1, len(sources) + 1)
    )
    assert tuple(result.Label for result in results) == expected_labels
    for index, result in enumerate(results):
        assert result.TypeId == "Part::Feature"
        assert result.getParentGeoFeatureGroup() is None
        assert result.isValid() and result.Shape.isValid() and not result.Shape.isNull()
        assert result.VibeCADTimelineRole == (
            "operation" if result is root else "resource"
        )
        assert getattr(result, "VibeCADTimelineOwner", None) is (
            None if result is root else root
        )
        if result is not root:
            assert "VibeCADTimelineReplacedInputs" not in result.PropertiesList
        assert len(result.Shape.Solids) == 1
        assert result.Shape.Volume > sources[index].Shape.Volume
    assert str(root.VibeCADDefinitionId) and str(root.DesignId)
    assert tuple(root.VibeCADTimelineReplacedInputs) == tuple(durable_replaced)
    assert all(not source.Visibility for source in sources)
    assert response["source_count"] == len(sources)
    assert response["result_count"] == len(sources)
    assert response["resource_count"] == len(sources) - 1
    assert response["removed_face_count"] == sum(len(item) for item in faces)
    assert response["shape_types"] == list(
        dict.fromkeys(str(result.Shape.ShapeType) for result in results)
    )
    assert response["total_face_count"] == sum(len(result.Shape.Faces) for result in results)
    assert math.isclose(
        response["total_volume_mm3"],
        sum(result.Shape.Volume for result in results),
        rel_tol=1.0e-9,
        abs_tol=1.0e-7,
    )
    created = [item["object_name"] for item in response["receipt"]["created"]]
    assert created == sorted(result.Name for result in results)
    receipt_replaced = [
        item["object_name"] for item in response["receipt"]["replaced"]
    ]
    assert receipt_replaced == sorted(item.Name for item in durable_replaced)
    assert response["receipt"]["changed"] == []
    assert response["receipt"]["deleted"] == []
    assert response["assistant_undo_available"] is True
    PartDesign.validateDesign(root)
    return root, results


def _record(root, results):
    return {
        "root": root.Name,
        "names": tuple(result.Name for result in results),
        "labels": tuple(str(result.Label) for result in results),
        "roles": tuple(str(result.VibeCADTimelineRole) for result in results),
        "owners": tuple(
            getattr(result, "VibeCADTimelineOwner", None).Name
            if getattr(result, "VibeCADTimelineOwner", None) is not None
            else None
            for result in results
        ),
        "shapes": tuple(_shape_signature(result.Shape) for result in results),
        "replaced": tuple(item.Name for item in root.VibeCADTimelineReplacedInputs),
        "definition_id": str(root.VibeCADDefinitionId),
        "design_id": str(root.DesignId),
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeModelPartDefeatureGate")
        document.UndoMode = True
        VibeGui._connect_document_observer()
        _process_events()

        # Freeze the real immediate command, grouped face selection, publication,
        # replacement behavior, automatic labels, and one-step human undo.
        human_first = _publish_shape(
            document,
            "HumanDefeatureFirst",
            _drilled_box(0),
        )
        human_second = _publish_shape(
            document,
            "HumanDefeatureSecond",
            _drilled_box(20),
        )
        human_faces = (
            _cylindrical_faces(human_first.Shape),
            _cylindrical_faces(human_second.Shape),
        )
        assert tuple(len(item) for item in human_faces) == (1, 1)
        _select((human_first, human_faces[0]), (human_second, human_faces[1]))
        assert Gui.isCommandActive("Part_Defeaturing")
        before_human = tuple(obj.Name for obj in document.Objects)
        Gui.runCommand("Part_Defeaturing", 0)
        _process_events()
        assert not Gui.Control.activeDialog()
        human_results = tuple(
            obj
            for obj in document.Objects
            if obj.Name not in before_human and obj.TypeId == "Part::Feature"
        )
        assert len(human_results) == 2
        human_root = human_results[-1]
        assert tuple(result.Label for result in human_results) == (
            "Defeatured",
            "Defeatured001",
        )
        assert tuple(result.VibeCADTimelineRole for result in human_results) == (
            "resource",
            "operation",
        )
        assert human_results[0].VibeCADTimelineOwner is human_root
        assert getattr(human_root, "VibeCADTimelineOwner", None) is None
        assert tuple(human_root.VibeCADTimelineReplacedInputs) == (
            human_first,
            human_second,
        )
        assert all(not source.Visibility for source in (human_first, human_second))
        assert all(math.isclose(result.Shape.Volume, 1152.0) for result in human_results)
        human_result_names = tuple(result.Name for result in human_results)
        document.undo()
        _process_events()
        assert all(document.getObject(name) is None for name in human_result_names)
        assert human_first.Visibility and human_second.Visibility

        # Whole-shape and edge-only selections never activate the human command.
        _select(human_first)
        assert not Gui.isCommandActive("Part_Defeaturing")
        _select((human_first, ("Edge1",)))
        assert not Gui.isCommandActive("Part_Defeaturing")

        dispatcher = _dispatcher(document)
        call_number = 0

        def native_call(arguments, *, succeeds=True):
            nonlocal call_number
            call_number += 1
            response = dispatcher.call(
                "model.part",
                json.dumps(arguments, separators=(",", ":")),
                f"model-part-defeature-call-{call_number}",
            )
            assert response.get("ok") is succeeds, (arguments, response)
            return response

        # Closed-schema, exact-target, face, and source-shape failures are no-ops.
        before = tuple(obj.Name for obj in document.Objects)
        missing_definition = native_call(
            {"operation": "defeature", "label": "Missing definition"},
            succeeds=False,
        )
        assert missing_definition["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before

        missing = native_call(
            {
                "operation": "defeature",
                "label": "Missing source",
                "definition": {
                    "sources": [
                        {"object_name": "MissingSource", "faces": ["Face1"]}
                    ]
                },
            },
            succeeds=False,
        )
        assert missing["error_code"] == "NATIVE_TARGET_INVALID"

        invalid_face_source = _publish_shape(
            document,
            "InvalidFaceSource",
            _drilled_box(40),
        )
        before = tuple(obj.Name for obj in document.Objects)
        invalid_face = native_call(
            _arguments("Invalid face", ((invalid_face_source, ("Face999",)),)),
            succeeds=False,
        )
        assert invalid_face["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before

        wire = _publish_shape(
            document,
            "DefeatureWire",
            Part.makePolygon([App.Vector(0, 30, 0), App.Vector(10, 30, 0)]),
        )
        wire_failure = native_call(
            _arguments("Wire failure", ((wire, ("Face1",)),)),
            succeeds=False,
        )
        assert wire_failure["error_code"] == "NATIVE_MODEL_INVALID"

        duplicate = native_call(
            {
                "operation": "defeature",
                "label": "Duplicate source",
                "definition": {
                    "sources": [
                        {
                            "object_name": invalid_face_source.Name,
                            "faces": ["Face1"],
                        },
                        {
                            "object_name": invalid_face_source.Name,
                            "faces": ["Face2"],
                        },
                    ]
                },
            },
            succeeds=False,
        )
        assert duplicate["error_code"] == "NATIVE_MODEL_INVALID"

        # A single exact source heals the hole and is one exact undo/redo step.
        single_source = _publish_shape(
            document,
            "NativeSingleDefeatureSource",
            _drilled_box(60),
        )
        single_face = _cylindrical_faces(single_source.Shape)
        source_signature = _shape_signature(single_source.Shape)
        single_response = native_call(
            _arguments("Healed Gearcase", ((single_source, single_face),))
        )
        single_root, single_results = _assert_result(
            document,
            single_response,
            sources=(single_source,),
            faces=(single_face,),
            label="Healed Gearcase",
            replaced=(single_source,),
        )
        assert math.isclose(single_root.Shape.Volume, 1152.0, abs_tol=1.0e-7)
        single_name = single_root.Name
        single_shape = _shape_signature(single_root.Shape)
        document.undo()
        _process_events()
        assert document.getObject(single_name) is None
        assert single_source.Visibility
        _assert_signature(_shape_signature(single_source.Shape), source_signature)
        document.redo()
        _process_events()
        single_root = document.getObject(single_name)
        single_results = (single_root,)
        _assert_signature(_shape_signature(single_root.Shape), single_shape)
        assert not single_source.Visibility
        PartDesign.validateDesign(single_root)

        # Multiple selected faces on one source are removed as one exact feature set.
        two_hole_source = _publish_shape(
            document,
            "NativeTwoHoleSource",
            _drilled_box(80, holes=((4.0, 4.0), (8.0, 8.0))),
        )
        two_hole_faces = _cylindrical_faces(two_hole_source.Shape)
        assert len(two_hole_faces) == 2
        two_hole_response = native_call(
            _arguments("Two Holes Removed", ((two_hole_source, two_hole_faces),))
        )
        two_hole_root, two_hole_results = _assert_result(
            document,
            two_hole_response,
            sources=(two_hole_source,),
            faces=(two_hole_faces,),
            label="Two Holes Removed",
            replaced=(two_hole_source,),
        )
        assert math.isclose(two_hole_root.Shape.Volume, 1152.0, abs_tol=1.0e-7)

        # A batch publishes resources plus one root and preserves hidden inputs.
        pair_first = _publish_shape(
            document,
            "NativePairFirst",
            _drilled_box(100),
        )
        pair_second = _publish_shape(
            document,
            "NativePairSecond",
            _drilled_box(120),
            visible=False,
        )
        pair_faces = (
            _cylindrical_faces(pair_first.Shape),
            _cylindrical_faces(pair_second.Shape),
        )
        pair_response = native_call(
            _arguments(
                "Healed Pair",
                ((pair_first, pair_faces[0]), (pair_second, pair_faces[1])),
            )
        )
        pair_root, pair_results = _assert_result(
            document,
            pair_response,
            sources=(pair_first, pair_second),
            faces=pair_faces,
            label="Healed Pair",
            replaced=(pair_first,),
        )
        assert tuple(pair_root.VibeCADTimelineReplacedInputs) == (pair_first,)
        assert not pair_second.Visibility

        # Global transformed geometry is retained on the root-level result.
        transformed_source = _publish_shape(
            document,
            "NativeTransformedDefeatureSource",
            _drilled_box(0),
            placement=App.Placement(
                App.Vector(150, -20, 7),
                App.Rotation(App.Vector(0, 0, 1), 23),
            ),
        )
        transformed_face = _cylindrical_faces(transformed_source.Shape)
        transformed_placement = transformed_source.Placement
        transformed_response = native_call(
            _arguments(
                "Transformed Healed Housing",
                ((transformed_source, transformed_face),),
            )
        )
        transformed_root, transformed_results = _assert_result(
            document,
            transformed_response,
            sources=(transformed_source,),
            faces=(transformed_face,),
            label="Transformed Healed Housing",
            replaced=(transformed_source,),
        )
        expected_center = transformed_placement.multVec(App.Vector(6, 6, 4))
        actual_center = _volume_center(transformed_root.Shape)
        assert (actual_center - expected_center).Length < 1.0e-7
        assert transformed_source.Placement == transformed_placement

        # Body-backed selection resolves its current state but replaces the Body presentation.
        body_source = _body_with_shape(
            document,
            "NativeDefeatureBody",
            _drilled_box(180),
        )
        body_state = PartGui.resolveModelingObject(body_source)
        assert body_state is not None and body_state is not body_source
        body_face = _cylindrical_faces(body_source.Shape)
        body_response = native_call(
            _arguments("Body Healed Housing", ((body_source, body_face),))
        )
        body_root, body_results = _assert_result(
            document,
            body_response,
            sources=(body_source,),
            faces=(body_face,),
            label="Body Healed Housing",
            replaced=(body_source,),
            durable_replaced=(body_state,),
        )
        assert math.isclose(body_root.Shape.Volume, 1152.0, abs_tol=1.0e-7)

        # An inactive History source fails before mutation.
        inactive_source = _publish_shape(
            document,
            "InactiveDefeatureSource",
            _drilled_box(200),
        )
        inactive_face = _cylindrical_faces(inactive_source.Shape)
        timeline = document.getObject("VibeCADTimeline")
        timeline_end = int(timeline.Position)
        timeline.Position = 0
        _process_events()
        assert not PartGui.isModelingObjectActive(inactive_source)
        before = tuple(obj.Name for obj in document.Objects)
        inactive_response = native_call(
            _arguments("Inactive Defeature", ((inactive_source, inactive_face),)),
            succeeds=False,
        )
        assert inactive_response["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        timeline.Position = timeline_end
        _process_events()
        assert PartGui.isModelingObjectActive(inactive_source)

        # Exact preflight change rejection does not create an output.
        stale_source = _publish_shape(
            document,
            "StaleDefeatureSource",
            _drilled_box(220),
        )
        stale_face = _cylindrical_faces(stale_source.Shape)
        stale_spec = prepare_part_defeature(
            str(document.Uid),
            _arguments("Stale", ((stale_source, stale_face),))["definition"],
        )
        stale_prepared = preflight_part_defeature(document, stale_spec)
        names_before = tuple(obj.Name for obj in document.Objects)
        document.openTransaction("Reject stale Part Defeaturing")
        try:
            stale_source.Shape = _drilled_box(221)
            try:
                create_part_defeature(
                    document,
                    label="Stale",
                    prepared=stale_prepared,
                )
            except NativeModelError:
                pass
            else:
                raise AssertionError("Changed Part Defeaturing preflight was accepted")
        finally:
            document.abortTransaction()
        assert tuple(obj.Name for obj in document.Objects) == names_before

        # A verifier failure rolls back outputs, visibility, and replacement metadata.
        rollback_source = _publish_shape(
            document,
            "RollbackDefeatureSource",
            _drilled_box(240),
        )
        rollback_face = _cylindrical_faces(rollback_source.Shape)
        rollback_names = tuple(obj.Name for obj in document.Objects)
        original_verifier = runtime_module.verify_part_defeature

        def fail_verifier(_document, _draft):
            raise NativeModelError("Forced Part Defeaturing verifier failure")

        runtime_module.verify_part_defeature = fail_verifier
        try:
            rollback_response = native_call(
                _arguments("Rollback Defeature", ((rollback_source, rollback_face),)),
                succeeds=False,
            )
        finally:
            runtime_module.verify_part_defeature = original_verifier
        assert rollback_response["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == rollback_names
        assert rollback_source.Visibility
        assert not document.HasPendingTransaction

        records = tuple(
            _record(root, results)
            for root, results in (
                (single_root, single_results),
                (two_hole_root, two_hole_results),
                (pair_root, pair_results),
                (transformed_root, transformed_results),
                (body_root, body_results),
            )
        )
        assert document.recompute(None, True, True) is not False
        for record in records:
            root = document.getObject(record["root"])
            PartDesign.validateDesign(root)

        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-defeature-"))
        save_path = save_directory / "NativePartDefeature.FCStd"
        document.saveAs(str(save_path))
        document_name = document.Name
        App.closeDocument(document_name)
        document = App.openDocument(str(save_path))
        Gui.activeDocument().activeView().viewAxonometric()
        _process_events()

        for record in records:
            root = document.getObject(record["root"])
            results = tuple(document.getObject(name) for name in record["names"])
            assert tuple(str(result.Label) for result in results) == record["labels"]
            assert tuple(str(result.VibeCADTimelineRole) for result in results) == record["roles"]
            assert tuple(
                getattr(result, "VibeCADTimelineOwner", None).Name
                if getattr(result, "VibeCADTimelineOwner", None) is not None
                else None
                for result in results
            ) == record["owners"]
            for result, signature in zip(results, record["shapes"], strict=True):
                _assert_signature(_shape_signature(result.Shape), signature)
            assert tuple(item.Name for item in root.VibeCADTimelineReplacedInputs) == record["replaced"]
            assert str(root.VibeCADDefinitionId) == record["definition_id"]
            assert str(root.DesignId) == record["design_id"]
            assert all(not document.getObject(name).Visibility for name in record["replaced"])
            PartDesign.validateDesign(root)

        print("VIBECAD_NATIVE_MODEL_PART_DEFEATURE_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        try:
            Gui.Selection.clearSelection()
            if document is not None:
                App.closeDocument(document.Name)
        except Exception:
            traceback.print_exc()
            exit_code = 1
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
