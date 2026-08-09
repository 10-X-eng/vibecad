# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real VibeCAD GUI and provider lifecycle gate for Surface Filling."""

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
from VibeCADNativeSurfaceFilling import (
    create_surface_filling,
    preflight_surface_filling,
    prepare_surface_filling,
)
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger


_DEFAULTS = {
    "degree": 3,
    "points_on_curve": 15,
    "iterations": 2,
    "anisotropy": False,
    "tolerance_2d": 1.0e-5,
    "tolerance_3d": 1.0e-4,
    "angular_tolerance": 0.01,
    "curvature_tolerance": 0.1,
    "maximum_degree": 8,
    "maximum_segments": 9,
}
_CONTINUITY = {"C0": 0, "G1": 1, "G2": 2}


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
        float(shape.Area),
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
    )


def _square_wire(x: float, y: float = 0.0, z: float = 0.0, size: float = 10.0):
    return Part.makePolygon(
        [
            App.Vector(x, y, z),
            App.Vector(x + size, y, z),
            App.Vector(x + size, y + size, z),
            App.Vector(x, y + size, z),
            App.Vector(x, y, z),
        ]
    )


def _bezier(points):
    curve = Part.BezierCurve()
    curve.setPoles([App.Vector(*point) for point in points])
    return curve.toShape()


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
    document.openTransaction("Create Surface Filling gate sources")
    try:
        sources = {
            "HumanBoundary": _publish_source(
                document, "HumanBoundary", Part.Face(_square_wire(0))
            ),
            "BasicBoundary": _publish_source(
                document, "BasicBoundary", _square_wire(20)
            ),
            "GuidedBoundary": _publish_source(
                document, "GuidedBoundary", _square_wire(40)
            ),
            "GuideCurve": _publish_source(
                document,
                "GuideCurve",
                _bezier(((42, 5, 0), (45, 5, 2), (48, 5, 0))),
            ),
            "GuidePoint": _publish_source(
                document, "GuidePoint", Part.Vertex(App.Vector(45, 5, 1.2))
            ),
            "SupportedBoundary": _publish_source(
                document, "SupportedBoundary", Part.Face(_square_wire(60))
            ),
            "PlacedBoundary": _publish_source(
                document,
                "PlacedBoundary",
                _square_wire(0),
                placement=App.Placement(
                    App.Vector(90, -6, 4),
                    App.Rotation(App.Vector(0, 0, 1), 17),
                ),
            ),
            "InactiveBoundary": _publish_source(
                document, "InactiveBoundary", _square_wire(140)
            ),
            "RollbackBoundary": _publish_source(
                document, "RollbackBoundary", _square_wire(160)
            ),
            "HiddenBoundary": _publish_source(
                document,
                "HiddenBoundary",
                _square_wire(175),
                visible=False,
            ),
            "OpenBoundary": _publish_source(
                document,
                "OpenBoundary",
                Part.makeLine(App.Vector(180, 0, 0), App.Vector(190, 0, 0)),
            ),
            "NonAdjacentSupport": _publish_source(
                document,
                "NonAdjacentSupport",
                Part.makeCompound(
                    [Part.Face(_square_wire(200)), Part.Face(_square_wire(220))]
                ),
            ),
        }
        stale = _publish_source(document, "StaleBoundary", _square_wire(240))
        body, seed = _body_source(document, "BodyBoundary", _square_wire(120))
        sources["BodyBoundary"] = body
        sources["BodyBoundarySeed"] = seed
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise

    document.openTransaction("Delete stale Surface Filling source")
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
            "u" * 64,
            ("Surface_Filling",),
            (),
            (),
        ),
        available=True,
        unavailable_reason="",
        tool_names=(definition.name,),
        schemas=(definition.provider_schema(("filling",)),),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(surface)


def _constraint(kind: str, source, subelement: str, **values):
    return {
        "kind": kind,
        "object_name": source.Name if hasattr(source, "Name") else str(source),
        "subelement": subelement,
        **values,
    }


def _boundary(source, **first_values):
    return tuple(
        _constraint(
            "boundary_edge",
            source,
            f"Edge{index}",
            **(first_values if index == 1 else {}),
        )
        for index in range(1, 5)
    )


