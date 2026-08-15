# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI gate for the Native leave-Sketch surface boundary."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import SketcherGui
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADEditState import active_edit_object
from VibeCADNativeCapabilityRegistry import resolve_native_provider_surface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeProviderRunner import NativeProviderToolRunner
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSessionFactory import NativeSessionExecution
from VibeCADNativeSketchControlBindings import SKETCH_CONTROL_CAPABILITY_NAME
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface
from vibecad_tests.native_sketch_geometry_gui_support import process_events


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-sketch-leave-")
    runner = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        workbench_name = Gui.activeWorkbench().name()
        document = App.newDocument("NativeSketchLeaveGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        sketch = document.addObject("Sketcher::SketchObject", "LeaveSketch")
        sketch.Label = "Native leave boundary"
        document.recompute()
        save_path = Path(temporary.name) / "NativeSketchLeave.FCStd"
        document.saveAs(str(save_path))
        process_events(16)

        main_window = Gui.getMainWindow()
        controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
        assert controller is not None
        assert Gui.activeDocument().setEdit(sketch.Name)
        process_events(24)
        live_surface = read_active_ribbon_surface(controller)
        assert live_surface.surface_id == "sketch.edit"
        assert active_edit_object() is sketch

        registry = build_native_capability_registry()
        provider = resolve_native_provider_surface(live_surface, registry)
        assert provider.available is True, provider.debug_summary()
        assert SKETCH_CONTROL_CAPABILITY_NAME in provider.tool_names
        assert "document.save" not in provider.tool_names
        assert "document.open" not in provider.tool_names
        assert "Sketcher_CancelSketch" in provider.human_only_action_ids
        assert "Sketcher_LeaveSketch" not in provider.human_only_action_ids
        frozen_surface = NativeSurfaceSnapshot.from_surface(live_surface)
        turn = NativeTurnSnapshot.from_provider_surface(provider)

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        run_id = "native-sketch-leave-gui"
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
            edit_or_task_active=lambda: active_edit_object() is not None,
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
        frozen_provider_surface = {
            "engine": "native",
            "domain": "sketch.edit",
            "surface_id": "native-sketch-leave-frozen",
            "schema_sha256": turn.schema_sha256,
            "workbench": workbench_name,
        }

        def refreshed_context() -> dict:
            current = read_active_ribbon_surface(controller)
            return {
                "provider_tool_surface": {
                    "engine": "native",
                    "domain": current.surface_id,
                    "surface_id": f"native-{current.surface_id}-live",
                    "schema_sha256": "changed",
                    "workbench": Gui.activeWorkbench().name(),
                },
                "native_state": state.snapshot(str(document.Uid)),
            }

        runner = NativeProviderToolRunner(
            execution=NativeSessionExecution(dispatcher, turn, ledger, run_id),
            document_dispatch=lambda operation: operation(),
            refresh_context=refreshed_context,
            frozen_surface=frozen_provider_surface,
            frozen_schemas=list(provider.schemas),
            frozen_modeling_surface={
                "engine": "native",
                "domain": "sketch.edit",
                "surface_id": "native-sketch-leave-frozen",
            },
            tool_trace=[],
        )
        for tool_name, arguments, call_id in (
            ("document.save", '{"operation":"existing_path"}', "save-in-sketch"),
            ("document.open", "{}", "open-in-sketch"),
        ):
            unavailable = runner(tool_name, arguments, call_id)
            assert unavailable.get("ok") is False, unavailable
            assert unavailable["error_code"] == "NATIVE_TOOL_UNAVAILABLE"
            assert active_edit_object() is sketch
        try:
            SketcherGui.leaveActiveSketch(
                document.Name,
                "wrong-document-uid",
                sketch.Name,
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError("A wrong document UID left the active Sketch.")
        assert active_edit_object() is sketch
        stale = runner(
            SKETCH_CONTROL_CAPABILITY_NAME,
            json.dumps(
                {
                    "operation": "leave",
                    "sketch": {"object_name": sketch.Name},
                    "expected_geometry_count": int(sketch.GeometryCount) + 1,
                    "expected_constraint_count": int(sketch.ConstraintCount),
                },
                separators=(",", ":"),
            ),
            "native-sketch-leave-stale",
        )
        assert stale.get("ok") is False, stale
        assert stale["error_code"] == "NATIVE_SKETCH_INVALID"
        assert active_edit_object() is sketch
        response = runner(
            SKETCH_CONTROL_CAPABILITY_NAME,
            json.dumps(
                {
                    "operation": "leave",
                    "sketch": {"object_name": sketch.Name},
                    "expected_geometry_count": int(sketch.GeometryCount),
                    "expected_constraint_count": int(sketch.ConstraintCount),
                },
                separators=(",", ":"),
            ),
            "native-sketch-leave",
        )
        assert response.get("ok") is True, response
        assert response["operation"] == "leave"
        assert response["sketch"]["object_name"] == sketch.Name
        assert response["edit_mode"] == "closed"
        assert response["next_turn_required"] is True
        assert response["next_surface"] != "sketch.edit"
        assert active_edit_object() is None
        assert Gui.activeDocument().getInEdit() is None
        assert document.getObject(sketch.Name) is sketch
        assert Gui.activeWorkbench().name() == workbench_name

        update = runner.provider_update()
        assert "native_state" not in update
        assert update["modeling_surface"]["invalidated"] is True
        assert update["modeling_surface"]["next_turn_required"] is True
        refused = runner(
            "state.read",
            '{"operation":"active"}',
            "native-sketch-after-leave",
        )
        assert refused.get("ok") is False, refused
        assert refused["error_code"] == "NATIVE_SURFACE_CHANGED", refused
        assert refused["current_surface"] == response["next_surface"]
        assert refused["repair"] == {"resume_next_turn": True}

        next_surface = read_active_ribbon_surface(controller)
        assert next_surface.surface_id == response["next_surface"]
        next_provider = resolve_native_provider_surface(next_surface, registry)
        assert next_provider.available is True, next_provider.debug_summary()
        assert "document.save" in next_provider.tool_names
        assert "document.open" not in next_provider.tool_names

        print(
            "VIBECAD_NATIVE_SKETCH_LEAVE_GUI_OK "
            "save-hidden leave-exact turn-invalidated save-restored",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=__import__("sys").__stderr__)
    finally:
        if runner is not None:
            runner.close()
        elif "ledger" in locals():
            ledger.end_run(run_id)
        if Gui.activeDocument() and Gui.activeDocument().getInEdit():
            Gui.activeDocument().resetEdit()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
