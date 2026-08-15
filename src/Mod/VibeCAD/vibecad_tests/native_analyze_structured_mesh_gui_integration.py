# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for Native structured mesh tools."""

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
from VibeCADNativeAnalyzeMeshRefinementState import mesh_refinement_state
from VibeCADNativeAnalyzeMeshSchema import ANALYZE_MESH_CAPABILITY_NAME
from VibeCADNativeAnalyzeMeshState import fem_mesh_definition_state
from VibeCADNativeAnalyzeModelSchema import ANALYZE_MODEL_CAPABILITY_NAME
from VibeCADNativeAnalyzeState import analysis_state
from VibeCADNativeAnalyzeStructuredMeshSchema import (
    ANALYZE_STRUCTURED_MESH_CAPABILITY_NAME,
)
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


MODES = ("transfinite_curve", "transfinite_surface", "transfinite_volume")
OPERATIONS = tuple(
    operation for mode in MODES for operation in (f"create_{mode}", f"update_{mode}")
)


def _events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _surface(main_window):
    controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
    tabs = main_window.findChild(QtWidgets.QTabBar, "VibeCADRibbonTabs")
    assert controller is not None and tabs is not None
    index = next(i for i in range(tabs.count()) if str(tabs.tabData(i)) == "FemWorkbench")
    tabs.setCurrentIndex(index)
    _events(24)
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "analyze"
    return controller, surface


