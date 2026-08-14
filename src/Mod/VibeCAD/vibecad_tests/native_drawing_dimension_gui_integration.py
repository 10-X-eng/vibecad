# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for explicit Native Drawing dimensions."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
import VibeCADNativeDrawingDimensionRuntime as DimensionRuntimeModule
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeDrawingDimensionSchema import (
    DRAWING_DIMENSION_CAPABILITY_NAME,
    DRAWING_GENERAL_DIMENSION_OPERATIONS,
)
from VibeCADNativeDrawingDimensionEditState import drawing_dimension_edit_state
from VibeCADNativeDrawingDimensionState import (
    drawing_axonometric_dimension_state,
    drawing_dimension_repair_state,
    drawing_dimension_state,
    drawing_extent_state,
    is_drawing_axonometric_dimension,
    is_drawing_extent,
)
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewState import drawing_view_state
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSnapshot import build_active_snapshot
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


_ACTION_CONTRACTS = {
    "TechDraw_LengthDimension": (
        "create_length",
        "ExactDrawingAlignedDimensionReferences",
    ),
    "TechDraw_HorizontalDimension": (
        "create_horizontal",
        "ExactDrawingHorizontalDimensionReferences",
    ),
    "TechDraw_VerticalDimension": (
        "create_vertical",
        "ExactDrawingVerticalDimensionReferences",
    ),
    "TechDraw_RadiusDimension": (
        "create_radius",
        "ExactDrawingRadialEdge",
    ),
    "TechDraw_DiameterDimension": (
        "create_diameter",
        "ExactDrawingRadialEdge",
    ),
    "TechDraw_AngleDimension": (
        "create_angle",
        "ExactDrawingTwoEdgeAngle",
    ),
    "TechDraw_3PtAngleDimension": (
        "create_three_point_angle",
        "ExactDrawingOrderedThreePointAngle",
    ),
    "TechDraw_AreaDimension": (
        "create_area",
        "ExactDrawingProjectedFace",
    ),
    "TechDraw_HorizontalExtentDimension": (
        "create_horizontal_extent",
        "ExactDrawingHorizontalExtentTarget",
    ),
    "TechDraw_VerticalExtentDimension": (
        "create_vertical_extent",
        "ExactDrawingVerticalExtentTarget",
    ),
    "TechDraw_AxoLengthDimension": (
        "create_axonometric_length",
        "ExactDrawingAxonometricMeasurementDirectionsAndValueMode",
    ),
}


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
    assert surface.surface_id == "drawing", surface.surface_id
    return controller, surface


def _selection() -> tuple:
    return tuple(
        (item.Object.Name, tuple(item.SubElementNames))
        for item in Gui.Selection.getSelectionEx()
    )


