# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real human-command and Native lifecycle gate for Surface Blend Curve."""

from __future__ import annotations

import json
import math
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
import VibeCADNativeModelSurfaceRuntime as runtime_module
from VibeCADNativeModelSurfaceSchema import model_surface_capability_definition
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot
from VibeCADNativeSurfaceBlendCurve import (
    create_surface_blend_curve,
    preflight_surface_blend_curve,
    prepare_surface_blend_curve,
)
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger


_CONTINUITIES = {"C0": 0, "G1": 1, "G2": 2, "G3": 3, "G4": 4}


def _process_events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _close(left: float, right: float, tolerance: float = 1.0e-7) -> bool:
    return math.isclose(float(left), float(right), rel_tol=1.0e-9, abs_tol=tolerance)


def _shape_signature(shape) -> tuple[object, ...]:
    bounds = shape.BoundBox
    return (
        str(shape.ShapeType),
        len(shape.Vertexes),
        len(shape.Edges),
        len(shape.Wires),
        len(shape.Faces),
        float(shape.Length),
        float(bounds.XMin),
        float(bounds.XMax),
        float(bounds.YMin),
        float(bounds.YMax),
        float(bounds.ZMin),
        float(bounds.ZMax),
    )


def _assert_signature(actual, expected) -> None:
    assert actual[:5] == expected[:5]
    assert all(
        _close(left, right)
        for left, right in zip(actual[5:], expected[5:], strict=True)
    ), (actual, expected)


def _line(x: float, y: float, z: float, length: float = 10.0):
    return Part.makeLine(App.Vector(x, y, z), App.Vector(x + length, y, z))


def _bezier_edge(x: float, y: float, z: float, rise: float = 1.0):
    curve = Part.BezierCurve()
    curve.setPoles(
        [
            App.Vector(x, y, z),
            App.Vector(x + 2, y + 0.3, z + rise),
            App.Vector(x + 4, y + 0.8, z - 0.4 * rise),
            App.Vector(x + 6, y + 1.4, z + 0.6 * rise),
            App.Vector(x + 8, y + 1.0, z + 0.2 * rise),
            App.Vector(x + 10, y, z + 0.5 * rise),
        ]
    )
    return curve.toShape()


def _edge_pair(x: float, y: float, z: float, *, bezier: bool = False):
    if bezier:
        return Part.makeCompound(
            [
                _bezier_edge(x, y, z, 1.2),
                _bezier_edge(x, y + 8, z + 2, -0.9),
            ]
        )
    return Part.makeCompound(
        [_line(x, y, z), _line(x, y + 8, z + 2)]
    )


def _publish_object(document, obj):
    PartDesign.initializeDesignDefinition(obj)
    document.publishProvisionalTimelineOperationBlock(obj, (), ())
    assert document.recompute([obj], True, True) is not False
    PartDesign.finalizeDesignDefinition(obj)
    assert PartGui.isModelingObjectActive(obj)
    return obj


def _publish_source(document, name: str, shape, *, placement=None, visible=True):
    source = document.addObject("Part::Feature", name)
    source.Label = name
    source.Shape = shape
    if placement is not None:
        source.Placement = placement
    source.Visibility = visible
    return _publish_object(document, source)


def _body_source(document, name: str, shape):
    body = document.addObject("PartDesign::Body", name)
    seed = body.newObject("PartDesign::Feature", f"{name}Seed")
    seed.Label = f"{name} Seed"
    seed.Shape = shape
    assert document.recompute([seed, body], True, True) is not False
    body.Visibility = True
    assert PartGui.isModelingObjectActive(body)
    return body, seed


