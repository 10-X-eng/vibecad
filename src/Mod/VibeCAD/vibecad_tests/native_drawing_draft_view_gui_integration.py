# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact Native Drawing Draft views."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import traceback

import Draft
import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
import VibeCADNativeDrawingDraftRuntime as DraftRuntimeModule
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeBackgroundSchema import NATIVE_BACKGROUND_CAPABILITY_NAME
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeDrawingDraftSchema import DRAWING_DRAFT_CAPABILITY_NAME
from VibeCADNativeDrawingDraftState import (
    draft_source_fingerprint,
    drawing_draft_source_state,
    drawing_draft_view_state,
)
from VibeCADNativeDrawingPageSchema import DRAWING_PAGE_CAPABILITY_NAMES
from VibeCADNativeDrawingState import drawing_page_state
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
    document.openTransaction("Create exact Draft source")
    transaction = int(document.getBookedTransactionID())
    try:
        source = Draft.make_wire(
            [
                App.Vector(0.0, 0.0, 0.0),
                App.Vector(42.0, 0.0, 0.0),
                App.Vector(42.0, 22.0, 0.0),
                App.Vector(0.0, 22.0, 0.0),
            ],
            closed=True,
        )
        source.Label = "Exact Draft Source"
        source.ViewObject.LineColor = (0.15, 0.35, 0.75)
        source.ViewObject.LineWidth = 3.0
        document.publishProvisionalTimelineOperationBlock(source, (), ())
        assert document.recompute([source], True, True) is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    return source


