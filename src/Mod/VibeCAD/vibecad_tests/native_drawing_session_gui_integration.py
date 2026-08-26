# SPDX-License-Identifier: LGPL-2.1-or-later

"""Production-session gate for a human-selected Native Drawing ribbon."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import traceback
from types import SimpleNamespace

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
import VibeCADSession as Session
from VibeCADCore import get_service
from VibeCADNativeProviderRunner import NativeProviderToolRunner
from VibeCADNativeSessionFactory import create_native_session_execution
from VibeCADRibbonSurface import read_active_ribbon_surface


def _events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _activate(workbench: str, surface_id: str):
    Gui.activateWorkbench(workbench)
    _events(24)
    controller = Gui.getMainWindow().findChild(
        QtCore.QObject,
        "VibeCADRibbonController",
    )
    assert controller is not None
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == surface_id, (surface_id, surface.surface_id)
    return controller


def _capture_execution(service, controller):
    context = Session._context_for_provider(service)
    execution = create_native_session_execution(
        service=service,
        expected_surface=dict(context["provider_tool_surface"]),
        expected_schemas=list(context["provider_tool_schemas"]),
        expected_authorization=dict(context["_native_turn_authorization"]),
        controller=controller,
        document_thread_dispatch=VibeGui._dispatch_to_document_thread,
    )
    return context, execution


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = tempfile.TemporaryDirectory(
        prefix="vibecad-native-drawing-session-"
    )
    exit_code = 1
    try:
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        Gui.getMainWindow().show()
        controller = _activate("TechDrawWorkbench", "drawing")

        path = Path(temporary.name) / "drawing-session.FCStd"
        document = App.newDocument("NativeDrawingSessionGate")
        shape = document.addObject("Part::Feature", "Source")
        shape.Shape = Part.makeBox(40.0, 30.0, 10.0)
        document.recompute()
        document.saveAs(str(path))
        name = document.Name
        App.closeDocument(name)
        document = App.openDocument(str(path))
        _events(16)

        service = get_service()
        service.select_modeling_engine("native")

        drawing_context, execution = _capture_execution(service, controller)
        assert drawing_context["modeling_surface"]["domain"] == "drawing"
        assert execution.turn.surface.surface_id == "drawing"
        traces = []
        runner = NativeProviderToolRunner(
            execution=execution,
            document_dispatch=lambda operation: operation(),
            refresh_context=lambda: Session._context_for_provider(service),
            frozen_surface=dict(drawing_context["provider_tool_surface"]),
            frozen_schemas=list(drawing_context["provider_tool_schemas"]),
            frozen_modeling_surface=dict(drawing_context["modeling_surface"]),
            tool_trace=traces,
        )
        created = runner(
            "drawing.create_page",
            '{"operation":"page_default"}',
            "create-page",
        )
        assert created["ok"] is True, created

        controller = _activate("PartDesignWorkbench", "model")
        stale = runner(
            "state.read",
            '{"operation":"active"}',
            "stale-drawing-read",
        )
        assert stale["ok"] is False
        assert stale["error_code"] == "NATIVE_SURFACE_CHANGED"
        assert stale["next_turn_required"] is True
        assert stale["next_surface"] == "model"
        continuation = VibeGui._native_surface_continuation_event(
            SimpleNamespace(error=None, tool_trace=traces)
        )
        assert continuation is not None
        assert continuation["surface_id"] == "model"
        runner.close()

        model_context, execution = _capture_execution(service, controller)
        assert model_context["modeling_surface"]["domain"] == "model"
        assert execution.turn.surface.surface_id == "model"
        execution.close()

        controller = _activate("TechDrawWorkbench", "drawing")
        next_drawing_context, execution = _capture_execution(service, controller)
        assert next_drawing_context["modeling_surface"]["domain"] == "drawing"
        assert execution.turn.surface.surface_id == "drawing"
        assert (
            next_drawing_context["provider_tool_surface"]["schema_sha256"]
            == drawing_context["provider_tool_surface"]["schema_sha256"]
        )
        execution.close()

        print(
            "VIBECAD_NATIVE_DRAWING_SESSION_GUI_OK "
            "open=true create-page=true dispatch=true stale-rejected=true "
            "automatic-transition=true drawing-model-drawing=true fresh-contract=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
