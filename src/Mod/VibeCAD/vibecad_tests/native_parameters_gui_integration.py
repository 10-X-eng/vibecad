# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for the Native Parameters spreadsheet surface."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeBackgroundSchema import NATIVE_BACKGROUND_CAPABILITY_NAME
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeInput import authorize_native_input_path
from VibeCADNativeOutput import authorize_native_output_path
from VibeCADNativeParametersSchema import (
    PARAMETERS_CELL_CAPABILITY_NAME,
    PARAMETERS_EXPORT_CAPABILITY_NAME,
    PARAMETERS_FORMAT_CAPABILITY_NAME,
    PARAMETERS_READ_CAPABILITY_NAME,
    PARAMETERS_SHEET_CAPABILITY_NAME,
)
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


_ACTION_MAP = {
    "Spreadsheet_CreateSheet": ("parameters.sheet", "create", "document", False),
    "Spreadsheet_Import": ("parameters.sheet", "import_csv", "background", True),
    "Spreadsheet_Export": ("parameters.export", "export_csv", "background_output", True),
    "Spreadsheet_MergeCells": ("parameters.cell", "merge", "document", False),
    "Spreadsheet_SplitCell": ("parameters.cell", "split", "document", False),
    "Spreadsheet_CellProperties": ("parameters.cell", "set_properties", "document", False),
    "Spreadsheet_SetAlias": ("parameters.cell", "set_alias", "document", False),
    "Spreadsheet_AlignLeft": ("parameters.format", "align_left", "document", False),
    "Spreadsheet_AlignCenter": ("parameters.format", "align_center", "document", False),
    "Spreadsheet_AlignRight": ("parameters.format", "align_right", "document", False),
    "Spreadsheet_AlignTop": ("parameters.format", "align_top", "document", False),
    "Spreadsheet_AlignVCenter": ("parameters.format", "align_vertical_center", "document", False),
    "Spreadsheet_AlignBottom": ("parameters.format", "align_bottom", "document", False),
    "Spreadsheet_StyleBold": ("parameters.format", "set_bold", "document", False),
    "Spreadsheet_StyleItalic": ("parameters.format", "set_italic", "document", False),
    "Spreadsheet_StyleUnderline": ("parameters.format", "set_underline", "document", False),
}
_TOOLS = (
    NATIVE_BACKGROUND_CAPABILITY_NAME,
    PARAMETERS_SHEET_CAPABILITY_NAME,
    PARAMETERS_READ_CAPABILITY_NAME,
    PARAMETERS_CELL_CAPABILITY_NAME,
    PARAMETERS_FORMAT_CAPABILITY_NAME,
    PARAMETERS_EXPORT_CAPABILITY_NAME,
)


def _events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _parameters_surface():
    main_window = Gui.getMainWindow()
    controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
    tabs = main_window.findChild(QtWidgets.QTabBar, "VibeCADRibbonTabs")
    assert controller is not None and tabs is not None
    index = next(
        value
        for value in range(tabs.count())
        if str(tabs.tabData(value)) == "SpreadsheetWorkbench"
    )
    tabs.setCurrentIndex(index)
    _events(24)
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "parameters", surface.surface_id
    return controller, surface


def _selection() -> tuple:
    return tuple(
        (item.Object.Name, tuple(item.SubElementNames))
        for item in Gui.Selection.getSelectionEx()
    )