def _turn(surface, registry) -> NativeTurnSnapshot:
    page_definition = registry.definition(DRAWING_PAGE_CAPABILITY_NAMES[0])
    draft_definition = registry.definition(DRAWING_DRAFT_CAPABILITY_NAME)
    job_definition = registry.definition(NATIVE_BACKGROUND_CAPABILITY_NAME)
    assert all(
        item is not None
        for item in (page_definition, draft_definition, job_definition)
    )
    page_schema = page_definition.provider_schema(("page_default",))
    draft_schema = draft_definition.provider_schema(("create_draft_source_view",))
    encoded = json.dumps(draft_schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "path" not in encoded.casefold()
    for field in (
        "expected_state_sha256",
        "source",
        "orientation",
        "position_on_page_mm",
        "scale",
        "style",
        "color_rgb",
    ):
        assert field in encoded
    for hidden in ("raw_svg", "symbol", "file_path", "view_object"):
        assert hidden not in encoded.casefold()
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(
                DRAWING_PAGE_CAPABILITY_NAMES[0],
                DRAWING_DRAFT_CAPABILITY_NAME,
                NATIVE_BACKGROUND_CAPABILITY_NAME,
            ),
            schemas=(
                page_schema,
                draft_schema,
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
        "operation": "create_draft_source_view",
        "page": {
            "object_name": page_state["object_name"],
            "expected_state_sha256": page_state["state_sha256"],
        },
        "source": {
            "object_name": source_state["object_name"],
            "expected_state_sha256": source_state["state_sha256"],
        },
        "orientation": "top",
        "position_on_page_mm": {"x_mm": 125.0, "y_mm": 85.0},
        "scale": {"kind": "custom", "value": 1.5},
        "style": {
            "kind": "override",
            "line_width_mm": 0.6,
            "font_size_pt": 11.0,
            "color_rgb": {"red": 36, "green": 78, "blue": 160},
            "line_style": "Dashed",
            "line_spacing": 1.2,
        },
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-drawing-draft-view-"
        )
        save_path = Path(temporary.name) / "native-drawing-draft-view.FCStd"
        controller, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["TechDraw_DraftView"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.transaction_behavior,
            plan.background_required,
        ) == (
            DRAWING_DRAFT_CAPABILITY_NAME,
            "create_draft_source_view",
            "ExactDrawingPageDraftSourceOrientationPlacementScaleAndStyle",
            "background",
            True,
        )

        document = App.newDocument("NativeDrawingDraftViewGate")
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
        ledger.begin_run("native-drawing-draft-view-gui")

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
        def refresh_dispatcher() -> NativeTurnDispatcher:
            nonlocal turn, frozen
            turn = _turn(surface, registry)
            frozen = turn.surface
            return NativeTurnDispatcher(
                document=document,
                state=state_store,
                registry=registry,
                turn=turn,
                runtimes=build_native_runtime_bindings(context, turn.tool_names),
                reauthorize_turn=reauthorize,
                active_document=lambda: App.ActiveDocument,
            )

        dispatcher = refresh_dispatcher()
        call_index = 0

        def call(tool_name: str, arguments: dict, *, succeeds: bool = True) -> dict:
            nonlocal call_index
            call_index += 1
            response = dispatcher.call(
                tool_name,
                json.dumps(arguments, separators=(",", ":")),
                f"native-drawing-draft-view-{call_index}",
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
            raise AssertionError(f"Background Draft-view job {job_id} did not finish")

        page_result = call(DRAWING_PAGE_CAPABILITY_NAMES[0], {"operation": "page_default"})
        _events(12)
        page = document.getObject(page_result["page"]["object_name"])
        assert page is not None

        # The shipped command is the human parity oracle for object type,
        # source link, page ownership, scale, and generated SVG.
        objects_before_human = tuple(document.Objects)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source)
        Gui.runCommand("TechDraw_DraftView")
        _events(16)
        human_objects = tuple(
            obj for obj in document.Objects if obj not in objects_before_human
        )
        assert len(human_objects) == 1
        human_view = human_objects[0]
        human_name = str(human_view.Name)
        assert human_view.isDerivedFrom("TechDraw::DrawViewDraft")
        assert human_view.Source is source
        assert human_view in tuple(page.Views)
        assert str(human_view.ScaleType) == "Custom"
        assert str(human_view.Symbol)
        assert not Gui.Control.activeDialog()
        document.undo()
        _events(12)
        page = document.getObject(page.Name)
        source = document.getObject(source.Name)
        assert page is not None and source is not None
        assert document.getObject(human_name) is None
        assert tuple(page.Views) == ()
        dispatcher = refresh_dispatcher()

        source.ViewObject.Visibility = True
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source)
        selection_before = _selection()
        visibility_before = bool(source.ViewObject.Visibility)
        page_state = drawing_page_state(page)
        source_state = drawing_draft_source_state(source)

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
                        "subelements": [],
                    }
                ],
            },
        )
        domain = active["domain"]
        assert domain["active_page_resolution"] == "only_page"
        assert domain["active_page"]["state_sha256"] == page_state["state_sha256"]
        assert domain["selected_draft_sources"] == [source_state]
        assert len(json.dumps(active, separators=(",", ":")).encode()) < 64 * 1024

        invalid = _arguments(page_state, source_state)
        invalid["raw_svg"] = "<svg/>"
        rejected = call(DRAWING_DRAFT_CAPABILITY_NAME, invalid, succeeds=False)
        assert rejected["error_code"] == "NATIVE_ARGUMENTS_INVALID"

        stale_source = _arguments(page_state, source_state)
        stale_source["source"]["expected_state_sha256"] = "0" * 64
        rejected = call(DRAWING_DRAFT_CAPABILITY_NAME, stale_source, succeeds=False)
        assert rejected["error_code"] == "NATIVE_DRAWING_DRAFT_SOURCE_STALE"

        arguments = _arguments(page_state, source_state)
        original_verify = DraftRuntimeModule.verify_draft_view_create

        def fail_verify(_document, _draft):
            raise RuntimeError("injected Draft-view publication failure")

        DraftRuntimeModule.verify_draft_view_create = fail_verify
        try:
            rollback_start = call(DRAWING_DRAFT_CAPABILITY_NAME, arguments)
            rolled_back = wait_for_job(rollback_start["job"]["job_id"])
        finally:
            DraftRuntimeModule.verify_draft_view_create = original_verify
        assert rolled_back["phase"] == "failed", rolled_back
        assert rolled_back["failure"]["error_code"] == "NATIVE_POSTCONDITION_FAILED", rolled_back
        assert tuple(page.Views) == ()
        assert document.getObject("DraftView") is None

        cancelled_start = call(DRAWING_DRAFT_CAPABILITY_NAME, arguments)
        cancelled_request = call(
            NATIVE_BACKGROUND_CAPABILITY_NAME,
            {"operation": "cancel", "job_id": cancelled_start["job"]["job_id"]},
        )
        assert cancelled_request["cancel_accepted"] is True
        cancelled = wait_for_job(cancelled_start["job"]["job_id"])
        assert cancelled["phase"] == "cancelled", cancelled
        assert tuple(page.Views) == ()

        original_execute = DraftRuntimeModule.execute_draft_render
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

        DraftRuntimeModule.execute_draft_render = gated_execute
        try:
            stale_start = call(DRAWING_DRAFT_CAPABILITY_NAME, arguments)
            deadline = time.monotonic() + 10.0
            while not worker_ready.is_set() and time.monotonic() < deadline:
                _events(2)
                time.sleep(0.01)
            assert worker_ready.is_set()
            source.ViewObject.LineColor = (0.8, 0.1, 0.1)
            worker_release.set()
            stale_result = wait_for_job(stale_start["job"]["job_id"])
        finally:
            worker_release.set()
            DraftRuntimeModule.execute_draft_render = original_execute
        assert stale_result["phase"] == "failed", stale_result
        assert stale_result["failure"]["error_code"] == (
            "NATIVE_DRAWING_DRAFT_SOURCE_STALE"
        )
        assert tuple(page.Views) == ()
        source.ViewObject.LineColor = (0.15, 0.35, 0.75)
        assert drawing_draft_source_state(source)["state_sha256"] == (
            source_state["state_sha256"]
        )

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
        started = call(DRAWING_DRAFT_CAPABILITY_NAME, arguments)
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
        assert "<svg" not in encoded_result.casefold()

        view_name = result["view"]["object_name"]
        view = document.getObject(view_name)
        assert view is not None and view.TypeId == "TechDraw::DrawViewDraft"
        state = drawing_draft_view_state(view)
        assert state == result["view"]
        assert state["source"] == {
            "object_name": source.Name,
            "state_sha256": source_state["state_sha256"],
        }
        assert state["direction"] == [0.0, 0.0, 1.0]
        assert state["x_mm"] == 125.0 and state["y_mm"] == 85.0
        assert state["scale_type"] == "Custom" and state["scale"] == 1.5
        style = state["style"]
        assert style == {
            **style,
            "line_width": 0.6,
            "font_size_pt": 11.0,
            "line_style": "Dashed",
            "line_spacing": 1.2,
            "override": True,
        }
        assert all(
            abs(actual - expected) < 1.0e-7
            for actual, expected in zip(
                style["color"],
                (36 / 255.0, 78 / 255.0, 160 / 255.0),
                strict=True,
            )
        )
        assert state["svg_bytes"] > 32
        assert tuple(page.Views) == (view,)
        assert str(view.VibeCADTimelineRole) == "operation"
        assert getattr(view, "VibeCADTimelineOwner", None) is None
        assert int(document.UndoCount) == undo_before + 1
        assert _selection() == selection_before
        assert bool(source.ViewObject.Visibility) is visibility_before
        assert not Gui.Control.activeDialog()
        operations_after = tuple(document.VibeCADTimeline.Operations)

        stale_page = _arguments(page_state, source_state)
        rejected = call(DRAWING_DRAFT_CAPABILITY_NAME, stale_page, succeeds=False)
        assert rejected["error_code"] == "NATIVE_DRAWING_PAGE_STALE"

        document.undo()
        _events(12)
        assert document.getObject(view_name) is None
        page = document.getObject(page.Name)
        source = document.getObject(source.Name)
        assert page is not None and source is not None and tuple(page.Views) == ()
        document.redo()
        _events(12)
        view = document.getObject(view_name)
        page = document.getObject(page.Name)
        source = document.getObject(source.Name)
        assert view is not None and tuple(page.Views) == (view,)
        assert drawing_draft_view_state(view) == state
        assert tuple(document.VibeCADTimeline.Operations) == operations_after
        assert view.getPrecomputedDraft() == {
            "symbol": str(view.Symbol),
            "source_state_sha256": source_state["state_sha256"],
        }

        page_name = str(page.Name)
        source_name = str(source.Name)
        source_fingerprint_before_reopen = draft_source_fingerprint(source)
        document.saveAs(str(save_path))
        document_name = document.Name
        App.closeDocument(document_name)
        document = App.openDocument(str(save_path))
        _events(16)
        reopened_view = document.getObject(view_name)
        reopened_page = document.getObject(page_name)
        reopened_source = document.getObject(source_name)
        assert reopened_view is not None and reopened_page is not None
        assert reopened_source is not None and reopened_view in tuple(reopened_page.Views)
        try:
            reopened_cache = reopened_view.getPrecomputedDraft()
        except Exception as exc:
            raise AssertionError(
                "Draft cache was not retained across reopen: "
                f"symbol_bytes={len(str(reopened_view.PrecomputedDraftSymbol).encode())}, "
                f"source_state={reopened_view.PrecomputedDraftSourceState!r}"
            ) from exc
        assert reopened_cache == {
            "symbol": str(reopened_view.Symbol),
            "source_state_sha256": source_fingerprint_before_reopen.state_sha256,
        }, reopened_cache
        source_fingerprint_after_reopen = draft_source_fingerprint(reopened_source)
        if (
            source_fingerprint_after_reopen.state_sha256
            != source_fingerprint_before_reopen.state_sha256
        ):
            raise AssertionError(
                "Draft source state changed across save/reopen: "
                f"{source_fingerprint_before_reopen.state_sha256} -> "
                f"{source_fingerprint_after_reopen.state_sha256}"
            )
        reopened_state = drawing_draft_view_state(reopened_view)
        assert reopened_state == state, (state, reopened_state)
        assert str(reopened_view.VibeCADTimelineRole) == "operation"

        document.openTransaction("Verify Draft cache invalidation")
        cache_transaction = int(document.getBookedTransactionID())
        try:
            reopened_source.Label = "Changed exact Draft source"
            assert draft_source_fingerprint(reopened_source).state_sha256 != (
                source_fingerprint_after_reopen.state_sha256
            )
            assert not str(reopened_view.PrecomputedDraftSymbol)
            assert not str(reopened_view.PrecomputedDraftSourceState)
            try:
                reopened_view.getPrecomputedDraft()
            except RuntimeError:
                pass
            else:
                raise AssertionError("A changed Draft source retained its stale SVG cache")
        except Exception:
            App.closeActiveTransaction(True, cache_transaction)
            raise
        App.closeActiveTransaction(True, cache_transaction)
        _events(8)
        reopened_view = document.getObject(view_name)
        reopened_source = document.getObject(source_name)
        assert reopened_view is not None and reopened_source is not None
        assert drawing_draft_view_state(reopened_view) == state
        assert reopened_view.getPrecomputedDraft() == reopened_cache

        print(
            "VIBECAD_NATIVE_DRAWING_DRAFT_VIEW_GUI_OK "
            "human_parity=true exact_page=true exact_source=true "
            "source_presentation=true context_hash=true closed_schema=true "
            "orientation=true placement=true custom_scale=true style=true "
            "native_type=true svg_geometry=true no_task=true stale_page=true "
            "stale_source=true rollback=true cancel=true stale_commit=true "
            "selection=true visibility=true history=true undo=true redo=true "
            "reopen=true responsive=true authenticated=true offscreen=true "
            "path_private=true low_noise=true cache_invalidation=true",
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
