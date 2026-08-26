# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact Drawing frame presentation."""

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
import TechDrawGui

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeDrawingDimensionSupport import drawing_selection_state
from VibeCADNativeDrawingPresentationSchema import (
    DRAWING_PRESENTATION_CAPABILITY_NAMES,
)
from VibeCADNativeDrawingPresentationState import (
    drawing_frame_visibility_state,
    drawing_grid_visibility_state,
    drawing_hidden_edge_visibility_state,
    drawing_page_presentation_state,
)
from VibeCADNativeDrawingSnapshot import build_drawing_snapshot
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewState import drawing_view_state
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADNativeView import capture_screenshot
from VibeCADRibbonSurface import read_active_ribbon_surface


FRAME_PREFERENCE_PATH = "User parameter:BaseApp/Preferences/Mod/TechDraw/View"
MANUAL_FRAME_MODE = 3


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


def _selection() -> tuple:
    return tuple(
        (item.Object.Name, tuple(item.SubElementNames))
        for item in Gui.Selection.getSelectionEx()
    )


def _page_image_sha256() -> str:
    mdi = Gui.getMainWindow().findChild(QtWidgets.QMdiArea)
    assert mdi is not None and mdi.activeSubWindow() is not None
    image = mdi.activeSubWindow().grab().toImage()
    data = QtCore.QByteArray()
    buffer = QtCore.QBuffer(data)
    assert buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    buffer.close()
    return hashlib.sha256(bytes(data)).hexdigest()