def _arguments(label: str, constraints, **definition_values):
    return {
        "operation": "filling",
        "label": label,
        "definition": {
            "constraints": list(constraints),
            **definition_values,
        },
    }


def _link_sub(value):
    if not value:
        return None, ()
    target, names = value if isinstance(value, tuple) else (value, ())
    if isinstance(names, str):
        names = (names,) if names else ()
    return target, tuple(str(name) for name in names)


def _flatten_links(value):
    flattened = []
    for group in tuple(value):
        target, names = _link_sub(group)
        for name in names or ("",):
            flattened.append((target, (name,)))
    return tuple(flattened)


def _task_button(role):
    for box in Gui.getMainWindow().findChildren(QtWidgets.QDialogButtonBox):
        button = box.button(role)
        if button is not None and button.isVisible():
            return button
    return None


def _finish_task(role) -> None:
    button = _task_button(role)
    assert button is not None and button.isEnabled()
    button.click()
    _process_events(40)
    assert not Gui.Control.activeDialog()


def _assert_human_contract(document, source) -> None:
    before = tuple(obj.Name for obj in document.Objects)
    Gui.Selection.clearSelection()
    assert Gui.isCommandActive("Surface_Filling")
    Gui.runCommand("Surface_Filling", 0)
    _process_events(32)
    assert Gui.Control.activeDialog()
    window = Gui.getMainWindow()
    boundary_list = window.findChild(QtWidgets.QListWidget, "listBoundary")
    unbound_list = window.findChild(QtWidgets.QListWidget, "listUnbound")
    vertex_list = window.findChild(QtWidgets.QListWidget, "listFreeVertex")
    initial = window.findChild(QtWidgets.QPushButton, "buttonInitFace")
    add_boundary = window.findChild(QtWidgets.QToolButton, "buttonEdgeAdd")
    remove_boundary = window.findChild(QtWidgets.QToolButton, "buttonEdgeRemove")
    add_unbound = window.findChild(QtWidgets.QToolButton, "buttonUnboundEdgeAdd")
    add_vertex = window.findChild(QtWidgets.QToolButton, "buttonVertexAdd")
    boundary_faces = window.findChild(QtWidgets.QComboBox, "comboBoxFaces")
    boundary_continuity = window.findChild(QtWidgets.QComboBox, "comboBoxCont")
    boundary_accept = window.findChild(QtWidgets.QPushButton, "buttonAccept")
    assert all(
        widget is not None
        for widget in (
            boundary_list,
            unbound_list,
            vertex_list,
            initial,
            add_boundary,
            remove_boundary,
            add_unbound,
            add_vertex,
            boundary_faces,
            boundary_continuity,
            boundary_accept,
        )
    )
    assert (initial.text(), add_boundary.text(), remove_boundary.text()) == (
        "Support Surface",
        "Add Edge",
        "Remove Edge",
    )
    assert add_unbound.text() == "Add Edge" and add_vertex.text() == "Add Vertex"
    assert add_boundary.isChecked()
    for index in range(1, 5):
        Gui.Selection.addSelection(source, f"Edge{index}")
        _process_events(12)
    assert boundary_list.count() == 4
    assert unbound_list.count() == 0 and vertex_list.count() == 0
    first_boundary = boundary_list.item(0)
    boundary_list.setCurrentItem(first_boundary)
    boundary_list.itemDoubleClicked.emit(first_boundary)
    _process_events(12)
    assert boundary_faces.findText("Face1") >= 0
    assert boundary_continuity.findText("G1") >= 0
    boundary_faces.setCurrentIndex(boundary_faces.findText("Face1"))
    boundary_continuity.setCurrentIndex(boundary_continuity.findText("G1"))
    boundary_accept.click()
    _process_events(12)
    created = [obj for obj in document.Objects if obj.Name not in before]
    assert len(created) == 1 and created[0].TypeId == "Surface::Filling"
    human_result = created[0]
    assert int(human_result.Degree) == 3
    assert int(human_result.PointsOnCurve) == 15
    assert int(human_result.Iterations) == 2
    assert not bool(human_result.Anisotropy)
    _finish_task(QtWidgets.QDialogButtonBox.Ok)
    assert human_result.isValid() and human_result.Shape.ShapeType == "Face"
    assert _flatten_links(human_result.BoundaryEdges) == tuple(
        (source, (f"Edge{index}",)) for index in range(1, 5)
    )
    human_faces = tuple(human_result.BoundaryFaces)
    human_orders = tuple(int(value) for value in human_result.BoundaryOrder)
    assert human_faces == ("Face1", "", "", ""), human_faces
    assert human_orders == (1, 0, 0, 0), human_orders
    assert source.Visibility
    human_name = human_result.Name
    document.undo()
    _process_events()
    assert document.getObject(human_name) is None and source.Visibility

    before = tuple(obj.Name for obj in document.Objects)
    Gui.runCommand("Surface_Filling", 0)
    _process_events(24)
    assert Gui.Control.activeDialog()
    _finish_task(QtWidgets.QDialogButtonBox.Cancel)
    assert tuple(obj.Name for obj in document.Objects) == before