def _create_fixture(document):
    document.openTransaction("Create exact Drawing dimension fixture")
    transaction = int(document.getBookedTransactionID())
    try:
        box = Part.makeBox(
            40.0,
            24.0,
            8.0,
            App.Vector(-50.0, -12.0, 0.0),
        )
        cylinder = Part.makeCylinder(
            8.0,
            8.0,
            App.Vector(25.0, 0.0, 0.0),
            App.Vector(0.0, 0.0, 1.0),
        )
        ellipse = Part.Ellipse(App.Vector(52.0, 0.0, 0.0), 10.0, 5.0).toShape()
        source = document.addObject("Part::Feature", "DimensionSource")
        source.Label = "Dimension Source"
        source.Shape = Part.makeCompound((box, cylinder, ellipse))
        document.publishProvisionalTimelineOperationBlock(source, (), ())

        page = document.addObject("TechDraw::DrawPage", "DimensionPage")
        page.Label = "Dimension Page"
        template = document.addObject(
            "TechDraw::DrawSVGTemplate",
            "DimensionTemplate",
        )
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

        view = document.addObject("TechDraw::DrawViewPart", "DimensionView")
        view.Label = "Dimension View"
        view.Source = [source]
        view.Direction = App.Vector(0.0, 0.0, 1.0)
        view.XDirection = App.Vector(1.0, 0.0, 0.0)
        view.ScaleType = "Custom"
        view.Scale = 1.0
        view.X = 105.0
        view.Y = 80.0
        view.CoarseView = False
        document.publishProvisionalTimelineOperationBlock(view, (), ())
        assert int(page.addView(view)) >= 1
        assert document.recompute([source, view, page], True, True) is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    page.ViewObject.show()
    _events(24)
    return source, page, view


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(DRAWING_DIMENSION_CAPABILITY_NAME)
    assert definition is not None
    exposed_operations = (*DRAWING_GENERAL_DIMENSION_OPERATIONS, "edit")
    schema = definition.provider_schema(exposed_operations)
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "path" not in encoded.casefold()
    assert len(encoded.encode()) < 36 * 1024
    branches = schema["parameters"]["oneOf"]
    assert len(branches) == len(exposed_operations)
    required_by_operation = {
        branch["properties"]["operation"]["const"]: set(branch["required"])
        for branch in branches
    }
    assert required_by_operation["create_radius"] >= {
        "edge",
        "allow_approximate",
    }
    assert required_by_operation["create_angle"] >= {
        "first_edge",
        "second_edge",
    }
    assert required_by_operation["create_three_point_angle"] >= {
        "first_arm_point",
        "apex_point",
        "second_arm_point",
    }
    assert required_by_operation["create_area"] >= {"face"}
    assert required_by_operation["create_horizontal_extent"] >= {"extent"}
    assert required_by_operation["create_vertical_extent"] >= {"extent"}
    assert required_by_operation["create_axonometric_length"] >= {
        "measurement",
        "extension_direction_edge",
        "expected_value_mode",
    }
    assert required_by_operation["edit"] == {
        "operation",
        "dimension",
        "display",
        "tolerance",
        "layout",
        "appearance",
    }
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(DRAWING_DIMENSION_CAPABILITY_NAME,),
            schemas=(schema,),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _element_target(element: dict) -> dict[str, str]:
    return {
        "subelement": element["name"],
        "expected_element_state_sha256": element["element_state_sha256"],
    }


def _base_arguments(operation: str, page, view, label: str, x: float, y: float):
    page_state = drawing_page_state(page)
    view_state = drawing_view_state(view)
    projection = drawing_projected_geometry_state(view)
    return {
        "operation": operation,
        "label": label,
        "page": {
            "object_name": page_state["object_name"],
            "expected_state_sha256": page_state["state_sha256"],
        },
        "view": {
            "object_name": view_state["object_name"],
            "expected_state_sha256": view_state["state_sha256"],
            "expected_projection_state_sha256": projection["projection_state_sha256"],
        },
        "label_position_in_view_mm": {"x_mm": x, "y_mm": y},
    }


def _line_direction(edge: dict) -> tuple[float, float]:
    first = edge["start_in_view_mm"]
    second = edge["end_in_view_mm"]
    return second["x_mm"] - first["x_mm"], second["y_mm"] - first["y_mm"]


def _reference_geometry(view) -> dict[str, object]:
    state = drawing_projected_geometry_state(view)
    visible_edges = [
        item
        for item in state["elements"]
        if item["element_type"] == "edge" and item["visible"]
    ]
    horizontal = next(
        item
        for item in visible_edges
        if abs(_line_direction(item)[1]) <= 1.0e-7
        and abs(_line_direction(item)[0]) > 1.0
    )
    vertical = next(
        item
        for item in visible_edges
        if abs(_line_direction(item)[0]) <= 1.0e-7
        and abs(_line_direction(item)[1]) > 1.0
    )
    circle = next(
        item
        for item in visible_edges
        if "Circle" in item["geometry_type"]
        and item.get("closed")
        and "radius_view_mm" in item
    )
    ellipse = next(
        item
        for item in visible_edges
        if "Ellipse" in item["geometry_type"] and item.get("closed")
    )
    face = next(item for item in state["elements"] if item["element_type"] == "face")
    unique_vertices = {}
    for item in state["elements"]:
        if item["element_type"] != "vertex" or not item["visible"]:
            continue
        point = item["point_in_view_mm"]
        key = (round(point["x_mm"], 8), round(point["y_mm"], 8))
        unique_vertices.setdefault(key, item)
    vertices = tuple(unique_vertices.values())
    three_points = None
    for apex in vertices:
        origin = apex["point_in_view_mm"]
        for first in vertices:
            if first is apex:
                continue
            first_point = first["point_in_view_mm"]
            first_vector = (
                first_point["x_mm"] - origin["x_mm"],
                first_point["y_mm"] - origin["y_mm"],
            )
            for second in vertices:
                if second is apex or second is first:
                    continue
                second_point = second["point_in_view_mm"]
                second_vector = (
                    second_point["x_mm"] - origin["x_mm"],
                    second_point["y_mm"] - origin["y_mm"],
                )
                cross = abs(
                    first_vector[0] * second_vector[1]
                    - first_vector[1] * second_vector[0]
                )
                if cross > 1.0:
                    three_points = (first, apex, second)
                    break
            if three_points is not None:
                break
        if three_points is not None:
            break
    assert three_points is not None
    assert horizontal["axonometric_value_mode"] == "x_axis_true_length"
    assert vertical["axonometric_value_mode"] == "y_axis_true_length"
    return {
        "horizontal": horizontal,
        "vertical": vertical,
        "circle": circle,
        "ellipse": ellipse,
        "face": face,
        "three_points": three_points,
        "projection": state,
    }


