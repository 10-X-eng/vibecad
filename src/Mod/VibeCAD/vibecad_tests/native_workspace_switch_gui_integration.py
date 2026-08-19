# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI gate for one provider-controlled Native ribbon transition."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import traceback
from types import SimpleNamespace

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeCapabilityRegistry import resolve_native_provider_surface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeProviderRunner import NativeProviderToolRunner
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSessionFactory import NativeSessionExecution
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


def _process_events(rounds: int = 16) -> None:
    for _ in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    runner = None
    temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-workspace-")
    exit_code = 1
    try:
        VibeGui._ensure_document_thread_invoker()
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeWorkspaceSwitchGate")
        document.UndoMode = 1
        document.saveAs(str(Path(temporary.name) / "workspace-switch.FCStd"))
        _process_events(24)

        main_window = Gui.getMainWindow()
        controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
        assert controller is not None
        live_surface = read_active_ribbon_surface(controller)
        assert live_surface.surface_id == "model"

        registry = build_native_capability_registry()
        provider = resolve_native_provider_surface(live_surface, registry)
        assert provider.available is True, provider.debug_summary()
        assert "workspace.switch" in provider.tool_names
        frozen_surface = NativeSurfaceSnapshot.from_surface(live_surface)
        turn = NativeTurnSnapshot.from_provider_surface(provider)

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        run_id = "native-workspace-switch-gui"
        ledger.begin_run(run_id)

        def reauthorize() -> None:
            require_frozen_native_surface(frozen_surface, controller)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state,
            undo_ledger=ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(controller).surface_id,
            edit_or_task_active=lambda: False,
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
        runner = NativeProviderToolRunner(
            execution=NativeSessionExecution(dispatcher, turn, ledger, run_id),
            document_dispatch=lambda operation: operation(),
            refresh_context=lambda: {
                "provider_tool_surface": {
                    "engine": "native",
                    "domain": read_active_ribbon_surface(controller).surface_id,
                    "surface_id": NativeSurfaceSnapshot.from_surface(
                        read_active_ribbon_surface(controller)
                    ).modeling_surface_id,
                    "schema_sha256": "live",
                    "workbench": Gui.activeWorkbench().name(),
                }
            },
            frozen_surface={
                "engine": "native",
                "domain": "model",
                "surface_id": frozen_surface.modeling_surface_id,
                "schema_sha256": turn.schema_sha256,
                "workbench": "PartDesignWorkbench",
            },
            frozen_schemas=list(provider.schemas),
            frozen_modeling_surface={
                "engine": "native",
                "domain": "model",
                "surface_id": frozen_surface.modeling_surface_id,
            },
            tool_trace=[],
        )

        response = runner(
            "workspace.switch",
            json.dumps({"operation": "switch", "workspace": "assembly"}),
            "workspace-model-to-assemble",
        )
        assert response == {
            "ok": True,
            "workspace": "assembly",
            "next_turn_required": True,
        }, response
        _process_events(24)
        assert Gui.activeWorkbench().name() == "AssemblyWorkbench"
        assert read_active_ribbon_surface(controller).surface_id == "assemble"
        continuation = VibeGui._native_surface_continuation_event(
            SimpleNamespace(
                error=None,
                tool_trace=[
                    {
                        "tool_name": "workspace.switch",
                        "result": response,
                    }
                ],
            )
        )
        assert continuation == {
            "type": "cad_workspace_changed",
            "document_uid": str(document.Uid),
            "document_name": document.Name,
            "surface_id": "assemble",
            "workspace": "assembly",
        }

        refused = runner(
            "state.read",
            '{"operation":"active"}',
            "state-after-switch",
        )
        assert refused["error_code"] == "NATIVE_SURFACE_CHANGED", refused
        update = runner.provider_update()
        assert update["modeling_surface"]["invalidated"] is True
        assert update["modeling_surface"]["next_turn_required"] is True

        next_provider = resolve_native_provider_surface(
            read_active_ribbon_surface(controller),
            registry,
        )
        assert next_provider.available is True, next_provider.debug_summary()
        assert "assembly.structure" in next_provider.tool_names
        assert "model.feature" not in next_provider.tool_names
        assert "workspace.switch" in next_provider.tool_names
        print(
            "VIBECAD_NATIVE_WORKSPACE_SWITCH_GUI_OK "
            "model-to-assemble old-turn-invalid next-tools-fresh continuation-exact",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=__import__("sys").__stderr__)
    finally:
        if runner is not None:
            runner.close()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
