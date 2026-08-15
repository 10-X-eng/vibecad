# SPDX-License-Identifier: LGPL-2.1-or-later

"""Live compiled-GUI gate for fit, surface-finish, and weld-symbol tools."""

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
from PySide import QtCore, QtWidgets
import TechDrawGui

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeDrawingFormatSchema import (
    DRAWING_FORMAT_CAPABILITY_NAME,
    DRAWING_FORMAT_OPERATIONS,
)
from VibeCADNativeDrawingFormatState import drawing_format_state
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state
from VibeCADNativeDrawingLeaderState import drawing_leader_state
from VibeCADNativeDrawingRichAnnotationState import drawing_rich_annotation_owner_state
from VibeCADNativeDrawingSnapshot import build_drawing_snapshot
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingSymbol import drawing_weld_catalog_state
from VibeCADNativeDrawingSymbolSchema import (
    DRAWING_SYMBOL_CAPABILITY_NAME,
    DRAWING_SYMBOL_OPERATIONS,
)
from VibeCADNativeDrawingSymbolState import (
    drawing_surface_finish_symbol_state,
    drawing_weld_symbol_state,
)
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


def _fixture(document):
    document.openTransaction("Create engineering-symbol fixture")
    transaction = int(document.getBookedTransactionID())
    try:
        source = document.addObject("Part::Feature", "SymbolSource")
        source.Shape = Part.makeBox(40.0, 28.0, 8.0)
        document.publishProvisionalTimelineOperationBlock(source, (), ())

        page = document.addObject("TechDraw::DrawPage", "SymbolPage")
        template = document.addObject("TechDraw::DrawSVGTemplate", "SymbolTemplate")
        template.Template = str(
            Path(App.getResourceDir())
            / "Mod" / "TechDraw" / "Templates" / "ISO" / "A4_Landscape_TD.svg"
        )
        page.Template = template
        document.publishProvisionalTimelineOperationBlock(page, (template,), ())

        view = document.addObject("TechDraw::DrawViewPart", "SymbolView")
        view.Source = [source]
        view.Direction = App.Vector(0.0, 0.0, 1.0)
        view.XDirection = App.Vector(1.0, 0.0, 0.0)
        view.ScaleType = "Custom"
        view.Scale = 1.3
        view.X = 105.0
        view.Y = 72.0
        assert int(page.addView(view)) >= 1
        document.publishProvisionalTimelineOperationBlock(view, (), ())
        assert document.recompute([source, view, page], True, True) is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    page.ViewObject.show()
    _events(28)

    document.openTransaction("Create engineering-symbol targets")
    transaction = int(document.getBookedTransactionID())
    try:
        projection = drawing_projected_geometry_state(view)
        edge = next(item for item in projection["elements"] if item["element_type"] == "edge")
        dimension = TechDrawGui.createProjectedDimension(
            view, "Distance", [edge["name"]], False, 20.0, 30.0
        )
        dimension.Label = "Fit Dimension"
        document.publishProvisionalTimelineOperationBlock(dimension, (), ())
        leader = TechDrawGui.createDrawingLeaderLine(
            page,
            view,
            [(75.0, 58.0), (115.0, 92.0), (155.0, 92.0)],
            "Weld Leader",
            "filled_arrow",
            "none",
            True,
            True,
            True,
            0.5,
            "continuous",
            0.1,
            0.1,
            0.1,
        )["leader"]
        assert document.recompute([dimension, leader, view, page], True, True) is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    _events(24)
    return source, page, view, dimension, leader


def _human_accept(command: str, target, document, expected_new: int) -> tuple:
    before = tuple(document.Objects)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(target)
    Gui.runCommand(command)
    _events(18)
    assert Gui.Control.activeDialog(), command
    task = Gui.Control.activeTaskDialog()
    assert task is not None, command
    task.accept()
    _events(24)
    assert not Gui.Control.activeDialog(), command
    created = tuple(obj for obj in document.Objects if obj not in before)
    assert len(created) == expected_new, (command, created)
    return created