def _target(source):
    return PartGui.resolveModelingObject(source)


def _expected_links(constraints, sources, kind):
    return tuple(
        (_target(sources[item["object_name"]]), (item["subelement"],))
        for item in constraints
        if item["kind"] == kind
    )


def _assert_result(document, response, arguments, sources):
    assert set(response) == {
        "ok",
        "root",
        "boundary_edge_count",
        "curve_constraint_count",
        "face_constraint_count",
        "point_constraint_count",
        "has_initial_face",
        "degree",
        "maximum_degree",
        "maximum_segments",
        "edge_count",
        "area_mm2",
        "receipt",
        "assistant_undo_available",
    }
    definition = arguments["definition"]
    constraints = tuple(definition["constraints"])
    controls = {**_DEFAULTS, **{key: definition[key] for key in _DEFAULTS if key in definition}}
    result = document.getObject(response["root"]["object_name"])
    assert result is not None and result.TypeId == "Surface::Filling"
    assert result.Label == arguments["label"]
    assert result.getParentGeoFeatureGroup() is None
    assert result.isValid() and result.Shape.isValid()
    assert result.Shape.ShapeType == "Face" and len(result.Shape.Faces) == 1
    assert result.VibeCADTimelineRole == "operation"
    assert getattr(result, "VibeCADTimelineOwner", None) is None
    assert str(result.VibeCADDefinitionId) and str(result.DesignId)
    assert _flatten_links(result.BoundaryEdges) == _expected_links(
        constraints, sources, "boundary_edge"
    )
    boundary = tuple(item for item in constraints if item["kind"] == "boundary_edge")
    assert tuple(result.BoundaryFaces) == tuple(
        item.get("support_face", "") for item in boundary
    )
    assert tuple(int(value) for value in result.BoundaryOrder) == tuple(
        _CONTINUITY[item.get("continuity", "C0")] for item in boundary
    )
    assert _flatten_links(result.UnboundEdges) == _expected_links(
        constraints, sources, "curve_edge"
    )
    curves = tuple(item for item in constraints if item["kind"] == "curve_edge")
    assert tuple(result.UnboundFaces) == tuple(
        item.get("support_face", "") for item in curves
    )
    assert tuple(int(value) for value in result.UnboundOrder) == tuple(
        _CONTINUITY[item.get("continuity", "C0")] for item in curves
    )
    faces = tuple(item for item in constraints if item["kind"] == "face")
    assert _flatten_links(result.FreeFaces) == _expected_links(constraints, sources, "face")
    assert tuple(int(value) for value in result.FreeOrder) == tuple(
        _CONTINUITY[item.get("continuity", "C0")] for item in faces
    )
    assert _flatten_links(result.Points) == _expected_links(constraints, sources, "point")
    initial = definition.get("initial_face")
    assert _link_sub(result.InitialFace) == (
        (None, ())
        if initial is None
        else (_target(sources[initial["object_name"]]), (initial["face"],))
    )
    assert int(result.Degree) == controls["degree"]
    assert int(result.PointsOnCurve) == controls["points_on_curve"]
    assert int(result.Iterations) == controls["iterations"]
    assert bool(result.Anisotropy) is controls["anisotropy"]
    assert int(result.MaximumDegree) == controls["maximum_degree"]
    assert int(result.MaximumSegments) == controls["maximum_segments"]
    for property_name, control_name in (
        ("Tolerance2d", "tolerance_2d"),
        ("Tolerance3d", "tolerance_3d"),
        ("TolAngular", "angular_tolerance"),
        ("TolCurvature", "curvature_tolerance"),
    ):
        assert _close(getattr(result, property_name), controls[control_name], 1.0e-12)
    counts = {
        kind: sum(item["kind"] == kind for item in constraints)
        for kind in ("boundary_edge", "curve_edge", "face", "point")
    }
    assert response["boundary_edge_count"] == counts["boundary_edge"]
    assert response["curve_constraint_count"] == counts["curve_edge"]
    assert response["face_constraint_count"] == counts["face"]
    assert response["point_constraint_count"] == counts["point"]
    assert response["has_initial_face"] is (initial is not None)
    assert response["degree"] == controls["degree"]
    assert response["maximum_degree"] == controls["maximum_degree"]
    assert response["maximum_segments"] == controls["maximum_segments"]
    assert response["edge_count"] == len(result.Shape.Edges)
    assert _close(response["area_mm2"], result.Shape.Area)
    assert response["assistant_undo_available"] is True
    assert [item["object_name"] for item in response["receipt"]["created"]] == [result.Name]
    assert response["receipt"]["changed"] == []
    assert response["receipt"]["deleted"] == []
    assert response["receipt"]["replaced"] == []
    PartDesign.validateDesign(result)
    return result, controls


