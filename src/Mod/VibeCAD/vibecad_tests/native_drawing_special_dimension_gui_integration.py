# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for specialized Drawing dimensions."""

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
import VibeCADNativeDrawingDimensionRuntime as DimensionRuntimeModule
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state
from VibeCADNativeDrawingDimensionState import drawing_dimension_repair_state
from VibeCADNativeDrawingDimensionSchema import (
    DRAWING_DIMENSION_CAPABILITY_NAME,
)
from VibeCADNativeDrawingSpecialDimensionSchema import (
    DRAWING_SPECIAL_DIMENSION_OPERATIONS,
)
from VibeCADNativeDrawingSpecialDimensionState import (
    drawing_arc_length_dimension_state,
    drawing_chamfer_dimension_state,
    is_drawing_arc_length_dimension,
    is_drawing_chamfer_dimension,
)
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
    "TechDraw_ExtensionCreateHorizChamferDimension": (
        "create_horizontal_chamfer",
        "ExactDrawingHorizontalChamferVertices",
    ),
    "TechDraw_ExtensionCreateVertChamferDimension": (
        "create_vertical_chamfer",
        "ExactDrawingVerticalChamferVertices",
    ),
    "TechDraw_ExtensionCreateLengthArc": (
        "create_arc_length_dimension",
        "ExactDrawingCircularArcLength",
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
    document.openTransaction("Create Drawing chamfer fixture")
    transaction = int(document.getBookedTransactionID())
    try:
        points = (
            App.Vector(-20.0, -12.0, 0.0),
            App.Vector(15.0, -12.0, 0.0),
            App.Vector(20.0, -7.0, 0.0),
            App.Vector(20.0, 12.0, 0.0),
            App.Vector(-20.0, 12.0, 0.0),
            App.Vector(-20.0, -12.0, 0.0),
        )
        face = Part.Face(Part.makePolygon(points))
        source = document.addObject("Part::Feature", "ChamferSource")
        source.Label = "Chamfer Source"
        solid = face.extrude(App.Vector(0.0, 0.0, 8.0))
        arc = Part.Edge(
            Part.Arc(
                App.Vector(30.0, -10.0, 0.0),
                App.Vector(40.0, 0.0, 0.0),
                App.Vector(30.0, 10.0, 0.0),
            )
        )
        source.Shape = Part.makeCompound((solid, arc))
        document.publishProvisionalTimelineOperationBlock(source, (), ())

        page = document.addObject("TechDraw::DrawPage", "ChamferPage")
        page.Label = "Chamfer Page"
        template = document.addObject("TechDraw::DrawSVGTemplate", "ChamferTemplate")
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

        view = document.addObject("TechDraw::DrawViewPart", "ChamferView")
        view.Label = "Chamfer View"
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


def _point(element: dict, field: str) -> tuple[float, float]:
    value = element[field]
    return float(value["x_mm"]), float(value["y_mm"])


def _same_point(first: tuple[float, float], second: tuple[float, float]) -> bool:
    return abs(first[0] - second[0]) <= 1.0e-7 and abs(
        first[1] - second[1]
    ) <= 1.0e-7


def _chamfer_vertices(view) -> tuple[dict, dict, dict]:
    projection = drawing_projected_geometry_state(view)
    diagonal = next(
        element
        for element in projection["elements"]
        if element["element_type"] == "edge"
        and element["visible"]
        and not element["closed"]
        and abs(
            element["end_in_view_mm"]["x_mm"]
            - element["start_in_view_mm"]["x_mm"]
        )
        > 1.0
        and abs(
            element["end_in_view_mm"]["y_mm"]
            - element["start_in_view_mm"]["y_mm"]
        )
        > 1.0
    )
    vertices = tuple(
        element
        for element in projection["elements"]
        if element["element_type"] == "vertex" and element["visible"]
    )
    start = _point(diagonal, "start_in_view_mm")
    end = _point(diagonal, "end_in_view_mm")
    first = next(
        element
        for element in vertices
        if _same_point(_point(element, "point_in_view_mm"), start)
    )
    second = next(
        element
        for element in vertices
        if _same_point(_point(element, "point_in_view_mm"), end)
    )
    assert first["name"] != second["name"]
    return first, second, projection


def _arc_edge(view) -> dict:
    projection = drawing_projected_geometry_state(view)
    return next(
        element
        for element in projection["elements"]
        if element["element_type"] == "edge"
        and element["visible"]
        and not element["closed"]
        and "Circle" in element["geometry_type"]
        and "radius_view_mm" in element
    )


def _target(element: dict) -> dict[str, str]:
    return {
        "subelement": element["name"],
        "expected_element_state_sha256": element["element_state_sha256"],
    }


def _arguments(operation: str, page, view, first: dict, second: dict) -> dict:
    page_state = drawing_page_state(page)
    view_state = drawing_view_state(view)
    projection = drawing_projected_geometry_state(view)
    return {
        "operation": operation,
        "label": operation.replace("create_", "").replace("_", " ").title(),
        "page": {
            "object_name": page_state["object_name"],
            "expected_state_sha256": page_state["state_sha256"],
        },
        "view": {
            "object_name": view_state["object_name"],
            "expected_state_sha256": view_state["state_sha256"],
            "expected_projection_state_sha256": projection[
                "projection_state_sha256"
            ],
        },
        "first_vertex": _target(first),
        "second_vertex": _target(second),
        "label_position_in_view_mm": {
            "x_mm": -12.0 if operation == "create_horizontal_chamfer" else 32.0,
            "y_mm": 28.0 if operation == "create_horizontal_chamfer" else -4.0,
        },
    }


def _arc_arguments(page, view, arc_edge: dict) -> dict:
    page_state = drawing_page_state(page)
    view_state = drawing_view_state(view)
    projection = drawing_projected_geometry_state(view)
    return {
        "operation": "create_arc_length_dimension",
        "label": "Circular Arc Length",
        "page": {
            "object_name": page_state["object_name"],
            "expected_state_sha256": page_state["state_sha256"],
        },
        "view": {
            "object_name": view_state["object_name"],
            "expected_state_sha256": view_state["state_sha256"],
            "expected_projection_state_sha256": projection[
                "projection_state_sha256"
            ],
        },
        "arc_edge": _target(arc_edge),
        "label_position_in_view_mm": {"x_mm": 38.0, "y_mm": 18.0},
    }


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(DRAWING_DIMENSION_CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(DRAWING_SPECIAL_DIMENSION_OPERATIONS)
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "path" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 16 * 1024
    assert len(schema["parameters"]["oneOf"]) == 3
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


def _dimensions(document) -> tuple:
    return tuple(
        obj
        for obj in document.Objects
        if obj.isDerivedFrom("TechDraw::DrawViewDimension")
    )


def _dimension_state(dimension) -> dict:
    return (
        drawing_arc_length_dimension_state(dimension)
        if is_drawing_arc_length_dimension(dimension)
        else drawing_chamfer_dimension_state(dimension)
    )


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-drawing-special-dimension-"
        )
        save_path = Path(temporary.name) / "drawing-special-dimension.FCStd"
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

        document = App.newDocument("NativeDrawingSpecialDimensionGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        source, page, view = _create_fixture(document)
        first, second, _projection = _chamfer_vertices(view)
        arc_edge = _arc_edge(view)

        human_oracles = (
            (
                "TechDraw_ExtensionCreateHorizChamferDimension",
                "horizontal",
            ),
            (
                "TechDraw_ExtensionCreateVertChamferDimension",
                "vertical",
            ),
        )
        for command_id, direction in human_oracles:
            before = tuple(document.Objects)
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(view, first["name"])
            Gui.Selection.addSelection(view, second["name"])
            Gui.runCommand(command_id)
            _events(16)
            created = tuple(obj for obj in document.Objects if obj not in before)
            assert len(created) == 1
            assert is_drawing_chamfer_dimension(created[0])
            state = drawing_chamfer_dimension_state(created[0])
            assert state["chamfer"]["direction"] == direction
            assert 0 < state["chamfer"]["angle_degrees"] < 180
            human_name = str(created[0].Name)
            document.undo()
            _events(12)
            assert document.getObject(human_name) is None
            assert document.recompute([view, page], True, True) is not False
            first, second, _projection = _chamfer_vertices(view)
        Gui.Selection.clearSelection()

        human_projection_before = drawing_projected_geometry_state(view)
        before = tuple(document.Objects)
        Gui.Selection.addSelection(view, arc_edge["name"])
        Gui.runCommand("TechDraw_ExtensionCreateLengthArc")
        _events(16)
        created = tuple(obj for obj in document.Objects if obj not in before)
        assert len(created) == 1
        assert is_drawing_arc_length_dimension(created[0])
        human_arc = drawing_arc_length_dimension_state(created[0])
        assert human_arc["arc_length"]["source"]["subelement"] == arc_edge["name"]
        assert human_arc["arc_length"]["length_mm"] > 0.0
        assert human_arc["arc_length"]["arbitrary_display"] is False
        assert "%" in human_arc["arc_length"]["format_spec"]
        assert human_arc["references"] == [
            {
                "view_name": view.Name,
                "subelement": arc_edge["name"],
            }
        ]
        human_name = str(created[0].Name)
        document.undo()
        _events(12)
        assert document.getObject(human_name) is None
        assert document.recompute([view, page], True, True) is not False
        human_projection_after_undo = drawing_projected_geometry_state(view)
        assert (
            human_projection_after_undo["projection_state_sha256"]
            == human_projection_before["projection_state_sha256"]
        ), {
            "before": human_projection_before,
            "after_undo": human_projection_after_undo,
        }
        Gui.Selection.clearSelection()
        first, second, _projection = _chamfer_vertices(view)
        arc_edge = _arc_edge(view)

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-drawing-special-dimension-gui")

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
                f"native-drawing-special-dimension-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        source.ViewObject.Visibility = True
        view.ViewObject.Visibility = True
        Gui.Selection.addSelection(source)
        Gui.Selection.addSelection(view, first["name"])
        selection_before = _selection()
        visibility_before = (
            bool(source.ViewObject.Visibility),
            bool(view.ViewObject.Visibility),
            bool(page.ViewObject.Visibility),
        )
        history_before = tuple(document.VibeCADTimeline.Operations)
        revision_before = state_store.current_revision(str(document.Uid))
        created_names = []
        for operation, direction in zip(
            DRAWING_SPECIAL_DIMENSION_OPERATIONS[:2],
            ("horizontal", "vertical"),
            strict=True,
        ):
            result = call(_arguments(operation, page, view, first, second))
            state = result["dimension"]
            created_names.append(state["object_name"])
            assert result["operation"] == operation
            assert result["geometry_configuration"] == "diagonal"
            assert state["chamfer"]["direction"] == direction
            assert 0 < state["chamfer"]["angle_degrees"] < 180
            assert state["valid"] and state["timeline_usable"]
            assert state["measured_value"]["value"] > 0.0
            assert result["assistant_undo_available"] is True
            assert len(json.dumps(result, separators=(",", ":")).encode()) < 8 * 1024
            assert "elements" not in result
            assert not Gui.Control.activeDialog()

        arc_result = call(_arc_arguments(page, view, arc_edge))
        arc_state = arc_result["dimension"]
        created_names.append(arc_state["object_name"])
        assert arc_result["operation"] == "create_arc_length_dimension"
        assert arc_result["geometry_configuration"] == "circular_arc"
        assert arc_state["arc_length"]["source"]["subelement"] == arc_edge["name"]
        assert arc_state["arc_length"]["length_mm"] > 0.0
        assert arc_state["arc_length"]["arbitrary_display"] is False
        assert "%" in arc_state["arc_length"]["format_spec"]
        assert arc_state["references"] == [
            {
                "view_name": view.Name,
                "subelement": arc_edge["name"],
            }
        ]
        assert arc_state["measured_value"]["value"] == (
            arc_state["arc_length"]["length_mm"]
        )
        assert arc_state["valid"] and arc_state["timeline_usable"]
        assert arc_result["assistant_undo_available"] is True
        assert len(json.dumps(arc_result, separators=(",", ":")).encode()) < 8 * 1024
        assert "elements" not in arc_result
        assert not Gui.Control.activeDialog()

        assert state_store.current_revision(str(document.Uid)) == revision_before + 3
        assert _selection() == selection_before
        assert (
            bool(source.ViewObject.Visibility),
            bool(view.ViewObject.Visibility),
            bool(page.ViewObject.Visibility),
        ) == visibility_before
        dimensions = _dimensions(document)
        assert tuple(obj.Name for obj in dimensions) == tuple(created_names)
        assert tuple(document.VibeCADTimeline.Operations) == (
            *history_before,
            *dimensions,
        )
        assert all(dimension in tuple(view.InList) for dimension in dimensions)

        repeated = _arguments(
            "create_horizontal_chamfer", page, view, first, second
        )
        repeated["second_vertex"] = dict(repeated["first_vertex"])
        rejected = call(repeated, succeeds=False)
        assert rejected["error_code"] == (
            "NATIVE_DRAWING_DIMENSION_REFERENCES_INVALID"
        )

        stale = _arguments("create_vertical_chamfer", page, view, first, second)
        stale["second_vertex"]["expected_element_state_sha256"] = "0" * 64
        rejected = call(stale, succeeds=False)
        assert rejected["error_code"] == "NATIVE_DRAWING_DIMENSION_REFERENCE_STALE"
        assert rejected["repair"]["subelement"] == second["name"]

        invalid_arc = _arc_arguments(page, view, arc_edge)
        linear_edge = next(
            item
            for item in drawing_projected_geometry_state(view)["elements"]
            if item["element_type"] == "edge"
            and item["visible"]
            and "Circle" not in item["geometry_type"]
        )
        invalid_arc["arc_edge"] = _target(linear_edge)
        rejected = call(invalid_arc, succeeds=False)
        assert rejected["error_code"] == (
            "NATIVE_DRAWING_DIMENSION_REFERENCES_INVALID"
        )
        assert rejected["repair"]["accepted_references"]

        rollback = _arc_arguments(page, view, arc_edge)
        rollback_objects = tuple(document.Objects)
        rollback_views = tuple(page.Views)
        rollback_history = tuple(document.VibeCADTimeline.Operations)
        rollback_projection = drawing_projected_geometry_state(view)[
            "projection_state_sha256"
        ]
        rollback_revision = state_store.current_revision(str(document.Uid))
        rollback_undo = int(document.UndoCount)
        original_verify = DimensionRuntimeModule.verify_drawing_arc_length

        def fail_verify(_document, _draft):
            raise RuntimeError("injected Drawing arc-length verification failure")

        DimensionRuntimeModule.verify_drawing_arc_length = fail_verify
        try:
            rejected = call(rollback, succeeds=False)
        finally:
            DimensionRuntimeModule.verify_drawing_arc_length = original_verify
        _events(12)
        assert rejected["error_code"] == "NATIVE_POSTCONDITION_FAILED"
        assert tuple(document.Objects) == rollback_objects
        assert tuple(page.Views) == rollback_views
        assert tuple(document.VibeCADTimeline.Operations) == rollback_history
        assert drawing_projected_geometry_state(view)[
            "projection_state_sha256"
        ] == rollback_projection
        assert state_store.current_revision(str(document.Uid)) == rollback_revision
        assert int(document.UndoCount) == rollback_undo
        assert _selection() == selection_before

        undo_result = call(_arc_arguments(page, view, arc_edge))
        undo_name = undo_result["dimension"]["object_name"]
        undo_state = undo_result["dimension"]
        document.undo()
        _events(12)
        assert document.getObject(undo_name) is None
        document.redo()
        _events(16)
        redone = document.getObject(undo_name)
        assert redone is not None
        assert drawing_arc_length_dimension_state(redone)["state_sha256"] == (
            undo_state["state_sha256"]
        )
        created_names.append(undo_name)

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
        selected = selected_snapshot["domain"]["selected_dimensions"]
        assert len(selected) == 1
        selected_dimension = dict(selected[0])
        repair_target = selected_dimension.pop("repair_target")
        assert selected_dimension == drawing_arc_length_dimension_state(redone)
        repair_state = drawing_dimension_repair_state(redone)
        assert repair_target["expected_repair_state_sha256"] == (
            repair_state["repair_state_sha256"]
        )
        assert repair_target["repair_kind"] == "arc_length"
        assert repair_target["valid"] and repair_target["repairable"]
        page_summary = next(
            item
            for item in selected_snapshot["domain"]["pages"]
            if item["object_name"] == page.Name
        )
        summary = next(
            item for item in page_summary["views"] if item["object_name"] == undo_name
        )
        assert summary["dimension"]["arc_length"] == selected[0]["arc_length"]
        assert summary["dimension"]["repair_target"] == repair_target
        assert len(
            json.dumps(selected_snapshot, separators=(",", ":")).encode()
        ) < 96 * 1024

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
            "VIBECAD_NATIVE_DRAWING_SPECIAL_DIMENSION_GUI_OK "
            "operations=" + ",".join(DRAWING_SPECIAL_DIMENSION_OPERATIONS) + " "
            "human_oracle=true shared_host_builder=true exact_page=true "
            "exact_view=true projection_hash=true element_hash=true "
            "ordered_vertices=true angle_format=true selection=true "
            "arc_source=true arc_value=true direct_arc_reference=true "
            "projection_unchanged=true "
            "visibility=true tree_parent=true history=true rollback=true "
            "revision=true undo=true redo=true snapshot=true reopen=true "
            "low_noise=true no_task=true",
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
