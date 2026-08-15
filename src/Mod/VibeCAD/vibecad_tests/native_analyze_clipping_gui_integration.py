# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI gate for exact FEM clipping-plane presentation."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeAnalyzeClipping import (
    clipping_face_source_state,
    clipping_state,
)
from VibeCADNativeAnalyzePresentationSchema import (
    ANALYZE_PRESENTATION_CAPABILITY_NAME,
)
from VibeCADNativeAnalyzeSnapshot import build_analyze_snapshot
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


def _events(rounds: int = 8) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _surface(main_window):
    controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
    tabs = main_window.findChild(QtWidgets.QTabBar, "VibeCADRibbonTabs")
    assert controller is not None and tabs is not None
    index = next(
        candidate
        for candidate in range(tabs.count())
        if str(tabs.tabData(candidate)) == "FemWorkbench"
    )
    tabs.setCurrentIndex(index)
    _events(24)
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "analyze"
    assert {"FEM_ClippingPlaneAdd", "FEM_ClippingPlaneRemoveAll"} <= set(
        surface.command_ids
    )
    return controller, surface


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(ANALYZE_PRESENTATION_CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(
        ("add_clipping_plane", "remove_all_clipping_planes")
    )
    parameters = schema["parameters"]
    assert set(parameters["properties"]["operation"]["enum"]) == {
        "add_clipping_plane",
        "remove_all_clipping_planes",
    }
    assert set(parameters["required"]) == {
        "operation",
        "expected_clipping_state_sha256",
        "expected_clipping_plane_count",
    }
    assert set(parameters["properties"]) == {
        "operation",
        "face",
        "reverse",
        "expected_clipping_state_sha256",
        "expected_clipping_plane_count",
    }
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(ANALYZE_PRESENTATION_CAPABILITY_NAME,),
            schemas=(schema,),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _clipping_arguments(state: dict) -> dict:
    return {
        "expected_clipping_state_sha256": state["state_sha256"],
        "expected_clipping_plane_count": state["plane_count"],
    }


def _face_arguments(obj, face_index: int) -> dict:
    state = clipping_face_source_state(obj)
    return {
        "object_name": state["object_name"],
        "face_index": face_index,
        "expected_state_sha256": state["state_sha256"],
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("FemWorkbench")
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-analyze-clipping-"
        )
        output = Path(temporary.name) / "native-analyze-clipping.FCStd"
        document = App.newDocument("NativeAnalyzeClippingGate")
        document.UndoMode = 1
        shape = document.addObject("Part::Feature", "ClippingTarget")
        shape.Label = "Clipping Target"
        shape.Shape = Part.makeBox(20.0, 14.0, 8.0)
        document.recompute()
        document.saveAs(str(output))
        VibeGui._connect_document_observer()
        controller, surface = _surface(Gui.getMainWindow())
        Gui.activeDocument().activeView().viewAxonometric()
        Gui.activeDocument().activeView().fitAll()
        _events(12)

        frozen = NativeSurfaceSnapshot.from_surface(surface)
        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-analyze-clipping-gui")

        def authorize() -> None:
            require_frozen_native_surface(frozen, controller)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state_store,
            undo_ledger=ledger,
            reauthorize_turn=authorize,
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
            reauthorize_turn=authorize,
            active_document=lambda: App.ActiveDocument,
        )
        call_number = 0

        def call(arguments: dict, *, succeeds: bool = True) -> dict:
            nonlocal call_number
            call_number += 1
            response = dispatcher.call(
                ANALYZE_PRESENTATION_CAPABILITY_NAME,
                json.dumps(arguments, separators=(",", ":")),
                f"native-analyze-clipping-{call_number}",
            )
            assert response.get("ok") is succeeds, response
            assert len(json.dumps(response, separators=(",", ":")).encode("utf-8")) < 8192
            return response

        snapshot = build_analyze_snapshot(document)
        assert snapshot["clipping"]["plane_count"] == 0
        source = next(
            item
            for item in snapshot["geometry_sources"]
            if item["object_name"] == shape.Name
        )
        face_target = source["clipping_face_target"]
        assert face_target["face_count"] == 6
        before = clipping_state(document)
        revision_before = state_store.current_revision(str(document.Uid))
        undo_before = int(document.UndoCount)
        camera_before = Gui.activeDocument().activeView().getCameraOrientation()

        added = call(
            {
                "operation": "add_clipping_plane",
                "face": {
                    "object_name": face_target["object_name"],
                    "face_index": 3,
                    "expected_state_sha256": face_target["state_sha256"],
                },
                "reverse": False,
                **_clipping_arguments(before),
            }
        )
        assert added["changed"] is True
        assert added["added"]["source"] == f"{shape.Name}.Face3"
        assert added["clipping"]["plane_count"] == 1
        first_state = added["clipping"]

        stale = call(
            {
                "operation": "add_clipping_plane",
                "face": _face_arguments(shape, 4),
                "reverse": False,
                **_clipping_arguments(before),
            },
            succeeds=False,
        )
        assert stale["error_code"] == "NATIVE_ANALYZE_STATE_STALE", stale
        assert clipping_state(document)["plane_count"] == 1

        second = call(
            {
                "operation": "add_clipping_plane",
                "face": _face_arguments(shape, 4),
                "reverse": True,
                **_clipping_arguments(first_state),
            }
        )
        assert second["clipping"]["plane_count"] == 2
        assert second["added"]["reversed"] is True
        assert state_store.current_revision(str(document.Uid)) == revision_before
        assert int(document.UndoCount) == undo_before
        assert ledger.available(document, state_store) == {"available": False}
        assert Gui.activeDocument().activeView().getCameraOrientation() == camera_before

        removed = call(
            {
                "operation": "remove_all_clipping_planes",
                **_clipping_arguments(second["clipping"]),
            }
        )
        assert removed["changed"] is True
        assert removed["removed_plane_count"] == 2
        assert removed["clipping"]["plane_count"] == 0
        no_op = call(
            {
                "operation": "remove_all_clipping_planes",
                **_clipping_arguments(removed["clipping"]),
            }
        )
        assert no_op["changed"] is False
        assert no_op["removed_plane_count"] == 0
        assert state_store.current_revision(str(document.Uid)) == revision_before
        assert int(document.UndoCount) == undo_before
        assert document.getBookedTransactionID() == 0
        assert not document.HasPendingTransaction

        shape_name = shape.Name
        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(output))
        _events(20)
        assert document.getObject(shape_name) is not None
        assert clipping_state(document)["plane_count"] == 0
        print(
            "VIBECAD_NATIVE_ANALYZE_CLIPPING_GUI_OK "
            "actions=2 exact_face=true exact_graph=true reverse=true "
            "stale_rejection=true no_op=true no_transaction=true "
            "revision_stable=true camera_stable=true reopen=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
