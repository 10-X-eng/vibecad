# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for symmetric Drawing line resizing."""

from __future__ import annotations

import json
import math
from pathlib import Path
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeDrawingDimensionSupport import drawing_selection_state
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state
from VibeCADNativeDrawingLineAttributeState import (
    drawing_line_attribute_inventory_state,
)
from VibeCADNativeDrawingLineDefaults import drawing_line_defaults_state
from VibeCADNativeDrawingLineLengthSchema import (
    DRAWING_LINE_LENGTH_CAPABILITY_NAME,
    DRAWING_LINE_LENGTH_OPERATIONS,
)
from VibeCADNativeDrawingLineLengthState import (
    drawing_line_length_inventory_state,
)
import VibeCADNativeDrawingLineLengthRuntime as LineLengthRuntimeModule
from VibeCADNativeDrawingSnapshot import build_drawing_snapshot
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewState import drawing_view_state
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


def _create_fixture(document):
    document.openTransaction("Create Drawing line-length fixture")
    transaction = int(document.getBookedTransactionID())
    try:
        source = document.addObject("Part::Feature", "LineLengthSource")
        source.Label = "Line Length Source"
        source.Shape = Part.makeBox(80.0, 50.0, 8.0)
        document.publishProvisionalTimelineOperationBlock(source, (), ())

        page = document.addObject("TechDraw::DrawPage", "LineLengthPage")
        page.Label = "Line Length Page"
        template = document.addObject(
            "TechDraw::DrawSVGTemplate",
            "LineLengthTemplate",
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

        view = document.addObject("TechDraw::DrawViewPart", "LineLengthView")
        view.Label = "Line Length View"
        view.Source = [source]
        view.Direction = App.Vector(0.0, 0.0, 1.0)
        view.XDirection = App.Vector(1.0, 0.0, 0.0)
        view.ScaleType = "Custom"
        view.Scale = 1.0
        view.X = 100.0
        view.Y = 80.0
        document.publishProvisionalTimelineOperationBlock(view, (), ())
        assert int(page.addView(view)) >= 1
        assert document.recompute([source, view, page], True, True) is not False
        page.ViewObject.show()
        _events(24)

        projection = view.getExactProjectedElementDescriptors()
        vertices = [item["name"] for item in projection["vertices"]]
        assert len(vertices) >= 2
        cosmetic_tag = view.makeCosmeticLine(
            App.Vector(-24.0, -18.0, 0.0),
            App.Vector(24.0, 18.0, 0.0),
        )
        centerline_tag = view.makeCenterLine(vertices[:2], 2)
        assert cosmetic_tag and centerline_tag
        assert document.recompute([view, page], True, True) is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    page.ViewObject.show()
    _events(24)
    return source, page, view, cosmetic_tag, centerline_tag


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(DRAWING_LINE_LENGTH_CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(DRAWING_LINE_LENGTH_OPERATIONS)
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 10 * 1024
    branches = {
        branch["properties"]["operation"]["const"]: branch
        for branch in schema["parameters"]["oneOf"]
    }
    assert set(branches) == {"extend", "shorten", "read_view"}
    for operation in ("extend", "shorten"):
        delta = branches[operation]["properties"]["delta_distance_mm"]
        assert delta["minimum"] == 0.000001
        assert delta["maximum"] == 1_000_000.0
        assert branches[operation]["additionalProperties"] is False
    assert branches["read_view"]["properties"]["page_size"]["maximum"] == 48
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(DRAWING_LINE_LENGTH_CAPABILITY_NAME,),
            schemas=(schema,),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _line_map(inventory):
    return {(line["kind"], line["tag"]): line for line in inventory["lines"]}


def _line(inventory, kind, tag):
    return _line_map(inventory)[(kind, tag)]


def _close(left, right) -> bool:
    return math.isclose(float(left), float(right), rel_tol=1.0e-10, abs_tol=1.0e-8)


def _assert_change(before, after, signed_delta) -> None:
    length = before["length_mm"]
    dx = after["end_in_view_mm"]["x_mm"] - after["start_in_view_mm"]["x_mm"]
    dy = after["end_in_view_mm"]["y_mm"] - after["start_in_view_mm"]["y_mm"]
    assert _close(after["length_mm"], length + 2.0 * signed_delta)
    assert _close(math.hypot(dx, dy), after["length_mm"])
    for axis in ("x_mm", "y_mm"):
        assert _close(
            (
                before["start_in_view_mm"][axis]
                + before["end_in_view_mm"][axis]
            )
            / 2.0,
            (
                after["start_in_view_mm"][axis]
                + after["end_in_view_mm"][axis]
            )
            / 2.0,
        )
    if before["centerline_extension_mm"] is None:
        assert after["centerline_extension_mm"] is None
    else:
        assert _close(
            after["centerline_extension_mm"],
            before["centerline_extension_mm"] + signed_delta,
        )


def _selection():
    return tuple(
        (item.Object.Name, tuple(item.SubElementNames))
        for item in Gui.Selection.getSelectionEx()
    )


def _human_change(document, view, before_inventory, kind, tag, operation, delta):
    before = _line(before_inventory, kind, tag)
    undo_count_before = int(document.UndoCount)
    undo_names_before = tuple(document.UndoNames)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(view, before["subelement"])
    selected = _selection()
    command = (
        "TechDraw_ExtensionExtendLine"
        if operation == "extend"
        else "TechDraw_ExtensionShortenLine"
    )
    Gui.runCommand(command)
    _events(16)
    assert not Gui.Control.activeDialog()
    assert _selection() == selected
    after_inventory = drawing_line_length_inventory_state(view)
    undo_count_after = int(document.UndoCount)
    undo_names_after = tuple(document.UndoNames)
    assert undo_count_after == undo_count_before + 1, {
        "undo_names_before": undo_names_before,
        "undo_names_after": undo_names_after,
    }
    after = _line(after_inventory, kind, tag)
    signed = delta if operation == "extend" else -delta
    _assert_change(before, after, signed)
    for other_key, other_before in _line_map(before_inventory).items():
        if other_key != (kind, tag):
            assert _line_map(after_inventory)[other_key] == other_before
    document.undo()
    _events(12)
    undone = drawing_line_length_inventory_state(view)
    assert undone["inventory_state_sha256"] == before_inventory[
        "inventory_state_sha256"
    ], {
        "before": before_inventory,
        "after_change": after_inventory,
        "undone": undone,
        "persistent_cosmetic_start": tuple(view.CosmeticEdges[0].Start),
        "persistent_cosmetic_end": tuple(view.CosmeticEdges[0].End),
        "undo_names_before": undo_names_before,
        "undo_names_after": undo_names_after,
        "redo_names_after_undo": tuple(document.RedoNames),
    }
    document.redo()
    _events(12)
    assert drawing_line_length_inventory_state(view)[
        "inventory_state_sha256"
    ] == after_inventory["inventory_state_sha256"]
    document.undo()
    _events(12)
    return after


def _arguments(page, view, inventory, kind, tag, operation, delta):
    page_state = drawing_page_state(page)
    view_state = drawing_view_state(view)
    projection = drawing_projected_geometry_state(view)
    line = _line(inventory, kind, tag)
    return {
        "operation": operation,
        "page": {
            "object_name": page.Name,
            "expected_state_sha256": page_state["state_sha256"],
        },
        "view": {
            "object_name": view.Name,
            "expected_state_sha256": view_state["state_sha256"],
            "expected_projection_state_sha256": projection[
                "projection_state_sha256"
            ],
        },
        "expected_inventory_state_sha256": inventory["inventory_state_sha256"],
        "target": {
            "kind": kind,
            "tag": tag,
            "expected_line_length_state_sha256": line[
                "line_length_state_sha256"
            ],
        },
        "delta_distance_mm": delta,
    }


def _read_arguments(page, view, inventory):
    result = _arguments(
        page,
        view,
        inventory,
        inventory["lines"][0]["kind"],
        inventory["lines"][0]["tag"],
        "extend",
        1.0,
    )
    for name in ("target", "delta_distance_mm"):
        del result[name]
    result.update({"operation": "read_view", "offset": 0, "page_size": 48})
    return result


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-drawing-line-length-"
        )
        save_path = Path(temporary.name) / "drawing-line-length.FCStd"
        controller, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        expected_plan = (
            DRAWING_LINE_LENGTH_CAPABILITY_NAME,
            "ExactDrawingStraightPersistentLineAndSymmetricDelta",
            "document",
            False,
        )
        assert (
            plans["TechDraw_ExtensionExtendLine"].capability_family,
            plans["TechDraw_ExtensionExtendLine"].exact_target_type,
            plans["TechDraw_ExtensionExtendLine"].transaction_behavior,
            plans["TechDraw_ExtensionExtendLine"].background_required,
        ) == expected_plan
        assert plans["TechDraw_ExtensionExtendLine"].operation_variant == "extend"
        assert (
            plans["TechDraw_ExtensionShortenLine"].capability_family,
            plans["TechDraw_ExtensionShortenLine"].exact_target_type,
            plans["TechDraw_ExtensionShortenLine"].transaction_behavior,
            plans["TechDraw_ExtensionShortenLine"].background_required,
        ) == expected_plan
        assert plans["TechDraw_ExtensionShortenLine"].operation_variant == "shorten"

        document = App.newDocument("NativeDrawingLineLengthGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        source, page, view, cosmetic_tag, centerline_tag = _create_fixture(document)
        initial = drawing_line_length_inventory_state(view)
        assert initial["line_count"] == 2
        assert initial["cosmetic_edge_count"] == 1
        assert initial["centerline_count"] == 1
        assert set(_line_map(initial)) == {
            ("cosmetic_edge", cosmetic_tag),
            ("centerline", centerline_tag),
        }
        defaults = drawing_line_defaults_state()
        delta = defaults["delta_distance_mm"]
        assert 0.0 < delta < _line(initial, "centerline", centerline_tag)[
            "length_mm"
        ] / 2.0
        attributes_before = drawing_line_attribute_inventory_state(view)
        objects_before = tuple(document.Objects)
        views_before = tuple(page.Views)
        history_before = tuple(document.VibeCADTimeline.Operations)
        visibility_before = tuple(
            bool(item.ViewObject.Visibility) for item in (source, view, page)
        )

        human_cosmetic = _human_change(
            document,
            view,
            initial,
            "cosmetic_edge",
            cosmetic_tag,
            "extend",
            delta,
        )
        restored = drawing_line_length_inventory_state(view)
        human_centerline = _human_change(
            document,
            view,
            restored,
            "centerline",
            centerline_tag,
            "shorten",
            delta,
        )
        restored = drawing_line_length_inventory_state(view)
        assert restored["inventory_state_sha256"] == initial[
            "inventory_state_sha256"
        ]

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-drawing-line-length-gui")

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
            DRAWING_LINE_LENGTH_CAPABILITY_NAME,
            json.dumps(_read_arguments(page, view, restored)),
            "native-drawing-line-length-read",
        )
        assert read_response["ok"] is True
        assert read_response["line_lengths"]["lines"] == restored["lines"]
        assert read_response["line_lengths"]["next_offset"] is None
        assert state_store.current_revision(str(document.Uid)) == revision0

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(
            view,
            _line(restored, "cosmetic_edge", cosmetic_tag)["subelement"],
        )
        selection_before = _selection()
        extend_args = _arguments(
            page,
            view,
            restored,
            "cosmetic_edge",
            cosmetic_tag,
            "extend",
            delta,
        )
        extended_response = dispatcher.call(
            DRAWING_LINE_LENGTH_CAPABILITY_NAME,
            json.dumps(extend_args),
            "native-drawing-line-length-extend",
        )
        assert extended_response["ok"] is True, extended_response
        assert extended_response["operation"] == "extend"
        assert len(json.dumps(extended_response, separators=(",", ":")).encode()) < 4 * 1024
        assert dispatcher.call(
            DRAWING_LINE_LENGTH_CAPABILITY_NAME,
            json.dumps(extend_args),
            "native-drawing-line-length-extend",
        ) == extended_response
        extended = drawing_line_length_inventory_state(view)
        assert _line(extended, "cosmetic_edge", cosmetic_tag) == human_cosmetic
        assert state_store.current_revision(str(document.Uid)) == revision0 + 1
        stale = dispatcher.call(
            DRAWING_LINE_LENGTH_CAPABILITY_NAME,
            json.dumps(
                _arguments(
                    page,
                    view,
                    restored,
                    "cosmetic_edge",
                    cosmetic_tag,
                    "extend",
                    delta,
                )
            ),
            "native-drawing-line-length-stale",
        )
        assert stale["ok"] is False
        assert stale["error_code"] == "NATIVE_DRAWING_LINE_LENGTH_INVENTORY_STALE"

        shorten_args = _arguments(
            page,
            view,
            extended,
            "centerline",
            centerline_tag,
            "shorten",
            delta,
        )
        shortened_response = dispatcher.call(
            DRAWING_LINE_LENGTH_CAPABILITY_NAME,
            json.dumps(shorten_args),
            "native-drawing-line-length-shorten",
        )
        assert shortened_response["ok"] is True, shortened_response
        assert shortened_response["operation"] == "shorten"
        assert len(json.dumps(shortened_response, separators=(",", ":")).encode()) < 4 * 1024
        final_inventory = drawing_line_length_inventory_state(view)
        assert _line(final_inventory, "centerline", centerline_tag) == human_centerline
        assert _selection() == selection_before
        assert state_store.current_revision(str(document.Uid)) == revision0 + 2

        invalid_before = final_inventory
        invalid_revision = state_store.current_revision(str(document.Uid))
        invalid_undo = int(document.UndoCount)
        invalid_args = _arguments(
            page,
            view,
            invalid_before,
            "centerline",
            centerline_tag,
            "shorten",
            _line(invalid_before, "centerline", centerline_tag)["length_mm"] / 2.0,
        )
        invalid = dispatcher.call(
            DRAWING_LINE_LENGTH_CAPABILITY_NAME,
            json.dumps(invalid_args),
            "native-drawing-line-length-invalid-shorten",
        )
        assert invalid["ok"] is False
        assert invalid["error_code"] == "NATIVE_DRAWING_LINE_LENGTH_TOO_SHORT"
        assert drawing_line_length_inventory_state(view) == invalid_before
        assert state_store.current_revision(str(document.Uid)) == invalid_revision
        assert int(document.UndoCount) == invalid_undo

        rollback_before = drawing_line_length_inventory_state(view)
        rollback_revision = state_store.current_revision(str(document.Uid))
        rollback_undo = int(document.UndoCount)
        original_verify = LineLengthRuntimeModule.verify_drawing_line_length

        def fail_verify(_document, _draft):
            raise RuntimeError("Injected Drawing line-length verification failure")

        LineLengthRuntimeModule.verify_drawing_line_length = fail_verify
        try:
            rejected = dispatcher.call(
                DRAWING_LINE_LENGTH_CAPABILITY_NAME,
                json.dumps(
                    _arguments(
                        page,
                        view,
                        rollback_before,
                        "cosmetic_edge",
                        cosmetic_tag,
                        "extend",
                        0.5,
                    )
                ),
                "native-drawing-line-length-rollback",
            )
        finally:
            LineLengthRuntimeModule.verify_drawing_line_length = original_verify
        _events(12)
        assert rejected["ok"] is False
        assert rejected["error_code"] == "NATIVE_POSTCONDITION_FAILED"
        assert drawing_line_length_inventory_state(view) == rollback_before
        assert state_store.current_revision(str(document.Uid)) == rollback_revision
        assert int(document.UndoCount) == rollback_undo
        assert _selection() == selection_before

        assert drawing_line_attribute_inventory_state(view) == attributes_before
        assert tuple(document.Objects) == objects_before
        assert tuple(page.Views) == views_before
        assert tuple(document.VibeCADTimeline.Operations) == history_before
        assert visibility_before == tuple(
            bool(item.ViewObject.Visibility) for item in (source, view, page)
        )
        assert not Gui.Control.activeDialog()

        snapshot = build_drawing_snapshot(
            document,
            selection=drawing_selection_state(document),
        )
        view_summary = next(
            item
            for item in snapshot["pages"][0]["views"]
            if item["object_name"] == view.Name
        )
        assert view_summary["line_lengths"]["line_count"] == 2
        assert view_summary["line_lengths"]["inventory_state_sha256"] == (
            final_inventory["inventory_state_sha256"]
        )
        assert "lines" not in view_summary["line_lengths"]
        selected_summary = snapshot["selected_line_lengths"]
        assert len(selected_summary) == 1
        assert len(selected_summary[0]["selected_lines"]) == 1

        document.undo()
        _events(12)
        after_one_undo = drawing_line_length_inventory_state(view)
        assert _line(after_one_undo, "cosmetic_edge", cosmetic_tag) == human_cosmetic
        assert _line(after_one_undo, "centerline", centerline_tag) == _line(
            initial,
            "centerline",
            centerline_tag,
        )
        document.undo()
        _events(12)
        assert drawing_line_length_inventory_state(view)[
            "inventory_state_sha256"
        ] == initial["inventory_state_sha256"]
        document.redo()
        document.redo()
        _events(16)
        redone = drawing_line_length_inventory_state(view)
        assert redone["inventory_state_sha256"] == final_inventory[
            "inventory_state_sha256"
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
        reopened = drawing_line_length_inventory_state(view)
        assert reopened["inventory_state_sha256"] == redone[
            "inventory_state_sha256"
        ], {"before_save": redone, "after_reopen": reopened}
        assert set(_line_map(reopened)) == {
            ("cosmetic_edge", cosmetic_tag),
            ("centerline", centerline_tag),
        }

        print(
            "VIBECAD_NATIVE_DRAWING_LINE_LENGTH_GUI_OK operations=3 "
            "read_view=true extend=true shorten=true human_oracle=true "
            "shared_host_builder=true cosmetic_edge=true centerline=true "
            "explicit_delta=true symmetric=true stable_tags=true exact_page=true "
            "exact_view=true projection_hash=true inventory_hash=true line_hash=true "
            "paginated=true limits_published=true selection=true visibility=true "
            "history=true stale=true invalid_shorten=true rollback=true revision=true "
            "undo=true redo=true snapshot=true reopen=true low_noise=true no_task=true",
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
