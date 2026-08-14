# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for Drawing parallel/perpendicular lines."""

from __future__ import annotations

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
import VibeCADNativeDrawingCosmeticLineRuntime as LineRuntimeModule
from VibeCADNativeDrawingCosmeticLineSchema import (
    DRAWING_COSMETIC_LINE_CAPABILITY_NAME,
    DRAWING_COSMETIC_LINE_OPERATIONS,
)
from VibeCADNativeDrawingCosmeticLineState import (
    drawing_cosmetic_line_inventory_state,
    normalize_two_point_cosmetic_line_host_plan,
)
from VibeCADNativeDrawingDimensionSupport import drawing_selection_state
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state
from VibeCADNativeDrawingSnapshot import build_drawing_snapshot
from VibeCADNativeDrawingState import drawing_page_state
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


def _run_human_command(command: str) -> None:
    messages = []
    timer = QtCore.QTimer()

    def dismiss_messages() -> None:
        for box in Gui.getMainWindow().findChildren(QtWidgets.QMessageBox):
            messages.append((box.windowTitle(), box.text()))
            box.reject()

    timer.timeout.connect(dismiss_messages)
    timer.start(50)
    try:
        Gui.runCommand(command)
    finally:
        timer.stop()
    _events(20)
    assert not messages, messages


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
    document.openTransaction("Create Drawing cosmetic-line fixture")
    transaction = int(document.getBookedTransactionID())
    try:
        source = document.addObject("Part::Feature", "LineSource")
        source.Label = "Cosmetic Line Source"
        points = (
            App.Vector(-28.0, -14.0, 0.0),
            App.Vector(18.0, 21.0, 0.0),
            App.Vector(34.0, -8.0, 0.0),
            App.Vector(-11.0, 26.0, 0.0),
        )
        source.Shape = Part.makeCompound(
            [
                Part.makeLine(points[0], points[1]),
                Part.makeLine(points[1], points[2]),
                Part.makeLine(points[2], points[3]),
                Part.Wire([Part.makeCircle(6.0, App.Vector(-4.0, 5.0, 0.0))]),
            ]
        )
        document.publishProvisionalTimelineOperationBlock(source, (), ())

        page = document.addObject("TechDraw::DrawPage", "LinePage")
        page.Label = "Cosmetic Line Page"
        template = document.addObject("TechDraw::DrawSVGTemplate", "LineTemplate")
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

        view = document.addObject("TechDraw::DrawViewPart", "LineView")
        view.Label = "Cosmetic Line View"
        view.Source = [source]
        view.Direction = App.Vector(0.0, 0.0, 1.0)
        view.XDirection = App.Vector(1.0, 0.0, 0.0)
        view.ScaleType = "Custom"
        view.Scale = 1.35
        view.Rotation = 17.0
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

    elements = drawing_projected_geometry_state(view)["elements"]
    straight = next(
        item
        for item in elements
        if item["element_type"] == "edge"
        and not item["closed"]
        and "line" in item["geometry_type"].casefold()
        and item["length_view_mm"] > 1.0
    )
    curved = next(
        item
        for item in elements
        if item["element_type"] == "edge"
        and "circle" in item["geometry_type"].casefold()
    )
    vertices = tuple(item for item in elements if item["element_type"] == "vertex")
    assert len(vertices) >= 2
    return source, page, view, straight, curved, vertices[:2]


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(DRAWING_COSMETIC_LINE_CAPABILITY_NAME)
    schema = definition.provider_schema(DRAWING_COSMETIC_LINE_OPERATIONS)
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "selection" not in encoded.casefold()
    assert "start_in_view_mm" not in encoded
    assert "line_format" not in encoded
    assert len(encoded.encode("utf-8")) < 16 * 1024
    branches = schema["parameters"]["oneOf"]
    assert [branch["properties"]["operation"]["const"] for branch in branches] == list(
        DRAWING_COSMETIC_LINE_OPERATIONS
    )
    assert all(branch["additionalProperties"] is False for branch in branches)
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(DRAWING_COSMETIC_LINE_CAPABILITY_NAME,),
            schemas=(schema,),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _element_target(view, name, expected_type) -> dict:
    element = next(
        item
        for item in drawing_projected_geometry_state(view)["elements"]
        if item["name"] == name
    )
    assert element["element_type"] == expected_type
    return {
        "subelement": name,
        "expected_element_state_sha256": element["element_state_sha256"],
    }


