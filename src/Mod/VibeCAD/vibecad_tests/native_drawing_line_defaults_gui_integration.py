# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for current Drawing line defaults."""

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
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeDrawingLineDefaults import drawing_line_defaults_state
from VibeCADNativeDrawingLineDefaultsSchema import (
    DRAWING_LINE_DEFAULTS_CAPABILITY_NAME,
    DRAWING_LINE_DEFAULTS_OPERATIONS,
)
from VibeCADNativeDrawingSnapshot import build_drawing_snapshot
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
    document.openTransaction("Create Drawing line-defaults fixture")
    transaction = int(document.getBookedTransactionID())
    try:
        source = document.addObject("Part::Feature", "LineDefaultsSource")
        source.Label = "Line Defaults Source"
        source.Shape = Part.makeBox(40.0, 25.0, 8.0)
        document.publishProvisionalTimelineOperationBlock(source, (), ())

        page = document.addObject("TechDraw::DrawPage", "LineDefaultsPage")
        page.Label = "Line Defaults Page"
        template = document.addObject(
            "TechDraw::DrawSVGTemplate",
            "LineDefaultsTemplate",
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

        view = document.addObject("TechDraw::DrawViewPart", "LineDefaultsView")
        view.Label = "Line Defaults View"
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
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    page.ViewObject.show()
    _events(24)
    return source, page, view


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(DRAWING_LINE_DEFAULTS_CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(DRAWING_LINE_DEFAULTS_OPERATIONS)
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 2 * 1024
    assert schema["parameters"]["oneOf"] == [
        {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "const": "read_current"}
            },
            "required": ["operation"],
            "additionalProperties": False,
        }
    ]
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(DRAWING_LINE_DEFAULTS_CAPABILITY_NAME,),
            schemas=(schema,),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _human_oracle() -> dict:
    before = drawing_line_defaults_state()
    Gui.runCommand("TechDraw_ExtensionSelectLineAttributes")
    _events(12)
    dialog = Gui.Control.activeTaskDialog()
    assert dialog is not None
    main_window = Gui.getMainWindow()
    styles = main_window.findChild(QtWidgets.QComboBox, "cbLineStyle")
    thick = main_window.findChild(QtWidgets.QRadioButton, "rbThick")
    spacing = main_window.findChild(QtWidgets.QDoubleSpinBox, "sbSpacing")
    stretch = main_window.findChild(QtWidgets.QDoubleSpinBox, "sbStretch")
    assert all(widget is not None for widget in (styles, thick, spacing, stretch))
    assert styles.count() == before["available_style_count"]
    target_index = 1 if before["line_number"] == 1 else 0
    assert target_index < styles.count()
    styles.setCurrentIndex(target_index)
    thick.setChecked(True)
    spacing.setValue(11.5)
    stretch.setValue(4.25)
    dialog.accept()
    _events(12)
    assert not Gui.Control.activeDialog()

    after = drawing_line_defaults_state()
    assert after["state_sha256"] != before["state_sha256"]
    assert after["line_number"] == target_index + 1
    assert after["style_name"] == after["available_styles"][target_index]["name"]
    assert after["width_choice"] == "thick"
    assert after["width_mm"] == after["available_widths"]["thick_mm"]
    assert after["cascade_spacing_mm"] == 11.5
    assert after["delta_distance_mm"] == 4.25
    assert after["valid"] and not after["issues"]
    return after


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-drawing-line-defaults-"
        )
        save_path = Path(temporary.name) / "drawing-line-defaults.FCStd"
        controller, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["TechDraw_ExtensionSelectLineAttributes"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.transaction_behavior,
            plan.background_required,
        ) == (
            DRAWING_LINE_DEFAULTS_CAPABILITY_NAME,
            "read_current",
            "CurrentTechDrawLineAndPlacementDefaults",
            "none",
            False,
        )

        document = App.newDocument("NativeDrawingLineDefaultsGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        source, page, view = _create_fixture(document)
        objects_before = tuple(document.Objects)
        views_before = tuple(page.Views)
        history_before = tuple(document.VibeCADTimeline.Operations)
        visibility_before = (
            bool(source.ViewObject.Visibility),
            bool(view.ViewObject.Visibility),
            bool(page.ViewObject.Visibility),
        )
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(view)
        selection_before = tuple(
            (item.Object.Name, tuple(item.SubElementNames))
            for item in Gui.Selection.getSelectionEx()
        )
        human_state = _human_oracle()
        assert tuple(document.Objects) == objects_before
        assert tuple(page.Views) == views_before
        assert tuple(document.VibeCADTimeline.Operations) == history_before

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        revision_before = state_store.current_revision(str(document.Uid))
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-drawing-line-defaults-gui")

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

        arguments = json.dumps({"operation": "read_current"})
        response = dispatcher.call(
            DRAWING_LINE_DEFAULTS_CAPABILITY_NAME,
            arguments,
            "native-drawing-line-defaults-1",
        )
        assert response == {"ok": True, "line_defaults": human_state}
        assert len(json.dumps(response, separators=(",", ":")).encode()) < 12 * 1024
        repeated = dispatcher.call(
            DRAWING_LINE_DEFAULTS_CAPABILITY_NAME,
            arguments,
            "native-drawing-line-defaults-1",
        )
        assert repeated == response and dispatcher.call_count == 1
        invalid = dispatcher.call(
            DRAWING_LINE_DEFAULTS_CAPABILITY_NAME,
            json.dumps({"operation": "read_current", "unexpected": True}),
            "native-drawing-line-defaults-invalid",
        )
        assert not invalid["ok"]
        assert invalid["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert invalid["argument_error"]["path"] == []
        assert invalid["argument_error"]["rule"] == "additionalProperties"

        assert state_store.current_revision(str(document.Uid)) == revision_before
        assert tuple(document.Objects) == objects_before
        assert tuple(page.Views) == views_before
        assert tuple(document.VibeCADTimeline.Operations) == history_before
        assert selection_before == tuple(
            (item.Object.Name, tuple(item.SubElementNames))
            for item in Gui.Selection.getSelectionEx()
        )
        assert visibility_before == (
            bool(source.ViewObject.Visibility),
            bool(view.ViewObject.Visibility),
            bool(page.ViewObject.Visibility),
        )
        assert not Gui.Control.activeDialog()

        snapshot = build_drawing_snapshot(document)
        summary = snapshot["line_defaults"]
        assert summary is not None
        assert summary["state_sha256"] == human_state["state_sha256"]
        assert summary["available_style_count"] == len(
            human_state["available_styles"]
        )
        assert "available_styles" not in summary

        names = {
            "source": source.Name,
            "page": page.Name,
            "view": view.Name,
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
        assert drawing_line_defaults_state()["state_sha256"] == human_state[
            "state_sha256"
        ]

        print(
            "VIBECAD_NATIVE_DRAWING_LINE_DEFAULTS_GUI_OK "
            "operation=read_current human_oracle=true shared_host_state=true "
            "session_scope=true standard=true style_catalog=true line_number=true "
            "style_code=true exact_width=true width_choices=true color=true "
            "cascade_spacing=true delta_distance=true argument_free=true "
            "read_only=true revision_unchanged=true objects_unchanged=true "
            "selection=true visibility=true history=true snapshot=true "
            "reopen=true low_noise=true no_task=true",
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
