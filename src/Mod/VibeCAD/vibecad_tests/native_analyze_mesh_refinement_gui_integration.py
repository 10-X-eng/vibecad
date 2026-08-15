# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for Native FEM mesh-refinement tools."""

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
from VibeCADNativeAnalyzeMeshRefinementSchema import (
    ANALYZE_MESH_REFINEMENT_CAPABILITY_NAME,
)
from VibeCADNativeAnalyzeMeshRefinementState import mesh_refinement_state
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


MODES = ("region", "group", "distance", "boundary_layer", "shape")
OPERATIONS = tuple(
    operation for mode in MODES for operation in (f"create_{mode}", f"update_{mode}")
)


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
    return controller, surface


def _turn(surface, registry) -> NativeTurnSnapshot:
    model = registry.definition(ANALYZE_MODEL_CAPABILITY_NAME)
    mesh = registry.definition(ANALYZE_MESH_CAPABILITY_NAME)
    refinement = registry.definition(ANALYZE_MESH_REFINEMENT_CAPABILITY_NAME)
    inspect = registry.definition(ANALYZE_INSPECT_CAPABILITY_NAME)
    assert all(item is not None for item in (model, mesh, refinement, inspect))
    action_operations = {
        "FEM_MeshRegion": "create_region",
        "FEM_MeshGroup": "create_group",
        "FEM_MeshDistance": "create_distance",
        "FEM_MeshBoundaryLayer": "create_boundary_layer",
        "FEM_MeshShape": "create_shape",
    }
    plans = {
        plan.command_id: plan
        for plan in resolve_native_action_inventory(surface).plans
        if plan.command_id in action_operations
    }
    assert set(plans) == set(action_operations)
    for action_id, operation in action_operations.items():
        assert plans[action_id].capability_family == ANALYZE_MESH_REFINEMENT_CAPABILITY_NAME
        assert plans[action_id].operation_variant == operation
        assert plans[action_id].transaction_behavior == "document"
    contexts = {
        action.action_id: action
        for action in provider_context_actions_for_surface("analyze")
    }
    assert contexts["VibeCAD_AnalyzeReadMeshRefinement"].operation_variant == (
        "mesh_refinement"
    )
    for mode in MODES:
        action_id = "VibeCAD_AnalyzeUpdateMesh" + "".join(
            part.title() for part in mode.split("_")
        )
        assert contexts[action_id].operation_variant == f"update_{mode}"
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(
                ANALYZE_MODEL_CAPABILITY_NAME,
                ANALYZE_MESH_CAPABILITY_NAME,
                ANALYZE_MESH_REFINEMENT_CAPABILITY_NAME,
                ANALYZE_INSPECT_CAPABILITY_NAME,
            ),
            schemas=(
                model.provider_schema(("create_analysis",)),
                mesh.provider_schema(("create_gmsh",)),
                refinement.provider_schema(OPERATIONS),
                inspect.provider_schema(("mesh_refinement",)),
            ),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _analysis_target(value: dict) -> dict:
    return {
        "object_name": value["object_name"],
        "expected_state_sha256": value["state_sha256"],
        "expected_member_count": value["member_count"],
    }


def _target(value: dict) -> dict:
    return {
        "object_name": value["object_name"],
        "expected_state_sha256": value["state_sha256"],
    }


def _reference(source, *subelements: str) -> list[dict]:
    return [
        {
            "object_name": source.Name,
            "expected_state_sha256": mesh_object_state(source)["state_sha256"],
            "subelements": list(subelements),
        }
    ]