def _human_oracles(document, page, view, dimension, leader) -> None:
    fit_before = drawing_format_state(dimension)
    _human_accept("TechDraw_HoleShaftFit", dimension, document, 0)
    assert drawing_format_state(dimension)["format_state_sha256"] != fit_before["format_state_sha256"]
    document.undo()
    _events(12)
    fit_after_undo = drawing_format_state(dimension)
    assert fit_after_undo["format_state_sha256"] == fit_before["format_state_sha256"], (
        fit_before,
        fit_after_undo,
    )

    surface_created = _human_accept(
        "TechDraw_SurfaceFinishSymbols", view, document, 1
    )
    drawing_surface_finish_symbol_state(surface_created[0])
    document.undo()
    _events(12)
    assert all(obj not in tuple(document.Objects) for obj in surface_created)

    weld_created = _human_accept("TechDraw_WeldSymbol", leader, document, 3)
    weld = next(obj for obj in weld_created if obj.TypeId == "TechDraw::DrawWeldSymbol")
    drawing_weld_symbol_state(weld)
    document.undo()
    _events(12)
    assert all(obj not in tuple(document.Objects) for obj in weld_created)
    assert document.getBookedTransactionID() == 0
    page.ViewObject.show()


def _turn(surface, registry) -> NativeTurnSnapshot:
    definitions = (
        (DRAWING_FORMAT_CAPABILITY_NAME, DRAWING_FORMAT_OPERATIONS),
        (DRAWING_SYMBOL_CAPABILITY_NAME, DRAWING_SYMBOL_OPERATIONS),
    )
    schemas = []
    for name, operations in definitions:
        definition = registry.definition(name)
        assert definition is not None
        schema = definition.provider_schema(operations)
        branches = {
            branch["properties"]["operation"]["const"]: branch
            for branch in schema["parameters"]["oneOf"]
        }
        assert set(branches) == set(operations)
        assert all(branch["additionalProperties"] is False for branch in branches.values())
        assert "unknown" not in json.dumps(schema).casefold()
        schemas.append(schema)
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=tuple(name for name, _operations in definitions),
            schemas=tuple(schemas),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _format_target(dimension) -> dict[str, str]:
    state = drawing_format_state(dimension)
    return {
        "object_name": state["object_name"],
        "expected_format_state_sha256": state["format_state_sha256"],
    }


def _page_target(page) -> dict[str, str]:
    state = drawing_page_state(page)
    return {"object_name": page.Name, "expected_state_sha256": state["state_sha256"]}


def _owner_target(view, page) -> dict[str, str]:
    state = drawing_rich_annotation_owner_state(view, page=page)
    return {
        "kind": "view",
        "object_name": view.Name,
        "expected_owner_state_sha256": state["owner_state_sha256"],
    }


