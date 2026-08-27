# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact Drawing view position locks."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets
import TechDrawGui

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeDrawingDimensionSupport import drawing_selection_state
from VibeCADNativeDrawingSnapshot import build_drawing_snapshot
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewLockSchema import (
    DRAWING_VIEW_LOCK_CAPABILITY_NAMES,
)
from VibeCADNativeDrawingViewLockState import (
    drawing_view_lock_inventory_state,
    drawing_view_lock_state,
)
import VibeCADNativeDrawingViewLockRuntime as ViewLockRuntimeModule
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
    assert surface.surface_id == "drawing"
    return controller, surface


def _create_page(document, name, label):
    page = document.addObject("TechDraw::DrawPage", name)
    page.Label = label
    template = document.addObject("TechDraw::DrawSVGTemplate", f"{name}Template")
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
    return page


def _create_view(document, page, source, name, x_mm, y_mm, locked):
    view = document.addObject("TechDraw::DrawViewPart", name)
    view.Label = name.replace("View", " View")
    view.Source = [source]
    view.Direction = App.Vector(0.0, 0.0, 1.0)
    view.XDirection = App.Vector(1.0, 0.0, 0.0)
    view.ScaleType = "Custom"
    view.Scale = 1.0
    view.X = x_mm
    view.Y = y_mm
    view.LockPosition = locked
    document.publishProvisionalTimelineOperationBlock(view, (), ())
    assert int(page.addView(view)) >= 1
    return view


def _create_fixture(document):
    document.openTransaction("Create Drawing view-lock fixture")
    transaction = int(document.getBookedTransactionID())
    try:
        source = document.addObject("Part::Feature", "ViewLockSource")
        source.Label = "View Lock Source"
        source.Shape = Part.makeBox(70.0, 40.0, 12.0)
        document.publishProvisionalTimelineOperationBlock(source, (), ())
        page = _create_page(document, "ViewLockPage", "View Lock Page")
        first = _create_view(
            document,
            page,
            source,
            "ViewLockFirstView",
            75.0,
            75.0,
            False,
        )
        second = _create_view(
            document,
            page,
            source,
            "ViewLockSecondView",
            175.0,
            75.0,
            True,
        )
        other_page = _create_page(
            document,
            "OtherViewLockPage",
            "Other View Lock Page",
        )
        other = _create_view(
            document,
            other_page,
            source,
            "OtherPageLockView",
            100.0,
            70.0,
            False,
        )
        assert document.recompute(
            [source, first, second, page, other, other_page],
            True,
            True,
        ) is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    page.ViewObject.show()
    _events(24)
    return source, page, first, second, other_page, other


