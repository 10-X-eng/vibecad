# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real GUI/provider lifecycle gate for standalone Part 3D Offset."""

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
        if not button_box.isVisible():
            continue
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
            len(shape.Shells),
            len(shape.Solids),
        ),
        "bounds": tuple(
            float(getattr(bounds, name))
            for name in ("XMin", "XMax", "YMin", "YMax", "ZMin", "ZMax")
        ),
        "area": float(shape.Area),
        "volume": float(shape.Volume),
    }


def _assert_shape_signature(shape, expected: dict[str, object]) -> None:
    actual = _shape_signature(shape)
    assert actual["shape_type"] == expected["shape_type"]
    assert actual["topology"] == expected["topology"]
    assert all(
        _close(left, right, 5.0e-3)
        for left, right in zip(actual["bounds"], expected["bounds"], strict=True)
    ), (actual["bounds"], expected["bounds"])
    assert _close(actual["area"], expected["area"], 5.0e-3)
    assert _close(actual["volume"], expected["volume"], 5.0e-3)


def _visual_signature(obj) -> tuple[tuple[float, ...], ...]:
    view = obj.ViewObject
    return tuple(
        tuple(float(value) for value in getattr(view, name))
        for name in ("ShapeColor", "LineColor", "PointColor")
    )


def _assert_visual_signature(obj, expected) -> None:
    actual = _visual_signature(obj)
    assert all(
        _close(left, right, 1.0 / 255.0 + 1.0e-7)
        for actual_color, expected_color in zip(actual, expected, strict=True)
        for left, right in zip(actual_color, expected_color, strict=True)
    ), (actual, expected)


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


def _publish_source(document, name: str, shape, *, placement=None, color=None):
    source = document.addObject("Part::Feature", name)
    source.Label = name
    source.Shape = shape
    if placement is not None:
        source.Placement = placement
    if color is not None:
        source.ViewObject.ShapeColor = color
        source.ViewObject.LineColor = tuple(min(1.0, value + 0.1) for value in color)
        source.ViewObject.PointColor = tuple(max(0.0, value - 0.1) for value in color)
    return _publish_object(document, source)


def _create_sources(document) -> tuple[dict[str, object], str]:
    document.openTransaction("Create Part 3D Offset gate sources")
    try:
        sources = {
            "HumanBox": _publish_source(
                document,
                "HumanBox",
                Part.makeBox(10, 8, 6),
                color=(0.2, 0.4, 0.7),
            ),
            "SkinBox": _publish_source(
                document,
                "SkinBox",
                Part.makeBox(10, 8, 6, App.Vector(20, 0, 0)),
                color=(0.3, 0.6, 0.2),
            ),
            "PipeBox": _publish_source(
                document,
                "PipeBox",
                Part.makeBox(9, 7, 5, App.Vector(40, 0, 0)),
            ),
            "RectoBox": _publish_source(
                document,
                "RectoBox",
                Part.makeBox(8, 6, 5, App.Vector(58, 0, 0)),
            ),
            "FillFace": _publish_source(
                document,
                "FillFace",
                Part.makePlane(10, 8, App.Vector(76, 0, 0)),
            ),
            "PlacedBox": _publish_source(
                document,
                "PlacedBox",
                Part.makeBox(8, 7, 6),
                placement=App.Placement(
                    App.Vector(96, 3, 2),
                    App.Rotation(App.Vector(0, 0, 1), 17),
                ),
            ),
            "TangentBox": _publish_source(
                document,
                "TangentBox",
                Part.makeBox(7, 6, 5, App.Vector(120, 0, 0)),
            ),
            "RollbackBox": _publish_source(
                document,
                "RollbackBox",
                Part.makeBox(7, 6, 5, App.Vector(136, 0, 0)),
            ),
            "InactiveBox": _publish_source(
                document,
                "InactiveBox",
                Part.makeBox(7, 6, 5, App.Vector(152, 0, 0)),
            ),
        }
        null_source = _publish_source(document, "NullOffsetSource", Part.Shape())
        stale = _publish_source(
            document,
            "StaleOffsetSource",
            Part.makeBox(4, 4, 4, App.Vector(168, 0, 0)),
        )
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    document.openTransaction("Delete stale 3D Offset source")
    try:
        stale_name = stale.Name
        document.removeObject(stale_name)
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    sources["NullOffsetSource"] = null_source
    return sources, stale_name


def _turn() -> NativeTurnSnapshot:
    definition = model_part_capability_definition()
    surface = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot("model", 1, "d" * 64, ("Part_Offset",), (), ()),
        available=True,
        unavailable_reason="",
        tool_names=(definition.name,),
        schemas=(definition.provider_schema(("offset_3d",)),),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(surface)


def _definition(
    source: str,
    value: float,
    mode: str,
    join: str,
    intersection: bool,
    self_intersection: bool,
    fill: bool,
) -> dict[str, object]:
    return {
        "source": {"object_name": source},
        "value_mm": value,
        "mode": mode,
        "join": join,
        "intersection": intersection,
        "self_intersection": self_intersection,
        "fill": fill,
    }