def _publish_box(document):
    document.openTransaction("Create Refinement Source")
    try:
        source = document.addObject("Part::Box", "RefinementSource")
        source.Length = 30.0
        source.Width = 20.0
        source.Height = 10.0
        assert document.recompute([source], True, True) is not False
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
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-analyze-refine-")
        save_path = Path(temporary.name) / "native-analyze-refine.FCStd"
        document = App.newDocument("NativeAnalyzeRefinementGate")
        document.UndoMode = 1
        document.saveAs(str(save_path))
        VibeGui._connect_document_observer()
        controller, surface = _select_analyze_ribbon(Gui.getMainWindow())
        source = _publish_box(document)
        frozen = NativeSurfaceSnapshot.from_surface(surface)
        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        revision_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-analyze-refinement-gui")

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
                f"native-analyze-refinement-{call_number}",
            )
            assert result.get("ok") is succeeds, result
            assert not Gui.Control.activeDialog()
            return result

        analysis_result = call(
            ANALYZE_MODEL_CAPABILITY_NAME,
            {
                "operation": "create_analysis",
                "label": "Refinement Analysis",
                "default_solver_policy": "none",
            },
        )
        analysis = document.getObject(analysis_result["created_analysis"]["object_name"])
        mesh_result = call(
            ANALYZE_MESH_CAPABILITY_NAME,
            {
                "operation": "create_gmsh",
                "analysis": _analysis_target(analysis_state(analysis)),
                "source": _target(mesh_object_state(source)),
                "label": "Refinement Mesh Definition",
                "settings": {
                    "maximum_size_mm": 5.0,
                    "minimum_size_mm": 1.0,
                    "element_dimension": "3d",
                    "element_order": "second",
                },
            },
        )
        mesh = document.getObject(mesh_result["created_mesh_definition"]["object_name"])

        creates = {
            "region": {
                "references": _reference(source, "Solid1", "Face1"),
                "definition": {"element_size_mm": 2.0},
            },
            "group": {
                "references": _reference(source, "Face1", "Edge1"),
                "definition": {"export_identifier": "object_name"},
            },
            "distance": {
                "references": _reference(source, "Face2"),
                "definition": {
                    "distance_minimum_mm": 2.0,
                    "distance_maximum_mm": 20.0,
                    "size_minimum_mm": 1.0,
                    "size_maximum_mm": 5.0,
                    "linear_interpolation": True,
                    "sampling": 20,
                },
            },
            "boundary_layer": {
                "references": _reference(source, "Edge1", "Edge2"),
                "definition": {
                    "minimum_thickness_mm": 0.2,
                    "number_of_layers": 4,
                    "growth_rate": 1.4,
                },
            },
            "shape": {
                "definition": {
                    "shape": {
                        "kind": "box",
                        "center_mm": {"x": 15.0, "y": 10.0, "z": 5.0},
                        "length_mm": 20.0,
                        "width_mm": 15.0,
                        "height_mm": 8.0,
                    },
                    "size_inside_mm": 1.0,
                    "size_outside_mm": 8.0,
                    "transition_thickness_mm": 2.0,
                }
            },
        }
        refinements = {}
        before_updates = {}
        for mode in MODES:
            result = call(
                ANALYZE_MESH_REFINEMENT_CAPABILITY_NAME,
                {
                    "operation": f"create_{mode}",
                    "mesh": _target(fem_mesh_definition_state(mesh)),
                    "label": mode.replace("_", " ").title(),
                    **creates[mode],
                },
            )
            refinement = document.getObject(
                result["created_mesh_refinement"]["object_name"]
            )
            refinements[mode] = refinement
            assert not result["mesh_definition"]["generated"]

        updates = {
            "region": {"definition": {"element_size_mm": 1.5}},
            "group": {"definition": {"export_identifier": "label"}},
            "distance": {
                "references": _reference(source, "Vertex1"),
                "definition": {
                    "distance_minimum_mm": 1.0,
                    "distance_maximum_mm": 15.0,
                    "size_minimum_mm": 0.75,
                    "size_maximum_mm": 4.0,
                    "linear_interpolation": False,
                    "sampling": 32,
                },
            },
            "boundary_layer": {
                "definition": {
                    "minimum_thickness_mm": 0.15,
                    "number_of_layers": 6,
                    "growth_rate": 1.25,
                }
            },
            "shape": {
                "definition": {
                    "shape": {
                        "kind": "sphere",
                        "center_mm": {"x": 15.0, "y": 10.0, "z": 5.0},
                        "radius_mm": 7.0,
                    },
                    "size_inside_mm": 0.8,
                    "size_outside_mm": 6.0,
                    "transition_thickness_mm": 1.5,
                }
            },
        }
        updated = {}
        for mode in MODES:
            before_updates[mode] = mesh_refinement_state(refinements[mode])
            updated[mode] = call(
                ANALYZE_MESH_REFINEMENT_CAPABILITY_NAME,
                {
                    "operation": f"update_{mode}",
                    "target": _target(before_updates[mode]),
                    **updates[mode],
                },
            )["updated_mesh_refinement"]

        stale = call(
            ANALYZE_MESH_REFINEMENT_CAPABILITY_NAME,
            {
                "operation": "update_region",
                "target": _target(before_updates["region"]),
                "definition": {"element_size_mm": 1.0},
            },
            succeeds=False,
        )
        assert stale["error_code"] == "NATIVE_ANALYZE_STATE_STALE"

        read_revision = revision_store.current_revision(str(document.Uid))
        for mode in MODES:
            current = mesh_refinement_state(refinements[mode])
            read = call(
                ANALYZE_INSPECT_CAPABILITY_NAME,
                {"operation": "mesh_refinement", "target": _target(current)},
            )
            assert read["mesh_refinement"] == current
        assert revision_store.current_revision(str(document.Uid)) == read_revision

        snapshot = build_analyze_snapshot(document)
        assert snapshot["mesh_refinement_count"] == 5
        assert {item["refinement_mode"] for item in snapshot["mesh_refinements"]} == set(
            MODES
        )
        assert tuple(mesh.MeshRefinementList) == (
            refinements["region"],
            refinements["distance"],
            refinements["boundary_layer"],
            refinements["shape"],
        )
        assert tuple(mesh.MeshGroupList) == (refinements["group"],)
        expected_history = (
            source.Name,
            analysis.Name,
            *(refinements[mode].Name for mode in MODES),
            mesh.Name,
        )
        assert tuple(obj.Name for obj in document.VibeCADTimeline.Operations) == expected_history

        document.undo()
        assert mesh_refinement_state(refinements["shape"])["definition"] == before_updates[
            "shape"
        ]["definition"]
        document.redo()
        assert mesh_refinement_state(refinements["shape"])["definition"] == updated["shape"][
            "definition"
        ]

        expected = {
            refinement.Name: mesh_refinement_state(refinement)
            for refinement in refinements.values()
        }
        mesh_name = mesh.Name
        refinement_names = {mode: value.Name for mode, value in refinements.items()}
        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        App.setActiveDocument(document.Name)
        _process_events(12)
        reopened_mesh = document.getObject(mesh_name)
        assert tuple(obj.Name for obj in reopened_mesh.MeshRefinementList) == tuple(
            refinement_names[mode]
            for mode in ("region", "distance", "boundary_layer", "shape")
        )
        assert tuple(obj.Name for obj in reopened_mesh.MeshGroupList) == (
            refinement_names["group"],
        )
        assert tuple(obj.Name for obj in document.VibeCADTimeline.Operations) == expected_history
        for name, old_state in expected.items():
            new_state = mesh_refinement_state(document.getObject(name))
            assert new_state["state_sha256"] == old_state["state_sha256"]
            assert new_state["definition"] == old_state["definition"]
            assert new_state["references"] == old_state["references"]

        print(
            "VIBECAD_NATIVE_ANALYZE_MESH_REFINEMENT_GUI_OK actions=5 modes=5 "
            "edits=5 exact_geometry=true owned_resources=true invalidation=true "
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
