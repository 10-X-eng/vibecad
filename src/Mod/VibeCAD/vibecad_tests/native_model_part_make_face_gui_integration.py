# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real VibeCAD GUI and provider lifecycle gate for Face From Wires."""

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
from VibeCADNativeModelErrors import NativeModelError
import VibeCADNativeModelPartRuntime as runtime_module
from VibeCADNativeModelPartSchema import model_part_capability_definition
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


def _close(left: float, right: float, tolerance: float = 1.0e-7) -> bool:
    return abs(float(left) - float(right)) <= tolerance


def _shape_signature(shape) -> dict[str, object]:
    bounds = shape.BoundBox
    return {
        "shape_type": str(shape.ShapeType),
        "topology": (
            len(shape.Vertexes),
            len(shape.Edges),
            len(shape.Wires),
            len(shape.Faces),
            len(shape.Solids),
        ),
        "bounds": tuple(
            float(getattr(bounds, name))
            for name in ("XMin", "XMax", "YMin", "YMax", "ZMin", "ZMax")
        ),
        "measures": (float(shape.Length), float(shape.Area), float(shape.Volume)),
    }


def _assert_shape_signature(shape, expected: dict[str, object]) -> None:
    actual = _shape_signature(shape)
    assert actual["shape_type"] == expected["shape_type"]
    assert actual["topology"] == expected["topology"]
    for field in ("bounds", "measures"):
        assert all(
            _close(left, right)
            for left, right in zip(actual[field], expected[field], strict=True)
        ), (field, actual[field], expected[field])


def _closed_wire(
    x: float,
    y: float,
    width: float,
    height: float,
    z: float = 0.0,
):
    return Part.makePolygon(
        [
            App.Vector(x, y, z),
            App.Vector(x + width, y, z),
            App.Vector(x + width, y + height, z),
            App.Vector(x, y + height, z),
            App.Vector(x, y, z),
        ]
    )


def _open_wire(x: float, y: float):
    return Part.makePolygon(
        [
            App.Vector(x, y, 0),
            App.Vector(x + 4, y, 0),
            App.Vector(x + 4, y + 3, 0),
        ]
    )


def _publish_object(document, obj):
    PartDesign.initializeDesignDefinition(obj)
    document.publishProvisionalTimelineOperationBlock(obj, (), ())
    assert document.recompute([obj], True, True) is not False
    PartDesign.finalizeDesignDefinition(obj)
    assert PartGui.isModelingObjectActive(obj)
    return obj


def _publish_source(document, name: str, shape, *, placement=None):
    source = document.addObject("Part::Feature", name)
    source.Label = name
    source.Shape = shape
    if placement is not None:
        source.Placement = placement
    return _publish_object(document, source)


def _create_sources(document) -> tuple[dict[str, object], str]:
    document.openTransaction("Create Face From Wires gate sources")
    try:
        sources = {
            "HumanClosedWire": _publish_source(
                document,
                "HumanClosedWire",
                _closed_wire(0, 0, 5, 4),
            ),
            "SingleWire": _publish_source(
                document,
                "SingleWire",
                _closed_wire(10, 0, 8, 6),
            ),
            "OuterWire": _publish_source(
                document,
                "OuterWire",
                _closed_wire(22, 0, 10, 10),
            ),
            "InnerWire": _publish_source(
                document,
                "InnerWire",
                _closed_wire(25, 3, 4, 4),
            ),
            "DisjointCompound": _publish_source(
                document,
                "DisjointCompound",
                Part.makeCompound(
                    [
                        _closed_wire(36, 0, 3, 2),
                        _closed_wire(42, 0, 2, 2),
                    ]
                ),
            ),
            "TransformedWire": _publish_source(
                document,
                "TransformedWire",
                _closed_wire(0, 0, 3, 5),
                placement=App.Placement(
                    App.Vector(50, 4, 7),
                    App.Rotation(App.Vector(0, 0, 1), 0),
                ),
            ),
            "RollbackWire": _publish_source(
                document,
                "RollbackWire",
                _closed_wire(60, 0, 4, 4),
            ),
            "InactiveWire": _publish_source(
                document,
                "InactiveWire",
                _closed_wire(68, 0, 4, 3),
            ),
            "OpenWire": _publish_source(
                document,
                "OpenWire",
                _open_wire(76, 0),
            ),
            "ExistingFace": _publish_source(
                document,
                "ExistingFace",
                Part.Face(_closed_wire(84, 0, 4, 3)),
            ),
        }
        stale = _publish_source(
            document,
            "StaleWire",
            _closed_wire(92, 0, 3, 3),
        )
        empty = document.addObject("Part::Feature", "EmptyObject")
        empty.Label = "EmptyObject"
        _publish_object(document, empty)
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise

    document.openTransaction("Delete stale Face From Wires source")
    try:
        stale_name = stale.Name
        document.removeObject(stale_name)
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    assert document.getObject(stale_name) is None
    return sources, stale_name