def _turn(surface, registry) -> NativeTurnSnapshot:
    operations = {
        NATIVE_BACKGROUND_CAPABILITY_NAME: ("status", "cancel"),
        PARAMETERS_SHEET_CAPABILITY_NAME: ("create", "import_csv"),
        PARAMETERS_READ_CAPABILITY_NAME: ("read_range",),
        PARAMETERS_CELL_CAPABILITY_NAME: (
            "write_values",
            "write_formulas",
            "set_alias",
            "merge",
            "split",
            "set_properties",
        ),
        PARAMETERS_FORMAT_CAPABILITY_NAME: (
            "align_left",
            "align_center",
            "align_right",
            "align_top",
            "align_vertical_center",
            "align_bottom",
            "set_bold",
            "set_italic",
            "set_underline",
        ),
        PARAMETERS_EXPORT_CAPABILITY_NAME: ("export_csv",),
    }
    schemas = tuple(
        registry.definition(name).provider_schema(operations[name]) for name in _TOOLS
    )
    serialized = json.dumps(schemas, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in serialized.casefold()
    assert len(serialized.encode()) < 48 * 1024
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=_TOOLS,
            schemas=schemas,
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _sheet_target(summary: dict) -> dict:
    return {
        "object_name": summary["object_name"],
        "expected_state_sha256": summary["state_sha256"],
    }


def _cell(read: dict, address: str) -> dict:
    state = next(item for item in read["cells"] if item["address"] == address)
    return {
        "address": address,
        "expected_cell_state_sha256": state["cell_state_sha256"],
    }


def _range(read: dict) -> dict:
    return {
        "range": read["range"],
        "expected_range_state_sha256": read["range_state_sha256"],
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("SpreadsheetWorkbench")
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-parameters-")
        root = Path(temporary.name)
        save_path = root / "native-parameters.FCStd"
        csv_path = root / "exported-parameters.csv"
        document = App.newDocument("NativeParametersGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        controller, surface = _parameters_surface()
        plans = {plan.command_id: plan for plan in resolve_native_action_inventory(surface).plans}
        for command_id, expected in _ACTION_MAP.items():
            plan = plans[command_id]
            assert (
                plan.capability_family,
                plan.operation_variant,
                plan.transaction_behavior,
                plan.background_required,
            ) == expected

        # Human and Native creation share SpreadsheetGui::MutationSupport publication.
        before = tuple(document.Objects)
        Gui.runCommand("Spreadsheet_CreateSheet")
        _events(12)
        human_sheet = next(
            value
            for value in document.Objects
            if value not in before and value.TypeId == "Spreadsheet::Sheet"
        )
        assert human_sheet in tuple(document.VibeCADTimeline.Operations)
        human_name = human_sheet.Name
        document.undo()
        _events(8)
        assert document.getObject(human_name) is None

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-parameters-gui")

        def input_authorizer(request):
            return authorize_native_input_path(request, csv_path)

        def output_authorizer(request):
            return authorize_native_output_path(request, csv_path)

        def reauthorize() -> None:
            require_frozen_native_surface(frozen, controller)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state,
            undo_ledger=ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(controller).surface_id,
            edit_or_task_active=lambda: bool(Gui.Control.activeDialog()),
            authorize_input=input_authorizer,
            authorize_output=output_authorizer,
            background_manager=service.native_background_manager(),
            document_thread_dispatch=VibeGui._dispatch_to_document_thread,
        )
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )
        call_number = 0

        def call(name: str, arguments: dict, *, succeeds: bool = True) -> dict:
            nonlocal call_number
            call_number += 1
            result = dispatcher.call(
                name,
                json.dumps(arguments, separators=(",", ":")),
                f"native-parameters-{call_number}",
            )
            assert result.get("ok") is succeeds, result
            assert len(json.dumps(result, default=str, separators=(",", ":")).encode()) < 64 * 1024
            return result

        def wait_job(job_id: str) -> dict:
            deadline = time.monotonic() + 20.0
            while time.monotonic() < deadline:
                _events(2)
                snapshot = context.background_manager.snapshot(job_id)
                if snapshot.terminal:
                    return call(
                        NATIVE_BACKGROUND_CAPABILITY_NAME,
                        {"operation": "status", "job_id": job_id},
                    )["job"]
                time.sleep(0.01)
            raise AssertionError(f"Parameters job {job_id} did not finish")

        created = call(
            PARAMETERS_SHEET_CAPABILITY_NAME,
            {"operation": "create", "label": "Blade Parameters"},
        )
        summary = created["sheet"]
        sheet = document.getObject(summary["object_name"])
        assert sheet is not None and sheet in tuple(document.VibeCADTimeline.Operations)
        sheet_target = _sheet_target(summary)

        empty = call(
            PARAMETERS_READ_CAPABILITY_NAME,
            {"operation": "read_range", "sheet": sheet_target, "range": "A1:C3"},
        )
        selection_before = _selection()
        written = call(
            PARAMETERS_CELL_CAPABILITY_NAME,
            {
                "operation": "write_values",
                "sheet": sheet_target,
                "updates": [
                    {**_cell(empty, "A1"), "value": "10 mm"},
                    {**_cell(empty, "A2"), "value": "5 mm"},
                    {**_cell(empty, "C1"), "value": "Blade"},
                ],
            },
        )
        assert written["changed_range"] == "A1:C2"
        state_after_values = call(
            PARAMETERS_READ_CAPABILITY_NAME,
            {"operation": "read_range", "sheet": sheet_target, "range": "A1:C3"},
        )
        call(
            PARAMETERS_CELL_CAPABILITY_NAME,
            {
                "operation": "set_alias",
                "sheet": sheet_target,
                "cell": _cell(state_after_values, "A1"),
                "alias": "blade_length",
            },
        )

        document.openTransaction("Create driven fixture")
        transaction = int(document.getBookedTransactionID())
        try:
            driven = document.addObject("App::FeaturePython", "DrivenFixture")
            driven.addProperty("App::PropertyLength", "DrivenLength")
            driven.setExpression("DrivenLength", f"{sheet.Name}.blade_length")
            document.publishProvisionalTimelineOperationBlock(driven, (), ())
            document.recompute()
        except Exception:
            App.closeActiveTransaction(True, transaction)
            raise
        App.closeActiveTransaction(False, transaction)
        assert abs(float(driven.DrivenLength) - 10.0) < 1.0e-9

        before_formulas = call(
            PARAMETERS_READ_CAPABILITY_NAME,
            {"operation": "read_range", "sheet": sheet_target, "range": "A1:C3"},
        )
        stale_range = _range(before_formulas)
        formulas = call(
            PARAMETERS_CELL_CAPABILITY_NAME,
            {
                "operation": "write_formulas",
                "sheet": sheet_target,
                "updates": [
                    {**_cell(before_formulas, "A3"), "formula": "=A1+A2"},
                    {**_cell(before_formulas, "B1"), "formula": "=A1*2"},
                ],
            },
        )
        assert "formula_errors" not in formulas
        after_formulas = call(
            PARAMETERS_READ_CAPABILITY_NAME,
            {"operation": "read_range", "sheet": sheet_target, "range": "A1:C3"},
        )
        call(
            PARAMETERS_CELL_CAPABILITY_NAME,
            {
                "operation": "write_values",
                "sheet": sheet_target,
                "updates": [{**_cell(after_formulas, "A1"), "value": "25 mm"}],
            },
        )
        assert abs(float(driven.DrivenLength) - 25.0) < 1.0e-9
        stale = call(
            PARAMETERS_FORMAT_CAPABILITY_NAME,
            {
                "operation": "set_bold",
                "sheet": sheet_target,
                "target": stale_range,
                "enabled": True,
            },
            succeeds=False,
        )
        assert stale["error_code"] == "NATIVE_PARAMETERS_RANGE_STALE"

        merge_range = call(
            PARAMETERS_READ_CAPABILITY_NAME,
            {"operation": "read_range", "sheet": sheet_target, "range": "C2:C3"},
        )
        call(
            PARAMETERS_CELL_CAPABILITY_NAME,
            {
                "operation": "merge",
                "sheet": sheet_target,
                "target": _range(merge_range),
            },
        )
        merged = call(
            PARAMETERS_READ_CAPABILITY_NAME,
            {"operation": "read_range", "sheet": sheet_target, "range": "C2:C3"},
        )
        call(
            PARAMETERS_CELL_CAPABILITY_NAME,
            {"operation": "split", "sheet": sheet_target, "cell": _cell(merged, "C2")},
        )

        for operation in (
            "align_left",
            "align_center",
            "align_right",
            "align_top",
            "align_vertical_center",
            "align_bottom",
        ):
            exact = call(
                PARAMETERS_READ_CAPABILITY_NAME,
                {"operation": "read_range", "sheet": sheet_target, "range": "A1:B2"},
            )
            call(
                PARAMETERS_FORMAT_CAPABILITY_NAME,
                {"operation": operation, "sheet": sheet_target, "target": _range(exact)},
            )
        for operation in ("set_bold", "set_italic", "set_underline"):
            exact = call(
                PARAMETERS_READ_CAPABILITY_NAME,
                {"operation": "read_range", "sheet": sheet_target, "range": "A1:B2"},
            )
            call(
                PARAMETERS_FORMAT_CAPABILITY_NAME,
                {"operation": operation, "sheet": sheet_target, "target": _range(exact), "enabled": True},
            )
        exact = call(
            PARAMETERS_READ_CAPABILITY_NAME,
            {"operation": "read_range", "sheet": sheet_target, "range": "A1:B2"},
        )
        call(
            PARAMETERS_CELL_CAPABILITY_NAME,
            {
                "operation": "set_properties",
                "sheet": sheet_target,
                "target": _range(exact),
                "properties": {
                    "display_unit": "mm",
                    "foreground_rgb": [0.1, 0.2, 0.3],
                    "background_rgb": [0.9, 0.8, 0.7],
                },
            },
        )
        assert _selection() == selection_before

        export_started = call(
            PARAMETERS_EXPORT_CAPABILITY_NAME,
            {"operation": "export_csv", "sheet": sheet_target},
        )
        export_job = wait_job(export_started["job"]["job_id"])
        assert export_job["phase"] == "completed", export_job
        assert csv_path.is_file() and "=A1 + A2" in csv_path.read_text(encoding="utf-8")
        undo_before_import = int(document.UndoCount)
        import_started = call(PARAMETERS_SHEET_CAPABILITY_NAME, {"operation": "import_csv"})
        import_job = wait_job(import_started["job"]["job_id"])
        assert import_job["phase"] == "completed", import_job
        imported_name = import_job["result"]["sheet"]["object_name"]
        imported = document.getObject(imported_name)
        assert imported is not None and imported in tuple(document.VibeCADTimeline.Operations)
        assert int(document.UndoCount) == undo_before_import + 1
        document.undo()
        _events(8)
        assert document.getObject(imported_name) is None
        document.redo()
        _events(8)
        assert document.getObject(imported_name) is not None

        document.recompute()
        document.saveAs(str(save_path))
        original_name = sheet.Name
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        assert document.getObject(original_name) in tuple(document.VibeCADTimeline.Operations)
        assert document.getObject(imported_name) in tuple(document.VibeCADTimeline.Operations)
        assert abs(float(document.getObject("DrivenFixture").DrivenLength) - 25.0) < 1.0e-9

        print(
            "VIBECAD_NATIVE_PARAMETERS_GUI_OK "
            "surface=true human_shared_create=true exact_read=true values=true formulas=true "
            "dependency=true aliases=true merge_split=true properties=true all_formats=true "
            "stale_refusal=true selection=true background_import=true background_export=true "
            "path_private=true history=true undo=true redo=true reopen=true low_noise=true",
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
