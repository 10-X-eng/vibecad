# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real human and Native lifecycle gate for Model Curve on Mesh."""

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
import Mesh
import MeshPart  # noqa: F401 - registers MeshPart::CurveOnMesh
import Part
import PartGui
from PySide import QtCore, QtGui, QtWidgets

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
from VibeCADNativeSurfaceCurveOnMesh import (
    create_surface_curve_on_mesh,
    preflight_surface_curve_on_mesh,
    prepare_surface_curve_on_mesh,
)
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger


def _process_events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _close(left: float, right: float, tolerance: float = 5.0e-6) -> bool:
    return math.isclose(float(left), float(right), rel_tol=1.0e-8, abs_tol=tolerance)


def _vector(value) -> tuple[float, float, float]:
    return float(value.x), float(value.y), float(value.z)


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
    assert actual[:5] == expected[:5], (actual, expected)
    assert all(
        _close(left, right)
        for left, right in zip(actual[5:], expected[5:], strict=True)
    ), (actual, expected)


def _plane_mesh(x: float, y: float = 0.0, size: float = 30.0):
    step = size / 2.0
    facets = []
    for row in range(2):
        for column in range(2):
            x0 = x + column * step
            x1 = x0 + step
            y0 = y + row * step
            y1 = y0 + step
            facets.extend(
                (
                    (
                        App.Vector(x0, y0, 0),
                        App.Vector(x1, y0, 0),
                        App.Vector(x1, y1, 0),
                    ),
                    (
                        App.Vector(x0, y0, 0),
                        App.Vector(x1, y1, 0),
                        App.Vector(x0, y1, 0),
                    ),
                )
            )
    return Mesh.Mesh(facets)


def _publish_mesh(document, name: str, x: float, *, visible: bool):
    source = document.addObject("Mesh::Feature", name)
    source.Label = name
    source.Mesh = _plane_mesh(x)
    source.Visibility = visible
    document.publishProvisionalTimelineOperationBlock(source, (), ())
    assert document.recompute([source], True, True) is not False
    assert PartGui.isModelingObjectActive(source)
    return source


def _publish_part(document, name: str):
    source = document.addObject("Part::Feature", name)
    source.Label = name
    source.Shape = Part.makeBox(10, 10, 10)
    source.Visibility = False
    document.publishProvisionalTimelineOperationBlock(source, (), ())
    assert document.recompute([source], True, True) is not False
    assert PartGui.isModelingObjectActive(source)
    return source


def _create_sources(document):
    document.openTransaction("Create Curve on Mesh gate sources")
    try:
        sources = {
            "HumanMesh": _publish_mesh(document, "HumanMesh", 0, visible=True),
            "DefaultMesh": _publish_mesh(document, "DefaultMesh", 50, visible=False),
            "ClosedMesh": _publish_mesh(document, "ClosedMesh", 100, visible=False),
            "HiddenMesh": _publish_mesh(document, "HiddenMesh", 150, visible=False),
            "InactiveMesh": _publish_mesh(document, "InactiveMesh", 200, visible=False),
            "RollbackMesh": _publish_mesh(document, "RollbackMesh", 250, visible=False),
            "NotMesh": _publish_part(document, "NotMesh"),
        }
        empty = document.addObject("Mesh::Feature", "EmptyMesh")
        empty.Label = "EmptyMesh"
        empty.Mesh = Mesh.Mesh()
        empty.Visibility = False
        document.publishProvisionalTimelineOperationBlock(empty, (), ())
        stale = _publish_mesh(document, "StaleMesh", 300, visible=False)
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    document.openTransaction("Delete stale Curve on Mesh source")
    try:
        stale_name = stale.Name
        document.removeObject(stale_name)
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    assert document.getObject(stale_name) is None
    sources["EmptyMesh"] = empty
    return sources, stale_name


def _turn() -> NativeTurnSnapshot:
    definition = model_surface_capability_definition()
    surface = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot(
            "model",
            1,
            "c" * 64,
            ("Surface_CurveOnMesh",),
            (),
            (),
        ),
        available=True,
        unavailable_reason="",
        tool_names=(definition.name,),
        schemas=(definition.provider_schema(("curve_on_mesh",)),),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(surface)