def _turn() -> NativeTurnSnapshot:
    definition = model_part_capability_definition()
    surface = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot(
            "model",
            1,
            "f" * 64,
            ("Part_MakeFace",),
            (),
            (),
        ),
        available=True,
        unavailable_reason="",
        tool_names=(definition.name,),
        schemas=(definition.provider_schema(("make_face",)),),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(surface)


def _arguments(label: str, *source_names: str) -> dict[str, object]:
    return {
        "operation": "make_face",
        "label": label,
        "definition": {
            "sources": [{"object_name": name} for name in source_names]
        },
    }


def _select(source) -> None:
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(source)
    _process_events(8)


def _assert_human_contract(document, sources) -> None:
    Gui.Selection.clearSelection()
    _process_events()
    assert not Gui.isCommandActive("Part_MakeFace")

    _select(sources["OpenWire"])
    assert not Gui.isCommandActive("Part_MakeFace")
    _select(sources["ExistingFace"])
    assert not Gui.isCommandActive("Part_MakeFace")

    source = sources["HumanClosedWire"]
    source_signature = _shape_signature(Part.getShape(source, transform=True))
    before = tuple(obj.Name for obj in document.Objects)
    _select(source)
    assert Gui.isCommandActive("Part_MakeFace")
    Gui.runCommand("Part_MakeFace", 0)
    _process_events(24)
    assert not Gui.Control.activeDialog()
    created = [obj for obj in document.Objects if obj.Name not in before]
    assert len(created) == 1
    result = created[0]
    assert result.TypeId == "Part::Face"
    assert tuple(result.Sources) == (source,)
    assert result.FaceMakerClass == "Part::FaceMakerUnified"
    assert result.getParentGeoFeatureGroup() is None
    assert result.VibeCADTimelineRole == "operation"
    assert tuple(result.VibeCADTimelineReplacedInputs) == (source,)
    assert result.Shape.ShapeType == "Face"
    assert len(result.Shape.Faces) == 1
    assert _close(result.Shape.Area, 20.0)
    assert not source.Visibility
    _assert_shape_signature(Part.getShape(source, transform=True), source_signature)
    Gui.Selection.clearSelection()


def _cases() -> tuple[dict[str, object], ...]:
    return (
        {
            "label": "Gate Single Face",
            "sources": ("SingleWire",),
            "shape_type": "Face",
            "face_count": 1,
            "wire_count": 1,
            "area": 48.0,
            "bounds": (10.0, 18.0, 0.0, 6.0, 0.0, 0.0),
        },
        {
            "label": "Gate Face With Hole",
            "sources": ("OuterWire", "InnerWire"),
            "shape_type": "Face",
            "face_count": 1,
            "wire_count": 2,
            "area": 84.0,
            "bounds": (22.0, 32.0, 0.0, 10.0, 0.0, 0.0),
        },
        {
            "label": "Gate Compound Faces",
            "sources": ("DisjointCompound",),
            "shape_type": "Compound",
            "face_count": 2,
            "wire_count": 2,
            "area": 10.0,
            "bounds": (36.0, 44.0, 0.0, 2.0, 0.0, 0.0),
        },
        {
            "label": "Gate Transformed Face",
            "sources": ("TransformedWire",),
            "shape_type": "Face",
            "face_count": 1,
            "wire_count": 1,
            "area": 15.0,
            "bounds": (50.0, 53.0, 4.0, 9.0, 7.0, 7.0),
        },
    )