def _weld_spec(catalog_hash: str, *, label: str, field: bool) -> dict:
    return {
        "expected_catalog_sha256": catalog_hash,
        "all_around": True,
        "field_weld": field,
        "alternating_weld": False,
        "tail_text": "WPS-42",
        "arrow_side": {
            "left_text": "6",
            "center_text": "",
            "right_text": "50-100",
            "symbol_key": "aws_fillet_down",
        },
        "other_side": {
            "left_text": "4",
            "center_text": "",
            "right_text": "",
            "symbol_key": "aws_fillet_up",
        },
        "label": label,
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-drawing-symbols-")
        save_path = Path(temporary.name) / "drawing-engineering-symbols.FCStd"
        controller, surface = _surface()
        inventory = {plan.command_id: plan for plan in resolve_native_action_inventory(surface).plans}
        assert (
            inventory["TechDraw_HoleShaftFit"].capability_family,
            inventory["TechDraw_HoleShaftFit"].operation_variant,
        ) == (DRAWING_FORMAT_CAPABILITY_NAME, "apply_iso_286_fit")
        assert (
            inventory["TechDraw_SurfaceFinishSymbols"].capability_family,
            inventory["TechDraw_SurfaceFinishSymbols"].operation_variant,
        ) == (DRAWING_SYMBOL_CAPABILITY_NAME, "create_iso_surface_finish")
        assert (
            inventory["TechDraw_WeldSymbol"].capability_family,
            inventory["TechDraw_WeldSymbol"].operation_variant,
        ) == (DRAWING_SYMBOL_CAPABILITY_NAME, "create_weld")

        document = App.newDocument("NativeDrawingEngineeringSymbolGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        source, page, view, dimension, leader = _fixture(document)
        _human_oracles(document, page, view, dimension, leader)

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-drawing-engineering-symbols-gui")

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

        def call(tool: str, arguments: dict, succeeds: bool = True) -> dict:
            nonlocal call_index
            call_index += 1
            response = dispatcher.call(
                tool,
                json.dumps(arguments, separators=(",", ":")),
                f"native-drawing-symbol-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        image_before = _page_image_sha256()
        stale_fit = _format_target(dimension)
        stale_fit["expected_format_state_sha256"] = "0" * 64
        rejected = call(
            DRAWING_FORMAT_CAPABILITY_NAME,
            {"operation": "apply_iso_286_fit", "dimension": stale_fit, "tolerance_class": "H7"},
            False,
        )
        assert rejected["error_code"] == "NATIVE_DRAWING_FIT_TARGET_STALE"

        fit = call(
            DRAWING_FORMAT_CAPABILITY_NAME,
            {
                "operation": "apply_iso_286_fit",
                "dimension": _format_target(dimension),
                "tolerance_class": "H7",
            },
        )
        assert fit["fit"]["tolerance_class"] == "H7"
        assert fit["fit"]["equal"] is False
        fit_hash = fit["dimension"]["format_state_sha256"]

        surface_result = call(
            DRAWING_SYMBOL_CAPABILITY_NAME,
            {
                "operation": "create_iso_surface_finish",
                "page": _page_target(page),
                "owner": _owner_target(view, page),
                "placement_on_page_mm": {"x_mm": 155.0, "y_mm": 52.0},
                "symbol_type": "removal_required_all_around",
                "method": "GRIND",
                "machining_allowance": "0.5",
                "lay": "M",
                "rotation_degrees": 0.0,
                "label": "Ground Surface Finish",
                "roughness": "Ra1, 6",
            },
        )
        surface_state = surface_result["surface_finish_symbol"]
        surface_symbol = document.getObject(surface_state["object_name"])
        assert surface_symbol is not None and surface_state["valid"]

        catalog_revision = state_store.current_revision(str(document.Uid))
        catalog = call(
            DRAWING_SYMBOL_CAPABILITY_NAME,
            {"operation": "read_weld_catalog"},
        )
        assert state_store.current_revision(str(document.Uid)) == catalog_revision
        assert len(catalog["items"]) == 26
        assert {item["key"] for item in catalog["items"]} >= {
            "aws_fillet_down", "aws_fillet_up", "blank"
        }

        leader_state = drawing_leader_state(leader)
        weld_result = call(
            DRAWING_SYMBOL_CAPABILITY_NAME,
            {
                "operation": "create_weld",
                "leader": {
                    "object_name": leader.Name,
                    "expected_leader_state_sha256": leader_state["leader_state_sha256"],
                },
                **_weld_spec(catalog["catalog_sha256"], label="Production Weld", field=False),
            },
        )
        weld_state = weld_result["weld_symbol"]
        weld = document.getObject(weld_state["object_name"])
        assert weld is not None and len(weld_state["tiles"]) == 2
        assert all(
            tile["source_svg_sha256"] == tile["embedded_svg_sha256"]
            for tile in weld_state["tiles"]
        )

        stale_weld = {
            "object_name": weld.Name,
            "expected_symbol_state_sha256": "0" * 64,
        }
        rejected = call(
            DRAWING_SYMBOL_CAPABILITY_NAME,
            {
                "operation": "edit_weld",
                "symbol": stale_weld,
                **_weld_spec(catalog["catalog_sha256"], label="Production Weld Rev B", field=True),
            },
            False,
        )
        assert rejected["error_code"] == "NATIVE_DRAWING_WELD_SYMBOL_STALE"

        weld_before_edit = drawing_weld_symbol_state(weld)
        edited = call(
            DRAWING_SYMBOL_CAPABILITY_NAME,
            {
                "operation": "edit_weld",
                "symbol": {
                    "object_name": weld.Name,
                    "expected_symbol_state_sha256": weld_before_edit["symbol_state_sha256"],
                },
                **_weld_spec(catalog["catalog_sha256"], label="Production Weld Rev B", field=True),
            },
        )
        edited_state = edited["weld_symbol"]
        assert edited_state["field_weld"] is True
        assert _page_image_sha256() != image_before

        document.undo()
        _events(12)
        assert drawing_weld_symbol_state(weld)["symbol_state_sha256"] == weld_before_edit["symbol_state_sha256"]
        document.redo()
        _events(12)
        weld = document.getObject(edited_state["object_name"])
        reopened_weld_state = drawing_weld_symbol_state(weld)
        assert reopened_weld_state["symbol_state_sha256"] == edited_state["symbol_state_sha256"], (
            edited_state,
            reopened_weld_state,
        )

        snapshot = build_drawing_snapshot(
            document,
            selection={
                "document_uid": str(document.Uid),
                "selected_count": 1,
                "items": [{"object": {"document_uid": str(document.Uid), "object_name": weld.Name, "type_id": weld.TypeId}}],
            },
        )
        assert snapshot["weld_symbol_catalog"] == {
            "catalog_sha256": catalog["catalog_sha256"],
            "item_count": 26,
        }
        assert snapshot["selected_engineering_symbols"][0]["kind"] == "weld"
        assert len(json.dumps(snapshot, separators=(",", ":")).encode()) < 128 * 1024

        leader_before_save = drawing_leader_state(leader)
        names = {
            "page": page.Name,
            "dimension": dimension.Name,
            "surface": surface_symbol.Name,
            "weld": weld.Name,
            "leader": leader.Name,
        }
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        page = document.getObject(names["page"])
        dimension = document.getObject(names["dimension"])
        surface_symbol = document.getObject(names["surface"])
        weld = document.getObject(names["weld"])
        assert all(obj is not None for obj in (page, dimension, surface_symbol, weld))
        page.ViewObject.show()
        assert document.recompute() is not False
        _events(20)
        assert drawing_format_state(dimension)["format_state_sha256"] == fit_hash
        assert drawing_surface_finish_symbol_state(surface_symbol)["symbol_state_sha256"] == surface_state["symbol_state_sha256"]
        leader_after_reopen = drawing_leader_state(document.getObject(names["leader"]))
        assert leader_after_reopen["leader_state_sha256"] == leader_before_save["leader_state_sha256"], (
            leader_before_save,
            leader_after_reopen,
        )
        reopened_weld_state = drawing_weld_symbol_state(weld)
        assert reopened_weld_state["symbol_state_sha256"] == edited_state["symbol_state_sha256"], (
            edited_state,
            reopened_weld_state,
        )

        print(
            "VIBECAD_NATIVE_DRAWING_ENGINEERING_SYMBOLS_GUI_OK operations=6 "
            "iso286_fit=true surface_finish=true weld_create=true weld_edit=true "
            "weld_catalog=true human_oracles=3 shared_builders=true embedded_svg=true "
            "exact_targets=true stale_refusal=true history_resources=true visual=true "
            "undo=true redo=true snapshot=true reopen=true low_noise=true",
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
