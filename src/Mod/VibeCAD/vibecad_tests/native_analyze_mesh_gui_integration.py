# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for Native FEM mesh-definition tools."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeAnalyzeInspectSchema import ANALYZE_INSPECT_CAPABILITY_NAME
from VibeCADNativeAnalyzeMeshSchema import ANALYZE_MESH_CAPABILITY_NAME
from VibeCADNativeAnalyzeMeshState import fem_mesh_definition_state
from VibeCADNativeAnalyzeModelSchema import ANALYZE_MODEL_CAPABILITY_NAME
from VibeCADNativeAnalyzeSnapshot import build_analyze_snapshot
from VibeCADNativeAnalyzeState import analysis_state
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeContextManifest import provider_context_actions_for_surface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


OPERATIONS = ("create_gmsh", "create_netgen", "update_gmsh", "update_netgen")


def _process_events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _select_analyze_ribbon(main_window):
    controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
    tabs = main_window.findChild(QtWidgets.QTabBar, "VibeCADRibbonTabs")
    assert controller is not None and tabs is not None
    index = next(
        candidate
        for candidate in range(tabs.count())
        if str(tabs.tabData(candidate)) == "FemWorkbench"
    )
    tabs.setCurrentIndex(index)
    _process_events(24)
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "analyze"
    assert {"FEM_MeshGmshFromShape", "FEM_MeshNetgenFromShape"} <= set(
        surface.command_ids
    )
    return controller, surface


def _turn(surface, registry) -> NativeTurnSnapshot:
    model = registry.definition(ANALYZE_MODEL_CAPABILITY_NAME)
    mesh = registry.definition(ANALYZE_MESH_CAPABILITY_NAME)
    inspect = registry.definition(ANALYZE_INSPECT_CAPABILITY_NAME)
    assert model is not None and mesh is not None and inspect is not None
    plans = {
        plan.command_id: plan
        for plan in resolve_native_action_inventory(surface).plans
        if plan.command_id in {"FEM_MeshGmshFromShape", "FEM_MeshNetgenFromShape"}
    }
    assert plans["FEM_MeshGmshFromShape"].operation_variant == "create_gmsh"
    assert plans["FEM_MeshNetgenFromShape"].operation_variant == "create_netgen"
    assert all(plan.transaction_behavior == "document" for plan in plans.values())
    contexts = {
        action.action_id: action
        for action in provider_context_actions_for_surface("analyze")
    }
    assert contexts["VibeCAD_AnalyzeReadMeshDefinition"].operation_variant == (
        "fem_mesh_definition"
    )
    assert contexts["VibeCAD_AnalyzeUpdateGmshMesh"].operation_variant == "update_gmsh"
    assert contexts["VibeCAD_AnalyzeUpdateNetgenMesh"].operation_variant == (
        "update_netgen"
    )
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(
                ANALYZE_MODEL_CAPABILITY_NAME,
                ANALYZE_MESH_CAPABILITY_NAME,
                ANALYZE_INSPECT_CAPABILITY_NAME,
            ),
            schemas=(
                model.provider_schema(("create_analysis",)),
                mesh.provider_schema(OPERATIONS),
                inspect.provider_schema(("fem_mesh_definition",)),
            ),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _analysis_target(state: dict) -> dict:
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
        "expected_member_count": state["member_count"],
    }


def _object_target(state: dict) -> dict:
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _source_target(source) -> dict:
    return {
        "object_name": source.Name,
        "expected_state_sha256": mesh_object_state(source)["state_sha256"],
    }


