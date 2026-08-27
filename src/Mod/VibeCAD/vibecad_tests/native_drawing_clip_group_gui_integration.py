# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact Native Drawing clip groups."""

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

import VibeCADGui as VibeGui
import VibeCADNativeDrawingClipRuntime as ClipRuntimeModule
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeDrawingClipSchema import DRAWING_CLIP_CAPABILITY_NAME
from VibeCADNativeDrawingClipState import (
    drawing_clip_group_state,
    drawing_clip_member_state,
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
    document.openTransaction("Create exact clip source")
    transaction = int(document.getBookedTransactionID())
    try:
        source = document.addObject("Part::Feature", "ClipSource")
        source.Label = "Clip Source"
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


def _turn(surface, registry) -> NativeTurnSnapshot:
    page_definition = registry.definition(DRAWING_PAGE_CAPABILITY_NAMES[0])
    clip_definition = registry.definition(DRAWING_CLIP_CAPABILITY_NAME)
    assert page_definition is not None and clip_definition is not None
    page_schema = page_definition.provider_schema(("page_default",))
    operations = (
        "create_clip_group",
        "add_views",
        "remove_views",
        "configure_clip_group",
    )
    clip_schema = clip_definition.provider_schema(operations)
    encoded = json.dumps(clip_schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "path" not in encoded.casefold()
    assert len(encoded.encode()) < 4 * 1024
    for field in (
        "expected_state_sha256",
        "position_on_page_mm",
        "position_in_clip_mm",
        "frame",
        "clip_children",
    ):
        assert field in encoded
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(DRAWING_PAGE_CAPABILITY_NAMES[0], DRAWING_CLIP_CAPABILITY_NAME),
            schemas=(page_schema, clip_schema),
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


def _member_in(state: dict, x_mm: float, y_mm: float) -> dict:
    return {
        "view": _target(state),
        "position_in_clip_mm": {"x_mm": x_mm, "y_mm": y_mm},
    }


def _create_arguments(page_state: dict, member_state: dict) -> dict:
    return {
        "operation": "create_clip_group",
        "page": _target(page_state),
        "label": "Native Clip Group",
        "position_on_page_mm": {"x_mm": 135.0, "y_mm": 82.0},
        "frame": {
            "width_mm": 80.0,
            "height_mm": 50.0,
            "show_frame": False,
            "clip_children": True,
        },
        "members": [_member_in(member_state, -12.0, 4.0)],
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-drawing-clip-group-"
        )
        save_path = Path(temporary.name) / "native-drawing-clip-group.FCStd"
        controller, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["TechDraw_ClipGroup"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.transaction_behavior,
            plan.background_required,
        ) == (
            DRAWING_CLIP_CAPABILITY_NAME,
            "create_clip_group",
            "ExactDrawingPageClipFrameMembersAndPlacements",
            "document",
            False,
        )

        document = App.newDocument("NativeDrawingClipGroupGate")
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
        ledger.begin_run("native-drawing-clip-group-gui")

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
                f"native-drawing-clip-group-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        page_result = call(DRAWING_PAGE_CAPABILITY_NAMES[0], {"operation": "page_default"})
        _events(12)
        page = document.getObject(page_result["page"]["object_name"])
        assert page is not None
        page.ViewObject.show()
        _events(12)
        first = _create_view(document, page, source, "ClipViewOne", 70.0, 75.0)

        # The shipped human command remains the native-type and undo oracle.
        objects_before_human = tuple(document.Objects)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(page)
        Gui.runCommand("TechDraw_ClipGroup")
        _events(12)
        human_objects = tuple(
            obj for obj in document.Objects if obj not in objects_before_human
        )
        assert len(human_objects) == 1
        human_clip = human_objects[0]
        human_name = str(human_clip.Name)
        assert human_clip.isDerivedFrom("TechDraw::DrawViewClip")
        assert tuple(human_clip.Views) == ()
        assert human_clip in tuple(page.Views)
        assert human_clip in tuple(document.VibeCADTimeline.Operations)
        document.undo()
        _events(12)
        page = document.getObject(page.Name)
        first = document.getObject(first.Name)
        source = document.getObject(source.Name)
        assert page is not None and first is not None and source is not None
        assert document.getObject(human_name) is None
        assert tuple(page.Views) == (first,)
        dispatcher = refresh_dispatcher()

        page_state = drawing_page_state(page)
        first_state = drawing_clip_member_state(first)
        duplicate = _create_arguments(page_state, first_state)
        duplicate["members"].append(_member_in(first_state, 2.0, 3.0))
        rejected = call(DRAWING_CLIP_CAPABILITY_NAME, duplicate, succeeds=False)
        assert rejected["error_code"] == "NATIVE_DRAWING_CLIP_MEMBERS_INVALID"

        stale_member = _create_arguments(page_state, first_state)
        stale_member["members"][0]["view"]["expected_state_sha256"] = "0" * 64
        rejected = call(DRAWING_CLIP_CAPABILITY_NAME, stale_member, succeeds=False)
        assert rejected["error_code"] == "NATIVE_DRAWING_CLIP_MEMBER_STALE"

        source.ViewObject.Visibility = True
        first.ViewObject.Visibility = True
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(first)
        selection_before = _selection()
        visibility_before = (
            bool(source.ViewObject.Visibility),
            bool(first.ViewObject.Visibility),
        )
        undo_before = int(document.UndoCount)
        create_arguments = _create_arguments(page_state, first_state)
        original_create_verify = ClipRuntimeModule.verify_clip_mutation

        def fail_create_verify(_document, _draft):
            raise RuntimeError("injected clip-group create failure")

        ClipRuntimeModule.verify_clip_mutation = fail_create_verify
        try:
            rejected = call(
                DRAWING_CLIP_CAPABILITY_NAME,
                create_arguments,
                succeeds=False,
            )
        finally:
            ClipRuntimeModule.verify_clip_mutation = original_create_verify
        assert rejected["error_code"] == "NATIVE_POSTCONDITION_FAILED"
        assert tuple(page.Views) == (first,)
        assert _selection() == selection_before

        result = call(DRAWING_CLIP_CAPABILITY_NAME, create_arguments)
        assert len(json.dumps(result, separators=(",", ":")).encode()) < 32 * 1024
        assert "path" not in json.dumps(result).casefold()
        clip = document.getObject(result["clip_group"]["object_name"])
        assert clip is not None
        state = drawing_clip_group_state(clip)
        assert state == result["clip_group"]
        assert state["position_on_page_mm"] == [135.0, 82.0]
        assert state["frame"] == {
            "width_mm": 80.0,
            "height_mm": 50.0,
            "show_frame": False,
            "clip_children": True,
        }
        assert [member["object_name"] for member in state["members"]] == [first.Name]
        assert tuple(clip.ViewObject.claimChildren()) == (first,)
        assert drawing_clip_member_state(first)["position_mm"] == [-12.0, 4.0]
        assert _selection() == selection_before
        assert (
            bool(source.ViewObject.Visibility),
            bool(first.ViewObject.Visibility),
        ) == visibility_before
        assert int(document.UndoCount) == undo_before + 1
        assert not Gui.Control.activeDialog()

        stale_page = _create_arguments(page_state, first_state)
        rejected = call(DRAWING_CLIP_CAPABILITY_NAME, stale_page, succeeds=False)
        assert rejected["error_code"] == "NATIVE_DRAWING_PAGE_STALE"

        nested = {
            "operation": "add_views",
            "page": _target(drawing_page_state(page)),
            "clip_group": _target(state),
            "members": [
                {
                    "view": _target(state),
                    "position_in_clip_mm": {"x_mm": 0.0, "y_mm": 0.0},
                }
            ],
        }
        rejected = call(DRAWING_CLIP_CAPABILITY_NAME, nested, succeeds=False)
        assert rejected["error_code"] == "NATIVE_DRAWING_CLIP_MEMBERS_INVALID"

        already_grouped = {
            "operation": "add_views",
            "page": _target(drawing_page_state(page)),
            "clip_group": _target(state),
            "members": [_member_in(drawing_clip_member_state(first), 0.0, 0.0)],
        }
        rejected = call(DRAWING_CLIP_CAPABILITY_NAME, already_grouped, succeeds=False)
        assert rejected["error_code"] == "NATIVE_DRAWING_CLIP_ALREADY_GROUPED"

        # Create a later History operation, then prove grouping it does not
        # reorder semantic History: Views is structural ownership, not a design input.
        second = _create_view(document, page, source, "ClipViewTwo", 185.0, 75.0)
        operations_before_add = tuple(document.VibeCADTimeline.Operations)
        assert operations_before_add.index(clip) < operations_before_add.index(second)

        # Exercise the shipped add command and its one-step human undo.
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(clip)
        Gui.Selection.addSelection(second)
        Gui.runCommand("TechDraw_ClipGroupAdd")
        _events(12)
        assert tuple(clip.Views) == (first, second)
        assert tuple(document.VibeCADTimeline.Operations) == operations_before_add
        document.undo()
        _events(12)
        page = document.getObject(page.Name)
        clip = document.getObject(clip.Name)
        first = document.getObject(first.Name)
        second = document.getObject(second.Name)
        assert tuple(clip.Views) == (first,)
        dispatcher = refresh_dispatcher()

        add_arguments = {
            "operation": "add_views",
            "page": _target(drawing_page_state(page)),
            "clip_group": _target(drawing_clip_group_state(clip)),
            "members": [_member_in(drawing_clip_member_state(second), 18.0, -6.0)],
        }
        add_result = call(DRAWING_CLIP_CAPABILITY_NAME, add_arguments)
        assert [
            member["object_name"] for member in add_result["clip_group"]["members"]
        ] == [first.Name, second.Name]
        assert tuple(clip.ViewObject.claimChildren()) == (first, second)
        assert drawing_clip_member_state(second)["position_mm"] == [18.0, -6.0]
        assert tuple(document.VibeCADTimeline.Operations) == operations_before_add

        # The shipped remove command remains compatible and undoable.
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(second)
        Gui.runCommand("TechDraw_ClipGroupRemove")
        _events(12)
        assert tuple(clip.Views) == (first,)
        document.undo()
        _events(12)
        page = document.getObject(page.Name)
        clip = document.getObject(clip.Name)
        first = document.getObject(first.Name)
        second = document.getObject(second.Name)
        assert tuple(clip.Views) == (first, second)
        dispatcher = refresh_dispatcher()

        configure = {
            "operation": "configure_clip_group",
            "page": _target(drawing_page_state(page)),
            "clip_group": _target(drawing_clip_group_state(clip)),
            "label": "Configured Clip",
            "position_on_page_mm": {"x_mm": 145.0, "y_mm": 90.0},
            "frame": {
                "width_mm": 92.0,
                "height_mm": 58.0,
                "show_frame": True,
                "clip_children": False,
            },
        }
        state_before_rollback = drawing_clip_group_state(clip)
        original_verify = ClipRuntimeModule.verify_clip_mutation

        def fail_verify(_document, _draft):
            raise RuntimeError("injected clip-group publication failure")

        ClipRuntimeModule.verify_clip_mutation = fail_verify
        try:
            rejected = call(DRAWING_CLIP_CAPABILITY_NAME, configure, succeeds=False)
        finally:
            ClipRuntimeModule.verify_clip_mutation = original_verify
        assert rejected["error_code"] == "NATIVE_POSTCONDITION_FAILED"
        page = document.getObject(page.Name)
        clip = document.getObject(clip.Name)
        first = document.getObject(first.Name)
        second = document.getObject(second.Name)
        assert drawing_clip_group_state(clip) == state_before_rollback

        configure["page"] = _target(drawing_page_state(page))
        configure["clip_group"] = _target(drawing_clip_group_state(clip))
        configured = call(DRAWING_CLIP_CAPABILITY_NAME, configure)
        assert configured["clip_group"]["label"] == "Configured Clip"
        assert configured["clip_group"]["frame"]["show_frame"] is True
        assert configured["clip_group"]["frame"]["clip_children"] is False

        remove = {
            "operation": "remove_views",
            "page": _target(drawing_page_state(page)),
            "clip_group": _target(drawing_clip_group_state(clip)),
            "members": [
                {
                    "view": _target(drawing_clip_member_state(first)),
                    "position_on_page_mm": {"x_mm": 42.0, "y_mm": 38.0},
                }
            ],
        }
        removed = call(DRAWING_CLIP_CAPABILITY_NAME, remove)
        final_state = removed["clip_group"]
        assert [member["object_name"] for member in final_state["members"]] == [second.Name]
        assert tuple(clip.ViewObject.claimChildren()) == (second,)
        assert drawing_clip_member_state(first)["clip_group_names"] == []
        assert drawing_clip_member_state(first)["position_mm"] == [42.0, 38.0]

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
                            "object_name": clip.Name,
                            "type_id": clip.TypeId,
                        },
                        "subelements": [],
                    }
                ],
            },
        )
        domain = active["domain"]
        assert domain["selected_clip_groups"][0] == final_state
        summaries = {view["object_name"]: view for view in domain["pages"][0]["views"]}
        assert summaries[clip.Name]["clip_group"]["state_sha256"] == final_state["state_sha256"]
        assert summaries[first.Name]["clip_member"]["state_sha256"] == drawing_clip_member_state(first)["state_sha256"]
        assert len(json.dumps(active, separators=(",", ":")).encode()) < 64 * 1024

        document.undo()
        _events(12)
        clip = document.getObject(clip.Name)
        first = document.getObject(first.Name)
        second = document.getObject(second.Name)
        assert tuple(clip.Views) == (first, second)
        assert drawing_clip_member_state(first)["position_mm"] == [-12.0, 4.0]
        document.redo()
        _events(12)
        clip = document.getObject(clip.Name)
        first = document.getObject(first.Name)
        second = document.getObject(second.Name)
        assert drawing_clip_group_state(clip) == final_state
        assert drawing_clip_member_state(first)["position_mm"] == [42.0, 38.0]

        names = {
            "page": str(page.Name),
            "clip": str(clip.Name),
            "first": str(first.Name),
            "second": str(second.Name),
        }
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        _events(16)
        page = document.getObject(names["page"])
        clip = document.getObject(names["clip"])
        first = document.getObject(names["first"])
        second = document.getObject(names["second"])
        assert all(obj is not None for obj in (page, clip, first, second))
        assert drawing_clip_group_state(clip) == final_state
        assert tuple(clip.Views) == (second,)
        assert tuple(clip.ViewObject.claimChildren()) == (second,)
        assert drawing_clip_member_state(first)["position_mm"] == [42.0, 38.0]
        assert str(clip.VibeCADTimelineRole) == "operation"

        print(
            "VIBECAD_NATIVE_DRAWING_CLIP_GROUP_GUI_OK "
            "human_create=true human_add=true human_remove=true exact_page=true "
            "closed_schema=true exact_group=true exact_members=true local_entry=true "
            "page_exit=true complete_frame=true clip_children=true nested_guard=true "
            "projection_group_guard=true duplicate_guard=true exclusive_membership=true "
            "stale_page=true stale_member=true structural_history=true tree_children=true "
            "rollback=true selection=true visibility=true history=true undo=true redo=true "
            "reopen=true path_private=true low_noise=true no_task=true",
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
