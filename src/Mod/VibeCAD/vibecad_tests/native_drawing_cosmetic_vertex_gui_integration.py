# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for both Drawing cosmetic-vertex actions."""

from __future__ import annotations

import itertools
import json
import math
from pathlib import Path
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets
import TechDrawGui

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeDrawingCosmeticVertexState import (
    drawing_cosmetic_vertex_inventory_state,
)
from VibeCADNativeDrawingDimensionSupport import drawing_selection_state
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state
from VibeCADNativeDrawingSnapshot import build_drawing_snapshot
from VibeCADNativeDrawingState import drawing_page_state
import VibeCADNativeDrawingCosmeticVertexRuntime as VertexRuntimeModule
from VibeCADNativeDrawingCosmeticVertexSchema import (
    DRAWING_COSMETIC_VERTEX_CAPABILITY_NAME,
    DRAWING_COSMETIC_VERTEX_OPERATIONS,
)
from VibeCADNativeDrawingViewState import drawing_view_state
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


def _events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _surface():
    Gui.activateWorkbench("TechDrawWorkbench")
    _events(24)
    controller = Gui.getMainWindow().findChild(
        QtCore.QObject,
        "VibeCADRibbonController",
    )
    assert controller is not None
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "drawing"
    return controller, surface


def _create_fixture(document):
    document.openTransaction("Create Drawing cosmetic-vertex fixture")
    transaction = int(document.getBookedTransactionID())
    try:
        source = document.addObject("Part::Feature", "VertexSource")
        source.Label = "Cosmetic Vertex Source"
        horizontal = Part.makeLine(
            App.Vector(-25.0, 0.0, 0.0),
            App.Vector(25.0, 0.0, 0.0),
        )
        vertical = Part.makeLine(
            App.Vector(0.0, -20.0, 0.0),
            App.Vector(0.0, 20.0, 0.0),
        )
        separate = Part.makeLine(
            App.Vector(-25.0, 30.0, 0.0),
            App.Vector(25.0, 30.0, 0.0),
        )
        circle = Part.makeCircle(7.0, App.Vector(38.0, 22.0, 0.0))
        source.Shape = Part.makeCompound([horizontal, vertical, separate, circle])
        document.publishProvisionalTimelineOperationBlock(source, (), ())

        page = document.addObject("TechDraw::DrawPage", "VertexPage")
        page.Label = "Cosmetic Vertex Page"
        template = document.addObject("TechDraw::DrawSVGTemplate", "VertexTemplate")
        template.Template = str(
            Path(App.getResourceDir())
            / "Mod"
            / "TechDraw"
            / "Templates"
            / "ISO"
            / "A4_Landscape_TD.svg"
        )
        page.Template = template
        document.publishProvisionalTimelineOperationBlock(page, (template,), ())

        view = document.addObject("TechDraw::DrawViewPart", "VertexView")
        view.Label = "Cosmetic Vertex View"
        view.Source = [source]
        view.Direction = App.Vector(0.0, 0.0, 1.0)
        view.XDirection = App.Vector(1.0, 0.0, 0.0)
        view.ScaleType = "Custom"
        view.Scale = 1.4
        view.Rotation = 11.0
        view.X = 112.0
        view.Y = 76.0
        document.publishProvisionalTimelineOperationBlock(view, (), ())
        assert int(page.addView(view)) >= 1
        assert document.recompute([source, view, page], True, True) is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    page.ViewObject.show()
    _events(24)
    assert document.recompute([view, page], True, True) is not False

    projection = drawing_projected_geometry_state(view)
    edges = tuple(
        item for item in projection["elements"] if item["element_type"] == "edge"
    )
    vertices = tuple(
        item for item in projection["elements"] if item["element_type"] == "vertex"
    )
    assert len(edges) == 4, projection
    assert len(vertices) >= 6, projection
    intersecting = None
    nonintersecting = None
    for pair in itertools.combinations((item["name"] for item in edges), 2):
        try:
            plan = TechDrawGui.validateDrawingVertexIntersections(view, list(pair))
        except Exception:
            if nonintersecting is None:
                nonintersecting = pair
            continue
        assert plan["vertices"]
        if intersecting is None:
            intersecting = pair
    assert intersecting is not None
    assert nonintersecting is not None
    quadrant_edge = next(
        item["name"]
        for item in edges
        if "circle" in item["geometry_type"].casefold()
    )
    return (
        source,
        page,
        view,
        intersecting,
        nonintersecting,
        vertices[0]["name"],
        quadrant_edge,
    )


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(DRAWING_COSMETIC_VERTEX_CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(DRAWING_COSMETIC_VERTEX_OPERATIONS)
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "selection" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 14 * 1024
    branches = schema["parameters"]["oneOf"]
    assert [branch["properties"]["operation"]["const"] for branch in branches] == list(
        DRAWING_COSMETIC_VERTEX_OPERATIONS
    )
    assert all(branch["additionalProperties"] is False for branch in branches)
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(DRAWING_COSMETIC_VERTEX_CAPABILITY_NAME,),
            schemas=(schema,),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _base_arguments(page, view, operation) -> dict:
    page_state = drawing_page_state(page)
    view_state = drawing_view_state(view)
    projection = drawing_projected_geometry_state(view)
    return {
        "operation": operation,
        "page": {
            "object_name": page.Name,
            "expected_state_sha256": page_state["state_sha256"],
        },
        "view": {
            "object_name": view.Name,
            "expected_state_sha256": view_state["state_sha256"],
            "expected_projection_state_sha256": projection["projection_state_sha256"],
        },
    }


def _intersection_arguments(page, view, names) -> dict:
    result = _base_arguments(page, view, "create_intersections")
    by_name = {
        item["name"]: item
        for item in drawing_projected_geometry_state(view)["elements"]
    }
    result["edges"] = [
        {
            "subelement": name,
            "expected_element_state_sha256": by_name[name]["element_state_sha256"],
        }
        for name in names
    ]
    return result


def _offset_arguments(page, view, name, offset) -> dict:
    result = _base_arguments(page, view, "create_offset")
    by_name = {
        item["name"]: item
        for item in drawing_projected_geometry_state(view)["elements"]
    }
    result.update(
        {
            "source_vertex": {
                "subelement": name,
                "expected_element_state_sha256": by_name[name]["element_state_sha256"],
            },
            "offset_mm": {"x_mm": offset[0], "y_mm": offset[1]},
        }
    )
    return result


def _point_arguments(page, view, point) -> dict:
    result = _base_arguments(page, view, "create_point")
    result["point_in_view_mm"] = {"x_mm": point[0], "y_mm": point[1]}
    return result


def _midpoint_arguments(page, view, names) -> dict:
    result = _base_arguments(page, view, "create_midpoints")
    by_name = {
        item["name"]: item
        for item in drawing_projected_geometry_state(view)["elements"]
    }
    result["edges"] = [
        {
            "subelement": name,
            "expected_element_state_sha256": by_name[name][
                "element_state_sha256"
            ],
        }
        for name in names
    ]
    return result


def _quadrant_arguments(page, view, names) -> dict:
    result = _midpoint_arguments(page, view, names)
    result["operation"] = "create_quadrants"
    return result


def _selection():
    return tuple(
        (item.Object.Name, tuple(item.SubElementNames))
        for item in Gui.Selection.getSelectionEx()
    )


def _signature(vertices):
    return sorted(
        json.dumps(
            {
                "point": item["point_in_view_mm"],
                "format": item["vertex_format"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        for item in vertices
    )


def _points_close(left, right) -> bool:
    return math.isclose(
        float(left["x_mm"]),
        float(right["x_mm"]),
        rel_tol=1.0e-10,
        abs_tol=1.0e-8,
    ) and math.isclose(
        float(left["y_mm"]),
        float(right["y_mm"]),
        rel_tol=1.0e-10,
        abs_tol=1.0e-8,
    )


def _human_intersection_oracle(document, view, names):
    before = drawing_cosmetic_vertex_inventory_state(view)
    Gui.Selection.clearSelection()
    for name in names:
        Gui.Selection.addSelection(view, name)
    Gui.runCommand("TechDraw_ExtensionVertexAtIntersection")
    _events(20)
    assert not Gui.Control.activeDialog()
    assert not Gui.Selection.getSelectionEx()
    after = drawing_cosmetic_vertex_inventory_state(view)
    old_tags = {item["tag"] for item in before["vertices"]}
    created = [item for item in after["vertices"] if item["tag"] not in old_tags]
    assert created
    document.undo()
    _events(16)
    assert (
        drawing_cosmetic_vertex_inventory_state(view)["inventory_state_sha256"]
        == before["inventory_state_sha256"]
    )
    return _signature(created)


def _set_offset_values(x_value: float, y_value: float) -> None:
    window = Gui.getMainWindow()
    x_box = window.findChild(QtWidgets.QDoubleSpinBox, "dSpinBoxX")
    y_box = window.findChild(QtWidgets.QDoubleSpinBox, "dSpinBoxY")
    assert x_box is not None and y_box is not None
    x_box.setValue(x_value)
    y_box.setValue(y_value)
    _events(12)


def _open_offset_task(view, vertex_name) -> None:
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(view, vertex_name)
    Gui.runCommand("TechDraw_CommandAddOffsetVertex")
    _events(16)
    assert Gui.Control.activeDialog()
    assert Gui.Control.activeTaskDialog() is not None
    assert int(view.Document.getBookedTransactionID()) != 0


def _human_offset_oracle(document, view, vertex_name, offset):
    before = drawing_cosmetic_vertex_inventory_state(view)

    _open_offset_task(view, vertex_name)
    _set_offset_values(-1.5, 2.25)
    preview = drawing_cosmetic_vertex_inventory_state(view)
    assert preview["vertex_count"] == before["vertex_count"] + 1
    Gui.Control.activeTaskDialog().reject()
    _events(20)
    assert not Gui.Control.activeDialog()
    assert int(document.getBookedTransactionID()) == 0
    assert (
        drawing_cosmetic_vertex_inventory_state(view)["inventory_state_sha256"]
        == before["inventory_state_sha256"]
    )

    _open_offset_task(view, vertex_name)
    Gui.Control.activeTaskDialog().accept()
    _events(20)
    assert not Gui.Control.activeDialog()
    zero = drawing_cosmetic_vertex_inventory_state(view)
    assert zero["vertex_count"] == before["vertex_count"] + 1
    zero_created = zero["vertices"][-1]
    source = next(
        item
        for item in drawing_projected_geometry_state(view)["elements"]
        if item["name"] == vertex_name
    )
    expected_point = TechDrawGui.validateDrawingOffsetVertex(
        view, vertex_name, 0.0, 0.0
    )["source_point_in_view_mm"]
    assert _points_close(zero_created["point_in_view_mm"], expected_point)
    assert source["element_type"] == "vertex"
    document.undo()
    _events(16)
    assert (
        drawing_cosmetic_vertex_inventory_state(view)["inventory_state_sha256"]
        == before["inventory_state_sha256"]
    )

    _open_offset_task(view, vertex_name)
    _set_offset_values(*offset)
    Gui.Control.activeTaskDialog().accept()
    _events(20)
    assert not Gui.Control.activeDialog()
    assert int(document.getBookedTransactionID()) == 0
    after = drawing_cosmetic_vertex_inventory_state(view)
    old_tags = {item["tag"] for item in before["vertices"]}
    created = [item for item in after["vertices"] if item["tag"] not in old_tags]
    assert len(created) == 1
    document.undo()
    _events(16)
    assert (
        drawing_cosmetic_vertex_inventory_state(view)["inventory_state_sha256"]
        == before["inventory_state_sha256"]
    )
    return _signature(created)


def _set_direct_point(point) -> None:
    window = Gui.getMainWindow()
    x_box = window.findChild(QtWidgets.QAbstractSpinBox, "dsbX")
    y_box = window.findChild(QtWidgets.QAbstractSpinBox, "dsbY")
    assert x_box is not None and y_box is not None
    assert x_box.setProperty("rawValue", point[0])
    assert y_box.setProperty("rawValue", point[1])
    _events(8)
    assert math.isclose(float(x_box.property("rawValue")), point[0])
    assert math.isclose(float(y_box.property("rawValue")), point[1])


def _open_direct_point_task(view) -> None:
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(view)
    Gui.runCommand("TechDraw_CosmeticVertex")
    _events(16)
    assert Gui.Control.activeDialog()
    assert Gui.Control.activeTaskDialog() is not None
    assert int(view.Document.getBookedTransactionID()) == 0


def _human_direct_point_oracle(document, view, point):
    before = drawing_cosmetic_vertex_inventory_state(view)

    _open_direct_point_task(view)
    _set_direct_point((point[0] + 1.0, point[1] - 1.0))
    Gui.Control.activeTaskDialog().reject()
    _events(20)
    assert not Gui.Control.activeDialog()
    assert int(document.getBookedTransactionID()) == 0
    assert (
        drawing_cosmetic_vertex_inventory_state(view)["inventory_state_sha256"]
        == before["inventory_state_sha256"]
    )

    _open_direct_point_task(view)
    _set_direct_point(point)
    Gui.Control.activeTaskDialog().accept()
    _events(20)
    assert not Gui.Control.activeDialog()
    assert int(document.getBookedTransactionID()) == 0
    after = drawing_cosmetic_vertex_inventory_state(view)
    old_tags = {item["tag"] for item in before["vertices"]}
    created = [item for item in after["vertices"] if item["tag"] not in old_tags]
    assert len(created) == 1
    assert _points_close(
        created[0]["point_in_view_mm"],
        {"x_mm": point[0], "y_mm": point[1]},
    )
    document.undo()
    _events(16)
    assert (
        drawing_cosmetic_vertex_inventory_state(view)["inventory_state_sha256"]
        == before["inventory_state_sha256"]
    )
    return _signature(created)


def _human_midpoint_oracle(document, view, names):
    before = drawing_cosmetic_vertex_inventory_state(view)
    Gui.Selection.clearSelection()
    for name in names:
        Gui.Selection.addSelection(view, name)
    Gui.runCommand("TechDraw_Midpoints")
    _events(20)
    assert not Gui.Control.activeDialog()
    assert not Gui.Selection.getSelectionEx()
    assert int(document.getBookedTransactionID()) == 0
    after = drawing_cosmetic_vertex_inventory_state(view)
    old_tags = {item["tag"] for item in before["vertices"]}
    created = [item for item in after["vertices"] if item["tag"] not in old_tags]
    assert len(created) == len(names)
    document.undo()
    _events(16)
    assert (
        drawing_cosmetic_vertex_inventory_state(view)["inventory_state_sha256"]
        == before["inventory_state_sha256"]
    )
    return _signature(created)


def _human_quadrant_oracle(document, view, names):
    before = drawing_cosmetic_vertex_inventory_state(view)
    Gui.Selection.clearSelection()
    for name in names:
        Gui.Selection.addSelection(view, name)
    Gui.runCommand("TechDraw_Quadrants")
    _events(20)
    assert not Gui.Control.activeDialog()
    assert not Gui.Selection.getSelectionEx()
    after = drawing_cosmetic_vertex_inventory_state(view)
    old_tags = {item["tag"] for item in before["vertices"]}
    created = [item for item in after["vertices"] if item["tag"] not in old_tags]
    assert len(created) == len(names) * 3
    document.undo()
    _events(16)
    assert (
        drawing_cosmetic_vertex_inventory_state(view)["inventory_state_sha256"]
        == before["inventory_state_sha256"]
    )
    return _signature(created)


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-drawing-cosmetic-vertex-"
        )
        save_path = Path(temporary.name) / "cosmetic-vertices.FCStd"
        controller, surface = _surface()
        action_plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        expected_actions = {
            "TechDraw_ExtensionVertexAtIntersection": (
                "create_intersections",
                "ExactDrawingIntersectingEdgesAndDerivedCosmeticVertices",
            ),
            "TechDraw_CommandAddOffsetVertex": (
                "create_offset",
                "ExactDrawingProjectedVertexAndExplicitOffset",
            ),
            "TechDraw_CosmeticVertex": (
                "create_point",
                "ExactDrawingViewAndExplicitCosmeticVertexPoint",
            ),
            "TechDraw_Midpoints": (
                "create_midpoints",
                "ExactDrawingEdgesAndDerivedMidpointVertices",
            ),
            "TechDraw_Quadrants": (
                "create_quadrants",
                "ExactDrawingEdgesAndDerivedQuadrantVertices",
            ),
        }
        for action_id, (operation, target_type) in expected_actions.items():
            action = action_plans[action_id]
            assert (
                action.capability_family,
                action.operation_variant,
                action.exact_target_type,
                action.transaction_behavior,
                action.background_required,
            ) == (
                DRAWING_COSMETIC_VERTEX_CAPABILITY_NAME,
                operation,
                target_type,
                "document",
                False,
            )

        document = App.newDocument("NativeDrawingCosmeticVertexGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        (
            source,
            page,
            view,
            intersecting,
            nonintersecting,
            vertex_name,
            quadrant_edge,
        ) = _create_fixture(document)
        objects_before = tuple(document.Objects)
        views_before = tuple(page.Views)
        history_before = tuple(document.VibeCADTimeline.Operations)
        visibility_before = (
            bool(source.ViewObject.Visibility),
            bool(view.ViewObject.Visibility),
            bool(page.ViewObject.Visibility),
        )
        offset = (4.25, -3.5)
        point = (-7.25, 9.5)
        midpoint_edges = tuple(
            item["name"]
            for item in drawing_projected_geometry_state(view)["elements"]
            if item["element_type"] == "edge"
        )[:2]
        assert len(midpoint_edges) == 2
        human_intersection = _human_intersection_oracle(document, view, intersecting)
        human_offset = _human_offset_oracle(document, view, vertex_name, offset)
        human_point = _human_direct_point_oracle(document, view, point)
        human_midpoints = _human_midpoint_oracle(document, view, midpoint_edges)
        human_quadrants = _human_quadrant_oracle(
            document, view, (quadrant_edge,)
        )
        assert drawing_cosmetic_vertex_inventory_state(view)["vertex_count"] == 0

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-drawing-cosmetic-vertex-gui")

        def reauthorize() -> None:
            require_frozen_native_surface(frozen, controller)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state_store,
            undo_ledger=ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(controller).surface_id,
            edit_or_task_active=lambda: bool(Gui.Control.activeDialog()),
        )
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state_store,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(view, vertex_name)
        selection_before = _selection()
        snapshot = build_drawing_snapshot(
            document,
            selection=drawing_selection_state(document),
        )
        selected = snapshot["selected_projected_geometry"]
        assert len(selected) == 1
        assert selected[0]["selected_elements"][0]["name"] == vertex_name

        intersection_arguments = _intersection_arguments(page, view, intersecting)
        revision_before = state_store.current_revision(str(document.Uid))
        intersection_response = dispatcher.call(
            DRAWING_COSMETIC_VERTEX_CAPABILITY_NAME,
            json.dumps(intersection_arguments),
            "native-drawing-cosmetic-vertex-intersections",
        )
        assert intersection_response["ok"] is True, intersection_response
        assert intersection_response["operation"] == "create_intersections"
        intersection_result = intersection_response["cosmetic_vertices"]
        assert _signature(intersection_result["vertices"]) == human_intersection
        assert intersection_result["created_vertex_count"] >= 1
        assert state_store.current_revision(str(document.Uid)) == revision_before + 1
        assert _selection() == selection_before
        assert not Gui.Control.activeDialog()
        repeated = dispatcher.call(
            DRAWING_COSMETIC_VERTEX_CAPABILITY_NAME,
            json.dumps(intersection_arguments),
            "native-drawing-cosmetic-vertex-intersections",
        )
        assert repeated == intersection_response

        offset_arguments = _offset_arguments(page, view, vertex_name, offset)
        offset_revision = state_store.current_revision(str(document.Uid))
        offset_response = dispatcher.call(
            DRAWING_COSMETIC_VERTEX_CAPABILITY_NAME,
            json.dumps(offset_arguments),
            "native-drawing-cosmetic-vertex-offset",
        )
        assert offset_response["ok"] is True, offset_response
        assert offset_response["operation"] == "create_offset"
        offset_result = offset_response["cosmetic_vertices"]
        assert _signature([offset_result["vertex"]]) == human_offset
        assert offset_result["offset_mm"] == {"x_mm": offset[0], "y_mm": offset[1]}
        assert state_store.current_revision(str(document.Uid)) == offset_revision + 1
        assert _selection() == selection_before
        assert not Gui.Control.activeDialog()
        assert len(json.dumps(offset_response, separators=(",", ":")).encode()) < 4096

        zero_arguments = _offset_arguments(page, view, vertex_name, (0.0, 0.0))
        zero_revision = state_store.current_revision(str(document.Uid))
        zero_response = dispatcher.call(
            DRAWING_COSMETIC_VERTEX_CAPABILITY_NAME,
            json.dumps(zero_arguments),
            "native-drawing-cosmetic-vertex-zero-offset",
        )
        assert zero_response["ok"] is True, zero_response
        zero_result = zero_response["cosmetic_vertices"]
        assert zero_result["offset_mm"] == {"x_mm": 0.0, "y_mm": 0.0}
        assert (
            zero_result["vertex"]["point_in_view_mm"]
            == zero_result["source"]["point_in_view_mm"]
        )
        assert state_store.current_revision(str(document.Uid)) == zero_revision + 1
        assert _selection() == selection_before
        assert not Gui.Control.activeDialog()

        point_arguments = _point_arguments(page, view, point)
        point_revision = state_store.current_revision(str(document.Uid))
        point_response = dispatcher.call(
            DRAWING_COSMETIC_VERTEX_CAPABILITY_NAME,
            json.dumps(point_arguments),
            "native-drawing-cosmetic-vertex-point",
        )
        assert point_response["ok"] is True, point_response
        assert point_response["operation"] == "create_point"
        point_result = point_response["cosmetic_vertices"]
        assert _signature([point_result["vertex"]]) == human_point
        assert point_result["coordinate_space"] == "drawing_view_unscaled_mm"
        assert point_result["axis_convention"] == "x_right_y_up"
        assert state_store.current_revision(str(document.Uid)) == point_revision + 1
        assert _selection() == selection_before
        assert not Gui.Control.activeDialog()
        assert len(json.dumps(point_response, separators=(",", ":")).encode()) < 4096

        midpoint_arguments = _midpoint_arguments(page, view, midpoint_edges)
        midpoint_revision = state_store.current_revision(str(document.Uid))
        midpoint_response = dispatcher.call(
            DRAWING_COSMETIC_VERTEX_CAPABILITY_NAME,
            json.dumps(midpoint_arguments),
            "native-drawing-cosmetic-vertex-midpoints",
        )
        assert midpoint_response["ok"] is True, midpoint_response
        assert midpoint_response["operation"] == "create_midpoints"
        midpoint_result = midpoint_response["cosmetic_vertices"]
        assert midpoint_result["midpoint_count"] == len(midpoint_edges)
        assert _signature(
            [item["vertex"] for item in midpoint_result["midpoints"]]
        ) == human_midpoints
        assert [
            item["source"]["subelement"] for item in midpoint_result["midpoints"]
        ] == list(midpoint_edges)
        assert state_store.current_revision(str(document.Uid)) == midpoint_revision + 1
        assert _selection() == selection_before
        assert not Gui.Control.activeDialog()
        assert (
            len(json.dumps(midpoint_response, separators=(",", ":")).encode())
            < 8192
        )

        quadrant_arguments = _quadrant_arguments(page, view, (quadrant_edge,))
        quadrant_revision = state_store.current_revision(str(document.Uid))
        quadrant_response = dispatcher.call(
            DRAWING_COSMETIC_VERTEX_CAPABILITY_NAME,
            json.dumps(quadrant_arguments),
            "native-drawing-cosmetic-vertex-quadrants",
        )
        assert quadrant_response["ok"] is True, quadrant_response
        assert quadrant_response["operation"] == "create_quadrants"
        quadrant_result = quadrant_response["cosmetic_vertices"]
        assert quadrant_result["source_count"] == 1
        assert quadrant_result["created_vertex_count"] == 3
        assert _signature(
            quadrant_result["sources"][0]["vertices"]
        ) == human_quadrants
        assert (
            quadrant_result["sources"][0]["source"]["subelement"]
            == quadrant_edge
        )
        assert state_store.current_revision(str(document.Uid)) == quadrant_revision + 1
        assert _selection() == selection_before
        assert not Gui.Control.activeDialog()

        inventory = drawing_cosmetic_vertex_inventory_state(view)
        expected_count = (
            intersection_result["created_vertex_count"]
            + 3
            + len(midpoint_edges)
            + 3
        )
        assert inventory["vertex_count"] == expected_count
        all_tags = {item["tag"] for item in inventory["vertices"]}

        refusal_revision = state_store.current_revision(str(document.Uid))
        refusal_undo = int(document.UndoCount)
        stale = dispatcher.call(
            DRAWING_COSMETIC_VERTEX_CAPABILITY_NAME,
            json.dumps(intersection_arguments),
            "native-drawing-cosmetic-vertex-stale",
        )
        assert stale["ok"] is False
        assert stale["error_code"] in {
            "NATIVE_DRAWING_COSMETIC_VERTEX_VIEW_STALE",
            "NATIVE_DRAWING_COSMETIC_VERTEX_PROJECTION_STALE",
        }
        nonintersection = dispatcher.call(
            DRAWING_COSMETIC_VERTEX_CAPABILITY_NAME,
            json.dumps(_intersection_arguments(page, view, nonintersecting)),
            "native-drawing-cosmetic-vertex-no-intersection",
        )
        assert nonintersection["ok"] is False
        assert (
            nonintersection["error_code"]
            == "NATIVE_DRAWING_COSMETIC_VERTEX_REFERENCES_INVALID"
        )
        wrong_type_arguments = _offset_arguments(page, view, vertex_name, (0.0, 0.0))
        wrong_type_arguments["source_vertex"]["subelement"] = intersecting[0]
        wrong_type = dispatcher.call(
            DRAWING_COSMETIC_VERTEX_CAPABILITY_NAME,
            json.dumps(wrong_type_arguments),
            "native-drawing-cosmetic-vertex-wrong-type",
        )
        assert wrong_type["ok"] is False
        assert wrong_type["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        invalid_point = dispatcher.call(
            DRAWING_COSMETIC_VERTEX_CAPABILITY_NAME,
            json.dumps(_point_arguments(page, view, (1_000_000_001.0, 0.0))),
            "native-drawing-cosmetic-vertex-invalid-point",
        )
        assert invalid_point["ok"] is False
        assert invalid_point["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        duplicate_midpoint = dispatcher.call(
            DRAWING_COSMETIC_VERTEX_CAPABILITY_NAME,
            json.dumps(
                _midpoint_arguments(
                    page,
                    view,
                    (midpoint_edges[0], midpoint_edges[0]),
                )
            ),
            "native-drawing-cosmetic-vertex-duplicate-midpoint",
        )
        assert duplicate_midpoint["ok"] is False
        assert (
            duplicate_midpoint["error_code"]
            == "NATIVE_DRAWING_COSMETIC_VERTEX_REFERENCES_INVALID"
        )
        assert state_store.current_revision(str(document.Uid)) == refusal_revision
        assert int(document.UndoCount) == refusal_undo
        assert (
            drawing_cosmetic_vertex_inventory_state(view)["inventory_state_sha256"]
            == inventory["inventory_state_sha256"]
        )

        rollback_revision = state_store.current_revision(str(document.Uid))
        rollback_undo = int(document.UndoCount)
        original_verify = VertexRuntimeModule.verify_drawing_cosmetic_vertex

        def fail_verify(_document, _draft):
            raise RuntimeError("Injected cosmetic-vertex verification failure")

        VertexRuntimeModule.verify_drawing_cosmetic_vertex = fail_verify
        try:
            rollback = dispatcher.call(
                DRAWING_COSMETIC_VERTEX_CAPABILITY_NAME,
                json.dumps(_offset_arguments(page, view, vertex_name, (1.0, 1.0))),
                "native-drawing-cosmetic-vertex-rollback",
            )
        finally:
            VertexRuntimeModule.verify_drawing_cosmetic_vertex = original_verify
        _events(16)
        assert rollback["ok"] is False
        assert rollback["error_code"] == "NATIVE_POSTCONDITION_FAILED"
        assert (
            drawing_cosmetic_vertex_inventory_state(view)["inventory_state_sha256"]
            == inventory["inventory_state_sha256"]
        )
        assert state_store.current_revision(str(document.Uid)) == rollback_revision
        assert int(document.UndoCount) == rollback_undo
        assert _selection() == selection_before

        assert tuple(document.Objects) == objects_before
        assert tuple(page.Views) == views_before
        assert tuple(document.VibeCADTimeline.Operations) == history_before
        assert visibility_before == (
            bool(source.ViewObject.Visibility),
            bool(view.ViewObject.Visibility),
            bool(page.ViewObject.Visibility),
        )

        document.undo()
        _events(16)
        undone = drawing_cosmetic_vertex_inventory_state(view)
        assert undone["vertex_count"] == expected_count - 3
        document.redo()
        _events(16)
        redone = drawing_cosmetic_vertex_inventory_state(view)
        assert redone["inventory_state_sha256"] == inventory["inventory_state_sha256"]

        names = {"page": page.Name, "view": view.Name}
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        page = document.getObject(names["page"])
        view = document.getObject(names["view"])
        assert page is not None and view is not None
        page.ViewObject.show()
        assert document.recompute([view, page], True, True) is not False
        _events(24)
        reopened = drawing_cosmetic_vertex_inventory_state(view)
        assert reopened["inventory_state_sha256"] == redone["inventory_state_sha256"]
        assert {item["tag"] for item in reopened["vertices"]} == all_tags
        for tag in all_tags:
            assert TechDrawGui.drawingPersistentCosmeticVertex(view, tag)["tag"] == tag

        print(
            "VIBECAD_NATIVE_DRAWING_COSMETIC_VERTEX_GUI_OK operations=5 "
            "intersection=true offset=true point=true canonical_coordinates=true "
            "midpoints=true quadrants=true ordered_sources=true "
            "human_oracle=true shared_host_builder=true "
            "task_accept=true task_reject=true task_transaction=true exact_page=true "
            "exact_view=true projection_hash=true element_hash=true all_intersections=true "
            "explicit_offset=true zero_offset=true explicit_point_range=true "
            "host_style=true persistent_tags=true "
            "selection=true visibility=true history=true no_intersection=true wrong_type=true "
            "stale=true rollback=true revision=true idempotency=true undo=true redo=true "
            "snapshot=true reopen=true low_noise=true native_no_task=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        try:
            if Gui.Control.activeDialog():
                Gui.Control.activeTaskDialog().reject()
                _events(8)
        except Exception:
            pass
        Gui.Selection.clearSelection()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
