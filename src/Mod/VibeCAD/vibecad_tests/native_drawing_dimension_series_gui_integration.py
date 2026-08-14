# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for Native Drawing dimension series and inference."""

from __future__ import annotations

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
import VibeCADNativeDrawingDimensionSeriesRuntime as SeriesRuntimeModule
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeDrawingDimensionInferenceSchema import (
    DRAWING_DIMENSION_INFERENCE_CAPABILITY_NAME,
    DRAWING_DIMENSION_INFERENCE_OPERATIONS,
)
from VibeCADNativeDrawingDimensionSeriesSchema import (
    DRAWING_DIMENSION_SERIES_CAPABILITY_NAME,
    DRAWING_DIMENSION_SERIES_OPERATIONS,
)
from VibeCADNativeDrawingDimensionState import drawing_dimension_state
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewState import drawing_view_state
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


_SERIES_ACTIONS = {
    "TechDraw_ExtensionCreateHorizChainDimension": (
        "create_horizontal_chain",
        "ExactDrawingHorizontalChainDimensionSeries",
    ),
    "TechDraw_ExtensionCreateVertChainDimension": (
        "create_vertical_chain",
        "ExactDrawingVerticalChainDimensionSeries",
    ),
    "TechDraw_ExtensionCreateObliqueChainDimension": (
        "create_oblique_chain",
        "ExactDrawingObliqueChainDimensionSeries",
    ),
    "TechDraw_ExtensionCreateHorizCoordDimension": (
        "create_horizontal_coordinate",
        "ExactDrawingHorizontalCoordinateDimensionSeries",
    ),
    "TechDraw_ExtensionCreateVertCoordDimension": (
        "create_vertical_coordinate",
        "ExactDrawingVerticalCoordinateDimensionSeries",
    ),
    "TechDraw_ExtensionCreateObliqueCoordDimension": (
        "create_oblique_coordinate",
        "ExactDrawingObliqueCoordinateDimensionSeries",
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
    assert surface.surface_id == "drawing"
    return controller, surface


def _selection() -> tuple:
    return tuple(
        (item.Object.Name, tuple(item.SubElementNames))
        for item in Gui.Selection.getSelectionEx()
    )


def _create_fixture(document):
    document.openTransaction("Create Drawing dimension-series fixture")
    transaction = int(document.getBookedTransactionID())
    try:
        points = (
            App.Vector(0.0, 0.0, 0.0),
            App.Vector(20.0, 5.0, 0.0),
            App.Vector(35.0, 18.0, 0.0),
            App.Vector(12.0, 28.0, 0.0),
            App.Vector(-8.0, 15.0, 0.0),
        )
        source = document.addObject("Part::Feature", "SeriesSource")
        source.Label = "Series Source"
        source.Shape = Part.Face(Part.makePolygon((*points, points[0])))
        document.publishProvisionalTimelineOperationBlock(source, (), ())

        page = document.addObject("TechDraw::DrawPage", "SeriesPage")
        page.Label = "Series Page"
        template = document.addObject("TechDraw::DrawSVGTemplate", "SeriesTemplate")
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

        view = document.addObject("TechDraw::DrawViewPart", "SeriesView")
        view.Label = "Series View"
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


def _target(element: dict) -> dict[str, str]:
    return {
        "subelement": element["name"],
        "expected_element_state_sha256": element["element_state_sha256"],
    }


def _targets(page, view) -> tuple[dict, dict, dict]:
    page_state = drawing_page_state(page)
    view_state = drawing_view_state(view)
    projection = drawing_projected_geometry_state(view)
    return (
        {
            "object_name": page_state["object_name"],
            "expected_state_sha256": page_state["state_sha256"],
        },
        {
            "object_name": view_state["object_name"],
            "expected_state_sha256": view_state["state_sha256"],
            "expected_projection_state_sha256": projection[
                "projection_state_sha256"
            ],
        },
        projection,
    )


def _series_vertices(projection: dict, direction: str) -> tuple[dict, dict, dict]:
    vertices = [
        item
        for item in projection["elements"]
        if item["element_type"] == "vertex" and item["visible"]
    ]
    for first in vertices:
        p0 = first["point_in_view_mm"]
        for second in vertices:
            if second is first:
                continue
            p1 = second["point_in_view_mm"]
            dx = p0["x_mm"] - p1["x_mm"]
            dy = p0["y_mm"] - p1["y_mm"]
            if dx * dx + dy * dy <= 1.0e-12:
                continue
            for third in vertices:
                if third is first or third is second:
                    continue
                p2 = third["point_in_view_mm"]
                if direction == "horizontal":
                    values = (p0["x_mm"], p1["x_mm"], p2["x_mm"])
                elif direction == "vertical":
                    values = (p0["y_mm"], p1["y_mm"], p2["y_mm"])
                else:
                    values = (
                        p0["x_mm"] * dx + p0["y_mm"] * dy,
                        p1["x_mm"] * dx + p1["y_mm"] * dy,
                        p2["x_mm"] * dx + p2["y_mm"] * dy,
                    )
                separations = (
                    abs(values[0] - values[1]),
                    abs(values[0] - values[2]),
                    abs(values[1] - values[2]),
                )
                if min(separations) > 1.0e-8:
                    return first, second, third
    raise AssertionError(f"fixture has no valid {direction} dimension-series vertices")


def _turn(surface, registry) -> NativeTurnSnapshot:
    definitions = (
        (
            registry.definition(DRAWING_DIMENSION_SERIES_CAPABILITY_NAME),
            DRAWING_DIMENSION_SERIES_OPERATIONS,
        ),
        (
            registry.definition(DRAWING_DIMENSION_INFERENCE_CAPABILITY_NAME),
            DRAWING_DIMENSION_INFERENCE_OPERATIONS,
        ),
    )
    schemas = tuple(definition.provider_schema(operations) for definition, operations in definitions)
    assert all(definition is not None for definition, _operations in definitions)
    assert len(json.dumps(schemas, separators=(",", ":")).encode()) < 16 * 1024
    assert "unknown" not in json.dumps(schemas, sort_keys=True).casefold()
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(
                DRAWING_DIMENSION_SERIES_CAPABILITY_NAME,
                DRAWING_DIMENSION_INFERENCE_CAPABILITY_NAME,
            ),
            schemas=schemas,
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-drawing-dimension-series-"
        )
        save_path = Path(temporary.name) / "dimension-series.FCStd"
        controller, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        infer_plan = plans["TechDraw_Dimension"]
        assert (
            infer_plan.capability_family,
            infer_plan.operation_variant,
            infer_plan.exact_target_type,
        ) == (
            DRAWING_DIMENSION_INFERENCE_CAPABILITY_NAME,
            "infer",
            "ExactDrawingElementsWithUnambiguousDimensionSemantics",
        )
        for command_id, (operation, exact_target) in _SERIES_ACTIONS.items():
            plan = plans[command_id]
            assert (
                plan.capability_family,
                plan.operation_variant,
                plan.exact_target_type,
                plan.transaction_behavior,
                plan.background_required,
            ) == (
                DRAWING_DIMENSION_SERIES_CAPABILITY_NAME,
                operation,
                exact_target,
                "document",
                False,
            )

        document = App.newDocument("NativeDrawingDimensionSeriesGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        source, page, view = _create_fixture(document)

        # The human ribbon action and Native use the same compiled builder.
        _page_target, _view_target, projection = _targets(page, view)
        human_vertices = _series_vertices(projection, "horizontal")
        before = tuple(document.Objects)
        Gui.Selection.clearSelection()
        for vertex in human_vertices:
            Gui.Selection.addSelection(view, vertex["name"])
        Gui.runCommand("TechDraw_ExtensionCreateHorizChainDimension")
        _events(16)
        human_created = tuple(value for value in document.Objects if value not in before)
        human_group = next(
            value for value in human_created if value.TypeId == "App::DocumentObjectGroup"
        )
        assert len(tuple(human_group.Group or ())) == 2
        assert all(
            value.isDerivedFrom("TechDraw::DrawViewDimension")
            for value in human_group.Group
        )
        human_group_name = str(human_group.Name)
        document.undo()
        _events(12)
        assert document.getObject(human_group_name) is None
        Gui.Selection.clearSelection()
        assert document.recompute([view, page], True, True) is not False

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-drawing-dimension-series-gui")

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

        def call(capability: str, arguments: dict, *, succeeds: bool = True) -> dict:
            nonlocal call_index
            call_index += 1
            response = dispatcher.call(
                capability,
                json.dumps(arguments, separators=(",", ":")),
                f"native-drawing-dimension-series-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        source.ViewObject.Visibility = True
        view.ViewObject.Visibility = True
        Gui.Selection.addSelection(source)
        selection_before = _selection()
        visibility_before = (
            bool(source.ViewObject.Visibility),
            bool(view.ViewObject.Visibility),
            bool(page.ViewObject.Visibility),
        )
        history_before = tuple(document.VibeCADTimeline.Operations)
        group_names = []
        dimension_names = []
        last_arguments = None
        for operation in DRAWING_DIMENSION_SERIES_OPERATIONS:
            direction = operation.split("_")[1]
            page_target, view_target, projection = _targets(page, view)
            vertices = _series_vertices(projection, direction)
            arguments = {
                "operation": operation,
                "label": operation.replace("create_", "").replace("_", " ").title(),
                "page": page_target,
                "view": view_target,
                "vertices": [_target(vertex) for vertex in vertices],
            }
            result = call(DRAWING_DIMENSION_SERIES_CAPABILITY_NAME, arguments)
            assert result["series"]["dimension_count"] == 2
            assert result["series"]["direction"] == direction
            assert result["history_operation"]["timeline_role"] == "operation"
            assert len(result["dimensions"]) == 2
            assert len(json.dumps(result, separators=(",", ":")).encode()) < 12 * 1024
            assert result["assistant_undo_available"] is True
            assert not Gui.Control.activeDialog()
            group_names.append(result["history_operation"]["object_name"])
            dimension_names.extend(
                item["object_name"] for item in result["dimensions"]
            )
            last_arguments = arguments

        assert _selection() == selection_before
        assert (
            bool(source.ViewObject.Visibility),
            bool(view.ViewObject.Visibility),
            bool(page.ViewObject.Visibility),
        ) == visibility_before
        assert tuple(document.VibeCADTimeline.Operations) == (
            *history_before,
            *(
                value
                for name in group_names
                for value in (
                    *tuple(document.getObject(name).Group or ()),
                    document.getObject(name),
                )
            ),
        )
        for group_name in group_names:
            group = document.getObject(group_name)
            assert group is not None and len(tuple(group.Group or ())) == 2
            assert all(value.VibeCADTimelineOwner is group for value in group.Group)

        page_target, view_target, projection = _targets(page, view)
        face = next(
            item for item in projection["elements"] if item["element_type"] == "face"
        )
        inferred = call(
            DRAWING_DIMENSION_INFERENCE_CAPABILITY_NAME,
            {
                "operation": "infer",
                "label": "Inferred Area",
                "page": page_target,
                "view": view_target,
                "label_position_in_view_mm": {"x_mm": 0.0, "y_mm": 32.0},
                "elements": [_target(face)],
            },
        )
        assert inferred["operation"] == "create_area"
        assert inferred["dimension"]["dimension_type"] == "Area"
        inferred_name = inferred["dimension"]["object_name"]

        page_target, view_target, projection = _targets(page, view)
        ambiguous_vertices = _series_vertices(projection, "oblique")
        rejected = call(
            DRAWING_DIMENSION_INFERENCE_CAPABILITY_NAME,
            {
                "operation": "infer",
                "label": "Ambiguous Dimension",
                "page": page_target,
                "view": view_target,
                "label_position_in_view_mm": {"x_mm": 0.0, "y_mm": 0.0},
                "elements": [_target(vertex) for vertex in ambiguous_vertices],
            },
            succeeds=False,
        )
        assert rejected["error_code"] == "NATIVE_DRAWING_DIMENSION_INFERENCE_AMBIGUOUS"
        candidates = rejected["repair"]["candidates"]
        assert any(
            candidate["capability"] == DRAWING_DIMENSION_SERIES_CAPABILITY_NAME
            and candidate["operation"] == "create_oblique_chain"
            for candidate in candidates
        )
        assert any(
            candidate["operation"] == "create_three_point_angle"
            for candidate in candidates
        )

        # A failed oblique postcondition must restore objects and carrier geometry.
        assert last_arguments is not None
        page_target, view_target, projection = _targets(page, view)
        rollback_vertices = _series_vertices(projection, "oblique")
        rollback_arguments = {
            "operation": "create_oblique_chain",
            "label": "Rollback Oblique Chain",
            "page": page_target,
            "view": view_target,
            "vertices": [_target(vertex) for vertex in rollback_vertices],
        }
        objects_before = tuple(document.Objects)
        views_before = tuple(page.Views)
        history_rollback_before = tuple(document.VibeCADTimeline.Operations)
        projection_before = projection["projection_state_sha256"]
        undo_before = int(document.UndoCount)
        original_verify = SeriesRuntimeModule.verify_drawing_dimension_series

        def fail_verify(_document, _draft):
            raise RuntimeError("injected dimension-series verification failure")

        SeriesRuntimeModule.verify_drawing_dimension_series = fail_verify
        try:
            rejected = call(
                DRAWING_DIMENSION_SERIES_CAPABILITY_NAME,
                rollback_arguments,
                succeeds=False,
            )
        finally:
            SeriesRuntimeModule.verify_drawing_dimension_series = original_verify
        _events(12)
        assert rejected["error_code"] == "NATIVE_POSTCONDITION_FAILED", rejected
        assert tuple(document.Objects) == objects_before
        assert tuple(page.Views) == views_before
        assert tuple(document.VibeCADTimeline.Operations) == history_rollback_before
        projection_after = drawing_projected_geometry_state(view)
        assert projection_after["projection_state_sha256"] == projection_before, {
            "before_view": projection["view"],
            "after_view": projection_after["view"],
            "before_counts": (
                projection["edge_count"],
                projection["vertex_count"],
                projection["face_count"],
            ),
            "after_counts": (
                projection_after["edge_count"],
                projection_after["vertex_count"],
                projection_after["face_count"],
            ),
            "before_elements": [
                (item["name"], item["element_state_sha256"])
                for item in projection["elements"]
            ],
            "after_elements": [
                (item["name"], item["element_state_sha256"])
                for item in projection_after["elements"]
            ],
        }
        assert int(document.UndoCount) == undo_before

        last_group_name = group_names[-1]
        last_group = document.getObject(last_group_name)
        last_states = {
            value.Name: drawing_dimension_state(value)["state_sha256"]
            for value in last_group.Group
        }
        document.undo()
        _events(12)
        assert document.getObject(inferred_name) is None
        document.undo()
        _events(12)
        assert document.getObject(last_group_name) is None
        document.redo()
        _events(12)
        restored_group = document.getObject(last_group_name)
        assert restored_group is not None
        assert {
            value.Name: drawing_dimension_state(value)["state_sha256"]
            for value in restored_group.Group
        } == last_states
        document.redo()
        _events(12)
        assert document.getObject(inferred_name) is not None

        document.recompute()
        document.saveAs(str(save_path))
        names = {"source": source.Name, "page": page.Name, "view": view.Name}
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        source = document.getObject(names["source"])
        page = document.getObject(names["page"])
        view = document.getObject(names["view"])
        assert all(value is not None for value in (source, page, view))
        for group_name in group_names:
            group = document.getObject(group_name)
            assert group is not None and group in tuple(document.VibeCADTimeline.Operations)
            assert len(tuple(group.Group or ())) == 2
            assert all(
                drawing_dimension_state(value)["timeline_owner_name"] == group_name
                for value in group.Group
            )

        print(
            "VIBECAD_NATIVE_DRAWING_DIMENSION_SERIES_GUI_OK "
            "operations=" + ",".join(DRAWING_DIMENSION_SERIES_OPERATIONS) + " "
            "human_oracle=true shared_builder=true exact_targets=true "
            "history_group=true owned_dimensions=true carrier_geometry=true "
            "inference=true ambiguity_refusal=true candidate_guidance=true "
            "selection=true visibility=true rollback=true undo=true redo=true "
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
