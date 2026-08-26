# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact projected Drawing balloons."""

from __future__ import annotations

import json
import math
from pathlib import Path
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
import VibeCADNativeDrawingBalloonRuntime as BalloonRuntimeModule
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeDrawingBalloonSchema import (
    DRAWING_BALLOON_CAPABILITY_NAME,
    DRAWING_BALLOON_OPERATIONS,
)
from VibeCADNativeDrawingBalloonState import (
    drawing_balloon_state,
    is_drawing_balloon,
)
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewState import drawing_view_state
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


def _create_fixture(document):
    document.openTransaction("Create Drawing balloon fixture")
    transaction = int(document.getBookedTransactionID())
    try:
        source = document.addObject("Part::Feature", "BalloonSource")
        source.Label = "Balloon Source"
        source.Shape = Part.makeBox(
            48.0,
            28.0,
            9.0,
            App.Vector(-24.0, -14.0, 0.0),
        )
        document.publishProvisionalTimelineOperationBlock(source, (), ())

        page = document.addObject("TechDraw::DrawPage", "BalloonPage")
        page.Label = "Balloon Page"
        template = document.addObject("TechDraw::DrawSVGTemplate", "BalloonTemplate")
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

        view = document.addObject("TechDraw::DrawViewPart", "BalloonView")
        view.Label = "Balloon View"
        view.Source = [source]
        view.Direction = App.Vector(0.0, 0.0, 1.0)
        view.XDirection = App.Vector(1.0, 0.0, 0.0)
        view.ScaleType = "Custom"
        view.Scale = 1.5
        view.Rotation = 17.0
        view.X = 105.0
        view.Y = 80.0
        view.CoarseView = False
        document.publishProvisionalTimelineOperationBlock(view, (), ())
        assert int(page.addView(view)) >= 1
        assert document.recompute([source, view, page], True, True) is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    page.ViewObject.show()
    _events(24)
    return source, page, view


def _anchor(view, kind: str = "edge") -> dict:
    projection = drawing_projected_geometry_state(view)
    return next(
        item
        for item in projection["elements"]
        if item["element_type"] == kind and item["visible"]
    )


def _target(element: dict) -> dict[str, str]:
    return {"subelement": element["name"]}


def _arguments(page, view, anchor: dict, *, suffix: str = "") -> dict:
    page_state = drawing_page_state(page)
    view_state = drawing_view_state(view)
    projection = drawing_projected_geometry_state(view)
    return {
        "operation": "create",
        "label": f"Pump Balloon{suffix}",
        "text": f"PUMP № 12 – Δ{suffix}",
        "page": {
            "object_name": page_state["object_name"],
            "expected_state_sha256": page_state["state_sha256"],
        },
        "view": {
            "object_name": view_state["object_name"],
            "expected_state_sha256": view_state["state_sha256"],
            "expected_projection_state_sha256": projection[
                "projection_state_sha256"
            ],
        },
        "anchor": _target(anchor),
        "bubble_offset_in_view_mm": {"x_mm": 28.5, "y_mm": -14.25},
    }


def _balloon_target(balloon, *, state_hash: str | None = None) -> dict[str, str]:
    state = drawing_balloon_state(balloon)
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"] if state_hash is None else state_hash,
    }


def _set_text_arguments(balloon, text: str, *, state_hash: str | None = None) -> dict:
    return {
        "operation": "set_text",
        "balloon": _balloon_target(balloon, state_hash=state_hash),
        "text": text,
    }


def _set_style_arguments(balloon, style: dict) -> dict:
    return {
        "operation": "set_style",
        "balloon": _balloon_target(balloon),
        "style": dict(style),
    }


