# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for Native Gmsh refinement-field tools."""

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
from VibeCADNativeAnalyzeMeshFieldSchema import ANALYZE_MESH_FIELD_CAPABILITY_NAME
from VibeCADNativeAnalyzeMeshFieldValues import ADVANCED_KINDS, MANIPULATION_KINDS
from VibeCADNativeAnalyzeMeshRefinementSchema import ANALYZE_MESH_REFINEMENT_CAPABILITY_NAME
from VibeCADNativeAnalyzeMeshRefinementState import mesh_refinement_state
from VibeCADNativeAnalyzeMeshSchema import ANALYZE_MESH_CAPABILITY_NAME
from VibeCADNativeAnalyzeMeshState import fem_mesh_definition_state
from VibeCADNativeAnalyzeModelSchema import ANALYZE_MODEL_CAPABILITY_NAME
from VibeCADNativeAnalyzeResultState import result_state
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


KINDS = (*MANIPULATION_KINDS, *ADVANCED_KINDS)
FIELD_OPERATIONS = tuple(
    operation for kind in KINDS for operation in (f"create_{kind}", f"update_{kind}")
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
    refinement = registry.definition(ANALYZE_MESH_REFINEMENT_CAPABILITY_NAME)
    field = registry.definition(ANALYZE_MESH_FIELD_CAPABILITY_NAME)
    assert all(item is not None for item in (model, mesh, refinement, field))
    plans = {
        plan.command_id: plan
        for plan in resolve_native_action_inventory(surface).plans
        if plan.command_id in {"FEM_MeshManipulate", "FEM_MeshAdvanced"}
    }
    assert plans["FEM_MeshManipulate"].capability_family == ANALYZE_MESH_FIELD_CAPABILITY_NAME
    assert plans["FEM_MeshManipulate"].operation_variant == "create_restrict"
    assert plans["FEM_MeshAdvanced"].capability_family == ANALYZE_MESH_FIELD_CAPABILITY_NAME
    assert plans["FEM_MeshAdvanced"].operation_variant == "create_attractor_aniso_curve"
    contexts = provider_context_actions_for_surface("analyze")
    observed = {
        action.operation_variant
        for action in contexts
        if action.capability_family == ANALYZE_MESH_FIELD_CAPABILITY_NAME
    }
    assert set(FIELD_OPERATIONS) <= observed | {"create_restrict", "create_attractor_aniso_curve"}
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(
                ANALYZE_MODEL_CAPABILITY_NAME,
                ANALYZE_MESH_CAPABILITY_NAME,
                ANALYZE_MESH_REFINEMENT_CAPABILITY_NAME,
                ANALYZE_MESH_FIELD_CAPABILITY_NAME,
            ),
            schemas=(
                model.provider_schema(("create_analysis",)),
                mesh.provider_schema(("create_gmsh",)),
                refinement.provider_schema(("create_region",)),
                field.provider_schema(FIELD_OPERATIONS),
            ),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _target(state: dict) -> dict:
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _analysis_target(state: dict) -> dict:
    return {**_target(state), "expected_member_count": state["member_count"]}


def _refs(source, *subelements: str) -> list[dict]:
    return [
        {
            "object_name": source.Name,
            "expected_state_sha256": mesh_object_state(source)["state_sha256"],
            "subelements": list(subelements),
        }
    ]


def _source(document):
    document.openTransaction("Create Mesh Field Source")
    try:
        source = document.addObject("Part::Box", "MeshFieldSource")
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


def _result_grid(field_name: str, values: tuple[float, ...]):
    from vtkmodules.vtkCommonCore import vtkDoubleArray, vtkPoints
    from vtkmodules.vtkCommonDataModel import vtkTetra, vtkUnstructuredGrid

    points = vtkPoints()
    for point in ((0, 0, 0), (10, 0, 0), (0, 10, 0), (0, 0, 10)):
        points.InsertNextPoint(*point)
    tetra = vtkTetra()
    for index in range(4):
        tetra.GetPointIds().SetId(index, index)
    grid = vtkUnstructuredGrid()
    grid.SetPoints(points)
    grid.InsertNextCell(tetra.GetCellType(), tetra.GetPointIds())
    size = vtkDoubleArray()
    size.SetName(field_name)
    size.SetNumberOfComponents(1)
    for value in values:
        size.InsertNextValue(value)
    grid.GetPointData().AddArray(size)
    return grid


def _result_sources(document, analysis):
    document.openTransaction("Create adaptive mesh result sources")
    try:
        first = document.addObject("Fem::FemPostPipeline", "InitialMeshSizeResult")
        first.Label = "Initial Mesh Size Result"
        first.Data = _result_grid("TargetSize", (1.0, 1.5, 2.0, 2.5))
        analysis.addObject(first)
        document.publishProvisionalTimelineOperationBlock(first, (), ())
        second = document.addObject("Fem::FemPostPipeline", "UpdatedMeshSizeResult")
        second.Label = "Updated Mesh Size Result"
        second.Data = _result_grid("RemeshSize", (0.75, 1.25, 1.75, 2.25))
        analysis.addObject(second)
        document.publishProvisionalTimelineOperationBlock(second, (), ())
        invalid = document.addObject("Fem::FemPostPipeline", "InvalidMeshSizeResult")
        invalid.Label = "Invalid Mesh Size Result"
        invalid.Data = _result_grid("InvalidSize", (0.0, 1.0, 2.0, 3.0))
        analysis.addObject(invalid)
        document.publishProvisionalTimelineOperationBlock(invalid, (), ())
        assert document.recompute([first, second, invalid], True, True) is not False
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    return first, second, invalid


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("FemWorkbench")
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-mesh-field-")
        path = Path(temporary.name) / "native-mesh-field.FCStd"
        document = App.newDocument("NativeAnalyzeMeshFieldGate")
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
        ledger.begin_run("native-analyze-mesh-field-gui")

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
        debug_events = []
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state_store,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=authorize,
            active_document=lambda: App.ActiveDocument,
            debug_sink=debug_events.append,
        )
        call_number = 0

        def call(tool: str, arguments: dict, *, succeeds: bool = True) -> dict:
            nonlocal call_number
            call_number += 1
            result = dispatcher.call(
                tool,
                json.dumps(arguments, separators=(",", ":")),
                f"native-mesh-field-{call_number}",
            )
            assert result.get("ok") is succeeds, (result, debug_events[-1:] or None)
            assert not Gui.Control.activeDialog()
            return result

        analysis_result = call(
            ANALYZE_MODEL_CAPABILITY_NAME,
            {
                "operation": "create_analysis",
                "label": "Mesh Field Analysis",
                "default_solver_policy": "none",
            },
        )
        analysis = document.getObject(analysis_result["created_analysis"]["object_name"])
        first_result, second_result, invalid_result = _result_sources(document, analysis)
        mesh_result = call(
            ANALYZE_MESH_CAPABILITY_NAME,
            {
                "operation": "create_gmsh",
                "analysis": _analysis_target(analysis_state(analysis)),
                "source": _target(mesh_object_state(source)),
                "label": "Mesh Field Gmsh",
                "settings": {
                    "maximum_size_mm": 5.0,
                    "minimum_size_mm": 1.0,
                    "element_dimension": "3d",
                    "element_order": "first",
                },
            },
        )
        mesh = document.getObject(mesh_result["created_mesh_definition"]["object_name"])
        region_result = call(
            ANALYZE_MESH_REFINEMENT_CAPABILITY_NAME,
            {
                "operation": "create_region",
                "mesh": _target(fem_mesh_definition_state(mesh)),
                "label": "Base Size Field",
                "references": _refs(source, "Solid1"),
                "definition": {"element_size_mm": 2.0},
            },
        )
        region = document.getObject(region_result["created_mesh_refinement"]["object_name"])

        invalid_field = call(
            ANALYZE_MESH_FIELD_CAPABILITY_NAME,
            {
                "operation": "create_result",
                "mesh": _target(fem_mesh_definition_state(mesh)),
                "label": "Invalid Result Field",
                "definition": {"field": "InvalidSize"},
                "result": _target(result_state(invalid_result, include_ranges=False)),
            },
            succeeds=False,
        )
        assert invalid_field["error_code"] == "NATIVE_ANALYZE_FIELD_RANGE_INVALID"
        assert len(tuple(mesh.MeshRefinementList or ())) == 1

        definitions = {
            "restrict": {"include_boundary": True},
            "threshold": {
                "input_minimum_mm": 2.0,
                "input_maximum_mm": 20.0,
                "size_minimum_mm": 1.0,
                "size_maximum_mm": 5.0,
                "linear_interpolation": True,
                "stop_at_input_maximum": True,
            },
            "mean": {"delta_mm": 2.0},
            "gradient": {"delta_mm": 2.5, "component": "mean"},
            "curvature": {"delta_mm": 3.0},
            "laplacian": {"delta_mm": 3.5},
            "attractor_aniso_curve": {
                "distance_minimum_mm": 2.0,
                "distance_maximum_mm": 20.0,
                "size_minimum_normal_mm": 0.8,
                "size_maximum_normal_mm": 4.0,
                "size_minimum_tangent_mm": 1.2,
                "size_maximum_tangent_mm": 6.0,
                "sampling": 20,
            },
            "math_eval": {"equation": "Min(F1, 4.0)"},
            "math_eval_aniso": {
                "metric": {
                    "m11": "F1", "m12": "0", "m13": "0",
                    "m22": "F1", "m23": "0", "m33": "F1",
                }
            },
            "distance": {"sampling": 24},
            "result": {"field": "TargetSize"},
        }
        fields = {}
        previous = region
        for kind in MANIPULATION_KINDS:
            arguments = {
                "operation": f"create_{kind}",
                "mesh": _target(fem_mesh_definition_state(mesh)),
                "label": kind.replace("_", " ").title(),
                "input_refinement": _target(mesh_refinement_state(previous)),
                "definition": definitions[kind],
            }
            if kind == "restrict":
                arguments["references"] = _refs(source, "Solid1", "Face1")
            result = call(ANALYZE_MESH_FIELD_CAPABILITY_NAME, arguments)
            field = document.getObject(result["created_mesh_field"]["object_name"])
            fields[kind] = field
            previous = field

        for kind in ADVANCED_KINDS:
            arguments = {
                "operation": f"create_{kind}",
                "mesh": _target(fem_mesh_definition_state(mesh)),
                "label": kind.replace("_", " ").title(),
                "definition": definitions[kind],
            }
            if kind in {"math_eval", "math_eval_aniso"}:
                input_obj = region if kind == "math_eval" else fields["math_eval"]
                arguments["input_refinements"] = [
                    _target(mesh_refinement_state(input_obj))
                ]
            elif kind == "attractor_aniso_curve":
                arguments["references"] = _refs(source, "Edge1", "Edge2")
            elif kind == "distance":
                arguments["references"] = _refs(source, "Face1", "Vertex1")
            elif kind == "result":
                arguments["result"] = _target(result_state(first_result, include_ranges=False))
            result = call(ANALYZE_MESH_FIELD_CAPABILITY_NAME, arguments)
            fields[kind] = document.getObject(result["created_mesh_field"]["object_name"])

        cycle = call(
            ANALYZE_MESH_FIELD_CAPABILITY_NAME,
            {
                "operation": "update_restrict",
                "target": _target(mesh_refinement_state(fields["restrict"])),
                "input_refinement": _target(mesh_refinement_state(fields["mean"])),
            },
            succeeds=False,
        )
        assert cycle["error_code"] == "NATIVE_ANALYZE_DEPENDENCY_CYCLE"

        updates = {
            "restrict": {"definition": {"include_boundary": False}},
            "threshold": {"definition": {**definitions["threshold"], "input_maximum_mm": 25.0}},
            "mean": {"definition": {"delta_mm": 2.25}},
            "gradient": {"definition": {"delta_mm": 2.75, "component": "x"}},
            "curvature": {"definition": {"delta_mm": 3.25}},
            "laplacian": {"definition": {"delta_mm": 3.75}},
            "attractor_aniso_curve": {"definition": {**definitions["attractor_aniso_curve"], "sampling": 32}},
            "math_eval": {"definition": {"equation": "Max(F1, 1.0)"}},
            "math_eval_aniso": {"definition": {"metric": {**definitions["math_eval_aniso"]["metric"], "m11": "2*F1"}}},
            "distance": {"definition": {"sampling": 36}},
            "result": {
                "definition": {"field": "RemeshSize"},
                "result": _target(result_state(second_result, include_ranges=False)),
            },
        }
        before_last = None
        final_states = {}
        for kind in KINDS:
            before = mesh_refinement_state(fields[kind])
            if kind == KINDS[-1]:
                before_last = before
            result = call(
                ANALYZE_MESH_FIELD_CAPABILITY_NAME,
                {
                    "operation": f"update_{kind}",
                    "target": _target(before),
                    **updates[kind],
                },
            )
            final_states[kind] = result["updated_mesh_field"]

        assert before_last is not None
        document.undo()
        assert mesh_refinement_state(fields[KINDS[-1]])["state_sha256"] == before_last["state_sha256"]
        document.redo()
        assert mesh_refinement_state(fields[KINDS[-1]])["state_sha256"] == final_states[KINDS[-1]]["state_sha256"]

        expected_history = (
            source.Name,
            analysis.Name,
            first_result.Name,
            second_result.Name,
            invalid_result.Name,
            region.Name,
            *(fields[kind].Name for kind in KINDS),
            mesh.Name,
        )
        assert tuple(obj.Name for obj in document.VibeCADTimeline.Operations) == expected_history
        names = {kind: fields[kind].Name for kind in KINDS}
        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(path))
        App.setActiveDocument(document.Name)
        _events(12)
        assert tuple(obj.Name for obj in document.VibeCADTimeline.Operations) == expected_history
        for kind, name in names.items():
            state = mesh_refinement_state(document.getObject(name))
            assert state["state_sha256"] == final_states[kind]["state_sha256"]

        print(
            f"VIBECAD_NATIVE_ANALYZE_MESH_FIELD_GUI_OK actions=2 kinds={len(KINDS)} "
            f"edits={len(KINDS)} "
            "exact_dependencies=true cycle_rejection=true typed_geometry=true "
            "result_source_identity=true positive_scalar_field=true "
            "nonpositive_field_rejection=true "
            "owned_resources=true history=true undo_redo=true reopen=true",
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