def _create_sources(document):
    document.openTransaction("Create Blend Curve gate sources")
    try:
        sources = {
            "HumanEdges": _publish_source(
                document, "HumanEdges", _edge_pair(0, 0, 0, bezier=True)
            ),
            "DefaultEdges": _publish_source(
                document, "DefaultEdges", _edge_pair(20, 0, 0)
            ),
            "ControlledEdges": _publish_source(
                document, "ControlledEdges", _edge_pair(40, 0, 0, bezier=True)
            ),
            "PlacedEdges": _publish_source(
                document,
                "PlacedEdges",
                _edge_pair(0, 0, 0, bezier=True),
                placement=App.Placement(
                    App.Vector(65, -4, 3),
                    App.Rotation(App.Vector(0, 0, 1), 23),
                ),
            ),
            "HiddenEdges": _publish_source(
                document,
                "HiddenEdges",
                _edge_pair(90, 0, 0),
                visible=False,
            ),
            "InactiveEdges": _publish_source(
                document, "InactiveEdges", _edge_pair(110, 0, 0)
            ),
            "RollbackEdges": _publish_source(
                document, "RollbackEdges", _edge_pair(130, 0, 0, bezier=True)
            ),
            "PointOnly": _publish_source(
                document, "PointOnly", Part.Vertex(App.Vector(155, 0, 0))
            ),
        }
        stale = _publish_source(
            document, "StaleBlendEdges", _edge_pair(165, 0, 0)
        )
        body, seed = _body_source(
            document, "BodyEdges", _edge_pair(185, 0, 0, bezier=True)
        )
        sources["BodyEdges"] = body
        sources["BodyEdgesSeed"] = seed
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise

    document.openTransaction("Delete stale Blend Curve source")
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
    definition = model_surface_capability_definition()
    surface = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot(
            "model",
            1,
            "b" * 64,
            ("Surface_BlendCurve",),
            (),
            (),
        ),
        available=True,
        unavailable_reason="",
        tool_names=(definition.name,),
        schemas=(definition.provider_schema(("blend_curve",)),),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(surface)


def _endpoint(source, edge: str, **controls):
    return {
        "object_name": source.Name if hasattr(source, "Name") else str(source),
        "edge": edge,
        **controls,
    }


def _arguments(label: str, start, end):
    return {
        "operation": "blend_curve",
        "label": label,
        "definition": {"start": start, "end": end},
    }


def _link_sub(value):
    if not value:
        return None, ()
    target, names = value if isinstance(value, tuple) else (value, ())
    if isinstance(names, str):
        names = (names,) if names else ()
    return target, tuple(str(name) for name in names)


def _target(source):
    return PartGui.resolveModelingObject(source)


def _expected_controls(endpoint):
    return {
        "parameter": float(endpoint.get("parameter", 0.0)),
        "continuity": str(endpoint.get("continuity", "G2")),
        "size": float(endpoint.get("size", 1.0)),
    }


def _resolved_edge(source, edge: str):
    return Part.getShape(
        _target(source),
        edge,
        needSubElement=True,
        transform=True,
    )


def _relative_point(source, edge: str, parameter: float):
    shape = _resolved_edge(source, edge)
    value = shape.FirstParameter + parameter * (
        shape.LastParameter - shape.FirstParameter
    )
    return shape.valueAt(value)


def _assert_endpoints(result, start_source, start, end_source, end) -> None:
    output = result.Shape.Edges[0]
    actual_start = output.valueAt(output.FirstParameter)
    actual_end = output.valueAt(output.LastParameter)
    expected_start = _relative_point(
        start_source, start["edge"], _expected_controls(start)["parameter"]
    )
    expected_end = _relative_point(
        end_source, end["edge"], _expected_controls(end)["parameter"]
    )
    assert actual_start.distanceToPoint(expected_start) <= 1.0e-7
    assert actual_end.distanceToPoint(expected_end) <= 1.0e-7


def _task_button(standard_button):
    _process_events()
    for box in Gui.getMainWindow().findChildren(QtWidgets.QDialogButtonBox):
        if not box.isVisible():
            continue
        parent = box.parentWidget()
        while parent is not None:
            if parent.metaObject().className() == "Gui::TaskView::TaskView":
                break
            parent = parent.parentWidget()
        if parent is None:
            continue
        button = box.button(standard_button)
        if button and button.isVisible() and button.isEnabled():
            return button
    return None


