# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact Native Drawing detail views."""

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

import VibeCADGui as VibeGui
import VibeCADNativeDrawingDetailRuntime as DetailRuntimeModule
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeBackgroundSchema import NATIVE_BACKGROUND_CAPABILITY_NAME
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeDrawingDetailSchema import DRAWING_DETAIL_CAPABILITY_NAME
from VibeCADNativeDrawingPageSchema import DRAWING_PAGE_CAPABILITY_NAME
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewState import (
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


def _create_source(document):
    document.openTransaction("Create exact detail source")
    transaction = int(document.getBookedTransactionID())
    try:
        source = document.addObject("Part::Feature", "DrawingDetailSource")
        source.Label = "Detail Source"
        source.Shape = Part.makeBox(36.0, 24.0, 12.0)
        document.publishProvisionalTimelineOperationBlock(source, (), ())
        assert document.recompute([source], True, True) is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    return source


def _create_base_view(document, page, source):
    document.openTransaction("Create exact detail base view")
    transaction = int(document.getBookedTransactionID())
    try:
        base = document.addObject("TechDraw::DrawViewPart", "DetailBaseView")
        base.Label = "Detail Base View"
        base.Source = [source]
        base.Direction = App.Vector(0.0, -1.0, 0.0)
        base.XDirection = App.Vector(1.0, 0.0, 0.0)
        base.ScaleType = "Custom"
        base.Scale = 1.0
        base.X = 70.0
        base.Y = 75.0
        document.publishProvisionalTimelineOperationBlock(base, (), ())
        assert int(page.addView(base)) >= 1
        assert document.recompute([source, base, page], True, True) is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        _events(4)
        if drawing_view_state(base)["visible_edge_count"]:
            break
        time.sleep(0.01)
    assert drawing_view_state(base)["visible_edge_count"]
    return base


def _turn(surface, registry) -> NativeTurnSnapshot:
    page_definition = registry.definition(DRAWING_PAGE_CAPABILITY_NAME)
    detail_definition = registry.definition(DRAWING_DETAIL_CAPABILITY_NAME)
    job_definition = registry.definition(NATIVE_BACKGROUND_CAPABILITY_NAME)
    assert all(
        item is not None
        for item in (page_definition, detail_definition, job_definition)
    )
    page_schema = page_definition.provider_schema(("page_default",))
    detail_schema = detail_definition.provider_schema(("create_detail_view",))
    encoded = json.dumps(detail_schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "path" not in encoded.casefold()
    for field in (
        "expected_state_sha256",
        "base_view",
        "anchor_on_base_mm",
        "radius_mm",
        "position_on_page_mm",
        "scale",
    ):
        assert field in encoded
    for hidden_implementation_detail in (
        "detail_shape",
        "matting_style",
        "direction",
        "x_direction",
        "rotation_degrees",
        "source",
    ):
        assert hidden_implementation_detail not in encoded
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(
                DRAWING_PAGE_CAPABILITY_NAME,
                DRAWING_DETAIL_CAPABILITY_NAME,
                NATIVE_BACKGROUND_CAPABILITY_NAME,
            ),
            schemas=(
                page_schema,
                detail_schema,
                job_definition.provider_schema(("status", "cancel")),
            ),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _arguments(page_state: dict, base_state: dict) -> dict:
    return {
        "operation": "create_detail_view",
        "reference": "A",
        "page": {
            "object_name": page_state["object_name"],
            "expected_state_sha256": page_state["state_sha256"],
        },
        "base_view": {
            "object_name": base_state["object_name"],
            "expected_state_sha256": base_state["state_sha256"],
        },
        "anchor_on_base_mm": {"x_mm": 18.0, "y_mm": 12.0},
        "radius_mm": 8.0,
        "position_on_page_mm": {"x_mm": 165.0, "y_mm": 75.0},
        "scale": {"kind": "custom", "value": 2.0},
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-drawing-detail-view-"
        )
        save_path = Path(temporary.name) / "native-drawing-detail-view.FCStd"
        controller, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["TechDraw_DetailView"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.transaction_behavior,
            plan.background_required,
        ) == (
            DRAWING_DETAIL_CAPABILITY_NAME,
            "create_detail_view",
            "ExactDrawingPageBaseViewAnchorRadiusPlacementAndScale",
            "background",
            True,
        )

        document = App.newDocument("NativeDrawingDetailViewGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        source = _create_source(document)

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-drawing-detail-view-gui")

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
                f"native-drawing-detail-view-{call_index}",
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
            raise AssertionError(f"Background detail-view job {job_id} did not finish")

        page_result = call(DRAWING_PAGE_CAPABILITY_NAME, {"operation": "page_default"})
        _events(12)
        page = document.getObject(page_result["page"]["object_name"])
        assert page is not None
        base = _create_base_view(document, page, source)

        # Exercise the shipped human command itself. Its provisional object is
        # the parity oracle for type, graph ownership, copied projection axes,
        # and task-owned rollback.
        objects_before_human = tuple(document.Objects)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(base)
        Gui.runCommand("TechDraw_DetailView")
        _events(16)
        assert Gui.Control.activeDialog()
        human_objects = tuple(
            obj for obj in document.Objects if obj not in objects_before_human
        )
        assert len(human_objects) == 1
        human_detail = human_objects[0]
        human_detail_name = str(human_detail.Name)
        assert human_detail.isDerivedFrom("TechDraw::DrawViewDetail")
        assert human_detail.BaseView is base
        assert tuple(human_detail.Source) == (source,)
        assert human_detail.Direction == base.Direction
        assert human_detail.XDirection == base.XDirection
        assert tuple(page.Views) == (base, human_detail)
        dialog = Gui.Control.activeTaskDialog()
        assert dialog is not None
        dialog.reject()
        _events(16)
        page = document.getObject(page.Name)
        base = document.getObject(base.Name)
        source = document.getObject(source.Name)
        assert page is not None and base is not None and source is not None
        assert tuple(page.Views) == (base,)
        assert document.getObject(human_detail_name) is None

        source.ViewObject.Visibility = True
        base.ViewObject.Visibility = True
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(base)
        selection_before = _selection()
        visibility_before = (
            bool(source.ViewObject.Visibility),
            bool(base.ViewObject.Visibility),
        )
        page_state = drawing_page_state(page)
        base_state = drawing_view_state(base)
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
                            "object_name": base.Name,
                            "type_id": base.TypeId,
                        },
                        "subelements": [],
                    }
                ],
            },
        )
        domain = active["domain"]
        assert domain["active_page_resolution"] == "selection"
        assert domain["active_page"]["state_sha256"] == page_state["state_sha256"]
        view_summary = domain["pages"][0]["views"][0]
        assert view_summary["state_sha256"] == base_state["state_sha256"]
        assert len(json.dumps(active, separators=(",", ":")).encode()) < 64 * 1024

        invalid = _arguments(page_state, base_state)
        invalid["matting_style"] = "circle"
        rejected = call(DRAWING_DETAIL_CAPABILITY_NAME, invalid, succeeds=False)
        assert rejected["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(page.Views) == (base,)

        stale_base = _arguments(page_state, base_state)
        stale_base["base_view"]["expected_state_sha256"] = "0" * 64
        rejected = call(DRAWING_DETAIL_CAPABILITY_NAME, stale_base, succeeds=False)
        assert rejected["error_code"] == "NATIVE_DRAWING_DETAIL_BASE_STALE"

        invalid_radius = _arguments(page_state, base_state)
        invalid_radius["radius_mm"] = 0.0
        rejected = call(DRAWING_DETAIL_CAPABILITY_NAME, invalid_radius, succeeds=False)
        assert rejected["error_code"] == "NATIVE_ARGUMENTS_INVALID"

        arguments = _arguments(page_state, base_state)
        original_verify = DetailRuntimeModule.verify_detail_view_create

        def fail_verify(_document, _draft):
            raise RuntimeError("injected detail publication failure")

        DetailRuntimeModule.verify_detail_view_create = fail_verify
        try:
            rollback_start = call(DRAWING_DETAIL_CAPABILITY_NAME, arguments)
            rolled_back = wait_for_job(rollback_start["job"]["job_id"])
        finally:
            DetailRuntimeModule.verify_detail_view_create = original_verify
        assert rolled_back["phase"] == "failed", rolled_back
        assert rolled_back["failure"]["error_code"] == "NATIVE_POSTCONDITION_FAILED"
        assert tuple(page.Views) == (base,)
        assert document.getObject("DetailView") is None

        cancelled_start = call(DRAWING_DETAIL_CAPABILITY_NAME, arguments)
        cancelled_request = call(
            NATIVE_BACKGROUND_CAPABILITY_NAME,
            {"operation": "cancel", "job_id": cancelled_start["job"]["job_id"]},
        )
        assert cancelled_request["cancel_accepted"] is True
        cancelled = wait_for_job(cancelled_start["job"]["job_id"])
        assert cancelled["phase"] == "cancelled", cancelled
        assert tuple(page.Views) == (base,)

        original_execute = DetailRuntimeModule.execute_detail_projection
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

        DetailRuntimeModule.execute_detail_projection = gated_execute
        try:
            stale_start = call(DRAWING_DETAIL_CAPABILITY_NAME, arguments)
            deadline = time.monotonic() + 10.0
            while not worker_ready.is_set() and time.monotonic() < deadline:
                _events(2)
                time.sleep(0.01)
            assert worker_ready.is_set()
            document.openTransaction("Change exact source during detailing")
            transaction = int(document.getBookedTransactionID())
            try:
                source.Shape = Part.makeBox(40.0, 24.0, 12.0)
                assert document.recompute([source, base, page], True, True) is not False
            except Exception:
                App.closeActiveTransaction(True, transaction)
                raise
            App.closeActiveTransaction(False, transaction)
            worker_release.set()
            stale_result = wait_for_job(stale_start["job"]["job_id"])
        finally:
            worker_release.set()
            DetailRuntimeModule.execute_detail_projection = original_execute
        assert stale_result["phase"] == "failed", stale_result
        assert stale_result["failure"]["error_code"] in {
            "NATIVE_DRAWING_DETAIL_BASE_STALE",
            "NATIVE_DRAWING_DETAIL_SOURCE_STALE",
            "NATIVE_REVISION_CONFLICT",
        }
        assert tuple(page.Views) == (base,)
        document.undo()
        _events(12)
        source = document.getObject(source.Name)
        base = document.getObject(base.Name)
        page = document.getObject(page.Name)
        assert drawing_source_state(source)["state_sha256"] == source_state["state_sha256"]
        assert drawing_view_state(base)["state_sha256"] == base_state["state_sha256"]

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
        started = call(DRAWING_DETAIL_CAPABILITY_NAME, arguments)
        returned_in = time.monotonic() - started_at
        assert returned_in < 2.0, returned_in
        completed = wait_for_job(started["job"]["job_id"])
        heartbeat.stop()
        assert completed["phase"] == "completed", completed
        assert ui_ticks > 0, ui_ticks
        result = completed["result"]
        encoded_result = json.dumps(result, separators=(",", ":"))
        assert len(encoded_result.encode()) < 32 * 1024
        assert "path" not in encoded_result.casefold()

        view_name = result["view"]["object_name"]
        detail = document.getObject(view_name)
        assert detail is not None and detail.TypeId == "TechDraw::DrawViewDetail"
        state = drawing_view_state(detail)
        assert state == result["view"]
        detail_state = state["detail"]
        assert detail_state["base_view"] == {
            "object_name": base.Name,
            "state_sha256": base_state["state_sha256"],
        }
        assert detail_state["anchor_mm"] == [18.0, 12.0]
        assert detail_state["radius_mm"] == 8.0
        assert detail_state["reference"] == "A"
        assert detail_state["detail_topology"]["edges"] >= 1
        assert state["scale_type"] == "Custom" and state["scale"] == 2.0
        assert state["x_mm"] == 165.0 and state["y_mm"] == 75.0
        assert state["line_visibility"] == base_state["line_visibility"]
        cache = detail.getPrecomputedDetail()
        assert len(tuple(cache["detail_shape"].Edges)) >= 1
        assert tuple(page.Views) == (base, detail)
        assert str(detail.VibeCADTimelineRole) == "operation"
        assert getattr(detail, "VibeCADTimelineOwner", None) is None
        assert int(document.UndoCount) == undo_before + 1
        assert _selection() == selection_before
        assert (
            bool(source.ViewObject.Visibility),
            bool(base.ViewObject.Visibility),
        ) == visibility_before
        assert not Gui.Control.activeDialog()
        operations_after = tuple(document.VibeCADTimeline.Operations)

        stale_page = _arguments(page_state, base_state)
        rejected = call(DRAWING_DETAIL_CAPABILITY_NAME, stale_page, succeeds=False)
        assert rejected["error_code"] == "NATIVE_DRAWING_PAGE_STALE"

        document.undo()
        _events(12)
        assert document.getObject(view_name) is None
        page = document.getObject(page.Name)
        base = document.getObject(base.Name)
        assert page is not None and tuple(page.Views) == (base,)
        document.redo()
        _events(12)
        detail = document.getObject(view_name)
        page = document.getObject(page.Name)
        assert detail is not None and tuple(page.Views) == (base, detail)
        assert drawing_view_state(detail) == state
        assert len(tuple(detail.getPrecomputedDetail()["detail_shape"].Edges)) >= 1
        assert tuple(document.VibeCADTimeline.Operations) == operations_after

        page_name = str(page.Name)
        document.saveAs(str(save_path))
        document_name = document.Name
        App.closeDocument(document_name)
        document = App.openDocument(str(save_path))
        _events(16)
        reopened_detail = document.getObject(view_name)
        reopened_page = document.getObject(page_name)
        assert reopened_detail is not None and reopened_page is not None
        assert reopened_detail in tuple(reopened_page.Views)
        assert drawing_view_state(reopened_detail) == state
        assert len(tuple(reopened_detail.getPrecomputedDetail()["detail_shape"].Edges)) >= 1
        assert str(reopened_detail.VibeCADTimelineRole) == "operation"

        print(
            "VIBECAD_NATIVE_DRAWING_DETAIL_VIEW_GUI_OK "
            "human_parity=true exact_page=true exact_base=true exact_sources=true "
            "context_hash=true closed_schema=true anchor=true radius=true "
            "deterministic_placement=true custom_scale=true native_type=true "
            "clipped_geometry=true no_task=true stale_page=true stale_base=true "
            "stale_source=true radius_guard=true rollback=true cancel=true "
            "stale_commit=true selection=true visibility=true history=true "
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