def _arguments(label: str, definition) -> dict[str, object]:
    return {"operation": "offset_3d", "label": label, "definition": definition}


def _expected_shape(document, source, definition):
    modes = {"skin": "Skin", "pipe": "Pipe", "recto_verso": "RectoVerso"}
    joins = {"arc": "Arc", "tangent": "Tangent", "intersection": "Intersection"}
    document.openTransaction("Probe exact Part 3D Offset feature")
    try:
        copied = document.addObject("Part::Feature", "OffsetOracleSource")
        copied.Shape = Part.getShape(source, transform=True).copy()
        result = document.addObject("Part::Offset", "OffsetOracle")
        result.Source = copied
        result.Value = definition["value_mm"]
        result.Mode = modes[definition["mode"]]
        result.Join = joins[definition["join"]]
        result.Intersection = definition["intersection"]
        result.SelfIntersection = definition["self_intersection"]
        result.Fill = definition["fill"]
        assert document.recompute([result], True, True) is not False
        result.touch()
        assert document.recompute([result], True, True) is not False
        assert result.isValid() and not result.Shape.isNull() and result.Shape.isValid()
        return result.Shape.copy()
    finally:
        document.abortTransaction()


def _assert_human_contract(document, sources) -> None:
    source = sources["HumanBox"]
    before = tuple(obj.Name for obj in document.Objects)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(source)
    _process_events()
    assert Gui.isCommandActive("Part_Offset")
    Gui.runCommand("Part_Offset", 0)
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
    assert [mode.itemText(index) for index in range(mode.count())] == [
        "Skin",
        "Pipe",
        "Recto verso",
    ]
    assert [join.itemText(index) for index in range(join.count())] == [
        "Arc",
        "Tangent",
        "Intersection",
    ]
    assert mode.currentIndex() == 0 and join.currentIndex() == 0
    assert not intersection.isChecked()
    assert not self_intersection.isChecked()
    assert not fill.isChecked()
    assert update.isChecked()
    assert not source.Visibility

    ok_button = _task_button(QtWidgets.QDialogButtonBox.Ok)
    assert ok_button is not None
    ok_button.click()
    _process_events(32)
    assert not Gui.Control.activeDialog()
    created = [
        obj
        for obj in document.Objects
        if obj.Name not in before and obj.TypeId == "Part::Offset"
    ]
    assert len(created) == 1
    result = created[0]
    assert result.Source is source
    assert _close(result.Value, 1.0)
    assert (str(result.Mode), str(result.Join)) == ("Skin", "Arc")
    assert not result.Intersection and not result.SelfIntersection and not result.Fill
    assert tuple(result.VibeCADTimelineReplacedInputs) == (source,)
    document.undo()
    _process_events()
    assert tuple(obj.Name for obj in document.Objects) == before
    assert source.Visibility
    Gui.Selection.clearSelection()


