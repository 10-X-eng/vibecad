# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled GUI lifecycle gate for responsive exact Drawing page redraw."""

from __future__ import annotations

import hashlib
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

import VibeCADGui as VibeGui
import VibeCADNativeDrawingPageRuntime as DrawingPageRuntimeModule
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeBackgroundSchema import NATIVE_BACKGROUND_CAPABILITY_NAME
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeDrawingPageSchema import DRAWING_PAGE_CAPABILITY_NAME
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewSchema import DRAWING_VIEW_CAPABILITY_NAME
from VibeCADNativeDrawingViewState import drawing_source_state
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
    assert surface.surface_id == "drawing", surface.surface_id
    return controller, surface


def _selection() -> tuple:
    return tuple(
        (item.Object.Name, tuple(item.SubElementNames))
        for item in Gui.Selection.getSelectionEx()
    )


def _transaction(document, name: str, mutate, *, targets=(), recompute=True) -> None:
    document.openTransaction(name)
    transaction = int(document.getBookedTransactionID())
    try:
        mutate()
        if not recompute:
            pass
        elif targets:
            assert document.recompute(list(targets), True, True) is not False
        else:
            assert document.recompute() is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)


def _projection_sha256(view) -> str:
    snapshot = view.getPrecomputedProjection()
    centroid = snapshot["centroid"]
    semantic = {
        "projected_elements": view.getProjectedElementDescriptors(),
        "face_count": len(tuple(snapshot["faces"].Faces)),
        "edge_classes": list(snapshot["edge_classes"]),
        "edge_visibility": list(snapshot["edge_visibility"]),
        "source_indices": list(snapshot["source_indices"]),
        "centroid": [centroid.x, centroid.y, centroid.z],
    }
    encoded = json.dumps(
        semantic,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _dimension_sha256(dimension) -> str:
    try:
        snapshot = dimension.getPrecomputedDimension()
    except RuntimeError as exc:
        references = tuple(
            (str(obj.Name), tuple(names))
            for obj, names in dimension.References2D
        )
        linear = tuple(
            (float(point.x), float(point.y), float(point.z))
            for point in dimension.getLinearPoints()
        )
        raise AssertionError(
            "TechDraw dimension cache is unavailable: "
            f"state={tuple(dimension.State)!r}, references={references!r}, "
            f"raw_value={float(dimension.getRawValue())!r}, linear={linear!r}, "
            f"vectors={len(tuple(dimension.PrecomputedDimensionVectors))!r}, "
            f"flags={tuple(dimension.PrecomputedDimensionFlags)!r}"
        ) from exc
    value = {
        "vectors": [
            [float(vector.x), float(vector.y), float(vector.z)]
            for vector in snapshot["vectors"]
        ],
        "scalars": [float(item) for item in snapshot["scalars"]],
        "flags": [bool(item) for item in snapshot["flags"]],
    }
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _turn(surface, registry) -> NativeTurnSnapshot:
    page_definition = registry.definition(DRAWING_PAGE_CAPABILITY_NAME)
    view_definition = registry.definition(DRAWING_VIEW_CAPABILITY_NAME)
    job_definition = registry.definition(NATIVE_BACKGROUND_CAPABILITY_NAME)
    assert all(
        item is not None
        for item in (page_definition, view_definition, job_definition)
    )
    page_schema = page_definition.provider_schema(("page_default", "redraw_page"))
    view_schema = view_definition.provider_schema(("create_standard_view",))
    encoded = json.dumps(page_schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "path" not in encoded.casefold()
    assert "snapshot" not in encoded.casefold()
    assert "expected_state_sha256" in encoded
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


def _view_arguments(page_state: dict, source_state: dict) -> dict:
    return {
        "operation": "create_standard_view",
        "label": "Redraw Target",
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
        "position": {"x_mm": 88.0, "y_mm": 82.0},
        "scale": {"kind": "custom", "value": 1.25},
        "line_style": "visible_and_hidden",
    }


def _redraw_arguments(page) -> dict:
    state = drawing_page_state(page)
    return {
        "operation": "redraw_page",
        "page": {
            "object_name": state["object_name"],
            "expected_state_sha256": state["state_sha256"],
        },
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-drawing-redraw-")
        save_path = Path(temporary.name) / "native-drawing-redraw.FCStd"
        controller, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        redraw_plan = plans["TechDraw_RedrawPage"]
        assert (
            redraw_plan.capability_family,
            redraw_plan.operation_variant,
            redraw_plan.exact_target_type,
            redraw_plan.transaction_behavior,
            redraw_plan.background_required,
        ) == (
            DRAWING_PAGE_CAPABILITY_NAME,
            "redraw_page",
            "ExactDrawingPageAndActiveViewGraph",
            "background",
            True,
        )

        document = App.newDocument("NativeDrawingRedrawGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        source = None

        def create_source() -> None:
            nonlocal source
            source = document.addObject("Part::Feature", "DrawingSource")
            source.Label = "Drawing Source"
            source.Shape = Part.makeBox(36.0, 24.0, 12.0)
            document.publishProvisionalTimelineOperationBlock(source, (), ())

        _transaction(document, "Create Drawing source", create_source)
        source.ViewObject.Visibility = True
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source, "Face1")

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen_surface = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-drawing-redraw-gui")

        def reauthorize() -> None:
            require_frozen_native_surface(frozen_surface, controller)

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
        debug_events = []
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state_store,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            debug_sink=debug_events.append,
        )
        call_index = 0

        def call(tool_name: str, arguments: dict, *, succeeds: bool = True) -> dict:
            nonlocal call_index
            call_index += 1
            response = dispatcher.call(
                tool_name,
                json.dumps(arguments, separators=(",", ":")),
                f"native-drawing-redraw-{call_index}",
            )
            assert response.get("ok") is succeeds, (response, tuple(debug_events))
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
            raise AssertionError(f"Background redraw job {job_id} did not finish")

        page_result = call(DRAWING_PAGE_CAPABILITY_NAME, {"operation": "page_default"})
        page = document.getObject(page_result["page"]["object_name"])
        assert page is not None
        view_started = call(
            DRAWING_VIEW_CAPABILITY_NAME,
            _view_arguments(drawing_page_state(page), drawing_source_state(source)),
        )
        view_completed = wait_for_job(view_started["job"]["job_id"])
        assert view_completed["phase"] == "completed", view_completed
        view = document.getObject(view_completed["result"]["view"]["object_name"])
        assert view is not None and tuple(page.Views) == (view,)

        def initialize_projection_cache() -> None:
            page.KeepUpdated = False
            page.KeepUpdated = True

        _transaction(
            document,
            "Initialize redraw projection cache",
            initialize_projection_cache,
            recompute=False,
        )
        _events(16)

        projected_edges = view.getProjectedElementDescriptors()["edges"]
        visible_edges = tuple(
            item for item in projected_edges if bool(item["visible"])
        )
        assert visible_edges
        dimension_edge = max(
            visible_edges,
            key=lambda item: float(item["length_view_mm"]),
        )["name"]
        dimension = None

        def create_dimension() -> None:
            nonlocal dimension
            dimension = document.addObject(
                "TechDraw::DrawViewDimension",
                "RedrawDimension",
            )
            dimension.Type = "Distance"
            dimension.MeasureType = "Projected"
            dimension.References2D = [(view, dimension_edge)]
            page.addView(dimension)
            document.publishProvisionalTimelineOperationBlock(
                dimension,
                (),
                (),
            )
            assert document.recompute() is not False

        _transaction(
            document,
            "Create redraw dimension",
            create_dimension,
            recompute=False,
        )
        def initialize_dimension_cache() -> None:
            page.KeepUpdated = False
            page.KeepUpdated = True
            dimension.touch()

        _transaction(
            document,
            "Initialize redraw dimension cache",
            initialize_dimension_cache,
            targets=(dimension,),
        )
        _events(16)
        assert dimension is not None and bool(dimension.isValid())
        assert dimension.Name in tuple(item.Name for item in page.getAllActiveViews()), (
            tuple(item.Name for item in page.getAllActiveViews()),
            tuple(item.Name for item in document.VibeCADTimeline.Operations),
            int(document.VibeCADTimeline.Position),
        )
        initial_dimension = _dimension_sha256(dimension)
        initial_dimension_value = float(dimension.getRawValue())

        _transaction(
            document,
            "Disable live Drawing updates",
            lambda: setattr(page, "KeepUpdated", False),
            targets=(page,),
        )
        assert not bool(page.KeepUpdated)
        selection_before = _selection()
        visibility_before = bool(source.ViewObject.Visibility)
        timeline_before = tuple(document.VibeCADTimeline.Operations)
        graph_before = tuple(item.Name for item in page.getAllActiveViews())
        initial_projection = _projection_sha256(view)

        invalid = _redraw_arguments(page)
        invalid["worker_path"] = "/tmp/not-provider-data"
        rejected = call(DRAWING_PAGE_CAPABILITY_NAME, invalid, succeeds=False)
        assert rejected["error_code"] == "NATIVE_ARGUMENTS_INVALID"

        stale = _redraw_arguments(page)
        stale["page"]["expected_state_sha256"] = "0" * 64
        rejected = call(DRAWING_PAGE_CAPABILITY_NAME, stale, succeeds=False)
        assert rejected["error_code"] == "NATIVE_DRAWING_PAGE_STALE"

        original_verify = DrawingPageRuntimeModule.verify_page_redraw

        def fail_verify(_document, _draft):
            raise RuntimeError("injected Drawing redraw postcondition failure")

        def change_source_without_live_hlr() -> None:
            source.Shape = Part.makeBox(44.0, 30.0, 12.0)
            source.purgeTouched()
            view.purgeTouched()

        _transaction(
            document,
            "Change source before rollback redraw",
            change_source_without_live_hlr,
            recompute=False,
        )
        projection_before_rollback = _projection_sha256(view)
        dimension_before_rollback = _dimension_sha256(dimension)
        DrawingPageRuntimeModule.verify_page_redraw = fail_verify
        try:
            rollback_started = call(DRAWING_PAGE_CAPABILITY_NAME, _redraw_arguments(page))
            rolled_back = wait_for_job(rollback_started["job"]["job_id"])
        finally:
            DrawingPageRuntimeModule.verify_page_redraw = original_verify
        assert rolled_back["phase"] == "failed", rolled_back
        assert (
            rolled_back["failure"]["error_code"] == "NATIVE_POSTCONDITION_FAILED"
        ), rolled_back
        current_view = document.getObject(view.Name)
        assert _projection_sha256(current_view) == projection_before_rollback, {
            "expected": projection_before_rollback,
            "current": _projection_sha256(current_view),
            "held": _projection_sha256(view),
        }
        view = current_view
        dimension = document.getObject(dimension.Name)
        assert _dimension_sha256(dimension) == dimension_before_rollback

        cancelled_started = call(DRAWING_PAGE_CAPABILITY_NAME, _redraw_arguments(page))
        cancelled_request = call(
            NATIVE_BACKGROUND_CAPABILITY_NAME,
            {"operation": "cancel", "job_id": cancelled_started["job"]["job_id"]},
        )
        assert cancelled_request["cancel_accepted"] is True, cancelled_request
        cancelled = wait_for_job(cancelled_started["job"]["job_id"])
        assert cancelled["phase"] == "cancelled", cancelled
        assert _projection_sha256(view) == projection_before_rollback
        assert _dimension_sha256(dimension) == dimension_before_rollback

        entered = threading.Event()
        release = threading.Event()
        original_execute = DrawingPageRuntimeModule.execute_page_redraw

        def held_execute(frozen, *, cancelled, progress):
            entered.set()
            while not release.wait(0.01):
                if cancelled():
                    raise RuntimeError("unexpected cancellation during stale-state gate")
            return original_execute(frozen, cancelled=cancelled, progress=progress)

        DrawingPageRuntimeModule.execute_page_redraw = held_execute
        try:
            stale_started = call(DRAWING_PAGE_CAPABILITY_NAME, _redraw_arguments(page))
            deadline = time.monotonic() + 10.0
            while not entered.is_set() and time.monotonic() < deadline:
                _events(2)
                time.sleep(0.01)
            assert entered.is_set()
            _transaction(
                document,
                "Change exact page during redraw",
                lambda: setattr(page, "Scale", float(page.Scale) + 0.125),
                targets=(page,),
            )
            release.set()
            stale_result = wait_for_job(stale_started["job"]["job_id"])
        finally:
            release.set()
            DrawingPageRuntimeModule.execute_page_redraw = original_execute
        assert stale_result["phase"] == "failed", stale_result
        assert stale_result["failure"]["error_code"] in {
            "NATIVE_REVISION_CONFLICT",
            "NATIVE_DRAWING_REDRAW_STALE",
        }
        assert _projection_sha256(view) == projection_before_rollback
        assert _dimension_sha256(dimension) == dimension_before_rollback

        selection_before = _selection()
        visibility_before = bool(source.ViewObject.Visibility)
        timeline_before = tuple(document.VibeCADTimeline.Operations)
        graph_before = tuple(item.Name for item in page.getAllActiveViews())
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
        started = call(DRAWING_PAGE_CAPABILITY_NAME, _redraw_arguments(page))
        returned_in = time.monotonic() - started_at
        assert returned_in < 2.0, returned_in
        completed = wait_for_job(started["job"]["job_id"])
        heartbeat.stop()
        assert completed["phase"] == "completed", completed
        assert ui_ticks > 0, ui_ticks
        result = completed["result"]
        assert result["page"]["object_name"] == page.Name
        assert result["page"]["view_count"] == 2
        assert len(result["redrawn_views"]) == 2
        assert {
            (item["object_name"], item["kind"])
            for item in result["redrawn_views"]
        } == {(view.Name, "projection"), (dimension.Name, "dimension")}
        redrawn_projection = _projection_sha256(view)
        redrawn_dimension = _dimension_sha256(dimension)
        assert redrawn_projection != initial_projection
        assert redrawn_projection != projection_before_rollback
        assert redrawn_dimension != initial_dimension, (
            dimension_edge,
            initial_dimension_value,
            float(dimension.getRawValue()),
            tuple(
                (item["name"], float(item["length_view_mm"]))
                for item in view.getProjectedElementDescriptors()["edges"]
                if bool(item["visible"])
            ),
        )
        assert redrawn_dimension != dimension_before_rollback
        assert not bool(page.KeepUpdated)
        assert tuple(item.Name for item in page.getAllActiveViews()) == graph_before
        assert tuple(document.VibeCADTimeline.Operations) == timeline_before
        assert _selection() == selection_before
        assert bool(source.ViewObject.Visibility) is visibility_before
        assert int(document.UndoCount) == undo_before
        assert result["assistant_undo_available"] is False
        assert "path" not in json.dumps(result).casefold()
        assert "snapshot" not in json.dumps(result).casefold()

        page_name = str(page.Name)
        view_name = str(view.Name)
        dimension_name = str(dimension.Name)
        document.saveAs(str(save_path))
        document_name = str(document.Name)
        App.closeDocument(document_name)
        document = App.openDocument(str(save_path))
        _events(16)
        page = document.getObject(page_name)
        view = document.getObject(view_name)
        dimension = document.getObject(dimension_name)
        assert page is not None and view is not None and dimension is not None
        assert _projection_sha256(view) == redrawn_projection
        assert _dimension_sha256(dimension) == redrawn_dimension
        assert not bool(page.KeepUpdated)
        assert tuple(item.Name for item in page.getAllActiveViews()) == (
            view_name,
            dimension_name,
        )

        print(
            "VIBECAD_NATIVE_DRAWING_REDRAW_GUI_OK exact_page=true exact_graph=true "
            "exact_sources=true closed_schema=true background=true detached=true "
            "authenticated=true projection=true dimension=true rollback=true "
            "cancel=true stale_preflight=true "
            "stale_commit=true selection=true visibility=true history=true "
            "keep_updated=true undo_stack_preserved=true reopen=true responsive=true "
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
