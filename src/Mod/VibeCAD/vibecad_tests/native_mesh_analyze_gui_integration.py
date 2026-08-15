# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for every Native Mesh Analyze ribbon action."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Mesh
import MeshGui
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeBackgroundSchema import NATIVE_BACKGROUND_CAPABILITY_NAME
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeMeshCurvatureSchema import MESH_CURVATURE_CAPABILITY_NAME
from VibeCADNativeMeshInspectSchema import MESH_INSPECT_CAPABILITY_NAME
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface
from native_mesh_modify_gui_support import add_source


ANALYZE_ACTIONS = frozenset(
    {
        "Mesh_Evaluation",
        "Mesh_EvaluateFacet",
        "Mesh_VertexCurvature",
        "Mesh_CurvatureInfo",
        "Mesh_EvaluateSolid",
        "Mesh_BoundingBox",
    }
)
INSPECT_OPERATIONS = (
    "evaluation",
    "evaluate_facet",
    "curvature_info",
    "evaluate_solid",
    "bounding_box",
)


def _process_events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _select_mesh_ribbon(main_window):
    controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
    tabs = main_window.findChild(QtWidgets.QTabBar, "VibeCADRibbonTabs")
    assert controller is not None and tabs is not None
    index = next(
        candidate
        for candidate in range(tabs.count())
        if str(tabs.tabData(candidate)) == "MeshWorkbench"
    )
    tabs.setCurrentIndex(index)
    _process_events(24)
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "mesh"
    assert ANALYZE_ACTIONS <= set(surface.command_ids)
    return controller, surface