def _operation_arguments(operation: str, page, view, geometry: dict, index: int):
    arguments = _base_arguments(
        operation,
        page,
        view,
        operation.replace("create_", "").replace("_", " ").title(),
        -40.0 + index * 10.0,
        28.0 - index * 3.0,
    )
    if operation in {"create_length", "create_horizontal"}:
        arguments["references"] = [_element_target(geometry["horizontal"])]
    elif operation == "create_vertical":
        arguments["references"] = [_element_target(geometry["vertical"])]
    elif operation in {"create_radius", "create_diameter"}:
        arguments["edge"] = _element_target(geometry["circle"])
        arguments["allow_approximate"] = False
    elif operation == "create_angle":
        arguments["first_edge"] = _element_target(geometry["horizontal"])
        arguments["second_edge"] = _element_target(geometry["vertical"])
    elif operation == "create_three_point_angle":
        first, apex, second = geometry["three_points"]
        arguments["first_arm_point"] = _element_target(first)
        arguments["apex_point"] = _element_target(apex)
        arguments["second_arm_point"] = _element_target(second)
    elif operation == "create_area":
        arguments["face"] = _element_target(geometry["face"])
    elif operation == "create_horizontal_extent":
        arguments["extent"] = {"scope": "whole_view"}
    elif operation == "create_vertical_extent":
        arguments["extent"] = {
            "scope": "edges",
            "edges": [_element_target(geometry["vertical"])],
        }
    elif operation == "create_axonometric_length":
        arguments["measurement"] = {
            "kind": "edge",
            "dimension_edge": _element_target(geometry["horizontal"]),
        }
        arguments["extension_direction_edge"] = _element_target(geometry["vertical"])
        arguments["expected_value_mode"] = "x_axis_true_length"
    else:
        raise AssertionError(operation)
    return arguments


def _dimension_objects(document) -> tuple:
    return tuple(
        obj
        for obj in document.Objects
        if obj.isDerivedFrom("TechDraw::DrawViewDimension")
    )


