# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact Native Drawing active-view capture."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtGui, QtWidgets

import VibeCADGui as VibeGui
import VibeCADNativeDrawingActiveViewRuntime as ActiveViewRuntimeModule
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeDrawingActiveView import (
    drawing_active_view_image_state,
    drawing_active_viewport_state,
)
from VibeCADNativeDrawingActiveViewSchema import (
    DRAWING_ACTIVE_VIEW_CAPABILITY_NAME,
)
from VibeCADNativeDrawingPageSchema import DRAWING_PAGE_CAPABILITY_NAME
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
    document.openTransaction("Create active-view source")
    transaction = int(document.getBookedTransactionID())
    try:
        source = document.addObject("Part::Feature", "CaptureSource")
        source.Label = "Capture Source"
        source.Shape = Part.makeBox(36.0, 24.0, 12.0)
        document.publishProvisionalTimelineOperationBlock(source, (), ())
        assert document.recompute([source], True, True) is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    return source


def _turn(surface, registry) -> NativeTurnSnapshot:
    page_definition = registry.definition(DRAWING_PAGE_CAPABILITY_NAME)
    active_definition = registry.definition(DRAWING_ACTIVE_VIEW_CAPABILITY_NAME)
    assert page_definition is not None and active_definition is not None
    page_schema = page_definition.provider_schema(("page_default",))
    active_schema = active_definition.provider_schema(("create_active_view",))
    encoded = json.dumps(active_schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "path" not in encoded.casefold()
    assert "data_url" not in encoded.casefold()
    assert "expected_state_sha256" in encoded
    assert "crop" in encoded and "background" in encoded
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(
                DRAWING_PAGE_CAPABILITY_NAME,
                DRAWING_ACTIVE_VIEW_CAPABILITY_NAME,
            ),
            schemas=(page_schema, active_schema),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _arguments(page_state: dict, viewport_state: dict) -> dict:
    return {
        "operation": "create_active_view",
        "label": "Native Active View",
        "page": {
            "object_name": page_state["object_name"],
            "expected_state_sha256": page_state["state_sha256"],
        },
        "viewport": {
            "expected_state_sha256": viewport_state["state_sha256"],
        },
        "position": {"x_mm": 92.0, "y_mm": 88.0},
        "scale": 0.2,
        "crop": {
            "kind": "rectangle",
            "width_mm": 64.0,
            "height_mm": 48.0,
        },
        "background": {
            "kind": "solid",
            "rgb": {"red": 12, "green": 34, "blue": 56},
        },
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-drawing-active-view-"
        )
        save_path = Path(temporary.name) / "native-drawing-active-view.FCStd"
        controller, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        active_plan = plans["TechDraw_ActiveView"]
        assert (
            active_plan.capability_family,
            active_plan.operation_variant,
            active_plan.exact_target_type,
            active_plan.transaction_behavior,
            active_plan.background_required,
        ) == (
            DRAWING_ACTIVE_VIEW_CAPABILITY_NAME,
            "create_active_view",
            "ExactDrawingPageActive3DViewportAndCaptureSettings",
            "document",
            False,
        )

        document = App.newDocument("NativeDrawingActiveViewGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        source = _create_source(document)
        source.ViewObject.Visibility = True
        source.ViewObject.ShapeColor = (0.82, 0.66, 0.18)
        active_3d = Gui.activeDocument().activeView()
        active_3d.viewAxonometric()
        active_3d.fitAll()
        _events(16)
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
        ledger.begin_run("native-drawing-active-view-gui")

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
                f"native-drawing-active-view-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        page_result = call(
            DRAWING_PAGE_CAPABILITY_NAME,
            {"operation": "page_default"},
        )
        _events(12)
        page = document.getObject(page_result["page"]["object_name"])
        assert page is not None
        page_name = str(page.Name)

        # Exercise the shipped human command itself. Its provisional object is
        # the parity oracle for type, embedding, default frame, and task-owned
        # rollback; the Native operation below must produce that same durable
        # TechDraw view class without opening the task panel.
        objects_before_human = tuple(document.Objects)
        Gui.runCommand("TechDraw_ActiveView")
        _events(16)
        assert Gui.Control.activeDialog()
        human_objects = tuple(
            obj for obj in document.Objects if obj not in objects_before_human
        )
        assert len(human_objects) == 1
        human_view = human_objects[0]
        human_view_name = str(human_view.Name)
        assert human_view.isDerivedFrom("TechDraw::DrawViewImage")
        assert tuple(page.Views) == (human_view,)
        assert Path(str(human_view.ImageIncluded)).is_file()
        human_image = QtGui.QImage(str(human_view.ImageIncluded))
        assert (human_image.width(), human_image.height()) == (1280, 1024)
        assert bool(human_view.ViewObject.Crop) is False
        dialog = Gui.Control.activeTaskDialog()
        assert dialog is not None
        dialog.reject()
        _events(16)
        page = document.getObject(page_name)
        source = document.getObject(source.Name)
        assert page is not None and source is not None
        assert tuple(page.Views) == ()
        assert document.getObject(human_view_name) is None
        assert not Gui.Control.activeDialog()

        page_state = drawing_page_state(page)
        viewport_state = drawing_active_viewport_state(document)
        assert viewport_state["visible_geometry_count"] == 1

        snapshot = build_active_snapshot(
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
        assert snapshot["domain"]["active_3d_viewport"] == viewport_state
        assert len(json.dumps(snapshot, separators=(",", ":")).encode()) < 64 * 1024

        invalid = _arguments(page_state, viewport_state)
        invalid["image_path"] = "/tmp/not-allowed.png"
        rejected = call(DRAWING_ACTIVE_VIEW_CAPABILITY_NAME, invalid, succeeds=False)
        assert rejected["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(page.Views) == ()

        stale = _arguments(page_state, viewport_state)
        camera_before = str(active_3d.getCamera())
        active_3d.viewTop()
        _events(8)
        changed_viewport = drawing_active_viewport_state(document)
        assert changed_viewport["state_sha256"] != viewport_state["state_sha256"]
        rejected = call(DRAWING_ACTIVE_VIEW_CAPABILITY_NAME, stale, succeeds=False)
        assert rejected["error_code"] == "NATIVE_DRAWING_ACTIVE_VIEW_STALE"
        active_3d.setCamera(camera_before)
        _events(8)
        viewport_state = drawing_active_viewport_state(document)

        oversized = _arguments(page_state, viewport_state)
        oversized["crop"] = {
            "kind": "rectangle",
            "width_mm": 1000.0,
            "height_mm": 1000.0,
        }
        rejected = call(DRAWING_ACTIVE_VIEW_CAPABILITY_NAME, oversized, succeeds=False)
        assert rejected["error_code"] == "NATIVE_DRAWING_ACTIVE_VIEW_SIZE_INVALID"
        assert tuple(page.Views) == ()

        arguments = _arguments(page_state, viewport_state)
        original_verify = ActiveViewRuntimeModule.verify_active_view_create

        def fail_verify(_document, _draft):
            raise RuntimeError("injected active-view verification failure")

        ActiveViewRuntimeModule.verify_active_view_create = fail_verify
        try:
            rejected = call(
                DRAWING_ACTIVE_VIEW_CAPABILITY_NAME,
                arguments,
                succeeds=False,
            )
        finally:
            ActiveViewRuntimeModule.verify_active_view_create = original_verify
        assert rejected["error_code"] == "NATIVE_POSTCONDITION_FAILED", rejected
        page = document.getObject(page_name)
        assert page is not None and tuple(page.Views) == ()
        assert document.getObject("ActiveView") is None
        assert _selection() == selection_before
        assert bool(source.ViewObject.Visibility) is visibility_before

        page_state = drawing_page_state(page)
        viewport_state = drawing_active_viewport_state(document)
        arguments = _arguments(page_state, viewport_state)
        undo_before = int(document.UndoCount)
        result = call(DRAWING_ACTIVE_VIEW_CAPABILITY_NAME, arguments)
        _events(16)
        assert "job" not in result
        view_name = result["view"]["object_name"]
        view = document.getObject(view_name)
        assert view is not None
        state = drawing_active_view_image_state(view)
        assert state == result["view"]
        assert state["type_id"] == "TechDraw::DrawViewImage"
        assert state["x_mm"] == 92.0 and state["y_mm"] == 88.0
        assert state["scale"] == 0.2 and state["crop"] is True
        assert state["width_mm"] == 64.0 and state["height_mm"] == 48.0
        expected_width = int(round(64.0 * viewport_state["resolution_pixels_per_mm"]))
        expected_height = int(round(48.0 * viewport_state["resolution_pixels_per_mm"]))
        assert state["image_width_px"] == expected_width
        assert state["image_height_px"] == expected_height
        assert state["background"] == "solid:#0c2238"
        assert result["capture"]["size_px"] == [expected_width, expected_height]
        assert result["capture"]["sha256"] == state["image_sha256"]
        assert str(view.VibeCADTimelineRole) == "operation"
        assert tuple(page.Views) == (view,)
        assert int(document.UndoCount) == undo_before + 1
        assert _selection() == selection_before
        assert bool(source.ViewObject.Visibility) is visibility_before
        assert not Gui.Control.activeDialog()
        encoded_result = json.dumps(result, separators=(",", ":")).casefold()
        assert "path" not in encoded_result and "data_url" not in encoded_result
        assert len(encoded_result.encode()) < 16 * 1024

        embedded = Path(str(view.ImageIncluded))
        content = embedded.read_bytes()
        assert hashlib.sha256(content).hexdigest() == state["image_sha256"]
        image = QtGui.QImage(str(embedded))
        assert image.width() == expected_width and image.height() == expected_height
        corner = QtGui.QColor(image.pixel(0, 0))
        assert (corner.red(), corner.green(), corner.blue()) == (12, 34, 56)
        operations_after = tuple(document.VibeCADTimeline.Operations)

        stale_page = _arguments(page_state, viewport_state)
        rejected = call(
            DRAWING_ACTIVE_VIEW_CAPABILITY_NAME,
            stale_page,
            succeeds=False,
        )
        assert rejected["error_code"] == "NATIVE_DRAWING_PAGE_STALE"

        document.undo()
        _events(12)
        assert document.getObject(view_name) is None
        page = document.getObject(page_name)
        assert page is not None and tuple(page.Views) == ()
        document.redo()
        _events(12)
        view = document.getObject(view_name)
        page = document.getObject(page_name)
        assert view is not None and tuple(page.Views) == (view,)
        assert drawing_active_view_image_state(view) == state
        assert tuple(document.VibeCADTimeline.Operations) == operations_after

        document.saveAs(str(save_path))
        document_name = document.Name
        App.closeDocument(document_name)
        document = App.openDocument(str(save_path))
        _events(16)
        reopened_view = document.getObject(view_name)
        reopened_page = document.getObject(page_name)
        assert reopened_view is not None and reopened_page is not None
        assert tuple(reopened_page.Views) == (reopened_view,)
        assert drawing_active_view_image_state(reopened_view) == state
        assert str(reopened_view.VibeCADTimelineRole) == "operation"

        print(
            "VIBECAD_NATIVE_DRAWING_ACTIVE_VIEW_GUI_OK "
            "human_parity=true exact_page=true exact_viewport=true context_hash=true "
            "closed_schema=true main_thread=true native_type=true png=true embedded=true "
            "placement=true scale=true crop=true background=true size_bound=true "
            "stale_viewport=true stale_page=true rollback=true selection=true "
            "visibility=true history=true undo=true redo=true reopen=true "
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