def _assert_human_contract(document, source):
    Gui.Selection.clearSelection()
    _process_events()
    assert not Gui.isCommandActive("Surface_BlendCurve")
    Gui.Selection.addSelection(source, "Edge1")
    _process_events()
    assert not Gui.isCommandActive("Surface_BlendCurve")
    Gui.Selection.addSelection(source, "Edge2")
    _process_events()
    assert Gui.isCommandActive("Surface_BlendCurve")
    before = tuple(obj.Name for obj in document.Objects)
    undo_before = int(document.UndoCount)
    Gui.runCommand("Surface_BlendCurve", 0)
    _process_events(32)
    created = [obj for obj in document.Objects if obj.Name not in before]
    assert len(created) == 1 and created[0].TypeId == "Surface::FeatureBlendCurve"
    result = created[0]
    assert not Gui.Control.activeDialog()
    assert _link_sub(result.StartEdge) == (source, ("Edge1",))
    assert _link_sub(result.EndEdge) == (source, ("Edge2",))
    assert (
        float(result.StartParameter),
        int(result.StartContinuity),
        float(result.StartSize),
        float(result.EndParameter),
        int(result.EndContinuity),
        float(result.EndSize),
    ) == (0.0, 2, 1.0, 0.0, 2, 1.0)
    assert result.isValid() and result.Shape.ShapeType == "Edge"
    assert int(result.Shape.Edges[0].Curve.Degree) == 5
    timeline = document.getObject("VibeCADTimeline")
    assert timeline is not None
    assert list(timeline.Operations).count(result) == 1
    if "VibeCADTimelineRole" in result.PropertiesList:
        assert result.VibeCADTimelineRole == "operation"
    assert source.Visibility and document.UndoCount == undo_before + 1
    _assert_endpoints(
        result,
        source,
        {"edge": "Edge1"},
        source,
        {"edge": "Edge2"},
    )
    result_name = result.Name
    signature = _shape_signature(result.Shape)
    document.undo()
    _process_events()
    assert document.getObject(result_name) is None and source.Visibility
    document.redo()
    _process_events()
    result = document.getObject(result_name)
    assert result is not None
    _assert_signature(_shape_signature(result.Shape), signature)

    gui_document = Gui.getDocument(document.Name)
    assert gui_document.setEdit(result.Name)
    _process_events(24)
    assert Gui.Control.activeDialog(gui_document)
    controls = {
        name: Gui.getMainWindow().findChild(widget_type, name)
        for name, widget_type in (
            ("contFirstEdge", QtWidgets.QComboBox),
            ("contSecondEdge", QtWidgets.QComboBox),
            ("paramFirstEdge", QtWidgets.QDoubleSpinBox),
            ("paramSecondEdge", QtWidgets.QDoubleSpinBox),
            ("sizeFirstEdge", QtWidgets.QDoubleSpinBox),
            ("sizeSecondEdge", QtWidgets.QDoubleSpinBox),
        )
    }
    assert all(controls.values())
    assert controls["contFirstEdge"].currentIndex() == 2
    assert controls["contSecondEdge"].currentIndex() == 2
    controls["contFirstEdge"].setCurrentIndex(1)
    controls["contSecondEdge"].setCurrentIndex(3)
    controls["paramFirstEdge"].setValue(0.25)
    controls["paramSecondEdge"].setValue(0.7)
    controls["sizeFirstEdge"].setValue(1.4)
    controls["sizeSecondEdge"].setValue(0.75)
    _process_events(32)
    ok = _task_button(QtWidgets.QDialogButtonBox.Ok)
    assert ok is not None
    ok.click()
    _process_events(32)
    assert not Gui.Control.activeDialog(gui_document)
    assert (
        float(result.StartParameter),
        int(result.StartContinuity),
        float(result.StartSize),
        float(result.EndParameter),
        int(result.EndContinuity),
        float(result.EndSize),
    ) == (0.25, 1, 1.4, 0.7, 3, 0.75)
    assert result.isValid() and int(result.Shape.Edges[0].Curve.Degree) == 5
    _assert_endpoints(
        result,
        source,
        {"edge": "Edge1", "parameter": 0.25},
        source,
        {"edge": "Edge2", "parameter": 0.7},
    )
    Gui.Selection.clearSelection()
    return result, _shape_signature(result.Shape)


