# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact Native Drawing view stacking."""

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
import TechDrawGui

import VibeCADGui as VibeGui
import VibeCADNativeDrawingStackRuntime as StackRuntimeModule
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeDrawingPageSchema import DRAWING_PAGE_CAPABILITY_NAMES
from VibeCADNativeDrawingStackSchema import DRAWING_STACK_CAPABILITY_NAME
from VibeCADNativeDrawingStackState import drawing_stack_state
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
    document.openTransaction("Create exact Drawing stack source")
    transaction = int(document.getBookedTransactionID())
    try:
        source = document.addObject("Part::Feature", "DrawingStackSource")
        source.Label = "Drawing Stack Source"
        source.Shape = Part.makeBox(40.0, 24.0, 12.0)
        document.publishProvisionalTimelineOperationBlock(source, (), ())
        assert document.recompute([source], True, True) is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    return source


def _create_view(document, page, source, name, x_mm, y_mm):
    document.openTransaction(f"Create {name}")
    transaction = int(document.getBookedTransactionID())
    try:
        view = document.addObject("TechDraw::DrawViewPart", name)
        view.Label = name.replace("View", " View")
        view.Source = [source]
        view.Direction = App.Vector(0.0, -1.0, 0.0)
        view.XDirection = App.Vector(1.0, 0.0, 0.0)
        view.ScaleType = "Custom"
        view.Scale = 1.0
        view.X = x_mm
        view.Y = y_mm
        document.publishProvisionalTimelineOperationBlock(view, (), ())
        assert int(page.addView(view)) >= 1
        assert document.recompute([source, view, page], True, True) is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    _events(8)
    return view


def _create_clip(document, page, view):
    document.openTransaction("Create nested Drawing stack scope")
    transaction = int(document.getBookedTransactionID())
    try:
        clip = document.addObject("TechDraw::DrawViewClip", "StackClip")
        clip.Label = "Stack Clip"
        clip.X = 140.0
        clip.Y = 80.0
        clip.Width = 90.0
        clip.Height = 55.0
        document.publishProvisionalTimelineOperationBlock(clip, (), ())
        assert int(page.addView(clip)) >= 1
        clip.addView(view)
        assert document.recompute([clip, page], True, True) is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    _events(12)
    return clip


