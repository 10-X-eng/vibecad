# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact Native complex section views."""

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
import VibeCADNativeDrawingComplexSectionRuntime as ComplexRuntimeModule
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeBackgroundSchema import NATIVE_BACKGROUND_CAPABILITY_NAME
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeDrawingComplexSectionSchema import (
    DRAWING_COMPLEX_SECTION_CAPABILITY_NAME,
)
from VibeCADNativeDrawingPageSchema import DRAWING_PAGE_CAPABILITY_NAMES
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


def _create_model_inputs(document):
    document.openTransaction("Create exact complex-section inputs")
    transaction = int(document.getBookedTransactionID())
    try:
        source = document.addObject("Part::Feature", "DrawingSource")
        source.Label = "Complex Section Source"
        source.Shape = Part.makeBox(36.0, 24.0, 12.0)
        document.publishProvisionalTimelineOperationBlock(source, (), ())
        profile = document.addObject("Part::Feature", "SectionProfile")
        profile.Label = "Complex Section Profile"
        profile.Shape = Part.makePolygon(
            [
                App.Vector(8.0, 12.0, 0.0),
                App.Vector(18.0, 12.0, 0.0),
                App.Vector(18.0, 12.0, 12.0),
                App.Vector(28.0, 12.0, 12.0),
            ]
        )
        document.publishProvisionalTimelineOperationBlock(profile, (), ())
        assert document.recompute([source, profile], True, True) is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    return source, profile


def _create_base_view(document, page, source):
    document.openTransaction("Create exact complex-section base view")
    transaction = int(document.getBookedTransactionID())
    try:
        base = document.addObject("TechDraw::DrawViewPart", "ComplexSectionBase")
        base.Label = "Complex Section Base"
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
            return base
        base.touch()
        assert document.recompute([source, base, page], True, True) is not False
        time.sleep(0.01)
    raise AssertionError("Complex-section base view produced no projection")