def _arguments(page, view, operation, edge_name, vertex_names) -> dict:
    page_state = drawing_page_state(page)
    view_state = drawing_view_state(view)
    projection = drawing_projected_geometry_state(view)
    common = {
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
    if operation == "create_between_vertices":
        return {
            **common,
            "vertices": [
                _element_target(view, name, "vertex") for name in vertex_names
            ],
        }
    return {
        **common,
        "reference_edge": _element_target(view, edge_name, "edge"),
        "through_vertex": _element_target(view, vertex_names[0], "vertex"),
    }


def _selection():
    return tuple(
        (item.Object.Name, tuple(item.SubElementNames))
        for item in Gui.Selection.getSelectionEx()
    )


def _signature(line) -> str:
    return json.dumps(
        {"line": line["line"], "line_format": line["line_format"]},
        sort_keys=True,
        separators=(",", ":"),
    )


def _created_line(before, after):
    old_tags = {item["tag"] for item in before["lines"]}
    created = [item for item in after["lines"] if item["tag"] not in old_tags]
    assert len(created) == 1
    return created[0]


def _human(document, view, command, selection_names):
    before = drawing_cosmetic_line_inventory_state(view)
    Gui.Selection.clearSelection()
    for name in selection_names:
        Gui.Selection.addSelection(view, name)
    _run_human_command(command)
    assert not Gui.Control.activeDialog()
    assert not Gui.Selection.getSelectionEx()
    after = drawing_cosmetic_line_inventory_state(view)
    created = _created_line(before, after)
    document.undo()
    _events(16)
    assert (
        drawing_cosmetic_line_inventory_state(view)["inventory_state_sha256"]
        == before["inventory_state_sha256"]
    )
    return _signature(created)


def _assert_relation(result, construction) -> None:
    reference = result["reference_edge"]
    line = result["line"]["line"]
    through = result["through_vertex"]["point_in_view_mm"]
    ref_vector = (
        reference["end_in_view_mm"]["x_mm"] - reference["start_in_view_mm"]["x_mm"],
        reference["end_in_view_mm"]["y_mm"] - reference["start_in_view_mm"]["y_mm"],
    )
    line_vector = (
        line["end_in_view_mm"]["x_mm"] - line["start_in_view_mm"]["x_mm"],
        line["end_in_view_mm"]["y_mm"] - line["start_in_view_mm"]["y_mm"],
    )
    assert math.isclose(
        math.hypot(*ref_vector),
        line["length_mm"],
        rel_tol=1.0e-10,
        abs_tol=1.0e-8,
    )
    midpoint = (
        (line["start_in_view_mm"]["x_mm"] + line["end_in_view_mm"]["x_mm"]) / 2.0,
        (line["start_in_view_mm"]["y_mm"] + line["end_in_view_mm"]["y_mm"]) / 2.0,
    )
    assert math.isclose(midpoint[0], through["x_mm"], abs_tol=1.0e-8)
    assert math.isclose(midpoint[1], through["y_mm"], abs_tol=1.0e-8)
    cross = ref_vector[0] * line_vector[1] - ref_vector[1] * line_vector[0]
    dot = ref_vector[0] * line_vector[0] + ref_vector[1] * line_vector[1]
    if construction == "parallel":
        assert abs(cross) <= 1.0e-6
    else:
        assert abs(dot) <= 1.0e-6


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-drawing-cosmetic-line-"
        )
        save_path = Path(temporary.name) / "cosmetic-lines.FCStd"
        controller, surface = _surface()
        action_plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        expected_actions = {
            "TechDraw_ExtensionLineParallel": (
                "create_parallel",
                "ExactDrawingStraightEdgeAndThroughVertexParallelLine",
            ),
            "TechDraw_ExtensionLinePerpendicular": (
                "create_perpendicular",
                "ExactDrawingStraightEdgeAndThroughVertexPerpendicularLine",
            ),
            "TechDraw_2PointCosmeticLine": (
                "create_between_vertices",
                "ExactDrawingVertexPairAndCosmeticLine",
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
                DRAWING_COSMETIC_LINE_CAPABILITY_NAME,
                operation,
                target_type,
                "document",
                False,
            )

        document = App.newDocument("NativeDrawingCosmeticLineGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        source, page, view, straight, curved, vertices = _create_fixture(document)
        edge_name = straight["name"]
        curved_name = curved["name"]
        vertex_names = tuple(item["name"] for item in vertices)
        objects_before = tuple(document.Objects)
        views_before = tuple(page.Views)
        history_before = tuple(document.VibeCADTimeline.Operations)
        visibility_before = tuple(
            bool(obj.ViewObject.Visibility) for obj in (source, view, page)
        )
        human = {
            "create_parallel": _human(
                document,
                view,
                "TechDraw_ExtensionLineParallel",
                (edge_name, vertex_names[0]),
            ),
            "create_perpendicular": _human(
                document,
                view,
                "TechDraw_ExtensionLinePerpendicular",
                (vertex_names[0], edge_name),
            ),
            "create_between_vertices": _signature(
                normalize_two_point_cosmetic_line_host_plan(
                    TechDrawGui.validateDrawingTwoPointCosmeticLine(
                        view, list(vertex_names)
                    ),
                    created=False,
                )
            ),
        }
        assert drawing_cosmetic_line_inventory_state(view)["line_count"] == 0

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-drawing-cosmetic-line-gui")

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
        Gui.Selection.addSelection(view, edge_name)
        Gui.Selection.addSelection(view, vertex_names[0])
        selection_before = _selection()
        snapshot = build_drawing_snapshot(
            document,
            selection=drawing_selection_state(document),
        )
        selected_names = snapshot["selected_projected_geometry"][0]["selected_elements"]
        assert [item["name"] for item in selected_names] == [
            edge_name,
            vertex_names[0],
        ]

        responses = {}
        first_arguments = None
        for index, operation in enumerate(DRAWING_COSMETIC_LINE_OPERATIONS):
            arguments = _arguments(page, view, operation, edge_name, vertex_names)
            if first_arguments is None:
                first_arguments = arguments
            revision = state_store.current_revision(str(document.Uid))
            response = dispatcher.call(
                DRAWING_COSMETIC_LINE_CAPABILITY_NAME,
                json.dumps(arguments),
                f"native-drawing-cosmetic-line-{index}",
            )
            assert response["ok"] is True, response
            assert response["operation"] == operation
            result = response["cosmetic_line"]
            assert _signature(result["line"]) == human[operation]
            if operation == "create_between_vertices":
                assert [
                    item["subelement"] for item in result["source_vertices"]
                ] == list(vertex_names)
            else:
                assert result["reference_edge"]["subelement"] == edge_name
                assert result["through_vertex"]["subelement"] == vertex_names[0]
                _assert_relation(result, operation.removeprefix("create_"))
            assert state_store.current_revision(str(document.Uid)) == revision + 1
            assert _selection() == selection_before
            assert not Gui.Control.activeDialog()
            assert len(json.dumps(response, separators=(",", ":")).encode()) < 4096
            responses[operation] = response

        assert first_arguments is not None
        repeated = dispatcher.call(
            DRAWING_COSMETIC_LINE_CAPABILITY_NAME,
            json.dumps(first_arguments),
            "native-drawing-cosmetic-line-0",
        )
        assert repeated == responses["create_parallel"]
        inventory = drawing_cosmetic_line_inventory_state(view)
        assert inventory["line_count"] == 3
        all_tags = {item["tag"] for item in inventory["lines"]}

        refusal_revision = state_store.current_revision(str(document.Uid))
        refusal_undo = int(document.UndoCount)
        stale = dispatcher.call(
            DRAWING_COSMETIC_LINE_CAPABILITY_NAME,
            json.dumps(first_arguments),
            "native-drawing-cosmetic-line-stale",
        )
        assert stale["ok"] is False
        assert stale["error_code"] in {
            "NATIVE_DRAWING_COSMETIC_LINE_VIEW_STALE",
            "NATIVE_DRAWING_COSMETIC_LINE_PROJECTION_STALE",
        }
        curved_arguments = _arguments(
            page,
            view,
            "create_parallel",
            curved_name,
            vertex_names,
        )
        curved_response = dispatcher.call(
            DRAWING_COSMETIC_LINE_CAPABILITY_NAME,
            json.dumps(curved_arguments),
            "native-drawing-cosmetic-line-curved",
        )
        assert curved_response["ok"] is False
        assert (
            curved_response["error_code"]
            == "NATIVE_DRAWING_COSMETIC_LINE_REFERENCES_INVALID"
        )
        wrong_type = _arguments(
            page,
            view,
            "create_parallel",
            edge_name,
            vertex_names,
        )
        wrong_type["reference_edge"] = wrong_type["through_vertex"]
        wrong_response = dispatcher.call(
            DRAWING_COSMETIC_LINE_CAPABILITY_NAME,
            json.dumps(wrong_type),
            "native-drawing-cosmetic-line-wrong-type",
        )
        assert wrong_response["ok"] is False
        assert wrong_response["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert state_store.current_revision(str(document.Uid)) == refusal_revision
        assert int(document.UndoCount) == refusal_undo
        assert (
            drawing_cosmetic_line_inventory_state(view)["inventory_state_sha256"]
            == inventory["inventory_state_sha256"]
        )

        rollback_revision = state_store.current_revision(str(document.Uid))
        rollback_undo = int(document.UndoCount)
        original_verify = LineRuntimeModule.verify_drawing_cosmetic_line

        def fail_verify(_document, _draft):
            raise RuntimeError("Injected cosmetic-line verification failure")

        LineRuntimeModule.verify_drawing_cosmetic_line = fail_verify
        try:
            rollback = dispatcher.call(
                DRAWING_COSMETIC_LINE_CAPABILITY_NAME,
                json.dumps(
                    _arguments(
                        page,
                        view,
                        "create_parallel",
                        edge_name,
                        vertex_names,
                    )
                ),
                "native-drawing-cosmetic-line-rollback",
            )
        finally:
            LineRuntimeModule.verify_drawing_cosmetic_line = original_verify
        _events(16)
        assert rollback["ok"] is False
        assert rollback["error_code"] == "NATIVE_POSTCONDITION_FAILED"
        assert (
            drawing_cosmetic_line_inventory_state(view)["inventory_state_sha256"]
            == inventory["inventory_state_sha256"]
        )
        assert state_store.current_revision(str(document.Uid)) == rollback_revision
        assert int(document.UndoCount) == rollback_undo
        assert _selection() == selection_before

        assert tuple(document.Objects) == objects_before
        assert tuple(page.Views) == views_before
        assert tuple(document.VibeCADTimeline.Operations) == history_before
        assert visibility_before == tuple(
            bool(obj.ViewObject.Visibility) for obj in (source, view, page)
        )

        document.undo()
        _events(16)
        assert drawing_cosmetic_line_inventory_state(view)["line_count"] == 2
        document.redo()
        _events(16)
        redone = drawing_cosmetic_line_inventory_state(view)
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
        reopened = drawing_cosmetic_line_inventory_state(view)
        assert reopened["inventory_state_sha256"] == redone["inventory_state_sha256"]
        assert {item["tag"] for item in reopened["lines"]} == all_tags
        for tag in all_tags:
            assert TechDrawGui.drawingPersistentCosmeticLine(view, tag)["tag"] == tag

        print(
            "VIBECAD_NATIVE_DRAWING_COSMETIC_LINE_GUI_OK operations=3 "
            "parallel=true perpendicular=true between_vertices=true human_oracle=true "
            "shared_host_builder=true selection_order=true exact_page=true "
            "exact_view=true projection_hash=true element_hash=true named_roles=true "
            "derived_geometry=true same_length=true centered=true host_style=true "
            "persistent_tags=true selection=true visibility=true history=true "
            "curved_refusal=true wrong_type=true stale=true rollback=true revision=true "
            "idempotency=true undo=true redo=true snapshot=true reopen=true "
            "low_noise=true native_no_task=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        Gui.Selection.clearSelection()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
