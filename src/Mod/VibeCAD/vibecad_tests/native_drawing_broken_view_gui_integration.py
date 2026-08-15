# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact Native broken Drawing views."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets
import Sketcher  # noqa: F401 - registers Sketcher::SketchObject

import VibeCADGui as VibeGui
import VibeCADNativeDrawingViewRuntime as DrawingViewRuntimeModule
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeBackgroundSchema import NATIVE_BACKGROUND_CAPABILITY_NAME
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeDrawingPageSchema import DRAWING_PAGE_CAPABILITY_NAME
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewSchema import DRAWING_VIEW_CAPABILITY_NAME
from VibeCADNativeDrawingViewState import (
    drawing_break_state,
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


def _create_inputs(document):
    document.openTransaction("Create exact broken-view inputs")
    transaction = int(document.getBookedTransactionID())
    try:
        source = document.addObject("Part::Feature", "DrawingSource")
        source.Label = "Drawing Source"
        source.Shape = Part.makeBox(60.0, 30.0, 12.0)
        document.publishProvisionalTimelineOperationBlock(source, (), ())
        edge_break = document.addObject("Part::Feature", "EdgeBreak")
        edge_break.Label = "Single-Edge Break Definition"
        edge_break.Shape = Part.makeLine(
            App.Vector(0.0, 10.0, 0.0),
            App.Vector(0.0, 20.0, 0.0),
        )
        document.publishProvisionalTimelineOperationBlock(edge_break, (), ())
        break_sketch = document.addObject("Sketcher::SketchObject", "BreakSketch")
        break_sketch.Label = "Two-Line Break Definition"
        break_sketch.addGeometry(
            Part.LineSegment(
                App.Vector(20.0, -5.0, 0.0),
                App.Vector(20.0, 35.0, 0.0),
            ),
            False,
        )
        break_sketch.addGeometry(
            Part.LineSegment(
                App.Vector(40.0, -5.0, 0.0),
                App.Vector(40.0, 35.0, 0.0),
            ),
            False,
        )
        document.publishProvisionalTimelineOperationBlock(break_sketch, (), ())
        assert document.recompute(
            [source, edge_break, break_sketch],
            True,
            True,
        ) is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    return source, break_sketch, edge_break


def _turn(surface, registry) -> NativeTurnSnapshot:
    page_definition = registry.definition(DRAWING_PAGE_CAPABILITY_NAME)
    view_definition = registry.definition(DRAWING_VIEW_CAPABILITY_NAME)
    job_definition = registry.definition(NATIVE_BACKGROUND_CAPABILITY_NAME)
    assert all(
        item is not None
        for item in (page_definition, view_definition, job_definition)
    )
    page_schema = page_definition.provider_schema(("page_default",))
    view_schema = view_definition.provider_schema(("create_broken_view",))
    encoded = json.dumps(view_schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "path" not in encoded.casefold()
    assert "expected_state_sha256" in encoded
    assert "breaks" in encoded and "gap_mm" in encoded
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(
                DRAWING_PAGE_CAPABILITY_NAME,
                DRAWING_VIEW_CAPABILITY_NAME,
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


def _arguments(
    page_state: dict,
    source_state: dict,
    break_state: dict,
    edge_break_state: dict,
) -> dict:
    return {
        "operation": "create_broken_view",
        "label": "Native Broken Top View",
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
        "breaks": [
            {
                "object_name": break_state["object_name"],
                "expected_state_sha256": break_state["state_sha256"],
            },
            {
                "object_name": edge_break_state["object_name"],
                "expected_state_sha256": edge_break_state["state_sha256"],
            },
        ],
        "gap_mm": 5.0,
        "orientation": "top",
        "position": {"x_mm": 96.0, "y_mm": 82.0},
        "scale": {"kind": "custom", "value": 1.0},
        "line_style": "visible_and_hidden",
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-drawing-broken-view-"
        )
        save_path = Path(temporary.name) / "native-drawing-broken-view.FCStd"
        controller, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["TechDraw_BrokenView"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.transaction_behavior,
            plan.background_required,
        ) == (
            DRAWING_VIEW_CAPABILITY_NAME,
            "create_broken_view",
            "ExactDrawingPageSourcesBreakDefinitionsAndProjectionSettings",
            "background",
            True,
        )

        document = App.newDocument("NativeDrawingBrokenViewGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        source, break_sketch, edge_break = _create_inputs(document)
        source.ViewObject.Visibility = True
        break_sketch.ViewObject.Visibility = True
        edge_break.ViewObject.Visibility = True
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source)
        Gui.Selection.addSelection(break_sketch)
        Gui.Selection.addSelection(edge_break)
        selection_before = _selection()
        visibility_before = (
            bool(source.ViewObject.Visibility),
            bool(break_sketch.ViewObject.Visibility),
            bool(edge_break.ViewObject.Visibility),
        )

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-drawing-broken-view-gui")

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
                f"native-drawing-broken-view-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        def wait_for_job(job_id: str, *, timeout: float = 45.0) -> dict:
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
            raise AssertionError(f"Background broken-view job {job_id} did not finish")

        page_result = call(
            DRAWING_PAGE_CAPABILITY_NAME,
            {"operation": "page_default"},
        )
        _events(12)
        page = document.getObject(page_result["page"]["object_name"])
        assert page is not None
        page_state = drawing_page_state(page)
        source_state = drawing_source_state(source)
        break_state = drawing_break_state(break_sketch)
        edge_break_state = drawing_break_state(edge_break)

        selection_items = [
            {
                "object": {
                    "document_uid": str(document.Uid),
                    "object_name": obj.Name,
                    "type_id": obj.TypeId,
                },
                "subelements": [],
            }
            for obj in (source, break_sketch, edge_break)
        ]
        active = build_active_snapshot(
            document,
            "drawing",
            state_store.snapshot(str(document.Uid)),
            selection={
                "document_uid": str(document.Uid),
                "selected_count": 3,
                "items": selection_items,
            },
        )
        domain = active["domain"]
        assert domain["active_page"]["state_sha256"] == page_state["state_sha256"]
        assert domain["selected_break_definitions"] == [
            break_state,
            edge_break_state,
        ]
        assert len(json.dumps(active, separators=(",", ":")).encode()) < 64 * 1024

        invalid = _arguments(page_state, source_state, break_state, edge_break_state)
        invalid["file_path"] = "/tmp/forbidden.FCStd"
        rejected = call(DRAWING_VIEW_CAPABILITY_NAME, invalid, succeeds=False)
        assert rejected["error_code"] == "NATIVE_ARGUMENTS_INVALID"

        stale_break = _arguments(
            page_state,
            source_state,
            break_state,
            edge_break_state,
        )
        stale_break["breaks"][0]["expected_state_sha256"] = "0" * 64
        rejected = call(DRAWING_VIEW_CAPABILITY_NAME, stale_break, succeeds=False)
        assert rejected["error_code"] == "NATIVE_DRAWING_BREAK_STALE"

        duplicate = _arguments(
            page_state,
            source_state,
            break_state,
            edge_break_state,
        )
        duplicate["breaks"].append(dict(duplicate["breaks"][0]))
        rejected = call(DRAWING_VIEW_CAPABILITY_NAME, duplicate, succeeds=False)
        assert rejected["error_code"] == "NATIVE_DRAWING_BREAKS_INVALID"

        disjoint = _arguments(
            page_state,
            source_state,
            break_state,
            edge_break_state,
        )
        disjoint["sources"] = [
            {
                "object_name": break_sketch.Name,
                "expected_state_sha256": drawing_source_state(break_sketch)[
                    "state_sha256"
                ],
            }
        ]
        rejected = call(DRAWING_VIEW_CAPABILITY_NAME, disjoint, succeeds=False)
        assert rejected["error_code"] == "NATIVE_DRAWING_BREAKS_INVALID"

        wrong_orientation = _arguments(
            page_state,
            source_state,
            break_state,
            edge_break_state,
        )
        wrong_orientation["orientation"] = "front"
        rejected = call(
            DRAWING_VIEW_CAPABILITY_NAME,
            wrong_orientation,
            succeeds=False,
        )
        assert rejected["error_code"] == "NATIVE_DRAWING_BREAK_ORIENTATION_INVALID"

        arguments = _arguments(
            page_state,
            source_state,
            break_state,
            edge_break_state,
        )
        original_verify = DrawingViewRuntimeModule.verify_broken_view_create

        def fail_verify(_document, _draft):
            raise RuntimeError("injected broken Drawing view failure")

        DrawingViewRuntimeModule.verify_broken_view_create = fail_verify
        try:
            rolled_back_start = call(DRAWING_VIEW_CAPABILITY_NAME, arguments)
            rolled_back = wait_for_job(rolled_back_start["job"]["job_id"])
        finally:
            DrawingViewRuntimeModule.verify_broken_view_create = original_verify
        assert rolled_back["phase"] == "failed", rolled_back
        assert (
            rolled_back["failure"]["error_code"] == "NATIVE_POSTCONDITION_FAILED"
        ), rolled_back
        assert tuple(page.Views or ()) == ()
        assert document.getObject("BrokenView") is None

        cancelled_start = call(DRAWING_VIEW_CAPABILITY_NAME, arguments)
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
        assert tuple(page.Views or ()) == ()

        original_execute = DrawingViewRuntimeModule.execute_broken_projection
        worker_ready = threading.Event()
        worker_release = threading.Event()

        def gated_execute(frozen_input, *, cancelled, progress):
            worker_ready.set()
            while not worker_release.wait(0.01):
                if cancelled():
                    raise RuntimeError("unexpected cancellation")
            return original_execute(
                frozen_input,
                cancelled=cancelled,
                progress=progress,
            )

        DrawingViewRuntimeModule.execute_broken_projection = gated_execute
        try:
            stale_start = call(DRAWING_VIEW_CAPABILITY_NAME, arguments)
            deadline = time.monotonic() + 10.0
            while not worker_ready.is_set() and time.monotonic() < deadline:
                _events(2)
                time.sleep(0.01)
            assert worker_ready.is_set()
            document.openTransaction("Change exact break during projection")
            transaction = int(document.getBookedTransactionID())
            try:
                break_sketch.delGeometry(1)
                break_sketch.addGeometry(
                    Part.LineSegment(
                        App.Vector(42.0, -5.0, 0.0),
                        App.Vector(42.0, 35.0, 0.0),
                    ),
                    False,
                )
                assert document.recompute([break_sketch], True, True) is not False
            except Exception:
                App.closeActiveTransaction(True, transaction)
                raise
            App.closeActiveTransaction(False, transaction)
            worker_release.set()
            stale_result = wait_for_job(stale_start["job"]["job_id"])
        finally:
            worker_release.set()
            DrawingViewRuntimeModule.execute_broken_projection = original_execute
        assert stale_result["phase"] == "failed", stale_result
        assert stale_result["failure"]["error_code"] in {
            "NATIVE_DRAWING_BREAK_STALE",
            "NATIVE_REVISION_CONFLICT",
        }
        assert tuple(page.Views or ()) == ()
        document.undo()
        _events(12)
        break_sketch = document.getObject("BreakSketch")
        assert drawing_break_state(break_sketch)["state_sha256"] == break_state[
            "state_sha256"
        ]

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
        started = call(DRAWING_VIEW_CAPABILITY_NAME, arguments)
        returned_in = time.monotonic() - started_at
        assert returned_in < 2.0, returned_in
        completed = wait_for_job(started["job"]["job_id"])
        heartbeat.stop()
        assert completed["phase"] == "completed", completed
        assert ui_ticks > 0, ui_ticks
        result = completed["result"]
        assert len(json.dumps(result, separators=(",", ":")).encode()) < 32 * 1024
        assert "path" not in json.dumps(result).casefold()
        view_name = result["view"]["object_name"]
        view = document.getObject(view_name)
        assert view is not None and view.TypeId == "TechDraw::DrawBrokenView"
        state = drawing_view_state(view)
        assert state == result["view"]
        assert state["page_name"] == page.Name
        assert state["breaks"] == [
            {
                "object_name": break_sketch.Name,
                "state_sha256": break_state["state_sha256"],
                "kind": "two_line_sketch",
            },
            {
                "object_name": edge_break.Name,
                "state_sha256": edge_break_state["state_sha256"],
                "kind": "single_edge",
            },
        ]
        assert state["gap_mm"] == 5.0
        assert state["direction"] == [0.0, 0.0, 1.0]
        assert state["x_direction"] == [1.0, 0.0, 0.0]
        projection = view.getPrecomputedProjection()
        projected_width = float(projection["edges"].BoundBox.XLength)
        assert 0.0 < projected_width < 50.0, projected_width
        assert tuple(page.Views) == (view,)
        assert str(view.VibeCADTimelineRole) == "operation"
        assert getattr(view, "VibeCADTimelineOwner", None) is None
        assert int(document.UndoCount) == undo_before + 1
        assert _selection() == selection_before
        assert (
            bool(source.ViewObject.Visibility),
            bool(break_sketch.ViewObject.Visibility),
            bool(edge_break.ViewObject.Visibility),
        ) == visibility_before
        assert not Gui.Control.activeDialog()
        operations_after = tuple(document.VibeCADTimeline.Operations)

        stale_page = _arguments(
            page_state,
            source_state,
            break_state,
            edge_break_state,
        )
        rejected = call(DRAWING_VIEW_CAPABILITY_NAME, stale_page, succeeds=False)
        assert rejected["error_code"] == "NATIVE_DRAWING_PAGE_STALE"

        document.undo()
        _events(12)
        assert document.getObject(view_name) is None
        page = document.getObject(page.Name)
        assert page is not None and tuple(page.Views or ()) == ()
        document.redo()
        _events(12)
        view = document.getObject(view_name)
        page = document.getObject(page.Name)
        assert view is not None and tuple(page.Views) == (view,)
        assert drawing_view_state(view) == state
        assert tuple(document.VibeCADTimeline.Operations) == operations_after

        page_name = str(page.Name)
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

        print(
            "VIBECAD_NATIVE_DRAWING_BROKEN_VIEW_GUI_OK "
            "exact_page=true exact_sources=true exact_breaks=true sketch_identity=true "
            "single_edge=true "
            "context_hash=true closed_schema=true background=true authenticated=true "
            "native_type=true orientation=true placement=true scale=true line_style=true "
            "gap=true broken_geometry=true no_task=true stale_page=true stale_break=true "
            "duplicate_guard=true disjoint_guard=true orientation_guard=true "
            "rollback=true cancel=true stale_commit=true selection=true visibility=true "
            "history=true undo=true redo=true reopen=true responsive=true "
            "path_private=true low_noise=true",
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