def _turn(surface, registry) -> NativeTurnSnapshot:
    page_definition = registry.definition(DRAWING_PAGE_CAPABILITY_NAMES[0])
    stack_definition = registry.definition(DRAWING_STACK_CAPABILITY_NAME)
    assert page_definition is not None and stack_definition is not None
    page_schema = page_definition.provider_schema(("page_default",))
    operations = ("stack_top", "stack_bottom", "stack_up", "stack_down")
    stack_schema = stack_definition.provider_schema(operations)
    encoded = json.dumps(stack_schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "path" not in encoded.casefold()
    assert len(encoded.encode()) < 8 * 1024
    for field in ("expected_state_sha256", "page", "views", "maxItems"):
        assert field in encoded
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(DRAWING_PAGE_CAPABILITY_NAMES[0], DRAWING_STACK_CAPABILITY_NAME),
            schemas=(page_schema, stack_schema),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _target(state: dict) -> dict:
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _arguments(operation: str, page, *views) -> dict:
    return {
        "operation": operation,
        "page": _target(drawing_page_state(page)),
        "views": [_target(drawing_stack_state(view)) for view in views],
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-drawing-stack-")
        save_path = Path(temporary.name) / "native-drawing-stack.FCStd"
        controller, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        assert plans["TechDraw_StackGroup"].classification.parent_only
        for command_id, operation in (
            ("TechDraw_StackTop", "stack_top"),
            ("TechDraw_StackBottom", "stack_bottom"),
            ("TechDraw_StackUp", "stack_up"),
            ("TechDraw_StackDown", "stack_down"),
        ):
            plan = plans[command_id]
            assert (
                plan.capability_family,
                plan.operation_variant,
                plan.exact_target_type,
                plan.transaction_behavior,
                plan.background_required,
            ) == (
                DRAWING_STACK_CAPABILITY_NAME,
                operation,
                "ExactDrawingPageAndOrderedGraphicalStackViews",
                "document",
                False,
            )

        document = App.newDocument("NativeDrawingStackGate")
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
        ledger.begin_run("native-drawing-stack-gui")

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
                f"native-drawing-stack-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        page_result = call(DRAWING_PAGE_CAPABILITY_NAMES[0], {"operation": "page_default"})
        page = document.getObject(page_result["page"]["object_name"])
        assert page is not None
        page.ViewObject.show()
        _events(16)
        first = _create_view(document, page, source, "StackViewOne", 55.0, 75.0)
        second = _create_view(document, page, source, "StackViewTwo", 125.0, 75.0)
        third = _create_view(document, page, source, "StackViewThree", 195.0, 75.0)
        assert all(drawing_stack_state(view)["available"] for view in (first, second, third))

        # The four shipped commands remain the behavioral oracle.
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(first)
        Gui.runCommand("TechDraw_StackTop")
        assert int(first.ViewObject.StackOrder) == 1
        Gui.runCommand("TechDraw_StackBottom")
        assert int(first.ViewObject.StackOrder) == -1
        Gui.runCommand("TechDraw_StackUp")
        assert int(first.ViewObject.StackOrder) == 0
        Gui.runCommand("TechDraw_StackDown")
        assert int(first.ViewObject.StackOrder) == -1
        first.ViewObject.StackOrder = 0
        _events(8)
        dispatcher = refresh_dispatcher()

        source.ViewObject.Visibility = True
        first.ViewObject.Visibility = True
        second.ViewObject.Visibility = True
        third.ViewObject.Visibility = True
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source)
        Gui.Selection.addSelection(first)
        selection_before = _selection()
        visibility_before = tuple(
            bool(obj.ViewObject.Visibility) for obj in (source, first, second, third)
        )
        history_before = tuple(document.VibeCADTimeline.Operations)

        result = call(DRAWING_STACK_CAPABILITY_NAME, _arguments("stack_up", page, first))
        assert result["views"][0]["stack_order"] == 1
        assert result["assistant_undo_available"] is True
        assert int(first.ViewObject.StackOrder) == 1
        result = call(DRAWING_STACK_CAPABILITY_NAME, _arguments("stack_down", page, first))
        assert result["views"][0]["stack_order"] == 0

        result = call(
            DRAWING_STACK_CAPABILITY_NAME,
            _arguments("stack_top", page, first, second),
        )
        assert [state["stack_order"] for state in result["views"]] == [1, 2]
        result = call(
            DRAWING_STACK_CAPABILITY_NAME,
            _arguments("stack_bottom", page, first, second),
        )
        assert [state["stack_order"] for state in result["views"]] == [-1, -2]
        assert _selection() == selection_before
        assert tuple(
            bool(obj.ViewObject.Visibility) for obj in (source, first, second, third)
        ) == visibility_before
        assert tuple(document.VibeCADTimeline.Operations) == history_before
        encoded_result = json.dumps(result, separators=(",", ":"))
        assert len(encoded_result.encode()) < 24 * 1024
        assert "scope_items" not in encoded_result
        assert "path" not in encoded_result.casefold()
        assert not Gui.Control.activeDialog()

        stale_arguments = _arguments("stack_up", page, third)
        TechDrawGui.stackView(third, "up")
        dispatcher = refresh_dispatcher()
        rejected = call(DRAWING_STACK_CAPABILITY_NAME, stale_arguments, succeeds=False)
        assert rejected["error_code"] == "NATIVE_DRAWING_STACK_TARGET_STALE"
        TechDrawGui.stackView(third, "down")
        dispatcher = refresh_dispatcher()

        stale_page = _arguments("stack_up", page, third)
        page.KeepUpdated = not bool(page.KeepUpdated)
        dispatcher = refresh_dispatcher()
        rejected = call(DRAWING_STACK_CAPABILITY_NAME, stale_page, succeeds=False)
        assert rejected["error_code"] == "NATIVE_DRAWING_PAGE_STALE"
        page.KeepUpdated = not bool(page.KeepUpdated)
        dispatcher = refresh_dispatcher()

        second_page_result = call(
            DRAWING_PAGE_CAPABILITY_NAMES[0],
            {"operation": "page_default"},
        )
        second_page = document.getObject(second_page_result["page"]["object_name"])
        second_page.ViewObject.show()
        _events(12)
        other = _create_view(document, second_page, source, "OtherPageStackView", 80.0, 70.0)
        dispatcher = refresh_dispatcher()
        mismatch = {
            "operation": "stack_up",
            "page": _target(drawing_page_state(page)),
            "views": [_target(drawing_stack_state(other))],
        }
        rejected = call(DRAWING_STACK_CAPABILITY_NAME, mismatch, succeeds=False)
        assert rejected["error_code"] == "NATIVE_DRAWING_STACK_PAGE_MISMATCH"
        page.ViewObject.show()
        _events(12)

        clip = _create_clip(document, page, third)
        dispatcher = refresh_dispatcher()
        nested_before = drawing_stack_state(third)
        assert nested_before["scope_kind"] == "owner"
        nested = call(
            DRAWING_STACK_CAPABILITY_NAME,
            _arguments("stack_top", page, third),
        )
        assert nested["views"][0]["scope_kind"] == "owner"
        assert nested["views"][0]["stack_order"] == nested_before["scope_maximum_order"] + 1
        assert tuple(clip.Views) == (third,)

        rollback_before = drawing_stack_state(first)
        rollback_arguments = _arguments("stack_up", page, first)
        undo_before = int(document.UndoCount)
        original_verify = StackRuntimeModule.verify_drawing_stack

        def fail_verify(_document, _draft):
            raise RuntimeError("injected Drawing stack verification failure")

        StackRuntimeModule.verify_drawing_stack = fail_verify
        try:
            rejected = call(
                DRAWING_STACK_CAPABILITY_NAME,
                rollback_arguments,
                succeeds=False,
            )
        finally:
            StackRuntimeModule.verify_drawing_stack = original_verify
        assert rejected["error_code"] == "NATIVE_POSTCONDITION_FAILED"
        assert drawing_stack_state(first) == rollback_before
        assert int(document.UndoCount) == undo_before
        assert _selection() == selection_before

        undo_state = drawing_stack_state(first)
        changed = call(
            DRAWING_STACK_CAPABILITY_NAME,
            _arguments("stack_up", page, first),
        )["views"][0]
        assert changed["stack_order"] == undo_state["stack_order"] + 1
        document.undo()
        _events(12)
        assert drawing_stack_state(first) == undo_state
        document.redo()
        _events(12)
        assert drawing_stack_state(first)["stack_order"] == changed["stack_order"]

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
                            "object_name": first.Name,
                            "type_id": first.TypeId,
                        },
                        "subelements": [],
                    }
                ],
            },
        )
        selected = active["domain"]["selected_stack_views"]
        assert selected == [drawing_stack_state(first)]
        page_summary = next(
            item for item in active["domain"]["pages"] if item["object_name"] == page.Name
        )
        first_summary = next(
            item for item in page_summary["views"] if item["object_name"] == first.Name
        )
        assert first_summary["stack"]["state_sha256"] == selected[0]["state_sha256"]
        assert len(json.dumps(active, separators=(",", ":")).encode()) < 96 * 1024

        names = {
            "page": str(page.Name),
            "first": str(first.Name),
            "third": str(third.Name),
            "clip": str(clip.Name),
        }
        final_orders = {
            name: int(document.getObject(name).ViewObject.StackOrder)
            for name in (names["first"], names["third"])
        }
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        page = document.getObject(names["page"])
        page.ViewObject.show()
        _events(20)
        first = document.getObject(names["first"])
        third = document.getObject(names["third"])
        clip = document.getObject(names["clip"])
        assert all(obj is not None for obj in (page, first, third, clip))
        assert int(first.ViewObject.StackOrder) == final_orders[names["first"]]
        assert int(third.ViewObject.StackOrder) == final_orders[names["third"]]
        assert drawing_stack_state(first)["available"]
        assert drawing_stack_state(third)["scope_kind"] == "owner"
        assert tuple(clip.Views) == (third,)

        print(
            "VIBECAD_NATIVE_DRAWING_STACK_GUI_OK "
            "human_top=true human_bottom=true human_up=true human_down=true "
            "shared_host_primitive=true exact_page=true ordered_targets=true "
            "page_scope=true owner_scope=true stale_target=true stale_page=true "
            "cross_page_guard=true rollback=true selection=true visibility=true "
            "history=true undo=true redo=true reopen=true closed_schema=true "
            "path_private=true low_noise=true no_task=true",
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
