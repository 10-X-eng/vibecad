# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact Drawing export and Print All."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeCapabilityRegistry import resolve_native_provider_surface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeDrawingExport import validate_drawing_output
from VibeCADNativeDrawingExportSchema import (
    DRAWING_EXPORT_CAPABILITY_NAME,
    DRAWING_EXPORT_OPERATIONS,
)
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeOutput import authorize_native_output_path
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


def _events(rounds: int = 12) -> None:
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


def _fixture(document):
    document.openTransaction("Create Drawing output fixture")
    transaction = int(document.getBookedTransactionID())
    try:
        source = document.addObject("Part::Feature", "OutputSource")
        source.Shape = Part.makeBox(42.0, 30.0, 10.0)
        document.publishProvisionalTimelineOperationBlock(source, (), ())
        page = document.addObject("TechDraw::DrawPage", "OutputPage")
        page.Label = "Output Page"
        template = document.addObject("TechDraw::DrawSVGTemplate", "OutputTemplate")
        template.Template = str(
            Path(App.getResourceDir())
            / "Mod" / "TechDraw" / "Templates" / "ISO" / "A4_Landscape_TD.svg"
        )
        page.Template = template
        document.publishProvisionalTimelineOperationBlock(page, (template,), ())
        view = document.addObject("TechDraw::DrawViewPart", "OutputView")
        view.Source = [source]
        view.Direction = App.Vector(0.0, 0.0, 1.0)
        view.XDirection = App.Vector(1.0, 0.0, 0.0)
        view.X = 105.0
        view.Y = 70.0
        assert int(page.addView(view)) >= 1
        document.publishProvisionalTimelineOperationBlock(view, (), ())
        assert document.recompute([source, view, page], True, True) is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    page.ViewObject.show()
    _events(24)
    return page


def _turn(surface, registry) -> NativeTurnSnapshot:
    provider = resolve_native_provider_surface(surface, registry)
    assert provider.available is True, provider.summary()
    schemas = {
        schema["name"]: schema
        for schema in provider.schemas
    }
    schema = schemas[DRAWING_EXPORT_CAPABILITY_NAME]
    advertised_operations = tuple(
        schema["parameters"]["properties"]["operation"]["enum"]
    )
    assert len(advertised_operations) == len(DRAWING_EXPORT_OPERATIONS)
    assert set(advertised_operations) == set(DRAWING_EXPORT_OPERATIONS)
    assert schema["parameters"]["additionalProperties"] is False
    assert "unknown" not in json.dumps(schema).casefold()
    return NativeTurnSnapshot.from_provider_surface(provider)


def _page_target(page) -> dict[str, str]:
    state = drawing_page_state(page)
    return {
        "object_name": page.Name,
        "expected_state_sha256": state["state_sha256"],
    }


def _wait(manager, job_id: str, timeout: float = 20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _events(2)
        snapshot = manager.snapshot(job_id)
        if snapshot.terminal:
            return snapshot
    raise AssertionError(f"Drawing output job {job_id} did not finish")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    print_timer = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-drawing-output-")
        root = Path(temporary.name)
        outputs = {
            suffix: root / f"drawing-output.{suffix}"
            for suffix in ("svg", "dxf", "pdf")
        }
        controller, surface = _surface()
        document = App.newDocument("NativeDrawingOutputGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        page = _fixture(document)

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        manager = service.native_background_manager()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-drawing-output-gui")

        def reauthorize() -> None:
            require_frozen_native_surface(turn.surface, controller)

        authorized = []

        def authorize(request):
            suffix = request.allowed_suffixes[0].lstrip(".")
            path = outputs[suffix]
            authorized.append((request.purpose, path.name))
            return authorize_native_output_path(request, path)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state_store,
            undo_ledger=ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(controller).surface_id,
            edit_or_task_active=lambda: bool(Gui.Control.activeDialog()),
            authorize_output=authorize,
            background_manager=manager,
            document_thread_dispatch=VibeGui._dispatch_to_document_thread,
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

        def call(arguments: dict, succeeds: bool = True) -> dict:
            nonlocal call_index
            call_index += 1
            response = dispatcher.call(
                DRAWING_EXPORT_CAPABILITY_NAME,
                json.dumps(arguments, separators=(",", ":")),
                f"native-drawing-output-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        revision_before = state_store.current_revision(str(document.Uid))
        undo_before = int(document.UndoCount)
        page_state_before = drawing_page_state(page)
        stale = _page_target(page)
        stale["expected_state_sha256"] = "0" * 64
        rejected = call({"operation": "svg", "page": stale}, False)
        assert rejected["error_code"] == "NATIVE_DRAWING_PAGE_STALE"
        assert authorized == []

        artifacts = {}
        for format_name in ("svg", "dxf", "pdf"):
            queued = call(
                {"operation": format_name, "page": _page_target(page)}
            )
            result = _wait(manager, queued["job"]["job_id"])
            assert result.phase == "completed", result.error
            artifact = result.result["output"]
            output = outputs[format_name]
            assert artifact["file_name"] == output.name
            assert artifact["sha256"] == _sha256(output)
            validate_drawing_output(output, format_name)
            artifacts[format_name] = artifact

        print_dialog_seen = {"value": False}

        def cancel_print_dialog() -> None:
            for widget in QtWidgets.QApplication.topLevelWidgets():
                if (
                    not isinstance(widget, QtWidgets.QDialog)
                    or widget.metaObject().className() != "QPrintDialog"
                ):
                    continue
                print_dialog_seen["value"] = True
                widget.reject()

        print_timer = QtCore.QTimer()
        print_timer.timeout.connect(cancel_print_dialog)
        print_timer.start(25)
        queued = call({"operation": "print_all"})
        printed = _wait(manager, queued["job"]["job_id"])
        print_timer.stop()
        assert printed.phase == "completed", printed.error
        assert printed.result["print"] == {
            "authorized": False,
            "submitted": False,
            "output_mode": "none",
            "page_count": 0,
        }
        assert print_dialog_seen["value"] is True

        assert len(authorized) == 3
        assert int(document.UndoCount) == undo_before
        assert state_store.current_revision(str(document.Uid)) == revision_before
        assert document.getBookedTransactionID() == 0
        assert drawing_page_state(page) == page_state_before
        print(
            "VIBECAD_NATIVE_DRAWING_OUTPUT_GUI_OK operations=4 "
            "svg=true dxf=true pdf=true print_all_dialog=true cancellation=true "
            "background=true atomic_publication=true bounded_validation=true "
            "paths_hidden=true exact_target=true stale_refusal=true "
            "revision_stable=true undo_stable=true low_noise=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if print_timer is not None:
            print_timer.stop()
        Gui.Selection.clearSelection()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