def _turn(surface, registry) -> NativeTurnSnapshot:
    schemas = []
    for name in DRAWING_VIEW_LOCK_CAPABILITY_NAMES:
        definition = registry.definition(name)
        assert definition is not None
        schemas.append(
            definition.provider_schema(
                tuple(variant.operation for variant in definition.variants)
            )
        )
    encoded = json.dumps(schemas, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "path" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 8 * 1024
    branches = {
        schema["name"]: schema["parameters"]["oneOf"][0]
        for schema in schemas
    }
    assert set(branches) == set(DRAWING_VIEW_LOCK_CAPABILITY_NAMES)
    assert branches["drawing.set_view_locks"]["properties"]["views"]["maxItems"] == 32
    read = branches["drawing.view_locks"]
    assert read["properties"]["operation"]["const"] == "read"
    assert read["properties"]["offset"]["maximum"] == 512
    assert "page_size" not in read["properties"]
    assert all(branch["additionalProperties"] is False for branch in branches.values())
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=DRAWING_VIEW_LOCK_CAPABILITY_NAMES,
            schemas=tuple(schemas),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _selection():
    return tuple(
        (item.Object.Name, tuple(item.SubElementNames))
        for item in Gui.Selection.getSelectionEx()
    )


def _visibility(document):
    return tuple(
        (obj.Name, bool(obj.ViewObject.Visibility))
        for obj in document.Objects
        if getattr(obj, "ViewObject", None) is not None
    )


def _page_target(page):
    return {"object_name": page.Name}


def _rendered_bounds(page, object_names):
    layout = TechDrawGui.inspectPageLayout(page)
    by_name = {item["object_name"]: item for item in layout["items"]}
    return {name: by_name[name]["bounds_mm"] for name in object_names}


def _assert_rendered_bounds(actual, expected):
    assert set(actual) == set(expected)
    for object_name, expected_bounds in expected.items():
        actual_bounds = actual[object_name]
        for coordinate, expected_value in expected_bounds.items():
            assert abs(actual_bounds[coordinate] - expected_value) < 1.0e-6, (
                object_name,
                coordinate,
                expected_value,
                actual_bounds[coordinate],
            )


def _change(state, locked):
    return {
        "object_name": state["object_name"],
        "locked": locked,
    }


def _set_arguments(page, inventory, requested):
    by_name = {state["object_name"]: state for state in inventory["views"]}
    return {
        "operation": "set",
        "page": _page_target(page),
        "views": [
            _change(by_name[view.Name], locked) for view, locked in requested
        ],
    }


def _read_arguments(page, inventory=None, *, offset=0):
    target = {"object_name": page.Name}
    return {"operation": "read", "page": target, "offset": offset}


def _human_oracle(document, first, second):
    before = (drawing_view_lock_state(first), drawing_view_lock_state(second))
    positions = tuple(state["position_on_page_mm"] for state in before)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(first)
    Gui.Selection.addSelection(second)
    selection = _selection()
    undo_before = int(document.UndoCount)
    Gui.runCommand("TechDraw_ExtensionLockUnlockView")
    _events(12)
    after = (drawing_view_lock_state(first), drawing_view_lock_state(second))
    assert [state["locked"] for state in after] == [True, False]
    assert tuple(state["position_on_page_mm"] for state in after) == positions
    assert _selection() == selection
    assert int(document.UndoCount) == undo_before + 1
    assert not Gui.Control.activeDialog()
    document.undo()
    _events(10)
    assert (drawing_view_lock_state(first), drawing_view_lock_state(second)) == before
    document.redo()
    _events(10)
    assert (drawing_view_lock_state(first), drawing_view_lock_state(second)) == after
    document.undo()
    _events(10)
    assert (drawing_view_lock_state(first), drawing_view_lock_state(second)) == before
    return after


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-drawing-view-lock-"
        )
        save_path = Path(temporary.name) / "drawing-view-lock.FCStd"
        controller, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["TechDraw_ExtensionLockUnlockView"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.transaction_behavior,
            plan.background_required,
        ) == (
            "drawing.set_view_locks",
            "set",
            "ExactDrawingPageAndExplicitViewLockStates",
            "document",
            False,
        )

        document = App.newDocument("NativeDrawingViewLockGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        source, page, first, second, other_page, other = _create_fixture(document)
        initial = drawing_view_lock_inventory_state(page)
        assert initial["view_count"] == 2
        assert initial["locked_count"] == 1
        human_result = _human_oracle(document, first, second)
        assert drawing_view_lock_inventory_state(page) == initial

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-drawing-view-lock-gui")

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

        revision0 = state_store.current_revision(str(document.Uid))
        read_response = dispatcher.call(
            "drawing.view_locks",
            json.dumps(_read_arguments(page), separators=(",", ":")),
            "native-drawing-view-lock-read",
        )
        assert read_response["ok"] is True
        assert read_response["view_locks"]["views"] == initial["views"]
        assert read_response["view_locks"]["next_offset"] is None
        assert state_store.current_revision(str(document.Uid)) == revision0

        page.ViewObject.show()
        source.ViewObject.Visibility = True
        first.ViewObject.Visibility = True
        second.ViewObject.Visibility = True
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source)
        Gui.Selection.addSelection(first)
        selection_before = _selection()
        visibility_before = _visibility(document)
        objects_before = tuple(document.Objects)
        page_views_before = tuple(page.Views)
        history_before = tuple(document.VibeCADTimeline.Operations)

        set_args = _set_arguments(
            page,
            initial,
            ((first, True), (second, False)),
        )
        response = dispatcher.call(
            "drawing.set_view_locks",
            json.dumps(set_args, separators=(",", ":")),
            "native-drawing-view-lock-set",
        )
        assert response["ok"] is True, response
        assert response["operation"] == "set"
        assert response["view_locks"]["changed_view_count"] == 2
        assert response["assistant_undo_available"] is True
        assert len(json.dumps(response, separators=(",", ":")).encode()) < 6 * 1024
        assert dispatcher.call(
            "drawing.set_view_locks",
            json.dumps(set_args, separators=(",", ":")),
            "native-drawing-view-lock-set",
        ) == response
        final_inventory = drawing_view_lock_inventory_state(page)
        assert tuple(
            drawing_view_lock_state(view) for view in (first, second)
        ) == human_result
        assert state_store.current_revision(str(document.Uid)) == revision0 + 1
        assert _selection() == selection_before
        assert _visibility(document) == visibility_before
        assert tuple(document.Objects) == objects_before
        assert tuple(page.Views) == page_views_before
        assert tuple(document.VibeCADTimeline.Operations) == history_before

        no_op_undo = int(document.UndoCount)
        no_op_revision = state_store.current_revision(str(document.Uid))
        no_op = dispatcher.call(
            "drawing.set_view_locks",
            json.dumps(
                _set_arguments(page, final_inventory, ((first, True),)),
                separators=(",", ":"),
            ),
            "native-drawing-view-lock-no-op",
        )
        assert no_op["ok"] is False
        assert no_op["error_code"] == "NATIVE_DRAWING_VIEW_LOCK_NO_CHANGE"
        assert int(document.UndoCount) == no_op_undo
        assert state_store.current_revision(str(document.Uid)) == no_op_revision

        duplicate_args = _set_arguments(
            page,
            final_inventory,
            ((first, False), (first, False)),
        )
        duplicate = dispatcher.call(
            "drawing.set_view_locks",
            json.dumps(duplicate_args, separators=(",", ":")),
            "native-drawing-view-lock-duplicate",
        )
        assert duplicate["ok"] is False
        assert duplicate["error_code"] == "NATIVE_DRAWING_VIEW_LOCK_TARGETS_INVALID"

        cross_page = {
            "operation": "set",
            "page": _page_target(page),
            "views": [_change(drawing_view_lock_state(other), True)],
        }
        mismatch = dispatcher.call(
            "drawing.set_view_locks",
            json.dumps(cross_page, separators=(",", ":")),
            "native-drawing-view-lock-cross-page",
        )
        assert mismatch["ok"] is False
        assert mismatch["error_code"] == "NATIVE_DRAWING_VIEW_LOCK_PAGE_MISMATCH"

        stale_target_args = _set_arguments(
            page,
            final_inventory,
            ((first, False),),
        )
        stale_target_args["views"][0]["expected_view_lock_state_sha256"] = "0" * 64
        stale_target = dispatcher.call(
            "drawing.set_view_locks",
            json.dumps(stale_target_args, separators=(",", ":")),
            "native-drawing-view-lock-stale-target",
        )
        assert stale_target["ok"] is False
        assert stale_target["error_code"] == "NATIVE_DRAWING_VIEW_LOCK_TARGET_STALE"
        assert drawing_view_lock_inventory_state(page) == final_inventory

        stale_inventory_args = _set_arguments(
            page,
            final_inventory,
            ((first, False),),
        )
        stale_inventory_args["expected_inventory_state_sha256"] = "0" * 64
        stale_inventory = dispatcher.call(
            "drawing.set_view_locks",
            json.dumps(stale_inventory_args, separators=(",", ":")),
            "native-drawing-view-lock-stale-inventory",
        )
        assert stale_inventory["ok"] is False
        assert stale_inventory["error_code"] == (
            "NATIVE_DRAWING_VIEW_LOCK_INVENTORY_STALE"
        )
        assert drawing_view_lock_inventory_state(page) == final_inventory

        rollback_undo = int(document.UndoCount)
        rollback_revision = state_store.current_revision(str(document.Uid))
        original_verify = ViewLockRuntimeModule.verify_drawing_view_locks

        def fail_verify(_document, _draft):
            raise RuntimeError("Injected Drawing view-lock verification failure")

        ViewLockRuntimeModule.verify_drawing_view_locks = fail_verify
        try:
            rollback = dispatcher.call(
                "drawing.set_view_locks",
                json.dumps(
                    _set_arguments(
                        page,
                        final_inventory,
                        ((first, False), (second, True)),
                    ),
                    separators=(",", ":"),
                ),
                "native-drawing-view-lock-rollback",
            )
        finally:
            ViewLockRuntimeModule.verify_drawing_view_locks = original_verify
        _events(12)
        assert rollback["ok"] is False
        assert rollback["error_code"] == "NATIVE_POSTCONDITION_FAILED"
        assert drawing_view_lock_inventory_state(page) == final_inventory
        assert int(document.UndoCount) == rollback_undo
        assert state_store.current_revision(str(document.Uid)) == rollback_revision
        assert _selection() == selection_before

        snapshot = build_drawing_snapshot(
            document,
            selection=drawing_selection_state(document),
        )
        page_summary = next(
            item for item in snapshot["pages"] if item["object_name"] == page.Name
        )
        assert page_summary["view_locks"]["inventory_state_sha256"] == (
            final_inventory["inventory_state_sha256"]
        )
        first_summary = next(
            item for item in page_summary["views"] if item["object_name"] == first.Name
        )
        assert first_summary["view_lock"] == {
            name: drawing_view_lock_state(first)[name]
            for name in (
                "view_lock_state_sha256",
                "page_name",
                "position_on_page_mm",
                "locked",
                "timeline_usable",
                "valid",
            )
        }
        assert "views" not in page_summary["view_locks"]
        assert len(json.dumps(snapshot, separators=(",", ":")).encode()) < 96 * 1024

        document.undo()
        _events(12)
        assert drawing_view_lock_inventory_state(page) == initial
        document.redo()
        _events(12)
        assert drawing_view_lock_inventory_state(page) == final_inventory

        names = {
            "page": page.Name,
            "first": first.Name,
            "second": second.Name,
            "other_page": other_page.Name,
        }
        positions = {
            name: drawing_view_lock_state(document.getObject(name))[
                "position_on_page_mm"
            ]
            for name in (names["first"], names["second"])
        }
        rendered_bounds = _rendered_bounds(page, positions)
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        page = document.getObject(names["page"])
        first = document.getObject(names["first"])
        second = document.getObject(names["second"])
        assert all(obj is not None for obj in (page, first, second))
        page.ViewObject.show()
        assert document.recompute([first, second, page], True, True) is not False
        _events(24)
        reopened = drawing_view_lock_inventory_state(page)
        assert reopened["view_count"] == final_inventory["view_count"]
        assert [state["locked"] for state in reopened["views"]] == [True, False]
        assert {
            view.Name: drawing_view_lock_state(view)["position_on_page_mm"]
            for view in (first, second)
        } == positions
        _assert_rendered_bounds(
            _rendered_bounds(page, positions),
            rendered_bounds,
        )

        print(
            "VIBECAD_NATIVE_DRAWING_VIEW_LOCK_GUI_OK operations=2 "
            "read_page=true set=true human_oracle=true shared_host_builder=true "
            "explicit_final_state=true mixed_batch=true exact_page=true "
            "provider_targets=true internal_state_guards=true paginated=true "
            "limits_published=true selection=true visibility=true history=true "
            "stale_inventory=true stale_target=true no_op=true duplicate=true "
            "cross_page=true rollback=true revision=true undo=true redo=true "
            "snapshot=true reopen=true rendered_position=true low_noise=true no_task=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        try:
            if Gui.Control.activeDialog():
                Gui.Control.closeDialog()
        except Exception:
            pass
        Gui.Selection.clearSelection()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