def _assert_bounds(shape, expected) -> None:
    bounds = shape.BoundBox
    actual = (
        bounds.XMin,
        bounds.XMax,
        bounds.YMin,
        bounds.YMax,
        bounds.ZMin,
        bounds.ZMax,
    )
    assert all(
        _close(left, right)
        for left, right in zip(actual, expected, strict=True)
    ), (actual, expected)


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    save_directory = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeModelPartMakeFaceGate")
        VibeGui._connect_document_observer()
        _process_events()
        sources, stale_name = _create_sources(document)
        _assert_human_contract(document, sources)

        source_names = tuple(
            dict.fromkeys(name for case in _cases() for name in case["sources"])
        )
        source_signatures = {
            name: _shape_signature(Part.getShape(sources[name], transform=True))
            for name in source_names
        }

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-part-make-face-gui")
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
        debug_events = []
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state,
            registry=build_native_capability_registry(),
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=lambda: None,
            active_document=lambda: App.ActiveDocument,
            debug_sink=debug_events.append,
        )
        call_number = 0

        def native_call(arguments, *, succeeds=True):
            nonlocal call_number
            call_number += 1
            response = dispatcher.call(
                "model.part",
                json.dumps(arguments, separators=(",", ":")),
                f"model-part-make-face-call-{call_number}",
            )
            assert response.get("ok") is succeeds, (
                arguments.get("label"),
                response,
                debug_events[-1] if debug_events else None,
            )
            return response

        before = tuple(obj.Name for obj in document.Objects)
        invalid_schema = native_call(
            {
                "operation": "make_face",
                "definition": {"sources": [{"object_name": "SingleWire"}]},
            },
            succeeds=False,
        )
        assert invalid_schema["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before

        expected_response_fields = {
            "ok",
            "root",
            "source_count",
            "shape_type",
            "face_count",
            "area_mm2",
            "receipt",
            "assistant_undo_available",
        }
        records = []
        for case in _cases():
            case_sources = tuple(sources[name] for name in case["sources"])
            assert all(source.Visibility for source in case_sources)
            response = native_call(_arguments(case["label"], *case["sources"]))
            assert set(response) == expected_response_fields
            assert response["source_count"] == len(case_sources)
            assert response["shape_type"] == case["shape_type"]
            assert response["face_count"] == case["face_count"]
            assert _close(response["area_mm2"], case["area"])
            assert response["assistant_undo_available"] is True
            assert len(response["receipt"]["created"]) == 1
            assert response["receipt"]["changed"] == []
            assert response["receipt"]["deleted"] == []

            result = document.getObject(response["root"]["object_name"])
            assert result is not None and result.TypeId == "Part::Face"
            assert result.Label == case["label"]
            assert result.FaceMakerClass == "Part::FaceMakerUnified"
            assert tuple(result.Sources) == case_sources
            assert result.getParentGeoFeatureGroup() is None
            assert result.VibeCADTimelineRole == "operation"
            assert getattr(result, "VibeCADTimelineOwner", None) is None
            assert str(result.VibeCADDefinitionId) and str(result.DesignId)
            assert tuple(result.VibeCADTimelineReplacedInputs) == case_sources
            assert all(not source.Visibility for source in case_sources)
            assert result.Shape.isValid() and not result.Shape.isNull()
            assert len(result.Shape.Faces) == case["face_count"]
            assert len(result.Shape.Wires) == case["wire_count"]
            assert len(result.Shape.Solids) == 0
            assert _close(result.Shape.Area, case["area"])
            _assert_bounds(result.Shape, case["bounds"])
            signature = _shape_signature(result.Shape)
            for _index in range(4):
                assert document.recompute([result], True, True) is not False
                _assert_shape_signature(result.Shape, signature)
            record = {
                "name": result.Name,
                "label": str(result.Label),
                "definition_id": str(result.VibeCADDefinitionId),
                "design_id": str(result.DesignId),
                "source_names": case["sources"],
                "signature": signature,
            }

            document.undo()
            _process_events()
            assert document.getObject(record["name"]) is None
            assert all(source.Visibility for source in case_sources)
            document.redo()
            _process_events()
            result = document.getObject(record["name"])
            assert result is not None
            assert all(not source.Visibility for source in case_sources)
            _assert_shape_signature(result.Shape, signature)
            records.append(record)

        for name, signature in source_signatures.items():
            _assert_shape_signature(Part.getShape(sources[name], transform=True), signature)

        failure_cases = (
            (
                _arguments("Stale Face", stale_name),
                "NATIVE_TARGET_INVALID",
            ),
            (
                _arguments("Missing Shape Face", "EmptyObject"),
                "NATIVE_MODEL_INVALID",
            ),
            (
                _arguments("Open Wire Face", "OpenWire"),
                "NATIVE_MODEL_INVALID",
            ),
            (
                _arguments("Existing Face Input", "ExistingFace"),
                "NATIVE_MODEL_INVALID",
            ),
            (
                _arguments("Duplicate Face Input", "RollbackWire", "RollbackWire"),
                "NATIVE_ARGUMENTS_INVALID",
            ),
        )
        for arguments, error_code in failure_cases:
            before = tuple(obj.Name for obj in document.Objects)
            response = native_call(arguments, succeeds=False)
            assert response["error_code"] == error_code
            assert tuple(obj.Name for obj in document.Objects) == before
            assert document.HasPendingTransaction is False

        timeline = document.getObject("VibeCADTimeline")
        timeline_end = int(timeline.Position)
        inactive = sources["InactiveWire"]
        timeline.Position = 0
        _process_events()
        assert not PartGui.isModelingObjectActive(inactive)
        before = tuple(obj.Name for obj in document.Objects)
        inactive_response = native_call(
            _arguments("Inactive History Face", inactive.Name),
            succeeds=False,
        )
        assert inactive_response["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        timeline.Position = timeline_end
        _process_events()
        assert PartGui.isModelingObjectActive(inactive)

        rollback_source = sources["RollbackWire"]
        rollback_signature = _shape_signature(Part.getShape(rollback_source, transform=True))
        before = tuple(obj.Name for obj in document.Objects)
        original_verify = runtime_module.verify_part_make_face

        def reject_after_creation(_document, _draft):
            raise NativeModelError("Forced exact Face From Wires postcondition failure.")

        runtime_module.verify_part_make_face = reject_after_creation
        try:
            rollback = native_call(
                _arguments("Rollback Face", rollback_source.Name),
                succeeds=False,
            )
        finally:
            runtime_module.verify_part_make_face = original_verify
        assert rollback["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert document.HasPendingTransaction is False
        assert rollback_source.Visibility
        _assert_shape_signature(
            Part.getShape(rollback_source, transform=True),
            rollback_signature,
        )

        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-make-face-"))
        save_path = save_directory / "ModelPartMakeFace.FCStd"
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        assert document.recompute() is not False
        _process_events()

        for record in records:
            result = document.getObject(record["name"])
            assert result is not None and result.TypeId == "Part::Face"
            assert result.Label == record["label"]
            assert result.FaceMakerClass == "Part::FaceMakerUnified"
            assert str(result.VibeCADDefinitionId) == record["definition_id"]
            assert str(result.DesignId) == record["design_id"]
            assert tuple(source.Name for source in result.Sources) == record["source_names"]
            assert tuple(
                source.Name for source in result.VibeCADTimelineReplacedInputs
            ) == record["source_names"]
            assert result.VibeCADTimelineRole == "operation"
            assert all(not source.Visibility for source in result.Sources)
            _assert_shape_signature(result.Shape, record["signature"])
            for _index in range(4):
                assert document.recompute([result], True, True) is not False
                _assert_shape_signature(result.Shape, record["signature"])

        print("VIBECAD_NATIVE_MODEL_PART_MAKE_FACE_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if save_directory is not None:
            shutil.rmtree(save_directory, ignore_errors=True)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