def _arguments(
    label: str,
    source,
    points,
    *,
    closed=False,
    approximate=True,
    maximum_degree=5,
    continuity="C2",
    tolerance=0.2,
    split_angle_degrees=45.0,
):
    return {
        "operation": "curve_on_mesh",
        "label": label,
        "definition": {
            "object_name": source.Name if hasattr(source, "Name") else str(source),
            "anchors": [
                {
                    "origin_mm": [float(x), float(y), 20.0],
                    "direction": [0.0, 0.0, -1.0],
                }
                for x, y in points
            ],
            "closed": bool(closed),
            "approximate": bool(approximate),
            "maximum_degree": int(maximum_degree),
            "continuity": continuity,
            "tolerance": float(tolerance),
            "split_angle_degrees": float(split_angle_degrees),
        },
    }


def _task_button(role):
    for box in Gui.getMainWindow().findChildren(QtWidgets.QDialogButtonBox):
        if not box.isVisible():
            continue
        button = box.button(role)
        if button is not None and button.isVisible():
            return button
    return None


def _send_mouse(widget, event_type, position, button, buttons) -> None:
    event = QtGui.QMouseEvent(
        event_type,
        position,
        widget.mapToGlobal(position),
        button,
        buttons,
        QtCore.Qt.NoModifier,
    )
    QtWidgets.QApplication.sendEvent(widget, event)


def _viewport_position(view, viewport, point) -> QtCore.QPoint:
    screen_x, screen_y = view.getPointOnScreen(App.Vector(*point))
    _width, height = view.getSize()
    scale = viewport.devicePixelRatioF()
    return QtCore.QPoint(
        int(round(screen_x / scale)),
        int(round((height - screen_y - 1) / scale)),
    )


def _click_mesh_point(view, viewport, source, point) -> None:
    center = _viewport_position(view, viewport, point)
    offsets = (0, -1, 1, -2, 2, -3, 3, -4, 4)
    observed = set()
    for dy in offsets:
        for dx in offsets:
            position = center + QtCore.QPoint(dx, dy)
            if not viewport.rect().contains(position):
                continue
            _send_mouse(
                viewport,
                QtCore.QEvent.MouseMove,
                position,
                QtCore.Qt.NoButton,
                QtCore.Qt.NoButton,
            )
            _process_events(2)
            preselection = Gui.Selection.getPreselection()
            info = view.getObjectInfo((position.x(), position.y())) or {}
            object_name = preselection.ObjectName or str(info.get("Object", ""))
            observed.add(object_name)
            if object_name != source.Name:
                continue
            _send_mouse(
                viewport,
                QtCore.QEvent.MouseButtonPress,
                position,
                QtCore.Qt.LeftButton,
                QtCore.Qt.LeftButton,
            )
            _send_mouse(
                viewport,
                QtCore.QEvent.MouseButtonRelease,
                position,
                QtCore.Qt.LeftButton,
                QtCore.Qt.NoButton,
            )
            _process_events(8)
            return
    raise AssertionError(("Could not preselect mesh point", point, observed))


def _trigger_curve_create(viewport) -> None:
    state = {"attempts": 0}
    loop = QtCore.QEventLoop()

    def trigger_create():
        state["attempts"] += 1
        popup = QtWidgets.QApplication.activePopupWidget()
        labels = {
            candidate.text().replace("&", "")
            for candidate in popup.actions()
        } if isinstance(popup, QtWidgets.QMenu) else set()
        if popup is None or not {"Create", "Clear", "Cancel"}.issubset(labels):
            if state["attempts"] < 100:
                QtCore.QTimer.singleShot(20, trigger_create)
            else:
                state["error"] = "Curve on Mesh context menu did not open"
                loop.quit()
            return
        try:
            action = next(
                (
                    candidate
                    for candidate in popup.actions()
                    if candidate.text().replace("&", "") == "Create"
                ),
                None,
            )
            if action is None:
                state["error"] = "Curve on Mesh context menu omitted Create"
            else:
                action.trigger()
                state["triggered"] = True
        finally:
            popup.close()
            loop.quit()

    position = viewport.rect().center()
    QtCore.QTimer.singleShot(100, trigger_create)
    _send_mouse(
        viewport,
        QtCore.QEvent.MouseButtonPress,
        position,
        QtCore.Qt.RightButton,
        QtCore.Qt.RightButton,
    )
    _send_mouse(
        viewport,
        QtCore.QEvent.MouseButtonRelease,
        position,
        QtCore.Qt.RightButton,
        QtCore.Qt.NoButton,
    )
    QtCore.QTimer.singleShot(3000, loop.quit)
    loop.exec()
    _process_events(24)
    assert state.get("triggered") is True, state