def _turn(surface, registry) -> NativeTurnSnapshot:
    model = registry.definition(ANALYZE_MODEL_CAPABILITY_NAME)
    mesh = registry.definition(ANALYZE_MESH_CAPABILITY_NAME)
    structured = registry.definition(ANALYZE_STRUCTURED_MESH_CAPABILITY_NAME)
    assert model is not None and mesh is not None and structured is not None
    expected = {
        "FEM_MeshTransfiniteCurve": "create_transfinite_curve",
        "FEM_MeshTransfiniteSurface": "create_transfinite_surface",
        "FEM_MeshTransfiniteVolume": "create_transfinite_volume",
    }
    plans = {
        plan.command_id: plan
        for plan in resolve_native_action_inventory(surface).plans
        if plan.command_id in expected
    }
    assert set(plans) == set(expected)
    for action_id, operation in expected.items():
        assert plans[action_id].capability_family == ANALYZE_STRUCTURED_MESH_CAPABILITY_NAME
        assert plans[action_id].operation_variant == operation
    contexts = {
        action.action_id: action for action in provider_context_actions_for_surface("analyze")
    }
    for mode in MODES:
        action_id = "VibeCAD_AnalyzeUpdate" + "".join(
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
                ANALYZE_STRUCTURED_MESH_CAPABILITY_NAME,
            ),
            schemas=(
                model.provider_schema(("create_analysis",)),
                mesh.provider_schema(("create_gmsh",)),
                structured.provider_schema(OPERATIONS),
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


def _refs(source, *subelements: str) -> list[dict]:
    return [
        {
            "object_name": source.Name,
            "expected_state_sha256": mesh_object_state(source)["state_sha256"],
            "subelements": list(subelements),
        }
    ]


def _source(document):
    document.openTransaction("Create Structured Source")
    try:
        source = document.addObject("Part::Box", "StructuredSource")
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
    app = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("FemWorkbench")
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-structured-")
        path = Path(temporary.name) / "native-structured.FCStd"
        document = App.newDocument("NativeStructuredMeshGate")
        document.UndoMode = 1
        document.saveAs(str(path))
        VibeGui._connect_document_observer()
        controller, surface = _surface(Gui.getMainWindow())
        source = _source(document)
        frozen = NativeSurfaceSnapshot.from_surface(surface)
        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-structured-mesh-gui")

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

        def call(tool: str, arguments: dict, *, succeeds: bool = True) -> dict:
            nonlocal call_number
            call_number += 1
            result = dispatcher.call(
                tool,
                json.dumps(arguments, separators=(",", ":")),
                f"native-structured-{call_number}",
            )
            assert result.get("ok") is succeeds, result
            return result

        analysis_result = call(
            ANALYZE_MODEL_CAPABILITY_NAME,
            {
                "operation": "create_analysis",
                "label": "Structured Analysis",
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
                "label": "Structured Gmsh",
                "settings": {
                    "maximum_size_mm": 5.0,
                    "minimum_size_mm": 1.0,
                    "element_dimension": "3d",
                    "element_order": "second",
                },
            },
        )
        mesh = document.getObject(mesh_result["created_mesh_definition"]["object_name"])
        common = {
            "recombine": False,
            "triangle_orientation": "alternate_right",
            "use_automation": True,
            "nodes": 8,
            "coefficient": 1.25,
            "distribution": "progression",
            "inverted": False,
        }
        creates = {
            "transfinite_curve": {
                "references": _refs(source, "Edge1", "Edge2"),
                "definition": {
                    "nodes": 10,
                    "coefficient": 1.2,
                    "distribution": "bump",
                    "inverted": False,
                },
            },
            "transfinite_surface": {
                "references": _refs(source, "Face1", "Vertex1"),
                "definition": dict(common),
            },
            "transfinite_volume": {
                "references": _refs(source, "Solid1"),
                "definition": {"mixed_elements": True, **common},
            },
        }
        resources = {}
        before = {}
        for mode in MODES:
            result = call(
                ANALYZE_STRUCTURED_MESH_CAPABILITY_NAME,
                {
                    "operation": f"create_{mode}",
                    "mesh": _target(fem_mesh_definition_state(mesh)),
                    "label": mode.replace("_", " ").title(),
                    **creates[mode],
                },
            )
            resource = document.getObject(result["created_mesh_refinement"]["object_name"])
            resources[mode] = resource
            assert mesh_refinement_state(resource)["refinement_mode"] == mode

        updates = {
            "transfinite_curve": {
                "definition": {
                    "nodes": 14,
                    "coefficient": 1.1,
                    "distribution": "progression",
                    "inverted": True,
                }
            },
            "transfinite_surface": {
                "definition": {
                    **common,
                    "recombine": True,
                    "triangle_orientation": "left",
                    "nodes": 12,
                }
            },
            "transfinite_volume": {
                "definition": {
                    "mixed_elements": False,
                    **common,
                    "triangle_orientation": "right",
                    "use_automation": False,
                }
            },
        }
        final = {}
        for mode in MODES:
            before[mode] = mesh_refinement_state(resources[mode])
            final[mode] = call(
                ANALYZE_STRUCTURED_MESH_CAPABILITY_NAME,
                {
                    "operation": f"update_{mode}",
                    "target": _target(before[mode]),
                    **updates[mode],
                },
            )["updated_mesh_refinement"]

        stale = call(
            ANALYZE_STRUCTURED_MESH_CAPABILITY_NAME,
            {
                "operation": "update_transfinite_curve",
                "target": _target(before["transfinite_curve"]),
                "label": "Stale",
            },
            succeeds=False,
        )
        assert stale["error_code"] == "NATIVE_ANALYZE_STATE_STALE"
        expected_history = (
            source.Name,
            analysis.Name,
            *(resources[mode].Name for mode in MODES),
            mesh.Name,
        )
        assert tuple(obj.Name for obj in document.VibeCADTimeline.Operations) == expected_history

        document.undo()
        assert mesh_refinement_state(resources["transfinite_volume"])["definition"] == before[
            "transfinite_volume"
        ]["definition"]
        document.redo()
        assert mesh_refinement_state(resources["transfinite_volume"])["definition"] == final[
            "transfinite_volume"
        ]["definition"]

        expected = {obj.Name: mesh_refinement_state(obj) for obj in resources.values()}
        mesh_name = mesh.Name
        resource_names = {mode: obj.Name for mode, obj in resources.items()}
        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(path))
        App.setActiveDocument(document.Name)
        _events(12)
        reopened_mesh = document.getObject(mesh_name)
        assert tuple(obj.Name for obj in reopened_mesh.MeshRefinementList) == tuple(
            resource_names[mode] for mode in MODES
        )
        assert tuple(obj.Name for obj in document.VibeCADTimeline.Operations) == expected_history
        for name, old in expected.items():
            assert mesh_refinement_state(document.getObject(name))["state_sha256"] == old[
                "state_sha256"
            ]

        print(
            "VIBECAD_NATIVE_ANALYZE_STRUCTURED_MESH_GUI_OK actions=3 modes=3 edits=3 "
            "typed_distribution=true mixed_surface_geometry=true owned_resources=true "
            "history=true undo_redo=true reopen=true",
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
        app.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
