# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real VibeCAD GUI and provider lifecycle gate for Ruled Surface."""

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
            len(shape.Shells),
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


def _edge(x: float, y: float, z: float, length: float):
    return Part.makeLine(App.Vector(x, y, z), App.Vector(x + length, y, z))


def _wire(x: float, y: float, z: float, size: float):
    return Part.makePolygon(
        [
            App.Vector(x, y, z),
            App.Vector(x + size, y, z),
            App.Vector(x + size, y + size, z),
            App.Vector(x, y + size, z),
            App.Vector(x, y, z),
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
    document.openTransaction("Create Ruled Surface gate sources")
    try:
        sources = {
            "HumanCurves": _publish_source(
                document,
                "HumanCurves",
                Part.makeCompound([_edge(0, 0, 0, 8), _edge(0, 0, 4, 8)]),
            ),
            "WholeEdgeA": _publish_source(
                document,
                "WholeEdgeA",
                _edge(12, 0, 0, 10),
            ),
            "WholeEdgeB": _publish_source(
                document,
                "WholeEdgeB",
                _edge(12, 0, 5, 10),
            ),
            "SubEdges": _publish_source(
                document,
                "SubEdges",
                Part.makeCompound([_edge(26, 0, 0, 9), _edge(26, 4, 0, 9)]),
            ),
            "WholeWireA": _publish_source(
                document,
                "WholeWireA",
                _wire(40, 0, 0, 4),
            ),
            "WholeWireB": _publish_source(
                document,
                "WholeWireB",
                _wire(40, 0, 3, 4),
            ),
            "PlacedEdgeA": _publish_source(
                document,
                "PlacedEdgeA",
                _edge(0, 0, 0, 6),
                placement=App.Placement(App.Vector(50, 2, 3), App.Rotation()),
            ),
            "PlacedEdgeB": _publish_source(
                document,
                "PlacedEdgeB",
                _edge(0, 0, 0, 6),
                placement=App.Placement(App.Vector(50, 2, 8), App.Rotation()),
            ),
            "RollbackEdgeA": _publish_source(
                document,
                "RollbackEdgeA",
                _edge(62, 0, 0, 5),
            ),
            "RollbackEdgeB": _publish_source(
                document,
                "RollbackEdgeB",
                _edge(62, 0, 4, 5),
            ),
            "InactiveEdge": _publish_source(
                document,
                "InactiveEdge",
                _edge(70, 0, 0, 4),
            ),
            "FaceSource": _publish_source(
                document,
                "FaceSource",
                Part.makePlane(4, 3, App.Vector(78, 0, 0)),
            ),
            "CompoundWhole": _publish_source(
                document,
                "CompoundWhole",
                Part.makeCompound([_edge(86, 0, 0, 4), _edge(86, 0, 3, 4)]),
            ),
        }
        stale = _publish_source(
            document,
            "StaleRuledEdge",
            _edge(94, 0, 0, 4),
        )
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise

    document.openTransaction("Delete stale Ruled Surface source")
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
            "r" * 64,
            ("Part_RuledSurface",),
            (),
            (),
        ),
        available=True,
        unavailable_reason="",
        tool_names=(definition.name,),
        schemas=(definition.provider_schema(("ruled_surface",)),),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(surface)


def _curve(name: str, subelement: str | None = None) -> dict[str, str]:
    value = {"object_name": name}
    if subelement is not None:
        value["subelement"] = subelement
    return value


def _arguments(label: str, first, second) -> dict[str, object]:
    return {
        "operation": "ruled_surface",
        "label": label,
        "definition": {"curves": [first, second]},
    }


def _link(value) -> tuple[object | None, tuple[str, ...]]:
    if not value:
        return None, ()
    target, names = value
    if isinstance(names, str):
        names = (names,) if names else ()
    return target, tuple(str(name) for name in names)


def _resolved_curve(source, subelement: str | None):
    if subelement is None:
        return Part.getShape(source, transform=True)
    return Part.getShape(
        source,
        subelement,
        needSubElement=True,
        transform=True,
    )


def _expected_shape(sources, first, second):
    curves = []
    for reference in (first, second):
        curves.append(
            _resolved_curve(
                sources[reference["object_name"]],
                reference.get("subelement"),
            )
        )
    return Part.makeRuledSurface(curves[0], curves[1], 0)


def _assert_human_contract(document, sources) -> None:
    Gui.Selection.clearSelection()
    _process_events()
    assert not Gui.isCommandActive("Part_RuledSurface")
    Gui.Selection.addSelection(sources["WholeEdgeA"])
    _process_events()
    assert not Gui.isCommandActive("Part_RuledSurface")

    owner = sources["HumanCurves"]
    expected = Part.makeRuledSurface(
        _resolved_curve(owner, "Edge1"),
        _resolved_curve(owner, "Edge2"),
        0,
    )
    before = tuple(obj.Name for obj in document.Objects)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(owner, "Edge1")
    Gui.Selection.addSelection(owner, "Edge2")
    _process_events()
    assert Gui.isCommandActive("Part_RuledSurface")
    Gui.runCommand("Part_RuledSurface", 0)
    _process_events(24)
    assert not Gui.Control.activeDialog()
    created = [obj for obj in document.Objects if obj.Name not in before]
    assert len(created) == 1
    result = created[0]
    assert result.TypeId == "Part::RuledSurface"
    assert _link(result.Curve1) == (owner, ("Edge1",))
    assert _link(result.Curve2) == (owner, ("Edge2",))
    assert str(result.Orientation) == "Automatic"
    assert result.VibeCADTimelineRole == "operation"
    assert "VibeCADTimelineReplacedInputs" not in result.PropertiesList
    assert owner.Visibility
    _assert_shape_signature(result.Shape, _shape_signature(expected))
    Gui.Selection.clearSelection()


def _cases() -> tuple[dict[str, object], ...]:
    return (
        {
            "label": "Gate Whole Edge Ruled Surface",
            "first": _curve("WholeEdgeA"),
            "second": _curve("WholeEdgeB"),
        },
        {
            "label": "Gate Same Object Ruled Surface",
            "first": _curve("SubEdges", "Edge1"),
            "second": _curve("SubEdges", "Edge2"),
        },
        {
            "label": "Gate Wire Ruled Surface",
            "first": _curve("WholeWireA"),
            "second": _curve("WholeWireB"),
        },
        {
            "label": "Gate Placed Ruled Surface",
            "first": _curve("PlacedEdgeA"),
            "second": _curve("PlacedEdgeB"),
        },
    )


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    save_directory = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeModelPartRuledSurfaceGate")
        VibeGui._connect_document_observer()
        _process_events()
        sources, stale_name = _create_sources(document)
        _assert_human_contract(document, sources)

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-part-ruled-surface-gui")
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
                f"model-part-ruled-surface-call-{call_number}",
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
                "operation": "ruled_surface",
                "definition": {
                    "curves": [_curve("WholeEdgeA"), _curve("WholeEdgeB")]
                },
            },
            succeeds=False,
        )
        assert invalid_schema["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before

        expected_fields = {
            "ok",
            "root",
            "curve_types",
            "shape_type",
            "face_count",
            "area_mm2",
            "receipt",
            "assistant_undo_available",
        }
        records = []
        for case in _cases():
            first = case["first"]
            second = case["second"]
            expected = _shape_signature(_expected_shape(sources, first, second))
            source_objects = tuple(
                dict.fromkeys(
                    (sources[first["object_name"]], sources[second["object_name"]])
                )
            )
            source_signatures = tuple(
                _shape_signature(Part.getShape(source, transform=True))
                for source in source_objects
            )
            assert all(source.Visibility for source in source_objects)
            response = native_call(
                _arguments(case["label"], first, second)
            )
            assert set(response) == expected_fields
            assert response["curve_types"] == [
                str(_resolved_curve(sources[first["object_name"]], first.get("subelement")).ShapeType),
                str(
                    _resolved_curve(
                        sources[second["object_name"]],
                        second.get("subelement"),
                    ).ShapeType
                ),
            ]
            assert response["shape_type"] == expected["shape_type"]
            assert response["face_count"] == expected["topology"][3]
            assert _close(response["area_mm2"], expected["measures"][1])
            assert response["assistant_undo_available"] is True
            assert len(response["receipt"]["created"]) == 1
            assert response["receipt"]["changed"] == []
            assert response["receipt"]["deleted"] == []

            result = document.getObject(response["root"]["object_name"])
            assert result is not None and result.TypeId == "Part::RuledSurface"
            assert result.Label == case["label"]
            assert str(result.Orientation) == "Automatic"
            assert _link(result.Curve1) == (
                sources[first["object_name"]],
                (first.get("subelement") or "",),
            )
            assert _link(result.Curve2) == (
                sources[second["object_name"]],
                (second.get("subelement") or "",),
            )
            assert result.getParentGeoFeatureGroup() is None
            assert result.VibeCADTimelineRole == "operation"
            assert getattr(result, "VibeCADTimelineOwner", None) is None
            assert str(result.VibeCADDefinitionId) and str(result.DesignId)
            assert "VibeCADTimelineReplacedInputs" not in result.PropertiesList
            assert all(source.Visibility for source in source_objects)
            _assert_shape_signature(result.Shape, expected)
            for _index in range(4):
                assert document.recompute([result], True, True) is not False
                _assert_shape_signature(result.Shape, expected)
            for source, signature in zip(
                source_objects,
                source_signatures,
                strict=True,
            ):
                _assert_shape_signature(Part.getShape(source, transform=True), signature)

            record = {
                "name": result.Name,
                "label": str(result.Label),
                "definition_id": str(result.VibeCADDefinitionId),
                "design_id": str(result.DesignId),
                "first": (first["object_name"], first.get("subelement") or ""),
                "second": (second["object_name"], second.get("subelement") or ""),
                "signature": expected,
            }
            document.undo()
            _process_events()
            assert document.getObject(record["name"]) is None
            assert all(source.Visibility for source in source_objects)
            document.redo()
            _process_events()
            result = document.getObject(record["name"])
            assert result is not None
            assert all(source.Visibility for source in source_objects)
            _assert_shape_signature(result.Shape, expected)
            records.append(record)

        failure_cases = (
            (
                _arguments("Missing Ruled Curve", _curve(stale_name), _curve("WholeEdgeB")),
                "NATIVE_TARGET_INVALID",
            ),
            (
                _arguments("Face Ruled Curve", _curve("FaceSource"), _curve("WholeEdgeB")),
                "NATIVE_MODEL_INVALID",
            ),
            (
                _arguments(
                    "Whole Compound Ruled Curve",
                    _curve("CompoundWhole"),
                    _curve("WholeEdgeB"),
                ),
                "NATIVE_MODEL_INVALID",
            ),
            (
                _arguments(
                    "Duplicate Ruled Curves",
                    _curve("RollbackEdgeA"),
                    _curve("RollbackEdgeA"),
                ),
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
        inactive = sources["InactiveEdge"]
        timeline.Position = 0
        _process_events()
        assert not PartGui.isModelingObjectActive(inactive)
        before = tuple(obj.Name for obj in document.Objects)
        inactive_response = native_call(
            _arguments(
                "Inactive Ruled Surface",
                _curve(inactive.Name),
                _curve("RollbackEdgeB"),
            ),
            succeeds=False,
        )
        assert inactive_response["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        timeline.Position = timeline_end
        _process_events()
        assert PartGui.isModelingObjectActive(inactive)

        rollback_sources = (sources["RollbackEdgeA"], sources["RollbackEdgeB"])
        rollback_signatures = tuple(
            _shape_signature(Part.getShape(source, transform=True))
            for source in rollback_sources
        )
        before = tuple(obj.Name for obj in document.Objects)
        original_verify = runtime_module.verify_part_ruled_surface

        def reject_after_creation(_document, _draft):
            raise NativeModelError("Forced exact Ruled Surface postcondition failure.")

        runtime_module.verify_part_ruled_surface = reject_after_creation
        try:
            rollback = native_call(
                _arguments(
                    "Rollback Ruled Surface",
                    _curve(rollback_sources[0].Name),
                    _curve(rollback_sources[1].Name),
                ),
                succeeds=False,
            )
        finally:
            runtime_module.verify_part_ruled_surface = original_verify
        assert rollback["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert document.HasPendingTransaction is False
        assert all(source.Visibility for source in rollback_sources)
        for source, signature in zip(
            rollback_sources,
            rollback_signatures,
            strict=True,
        ):
            _assert_shape_signature(Part.getShape(source, transform=True), signature)

        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-ruled-"))
        save_path = save_directory / "ModelPartRuledSurface.FCStd"
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        assert document.recompute() is not False
        _process_events()

        for record in records:
            result = document.getObject(record["name"])
            assert result is not None and result.TypeId == "Part::RuledSurface"
            assert result.Label == record["label"]
            assert str(result.Orientation) == "Automatic"
            assert str(result.VibeCADDefinitionId) == record["definition_id"]
            assert str(result.DesignId) == record["design_id"]
            first_target, first_names = _link(result.Curve1)
            second_target, second_names = _link(result.Curve2)
            assert (first_target.Name, first_names) == (
                record["first"][0],
                (record["first"][1],),
            )
            assert (second_target.Name, second_names) == (
                record["second"][0],
                (record["second"][1],),
            )
            assert result.VibeCADTimelineRole == "operation"
            assert "VibeCADTimelineReplacedInputs" not in result.PropertiesList
            assert first_target.Visibility and second_target.Visibility
            _assert_shape_signature(result.Shape, record["signature"])
            for _index in range(4):
                assert document.recompute([result], True, True) is not False
                _assert_shape_signature(result.Shape, record["signature"])

        print("VIBECAD_NATIVE_MODEL_PART_RULED_SURFACE_GUI_OK", flush=True)
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