def _human_intersections(result) -> tuple[tuple[float, float], ...]:
    points = []
    mesh = result.Source.Mesh
    for facet_index, weights in zip(
        result.AnchorFacets,
        result.AnchorWeights,
        strict=True,
    ):
        vertices = mesh.Facets[int(facet_index)].Points
        point = App.Vector()
        for vertex, weight in zip(vertices, _vector(weights), strict=True):
            point += App.Vector(*vertex) * weight
        points.append((float(point.x), float(point.y)))
    return tuple(points)


def _assert_human_contract(document, source):
    source_digest = Mesh.geometrySha256(source.Mesh)
    before = tuple(obj.Name for obj in document.Objects)
    before_undo = document.UndoCount
    assert Gui.isCommandActive("Surface_CurveOnMesh")
    Gui.runCommand("Surface_CurveOnMesh", 0)
    _process_events(24)
    assert Gui.Control.activeDialog()
    window = Gui.getMainWindow()
    approximation = window.findChild(QtWidgets.QGroupBox, "groupBox_2")
    tolerance = window.findChild(QtWidgets.QDoubleSpinBox, "meshTolerance")
    continuity = window.findChild(QtWidgets.QComboBox, "continuity")
    maximum_degree = window.findChild(QtWidgets.QComboBox, "maxDegree")
    split_angle = window.findChild(QtWidgets.QWidget, "splitAngle")
    start = window.findChild(QtWidgets.QPushButton, "startButton")
    assert all(
        value is not None
        for value in (
            approximation,
            tolerance,
            continuity,
            maximum_degree,
            split_angle,
            start,
        )
    )
    assert approximation.isChecked()
    assert _close(tolerance.value(), 0.2)
    assert continuity.currentText() == "C2"
    assert maximum_degree.currentText() == "5"
    assert _close(split_angle.property("rawValue"), 45.0)
    approximation.setChecked(False)
    tolerance.setValue(0.05)
    continuity.setCurrentIndex(1)
    maximum_degree.setCurrentIndex(2)
    assert split_angle.setProperty("rawValue", 70.0)
    start.click()
    _process_events(12)

    view = Gui.activeDocument().activeView()
    view.viewTop()
    view.fitAll()
    _process_events(20)
    graphics_view = view.graphicsView()
    viewport = graphics_view.viewport()
    points = ((5.0, 5.0, 0.0), (15.0, 22.0, 0.0), (25.0, 8.0, 0.0))
    for point in points:
        _click_mesh_point(view, viewport, source, point)
    assert source.Document is document
    assert PartGui.isModelingObjectActive(source)
    assert Mesh.geometrySha256(source.Mesh) == source_digest
    _trigger_curve_create(viewport)

    created = [obj for obj in document.Objects if obj.Name not in before]
    curve_results = [obj for obj in created if obj.TypeId == "MeshPart::CurveOnMesh"]
    assert len(curve_results) == 1, [(obj.Name, obj.TypeId) for obj in created]
    result = curve_results[0]
    assert result.Source is source
    assert len(result.AnchorFacets) == 3
    assert len(result.AnchorWeights) == 3
    assert len(result.ProjectionDirections) == 2
    assert result.Closed is False and result.Approximate is False
    assert int(result.MaximumDegree) == 3 and str(result.Continuity) == "C1"
    assert _close(result.Tolerance, 0.05) and _close(result.SplitAngle, 70)
    assert result.isValid() and result.Shape.isValid() and len(result.Shape.Edges) >= 1
    assert result.VibeCADTimelineRole == "operation"
    assert source.Visibility and document.UndoCount == before_undo + 1
    close = _task_button(QtWidgets.QDialogButtonBox.Close)
    if close is None:
        close = _task_button(QtWidgets.QDialogButtonBox.Cancel)
    assert close is not None
    close.click()
    _process_events(20)
    assert not Gui.Control.activeDialog()
    return result, _human_intersections(result)