def _turn(surface, registry) -> NativeTurnSnapshot:
    jobs = registry.definition(NATIVE_BACKGROUND_CAPABILITY_NAME)
    inspect = registry.definition(MESH_INSPECT_CAPABILITY_NAME)
    curvature = registry.definition(MESH_CURVATURE_CAPABILITY_NAME)
    assert jobs is not None and inspect is not None and curvature is not None
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(
                NATIVE_BACKGROUND_CAPABILITY_NAME,
                MESH_INSPECT_CAPABILITY_NAME,
                MESH_CURVATURE_CAPABILITY_NAME,
            ),
            schemas=(
                jobs.provider_schema(("status", "cancel")),
                inspect.provider_schema(INSPECT_OPERATIONS),
                curvature.provider_schema(("vertex_curvature",)),
            ),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _exact(obj) -> dict:
    return {
        "object_name": obj.Name,
        "expected_state_sha256": mesh_object_state(obj)["state_sha256"],
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("MeshWorkbench")
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-mesh-analyze-")
        save_path = Path(temporary.name) / "native-mesh-analyze.FCStd"
        document = App.newDocument("NativeMeshAnalyzeGate")
        document.UndoMode = 1
        document.saveAs(str(save_path))
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        controller, surface = _select_mesh_ribbon(Gui.getMainWindow())
        frozen = NativeSurfaceSnapshot.from_surface(surface)
        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-mesh-analyze-gui")

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
            response = dispatcher.call(
                name,
                json.dumps(arguments, separators=(",", ":")),
                f"native-mesh-analyze-{call_number}",
            )
            assert response.get("ok") is succeeds, response
            return response

        source = add_source(document, "AnalyzeSource", Mesh.createSphere(8.0, 36))
        _process_events(8)
        uid = str(document.Uid)
        read_revision = state.current_revision(uid)
        read_undo_count = int(document.UndoCount)

        stale = call(
            MESH_INSPECT_CAPABILITY_NAME,
            {
                "operation": "evaluate_solid",
                "target": {
                    "object_name": source.Name,
                    "expected_state_sha256": "0" * 64,
                },
            },
            succeeds=False,
        )
        assert stale["error_code"] == "NATIVE_MESH_STATE_STALE"

        exact = _exact(source)
        facets = call(
            MESH_INSPECT_CAPABILITY_NAME,
            {
                "operation": "evaluate_facet",
                "target": exact,
                "facet_indices": [0, 1],
            },
        )
        assert [item["facet_index"] for item in facets["facets"]] == [0, 1]
        assert all(len(item["vertices_mm"]) == 3 for item in facets["facets"])
        assert all(item["area_mm2"] > 0.0 for item in facets["facets"])

        solid = call(
            MESH_INSPECT_CAPABILITY_NAME,
            {"operation": "evaluate_solid", "target": exact},
        )
        assert solid["solid"] is True
        assert solid["watertight"] is True and solid["open_edge_count"] == 0
        bounds = call(
            MESH_INSPECT_CAPABILITY_NAME,
            {"operation": "bounding_box", "target": exact},
        )
        assert all(value > 15.0 for value in bounds["bounds"]["size_mm"])
        _process_events(8)
        assert state.current_revision(uid) == read_revision
        assert int(document.UndoCount) == read_undo_count

        ui_dispatched = {"value": False}
        QtCore.QTimer.singleShot(0, lambda: ui_dispatched.__setitem__("value", True))
        started = call(
            MESH_INSPECT_CAPABILITY_NAME,
            {
                "operation": "evaluation",
                "target": exact,
                "degeneration_mode": "mesh_tolerance",
            },
        )
        _process_events(2)
        assert ui_dispatched["value"] is True
        job_id = started["job"]["job_id"]
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            _process_events(2)
            snapshot = context.background_manager.snapshot(job_id)
            if snapshot.terminal:
                break
            time.sleep(0.01)
        evaluation = call(
            NATIVE_BACKGROUND_CAPABILITY_NAME,
            {"operation": "status", "job_id": job_id},
        )["job"]
        assert evaluation["phase"] == "completed", evaluation
        report = evaluation["result"]
        assert report["target"]["object_name"] == source.Name
        assert report["topology"]["facets"] == source.Mesh.CountFacets
        assert set(report["issue_counts"]) == {
            "boundary_folds",
            "corrupted_facets",
            "degenerated_facets",
            "duplicated_facets",
            "duplicated_points",
            "facet_indices_out_of_range",
            "invalid_neighbourhood",
            "nan_points",
            "non_manifold_edges",
            "non_manifold_points",
            "non_uniform_orientation",
            "point_indices_out_of_range",
            "self_intersections",
            "surface_fold_overs",
            "surface_folds",
        }
        assert state.current_revision(uid) == read_revision
        assert int(document.UndoCount) == read_undo_count

        curvature = call(
            MESH_CURVATURE_CAPABILITY_NAME,
            {
                "operation": "vertex_curvature",
                "targets": [{**exact, "label": "Analyzed Vertex Curvature"}],
            },
        )
        assert int(document.UndoCount) == read_undo_count + 1
        assert curvature["receipt"]["revision_after"] == read_revision + 1
        curvature_name = curvature["results"][0]["object_name"]
        curvature_object = document.getObject(curvature_name)
        assert curvature_object is not None
        assert curvature_object.TypeId == "Mesh::Curvature"
        assert curvature_object.Source is source
        assert int(curvature_object.SampleCount) == int(source.Mesh.CountPoints)
        assert MeshGui.isNativeMeshInputActive(source)
        assert MeshGui.isNativeMeshInputActive(curvature_object)

        curvature_revision = state.current_revision(uid)
        curvature_state = mesh_object_state(curvature_object)
        curvature_info = call(
            MESH_INSPECT_CAPABILITY_NAME,
            {
                "operation": "curvature_info",
                "curvature": {
                    "object_name": curvature_name,
                    "expected_state_sha256": curvature_state["state_sha256"],
                },
                "vertex_indices": [0, 1],
            },
        )
        assert [item["vertex_index"] for item in curvature_info["samples"]] == [0, 1]
        assert all(len(item["point_mm"]) == 3 for item in curvature_info["samples"])
        assert state.current_revision(uid) == curvature_revision
        assert int(document.UndoCount) == read_undo_count + 1

        history_names = tuple(obj.Name for obj in document.VibeCADTimeline.Operations)
        assert history_names[-1] == curvature_name
        source_name = source.Name
        document.undo()
        assert document.getObject(curvature_name) is None
        document.redo()
        curvature_object = document.getObject(curvature_name)
        assert curvature_object is not None and curvature_object.Source is source
        assert int(curvature_object.SampleCount) == int(source.Mesh.CountPoints)

        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        App.setActiveDocument(document.Name)
        _process_events(12)
        reopened = document.getObject(curvature_name)
        reopened_source = document.getObject(source_name)
        assert reopened is not None and reopened_source is not None
        assert reopened.Source is reopened_source
        assert int(reopened.SampleCount) == int(reopened_source.Mesh.CountPoints)
        assert tuple(obj.Name for obj in document.VibeCADTimeline.Operations) == history_names
        assert reopened.isValid(), reopened.getStatusString()

        print(
            "VIBECAD_NATIVE_MESH_ANALYZE_GUI_OK actions=6 background=true "
            "reads_stable=true curvature_retained=true undo_redo=true reopen=true",
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