def _publish_box(document, name: str, length: float):
    document.openTransaction(f"Create {name}")
    try:
        source = document.addObject("Part::Box", name)
        source.Length = length
        source.Width = 20.0
        source.Height = 10.0
        assert document.recompute([source], True, True) is not False
        assert not source.Shape.isNull() and source.Shape.isValid()
        document.publishProvisionalTimelineOperationBlock(source, (), ())
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    return source


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("FemWorkbench")
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-analyze-mesh-")
        save_path = Path(temporary.name) / "native-analyze-mesh.FCStd"
        document = App.newDocument("NativeAnalyzeMeshGate")
        document.UndoMode = 1
        document.saveAs(str(save_path))
        VibeGui._connect_document_observer()
        controller, surface = _select_analyze_ribbon(Gui.getMainWindow())
        first = _publish_box(document, "GmshSource", 30.0)
        second = _publish_box(document, "NetgenSource", 40.0)
        frozen = NativeSurfaceSnapshot.from_surface(surface)
        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        revision_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-analyze-mesh-gui")

        def reauthorize() -> None:
            require_frozen_native_surface(frozen, controller)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=revision_store,
            undo_ledger=ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(controller).surface_id,
            edit_or_task_active=lambda: bool(Gui.Control.activeDialog()),
        )
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=revision_store,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )
        call_number = 0

        def call(tool: str, arguments: dict, *, succeeds: bool = True) -> dict:
            nonlocal call_number
            call_number += 1
            result = dispatcher.call(
                tool,
                json.dumps(arguments, separators=(",", ":")),
                f"native-analyze-mesh-{call_number}",
            )
            assert result.get("ok") is succeeds, result
            assert not Gui.Control.activeDialog()
            return result

        analyses = []
        for label in ("Gmsh Analysis", "Netgen Analysis"):
            result = call(
                ANALYZE_MODEL_CAPABILITY_NAME,
                {
                    "operation": "create_analysis",
                    "label": label,
                    "default_solver_policy": "none",
                },
            )
            analyses.append(document.getObject(result["created_analysis"]["object_name"]))

        gmsh_result = call(
            ANALYZE_MESH_CAPABILITY_NAME,
            {
                "operation": "create_gmsh",
                "analysis": _analysis_target(analysis_state(analyses[0])),
                "source": _source_target(first),
                "label": "Production Gmsh Definition",
                "settings": {
                    "maximum_size_mm": 5.0,
                    "minimum_size_mm": 1.0,
                    "element_dimension": "3d",
                    "element_order": "second",
                },
            },
        )
        gmsh = document.getObject(gmsh_result["created_mesh_definition"]["object_name"])
        gmsh_before = fem_mesh_definition_state(gmsh)
        assert not gmsh_before["generated"]

        duplicate = call(
            ANALYZE_MESH_CAPABILITY_NAME,
            {
                "operation": "create_gmsh",
                "analysis": _analysis_target(analysis_state(analyses[0])),
                "source": _source_target(first),
                "label": "Ambiguous Mesh Must Fail",
                "settings": gmsh_before["settings"],
            },
            succeeds=False,
        )
        assert "already contains a mesh definition" in duplicate["error"]

        gmsh_updated = call(
            ANALYZE_MESH_CAPABILITY_NAME,
            {
                "operation": "update_gmsh",
                "target": _object_target(gmsh_before),
                "source": _source_target(second),
                "settings": {
                    "maximum_size_mm": 3.5,
                    "minimum_size_mm": 0.75,
                    "element_dimension": "from_shape",
                    "element_order": "first",
                },
            },
        )["updated_mesh_definition"]
        assert gmsh_updated["source"]["object_name"] == second.Name
        assert not gmsh_updated["generated"]

        stale = call(
            ANALYZE_MESH_CAPABILITY_NAME,
            {
                "operation": "update_gmsh",
                "target": _object_target(gmsh_before),
                "label": "Stale Must Fail",
            },
            succeeds=False,
        )
        assert stale["error_code"] == "NATIVE_ANALYZE_STATE_STALE"

        netgen_result = call(
            ANALYZE_MESH_CAPABILITY_NAME,
            {
                "operation": "create_netgen",
                "analysis": _analysis_target(analysis_state(analyses[1])),
                "source": _source_target(first),
                "label": "Production Netgen Definition",
                "settings": {
                    "maximum_size_mm": 6.0,
                    "minimum_size_mm": 1.5,
                    "fineness": "moderate",
                    "second_order": False,
                },
            },
        )
        netgen = document.getObject(
            netgen_result["created_mesh_definition"]["object_name"]
        )
        netgen_before = fem_mesh_definition_state(netgen)
        netgen_updated = call(
            ANALYZE_MESH_CAPABILITY_NAME,
            {
                "operation": "update_netgen",
                "target": _object_target(netgen_before),
                "settings": {
                    "maximum_size_mm": 4.0,
                    "minimum_size_mm": 0.5,
                    "fineness": "user_defined",
                    "second_order": True,
                    "user_fineness": {
                        "growth_rate": 0.25,
                        "curvature_safety": 2.5,
                        "segments_per_edge": 1.5,
                    },
                },
            },
        )["updated_mesh_definition"]
        assert netgen_updated["settings"]["fineness"] == "user_defined"
        assert netgen_updated["settings"]["user_fineness"] == {
            "growth_rate": 0.25,
            "curvature_safety": 2.5,
            "segments_per_edge": 1.5,
        }

        read_revision = revision_store.current_revision(str(document.Uid))
        for mesh in (gmsh, netgen):
            current = fem_mesh_definition_state(mesh)
            read = call(
                ANALYZE_INSPECT_CAPABILITY_NAME,
                {
                    "operation": "fem_mesh_definition",
                    "target": _object_target(current),
                },
            )
            assert read["fem_mesh_definition"] == current
        assert revision_store.current_revision(str(document.Uid)) == read_revision

        snapshot = build_analyze_snapshot(document)
        assert snapshot["mesh_definition_count"] == 2
        assert not snapshot["mesh_definitions_truncated"]
        assert {item["mesher"] for item in snapshot["mesh_definitions"]} == {
            "gmsh",
            "netgen",
        }
        assert tuple(analyses[0].Group) == (gmsh,)
        assert tuple(analyses[1].Group) == (netgen,)
        operation_names = tuple(obj.Name for obj in document.VibeCADTimeline.Operations)
        assert operation_names == (
            first.Name,
            second.Name,
            analyses[0].Name,
            analyses[1].Name,
            gmsh.Name,
            netgen.Name,
        )

        document.undo()
        assert fem_mesh_definition_state(netgen)["settings"] == netgen_before["settings"]
        document.redo()
        assert fem_mesh_definition_state(netgen)["settings"] == netgen_updated["settings"]

        expected = {
            mesh.Name: fem_mesh_definition_state(mesh) for mesh in (gmsh, netgen)
        }
        analysis_members = {
            analysis.Name: tuple(obj.Name for obj in analysis.Group)
            for analysis in analyses
        }
        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        App.setActiveDocument(document.Name)
        _process_events(12)
        assert tuple(obj.Name for obj in document.VibeCADTimeline.Operations) == operation_names
        for name, member_names in analysis_members.items():
            assert tuple(obj.Name for obj in document.getObject(name).Group) == member_names
        for name, old_state in expected.items():
            new_state = fem_mesh_definition_state(document.getObject(name))
            assert new_state["state_sha256"] == old_state["state_sha256"]
            assert new_state["settings"] == old_state["settings"]
            assert new_state["source"] == old_state["source"]

        print(
            "VIBECAD_NATIVE_ANALYZE_MESH_GUI_OK actions=2 meshers=2 edits=2 "
            "exact_sources=true one_mesh_per_analysis=true definitions_only=true "
            "history=true undo_redo=true reopen=true read_revision_stable=true",
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
