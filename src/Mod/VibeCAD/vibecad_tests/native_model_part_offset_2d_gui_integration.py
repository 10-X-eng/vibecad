# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real GUI/provider lifecycle gate for standalone Part 2D Offset."""

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


def _task_button(standard_button):
    _process_events()
    for button_box in Gui.getMainWindow().findChildren(QtWidgets.QDialogButtonBox):
        if button_box.isVisible():
            button = button_box.button(standard_button)
            if button is not None and button.isVisible() and button.isEnabled():
                return button
    return None


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
        "length": float(shape.Length),
        "area": float(shape.Area),
    }


def _assert_shape_signature(shape, expected) -> None:
    actual = _shape_signature(shape)
    assert actual["shape_type"] == expected["shape_type"]
    assert actual["topology"] == expected["topology"]
    assert all(
        _close(left, right, 5.0e-3)
        for left, right in zip(actual["bounds"], expected["bounds"], strict=True)
    ), (actual["bounds"], expected["bounds"])
    assert _close(actual["length"], expected["length"], 5.0e-3)
    assert _close(actual["area"], expected["area"], 5.0e-3)


def _placement_signature(obj) -> tuple[float, ...]:
    placement = obj.Placement
    return (
        float(placement.Base.x),
        float(placement.Base.y),
        float(placement.Base.z),
        *(float(value) for value in placement.Rotation.Q),
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


def _closed_wire(x: float):
    return Part.makePolygon(
        [
            App.Vector(x, 0, 0),
            App.Vector(x + 10, 0, 0),
            App.Vector(x + 10, 8, 0),
            App.Vector(x, 8, 0),
            App.Vector(x, 0, 0),
        ]
    )


def _open_wire(x: float):
    return Part.makePolygon(
        [App.Vector(x, 0, 0), App.Vector(x + 10, 0, 0), App.Vector(x + 10, 8, 0)]
    )


def _create_sources(document) -> tuple[dict[str, object], str]:
    document.openTransaction("Create Part 2D Offset gate sources")
    try:
        sources = {
            "HumanFace": _publish_source(document, "HumanFace", Part.makePlane(10, 8)),
            "SkinFace": _publish_source(
                document,
                "SkinFace",
                Part.makePlane(10, 8, App.Vector(20, 0, 0)),
            ),
            "PipeWire": _publish_source(document, "PipeWire", _closed_wire(40)),
            "OpenWire": _publish_source(document, "OpenWire", _open_wire(60)),
            "PlacedFace": _publish_source(
                document,
                "PlacedFace",
                Part.makePlane(9, 7),
                placement=App.Placement(
                    App.Vector(82, 3, 4),
                    App.Rotation(App.Vector(1, 0, 0), 22),
                ),
            ),
            "RollbackFace": _publish_source(
                document,
                "RollbackFace",
                Part.makePlane(8, 6, App.Vector(105, 0, 0)),
            ),
            "InactiveFace": _publish_source(
                document,
                "InactiveFace",
                Part.makePlane(8, 6, App.Vector(120, 0, 0)),
            ),
            "SolidSource": _publish_source(
                document,
                "SolidSource",
                Part.makeBox(6, 5, 4, App.Vector(135, 0, 0)),
            ),
            "NonPlanarSource": _publish_source(
                document,
                "NonPlanarSource",
                Part.makePolygon(
                    [
                        App.Vector(150, 0, 0),
                        App.Vector(156, 0, 0),
                        App.Vector(156, 6, 1),
                        App.Vector(150, 6, 0),
                    ]
                ),
            ),
        }
        null_source = _publish_source(document, "NullOffset2DSource", Part.Shape())
        stale = _publish_source(
            document,
            "StaleOffset2DSource",
            Part.makePlane(4, 4, App.Vector(170, 0, 0)),
        )
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    document.openTransaction("Delete stale 2D Offset source")
    try:
        stale_name = stale.Name
        document.removeObject(stale_name)
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    sources["NullOffset2DSource"] = null_source
    return sources, stale_name


def _turn() -> NativeTurnSnapshot:
    definition = model_part_capability_definition()
    surface = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot("model", 1, "e" * 64, ("Part_Offset2D",), (), ()),
        available=True,
        unavailable_reason="",
        tool_names=(definition.name,),
        schemas=(definition.provider_schema(("offset_2d",)),),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(surface)


def _definition(source, value, mode, join, intersection, fill):
    return {
        "source": {"object_name": source},
        "value_mm": value,
        "mode": mode,
        "join": join,
        "intersection": intersection,
        "fill": fill,
    }


def _arguments(label, definition):
    return {"operation": "offset_2d", "label": label, "definition": definition}


def _expected_shape(document, source, definition):
    document.openTransaction("Probe exact Part 2D Offset feature")
    try:
        copied = document.addObject("Part::Feature", "Offset2DOracleSource")
        copied.Shape = Part.getShape(source, transform=True).copy()
        result = document.addObject("Part::Offset2D", "Offset2DOracle")
        result.Source = copied
        result.Value = definition["value_mm"]
        result.Mode = {"skin": "Skin", "pipe": "Pipe"}[definition["mode"]]
        result.Join = {
            "arc": "Arc",
            "tangent": "Tangent",
            "intersection": "Intersection",
        }[definition["join"]]
        result.Intersection = definition["intersection"]
        result.Fill = definition["fill"]
        assert document.recompute([result], True, True) is not False
        assert result.isValid() and not result.Shape.isNull() and result.Shape.isValid()
        return result.Shape.copy()
    finally:
        document.abortTransaction()


def _assert_human_contract(document, sources) -> None:
    source = sources["HumanFace"]
    before = tuple(obj.Name for obj in document.Objects)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(source)
    _process_events()
    assert Gui.isCommandActive("Part_Offset2D")
    Gui.runCommand("Part_Offset2D", 0)
    _process_events(32)
    assert Gui.Control.activeDialog()
    window = Gui.getMainWindow()
    value = window.findChild(QtWidgets.QAbstractSpinBox, "spinOffset")
    mode = window.findChild(QtWidgets.QComboBox, "modeType")
    join = window.findChild(QtWidgets.QComboBox, "joinType")
    intersection = window.findChild(QtWidgets.QCheckBox, "intersection")
    self_intersection = window.findChild(QtWidgets.QCheckBox, "selfIntersection")
    fill = window.findChild(QtWidgets.QCheckBox, "fillOffset")
    update = window.findChild(QtWidgets.QCheckBox, "updateView")
    assert all(
        widget is not None
        for widget in (value, mode, join, intersection, self_intersection, fill, update)
    )
    assert _close(value.property("rawValue"), 1.0)
    assert [mode.itemText(index) for index in range(mode.count())] == ["Skin", "Pipe"]
    assert mode.currentIndex() == 1
    assert [join.itemText(index) for index in range(join.count())] == [
        "Arc",
        "Tangent",
        "Intersection",
    ]
    assert join.currentIndex() == 0
    assert not intersection.isChecked() and not fill.isChecked()
    assert not self_intersection.isVisible()
    assert update.isChecked() and not source.Visibility
    ok_button = _task_button(QtWidgets.QDialogButtonBox.Ok)
    assert ok_button is not None
    ok_button.click()
    _process_events(32)
    assert not Gui.Control.activeDialog()
    created = [
        obj
        for obj in document.Objects
        if obj.Name not in before and obj.TypeId == "Part::Offset2D"
    ]
    assert len(created) == 1
    result = created[0]
    assert result.Source is source
    assert (str(result.Mode), str(result.Join)) == ("Pipe", "Arc")
    assert not result.Intersection and not result.SelfIntersection and not result.Fill
    assert tuple(result.VibeCADTimelineReplacedInputs) == (source,)
    document.undo()
    _process_events()
    assert tuple(obj.Name for obj in document.Objects) == before
    assert source.Visibility
    Gui.Selection.clearSelection()


def _assert_exact_preflight_rejects_change(document, sources) -> None:
    definition = _definition("RollbackFace", 1.0, "pipe", "arc", False, False)
    spec = runtime_module.prepare_part_offset_2d(str(document.Uid), definition)
    prepared = runtime_module.preflight_part_offset(document, spec)
    source = sources["RollbackFace"]
    original = source.Placement
    before = tuple(obj.Name for obj in document.Objects)
    rejected = False
    try:
        moved = App.Placement(original)
        moved.Base.x += 1.0
        source.Placement = moved
        try:
            runtime_module.create_part_offset_2d(
                document,
                label="Must Not Exist",
                prepared=prepared,
            )
        except NativeModelError as exc:
            rejected = "changed after preflight" in str(exc)
    finally:
        source.Placement = original
        assert document.recompute([source], True, True) is not False
    assert rejected and tuple(obj.Name for obj in document.Objects) == before


def _cases():
    return (
        ("Gate Skin Arc 2D", _definition("SkinFace", 1.5, "skin", "arc", False, False)),
        (
            "Gate Pipe Tangent Filled 2D",
            _definition("PipeWire", 1.0, "pipe", "tangent", True, True),
        ),
        (
            "Gate Negative Intersection 2D",
            _definition("OpenWire", -0.75, "skin", "intersection", False, False),
        ),
        (
            "Gate Placed Arc 2D",
            _definition("PlacedFace", 1.0, "pipe", "arc", True, False),
        ),
    )


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    save_directory = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeModelPartOffset2DGate")
        VibeGui._connect_document_observer()
        _process_events()
        sources, stale_name = _create_sources(document)
        _assert_human_contract(document, sources)
        _assert_exact_preflight_rejects_change(document, sources)

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-part-offset-2d-gui")
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
                f"model-part-offset-2d-call-{call_number}",
            )
            assert response.get("ok") is succeeds, (
                arguments.get("label"),
                response,
                debug_events[-1] if debug_events else None,
            )
            return response

        before = tuple(obj.Name for obj in document.Objects)
        incomplete = native_call(
            {
                "operation": "offset_2d",
                "label": "Incomplete 2D Offset",
                "definition": {"source": {"object_name": "SkinFace"}},
            },
            succeeds=False,
        )
        assert incomplete["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before

        expected_fields = {
            "ok",
            "root",
            "shape_type",
            "solid_count",
            "face_count",
            "area_mm2",
            "volume_mm3",
            "receipt",
            "assistant_undo_available",
        }
        records = []
        for label, definition in _cases():
            source = sources[definition["source"]["object_name"]]
            expected = _shape_signature(_expected_shape(document, source, definition))
            source_shape = _shape_signature(Part.getShape(source, transform=True))
            source_placement = _placement_signature(source)
            response = native_call(_arguments(label, definition))
            assert set(response) == expected_fields
            assert response["shape_type"] == expected["shape_type"]
            assert response["solid_count"] == expected["topology"][4]
            assert response["face_count"] == expected["topology"][3]
            assert _close(response["area_mm2"], expected["area"], 5.0e-3)
            result = document.getObject(response["root"]["object_name"])
            assert result.TypeId == "Part::Offset2D" and result.Source is source
            assert _close(result.Value, definition["value_mm"])
            assert str(result.Mode) == {"skin": "Skin", "pipe": "Pipe"}[
                definition["mode"]
            ]
            assert str(result.Join) == {
                "arc": "Arc",
                "tangent": "Tangent",
                "intersection": "Intersection",
            }[definition["join"]]
            assert bool(result.Intersection) is definition["intersection"]
            assert not result.SelfIntersection
            assert bool(result.Fill) is definition["fill"]
            assert result.VibeCADTimelineRole == "operation"
            assert tuple(result.VibeCADTimelineReplacedInputs) == (source,)
            assert str(result.VibeCADDefinitionId) and str(result.DesignId)
            assert not source.Visibility
            _assert_shape_signature(result.Shape, expected)
            for _repeat in range(4):
                assert document.recompute([result], True, True) is not False
                _assert_shape_signature(result.Shape, expected)
            _assert_shape_signature(Part.getShape(source, transform=True), source_shape)
            assert _placement_signature(source) == source_placement
            record = {
                "result": result.Name,
                "source": source.Name,
                "definition_id": str(result.VibeCADDefinitionId),
                "design_id": str(result.DesignId),
                "signature": expected,
            }
            document.undo()
            _process_events()
            assert document.getObject(record["result"]) is None and source.Visibility
            document.redo()
            _process_events()
            assert document.getObject(record["result"]) is not None and not source.Visibility
            records.append(record)

        failure_cases = (
            (stale_name, "NATIVE_TARGET_INVALID"),
            ("NullOffset2DSource", "NATIVE_MODEL_INVALID"),
            ("SolidSource", "NATIVE_MODEL_INVALID"),
            ("NonPlanarSource", "NATIVE_MODEL_INVALID"),
        )
        for source_name, error_code in failure_cases:
            before = tuple(obj.Name for obj in document.Objects)
            response = native_call(
                _arguments(
                    "Invalid 2D Offset",
                    _definition(source_name, 1.0, "pipe", "arc", False, False),
                ),
                succeeds=False,
            )
            assert response["error_code"] == error_code
            assert tuple(obj.Name for obj in document.Objects) == before
            assert document.HasPendingTransaction is False

        timeline = document.getObject("VibeCADTimeline")
        timeline_end = int(timeline.Position)
        timeline.Position = 0
        _process_events()
        before = tuple(obj.Name for obj in document.Objects)
        inactive = native_call(
            _arguments(
                "Inactive 2D Offset",
                _definition("InactiveFace", 1.0, "pipe", "arc", False, False),
            ),
            succeeds=False,
        )
        assert inactive["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        timeline.Position = timeline_end
        _process_events()

        before = tuple(obj.Name for obj in document.Objects)
        original_verify = runtime_module.verify_part_offset_2d

        def reject_after_creation(_document, _draft):
            raise NativeModelError("Forced 2D Offset postcondition failure.")

        runtime_module.verify_part_offset_2d = reject_after_creation
        try:
            rollback = native_call(
                _arguments(
                    "Rollback 2D Offset",
                    _definition("RollbackFace", 1.0, "pipe", "arc", False, False),
                ),
                succeeds=False,
            )
        finally:
            runtime_module.verify_part_offset_2d = original_verify
        assert rollback["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert sources["RollbackFace"].Visibility

        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-offset-2d-"))
        save_path = save_directory / "ModelPartOffset2D.FCStd"
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        assert document.recompute() is not False
        _process_events()
        for record in records:
            result = document.getObject(record["result"])
            source = document.getObject(record["source"])
            assert result.Source is source
            assert str(result.VibeCADDefinitionId) == record["definition_id"]
            assert str(result.DesignId) == record["design_id"]
            assert not source.Visibility
            _assert_shape_signature(result.Shape, record["signature"])

        print("VIBECAD_NATIVE_MODEL_PART_OFFSET_2D_GUI_OK", flush=True)
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