def _move_arguments(balloon, x_mm: float, y_mm: float) -> dict:
    return {
        "operation": "move_bubble",
        "balloon": _balloon_target(balloon),
        "bubble_offset_in_view_mm": {"x_mm": x_mm, "y_mm": y_mm},
    }


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(DRAWING_BALLOON_CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(DRAWING_BALLOON_OPERATIONS)
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "path" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 8 * 1024
    branches = {
        operation: definition.provider_schema((operation,))["parameters"]["oneOf"][0]
        for operation in DRAWING_BALLOON_OPERATIONS
    }
    assert set(branches) == set(DRAWING_BALLOON_OPERATIONS)
    assert branches["create"]["required"] == [
        "label",
        "text",
        "page",
        "view",
        "anchor",
        "bubble_offset_in_view_mm",
    ]
    assert branches["set_text"]["required"] == ["balloon", "text"]
    assert branches["set_style"]["required"] == ["balloon", "style"]
    assert branches["move_bubble"]["required"] == [
        "balloon",
        "bubble_offset_in_view_mm",
    ]
    assert all(branch["additionalProperties"] is False for branch in branches.values())
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(DRAWING_BALLOON_CAPABILITY_NAME,),
            schemas=(schema,),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _balloons(document) -> tuple:
    return tuple(obj for obj in document.Objects if is_drawing_balloon(obj))


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-drawing-balloon-"
        )
        save_path = Path(temporary.name) / "drawing-balloon.FCStd"
        controller, surface = _surface()
        plan = next(
            item
            for item in resolve_native_action_inventory(surface).plans
            if item.command_id == "TechDraw_Balloon"
        )
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.transaction_behavior,
            plan.background_required,
        ) == (
            DRAWING_BALLOON_CAPABILITY_NAME,
            "create",
            "ExactDrawingProjectedBalloonAnchorAndPlacement",
            "document",
            False,
        )

        document = App.newDocument("NativeDrawingBalloonGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        source, page, view = _create_fixture(document)
        edge = _anchor(view, "edge")
        vertex = _anchor(view, "vertex")

        human_objects = tuple(document.Objects)
        human_views = tuple(page.Views)
        human_history = tuple(document.VibeCADTimeline.Operations)
        human_projection = drawing_projected_geometry_state(view)[
            "projection_state_sha256"
        ]
        human_index = int(page.NextBalloonIndex)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(view, edge["name"])
        Gui.runCommand("TechDraw_Balloon")
        _events(20)
        created = tuple(obj for obj in document.Objects if obj not in human_objects)
        assert len(created) == 1 and is_drawing_balloon(created[0])
        human_state = drawing_balloon_state(created[0])
        assert human_state["anchor"]["subelement"] == edge["name"]
        assert human_state["anchor"]["element_state_sha256"] == (
            edge["element_state_sha256"]
        )
        assert human_state["text"] == str(human_index)
        assert int(page.NextBalloonIndex) == human_index + 1
        assert tuple(page.Views) == (*human_views, created[0])
        assert created[0] in tuple(view.InList)
        assert not Gui.Control.activeDialog()

        human_balloon = created[0]
        human_auto_text = human_state["text"]
        assert human_balloon.ViewObject.doubleClicked()
        _events(12)
        assert Gui.Control.activeDialog()
        line_edit = Gui.getMainWindow().findChild(QtWidgets.QLineEdit, "leText")
        assert line_edit is not None
        line_edit.setText("HUMAN EDIT – № 7")
        _events(12)
        assert str(human_balloon.Text) == "HUMAN EDIT – № 7"
        active_task = Gui.Control.activeTaskDialog()
        assert active_task is not None
        active_task.accept()
        _events(16)
        assert not Gui.Control.activeDialog()
        assert drawing_balloon_state(human_balloon)["text"] == "HUMAN EDIT – № 7"
        document.undo()
        _events(12)
        assert drawing_balloon_state(human_balloon)["text"] == human_auto_text
        document.redo()
        _events(12)
        assert drawing_balloon_state(human_balloon)["text"] == "HUMAN EDIT – № 7"
        document.undo()
        _events(12)
        assert drawing_balloon_state(human_balloon)["text"] == human_auto_text
        human_name = str(created[0].Name)
        document.undo()
        _events(16)
        assert document.getObject(human_name) is None
        assert tuple(document.Objects) == human_objects
        assert tuple(page.Views) == human_views
        assert tuple(document.VibeCADTimeline.Operations) == human_history
        assert int(page.NextBalloonIndex) == human_index
        assert document.recompute([view, page], True, True) is not False
        assert drawing_projected_geometry_state(view)[
            "projection_state_sha256"
        ] == human_projection
        Gui.Selection.clearSelection()
        edge = _anchor(view, "edge")
        vertex = _anchor(view, "vertex")

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-drawing-balloon-gui")

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

        def call(arguments: dict, *, succeeds: bool = True) -> dict:
            nonlocal call_index
            call_index += 1
            response = dispatcher.call(
                DRAWING_BALLOON_CAPABILITY_NAME,
                json.dumps(arguments, separators=(",", ":")),
                f"native-drawing-balloon-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        source.ViewObject.Visibility = True
        view.ViewObject.Visibility = True
        Gui.Selection.addSelection(source)
        Gui.Selection.addSelection(view, edge["name"])
        selection_before = _selection()
        visibility_before = (
            bool(source.ViewObject.Visibility),
            bool(view.ViewObject.Visibility),
            bool(page.ViewObject.Visibility),
        )
        projection_before = drawing_projected_geometry_state(view)[
            "projection_state_sha256"
        ]
        view_before = drawing_view_state(view)["state_sha256"]
        history_before = tuple(document.VibeCADTimeline.Operations)
        index_before = int(page.NextBalloonIndex)
        revision_before = state_store.current_revision(str(document.Uid))

        result = call(_arguments(page, view, edge))
        state = result["balloon"]
        assert result["operation"] == "create"
        assert state["text"] == "PUMP № 12 – Δ"
        assert state["anchor"]["subelement"] == edge["name"]
        assert state["anchor"]["element_state_sha256"] == edge[
            "element_state_sha256"
        ]
        assert state["bubble_offset_in_view_mm"] == {
            "x_mm": 28.5,
            "y_mm": -14.25,
        }
        assert state["valid"] and state["timeline_usable"]
        assert state["timeline_role"] == "operation"
        assert result["assistant_undo_available"] is True
        assert int(page.NextBalloonIndex) == index_before
        assert len(json.dumps(result, separators=(",", ":")).encode()) < 12 * 1024
        assert "elements" not in result
        assert not Gui.Control.activeDialog()
        first_name = state["object_name"]
        first_balloon = document.getObject(first_name)
        assert first_balloon is not None
        assert tuple(document.VibeCADTimeline.Operations) == (
            *history_before,
            first_balloon,
        )
        assert first_balloon in tuple(view.InList)
        assert _selection() == selection_before
        assert (
            bool(source.ViewObject.Visibility),
            bool(view.ViewObject.Visibility),
            bool(page.ViewObject.Visibility),
        ) == visibility_before
        assert drawing_view_state(view)["state_sha256"] == view_before
        assert drawing_projected_geometry_state(view)[
            "projection_state_sha256"
        ] == projection_before
        assert state_store.current_revision(str(document.Uid)) == revision_before + 1

        edit_stale_revision = state_store.current_revision(str(document.Uid))
        edit_stale_hash = drawing_balloon_state(first_balloon)["state_sha256"]
        rejected = call(
            _set_text_arguments(
                first_balloon,
                "STALE EDIT",
                state_hash="0" * 64,
            ),
            succeeds=False,
        )
        assert rejected["error_code"] == "NATIVE_DRAWING_BALLOON_STALE"
        assert drawing_balloon_state(first_balloon)["state_sha256"] == edit_stale_hash
        assert state_store.current_revision(str(document.Uid)) == edit_stale_revision

        text_before = drawing_balloon_state(first_balloon)
        text_result = call(_set_text_arguments(first_balloon, "PUMP – EDITED № 12"))
        text_after = text_result["balloon"]
        assert text_result["operation"] == "set_text"
        assert text_after["text"] == "PUMP – EDITED № 12"
        assert text_after["anchor"] == text_before["anchor"]
        assert text_after["style"] == text_before["style"]
        assert text_after["bubble_offset_in_view_mm"] == text_before[
            "bubble_offset_in_view_mm"
        ]
        assert not Gui.Control.activeDialog()
        document.undo()
        _events(12)
        assert drawing_balloon_state(first_balloon)["state_sha256"] == text_before[
            "state_sha256"
        ]
        document.redo()
        _events(12)
        assert drawing_balloon_state(first_balloon)["state_sha256"] == text_after[
            "state_sha256"
        ]

        turn = _turn(surface, registry)
        frozen = turn.surface
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state_store,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )
        no_change_revision = state_store.current_revision(str(document.Uid))
        no_change_undo = int(document.UndoCount)
        rejected = call(
            _set_text_arguments(first_balloon, "PUMP – EDITED № 12"),
            succeeds=False,
        )
        assert rejected["error_code"] == "NATIVE_DRAWING_NO_CHANGE"
        assert state_store.current_revision(str(document.Uid)) == no_change_revision
        assert int(document.UndoCount) == no_change_undo

        requested_style = {
            "bubble_shape": "Hexagon",
            "leader_end": "Open arrow",
            "bubble_scale": 1.35,
            "leader_end_scale": 0.85,
            "kink_length_mm": -3.75,
            "font_size_mm": 4.2,
            "line_width_mm": 0.65,
            "line_visible": False,
            "color_rgb": {"red": 23, "green": 145, "blue": 211},
        }
        style_before = drawing_balloon_state(first_balloon)
        style_result = call(_set_style_arguments(first_balloon, requested_style))
        style_after = style_result["balloon"]
        assert style_result["operation"] == "set_style"
        assert style_after["style"] == {
            **style_before["style"],
            **requested_style,
        }
        assert style_after["text"] == text_after["text"]
        assert style_after["anchor"] == style_before["anchor"]
        document.undo()
        _events(12)
        assert drawing_balloon_state(first_balloon)["state_sha256"] == style_before[
            "state_sha256"
        ]
        document.redo()
        _events(12)
        assert drawing_balloon_state(first_balloon)["state_sha256"] == style_after[
            "state_sha256"
        ]

        turn = _turn(surface, registry)
        frozen = turn.surface
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state_store,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )
        move_before = drawing_balloon_state(first_balloon)
        move_result = call(_move_arguments(first_balloon, -19.25, 31.5))
        move_after = move_result["balloon"]
        assert move_result["operation"] == "move_bubble"
        assert math.isclose(
            move_after["bubble_offset_in_view_mm"]["x_mm"],
            -19.25,
            rel_tol=1.0e-10,
            abs_tol=1.0e-9,
        )
        assert math.isclose(
            move_after["bubble_offset_in_view_mm"]["y_mm"],
            31.5,
            rel_tol=1.0e-10,
            abs_tol=1.0e-9,
        )
        assert move_after["anchor"] == move_before["anchor"]
        assert move_after["text"] == move_before["text"]
        assert move_after["style"] == move_before["style"]
        document.undo()
        _events(12)
        assert drawing_balloon_state(first_balloon)["state_sha256"] == move_before[
            "state_sha256"
        ]
        document.redo()
        _events(12)
        assert drawing_balloon_state(first_balloon)["state_sha256"] == move_after[
            "state_sha256"
        ]

        assert tuple(document.VibeCADTimeline.Operations) == (
            *history_before,
            first_balloon,
        )
        assert int(page.NextBalloonIndex) == index_before
        assert _selection() == selection_before
        assert (
            bool(source.ViewObject.Visibility),
            bool(view.ViewObject.Visibility),
            bool(page.ViewObject.Visibility),
        ) == visibility_before
        assert drawing_view_state(view)["state_sha256"] == view_before
        assert drawing_projected_geometry_state(view)[
            "projection_state_sha256"
        ] == projection_before

        turn = _turn(surface, registry)
        frozen = turn.surface
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state_store,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )
        edit_rollback_state = drawing_balloon_state(first_balloon)
        edit_rollback_objects = tuple(document.Objects)
        edit_rollback_views = tuple(page.Views)
        edit_rollback_history = tuple(document.VibeCADTimeline.Operations)
        edit_rollback_revision = state_store.current_revision(str(document.Uid))
        edit_rollback_undo = int(document.UndoCount)
        original_edit_verify = BalloonRuntimeModule.verify_drawing_balloon_edit

        def fail_edit_verify(_document, _draft):
            raise RuntimeError("Injected Drawing Balloon edit verification failure")

        BalloonRuntimeModule.verify_drawing_balloon_edit = fail_edit_verify
        try:
            rejected = call(
                _set_text_arguments(first_balloon, "MUST ROLL BACK"),
                succeeds=False,
            )
        finally:
            BalloonRuntimeModule.verify_drawing_balloon_edit = original_edit_verify
        _events(12)
        assert rejected["error_code"] == "NATIVE_POSTCONDITION_FAILED", rejected
        assert tuple(document.Objects) == edit_rollback_objects
        assert tuple(page.Views) == edit_rollback_views
        assert tuple(document.VibeCADTimeline.Operations) == edit_rollback_history
        assert drawing_balloon_state(first_balloon)["state_sha256"] == (
            edit_rollback_state["state_sha256"]
        )
        assert state_store.current_revision(str(document.Uid)) == edit_rollback_revision
        assert int(document.UndoCount) == edit_rollback_undo
        assert _selection() == selection_before

        rollback_objects = tuple(document.Objects)
        rollback_views = tuple(page.Views)
        rollback_history = tuple(document.VibeCADTimeline.Operations)
        rollback_revision = state_store.current_revision(str(document.Uid))
        rollback_undo = int(document.UndoCount)
        original_verify = BalloonRuntimeModule.verify_drawing_balloon

        def fail_verify(_document, _draft):
            raise RuntimeError("Injected Drawing balloon verification failure")

        BalloonRuntimeModule.verify_drawing_balloon = fail_verify
        try:
            rejected = call(
                _arguments(page, view, edge, suffix=" rollback"),
                succeeds=False,
            )
        finally:
            BalloonRuntimeModule.verify_drawing_balloon = original_verify
        _events(12)
        assert rejected["error_code"] == "NATIVE_POSTCONDITION_FAILED", rejected
        assert tuple(document.Objects) == rollback_objects
        assert tuple(page.Views) == rollback_views
        assert tuple(document.VibeCADTimeline.Operations) == rollback_history
        assert int(page.NextBalloonIndex) == index_before
        assert state_store.current_revision(str(document.Uid)) == rollback_revision
        assert int(document.UndoCount) == rollback_undo
        assert _selection() == selection_before

        undo_result = call(_arguments(page, view, vertex, suffix=" redo"))
        undo_name = undo_result["balloon"]["object_name"]
        undo_state = undo_result["balloon"]
        assert undo_state["anchor"]["subelement"] == vertex["name"]
        assert undo_state["anchor"]["element_type"] == "vertex"
        document.undo()
        _events(12)
        assert document.getObject(undo_name) is None
        document.redo()
        _events(18)
        redone = document.getObject(undo_name)
        assert redone is not None
        assert drawing_balloon_state(redone)["state_sha256"] == undo_state[
            "state_sha256"
        ]
        assert int(page.NextBalloonIndex) == index_before

        selected_snapshot = build_active_snapshot(
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
                            "object_name": undo_name,
                            "type_id": redone.TypeId,
                        }
                    }
                ],
            },
        )
        selected = selected_snapshot["domain"]["selected_balloons"]
        assert selected == [drawing_balloon_state(redone)]
        page_summary = next(
            item
            for item in selected_snapshot["domain"]["pages"]
            if item["object_name"] == page.Name
        )
        summary = next(
            item for item in page_summary["views"] if item["object_name"] == undo_name
        )
        assert summary["balloon"]["anchor"] == selected[0]["anchor"]
        assert summary["balloon"]["text"] == selected[0]["text"]
        edited_summary = next(
            item for item in page_summary["views"] if item["object_name"] == first_name
        )
        edited_state = drawing_balloon_state(first_balloon)
        assert edited_summary["balloon"] == {
            name: edited_state[name]
            for name in (
                "state_sha256",
                "source_view_name",
                "anchor",
                "bubble_offset_in_view_mm",
                "text",
                "style",
                "timeline_usable",
                "valid",
            )
        }
        assert len(json.dumps(selected_snapshot, separators=(",", ":")).encode()) < (
            96 * 1024
        )

        final_states = {
            balloon.Name: drawing_balloon_state(balloon)["state_sha256"]
            for balloon in _balloons(document)
        }
        names = {
            "source": str(source.Name),
            "page": str(page.Name),
            "view": str(view.Name),
        }
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        source = document.getObject(names["source"])
        page = document.getObject(names["page"])
        view = document.getObject(names["view"])
        assert all(obj is not None for obj in (source, page, view))
        page.ViewObject.show()
        assert document.recompute([view, page], True, True) is not False
        _events(24)
        for name, expected_hash in final_states.items():
            balloon = document.getObject(name)
            assert balloon is not None
            assert drawing_balloon_state(balloon)["state_sha256"] == expected_hash
            assert balloon in tuple(page.Views)
            assert balloon in tuple(view.InList)
            assert balloon in tuple(document.VibeCADTimeline.Operations)

        print(
            "VIBECAD_NATIVE_DRAWING_BALLOON_GUI_OK operations=4 "
            "create=true set_text=true set_style=true move_bubble=true "
            "human_oracle=true human_edit_oracle=true shared_host_builder=true exact_page=true "
            "exact_view=true projection_hash=true element_hash=true "
            "edge_midpoint=true vertex=true anchor_persisted=true unicode_text=true "
            "scaled_view_offset=true auto_index=true selection=true "
            "visibility=true tree_parent=true history=true rollback=true "
            "edit_rollback=true stale_edit=true no_op=true complete_style=true "
            "revision=true undo=true redo=true snapshot=true reopen=true "
            "low_noise=true native_no_task=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        Gui.Selection.clearSelection()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
