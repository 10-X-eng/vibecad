# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact Native standard Drawing views."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
import VibeCADNativeDrawingViewRuntime as DrawingViewRuntimeModule
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeBackgroundSchema import NATIVE_BACKGROUND_CAPABILITY_NAME
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewState import (
    drawing_source_catalog_identity_state,
    drawing_source_state,
    drawing_view_state,
)
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSnapshot import build_active_snapshot
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


DRAWING_CREATE_PAGE_CAPABILITY_NAME = "drawing.create_page"
DRAWING_STANDARD_VIEW_CAPABILITY_NAME = "drawing.standard_view"


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


def _create_source(document):
    document.openTransaction("Create exact Drawing source")
    transaction = int(document.getBookedTransactionID())
    try:
        source = document.addObject("Part::Feature", "DrawingSource")
        source.Label = "Drawing Source"
        source.Shape = Part.makeBox(36.0, 24.0, 12.0)
        document.publishProvisionalTimelineOperationBlock(source, (), ())
        assert document.recompute([source], True, True) is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    return source


def _turn(surface, registry) -> NativeTurnSnapshot:
    page_definition = registry.definition(DRAWING_CREATE_PAGE_CAPABILITY_NAME)
    view_definition = registry.definition(DRAWING_STANDARD_VIEW_CAPABILITY_NAME)
    job_definition = registry.definition(NATIVE_BACKGROUND_CAPABILITY_NAME)
    assert all(
        item is not None
        for item in (page_definition, view_definition, job_definition)
    )
    page_schema = page_definition.provider_schema(("page_default",))
    view_schema = view_definition.provider_schema(("create_standard_view",))
    encoded = json.dumps(view_schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "path" not in encoded.casefold()
    assert "expected_state_sha256" in encoded
    assert "orientation" in encoded
    assert "position" in encoded
    assert "line_style" in encoded
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(
                DRAWING_CREATE_PAGE_CAPABILITY_NAME,
                DRAWING_STANDARD_VIEW_CAPABILITY_NAME,
                NATIVE_BACKGROUND_CAPABILITY_NAME,
            ),
            schemas=(
                page_schema,
                view_schema,
                job_definition.provider_schema(("status", "cancel")),
            ),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _arguments(page_state: dict, source_state: dict) -> dict:
    return {
        "operation": "create_standard_view",
        "label": "Native Front View",
        "page": {
            "object_name": page_state["object_name"],
            "expected_state_sha256": page_state["state_sha256"],
        },
        "sources": [
            {
                "object_name": source_state["object_name"],
                "expected_state_sha256": source_state["state_sha256"],
            }
        ],
        "orientation": "front",
        "position": {"x_mm": 92.0, "y_mm": 88.0},
        "line_style": "visible",
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-drawing-standard-view-"
        )
        save_path = Path(temporary.name) / "native-drawing-standard-view.FCStd"
        controller, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        view_plan = plans["TechDraw_View"]
        assert (
            view_plan.capability_family,
            view_plan.operation_variant,
            view_plan.exact_target_type,
            view_plan.transaction_behavior,
            view_plan.background_required,
        ) == (
            DRAWING_STANDARD_VIEW_CAPABILITY_NAME,
            "create_standard_view",
            "ExactDrawingPageSourcesAndProjectionSettings",
            "background",
            True,
        )

        document = App.newDocument("NativeDrawingStandardViewGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        source = _create_source(document)
        source.ViewObject.Visibility = True
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source, "Face1")
        selection_before = _selection()
        visibility_before = bool(source.ViewObject.Visibility)

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-drawing-standard-view-gui")

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
            background_manager=service.native_background_manager(),
            document_thread_dispatch=VibeGui._dispatch_to_document_thread,
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

        def call(tool_name: str, arguments: dict, *, succeeds: bool = True) -> dict:
            nonlocal call_index
            call_index += 1
            response = dispatcher.call(
                tool_name,
                json.dumps(arguments, separators=(",", ":")),
                f"native-drawing-standard-view-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        def wait_for_job(job_id: str, *, timeout: float = 30.0) -> dict:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                _events(2)
                snapshot = context.background_manager.snapshot(job_id)
                if snapshot.terminal:
                    return call(
                        NATIVE_BACKGROUND_CAPABILITY_NAME,
                        {"operation": "status", "job_id": job_id},
                    )["job"]
                time.sleep(0.01)
            raise AssertionError(f"Background Drawing job {job_id} did not finish")

        page_result = call(
            DRAWING_CREATE_PAGE_CAPABILITY_NAME,
            {"operation": "page_default"},
        )
        _events(12)
        page = document.getObject(page_result["page"]["object_name"])
        assert page is not None
        page_state = drawing_page_state(page)
        source_state = drawing_source_state(source)

        active = build_active_snapshot(
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
                            "object_name": source.Name,
                            "type_id": source.TypeId,
                        },
                        "subelements": ["Face1"],
                    }
                ],
            },
        )
        assert active["domain"]["active_page_resolution"] == "only_page"
        assert active["domain"]["active_page"]["state_sha256"] == page_state["state_sha256"]
        assert active["domain"]["selected_sources"] == [
            drawing_source_catalog_identity_state(source)
        ]
        assert len(json.dumps(active, separators=(",", ":")).encode()) < 64 * 1024

        invalid = _arguments(page_state, source_state)
        invalid["camera_direction"] = [0.0, 0.0, 1.0]
        rejected = call(DRAWING_STANDARD_VIEW_CAPABILITY_NAME, invalid, succeeds=False)
        assert rejected["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(getattr(page, "Views", ()) or ()) == ()

        stale_source = _arguments(page_state, source_state)
        stale_source["sources"][0]["expected_state_sha256"] = "0" * 64
        rejected = call(DRAWING_STANDARD_VIEW_CAPABILITY_NAME, stale_source, succeeds=False)
        assert rejected["error_code"] == "NATIVE_DRAWING_VIEW_SOURCE_STALE"

        duplicate_source = _arguments(page_state, source_state)
        duplicate_source["sources"].append(dict(duplicate_source["sources"][0]))
        rejected = call(DRAWING_STANDARD_VIEW_CAPABILITY_NAME, duplicate_source, succeeds=False)
        assert rejected["error_code"] == "NATIVE_DRAWING_VIEW_SOURCES_INVALID"

        arguments = _arguments(page_state, source_state)
        original_verify = DrawingViewRuntimeModule.verify_standard_view_create

        def fail_verify(_document, _draft):
            raise RuntimeError("injected standard Drawing view failure")

        DrawingViewRuntimeModule.verify_standard_view_create = fail_verify
        try:
            rolled_back_start = call(DRAWING_STANDARD_VIEW_CAPABILITY_NAME, arguments)
            rolled_back = wait_for_job(rolled_back_start["job"]["job_id"])
        finally:
            DrawingViewRuntimeModule.verify_standard_view_create = original_verify
        assert rolled_back["phase"] == "failed", rolled_back
        assert rolled_back["failure"]["error_code"] == "NATIVE_POSTCONDITION_FAILED"
        assert tuple(getattr(page, "Views", ()) or ()) == ()
        assert document.getObject("View") is None

        cancelled_start = call(DRAWING_STANDARD_VIEW_CAPABILITY_NAME, arguments)
        cancelled_request = call(
            NATIVE_BACKGROUND_CAPABILITY_NAME,
            {
                "operation": "cancel",
                "job_id": cancelled_start["job"]["job_id"],
            },
        )
        assert cancelled_request["cancel_accepted"] is True, cancelled_request
        cancelled = wait_for_job(cancelled_start["job"]["job_id"])
        assert cancelled["phase"] == "cancelled", cancelled
        assert tuple(getattr(page, "Views", ()) or ()) == ()
        assert document.getObject("View") is None

        undo_before = int(document.UndoCount)
        ui_ticks = 0
        heartbeat = QtCore.QTimer()
        heartbeat.setInterval(5)

        def tick() -> None:
            nonlocal ui_ticks
            ui_ticks += 1

        heartbeat.timeout.connect(tick)
        heartbeat.start()
        started_at = time.monotonic()
        started = call(DRAWING_STANDARD_VIEW_CAPABILITY_NAME, arguments)
        returned_in = time.monotonic() - started_at
        assert returned_in < 2.0, returned_in
        completed = wait_for_job(started["job"]["job_id"])
        heartbeat.stop()
        assert completed["phase"] == "completed", completed
        assert ui_ticks > 0, ui_ticks
        result = completed["result"]
        _events(16)
        view_name = result["view"]["object_name"]
        view = document.getObject(view_name)
        assert view is not None
        state = drawing_view_state(view)
        assert state == result["view"]
        assert state["type_id"] == "TechDraw::DrawViewPart"
        assert state["page_name"] == page.Name
        assert state["source_states"] == [
            {
                "object_name": source.Name,
                "state_sha256": source_state["state_sha256"],
            }
        ]
        assert state["direction"] == [0.0, -1.0, 0.0]
        assert state["x_direction"] == [1.0, 0.0, 0.0]
        assert state["x_mm"] == 92.0 and state["y_mm"] == 88.0
        assert state["scale_type"] == "Page"
        assert state["visible_edge_count"] >= 1
        assert str(view.VibeCADTimelineRole) == "operation"
        assert getattr(view, "VibeCADTimelineOwner", None) is None
        assert tuple(page.Views) == (view,)
        assert int(document.UndoCount) == undo_before + 1
        assert _selection() == selection_before
        assert bool(source.ViewObject.Visibility) is visibility_before
        assert not Gui.Control.activeDialog()
        assert "path" not in json.dumps(result).casefold()
        operations_after = tuple(document.VibeCADTimeline.Operations)

        stale_page = _arguments(page_state, source_state)
        rejected = call(DRAWING_STANDARD_VIEW_CAPABILITY_NAME, stale_page, succeeds=False)
        assert rejected["error_code"] == "NATIVE_DRAWING_PAGE_STALE"

        document.undo()
        _events(12)
        assert document.getObject(view_name) is None
        page = document.getObject(page.Name)
        assert page is not None and tuple(page.Views) == ()
        document.redo()
        _events(12)
        view = document.getObject(view_name)
        page = document.getObject(page.Name)
        assert view is not None and tuple(page.Views) == (view,)
        assert drawing_view_state(view) == state
        assert tuple(document.VibeCADTimeline.Operations) == operations_after

        page_name = str(page.Name)
        cached_projection_state = str(view.PrecomputedProjectionSourceState)
        assert cached_projection_state
        document.saveAs(str(save_path))
        document_name = document.Name
        App.closeDocument(document_name)
        document = App.openDocument(str(save_path))
        _events(16)
        reopened_view = document.getObject(view_name)
        reopened_page = document.getObject(page_name)
        assert reopened_view is not None and reopened_page is not None
        assert tuple(reopened_page.Views) == (reopened_view,)
        assert drawing_view_state(reopened_view) == state
        assert str(reopened_view.VibeCADTimelineRole) == "operation"
        assert str(reopened_view.PrecomputedProjectionSourceState) == cached_projection_state

        print(
            "VIBECAD_NATIVE_DRAWING_STANDARD_VIEW_GUI_OK "
            "exact_page=true exact_sources=true selected_sources=true "
            "closed_schema=true deterministic_orientation=true placement=true "
            "scale=true line_style=true projected_geometry=true no_task=true "
            "stale_page=true stale_source=true duplicate_guard=true "
            "rollback=true cancel=true selection=true visibility=true history=true "
            "undo=true redo=true reopen=true responsive=true path_private=true "
            "low_noise=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
