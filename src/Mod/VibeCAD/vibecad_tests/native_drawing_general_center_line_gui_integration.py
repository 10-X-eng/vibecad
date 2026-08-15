# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for face, edge-pair, and vertex-pair centerlines."""

from __future__ import annotations

import itertools
import json
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
from VibeCADNativeDrawingDimensionSupport import drawing_selection_state
import VibeCADNativeDrawingGeneralCenterLineRuntime as RuntimeModule
from VibeCADNativeDrawingGeneralCenterLineSchema import (
    DRAWING_GENERAL_CENTER_LINE_CAPABILITY_NAME,
    DRAWING_GENERAL_CENTER_LINE_OPERATIONS,
)
from VibeCADNativeDrawingGeneralCenterLineState import (
    drawing_general_center_line_inventory_state,
    normalize_general_center_line_host_plan,
)
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


def _events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _surface():
    Gui.activateWorkbench("TechDrawWorkbench")
    _events(24)
    controller = Gui.getMainWindow().findChild(
        QtCore.QObject, "VibeCADRibbonController"
    )
    assert controller is not None
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "drawing"
    return controller, surface


def _create_fixture(document):
    document.openTransaction("Create Drawing general-centerline fixture")
    transaction = int(document.getBookedTransactionID())
    try:
        source = document.addObject("Part::Feature", "GeneralCenterLineSource")
        source.Shape = Part.makeBox(40.0, 24.0, 12.0)
        document.publishProvisionalTimelineOperationBlock(source, (), ())
        page = document.addObject("TechDraw::DrawPage", "GeneralCenterLinePage")
        template = document.addObject(
            "TechDraw::DrawSVGTemplate", "GeneralCenterLineTemplate"
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
        view = document.addObject(
            "TechDraw::DrawViewPart", "GeneralCenterLineView"
        )
        view.Source = [source]
        view.Direction = App.Vector(0.0, 0.0, 1.0)
        view.XDirection = App.Vector(1.0, 0.0, 0.0)
        view.ScaleType = "Custom"
        view.Scale = 1.25
        view.Rotation = 13.0
        view.X = 110.0
        view.Y = 78.0
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
    faces = [item for item in projection["elements"] if item["element_type"] == "face"]
    edges = [item for item in projection["elements"] if item["element_type"] == "edge"]
    vertices = [
        item for item in projection["elements"] if item["element_type"] == "vertex"
    ]
    assert faces and len(edges) >= 2 and len(vertices) >= 2

    edge_names = next(
        pair
        for pair in itertools.combinations((item["name"] for item in edges), 2)
        if TechDrawGui.validateDrawingGeneralCenterLine(
            view, "between_edges", list(pair)
        )["line"]["length_mm"]
        > 0.0
    )
    vertex_names = next(
        pair
        for pair in itertools.combinations((item["name"] for item in vertices), 2)
        if TechDrawGui.validateDrawingGeneralCenterLine(
            view, "between_vertices", list(pair)
        )["line"]["length_mm"]
        > 0.0
    )
    return source, page, view, (faces[0]["name"],), edge_names, vertex_names


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(DRAWING_GENERAL_CENTER_LINE_CAPABILITY_NAME)
    schema = definition.provider_schema(DRAWING_GENERAL_CENTER_LINE_OPERATIONS)
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert len(encoded.encode()) < 12 * 1024
    branches = schema["parameters"]["oneOf"]
    assert [branch["properties"]["operation"]["const"] for branch in branches] == list(
        DRAWING_GENERAL_CENTER_LINE_OPERATIONS
    )
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(DRAWING_GENERAL_CENTER_LINE_CAPABILITY_NAME,),
            schemas=(schema,),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _arguments(page, view, operation, names) -> dict:
    page_state = drawing_page_state(page)
    view_state = drawing_view_state(view)
    projection = drawing_projected_geometry_state(view)
    by_name = {item["name"]: item for item in projection["elements"]}
    field = {
        "create_face": "faces",
        "create_between_edges": "edges",
        "create_between_vertices": "vertices",
    }[operation]
    return {
        "operation": operation,
        "page": {
            "object_name": page.Name,
            "expected_state_sha256": page_state["state_sha256"],
        },
        "view": {
            "object_name": view.Name,
            "expected_state_sha256": view_state["state_sha256"],
            "expected_projection_state_sha256": projection[
                "projection_state_sha256"
            ],
        },
        field: [
            {
                "subelement": name,
                "expected_element_state_sha256": by_name[name][
                    "element_state_sha256"
                ],
            }
            for name in names
        ],
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-drawing-general-centerline-"
        )
        save_path = Path(temporary.name) / "general-centerlines.FCStd"
        controller, surface = _surface()
        action_plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        expected_actions = {
            "TechDraw_FaceCenterLine": (
                "create_face",
                "ExactDrawingFacesAndDerivedCenterLine",
            ),
            "TechDraw_2LineCenterLine": (
                "create_between_edges",
                "ExactDrawingEdgePairAndDerivedCenterLine",
            ),
            "TechDraw_2PointCenterLine": (
                "create_between_vertices",
                "ExactDrawingVertexPairAndDerivedCenterLine",
            ),
        }
        for action_id, (operation, target_type) in expected_actions.items():
            plan = action_plans[action_id]
            assert (
                plan.capability_family,
                plan.operation_variant,
                plan.exact_target_type,
                plan.transaction_behavior,
                plan.background_required,
            ) == (
                DRAWING_GENERAL_CENTER_LINE_CAPABILITY_NAME,
                operation,
                target_type,
                "document",
                False,
            )

        document = App.newDocument("NativeDrawingGeneralCenterLineGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        source, page, view, faces, edges, vertices = _create_fixture(document)
        source_sets = {
            "create_face": faces,
            "create_between_edges": edges,
            "create_between_vertices": vertices,
        }
        objects_before = tuple(document.Objects)
        views_before = tuple(page.Views)
        history_before = tuple(document.VibeCADTimeline.Operations)
        selection_before = tuple(Gui.Selection.getSelectionEx())

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-drawing-general-centerline-gui")

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

        responses = {}
        for index, operation in enumerate(DRAWING_GENERAL_CENTER_LINE_OPERATIONS):
            names = source_sets[operation]
            kind = operation.removeprefix("create_")
            oracle = normalize_general_center_line_host_plan(
                TechDrawGui.validateDrawingGeneralCenterLine(
                    view, kind, list(names)
                ),
                created=False,
            )
            revision = state_store.current_revision(str(document.Uid))
            response = dispatcher.call(
                DRAWING_GENERAL_CENTER_LINE_CAPABILITY_NAME,
                json.dumps(_arguments(page, view, operation, names)),
                f"native-drawing-general-centerline-{index}",
            )
            assert response["ok"] is True, response
            assert response["operation"] == operation
            result = response["centerline"]
            assert result["centerline"]["line"] == oracle["line"]
            assert result["centerline"]["settings"] == oracle["settings"]
            assert [item["subelement"] for item in result["sources"]] == list(names)
            assert state_store.current_revision(str(document.Uid)) == revision + 1
            assert tuple(Gui.Selection.getSelectionEx()) == selection_before
            assert len(json.dumps(response, separators=(",", ":")).encode()) < 4096
            responses[operation] = response

        inventory = drawing_general_center_line_inventory_state(view)
        assert inventory["centerline_count"] == 3
        tags = {item["centerline_tag"] for item in inventory["centerlines"]}
        assert len(tags) == 3
        assert tuple(document.Objects) == objects_before
        assert tuple(page.Views) == views_before
        assert tuple(document.VibeCADTimeline.Operations) == history_before

        duplicate = _arguments(page, view, "create_between_vertices", vertices)
        duplicate["vertices"][1] = dict(duplicate["vertices"][0])
        refusal_revision = state_store.current_revision(str(document.Uid))
        refusal_undo = int(document.UndoCount)
        refused = dispatcher.call(
            DRAWING_GENERAL_CENTER_LINE_CAPABILITY_NAME,
            json.dumps(duplicate),
            "native-drawing-general-centerline-duplicate",
        )
        assert refused["ok"] is False
        assert state_store.current_revision(str(document.Uid)) == refusal_revision
        assert int(document.UndoCount) == refusal_undo

        original_verify = RuntimeModule.verify_drawing_general_center_line

        def fail_verify(_document, _draft):
            raise RuntimeError("Injected general-centerline verification failure")

        RuntimeModule.verify_drawing_general_center_line = fail_verify
        try:
            rollback = dispatcher.call(
                DRAWING_GENERAL_CENTER_LINE_CAPABILITY_NAME,
                json.dumps(_arguments(page, view, "create_face", faces)),
                "native-drawing-general-centerline-rollback",
            )
        finally:
            RuntimeModule.verify_drawing_general_center_line = original_verify
        assert rollback["ok"] is False
        assert rollback["error_code"] == "NATIVE_POSTCONDITION_FAILED"
        assert (
            drawing_general_center_line_inventory_state(view)[
                "inventory_state_sha256"
            ]
            == inventory["inventory_state_sha256"]
        )

        document.undo()
        _events(16)
        assert drawing_general_center_line_inventory_state(view)[
            "centerline_count"
        ] == 2
        document.redo()
        _events(16)
        redone = drawing_general_center_line_inventory_state(view)
        assert redone["inventory_state_sha256"] == inventory["inventory_state_sha256"]

        document.saveAs(str(save_path))
        names = {"page": page.Name, "view": view.Name}
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        page = document.getObject(names["page"])
        view = document.getObject(names["view"])
        assert page is not None and view is not None
        page.ViewObject.show()
        assert document.recompute([view, page], True, True) is not False
        _events(24)
        reopened = drawing_general_center_line_inventory_state(view)
        assert reopened["inventory_state_sha256"] == redone["inventory_state_sha256"]
        assert {
            item["centerline_tag"] for item in reopened["centerlines"]
        } == tags
        for tag in tags:
            assert TechDrawGui.drawingPersistentGeneralCenterLine(view, tag)[
                "centerline_tag"
            ] == tag

        print(
            "VIBECAD_NATIVE_DRAWING_GENERAL_CENTER_LINE_GUI_OK operations=3 "
            "face=true between_edges=true between_vertices=true "
            "shared_host_builder=true exact_page=true exact_view=true "
            "projection_hash=true element_hash=true host_defaults=true "
            "persistent_tags=true selection=true history=true duplicate=true "
            "rollback=true revision=true undo=true redo=true reopen=true "
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