def _assert_result(document, response, arguments, source):
    assert set(response) == {
        "ok",
        "root",
        "source",
        "anchor_count",
        "closed",
        "approximate",
        "curve_edges",
        "length_mm",
        "continuity",
        "maximum_degree",
        "tolerance",
        "split_angle_degrees",
        "receipt",
        "assistant_undo_available",
    }
    definition = arguments["definition"]
    result = document.getObject(response["root"]["object_name"])
    assert result is not None and result.TypeId == "MeshPart::CurveOnMesh"
    assert result.Label == arguments["label"] and result.Source is source
    assert tuple(result.AnchorFacets) and len(result.AnchorFacets) == len(definition["anchors"])
    assert len(result.AnchorWeights) == len(definition["anchors"])
    expected_connections = len(definition["anchors"]) if definition["closed"] else len(definition["anchors"]) - 1
    assert len(result.ProjectionDirections) == expected_connections
    assert bool(result.Closed) is definition["closed"]
    assert bool(result.Approximate) is definition["approximate"]
    assert int(result.MaximumDegree) == definition["maximum_degree"]
    assert str(result.Continuity) == definition["continuity"]
    assert _close(result.Tolerance, definition["tolerance"])
    assert _close(result.SplitAngle, definition["split_angle_degrees"])
    assert result.isValid() and result.Shape.isValid() and len(result.Shape.Edges) >= 1
    assert result.hasExtension("App::SuppressibleExtension")
    assert result.VibeCADTimelineRole == "operation"
    assert getattr(result, "VibeCADTimelineOwner", None) is None
    assert "VibeCADTimelineReplacedInputs" not in result.PropertiesList
    timeline = document.getObject("VibeCADTimeline")
    assert list(timeline.Operations).count(result) == 1
    assert response["source"]["object_name"] == source.Name
    assert response["anchor_count"] == len(definition["anchors"])
    assert response["closed"] is definition["closed"]
    assert response["approximate"] is definition["approximate"]
    assert response["curve_edges"] == len(result.Shape.Edges)
    assert _close(response["length_mm"], result.Shape.Length)
    assert response["assistant_undo_available"] is True
    assert [value["object_name"] for value in response["receipt"]["created"]] == [
        result.Name
    ]
    assert response["receipt"]["changed"] == []
    assert response["receipt"]["deleted"] == []
    assert response["receipt"]["replaced"] == []
    return result