def _dimension_state(dimension) -> dict:
    return (
        drawing_extent_state(dimension)
        if is_drawing_extent(dimension)
        else drawing_axonometric_dimension_state(dimension)
        if is_drawing_axonometric_dimension(dimension)
        else drawing_dimension_state(dimension)
    )


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-drawing-dimension-"
        )
        save_path = Path(temporary.name) / "native-drawing-dimension.FCStd"
        controller, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        for command_id, (operation, target_type) in _ACTION_CONTRACTS.items():
            plan = plans[command_id]
            assert (
                plan.capability_family,
                plan.operation_variant,
                plan.exact_target_type,
                plan.transaction_behavior,
                plan.background_required,
            ) == (
                DRAWING_DIMENSION_CAPABILITY_NAME,
                operation,
                target_type,
                "document",
                False,
            )

        document = App.newDocument("NativeDrawingDimensionGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        source, page, view = _create_fixture(document)
        geometry = _reference_geometry(view)

        # The human command and Native operation share DimensionBuilder.
        human_before = tuple(document.Objects)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(view, geometry["horizontal"]["name"])
        Gui.runCommand("TechDraw_LengthDimension")
        _events(12)
        human_created = tuple(
            obj for obj in document.Objects if obj not in human_before
        )
        assert len(human_created) == 1
        assert human_created[0].isDerivedFrom("TechDraw::DrawViewDimension")
        assert str(human_created[0].Type) == "Distance"
        human_name = str(human_created[0].Name)
        document.undo()
        _events(12)
        assert document.getObject(human_name) is None
        Gui.Selection.clearSelection()
        assert document.recompute([view, page], True, True) is not False
        _events(12)
        geometry = _reference_geometry(view)

        for command_id, expected_type in (
            ("TechDraw_HorizontalExtentDimension", "DistanceX"),
            ("TechDraw_VerticalExtentDimension", "DistanceY"),
        ):
            human_before = tuple(document.Objects)
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(view)
            Gui.runCommand(command_id)
            _events(12)
            human_created = tuple(
                obj for obj in document.Objects if obj not in human_before
            )
            assert len(human_created) == 1
            assert is_drawing_extent(human_created[0])
            assert str(human_created[0].Type) == expected_type
            human_name = str(human_created[0].Name)
            document.undo()
            _events(12)
            assert document.getObject(human_name) is None
            assert document.recompute([view, page], True, True) is not False
            _events(12)
        Gui.Selection.clearSelection()
        geometry = _reference_geometry(view)

        human_before = tuple(document.Objects)
        Gui.Selection.addSelection(view, geometry["horizontal"]["name"])
        Gui.Selection.addSelection(view, geometry["vertical"]["name"])
        Gui.runCommand("TechDraw_AxoLengthDimension")
        _events(16)
        human_created = tuple(
            obj for obj in document.Objects if obj not in human_before
        )
        assert len(human_created) == 1
        assert is_drawing_axonometric_dimension(human_created[0])
        human_axonometric = drawing_axonometric_dimension_state(human_created[0])
        assert human_axonometric["axonometric"]["angle_override"] is True
        assert human_axonometric["axonometric"]["arbitrary_display"] is True
        human_name = str(human_created[0].Name)
        document.undo()
        _events(12)
        assert document.getObject(human_name) is None
        assert document.recompute([view, page], True, True) is not False
        Gui.Selection.clearSelection()
        _events(12)
        geometry = _reference_geometry(view)

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-drawing-dimension-gui")

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
        call_index = 0

        def call(arguments: dict, *, succeeds: bool = True) -> dict:
            nonlocal call_index
            call_index += 1
            response = dispatcher.call(
                DRAWING_DIMENSION_CAPABILITY_NAME,
                json.dumps(arguments, separators=(",", ":")),
                f"native-drawing-dimension-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        source.ViewObject.Visibility = True
        view.ViewObject.Visibility = True
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source)
        Gui.Selection.addSelection(view, geometry["horizontal"]["name"])
        selection_before = _selection()
        visibility_before = (
            bool(source.ViewObject.Visibility),
            bool(view.ViewObject.Visibility),
            bool(page.ViewObject.Visibility),
        )
        history_before = tuple(document.VibeCADTimeline.Operations)
        revision_before = state_store.current_revision(str(document.Uid))

        created_names = []
        for index, operation in enumerate(DRAWING_GENERAL_DIMENSION_OPERATIONS):
            result = call(_operation_arguments(operation, page, view, geometry, index))
            state = result["dimension"]
            created_names.append(state["object_name"])
            assert result["operation"] == operation
            assert result["approximate"] is False
            assert state["valid"] and state["timeline_usable"]
            assert state["measured_value"]["value"] > 0.0
            assert result["assistant_undo_available"] is True
            assert len(json.dumps(result, separators=(",", ":")).encode()) < 8 * 1024
            assert "elements" not in result
            assert not Gui.Control.activeDialog()
        assert state_store.current_revision(str(document.Uid)) == (
            revision_before + len(DRAWING_GENERAL_DIMENSION_OPERATIONS)
        )
        assert _selection() == selection_before
        assert (
            bool(source.ViewObject.Visibility),
            bool(view.ViewObject.Visibility),
            bool(page.ViewObject.Visibility),
        ) == visibility_before
        dimensions = _dimension_objects(document)
        assert tuple(obj.Name for obj in dimensions) == tuple(created_names)
        assert tuple(document.VibeCADTimeline.Operations) == (
            *history_before,
            *dimensions,
        )
        assert all(dimension in tuple(view.InList) for dimension in dimensions)

        deferred_revision_before = state_store.current_revision(str(document.Uid))
        _events(12)
        deferred_revision_after = state_store.current_revision(str(document.Uid))
        assert deferred_revision_after == deferred_revision_before

        first_vertex, _apex, second_vertex = geometry["three_points"]
        vertex_pair = _base_arguments(
            "create_axonometric_length",
            page,
            view,
            "Axonometric Vertex Pair",
            54.0,
            -12.0,
        )
        vertex_pair["measurement"] = {
            "kind": "vertex_pair",
            "first_vertex": _element_target(first_vertex),
            "second_vertex": _element_target(second_vertex),
            "dimension_direction_edge": _element_target(geometry["horizontal"]),
        }
        vertex_pair["extension_direction_edge"] = _element_target(geometry["vertical"])
        vertex_pair["expected_value_mode"] = "x_axis_true_length"
        vertex_result = call(vertex_pair)
        assert vertex_result["value_mode"] == "x_axis_true_length"
        assert vertex_result["displayed_value_mm"] > 0.0
        vertex_name = vertex_result["dimension"]["object_name"]
        created_names.append(vertex_name)
        vertex_dimension = document.getObject(vertex_name)
        assert vertex_dimension in tuple(document.VibeCADTimeline.Operations)
        assert drawing_axonometric_dimension_state(vertex_dimension)["references"] == [
            {"view_name": str(view.Name), "subelement": first_vertex["name"]},
            {"view_name": str(view.Name), "subelement": second_vertex["name"]},
        ]

        stale_inference = _operation_arguments(
            "create_axonometric_length", page, view, geometry, 19
        )
        stale_inference["expected_value_mode"] = "projected"
        rejected = call(stale_inference, succeeds=False)
        assert rejected["error_code"] == ("NATIVE_DRAWING_DIMENSION_INFERENCE_STALE")
        assert rejected["repair"]["current_value_mode"] == "x_axis_true_length"

        invalid_extent = _base_arguments(
            "create_vertical_extent", page, view, "Invalid Mixed Extent", 0.0, 0.0
        )
        invalid_extent["extent"] = {
            "scope": "edges",
            "edges": [
                _element_target(geometry["horizontal"]),
                _element_target(geometry["vertical"]),
            ],
        }
        rejected = call(invalid_extent, succeeds=False)
        assert rejected["error_code"] == ("NATIVE_DRAWING_DIMENSION_REFERENCES_INVALID")
        assert rejected["repair"]["accepted_references"]

        invalid_radial = _base_arguments(
            "create_radius", page, view, "Invalid Radius", 0.0, 0.0
        )
        invalid_radial["edge"] = _element_target(geometry["horizontal"])
        invalid_radial["allow_approximate"] = False
        rejected = call(invalid_radial, succeeds=False)
        assert rejected["error_code"] == ("NATIVE_DRAWING_DIMENSION_REFERENCES_INVALID")
        assert rejected["repair"]["accepted_references"]

        approximate = _base_arguments(
            "create_radius", page, view, "Approximate Ellipse Radius", 42.0, 18.0
        )
        approximate["edge"] = _element_target(geometry["ellipse"])
        approximate["allow_approximate"] = False
        rejected = call(approximate, succeeds=False)
        assert rejected["error_code"] == ("NATIVE_DRAWING_DIMENSION_REFERENCES_INVALID")
        approximate["allow_approximate"] = True
        approximate["page"] = _base_arguments(
            "create_radius", page, view, "unused", 0.0, 0.0
        )["page"]
        accepted = call(approximate)
        assert accepted["approximate"] is True
        assert accepted["geometry_configuration"] == "ellipse"
        created_names.append(accepted["dimension"]["object_name"])

        stale_page = _operation_arguments("create_length", page, view, geometry, 20)
        page.KeepUpdated = not bool(page.KeepUpdated)
        rejected = call(stale_page, succeeds=False)
        assert rejected["error_code"] == "NATIVE_DRAWING_PAGE_STALE"
        page.KeepUpdated = not bool(page.KeepUpdated)

        stale_view = _operation_arguments("create_length", page, view, geometry, 21)
        view.X = float(view.X) + 1.0
        rejected = call(stale_view, succeeds=False)
        assert rejected["error_code"] == "NATIVE_DRAWING_DIMENSION_VIEW_STALE"
        view.X = float(view.X) - 1.0

        stale_projection = _operation_arguments(
            "create_length", page, view, geometry, 22
        )
        stale_projection["view"]["expected_projection_state_sha256"] = "0" * 64
        rejected = call(stale_projection, succeeds=False)
        assert rejected["error_code"] == ("NATIVE_DRAWING_DIMENSION_PROJECTION_STALE")

        stale_element = _operation_arguments("create_length", page, view, geometry, 23)
        stale_element["references"][0]["expected_element_state_sha256"] = "0" * 64
        rejected = call(stale_element, succeeds=False)
        assert rejected["error_code"] == ("NATIVE_DRAWING_DIMENSION_REFERENCE_STALE")

        rollback_arguments = _operation_arguments(
            "create_length", page, view, geometry, 24
        )
        rollback_objects = tuple(document.Objects)
        rollback_views = tuple(page.Views)
        rollback_history = tuple(document.VibeCADTimeline.Operations)
        rollback_revision = state_store.current_revision(str(document.Uid))
        rollback_undo = int(document.UndoCount)
        original_verify = DimensionRuntimeModule.verify_drawing_dimension

        def fail_verify(_document, _draft):
            raise RuntimeError("injected Drawing dimension verification failure")

        DimensionRuntimeModule.verify_drawing_dimension = fail_verify
        try:
            rejected = call(rollback_arguments, succeeds=False)
        finally:
            DimensionRuntimeModule.verify_drawing_dimension = original_verify
        _events(12)
        assert rejected["error_code"] == "NATIVE_POSTCONDITION_FAILED"
        assert tuple(document.Objects) == rollback_objects
        assert tuple(page.Views) == rollback_views
        assert tuple(document.VibeCADTimeline.Operations) == rollback_history
        rollback_revision_after = state_store.current_revision(str(document.Uid))
        assert rollback_revision_after == rollback_revision, (
            rollback_revision,
            rollback_revision_after,
        )
        assert int(document.UndoCount) == rollback_undo
        assert _selection() == selection_before

        undo_result = call(
            _operation_arguments("create_length", page, view, geometry, 25)
        )
        undo_name = undo_result["dimension"]["object_name"]
        undo_state = undo_result["dimension"]
        document.undo()
        _events(12)
        assert document.getObject(undo_name) is None
        document.redo()
        _events(16)
        redone = document.getObject(undo_name)
        assert redone is not None
        redone_state = drawing_dimension_state(redone)
        assert redone_state["state_sha256"] == undo_state["state_sha256"], (
            undo_state,
            redone_state,
        )
        created_names.append(undo_name)

        edit_before = drawing_dimension_edit_state(redone)
        edit_arguments = {
            "operation": "edit",
            "dimension": {
                "object_name": undo_name,
                "expected_edit_state_sha256": edit_before["edit_state_sha256"],
            },
            **{
                name: copy.deepcopy(edit_before[name])
                for name in ("display", "tolerance", "layout", "appearance")
            },
        }
        edit_arguments["display"] = {
            "format_spec": "Edited %.3f",
            "arbitrary": False,
        }
        edit_arguments["tolerance"].update(
            theoretical_exact=False,
            equal=True,
            over=0.1,
            under=-0.1,
            arbitrary=False,
            over_format_spec="%.2f",
            under_format_spec="%.2f",
        )
        edit_arguments["layout"]["label_position_in_view_mm"]["x_mm"] += 4.0
        edit_arguments["layout"]["label_position_in_view_mm"]["y_mm"] -= 3.0
        edit_arguments["appearance"].update(
            flip_arrowheads=not edit_before["appearance"]["flip_arrowheads"],
            color_rgb={"red": 38, "green": 92, "blue": 170},
            font_size_mm=4.2,
            standard_and_style="asme_inlined",
        )
        edited = call(edit_arguments)
        assert edited["operation"] == "edit"
        edited_state = edited["dimension"]
        assert edited_state["display"] == edit_arguments["display"]
        assert edited_state["tolerance"] == edit_arguments["tolerance"]
        assert edited_state["layout"] == edit_arguments["layout"]
        assert edited_state["appearance"] == edit_arguments["appearance"]
        assert edited["assistant_undo_available"] is True
        assert not Gui.Control.activeDialog()

        rejected = call(edit_arguments, succeeds=False)
        assert rejected["error_code"] == "NATIVE_DRAWING_DIMENSION_EDIT_STALE"
        document.undo()
        _events(12)
        redone = document.getObject(undo_name)
        assert (
            drawing_dimension_edit_state(redone)["edit_state_sha256"]
            == (edit_before["edit_state_sha256"])
        )
        document.redo()
        _events(16)
        redone = document.getObject(undo_name)
        assert (
            drawing_dimension_edit_state(redone)["edit_state_sha256"]
            == (edited_state["edit_state_sha256"])
        )

        selected_snapshot = build_active_snapshot(
            document,
            "drawing",
            state_store.snapshot(str(document.Uid)),
            selection={
                "document_uid": str(document.Uid),
                "selected_count": 1,
                "items": [
                    {
                        "object": {
                            "document_uid": str(document.Uid),
                            "object_name": undo_name,
                            "type_id": redone.TypeId,
                        }
                    }
                ],
            },
        )
        selected_dimensions = selected_snapshot["domain"]["selected_dimensions"]
        assert len(selected_dimensions) == 1
        selected_dimension = dict(selected_dimensions[0])
        repair_target = selected_dimension.pop("repair_target")
        edit_target = selected_dimension.pop("edit_target")
        assert selected_dimension == drawing_dimension_state(redone)
        assert edit_target == drawing_dimension_edit_state(redone)
        repair_state = drawing_dimension_repair_state(redone)
        assert (
            repair_target["expected_repair_state_sha256"]
            == (repair_state["repair_state_sha256"])
        )
        assert repair_target["repair_kind"] == "length"
        assert repair_target["valid"] and repair_target["repairable"]
        page_summary = next(
            item
            for item in selected_snapshot["domain"]["pages"]
            if item["object_name"] == page.Name
        )
        dimension_summary = next(
            item for item in page_summary["views"] if item["object_name"] == undo_name
        )
        assert (
            dimension_summary["dimension"]["state_sha256"]
            == (selected_dimensions[0]["state_sha256"])
        )
        assert dimension_summary["dimension"]["repair_target"] == repair_target
        assert (
            len(json.dumps(selected_snapshot, separators=(",", ":")).encode())
            < 96 * 1024
        )

        final_states = {
            name: _dimension_state(document.getObject(name))["state_sha256"]
            for name in created_names
        }
        names = {
            "source": str(source.Name),
            "page": str(page.Name),
            "view": str(view.Name),
        }
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        source = document.getObject(names["source"])
        page = document.getObject(names["page"])
        view = document.getObject(names["view"])
        assert all(obj is not None for obj in (source, page, view))
        page.ViewObject.show()
        assert document.recompute([view, page], True, True) is not False
        _events(24)
        for name, expected_hash in final_states.items():
            dimension = document.getObject(name)
            assert dimension is not None
            assert _dimension_state(dimension)["state_sha256"] == expected_hash
            assert dimension in tuple(view.InList)
            assert dimension in tuple(document.VibeCADTimeline.Operations)

        print(
            "VIBECAD_NATIVE_DRAWING_DIMENSION_GUI_OK "
            "operations=" + ",".join(DRAWING_GENERAL_DIMENSION_OPERATIONS) + " "
            "human_oracle=true human_extent_oracle=true human_axonometric_oracle=true "
            "shared_host_builder=true axonometric_value_mode=true "
            "exact_edit=true complete_edit_state=true human_edit_remains_human=true "
            "axonometric_edge=true axonometric_vertex_pair=true "
            "whole_view_extent=true edge_subset_extent=true projected_zero_based=true "
            "invalid_extent_repair=true "
            "closed_discriminated_schema=true exact_page=true exact_view=true "
            "projection_hash=true element_hash=true approximate_refusal=true "
            "approximate_acceptance=true invalid_geometry_repair=true "
            "selection=true visibility=true tree_parent=true history=true "
            "rollback=true revision=true undo=true redo=true snapshot=true "
            "reopen=true low_noise=true no_task=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        Gui.Selection.clearSelection()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
