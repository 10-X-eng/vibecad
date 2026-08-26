# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact Native straight section views."""

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
import VibeCADNativeDrawingSectionRuntime as SectionRuntimeModule
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeBackgroundSchema import NATIVE_BACKGROUND_CAPABILITY_NAME
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeDrawingPageSchema import DRAWING_PAGE_CAPABILITY_NAMES
from VibeCADNativeDrawingSectionSchema import DRAWING_SECTION_CAPABILITY_NAME
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewState import drawing_source_state, drawing_view_state
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
    document.openTransaction("Create exact section source")
    transaction = int(document.getBookedTransactionID())
    try:
        source = document.addObject("Part::Feature", "DrawingSource")
        source.Label = "Section Source"
        source.Shape = Part.makeBox(36.0, 24.0, 12.0)
        document.publishProvisionalTimelineOperationBlock(source, (), ())
        assert document.recompute([source], True, True) is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    return source


def _create_base_view(document, page, source):
    document.openTransaction("Create exact section base view")
    transaction = int(document.getBookedTransactionID())
    try:
        base = document.addObject("TechDraw::DrawViewPart", "SectionBaseView")
        base.Label = "Section Base View"
        base.Source = [source]
        base.Direction = App.Vector(0.0, -1.0, 0.0)
        base.XDirection = App.Vector(1.0, 0.0, 0.0)
        base.ScaleType = "Custom"
        base.Scale = 1.25
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
        base.touch()
        assert document.recompute([source, base, page], True, True) is not False
        time.sleep(0.01)
    assert drawing_view_state(base)["visible_edge_count"]
    return base