def _assert_result(document, response, arguments, sources):
    assert set(response) == {
        "ok",
        "root",
        "start",
        "end",
        "degree",
        "length_mm",
        "receipt",
        "assistant_undo_available",
    }
    definition = arguments["definition"]
    start = definition["start"]
    end = definition["end"]
    start_controls = _expected_controls(start)
    end_controls = _expected_controls(end)
    start_source = sources[start["object_name"]]
    end_source = sources[end["object_name"]]
    start_target = _target(start_source)
    end_target = _target(end_source)
    result = document.getObject(response["root"]["object_name"])
    assert result is not None and result.TypeId == "Surface::FeatureBlendCurve"
    assert result.Label == arguments["label"]
    assert result.getParentGeoFeatureGroup() is None
    assert result.isValid() and result.Shape.isValid()
    assert result.Shape.ShapeType == "Edge" and len(result.Shape.Edges) == 1
    assert result.VibeCADTimelineRole == "operation"
    assert getattr(result, "VibeCADTimelineOwner", None) is None
    assert str(result.VibeCADDefinitionId) and str(result.DesignId)
    assert "VibeCADTimelineReplacedInputs" not in result.PropertiesList
    assert _link_sub(result.StartEdge) == (start_target, (start["edge"],))
    assert _link_sub(result.EndEdge) == (end_target, (end["edge"],))
    assert response["start"] == {
        "source": response["start"]["source"],
        "edge": start["edge"],
        **start_controls,
    }
    assert response["start"]["source"]["object_name"] == start_target.Name
    assert response["end"] == {
        "source": response["end"]["source"],
        "edge": end["edge"],
        **end_controls,
    }
    assert response["end"]["source"]["object_name"] == end_target.Name
    expected_degree = (
        _CONTINUITIES[start_controls["continuity"]]
        + _CONTINUITIES[end_controls["continuity"]]
        + 1
    )
    assert response["degree"] == expected_degree
    assert int(result.Shape.Edges[0].Curve.Degree) == expected_degree
    assert _close(response["length_mm"], result.Shape.Length)
    assert (
        float(result.StartParameter),
        int(result.StartContinuity),
        float(result.StartSize),
        float(result.EndParameter),
        int(result.EndContinuity),
        float(result.EndSize),
    ) == (
        start_controls["parameter"],
        _CONTINUITIES[start_controls["continuity"]],
        start_controls["size"],
        end_controls["parameter"],
        _CONTINUITIES[end_controls["continuity"]],
        end_controls["size"],
    )
    _assert_endpoints(result, start_source, start, end_source, end)
    assert response["assistant_undo_available"] is True
    assert [item["object_name"] for item in response["receipt"]["created"]] == [
        result.Name
    ]
    assert response["receipt"]["changed"] == []
    assert response["receipt"]["deleted"] == []
    assert response["receipt"]["replaced"] == []
    PartDesign.validateDesign(result)
    return result, start_controls, end_controls