def _turn(surface, registry) -> NativeTurnSnapshot:
    page_definition = registry.definition(DRAWING_PAGE_CAPABILITY_NAMES[0])
    definition = registry.definition(DRAWING_COMPLEX_SECTION_CAPABILITY_NAME)
    job_definition = registry.definition(NATIVE_BACKGROUND_CAPABILITY_NAME)
    assert all(
        item is not None
        for item in (page_definition, definition, job_definition)
    )
    page_schema = page_definition.provider_schema(("page_default",))
    schema = definition.provider_schema(("create_complex_section_view",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "path" not in encoded.casefold()
    for field in (
        "expected_state_sha256",
        "base_view",
        "profile",
        "view_direction_on_base",
        "projection_strategy",
        "scale",
    ):
        assert field in encoded
    for hidden in (
        "section_normal",
        "x_direction",
        "rotation_degrees",
        "profile_subelements",
        "fuse_before_cut",
    ):
        assert hidden not in encoded
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(
                DRAWING_PAGE_CAPABILITY_NAMES[0],
                DRAWING_COMPLEX_SECTION_CAPABILITY_NAME,
                NATIVE_BACKGROUND_CAPABILITY_NAME,
            ),
            schemas=(
                page_schema,
                schema,
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
    base_state: dict,
    profile_state: dict,
    *,
    strategy: str = "aligned",
) -> dict:
    return {
        "operation": "create_complex_section_view",
        "label": f"Native {strategy.replace('_', ' ').title()} Section A-A",
        "symbol": "A",
        "page": {
            "object_name": page_state["object_name"],
            "expected_state_sha256": page_state["state_sha256"],
        },
        "base_view": {
            "object_name": base_state["object_name"],
            "expected_state_sha256": base_state["state_sha256"],
        },
        "profile": {
            "object_name": profile_state["object_name"],
            "expected_state_sha256": profile_state["state_sha256"],
        },
        "view_direction_on_base": {"x": 1.0, "y": 0.0},
        "projection_strategy": strategy,
        "scale": {"kind": "custom", "value": 1.0},
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
        decorations.SetInt("CutSurfaceDisplay", 2)
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-drawing-complex-section-"
        )
        save_path = Path(temporary.name) / "native-drawing-complex-section.FCStd"
        controller, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["TechDraw_ComplexSection"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.transaction_behavior,
            plan.background_required,
        ) == (
            DRAWING_COMPLEX_SECTION_CAPABILITY_NAME,
            "create_complex_section_view",
            "ExactDrawingPageBaseViewWholeProfileStrategyAndScale",
            "background",
            True,
        )

        document = App.newDocument("NativeDrawingComplexSectionGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        source, profile = _create_model_inputs(document)

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-drawing-complex-section-gui")

        def reauthorize() -> None:
            require_frozen_native_surface(frozen, controller)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state_store,
            undo_ledger=ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(
                controller
            ).surface_id,
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
                f"native-drawing-complex-section-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        def wait_for_job(job_id: str, *, timeout: float = 60.0) -> dict:
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
            raise AssertionError(
                f"Background complex-section job {job_id} did not finish"
            )

        page_result = call(
            DRAWING_PAGE_CAPABILITY_NAMES[0],
            {"operation": "page_default"},
        )
        _events(12)
        page = document.getObject(page_result["page"]["object_name"])
        assert page is not None
        base = _create_base_view(document, page, source)
        source.ViewObject.Visibility = True
        profile.ViewObject.Visibility = True
        base.ViewObject.Visibility = True
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(base)
        Gui.Selection.addSelection(profile)
        selection_before = _selection()
        visibility_before = tuple(
            bool(obj.ViewObject.Visibility) for obj in (source, profile, base)
        )
        page_state = drawing_page_state(page)
        base_state = drawing_view_state(base)
        source_state = drawing_source_state(source)
        profile_state = drawing_source_state(profile)
        arguments = _arguments(page_state, base_state, profile_state)
        dispatcher = refresh_dispatcher()

        active = build_active_snapshot(
            document,
            "drawing",
            state_store.snapshot(str(document.Uid)),
            selection={
                "document_uid": str(document.Uid),
                "selected_count": 2,
                "items": [
                    {
                        "object": {
                            "document_uid": str(document.Uid),
                            "object_name": obj.Name,
                            "type_id": obj.TypeId,
                        },
                        "subelements": [],
                    }
                    for obj in (base, profile)
                ],
            },
        )
        domain = active["domain"]
        assert domain["active_page_resolution"] == "selection"
        assert domain["selected_sources"][0]["state_sha256"] == profile_state[
            "state_sha256"
        ]
        assert len(json.dumps(active, separators=(",", ":")).encode()) < 64 * 1024

        invalid = dict(arguments)
        invalid["section_normal"] = [1.0, 0.0, 0.0]
        rejected = call(
            DRAWING_COMPLEX_SECTION_CAPABILITY_NAME,
            invalid,
            succeeds=False,
        )
        assert rejected["error_code"] == "NATIVE_ARGUMENTS_INVALID"

        stale_profile = json.loads(json.dumps(arguments))
        stale_profile["profile"]["expected_state_sha256"] = "0" * 64
        rejected = call(
            DRAWING_COMPLEX_SECTION_CAPABILITY_NAME,
            stale_profile,
            succeeds=False,
        )
        assert (
            rejected["error_code"]
            == "NATIVE_DRAWING_COMPLEX_SECTION_PROFILE_STALE"
        )

        original_verify = ComplexRuntimeModule.verify_complex_section_view_create

        def fail_verify(_document, _draft):
            raise RuntimeError("injected complex-section publication failure")

        ComplexRuntimeModule.verify_complex_section_view_create = fail_verify
        try:
            rollback_start = call(
                DRAWING_COMPLEX_SECTION_CAPABILITY_NAME,
                arguments,
            )
            rolled_back = wait_for_job(rollback_start["job"]["job_id"])
        finally:
            ComplexRuntimeModule.verify_complex_section_view_create = original_verify
        assert rolled_back["phase"] == "failed", rolled_back
        assert rolled_back["failure"]["error_code"] == "NATIVE_POSTCONDITION_FAILED"
        assert tuple(page.Views) == (base,)

        cancelled_start = call(DRAWING_COMPLEX_SECTION_CAPABILITY_NAME, arguments)
        cancelled_request = call(
            NATIVE_BACKGROUND_CAPABILITY_NAME,
            {"operation": "cancel", "job_id": cancelled_start["job"]["job_id"]},
        )
        assert cancelled_request["cancel_accepted"] is True
        cancelled = wait_for_job(cancelled_start["job"]["job_id"])
        assert cancelled["phase"] == "cancelled", cancelled
        assert tuple(page.Views) == (base,)

        original_execute = ComplexRuntimeModule.execute_complex_section_projection
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

        ComplexRuntimeModule.execute_complex_section_projection = gated_execute
        try:
            stale_start = call(DRAWING_COMPLEX_SECTION_CAPABILITY_NAME, arguments)
            deadline = time.monotonic() + 10.0
            while not worker_ready.is_set() and time.monotonic() < deadline:
                _events(2)
                time.sleep(0.01)
            assert worker_ready.is_set()
            document.openTransaction("Change exact profile during sectioning")
            transaction = int(document.getBookedTransactionID())
            try:
                profile.Shape = Part.makePolygon(
                    [
                        App.Vector(7.0, 12.0, 0.0),
                        App.Vector(18.0, 12.0, 0.0),
                        App.Vector(18.0, 12.0, 12.0),
                        App.Vector(29.0, 12.0, 12.0),
                    ]
                )
                assert document.recompute([profile], True, True) is not False
            except Exception:
                App.closeActiveTransaction(True, transaction)
                raise
            App.closeActiveTransaction(False, transaction)
            worker_release.set()
            stale_result = wait_for_job(stale_start["job"]["job_id"])
        finally:
            worker_release.set()
            ComplexRuntimeModule.execute_complex_section_projection = original_execute
        assert stale_result["phase"] == "failed", stale_result
        assert stale_result["failure"]["error_code"] in {
            "NATIVE_DRAWING_COMPLEX_SECTION_PROFILE_STALE",
            "NATIVE_REVISION_CONFLICT",
        }
        document.undo()
        _events(12)
        source = document.getObject(source.Name)
        profile = document.getObject(profile.Name)
        base = document.getObject(base.Name)
        page = document.getObject(page.Name)
        assert drawing_source_state(source)["state_sha256"] == source_state[
            "state_sha256"
        ]
        assert drawing_source_state(profile)["state_sha256"] == profile_state[
            "state_sha256"
        ]
        assert drawing_view_state(base)["state_sha256"] == base_state["state_sha256"]
        dispatcher = refresh_dispatcher()

        # The human task panel exposes all three strategies. Exercise the two
        # alternate algorithms as complete Native operations, then undo each
        # before the aligned operation's full lifecycle below.
        for strategy, expected_strategy in (
            ("offset", "Offset"),
            ("no_parallel", "NoParallel"),
        ):
            strategy_arguments = _arguments(
                drawing_page_state(page),
                drawing_view_state(base),
                drawing_source_state(profile),
                strategy=strategy,
            )
            strategy_start = call(
                DRAWING_COMPLEX_SECTION_CAPABILITY_NAME,
                strategy_arguments,
            )
            strategy_job = wait_for_job(strategy_start["job"]["job_id"])
            assert strategy_job["phase"] == "completed", strategy_job
            strategy_name = strategy_job["result"]["view"]["object_name"]
            strategy_view = document.getObject(strategy_name)
            assert strategy_view is not None
            strategy_state = drawing_view_state(strategy_view)
            assert strategy_state["section"]["complex"][
                "projection_strategy"
            ] == expected_strategy
            assert strategy_state["visible_edge_count"] >= 1
            assert strategy_state["section"]["section_face_count"] >= 1
            document.undo()
            _events(12)
            assert document.getObject(strategy_name) is None
            page = document.getObject(page.Name)
            base = document.getObject(base.Name)
            profile = document.getObject(profile.Name)
            assert tuple(page.Views) == (base,)
            assert drawing_page_state(page)["state_sha256"] == page_state[
                "state_sha256"
            ]
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
        started = call(DRAWING_COMPLEX_SECTION_CAPABILITY_NAME, arguments)
        assert time.monotonic() - started_at < 2.0
        completed = wait_for_job(started["job"]["job_id"])
        heartbeat.stop()
        assert completed["phase"] == "completed", completed
        assert ui_ticks > 0
        result = completed["result"]
        encoded_result = json.dumps(result, separators=(",", ":"))
        assert len(encoded_result.encode()) < 32 * 1024
        assert "path" not in encoded_result.casefold()

        view_name = result["view"]["object_name"]
        view = document.getObject(view_name)
        assert view is not None and view.TypeId == "TechDraw::DrawComplexSection"
        assert str(view.CutSurfaceDisplay) == "SvgHatch"
        state = drawing_view_state(view)
        assert state == result["view"]
        section_state = state["section"]
        assert section_state["base_view"] == {
            "object_name": base.Name,
            "state_sha256": base_state["state_sha256"],
        }
        assert section_state["complex"] == {
            "profile": {
                "object_name": profile.Name,
                "state_sha256": profile_state["state_sha256"],
            },
            "projection_strategy": "Aligned",
        }
        assert section_state["section_face_count"] >= 1
        assert state["visible_edge_count"] >= 1
        cache = view.getPrecomputedComplexSection()
        assert len(tuple(cache["cut_pieces"].Solids)) >= 1
        assert len(tuple(cache["section_faces"].Faces)) >= 1
        assert len(tuple(cache["prepared_shape"].Faces)) >= 1
        assert tuple(page.Views) == (base, view)
        assert str(view.VibeCADTimelineRole) == "operation"
        assert getattr(view, "VibeCADTimelineOwner", None) is None
        assert int(document.UndoCount) == undo_before + 1
        assert _selection() == selection_before
        assert tuple(
            bool(obj.ViewObject.Visibility) for obj in (source, profile, base)
        ) == visibility_before
        assert not Gui.Control.activeDialog()
        operations_after = tuple(document.VibeCADTimeline.Operations)

        stale_page = _arguments(page_state, base_state, profile_state)
        rejected = call(
            DRAWING_COMPLEX_SECTION_CAPABILITY_NAME,
            stale_page,
            succeeds=False,
        )
        assert rejected["error_code"] == "NATIVE_DRAWING_PAGE_STALE"

        document.undo()
        _events(12)
        assert document.getObject(view_name) is None
        page = document.getObject(page.Name)
        base = document.getObject(base.Name)
        assert tuple(page.Views) == (base,)
        document.redo()
        _events(12)
        view = document.getObject(view_name)
        page = document.getObject(page.Name)
        assert view is not None and tuple(page.Views) == (base, view)
        assert drawing_view_state(view) == state
        assert len(
            tuple(view.getPrecomputedComplexSection()["section_faces"].Faces)
        ) >= 1
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
        assert reopened_view in tuple(reopened_page.Views)
        reopened_state = drawing_view_state(reopened_view)
        restore_deadline = time.monotonic() + 5.0
        while reopened_state != state and time.monotonic() < restore_deadline:
            _events(2)
            time.sleep(0.01)
            reopened_state = drawing_view_state(reopened_view)
        state_differences = {
            key: {"before": state.get(key), "reopened": reopened_state.get(key)}
            for key in sorted(set(state) | set(reopened_state))
            if state.get(key) != reopened_state.get(key)
        }
        assert reopened_state == state, state_differences
        assert len(
            tuple(
                reopened_view.getPrecomputedComplexSection()[
                    "section_faces"
                ].Faces
            )
        ) >= 1
        assert str(reopened_view.VibeCADTimelineRole) == "operation"

        print(
            "VIBECAD_NATIVE_DRAWING_COMPLEX_SECTION_GUI_OK "
            "exact_page=true exact_base=true exact_profile=true exact_sources=true "
            "context_hash=true closed_schema=true derived_plane=true "
            "strategies=3 deterministic_placement=true custom_scale=true "
            "native_type=true cut_geometry=true prepared_geometry=true no_task=true "
            "stale_page=true stale_profile=true rollback=true cancel=true "
            "stale_commit=true selection=true visibility=true history=true "
            "undo=true redo=true reopen=true responsive=true path_private=true "
            "low_noise=true",
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