def _record(result):
    return {
        "name": result.Name,
        "label": str(result.Label),
        "source": result.Source.Name,
        "facets": tuple(int(value) for value in result.AnchorFacets),
        "weights": tuple(_vector(value) for value in result.AnchorWeights),
        "directions": tuple(_vector(value) for value in result.ProjectionDirections),
        "controls": (
            bool(result.Closed),
            bool(result.Approximate),
            int(result.MaximumDegree),
            str(result.Continuity),
            float(result.Tolerance),
            float(result.SplitAngle),
        ),
        "signature": _shape_signature(result.Shape),
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    save_directory = None
    exit_code = 1
    try:
        Gui.activateWorkbench("SurfaceWorkbench")
        document = App.newDocument("NativeModelSurfaceCurveOnMeshGate")
        VibeGui._connect_document_observer()
        _process_events()
        sources, stale_name = _create_sources(document)
        human_result, human_points = _assert_human_contract(document, sources["HumanMesh"])
        human_signature = _shape_signature(human_result.Shape)

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-surface-curve-on-mesh-gui")
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

        def make_dispatcher():
            return NativeTurnDispatcher(
                document=document,
                state=state,
                registry=build_native_capability_registry(),
                turn=turn,
                runtimes=build_native_runtime_bindings(context, turn.tool_names),
                reauthorize_turn=lambda: None,
                active_document=lambda: App.ActiveDocument,
                debug_sink=debug_events.append,
            )

        dispatcher = make_dispatcher()
        call_number = 0

        def native_call(arguments, *, succeeds=True):
            nonlocal call_number
            call_number += 1
            response = dispatcher.call(
                "model.surface",
                json.dumps(arguments, separators=(",", ":")),
                f"model-surface-curve-on-mesh-call-{call_number}",
            )
            assert response.get("ok") is succeeds, (
                arguments,
                response,
                debug_events[-1] if debug_events else None,
            )
            return response

        parity_arguments = _arguments(
            "Native Human-Parity Mesh Curve",
            sources["HumanMesh"],
            human_points,
            approximate=False,
            maximum_degree=3,
            continuity="C1",
            tolerance=0.05,
            split_angle_degrees=70,
        )
        cases = (
            parity_arguments,
            _arguments(
                "Default Mesh Curve",
                sources["DefaultMesh"],
                ((55, 5), (65, 20), (75, 8)),
            ),
            _arguments(
                "Closed Mesh Curve",
                sources["ClosedMesh"],
                ((105, 5), (125, 5), (115, 24)),
                closed=True,
                approximate=False,
                maximum_degree=8,
                continuity="C3",
                tolerance=0.01,
                split_angle_degrees=180,
            ),
            _arguments(
                "Hidden Source Mesh Curve",
                sources["HiddenMesh"],
                ((155, 7), (165, 23), (175, 9)),
                tolerance=0.1,
            ),
        )
        records = []
        for arguments in cases:
            source = sources[arguments["definition"]["object_name"]]
            source_visibility = bool(source.Visibility)
            source_topology = source.Mesh.Topology
            response = native_call(arguments)
            result = _assert_result(document, response, arguments, source)
            signature = _shape_signature(result.Shape)
            for _index in range(3):
                assert document.recompute([result], True, True) is not False
                _assert_signature(_shape_signature(result.Shape), signature)
            assert source.Mesh.Topology == source_topology
            assert bool(source.Visibility) is source_visibility
            record = _record(result)
            records.append(record)

        parity_result = document.getObject(records[0]["name"])
        _assert_signature(_shape_signature(parity_result.Shape), human_signature)
        closed_result = document.getObject(records[2]["name"])
        assert closed_result.Closed and len(closed_result.ProjectionDirections) == 3

        failure_cases = (
            (
                _arguments("Too Few", sources["RollbackMesh"], ((255, 5),)),
                "NATIVE_ARGUMENTS_INVALID",
            ),
            (
                _arguments("Missing", stale_name, ((305, 5), (315, 15))),
                "NATIVE_TARGET_INVALID",
            ),
            (
                _arguments("Wrong Type", sources["NotMesh"], ((1, 1), (2, 2))),
                "NATIVE_TARGET_INVALID",
            ),
            (
                _arguments("Empty", sources["EmptyMesh"], ((1, 1), (2, 2))),
                "NATIVE_MODEL_INVALID",
            ),
            (
                _arguments(
                    "Behind Ray",
                    sources["RollbackMesh"],
                    ((255, 5), (265, 15)),
                )
                | {
                    "definition": {
                        **_arguments(
                            "Behind Ray",
                            sources["RollbackMesh"],
                            ((255, 5), (265, 15)),
                        )["definition"],
                        "anchors": [
                            {"origin_mm": [255, 5, 20], "direction": [0, 0, 1]},
                            {"origin_mm": [265, 15, 20], "direction": [0, 0, 1]},
                        ],
                    }
                },
                "NATIVE_MODEL_INVALID",
            ),
            (
                _arguments(
                    "Miss",
                    sources["RollbackMesh"],
                    ((255, 5), (265, 15)),
                )
                | {
                    "definition": {
                        **_arguments(
                            "Miss",
                            sources["RollbackMesh"],
                            ((255, 5), (265, 15)),
                        )["definition"],
                        "anchors": [
                            {"origin_mm": [500, 5, 20], "direction": [0, 0, -1]},
                            {"origin_mm": [510, 15, 20], "direction": [0, 0, -1]},
                        ],
                    }
                },
                "NATIVE_MODEL_INVALID",
            ),
            (
                _arguments(
                    "Duplicate",
                    sources["RollbackMesh"],
                    ((255, 5), (255, 5)),
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
        dispatcher = make_dispatcher()
        assert not PartGui.isModelingObjectActive(sources["InactiveMesh"])
        before = tuple(obj.Name for obj in document.Objects)
        inactive = native_call(
            _arguments(
                "Inactive",
                sources["InactiveMesh"],
                ((205, 5), (215, 15)),
            ),
            succeeds=False,
        )
        assert inactive["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        timeline.Position = timeline_end
        _process_events()
        dispatcher = make_dispatcher()

        stale_source = sources["RollbackMesh"]
        stale_arguments = _arguments(
            "Stale",
            stale_source,
            ((255, 5), (265, 20), (275, 8)),
        )
        stale_spec = prepare_surface_curve_on_mesh(
            str(document.Uid), stale_arguments["definition"]
        )
        stale_prepared = preflight_surface_curve_on_mesh(document, stale_spec)
        names_before = tuple(obj.Name for obj in document.Objects)
        document.openTransaction("Reject stale Curve on Mesh")
        try:
            stale_source.Mesh = _plane_mesh(251)
            try:
                create_surface_curve_on_mesh(
                    document,
                    label="Stale",
                    prepared=stale_prepared,
                )
            except NativeModelError:
                pass
            else:
                raise AssertionError("Changed Curve on Mesh preflight was accepted")
        finally:
            document.abortTransaction()
        assert tuple(obj.Name for obj in document.Objects) == names_before
        dispatcher = make_dispatcher()

        rollback_arguments = _arguments(
            "Rollback Mesh Curve",
            sources["RollbackMesh"],
            ((255, 5), (265, 20), (275, 8)),
        )
        rollback_names = tuple(obj.Name for obj in document.Objects)
        original_verify = runtime_module.verify_surface_curve_on_mesh

        def reject_after_creation(_document, _draft):
            raise NativeModelError("Forced Curve on Mesh postcondition failure.")

        runtime_module.verify_surface_curve_on_mesh = reject_after_creation
        try:
            rollback = native_call(rollback_arguments, succeeds=False)
        finally:
            runtime_module.verify_surface_curve_on_mesh = original_verify
        assert rollback["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == rollback_names
        assert not document.HasPendingTransaction

        latest_record = records[-1]
        latest_source = sources[latest_record["source"]]
        latest_visibility = bool(latest_source.Visibility)
        document.undo()
        _process_events()
        assert document.getObject(latest_record["name"]) is None
        assert bool(latest_source.Visibility) is latest_visibility
        document.redo()
        _process_events()
        restored = document.getObject(latest_record["name"])
        assert restored is not None
        _assert_signature(_shape_signature(restored.Shape), latest_record["signature"])

        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-curve-mesh-"))
        save_path = save_directory / "ModelSurfaceCurveOnMesh.FCStd"
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        assert document.recompute() is not False
        _process_events()
        for record in records:
            result = document.getObject(record["name"])
            assert result is not None and result.TypeId == "MeshPart::CurveOnMesh"
            assert result.Label == record["label"]
            assert result.Source.Name == record["source"]
            assert tuple(int(value) for value in result.AnchorFacets) == record["facets"]
            assert all(
                all(_close(a, b) for a, b in zip(actual, expected, strict=True))
                for actual, expected in zip(
                    (_vector(value) for value in result.AnchorWeights),
                    record["weights"],
                    strict=True,
                )
            )
            assert all(
                all(_close(a, b) for a, b in zip(actual, expected, strict=True))
                for actual, expected in zip(
                    (_vector(value) for value in result.ProjectionDirections),
                    record["directions"],
                    strict=True,
                )
            )
            assert (
                bool(result.Closed),
                bool(result.Approximate),
                int(result.MaximumDegree),
                str(result.Continuity),
                float(result.Tolerance),
                float(result.SplitAngle),
            ) == record["controls"]
            _assert_signature(_shape_signature(result.Shape), record["signature"])
            assert result.VibeCADTimelineRole == "operation"

        print("VIBECAD_NATIVE_MODEL_SURFACE_CURVE_ON_MESH_GUI_OK", flush=True)
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