def _record(result, controls):
    return {
        "name": result.Name,
        "label": str(result.Label),
        "definition_id": str(result.VibeCADDefinitionId),
        "design_id": str(result.DesignId),
        "boundary": tuple((target.Name, names) for target, names in _flatten_links(result.BoundaryEdges)),
        "boundary_faces": tuple(result.BoundaryFaces),
        "boundary_order": tuple(int(value) for value in result.BoundaryOrder),
        "unbound": tuple((target.Name, names) for target, names in _flatten_links(result.UnboundEdges)),
        "unbound_faces": tuple(result.UnboundFaces),
        "unbound_order": tuple(int(value) for value in result.UnboundOrder),
        "free": tuple((target.Name, names) for target, names in _flatten_links(result.FreeFaces)),
        "free_order": tuple(int(value) for value in result.FreeOrder),
        "points": tuple((target.Name, names) for target, names in _flatten_links(result.Points)),
        "initial": (
            None
            if _link_sub(result.InitialFace)[0] is None
            else (_link_sub(result.InitialFace)[0].Name, _link_sub(result.InitialFace)[1])
        ),
        "controls": controls,
        "signature": _shape_signature(result.Shape),
        "source_visibility": {},
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    save_directory = None
    exit_code = 1
    try:
        Gui.activateWorkbench("SurfaceWorkbench")
        document = App.newDocument("NativeModelSurfaceFillingGate")
        VibeGui._connect_document_observer()
        _process_events()
        sources, stale_name = _create_sources(document)
        _assert_human_contract(document, sources["HumanBoundary"])

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-surface-filling-gui")
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
                f"model-surface-filling-call-{call_number}",
            )
            assert response.get("ok") is succeeds, (
                arguments,
                response,
                debug_events[-1] if debug_events else None,
            )
            return response

        cases = (
            _arguments("Default Filling", _boundary(sources["BasicBoundary"])),
            _arguments(
                "Guided Filling",
                (
                    *_boundary(sources["GuidedBoundary"]),
                    _constraint("curve_edge", sources["GuideCurve"], "Edge1"),
                    _constraint("point", sources["GuidePoint"], "Vertex1"),
                ),
                degree=4,
                points_on_curve=18,
                iterations=3,
                anisotropy=True,
                tolerance_2d=0.00002,
                tolerance_3d=0.0002,
                angular_tolerance=0.02,
                curvature_tolerance=0.2,
                maximum_degree=9,
                maximum_segments=12,
            ),
            _arguments(
                "Supported Filling",
                (
                    *_boundary(
                        sources["SupportedBoundary"],
                        support_face="Face1",
                        continuity="G1",
                    ),
                    _constraint(
                        "face",
                        sources["SupportedBoundary"],
                        "Face1",
                        continuity="C0",
                    ),
                ),
                initial_face={
                    "object_name": sources["SupportedBoundary"].Name,
                    "face": "Face1",
                },
            ),
            _arguments("Placed Filling", _boundary(sources["PlacedBoundary"])),
            _arguments("Body Filling", _boundary(sources["BodyBoundary"])),
            _arguments("Hidden Input Filling", _boundary(sources["HiddenBoundary"])),
        )
        records = []
        for arguments in cases:
            source_names = tuple(
                dict.fromkeys(
                    item["object_name"] for item in arguments["definition"]["constraints"]
                )
            )
            source_signatures = {
                name: _shape_signature(Part.getShape(sources[name], transform=True))
                for name in source_names
            }
            source_visibility = {
                name: bool(sources[name].Visibility) for name in source_names
            }
            response = native_call(arguments)
            result, controls = _assert_result(document, response, arguments, sources)
            signature = _shape_signature(result.Shape)
            assert signature[6] > 0.0
            for _index in range(4):
                assert document.recompute([result], True, True) is not False
                _assert_signature(_shape_signature(result.Shape), signature)
            for name, expected in source_signatures.items():
                _assert_signature(
                    _shape_signature(Part.getShape(sources[name], transform=True)),
                    expected,
                )
            record = _record(result, controls)
            record["source_visibility"] = source_visibility
            assert {
                name: bool(sources[name].Visibility) for name in source_names
            } == source_visibility
            document.undo()
            _process_events()
            assert document.getObject(record["name"]) is None
            assert {
                name: bool(sources[name].Visibility) for name in source_names
            } == source_visibility
            document.redo()
            _process_events()
            result = document.getObject(record["name"])
            assert result is not None
            _assert_signature(_shape_signature(result.Shape), signature)
            assert {
                name: bool(sources[name].Visibility) for name in source_names
            } == source_visibility
            PartDesign.validateDesign(result)
            records.append(record)

        body_state = _target(sources["BodyBoundary"])
        body_record = records[-2]
        assert body_state is sources["BodyBoundarySeed"]
        assert all(name == body_state.Name for name, _subs in body_record["boundary"])
        assert sources["BodyBoundary"].Visibility

        placed_signature = records[-3]["signature"]
        assert placed_signature[7] > 80.0 and placed_signature[11] >= 4.0 - 1.0e-7

        failure_cases = (
            (
                {
                    **_arguments("Unknown Field", _boundary(sources["RollbackBoundary"])),
                    "definition": {
                        **_arguments(
                            "Unknown Field", _boundary(sources["RollbackBoundary"])
                        )["definition"],
                        "unknown": True,
                    },
                },
                "NATIVE_ARGUMENTS_INVALID",
            ),
            (_arguments("Missing", _boundary(stale_name)), "NATIVE_TARGET_INVALID"),
            (
                _arguments(
                    "Open",
                    (_constraint("boundary_edge", sources["OpenBoundary"], "Edge1"),),
                ),
                "NATIVE_MODEL_INVALID",
            ),
            (
                _arguments(
                    "Unsupported Continuity",
                    (
                        _constraint(
                            "boundary_edge",
                            sources["RollbackBoundary"],
                            "Edge1",
                            continuity="G1",
                        ),
                    ),
                ),
                "NATIVE_MODEL_INVALID",
            ),
            (
                _arguments(
                    "Wrong Type",
                    (
                        _constraint(
                            "boundary_edge",
                            sources["SupportedBoundary"],
                            "Face1",
                        ),
                    ),
                ),
                "NATIVE_MODEL_INVALID",
            ),
            (
                _arguments(
                    "Nonadjacent Support",
                    (
                        _constraint(
                            "boundary_edge",
                            sources["NonAdjacentSupport"],
                            "Edge1",
                            support_face="Face2",
                            continuity="G1",
                        ),
                    ),
                ),
                "NATIVE_MODEL_INVALID",
            ),
            (
                _arguments(
                    "Resolved Duplicate",
                    (
                        _constraint(
                            "boundary_edge", sources["BodyBoundary"], "Edge1"
                        ),
                        _constraint(
                            "boundary_edge", sources["BodyBoundarySeed"], "Edge1"
                        ),
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
            assert not document.HasPendingTransaction

        timeline = document.getObject("VibeCADTimeline")
        timeline_end = int(timeline.Position)
        timeline.Position = 0
        _process_events()
        assert not PartGui.isModelingObjectActive(sources["InactiveBoundary"])
        before = tuple(obj.Name for obj in document.Objects)
        inactive = native_call(
            _arguments("Inactive", _boundary(sources["InactiveBoundary"])),
            succeeds=False,
        )
        assert inactive["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        timeline.Position = timeline_end
        _process_events()
        assert PartGui.isModelingObjectActive(sources["InactiveBoundary"])

        stale_source = sources["RollbackBoundary"]
        stale_arguments = _arguments("Stale", _boundary(stale_source))
        stale_spec = prepare_surface_filling(
            str(document.Uid), stale_arguments["definition"]
        )
        stale_prepared = preflight_surface_filling(document, stale_spec)
        names_before = tuple(obj.Name for obj in document.Objects)
        document.openTransaction("Reject stale Surface Filling")
        try:
            stale_source.Shape = _square_wire(161)
            try:
                create_surface_filling(
                    document,
                    label="Stale",
                    prepared=stale_prepared,
                )
            except NativeModelError:
                pass
            else:
                raise AssertionError("Changed Surface Filling preflight was accepted")
        finally:
            document.abortTransaction()
        assert tuple(obj.Name for obj in document.Objects) == names_before

        rollback_arguments = _arguments(
            "Rollback Filling", _boundary(sources["RollbackBoundary"])
        )
        rollback_names = tuple(obj.Name for obj in document.Objects)
        original_verify = runtime_module.verify_surface_filling

        def reject_after_creation(_document, _draft):
            raise NativeModelError("Forced exact Surface Filling postcondition failure.")

        runtime_module.verify_surface_filling = reject_after_creation
        try:
            rollback = native_call(rollback_arguments, succeeds=False)
        finally:
            runtime_module.verify_surface_filling = original_verify
        assert rollback["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == rollback_names
        assert sources["RollbackBoundary"].Visibility
        assert not document.HasPendingTransaction

        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-surface-filling-"))
        save_path = save_directory / "ModelSurfaceFilling.FCStd"
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        assert document.recompute() is not False
        _process_events()

        for record in records:
            result = document.getObject(record["name"])
            assert result is not None and result.TypeId == "Surface::Filling"
            assert result.Label == record["label"]
            assert str(result.VibeCADDefinitionId) == record["definition_id"]
            assert str(result.DesignId) == record["design_id"]
            assert tuple((target.Name, names) for target, names in _flatten_links(result.BoundaryEdges)) == record["boundary"]
            assert tuple(result.BoundaryFaces) == record["boundary_faces"]
            assert tuple(int(value) for value in result.BoundaryOrder) == record["boundary_order"]
            assert tuple((target.Name, names) for target, names in _flatten_links(result.UnboundEdges)) == record["unbound"]
            assert tuple(result.UnboundFaces) == record["unbound_faces"]
            assert tuple(int(value) for value in result.UnboundOrder) == record["unbound_order"]
            assert tuple((target.Name, names) for target, names in _flatten_links(result.FreeFaces)) == record["free"]
            assert tuple(int(value) for value in result.FreeOrder) == record["free_order"]
            assert tuple((target.Name, names) for target, names in _flatten_links(result.Points)) == record["points"]
            actual_initial = _link_sub(result.InitialFace)
            assert (
                None
                if actual_initial[0] is None
                else (actual_initial[0].Name, actual_initial[1])
            ) == record["initial"]
            assert int(result.Degree) == record["controls"]["degree"]
            assert int(result.MaximumDegree) == record["controls"]["maximum_degree"]
            assert int(result.MaximumSegments) == record["controls"]["maximum_segments"]
            _assert_signature(_shape_signature(result.Shape), record["signature"])
            PartDesign.validateDesign(result)
            assert {
                name: bool(document.getObject(name).Visibility)
                for name in record["source_visibility"]
            } == record["source_visibility"]

        print("VIBECAD_NATIVE_MODEL_SURFACE_FILLING_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        Gui.Selection.clearSelection()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if save_directory is not None:
            shutil.rmtree(save_directory, ignore_errors=True)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