def _record(result, start_controls, end_controls, source_visibility):
    start_target, start_names = _link_sub(result.StartEdge)
    end_target, end_names = _link_sub(result.EndEdge)
    return {
        "name": result.Name,
        "label": str(result.Label),
        "definition_id": str(result.VibeCADDefinitionId),
        "design_id": str(result.DesignId),
        "start": (start_target.Name, start_names),
        "end": (end_target.Name, end_names),
        "start_controls": start_controls,
        "end_controls": end_controls,
        "source_visibility": source_visibility,
        "signature": _shape_signature(result.Shape),
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    save_directory = None
    exit_code = 1
    try:
        Gui.activateWorkbench("SurfaceWorkbench")
        document = App.newDocument("NativeModelSurfaceBlendCurveGate")
        VibeGui._connect_document_observer()
        _process_events()
        sources, stale_name = _create_sources(document)
        human_result, human_controlled_signature = _assert_human_contract(
            document, sources["HumanEdges"]
        )

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-surface-blend-curve-gui")
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
                "model.surface",
                json.dumps(arguments, separators=(",", ":")),
                f"model-surface-blend-call-{call_number}",
            )
            assert response.get("ok") is succeeds, (
                arguments,
                response,
                debug_events[-1] if debug_events else None,
            )
            return response

        controlled = {
            "start": _endpoint(
                sources["HumanEdges"],
                "Edge1",
                parameter=0.25,
                continuity="G1",
                size=1.4,
            ),
            "end": _endpoint(
                sources["HumanEdges"],
                "Edge2",
                parameter=0.7,
                continuity="G3",
                size=0.75,
            ),
        }
        cases = (
            _arguments(
                "Default Blend Curve",
                _endpoint(sources["DefaultEdges"], "Edge1"),
                _endpoint(sources["DefaultEdges"], "Edge2"),
            ),
            _arguments("Human Parity Blend Curve", controlled["start"], controlled["end"]),
            _arguments(
                "Maximum Continuity Blend Curve",
                _endpoint(
                    sources["ControlledEdges"],
                    "Edge1",
                    parameter=0.2,
                    continuity="G4",
                    size=1.2,
                ),
                _endpoint(
                    sources["ControlledEdges"],
                    "Edge2",
                    parameter=0.8,
                    continuity="G4",
                    size=-0.6,
                ),
            ),
            _arguments(
                "Placed Blend Curve",
                _endpoint(
                    sources["PlacedEdges"], "Edge1", parameter=0.4, continuity="C0"
                ),
                _endpoint(
                    sources["PlacedEdges"], "Edge2", parameter=0.6, continuity="G1"
                ),
            ),
            _arguments(
                "Body Blend Curve",
                _endpoint(sources["BodyEdges"], "Edge1", parameter=0.3),
                _endpoint(sources["BodyEdges"], "Edge2", parameter=0.65),
            ),
            _arguments(
                "Hidden Input Blend Curve",
                _endpoint(sources["HiddenEdges"], "Edge1"),
                _endpoint(sources["HiddenEdges"], "Edge2"),
            ),
        )
        records = []
        for arguments in cases:
            names = tuple(
                dict.fromkeys(
                    endpoint["object_name"]
                    for endpoint in arguments["definition"].values()
                )
            )
            source_visibility = {
                name: bool(sources[name].Visibility) for name in names
            }
            source_signatures = {
                name: _shape_signature(Part.getShape(_target(sources[name]), transform=True))
                for name in names
            }
            response = native_call(arguments)
            result, start_controls, end_controls = _assert_result(
                document, response, arguments, sources
            )
            signature = _shape_signature(result.Shape)
            assert signature[5] > 0.0
            for _index in range(4):
                assert document.recompute([result], True, True) is not False
                _assert_signature(_shape_signature(result.Shape), signature)
            for name in names:
                _assert_signature(
                    _shape_signature(Part.getShape(_target(sources[name]), transform=True)),
                    source_signatures[name],
                )
                assert bool(sources[name].Visibility) is source_visibility[name]
            record = _record(
                result, start_controls, end_controls, source_visibility
            )
            document.undo()
            _process_events()
            assert document.getObject(record["name"]) is None
            assert {
                name: bool(sources[name].Visibility) for name in names
            } == source_visibility
            document.redo()
            _process_events()
            result = document.getObject(record["name"])
            assert result is not None
            _assert_signature(_shape_signature(result.Shape), signature)
            PartDesign.validateDesign(result)
            records.append(record)

        _assert_signature(records[1]["signature"], human_controlled_signature)
        assert _target(sources["BodyEdges"]) is sources["BodyEdgesSeed"]
        assert records[-2]["start"][0] == sources["BodyEdgesSeed"].Name
        assert records[-1]["source_visibility"] == {"HiddenEdges": False}

        suppressible = document.getObject(records[0]["name"])
        suppressible.Suppressed = True
        assert document.recompute([suppressible], True, True) is not False
        assert suppressible.Shape.isNull()
        suppressible.Suppressed = False
        assert document.recompute([suppressible], True, True) is not False
        _assert_signature(
            _shape_signature(suppressible.Shape), records[0]["signature"]
        )

        before = tuple(obj.Name for obj in document.Objects)
        invalid_schema = native_call(
            {
                "operation": "blend_curve",
                "definition": {
                    "start": _endpoint(sources["RollbackEdges"], "Edge1"),
                    "end": _endpoint(sources["RollbackEdges"], "Edge2"),
                },
            },
            succeeds=False,
        )
        assert invalid_schema["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before

        failure_cases = (
            (
                _arguments(
                    "Missing Blend Curve",
                    _endpoint(stale_name, "Edge1"),
                    _endpoint(sources["RollbackEdges"], "Edge2"),
                ),
                "NATIVE_TARGET_INVALID",
            ),
            (
                _arguments(
                    "Missing Edge Blend Curve",
                    _endpoint(sources["PointOnly"], "Edge1"),
                    _endpoint(sources["RollbackEdges"], "Edge2"),
                ),
                "NATIVE_MODEL_INVALID",
            ),
            (
                _arguments(
                    "Duplicate Blend Curve",
                    _endpoint(sources["RollbackEdges"], "Edge1"),
                    _endpoint(sources["RollbackEdges"], "Edge1"),
                ),
                "NATIVE_MODEL_INVALID",
            ),
            (
                _arguments(
                    "Bad Parameter Blend Curve",
                    _endpoint(
                        sources["RollbackEdges"], "Edge1", parameter=1.1
                    ),
                    _endpoint(sources["RollbackEdges"], "Edge2"),
                ),
                "NATIVE_ARGUMENTS_INVALID",
            ),
        )
        for arguments, error_code in failure_cases:
            before = tuple(obj.Name for obj in document.Objects)
            response = native_call(arguments, succeeds=False)
            assert response["error_code"] == error_code, response
            assert tuple(obj.Name for obj in document.Objects) == before
            assert not document.HasPendingTransaction

        timeline = document.getObject("VibeCADTimeline")
        timeline_end = int(timeline.Position)
        timeline.Position = 0
        _process_events()
        assert not PartGui.isModelingObjectActive(sources["InactiveEdges"])
        before = tuple(obj.Name for obj in document.Objects)
        inactive = native_call(
            _arguments(
                "Inactive Blend Curve",
                _endpoint(sources["InactiveEdges"], "Edge1"),
                _endpoint(sources["InactiveEdges"], "Edge2"),
            ),
            succeeds=False,
        )
        assert inactive["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        timeline.Position = timeline_end
        _process_events()
        assert PartGui.isModelingObjectActive(sources["InactiveEdges"])

        stale_source = sources["RollbackEdges"]
        stale_definition = {
            "start": _endpoint(stale_source, "Edge1"),
            "end": _endpoint(stale_source, "Edge2"),
        }
        stale_spec = prepare_surface_blend_curve(
            str(document.Uid), stale_definition
        )
        stale_prepared = preflight_surface_blend_curve(document, stale_spec)
        names_before = tuple(obj.Name for obj in document.Objects)
        document.openTransaction("Reject stale Blend Curve")
        try:
            stale_source.Shape = _edge_pair(131, 0, 0, bezier=True)
            try:
                create_surface_blend_curve(
                    document,
                    label="Stale Blend Curve",
                    prepared=stale_prepared,
                )
            except NativeModelError:
                pass
            else:
                raise AssertionError("Changed Blend Curve preflight was accepted")
        finally:
            document.abortTransaction()
        assert tuple(obj.Name for obj in document.Objects) == names_before

        body_source = sources["BodyEdges"]
        body_definition = {
            "start": _endpoint(body_source, "Edge1"),
            "end": _endpoint(body_source, "Edge2"),
        }
        body_spec = prepare_surface_blend_curve(str(document.Uid), body_definition)
        body_prepared = preflight_surface_blend_curve(document, body_spec)
        original_tip = _target(body_source)
        document.openTransaction("Reject changed Blend Curve Body tip")
        try:
            changed_tip = body_source.newObject(
                "PartDesign::Feature", "BodyEdgesChangedTip"
            )
            changed_tip.Shape = _edge_pair(186, 0, 0, bezier=True)
            assert document.recompute([changed_tip, body_source], True, True) is not False
            assert _target(body_source) is changed_tip
            try:
                create_surface_blend_curve(
                    document,
                    label="Stale Body Blend Curve",
                    prepared=body_prepared,
                )
            except NativeModelError:
                pass
            else:
                raise AssertionError("Changed Blend Curve Body tip was accepted")
        finally:
            document.abortTransaction()
        assert _target(body_source) is original_tip

        rollback_arguments = _arguments(
            "Rollback Blend Curve",
            _endpoint(sources["RollbackEdges"], "Edge1"),
            _endpoint(sources["RollbackEdges"], "Edge2"),
        )
        rollback_names = tuple(obj.Name for obj in document.Objects)
        rollback_signature = _shape_signature(
            Part.getShape(sources["RollbackEdges"], transform=True)
        )
        original_verify = runtime_module.verify_surface_blend_curve

        def reject_after_creation(_document, _draft):
            raise NativeModelError("Forced Blend Curve postcondition failure.")

        runtime_module.verify_surface_blend_curve = reject_after_creation
        try:
            rollback = native_call(rollback_arguments, succeeds=False)
        finally:
            runtime_module.verify_surface_blend_curve = original_verify
        assert rollback["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == rollback_names
        assert not document.HasPendingTransaction
        _assert_signature(
            _shape_signature(Part.getShape(sources["RollbackEdges"], transform=True)),
            rollback_signature,
        )

        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-blend-"))
        save_path = save_directory / "ModelSurfaceBlendCurve.FCStd"
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        assert document.recompute() is not False
        _process_events()
        for record in records:
            result = document.getObject(record["name"])
            assert result is not None and result.TypeId == "Surface::FeatureBlendCurve"
            assert result.Label == record["label"]
            assert str(result.VibeCADDefinitionId) == record["definition_id"]
            assert str(result.DesignId) == record["design_id"]
            start_target, start_names = _link_sub(result.StartEdge)
            end_target, end_names = _link_sub(result.EndEdge)
            assert (start_target.Name, start_names) == record["start"]
            assert (end_target.Name, end_names) == record["end"]
            start_controls = record["start_controls"]
            end_controls = record["end_controls"]
            assert (
                float(result.StartParameter),
                int(result.StartContinuity),
                float(result.StartSize),
                float(result.EndParameter),
                int(result.EndContinuity),
                float(result.EndSize),
            ) == (
                start_controls["parameter"],
                _CONTINUITIES[start_controls["continuity"]],
                start_controls["size"],
                end_controls["parameter"],
                _CONTINUITIES[end_controls["continuity"]],
                end_controls["size"],
            )
            _assert_signature(_shape_signature(result.Shape), record["signature"])
            assert {
                name: bool(document.getObject(name).Visibility)
                for name in record["source_visibility"]
            } == record["source_visibility"]
            PartDesign.validateDesign(result)

        print("VIBECAD_NATIVE_MODEL_SURFACE_BLEND_CURVE_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        Gui.Selection.clearSelection()
        if document is not None and document.Name in App.listDocuments():
            gui_document = Gui.getDocument(document.Name)
            if gui_document and Gui.Control.activeDialog(gui_document):
                task = Gui.Control.activeTaskDialog(gui_document)
                if task is not None:
                    try:
                        task.reject()
                    except RuntimeError:
                        pass
                Gui.Control.closeDialog(gui_document)
            App.closeDocument(document.Name)
        if save_directory is not None:
            shutil.rmtree(save_directory, ignore_errors=True)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