def _turn(surface, registry) -> NativeTurnSnapshot:
    page_definition = registry.definition(DRAWING_PAGE_CAPABILITY_NAMES[0])
    section_definition = registry.definition(DRAWING_SECTION_CAPABILITY_NAME)
    job_definition = registry.definition(NATIVE_BACKGROUND_CAPABILITY_NAME)
    assert all(
        item is not None
        for item in (page_definition, section_definition, job_definition)
    )
    page_schema = page_definition.provider_schema(("page_default",))
    section_schema = section_definition.provider_schema(("create_section_view",))
    encoded = json.dumps(section_schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "path" not in encoded.casefold()
    for field in (
        "expected_state_sha256",
        "base_view",
        "section_origin_mm",
        "view_direction_on_base",
        "scale",
    ):
        assert field in encoded
    for hidden_implementation_detail in (
        "section_normal",
        "x_direction",
        "rotation_degrees",
        "position",
        "fuse_before_cut",
    ):
        assert hidden_implementation_detail not in encoded
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(
                DRAWING_PAGE_CAPABILITY_NAMES[0],
                DRAWING_SECTION_CAPABILITY_NAME,
                NATIVE_BACKGROUND_CAPABILITY_NAME,
            ),
            schemas=(
                page_schema,
                section_schema,
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
        "operation": "create_section_view",
        "label": "Native Section A-A",
        "symbol": "A",
        "page": {
            "object_name": page_state["object_name"],
            "expected_state_sha256": page_state["state_sha256"],
        },
        "base_view": {
            "object_name": base_state["object_name"],
            "expected_state_sha256": base_state["state_sha256"],
        },
        "section_origin_mm": {"x_mm": 18.0, "y_mm": 12.0, "z_mm": 6.0},
        "view_direction_on_base": {"x": 1.0, "y": 0.0},
        "scale": {"kind": "custom", "value": 1.25},
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    decorations = App.ParamGet(
        "User parameter:BaseApp/Preferences/Mod/TechDraw/Decorations"
    )
    previous_cut_surface_display = decorations.GetInt("CutSurfaceDisplay", 2)
    exit_code = 1
    try:
        # Exercise the shipped default SVG hatch path independently from the
        # preference state of the machine running this lifecycle gate.
        decorations.SetInt("CutSurfaceDisplay", 2)
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-drawing-section-view-"
        )
        save_path = Path(temporary.name) / "native-drawing-section-view.FCStd"
        controller, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["TechDraw_SectionView"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.transaction_behavior,
            plan.background_required,
        ) == (
            DRAWING_SECTION_CAPABILITY_NAME,
            "create_section_view",
            "ExactDrawingPageBaseViewSectionPlaneAndScale",
            "background",
            True,
        )

        document = App.newDocument("NativeDrawingSectionViewGate")
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
        ledger.begin_run("native-drawing-section-view-gui")

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
                f"native-drawing-section-view-{call_index}",
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
            raise AssertionError(f"Background section-view job {job_id} did not finish")

        page_result = call(DRAWING_PAGE_CAPABILITY_NAMES[0], {"operation": "page_default"})
        _events(12)
        page = document.getObject(page_result["page"]["object_name"])
        assert page is not None
        base = _create_base_view(document, page, source)
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
        dispatcher = refresh_dispatcher()

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
        assert view_summary["object_name"] == base.Name
        assert view_summary["state_sha256"] == base_state["state_sha256"]
        assert len(json.dumps(active, separators=(",", ":")).encode()) < 64 * 1024

        invalid = _arguments(page_state, base_state)
        invalid["section_normal"] = [1.0, 0.0, 0.0]
        rejected = call(DRAWING_SECTION_CAPABILITY_NAME, invalid, succeeds=False)
        assert rejected["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(page.Views) == (base,)

        stale_base = _arguments(page_state, base_state)
        stale_base["base_view"]["expected_state_sha256"] = "0" * 64
        rejected = call(DRAWING_SECTION_CAPABILITY_NAME, stale_base, succeeds=False)
        assert rejected["error_code"] == "NATIVE_DRAWING_SECTION_BASE_STALE"

        zero_direction = _arguments(page_state, base_state)
        zero_direction["view_direction_on_base"] = {"x": 0.0, "y": 0.0}
        rejected = call(DRAWING_SECTION_CAPABILITY_NAME, zero_direction, succeeds=False)
        assert rejected["error_code"] == "NATIVE_DRAWING_SECTION_PARAMETERS_INVALID"

        arguments = _arguments(page_state, base_state)
        original_verify = SectionRuntimeModule.verify_section_view_create

        def fail_verify(_document, _draft):
            raise RuntimeError("injected straight-section publication failure")

        SectionRuntimeModule.verify_section_view_create = fail_verify
        try:
            rollback_start = call(DRAWING_SECTION_CAPABILITY_NAME, arguments)
            rolled_back = wait_for_job(rollback_start["job"]["job_id"])
        finally:
            SectionRuntimeModule.verify_section_view_create = original_verify
        assert rolled_back["phase"] == "failed", rolled_back
        assert (
            rolled_back["failure"]["error_code"] == "NATIVE_POSTCONDITION_FAILED"
        ), rolled_back
        assert tuple(page.Views) == (base,)
        assert document.getObject("SectionView") is None

        cancelled_start = call(DRAWING_SECTION_CAPABILITY_NAME, arguments)
        cancelled_request = call(
            NATIVE_BACKGROUND_CAPABILITY_NAME,
            {"operation": "cancel", "job_id": cancelled_start["job"]["job_id"]},
        )
        assert cancelled_request["cancel_accepted"] is True, cancelled_request
        cancelled = wait_for_job(cancelled_start["job"]["job_id"])
        assert cancelled["phase"] == "cancelled", cancelled
        assert tuple(page.Views) == (base,)

        original_execute = SectionRuntimeModule.execute_section_projection
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

        SectionRuntimeModule.execute_section_projection = gated_execute
        try:
            stale_start = call(DRAWING_SECTION_CAPABILITY_NAME, arguments)
            deadline = time.monotonic() + 10.0
            while not worker_ready.is_set() and time.monotonic() < deadline:
                _events(2)
                time.sleep(0.01)
            assert worker_ready.is_set()
            document.openTransaction("Change exact source during sectioning")
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
            SectionRuntimeModule.execute_section_projection = original_execute
        assert stale_result["phase"] == "failed", stale_result
        assert stale_result["failure"]["error_code"] in {
            "NATIVE_DRAWING_SECTION_BASE_STALE",
            "NATIVE_DRAWING_SECTION_SOURCE_STALE",
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
        dispatcher = refresh_dispatcher()

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
        started = call(DRAWING_SECTION_CAPABILITY_NAME, arguments)
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
        section = document.getObject(view_name)
        assert section is not None and section.TypeId == "TechDraw::DrawViewSection"
        assert str(section.CutSurfaceDisplay) == "SvgHatch"
        state = drawing_view_state(section)
        assert state == result["view"]
        section_state = state["section"]
        assert section_state["base_view"] == {
            "object_name": base.Name,
            "state_sha256": base_state["state_sha256"],
        }
        assert section_state["origin_mm"] == [18.0, 12.0, 6.0]
        assert section_state["normal"] == [-1.0, 0.0, 0.0]
        assert state["x_direction"] == [0.0, 0.0, -1.0]
        assert section_state["rotation_degrees"] == -90.0
        assert section_state["symbol"] == "A"
        assert section_state["direction_mode"] == "Aligned"
        assert section_state["section_face_count"] >= 1
        assert state["scale_type"] == "Custom" and state["scale"] == 1.25
        assert state["line_visibility"] == base_state["line_visibility"]
        assert state["x_mm"] == page_state["template_geometry"]["width_mm"] / 2.0
        assert state["y_mm"] == page_state["template_geometry"]["height_mm"] / 2.0
        cache = section.getPrecomputedSection()
        assert len(tuple(cache["cut_pieces"].Solids)) >= 1
        assert len(tuple(cache["section_faces"].Faces)) >= 1
        assert tuple(page.Views) == (base, section)
        assert str(section.VibeCADTimelineRole) == "operation"
        assert getattr(section, "VibeCADTimelineOwner", None) is None
        assert int(document.UndoCount) == undo_before + 1
        assert _selection() == selection_before
        assert (
            bool(source.ViewObject.Visibility),
            bool(base.ViewObject.Visibility),
        ) == visibility_before
        assert not Gui.Control.activeDialog()
        operations_after = tuple(document.VibeCADTimeline.Operations)

        stale_page = _arguments(page_state, base_state)
        rejected = call(DRAWING_SECTION_CAPABILITY_NAME, stale_page, succeeds=False)
        assert rejected["error_code"] == "NATIVE_DRAWING_PAGE_STALE"

        document.undo()
        _events(12)
        assert document.getObject(view_name) is None
        page = document.getObject(page.Name)
        base = document.getObject(base.Name)
        assert page is not None and tuple(page.Views) == (base,)
        document.redo()
        _events(12)
        section = document.getObject(view_name)
        page = document.getObject(page.Name)
        assert section is not None and tuple(page.Views) == (base, section)
        assert drawing_view_state(section) == state
        assert len(tuple(section.getPrecomputedSection()["section_faces"].Faces)) >= 1
        assert tuple(document.VibeCADTimeline.Operations) == operations_after

        page_name = str(page.Name)
        document.saveAs(str(save_path))
        document_name = document.Name
        App.closeDocument(document_name)
        document = App.openDocument(str(save_path))
        _events(16)
        reopened_section = document.getObject(view_name)
        reopened_page = document.getObject(page_name)
        assert reopened_section is not None and reopened_page is not None
        assert reopened_section in tuple(reopened_page.Views)
        reopened_state = drawing_view_state(reopened_section)
        restore_deadline = time.monotonic() + 5.0
        while reopened_state != state and time.monotonic() < restore_deadline:
            _events(2)
            time.sleep(0.01)
            reopened_state = drawing_view_state(reopened_section)
        assert reopened_state == state
        assert len(tuple(reopened_section.getPrecomputedSection()["section_faces"].Faces)) >= 1
        assert str(reopened_section.VibeCADTimelineRole) == "operation"

        print(
            "VIBECAD_NATIVE_DRAWING_SECTION_VIEW_GUI_OK "
            "exact_page=true exact_base=true exact_sources=true context_hash=true "
            "closed_schema=true derived_plane=true deterministic_placement=true "
            "custom_scale=true line_style=true native_type=true cut_geometry=true "
            "no_task=true stale_page=true stale_base=true stale_source=true "
            "zero_direction_guard=true rollback=true cancel=true stale_commit=true "
            "selection=true visibility=true history=true undo=true redo=true "
            "reopen=true responsive=true path_private=true low_noise=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        decorations.SetInt("CutSurfaceDisplay", previous_cut_surface_display)
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