def _assert_exact_preflight_rejects_change(document, sources) -> None:
    definition = _definition("RollbackBox", 1.0, "skin", "arc", False, False, False)
    spec = runtime_module.prepare_part_offset(str(document.Uid), definition)
    prepared = runtime_module.preflight_part_offset(document, spec)
    source = sources["RollbackBox"]
    original = source.Placement
    before = tuple(obj.Name for obj in document.Objects)
    rejected = False
    try:
        moved = App.Placement(original)
        moved.Base.x += 1.0
        source.Placement = moved
        try:
            runtime_module.create_part_offset(
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
        (
            "Gate Skin Arc Offset",
            _definition("SkinBox", 2.0, "skin", "arc", False, False, False),
        ),
        (
            "Gate Pipe Intersection Offset",
            _definition("PipeBox", 1.0, "pipe", "intersection", True, False, False),
        ),
        (
            "Gate Recto Verso Self Offset",
            _definition("RectoBox", 1.0, "recto_verso", "arc", False, True, False),
        ),
        (
            "Gate Filled Face Offset",
            _definition("FillFace", 1.0, "skin", "arc", False, False, True),
        ),
        (
            "Gate Placed Negative Offset",
            _definition("PlacedBox", -1.0, "pipe", "arc", False, False, False),
        ),
    )


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    save_directory = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeModelPartOffsetGate")
        VibeGui._connect_document_observer()
        _process_events()
        sources, stale_name = _create_sources(document)
        _assert_human_contract(document, sources)
        _assert_exact_preflight_rejects_change(document, sources)

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-part-offset-gui")
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
                f"model-part-offset-call-{call_number}",
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
                "operation": "offset_3d",
                "label": "Incomplete 3D Offset",
                "definition": {"source": {"object_name": "SkinBox"}},
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
            source_visual = _visual_signature(source)
            response = native_call(_arguments(label, definition))
            assert set(response) == expected_fields
            assert response["shape_type"] == expected["shape_type"]
            assert response["solid_count"] == expected["topology"][5]
            assert response["face_count"] == expected["topology"][3]
            assert _close(response["area_mm2"], expected["area"], 5.0e-3)
            assert _close(response["volume_mm3"], expected["volume"], 5.0e-3)
            assert response["assistant_undo_available"] is True
            assert len(response["receipt"]["created"]) == 1
            assert response["receipt"]["changed"] == []
            assert response["receipt"]["deleted"] == []

            result = document.getObject(response["root"]["object_name"])
            assert result.TypeId == "Part::Offset"
            assert result.Source is source
            assert _close(result.Value, definition["value_mm"])
            assert str(result.Mode) == {
                "skin": "Skin",
                "pipe": "Pipe",
                "recto_verso": "RectoVerso",
            }[definition["mode"]]
            assert str(result.Join) == {
                "arc": "Arc",
                "tangent": "Tangent",
                "intersection": "Intersection",
            }[definition["join"]]
            assert bool(result.Intersection) is definition["intersection"]
            assert bool(result.SelfIntersection) is definition["self_intersection"]
            assert bool(result.Fill) is definition["fill"]
            assert result.VibeCADTimelineRole == "operation"
            assert getattr(result, "VibeCADTimelineOwner", None) is None
            assert tuple(result.VibeCADTimelineReplacedInputs) == (source,)
            assert str(result.VibeCADDefinitionId) and str(result.DesignId)
            assert not source.Visibility
            _assert_visual_signature(result, source_visual)
            _assert_shape_signature(result.Shape, expected)
            for _repeat in range(4):
                assert document.recompute([result], True, True) is not False
                _assert_shape_signature(result.Shape, expected)
            _assert_shape_signature(
                Part.getShape(source, transform=True),
                source_shape,
            )
            assert _placement_signature(source) == source_placement
            record = {
                "result": result.Name,
                "source": source.Name,
                "definition_id": str(result.VibeCADDefinitionId),
                "design_id": str(result.DesignId),
                "definition": dict(definition),
                "signature": expected,
                "visual": source_visual,
            }
            document.undo()
            _process_events()
            assert document.getObject(record["result"]) is None
            assert source.Visibility
            document.redo()
            _process_events()
            assert document.getObject(record["result"]) is not None
            assert not source.Visibility
            records.append(record)

        failure_cases = (
            (
                _arguments(
                    "Stale 3D Offset",
                    _definition(stale_name, 1.0, "skin", "arc", False, False, False),
                ),
                "NATIVE_TARGET_INVALID",
            ),
            (
                _arguments(
                    "Null 3D Offset",
                    _definition(
                        "NullOffsetSource", 1.0, "skin", "arc", False, False, False
                    ),
                ),
                "NATIVE_MODEL_INVALID",
            ),
            (
                _arguments(
                    "Unsupported Tangent 3D Offset",
                    _definition(
                        "TangentBox", 1.0, "skin", "tangent", False, False, False
                    ),
                ),
                "NATIVE_MODEL_INVALID",
            ),
        )
        for arguments, error_code in failure_cases:
            before = tuple(obj.Name for obj in document.Objects)
            response = native_call(arguments, succeeds=False)
            assert response["error_code"] == error_code
            assert tuple(obj.Name for obj in document.Objects) == before
            assert document.HasPendingTransaction is False
            source_name = arguments["definition"]["source"]["object_name"]
            source = sources.get(source_name)
            if source is not None:
                assert source.Visibility

        timeline = document.getObject("VibeCADTimeline")
        timeline_end = int(timeline.Position)
        timeline.Position = 0
        _process_events()
        before = tuple(obj.Name for obj in document.Objects)
        inactive = native_call(
            _arguments(
                "Inactive 3D Offset",
                _definition("InactiveBox", 1.0, "skin", "arc", False, False, False),
            ),
            succeeds=False,
        )
        assert inactive["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        timeline.Position = timeline_end
        _process_events()

        before = tuple(obj.Name for obj in document.Objects)
        original_verify = runtime_module.verify_part_offset

        def reject_after_creation(_document, _draft):
            raise NativeModelError("Forced 3D Offset postcondition failure.")

        runtime_module.verify_part_offset = reject_after_creation
        try:
            rollback = native_call(
                _arguments(
                    "Rollback 3D Offset",
                    _definition(
                        "RollbackBox", 1.0, "skin", "arc", False, False, False
                    ),
                ),
                succeeds=False,
            )
        finally:
            runtime_module.verify_part_offset = original_verify
        assert rollback["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert sources["RollbackBox"].Visibility

        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-offset-"))
        save_path = save_directory / "ModelPartOffset.FCStd"
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        assert document.recompute() is not False
        _process_events()
        for record in records:
            result = document.getObject(record["result"])
            source = document.getObject(record["source"])
            definition = record["definition"]
            assert result.Source is source
            assert str(result.VibeCADDefinitionId) == record["definition_id"]
            assert str(result.DesignId) == record["design_id"]
            assert _close(result.Value, definition["value_mm"])
            assert not source.Visibility
            _assert_visual_signature(result, record["visual"])
            _assert_shape_signature(result.Shape, record["signature"])

        print("VIBECAD_NATIVE_MODEL_PART_OFFSET_GUI_OK", flush=True)
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