def _create_fixture(document):
    document.openTransaction("Create Drawing presentation fixture")
    transaction = int(document.getBookedTransactionID())
    try:
        source = document.addObject("Part::Feature", "PresentationSource")
        source.Shape = Part.makeBox(42.0, 26.0, 9.0)
        document.publishProvisionalTimelineOperationBlock(source, (), ())
        page = document.addObject("TechDraw::DrawPage", "PresentationPage")
        page.Label = "Frame Presentation Page"
        template = document.addObject(
            "TechDraw::DrawSVGTemplate", "PresentationTemplate"
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
        view = document.addObject("TechDraw::DrawViewPart", "PresentationView")
        view.Source = [source]
        view.Direction = App.Vector(0.0, 0.0, 1.0)
        view.XDirection = App.Vector(1.0, 0.0, 0.0)
        view.ScaleType = "Custom"
        view.Scale = 1.4
        view.X = 105.0
        view.Y = 76.0
        document.publishProvisionalTimelineOperationBlock(view, (), ())
        assert int(page.addView(view)) >= 1
        assert document.recompute([source, view, page], True, True) is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    page.ViewObject.show()
    _events(28)
    assert drawing_frame_visibility_state(page)["visible"] is False
    assert drawing_hidden_edge_visibility_state(view)["visible"] is False
    return source, page, view


def _turn(surface, registry) -> NativeTurnSnapshot:
    schemas = []
    for name in DRAWING_PRESENTATION_CAPABILITY_NAMES:
        definition = registry.definition(name)
        assert definition is not None
        schema = definition.provider_schema(
            tuple(variant.operation for variant in definition.variants)
        )
        branch = schema["parameters"]["oneOf"][0]
        assert branch["additionalProperties"] is False
        if name == "drawing.show_page":
            assert branch["properties"]["operation"]["const"] == "show"
            assert "visible" not in branch["properties"]
        else:
            assert branch["properties"]["operation"]["const"] == "set_visibility"
            assert branch["properties"]["visible"]["type"] == "boolean"
        schemas.append(schema)
    encoded = json.dumps(schemas, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "selection" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 5 * 1024
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=DRAWING_PRESENTATION_CAPABILITY_NAMES,
            schemas=tuple(schemas),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _arguments(page, visible: bool) -> dict:
    return {
        "operation": "set_visibility",
        "page": {"object_name": page.Name},
        "visible": visible,
    }


def _arguments_with_internal_state(page, visible: bool) -> dict:
    arguments = _arguments(page, visible)
    arguments["page"].update(
        {
            "expected_state_sha256": drawing_page_state(page)["state_sha256"],
            "expected_frame_visibility_state_sha256": (
                drawing_frame_visibility_state(page)[
                    "frame_visibility_state_sha256"
                ]
            ),
        }
    )
    return arguments


def _show_arguments(page) -> dict:
    return {
        "operation": "show",
        "page": {"object_name": page.Name},
    }


def _grid_arguments(page, visible: bool) -> dict:
    return {
        "operation": "set_visibility",
        "page": {"object_name": page.Name},
        "visible": visible,
    }


def _hidden_edge_arguments(view, visible: bool) -> dict:
    return {
        "operation": "set_visibility",
        "view": {"object_name": view.Name},
        "visible": visible,
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    frame_preferences = App.ParamGet(FRAME_PREFERENCE_PATH)
    prior_frame_mode = int(frame_preferences.GetInt("ViewFrameMode", 0))
    exit_code = 1
    try:
        frame_preferences.SetInt("ViewFrameMode", MANUAL_FRAME_MODE)
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-drawing-presentation-"
        )
        save_path = Path(temporary.name) / "drawing-presentation.FCStd"
        controller, surface = _surface()
        action_plans = {
            item.command_id: item
            for item in resolve_native_action_inventory(surface).plans
        }
        for action_id, capability, target_type in (
            (
                "TechDraw_ToggleFrame",
                "drawing.page_frames",
                "HumanActiveDrawingPageAndExactFrameVisibilityState",
            ),
            (
                "TechDraw_ShowAll",
                "drawing.hidden_edges",
                "HumanActiveDrawingViewAndExactHiddenEdgeVisibilityState",
            ),
        ):
            plan = action_plans[action_id]
            assert (
                plan.capability_family,
                plan.operation_variant,
                plan.exact_target_type,
                plan.transaction_behavior,
                plan.background_required,
            ) == (
                capability,
                "set_visibility",
                target_type,
                "presentation",
                False,
            )

        document = App.newDocument("NativeDrawingPresentationGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        source, page, view = _create_fixture(document)
        objects_before = tuple(document.Objects)
        views_before = tuple(page.Views)
        history_before = tuple(document.VibeCADTimeline.Operations)
        visibility_before = tuple(
            bool(item.ViewObject.Visibility) for item in (source, page, view)
        )
        page_before = drawing_page_state(page)
        undo_before = int(document.UndoCount)
        transaction_before = int(document.getBookedTransactionID())
        hidden_image = _page_image_sha256()
        service = get_service()

        page_capture = capture_screenshot(
            service,
            document,
            frame="all",
        )
        assert page_capture["target"] == {
            "frame": "drawing_page",
            "object_count": 1,
            "object_names": [page.Name],
        }
        assert page_capture["image"]["mime_type"] == "image/png"
        attachment = page_capture["_vibecad_image_attachment"]
        captured_image = QtGui.QImage(str(attachment["path"]))
        assert not captured_image.isNull()
        assert captured_image.width() > captured_image.height() > 0

        Gui.Selection.clearSelection()
        Gui.runCommand("TechDraw_ToggleFrame")
        _events(20)
        human_visible = drawing_frame_visibility_state(page)
        visible_image = _page_image_sha256()
        assert human_visible["visible"] is True
        assert human_visible["graphical_view_count"] >= 1
        assert visible_image != hidden_image
        assert int(document.UndoCount) == undo_before
        assert int(document.getBookedTransactionID()) == transaction_before

        Gui.runCommand("TechDraw_ToggleFrame")
        _events(20)
        assert drawing_frame_visibility_state(page)["visible"] is False
        assert _page_image_sha256() == hidden_image

        context_action = Gui.getMainWindow().findChild(
            QtGui.QAction, "TechDrawContextToggleFrames"
        )
        assert context_action is not None
        context_action.trigger()
        _events(20)
        assert drawing_frame_visibility_state(page)["visible"] is True
        assert _page_image_sha256() == visible_image
        context_action.trigger()
        _events(20)
        assert drawing_frame_visibility_state(page)["visible"] is False

        grid_action = Gui.getMainWindow().findChild(
            QtGui.QAction, "TechDrawContextToggleGrid"
        )
        assert grid_action is not None
        grid_before = drawing_grid_visibility_state(page)["visible"]
        grid_action.trigger()
        _events(20)
        assert drawing_grid_visibility_state(page)["visible"] is not grid_before
        grid_action.trigger()
        _events(20)
        assert drawing_grid_visibility_state(page)["visible"] is grid_before

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(view)
        Gui.runCommand("TechDraw_ShowAll")
        _events(20)
        assert drawing_hidden_edge_visibility_state(view)["visible"] is True
        Gui.runCommand("TechDraw_ShowAll")
        _events(20)
        assert drawing_hidden_edge_visibility_state(view)["visible"] is False

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-drawing-presentation-gui")

        def reauthorize() -> None:
            require_frozen_native_surface(turn.surface, controller)

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
        call_index = 0

        def call(tool_name: str, arguments: dict, succeeds: bool = True) -> dict:
            nonlocal call_index
            call_index += 1
            response = dispatcher.call(
                tool_name,
                json.dumps(arguments, separators=(",", ":")),
                f"native-drawing-presentation-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(page)
        selection_before = _selection()
        revision_before = state_store.current_revision(str(document.Uid))

        page.ViewObject.hide()
        _events(20)
        assert drawing_page_presentation_state(page)["active"] is False
        exact_show = _show_arguments(page)
        presented = call("drawing.show_page", exact_show)
        assert presented["operation"] == "show"
        assert presented["previous_active"] is False
        assert presented["open"] is True
        assert presented["active"] is True
        assert presented["changed"] is True
        presented_again = call("drawing.show_page", _show_arguments(page))
        assert presented_again["previous_active"] is True
        assert presented_again["changed"] is False
        stale_show = _show_arguments(page)
        stale_show["page"]["expected_state_sha256"] = "0" * 64
        rejected = call("drawing.show_page", stale_show, False)
        assert rejected["error_code"] == "NATIVE_DRAWING_PRESENTATION_PAGE_STALE"
        assert state_store.current_revision(str(document.Uid)) == revision_before
        assert int(document.UndoCount) == undo_before

        stale_frame_arguments = _arguments_with_internal_state(page, True)
        shown = call("drawing.page_frames", _arguments(page, True))
        assert shown["operation"] == "set_frame_visibility"
        assert shown["previous_visible"] is False
        assert shown["visible"] is True
        assert shown["changed"] is True
        assert shown["graphical_view_count"] >= 1
        assert _page_image_sha256() == visible_image
        assert state_store.current_revision(str(document.Uid)) == revision_before
        assert int(document.UndoCount) == undo_before
        assert len(json.dumps(shown, separators=(",", ":")).encode()) < 2048

        unchanged = call("drawing.page_frames", _arguments(page, True))
        assert unchanged["previous_visible"] is True
        assert unchanged["visible"] is True
        assert unchanged["changed"] is False
        assert state_store.current_revision(str(document.Uid)) == revision_before

        stale = call("drawing.page_frames", stale_frame_arguments, False)
        assert stale["error_code"] == "NATIVE_DRAWING_PRESENTATION_STATE_STALE"

        wrong_type = _arguments(page, False)
        wrong_type["page"]["object_name"] = view.Name
        rejected = call("drawing.page_frames", wrong_type, False)
        assert rejected["error_code"] == "NATIVE_TARGET_INVALID", rejected
        assert rejected["accepted_types"] == ["TechDraw::DrawPage"]

        manual_arguments = _arguments(page, False)
        frame_preferences.SetInt("ViewFrameMode", 2)
        rejected = call("drawing.page_frames", manual_arguments, False)
        assert rejected["error_code"] == "NATIVE_DRAWING_PRESENTATION_UNAVAILABLE", rejected
        assert "Manual" in rejected["repair"]["requirement"]
        frame_preferences.SetInt("ViewFrameMode", MANUAL_FRAME_MODE)
        assert drawing_frame_visibility_state(page)["visible"] is True

        hidden = call("drawing.page_frames", _arguments(page, False))
        assert hidden["previous_visible"] is True
        assert hidden["visible"] is False
        assert hidden["changed"] is True
        assert _page_image_sha256() == hidden_image

        rollback_arguments = _arguments(page, True)
        rollback_state = drawing_frame_visibility_state(page)
        original_change = TechDrawGui.changeDrawingFrameVisibility

        def fail_after_change(target_page, visible):
            result = original_change(target_page, visible)
            if visible:
                raise RuntimeError("Injected frame presentation failure")
            return result

        TechDrawGui.changeDrawingFrameVisibility = fail_after_change
        try:
            rejected = call("drawing.page_frames", rollback_arguments, False)
        finally:
            TechDrawGui.changeDrawingFrameVisibility = original_change
        _events(16)
        assert rejected["error_code"] == "NATIVE_DRAWING_PRESENTATION_FAILED"
        assert drawing_frame_visibility_state(page) == rollback_state
        assert _page_image_sha256() == hidden_image

        shown = call("drawing.page_frames", _arguments(page, True))
        assert shown["visible"] is True
        grid_target = not drawing_grid_visibility_state(page)["visible"]
        grid_result = call("drawing.page_grid", _grid_arguments(page, grid_target))
        assert grid_result["operation"] == "set_grid_visibility"
        assert grid_result["visible"] is grid_target
        assert grid_result["changed"] is True

        mdi = Gui.getMainWindow().findChild(QtWidgets.QMdiArea)
        assert mdi is not None
        page_window = mdi.activeSubWindow()
        other_window = next(
            (item for item in mdi.subWindowList() if item is not page_window),
            None,
        )
        assert page_window is not None and other_window is not None
        mdi.setActiveSubWindow(other_window)
        _events(20)
        assert mdi.activeSubWindow() is other_window

        hidden_edges = call(
            "drawing.hidden_edges", _hidden_edge_arguments(view, True)
        )
        assert hidden_edges["operation"] == "set_hidden_edges_visible"
        assert hidden_edges["previous_visible"] is False
        assert hidden_edges["visible"] is True
        assert hidden_edges["changed"] is True
        assert mdi.activeSubWindow() is other_window

        snapshot = build_drawing_snapshot(
            document,
            selection=drawing_selection_state(document),
        )
        assert snapshot["active_page"]["frame_visibility"] == (
            drawing_frame_visibility_state(page)
        )
        assert snapshot["active_page"]["grid_visibility"] == (
            drawing_grid_visibility_state(page)
        )
        assert snapshot["active_page"]["presentation"] == (
            drawing_page_presentation_state(page)
        )
        page_snapshot = next(
            item for item in snapshot["pages"] if item["object_name"] == page.Name
        )
        view_snapshot = next(
            item for item in page_snapshot["views"] if item["object_name"] == view.Name
        )
        assert view_snapshot["hidden_edge_visibility"] == (
            drawing_hidden_edge_visibility_state(view)
        )
        assert snapshot["active_page_resolution"] == "selection"

        assert tuple(document.Objects) == objects_before
        assert tuple(page.Views) == views_before
        assert tuple(document.VibeCADTimeline.Operations) == history_before
        assert drawing_page_state(page) == page_before
        assert _selection() == selection_before
        assert (
            tuple(bool(item.ViewObject.Visibility) for item in (source, page, view))
            == visibility_before
        )
        assert int(document.UndoCount) == undo_before
        assert int(document.getBookedTransactionID()) == transaction_before
        assert state_store.current_revision(str(document.Uid)) == revision_before
        assert not Gui.Control.activeDialog()

        saved_grid_visibility = drawing_grid_visibility_state(page)["visible"]
        saved_hidden_edge_visibility = drawing_hidden_edge_visibility_state(view)[
            "visible"
        ]
        names = {"page": page.Name, "view": view.Name}
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        page = document.getObject(names["page"])
        view = document.getObject(names["view"])
        assert page is not None and view is not None
        page.ViewObject.show()
        assert document.recompute([view, page], True, True) is not False
        _events(24)
        reopened = drawing_frame_visibility_state(page)
        assert reopened["visible"] is False
        assert reopened["graphical_view_count"] >= 1
        assert drawing_grid_visibility_state(page)["visible"] is saved_grid_visibility
        assert (
            drawing_hidden_edge_visibility_state(view)["visible"]
            is saved_hidden_edge_visibility
        )

        print(
            "VIBECAD_NATIVE_DRAWING_PRESENTATION_GUI_OK operations=4 "
            "show=true "
            "frame_visibility=true grid_visibility=true hidden_edges=true "
            "explicit_state=true transient=true "
            "human_oracle=true context_oracle=true shared_host_builder=true "
            "visual_hash=true manual_mode=true exact_active_page=true "
            "provider_targets=true internal_state_guards=true closed_schema=true stale=true "
            "wrong_type=true preference_refusal=true rollback=true no_undo=true "
            "no_transaction=true no_revision=true selection=true visibility=true "
            "history=true page_boundary=true snapshot=true reopen=true "
            "page_ui_active_boundary=true exact_view_inactive_page=true "
            "page_capture=true idempotent=true low_noise=true native_no_task=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        Gui.Selection.clearSelection()
        frame_preferences.SetInt("ViewFrameMode", prior_frame_mode)
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
