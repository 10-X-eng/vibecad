# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for Drawing bolt-circle centerlines."""

from __future__ import annotations

import json
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
import VibeCADNativeDrawingBoltCircleCenterLineRuntime as BoltRuntimeModule
from VibeCADNativeDrawingBoltCircleCenterLineSchema import (
    DRAWING_BOLT_CIRCLE_CENTER_LINE_CAPABILITY_NAME,
    DRAWING_BOLT_CIRCLE_CENTER_LINE_OPERATIONS,
)
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeDrawingDimensionSupport import drawing_selection_state
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state
from VibeCADNativeDrawingLineAttributeState import (
    drawing_line_attribute_inventory_state,
)
from VibeCADNativeDrawingLineLengthState import (
    drawing_line_length_inventory_state,
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
from VibeCADRibbonSurface import read_active_ribbon_surface


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


def _create_fixture(document):
    document.openTransaction("Create Drawing bolt-circle fixture")
    transaction = int(document.getBookedTransactionID())
    try:
        source = document.addObject("Part::Feature", "BoltCircleSource")
        source.Label = "Bolt Circle Source"
        centers = (
            App.Vector(20.0, 0.0, 0.0),
            App.Vector(0.0, 20.0, 0.0),
            App.Vector(-20.0, 0.0, 0.0),
        )
        circles = [
            Part.makeCircle(
                radius,
                center,
                App.Vector(0.0, 0.0, 1.0),
            )
            for radius, center in zip((3.0, 4.0, 5.0), centers, strict=True)
        ]
        arc = Part.makeCircle(
            4.5,
            App.Vector(0.0, -20.0, 0.0),
            App.Vector(0.0, 0.0, 1.0),
            20.0,
            300.0,
        )
        off_pattern = Part.makeCircle(
            2.5,
            App.Vector(25.0, 25.0, 0.0),
            App.Vector(0.0, 0.0, 1.0),
        )
        line = Part.makeLine(
            App.Vector(-8.0, -30.0, 0.0),
            App.Vector(10.0, -30.0, 0.0),
        )
        source.Shape = Part.makeCompound([*circles, arc, off_pattern, line])
        document.publishProvisionalTimelineOperationBlock(source, (), ())

        page = document.addObject("TechDraw::DrawPage", "BoltCirclePage")
        page.Label = "Bolt Circle Page"
        template = document.addObject(
            "TechDraw::DrawSVGTemplate", "BoltCircleTemplate"
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

        view = document.addObject("TechDraw::DrawViewPart", "BoltCircleView")
        view.Label = "Bolt Circle View"
        view.Source = [source]
        view.Direction = App.Vector(0.0, 0.0, 1.0)
        view.XDirection = App.Vector(1.0, 0.0, 0.0)
        view.ScaleType = "Custom"
        view.Scale = 1.4
        view.Rotation = 11.0
        view.X = 108.0
        view.Y = 82.0
        document.publishProvisionalTimelineOperationBlock(view, (), ())
        assert int(page.addView(view)) >= 1
        assert document.recompute([source, view, page], True, True) is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    page.ViewObject.show()
    _events(24)
    assert document.recompute([view, page], True, True) is not False
    projection = drawing_projected_geometry_state(view)
    circular = [
        item
        for item in projection["elements"]
        if item["element_type"] == "edge"
        and "center_in_view_mm" in item
        and float(item["radius_view_mm"]) > 0.0
    ]
    straight = next(
        item
        for item in projection["elements"]
        if item["element_type"] == "edge"
        and "center_in_view_mm" not in item
    )
    assert len(circular) == 5, projection
    assert sum(item["closed"] for item in circular) == 4
    assert sum(not item["closed"] for item in circular) == 1
    return source, page, view, tuple(circular), straight


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(
        DRAWING_BOLT_CIRCLE_CENTER_LINE_CAPABILITY_NAME
    )
    assert definition is not None
    schema = definition.provider_schema(
        DRAWING_BOLT_CIRCLE_CENTER_LINE_OPERATIONS
    )
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 5 * 1024
    branch = schema["parameters"]["oneOf"][0]
    assert branch["properties"]["operation"]["const"] == "create"
    assert branch["required"] == ["page", "view", "holes"]
    assert branch["additionalProperties"] is False
    assert branch["properties"]["holes"]["minItems"] == 3
    assert branch["properties"]["holes"]["maxItems"] == 32
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(DRAWING_BOLT_CIRCLE_CENTER_LINE_CAPABILITY_NAME,),
            schemas=(schema,),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _arguments(page, view, elements) -> dict:
    page_state = drawing_page_state(page)
    view_state = drawing_view_state(view)
    projection = drawing_projected_geometry_state(view)
    return {
        "operation": "create",
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
        "holes": [
            {"subelement": element["name"]}
            for element in elements
        ],
    }


def _selection():
    return tuple(
        (item.Object.Name, tuple(item.SubElementNames))
        for item in Gui.Selection.getSelectionEx()
    )


def _line_boundaries(inventory):
    return [
        json.dumps(
            {
                "start": line["start_in_view_mm"],
                "end": line["end_in_view_mm"],
                "length_mm": line["length_mm"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        for line in inventory["lines"]
    ]


def _human_oracle(document, view, circular):
    attributes_before = drawing_line_attribute_inventory_state(view)
    lengths_before = drawing_line_length_inventory_state(view)
    Gui.Selection.clearSelection()
    for element in circular:
        Gui.Selection.addSelection(view, element["name"])
    Gui.runCommand("TechDraw_ExtensionHoleCircle")
    _events(20)
    assert not Gui.Control.activeDialog()
    assert not Gui.Selection.getSelectionEx()
    attributes = drawing_line_attribute_inventory_state(view)
    lengths = drawing_line_length_inventory_state(view)
    assert attributes["line_count"] == attributes_before["line_count"] + 6
    assert attributes["cosmetic_edge_count"] == 6
    assert lengths["line_count"] == lengths_before["line_count"] + 5
    document.undo()
    _events(16)
    assert drawing_line_attribute_inventory_state(view)[
        "inventory_state_sha256"
    ] == attributes_before["inventory_state_sha256"]
    document.redo()
    _events(16)
    redone = drawing_line_attribute_inventory_state(view)
    assert redone["inventory_state_sha256"] == attributes[
        "inventory_state_sha256"
    ]
    document.undo()
    _events(16)
    return attributes, lengths


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-drawing-bolt-circle-"
        )
        save_path = Path(temporary.name) / "bolt-circle.FCStd"
        controller, surface = _surface()
        action_plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        action = action_plans["TechDraw_ExtensionHoleCircle"]
        assert (
            action.capability_family,
            action.operation_variant,
            action.exact_target_type,
            action.transaction_behavior,
            action.background_required,
        ) == (
            DRAWING_BOLT_CIRCLE_CENTER_LINE_CAPABILITY_NAME,
            "create",
            "ExactOrderedDrawingHoleCirclesAndDerivedBoltCircle",
            "document",
            False,
        )

        document = App.newDocument("NativeDrawingBoltCircleGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        source, page, view, circular, straight = _create_fixture(document)
        objects_before = tuple(document.Objects)
        views_before = tuple(page.Views)
        history_before = tuple(document.VibeCADTimeline.Operations)
        visibility_before = (
            bool(source.ViewObject.Visibility),
            bool(view.ViewObject.Visibility),
            bool(page.ViewObject.Visibility),
        )
        human_attributes, human_lengths = _human_oracle(
            document, view, circular
        )
        assert drawing_line_attribute_inventory_state(view)["cosmetic_edge_count"] == 0

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-drawing-bolt-circle-gui")

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

        Gui.Selection.clearSelection()
        for element in circular:
            Gui.Selection.addSelection(view, element["name"])
        selection_before = _selection()
        snapshot = build_drawing_snapshot(
            document, selection=drawing_selection_state(document)
        )
        selected = snapshot["selected_projected_geometry"]
        assert len(selected) == 1
        assert {
            item["name"] for item in selected[0]["selected_elements"]
        } == {item["name"] for item in circular}

        arguments = _arguments(page, view, circular)
        revision_before = state_store.current_revision(str(document.Uid))
        response = dispatcher.call(
            DRAWING_BOLT_CIRCLE_CENTER_LINE_CAPABILITY_NAME,
            json.dumps(arguments),
            "native-drawing-bolt-circle-create",
        )
        assert response["ok"] is True, response
        assert response["operation"] == "create"
        result = response["bolt_circle_center_lines"]
        assert result["pattern_definition"] == (
            "ordered_first_three_hole_centers"
        )
        assert result["hole_count"] == 5
        assert result["created_cosmetic_edge_count"] == 6
        assert result["all_centers_on_pattern"] is False
        assert result["maximum_pattern_radius_deviation_mm"] > 1.0
        assert len(result["holes"]) == 5
        assert len(json.dumps(response, separators=(",", ":")).encode()) < 14 * 1024
        assert _selection() == selection_before
        assert not Gui.Control.activeDialog()
        assert state_store.current_revision(str(document.Uid)) == revision_before + 1
        repeated = dispatcher.call(
            DRAWING_BOLT_CIRCLE_CENTER_LINE_CAPABILITY_NAME,
            json.dumps(arguments),
            "native-drawing-bolt-circle-create",
        )
        assert repeated == response

        native_attributes = drawing_line_attribute_inventory_state(view)
        native_lengths = drawing_line_length_inventory_state(view)
        assert native_attributes["line_count"] == human_attributes["line_count"]
        assert native_lengths["line_count"] == human_lengths["line_count"]
        assert [line["format"] for line in native_attributes["lines"]] == [
            line["format"] for line in human_attributes["lines"]
        ]
        assert _line_boundaries(native_lengths) == _line_boundaries(
            human_lengths
        )

        refusal_revision = state_store.current_revision(str(document.Uid))
        refusal_undo = int(document.UndoCount)
        stale = dispatcher.call(
            DRAWING_BOLT_CIRCLE_CENTER_LINE_CAPABILITY_NAME,
            json.dumps(arguments),
            "native-drawing-bolt-circle-stale",
        )
        assert stale["ok"] is False
        assert stale["error_code"] in {
            "NATIVE_DRAWING_BOLT_CIRCLE_VIEW_STALE",
            "NATIVE_DRAWING_BOLT_CIRCLE_PROJECTION_STALE",
        }
        fresh_projection = drawing_projected_geometry_state(view)
        fresh_by_name = {
            item["name"]: item for item in fresh_projection["elements"]
        }
        wrong_sources = (
            fresh_by_name[straight["name"]],
            fresh_by_name[circular[1]["name"]],
            fresh_by_name[circular[2]["name"]],
        )
        wrong_type = dispatcher.call(
            DRAWING_BOLT_CIRCLE_CENTER_LINE_CAPABILITY_NAME,
            json.dumps(_arguments(page, view, wrong_sources)),
            "native-drawing-bolt-circle-wrong-type",
        )
        assert wrong_type["ok"] is False
        assert wrong_type["error_code"] == (
            "NATIVE_DRAWING_BOLT_CIRCLE_REFERENCES_INVALID"
        )
        assert state_store.current_revision(str(document.Uid)) == refusal_revision
        assert int(document.UndoCount) == refusal_undo
        assert drawing_line_attribute_inventory_state(view)[
            "inventory_state_sha256"
        ] == native_attributes["inventory_state_sha256"]

        rollback_revision = state_store.current_revision(str(document.Uid))
        rollback_undo = int(document.UndoCount)
        original_verify = (
            BoltRuntimeModule.verify_drawing_bolt_circle_center_lines
        )

        def fail_verify(_document, _draft):
            raise RuntimeError("Injected bolt-circle verification failure")

        BoltRuntimeModule.verify_drawing_bolt_circle_center_lines = fail_verify
        try:
            rollback = dispatcher.call(
                DRAWING_BOLT_CIRCLE_CENTER_LINE_CAPABILITY_NAME,
                json.dumps(_arguments(page, view, circular)),
                "native-drawing-bolt-circle-rollback",
            )
        finally:
            BoltRuntimeModule.verify_drawing_bolt_circle_center_lines = (
                original_verify
            )
        _events(16)
        assert rollback["ok"] is False
        assert rollback["error_code"] == "NATIVE_POSTCONDITION_FAILED"
        assert drawing_line_attribute_inventory_state(view)[
            "inventory_state_sha256"
        ] == native_attributes["inventory_state_sha256"]
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

        document.undo()
        _events(16)
        assert drawing_line_attribute_inventory_state(view)["cosmetic_edge_count"] == 0
        document.redo()
        _events(16)
        redone = drawing_line_attribute_inventory_state(view)
        assert redone["inventory_state_sha256"] == native_attributes[
            "inventory_state_sha256"
        ]

        tags = {result["pattern_circle"]["tag"]} | {
            hole["center_line"]["tag"] for hole in result["holes"]
        }
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
        ]
        assert {
            line["tag"] for line in reopened["lines"] if "tag" in line
        } == tags

        print(
            "VIBECAD_NATIVE_DRAWING_BOLT_CIRCLE_CENTER_LINES_GUI_OK operations=1 "
            "three_point_definition=true five_holes=true circle=true arc=true "
            "human_oracle=true shared_host_builder=true exact_page=true "
            "exact_view=true projection_hash=true element_hash=true "
            "pattern_tag=true radial_tags=true host_style=true "
            "off_pattern_report=true human_acceptance_preserved=true "
            "selection=true visibility=true history=true wrong_type=true "
            "stale=true rollback=true revision=true undo=true redo=true "
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
