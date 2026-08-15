# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact Drawing line decoration."""

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
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state
from VibeCADNativeDrawingLineAttributeState import (
    drawing_line_attribute_inventory_state,
)
from VibeCADNativeDrawingLineAttributesSchema import (
    DRAWING_LINE_ATTRIBUTES_CAPABILITY_NAME,
    DRAWING_LINE_ATTRIBUTES_OPERATIONS,
)
import VibeCADNativeDrawingLineAttributesRuntime as LineAttributesRuntimeModule
from VibeCADNativeDrawingLineDefaults import drawing_line_defaults_state
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
    document.openTransaction("Create Drawing line-attributes fixture")
    transaction = int(document.getBookedTransactionID())
    try:
        source = document.addObject("Part::Feature", "LineAttributesSource")
        source.Label = "Line Attributes Source"
        source.Shape = Part.makeBox(40.0, 25.0, 8.0)
        document.publishProvisionalTimelineOperationBlock(source, (), ())

        page = document.addObject("TechDraw::DrawPage", "LineAttributesPage")
        page.Label = "Line Attributes Page"
        template = document.addObject(
            "TechDraw::DrawSVGTemplate",
            "LineAttributesTemplate",
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

        view = document.addObject("TechDraw::DrawViewPart", "LineAttributesView")
        view.Label = "Line Attributes View"
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
        assert document.recompute([view, page], True, True) is not False

        raw_projection = view.getExactProjectedElementDescriptors()
        vertices = [item["name"] for item in raw_projection["vertices"]]
        assert len(vertices) >= 2
        cosmetic_tag = view.makeCosmeticLine(
            App.Vector(-16.0, -12.0, 0.0),
            App.Vector(16.0, 12.0, 0.0),
        )
        centerline_tag = view.makeCenterLine(vertices[:2], 2)
        assert cosmetic_tag and centerline_tag
        assert document.recompute([view, page], True, True) is not False

        defaults = drawing_line_defaults_state()
        assert defaults["available_style_count"] >= 2
        initial_line_number = 2 if defaults["line_number"] == 1 else 1
        initial_width = (
            defaults["available_widths"]["thin_mm"]
            if defaults["width_choice"] != "thin"
            else defaults["available_widths"]["thick_mm"]
        )
        TechDrawGui.changeDrawingLineAttributes(
            view,
            [
                ("cosmetic_edge", cosmetic_tag),
                ("centerline", centerline_tag),
            ],
            initial_line_number,
            initial_width,
            0.125,
            0.25,
            0.375,
            True,
        )
        assert document.recompute([view, page], True, True) is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    page.ViewObject.show()
    _events(24)
    return source, page, view, cosmetic_tag, centerline_tag


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(DRAWING_LINE_ATTRIBUTES_CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(DRAWING_LINE_ATTRIBUTES_OPERATIONS)
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 8 * 1024
    branches = {
        branch["properties"]["operation"]["const"]: branch
        for branch in schema["parameters"]["oneOf"]
    }
    assert set(branches) == {"set", "read_view"}
    assert branches["set"]["properties"]["targets"]["maxItems"] == 32
    assert branches["read_view"]["properties"]["page_size"]["maximum"] == 48
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(DRAWING_LINE_ATTRIBUTES_CAPABILITY_NAME,),
            schemas=(schema,),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _line_map(inventory):
    return {
        (
            line["kind"],
            line.get("tag", line["subelement"]),
        ): line
        for line in inventory["lines"]
    }


def _expected_format(defaults):
    assert defaults["style_code"] == defaults["line_number"]
    return {
        "line_number": defaults["line_number"],
        "style_code": defaults["style_code"],
        "width_mm": defaults["width_mm"],
        "color_rgb": defaults["color_rgb"],
        "visible": defaults["visible"],
    }


def _human_change(document, view, targets):
    Gui.Selection.clearSelection()
    for line in targets:
        Gui.Selection.addSelection(view, line["subelement"])
    assert len(Gui.Selection.getSelectionEx()[0].SubElementNames) == len(targets)
    Gui.runCommand("TechDraw_ExtensionChangeLineAttributes")
    _events(16)
    assert not Gui.Control.activeDialog()
    assert not Gui.Selection.getSelectionEx()
    after = drawing_line_attribute_inventory_state(view)
    expected = _expected_format(drawing_line_defaults_state())
    for line in _line_map(after).values():
        assert line["format"] == expected
    document.undo()
    _events(12)
    document.redo()
    _events(12)
    redone = drawing_line_attribute_inventory_state(view)
    assert redone["inventory_state_sha256"] == after["inventory_state_sha256"]
    document.undo()
    _events(12)
    return after


def _arguments(page, view, inventory, defaults, *, alternate=False):
    page_state = drawing_page_state(page)
    view_state = drawing_view_state(view)
    projection = drawing_projected_geometry_state(view)
    if alternate:
        line_number = 2 if defaults["line_number"] == 1 else 1
        width_choice = "thin" if defaults["width_choice"] != "thin" else "thick"
        color = {"red": 0.2, "green": 0.4, "blue": 0.6}
    else:
        line_number = defaults["line_number"]
        width_choice = defaults["width_choice"]
        color = defaults["color_rgb"]
    return {
        "operation": "set",
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
        "expected_inventory_state_sha256": inventory[
            "inventory_state_sha256"
        ],
        "targets": [
            (
                {
                    "kind": line["kind"],
                    "subelement": line["subelement"],
                    "expected_line_state_sha256": line["line_state_sha256"],
                }
                if line["kind"] == "projected_edge"
                else {
                    "kind": line["kind"],
                    "tag": line["tag"],
                    "expected_line_state_sha256": line["line_state_sha256"],
                }
            )
            for line in inventory["lines"]
        ],
        "attributes": {
            "expected_line_defaults_state_sha256": defaults["state_sha256"],
            "line_number": line_number,
            "width_choice": width_choice,
            "color_rgb": color,
            "visible": defaults["visible"],
        },
    }


def _read_arguments(page, view, inventory):
    page_state = drawing_page_state(page)
    view_state = drawing_view_state(view)
    projection = drawing_projected_geometry_state(view)
    return {
        "operation": "read_view",
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
        "expected_inventory_state_sha256": inventory[
            "inventory_state_sha256"
        ],
        "offset": 0,
        "page_size": 48,
    }


def _selection():
    return tuple(
        (item.Object.Name, tuple(item.SubElementNames))
        for item in Gui.Selection.getSelectionEx()
    )


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-drawing-line-attributes-"
        )
        save_path = Path(temporary.name) / "drawing-line-attributes.FCStd"
        controller, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        for command_id, exact_target_type in (
            (
                "TechDraw_ExtensionChangeLineAttributes",
                "ExactDrawingPersistentLinesAndCompleteFormat",
            ),
            ("TechDraw_DecorateLine", "ExactDrawingLinesAndCompleteFormat"),
        ):
            plan = plans[command_id]
            assert (
                plan.capability_family,
                plan.operation_variant,
                plan.exact_target_type,
                plan.transaction_behavior,
                plan.background_required,
            ) == (
                DRAWING_LINE_ATTRIBUTES_CAPABILITY_NAME,
                "set",
                exact_target_type,
                "document",
                False,
            )

        document = App.newDocument("NativeDrawingLineAttributesGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        source, page, view, cosmetic_tag, centerline_tag = _create_fixture(document)
        initial = drawing_line_attribute_inventory_state(view)
        assert initial["line_count"] >= 6
        assert initial["projected_edge_count"] >= 4
        assert initial["cosmetic_edge_count"] == 1
        assert initial["centerline_count"] == 1
        assert {
            ("cosmetic_edge", cosmetic_tag),
            ("centerline", centerline_tag),
        } <= set(_line_map(initial))
        projected = next(
            line for line in initial["lines"] if line["kind"] == "projected_edge"
        )
        decorate_before = initial["inventory_state_sha256"]
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(view, projected["subelement"])
        Gui.runCommand("TechDraw_DecorateLine")
        _events(16)
        assert Gui.Control.activeDialog()
        Gui.Control.closeDialog()
        _events(16)
        assert not Gui.Control.activeDialog()
        assert drawing_line_attribute_inventory_state(view)[
            "inventory_state_sha256"
        ] == decorate_before
        objects_before = tuple(document.Objects)
        views_before = tuple(page.Views)
        history_before = tuple(document.VibeCADTimeline.Operations)
        visibility_before = (
            bool(source.ViewObject.Visibility),
            bool(view.ViewObject.Visibility),
            bool(page.ViewObject.Visibility),
        )
        human_after = _human_change(document, view, initial["lines"])
        restored = drawing_line_attribute_inventory_state(view)
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
        ledger.begin_run("native-drawing-line-attributes-gui")

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

        revision_before_read = state_store.current_revision(str(document.Uid))
        read_response = dispatcher.call(
            DRAWING_LINE_ATTRIBUTES_CAPABILITY_NAME,
            json.dumps(_read_arguments(page, view, restored)),
            "native-drawing-line-attributes-read",
        )
        assert read_response["ok"] is True
        read_state = read_response["line_attributes"]
        assert read_state["returned_count"] == restored["line_count"]
        assert read_state["next_offset"] is None
        assert read_state["lines"] == restored["lines"]
        assert state_store.current_revision(str(document.Uid)) == revision_before_read

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(view, restored["lines"][0]["subelement"])
        selection_before = _selection()
        defaults = drawing_line_defaults_state()
        arguments = _arguments(page, view, restored, defaults)
        response = dispatcher.call(
            DRAWING_LINE_ATTRIBUTES_CAPABILITY_NAME,
            json.dumps(arguments),
            "native-drawing-line-attributes-set",
        )
        assert response["ok"] is True, response
        assert response["operation"] == "set"
        assert len(response["line_attributes"]["changed_lines"]) == restored[
            "line_count"
        ]
        assert len(json.dumps(response, separators=(",", ":")).encode()) < 6 * 1024
        repeated = dispatcher.call(
            DRAWING_LINE_ATTRIBUTES_CAPABILITY_NAME,
            json.dumps(arguments),
            "native-drawing-line-attributes-set",
        )
        assert repeated == response
        after = drawing_line_attribute_inventory_state(view)
        assert after["inventory_state_sha256"] == response["line_attributes"][
            "inventory_state_sha256"
        ]
        assert [line["format"] for line in after["lines"]] == [
            line["format"] for line in human_after["lines"]
        ]
        assert _selection() == selection_before
        assert not Gui.Control.activeDialog()

        stale = dispatcher.call(
            DRAWING_LINE_ATTRIBUTES_CAPABILITY_NAME,
            json.dumps(arguments),
            "native-drawing-line-attributes-stale",
        )
        assert stale["ok"] is False
        assert stale["error_code"] == (
            "NATIVE_DRAWING_LINE_ATTRIBUTES_INVENTORY_STALE"
        )
        fresh = drawing_line_attribute_inventory_state(view)
        no_op = dispatcher.call(
            DRAWING_LINE_ATTRIBUTES_CAPABILITY_NAME,
            json.dumps(_arguments(page, view, fresh, defaults)),
            "native-drawing-line-attributes-no-op",
        )
        assert no_op["ok"] is False
        assert no_op["error_code"] == "NATIVE_DRAWING_NO_CHANGE"

        rollback_before = drawing_line_attribute_inventory_state(view)
        rollback_revision = state_store.current_revision(str(document.Uid))
        rollback_undo = int(document.UndoCount)
        original_verify = LineAttributesRuntimeModule.verify_drawing_line_attributes

        def fail_verify(_document, _draft):
            raise RuntimeError("Injected Drawing line-attribute verification failure")

        LineAttributesRuntimeModule.verify_drawing_line_attributes = fail_verify
        try:
            rejected = dispatcher.call(
                DRAWING_LINE_ATTRIBUTES_CAPABILITY_NAME,
                json.dumps(
                    _arguments(
                        page,
                        view,
                        rollback_before,
                        defaults,
                        alternate=True,
                    )
                ),
                "native-drawing-line-attributes-rollback",
            )
        finally:
            LineAttributesRuntimeModule.verify_drawing_line_attributes = original_verify
        _events(12)
        assert rejected["ok"] is False
        assert rejected["error_code"] == "NATIVE_POSTCONDITION_FAILED"
        assert drawing_line_attribute_inventory_state(view)[
            "inventory_state_sha256"
        ] == rollback_before["inventory_state_sha256"]
        assert state_store.current_revision(str(document.Uid)) == rollback_revision
        assert int(document.UndoCount) == rollback_undo
        assert _selection() == selection_before

        assert tuple(document.Objects) == objects_before
        assert tuple(page.Views) == views_before
        assert tuple(document.VibeCADTimeline.Operations) == history_before
        assert visibility_before == (
            bool(source.ViewObject.Visibility),
            bool(view.ViewObject.Visibility),
            bool(page.ViewObject.Visibility),
        )

        snapshot = build_drawing_snapshot(
            document,
            selection=drawing_selection_state(document),
        )
        view_summary = next(
            item
            for item in snapshot["pages"][0]["views"]
            if item["object_name"] == view.Name
        )
        assert view_summary["line_attributes"]["line_count"] == after["line_count"]
        assert view_summary["line_attributes"]["projected_edge_count"] >= 4
        assert view_summary["line_attributes"]["inventory_state_sha256"] == (
            after["inventory_state_sha256"]
        )
        assert view_summary["line_attributes"]["projection_state_sha256"]
        selected_summary = snapshot["selected_line_attributes"]
        assert len(selected_summary) == 1
        assert len(selected_summary[0]["selected_lines"]) == 1
        assert "lines" not in view_summary["line_attributes"]

        document.undo()
        _events(12)
        assert drawing_line_attribute_inventory_state(view)[
            "inventory_state_sha256"
        ] == initial["inventory_state_sha256"]
        document.redo()
        _events(12)
        redone = drawing_line_attribute_inventory_state(view)
        assert redone["inventory_state_sha256"] == after[
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
        reopened = drawing_line_attribute_inventory_state(view)
        assert reopened["inventory_state_sha256"] == redone[
            "inventory_state_sha256"
        ], {"before_save": redone, "after_reopen": reopened}
        assert {
            ("cosmetic_edge", cosmetic_tag),
            ("centerline", centerline_tag),
        } <= set(_line_map(reopened))

        print(
            "VIBECAD_NATIVE_DRAWING_LINE_ATTRIBUTES_GUI_OK operations=2 "
            "read_view=true set=true human_oracle=true shared_host_builder=true "
            "decorate_dialog=true projected_edge=true cosmetic_edge=true "
            "centerline=true stable_tags=true exact_page=true "
            "exact_view=true projection_hash=true inventory_hash=true line_hash=true "
            "complete_format=true explicit_defaults_hash=true paginated=true "
            "limits_published=true selection=true visibility=true history=true "
            "stale=true no_op=true rollback=true revision=true undo=true redo=true "
            "snapshot=true reopen=true low_noise=true no_task=true",
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
