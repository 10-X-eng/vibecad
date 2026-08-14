# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI gate for exact FEM post-pipeline creation from a result."""

from __future__ import annotations

import json
import math
from pathlib import Path
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Fem
import ObjectsFem
import Part
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeAnalyzeInspectSchema import ANALYZE_INSPECT_CAPABILITY_NAME
from VibeCADNativeAnalyzePostSchema import ANALYZE_POST_CAPABILITY_NAME
from VibeCADNativeAnalyzePostFunctionSchema import (
    ANALYZE_POST_FUNCTION_CAPABILITY_NAME,
)
from VibeCADNativeAnalyzeVisualizationSchema import (
    ANALYZE_VISUALIZATION_CAPABILITY_NAME,
)
from VibeCADNativeAnalyzeResultState import result_reference_state, result_state
from VibeCADNativeAnalyzeState import analysis_state
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
    assert "FEM_PostPipelineFromResult" in surface.command_ids
    assert "FEM_PostBranchFilter" in surface.command_ids
    assert "FEM_PostFilterWarp" in surface.command_ids
    assert "FEM_PostFilterClipScalar" in surface.command_ids
    assert "FEM_PostFilterCutFunction" in surface.command_ids
    assert "FEM_PostFilterClipRegion" in surface.command_ids
    assert "FEM_PostFilterContours" in surface.command_ids
    assert "FEM_PostFilterGlyph" in surface.command_ids
    assert "FEM_PostFilterDataAlongLine" in surface.command_ids
    assert "FEM_PostFilterLinearizedStresses" in surface.command_ids
    assert "FEM_PostFilterDataAtPoint" in surface.command_ids
    assert "FEM_PostFilterCalculator" in surface.command_ids
    assert "FEM_PostCreateFunctions" in surface.command_ids
    assert "FEM_PostCreateFunctionPlane" in surface.command_ids
    assert "FEM_PostCreateFunctionSphere" in surface.command_ids
    assert "FEM_PostCreateFunctionCylinder" in surface.command_ids
    assert "FEM_PostCreateFunctionBox" in surface.command_ids
    assert "FEM_PostVisualization" in surface.command_ids
    assert "FEM_PostVisualizationLineplot" in surface.command_ids
    assert "FEM_PostVisualizationHistogram" in surface.command_ids
    assert "FEM_PostVisualizationTable" in surface.command_ids
    return controller, surface


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(ANALYZE_POST_CAPABILITY_NAME)
    assert definition is not None
    for operation, required in (
        ("create_pipeline", {"operation", "analysis", "result", "label"}),
        ("create_branch", {"operation", "source", "label", "mode", "output"}),
        (
            "create_warp",
            {"operation", "source", "label", "vector_field", "factor"},
        ),
        (
            "create_scalar_clip",
            {
                "operation",
                "source",
                "label",
                "scalar_field",
                "threshold",
                "inside_out",
            },
        ),
        (
            "create_cut",
            {"operation", "source", "function", "label"},
        ),
        (
            "create_region_clip",
            {
                "operation",
                "source",
                "function",
                "label",
                "inside_out",
                "cut_cells",
            },
        ),
        (
            "create_contours",
            {
                "operation",
                "source",
                "label",
                "field",
                "component",
                "count",
                "color_by_field",
                "smoothing",
                "relaxation",
            },
        ),
        (
            "create_glyphs",
            {
                "operation",
                "source",
                "label",
                "glyph",
                "orientation",
                "scaling",
                "sampling",
            },
        ),
        (
            "create_line_sample",
            {
                "operation",
                "source",
                "label",
                "field",
                "component",
                "start_mm",
                "end_mm",
                "resolution",
            },
        ),
        (
            "create_point_sample",
            {"operation", "source", "label", "field", "point_mm"},
        ),
        (
            "create_calculated_field",
            {
                "operation",
                "source",
                "label",
                "result_field",
                "result_unit",
                "expression",
                "invalid_values",
            },
        ),
    ):
        single = definition.provider_schema((operation,))
        variant = single["parameters"]["oneOf"][0]
        assert variant["properties"]["operation"]["const"] == operation
        assert set(variant["required"]) == required
    schema = definition.provider_schema(
        (
            "create_pipeline",
            "create_branch",
            "create_warp",
            "create_scalar_clip",
            "create_cut",
            "create_region_clip",
            "create_contours",
            "create_glyphs",
            "create_line_sample",
            "create_point_sample",
            "create_calculated_field",
        )
    )
    operation_schema = schema["parameters"]["properties"]["operation"]
    assert operation_schema["enum"] == [
        "create_pipeline",
        "create_branch",
        "create_warp",
        "create_scalar_clip",
        "create_cut",
        "create_region_clip",
        "create_contours",
        "create_glyphs",
        "create_line_sample",
        "create_point_sample",
        "create_calculated_field",
    ]
    assert "create_pipeline=analysis,result,label" in operation_schema["description"]
    assert "create_branch=source,label,mode,output" in operation_schema["description"]
    assert "create_warp=source,label,vector_field,factor" in operation_schema[
        "description"
    ]
    assert (
        "create_scalar_clip=source,label,scalar_field,threshold,inside_out"
        in operation_schema["description"]
    )
    assert "create_cut=source,function,label" in operation_schema["description"]
    assert (
        "create_region_clip=source,function,label,inside_out,cut_cells"
        in operation_schema["description"]
    )
    assert (
        "create_contours=source,label,field,component,count,color_by_field,smoothing,relaxation"
        in operation_schema["description"]
    )
    assert (
        "create_glyphs=source,label,glyph,orientation,scaling,sampling"
        in operation_schema["description"]
    )
    assert (
        "create_line_sample=source,label,field,component,start_mm,end_mm,resolution"
        in operation_schema["description"]
    )
    assert "create_point_sample=source,label,field,point_mm" in operation_schema[
        "description"
    ]
    assert (
        "create_calculated_field=source,label,result_field,result_unit,expression,invalid_values"
        in operation_schema["description"]
    )
    function_definition = registry.definition(ANALYZE_POST_FUNCTION_CAPABILITY_NAME)
    assert function_definition is not None
    function_operations = (
        ("create_plane", {"operation", "pipeline", "label", "origin_mm", "normal"}),
        (
            "create_sphere",
            {"operation", "pipeline", "label", "center_mm", "radius_mm"},
        ),
        (
            "create_cylinder",
            {"operation", "pipeline", "label", "center_mm", "axis", "radius_mm"},
        ),
        (
            "create_box",
            {
                "operation",
                "pipeline",
                "label",
                "center_mm",
                "length_mm",
                "width_mm",
                "height_mm",
            },
        ),
    )
    for operation, required in function_operations:
        single = function_definition.provider_schema((operation,))
        variant = single["parameters"]["oneOf"][0]
        assert variant["properties"]["operation"]["const"] == operation
        assert set(variant["required"]) == required
    function_schema = function_definition.provider_schema(
        tuple(operation for operation, _required in function_operations)
    )
    assert function_schema["parameters"]["properties"]["operation"]["enum"] == [
        "create_plane",
        "create_sphere",
        "create_cylinder",
        "create_box",
    ]
    inspect_definition = registry.definition(ANALYZE_INSPECT_CAPABILITY_NAME)
    assert inspect_definition is not None
    inspect_schema = inspect_definition.provider_schema(("linearized_stress",))
    inspect_variant = inspect_schema["parameters"]["oneOf"][0]
    assert inspect_variant["properties"]["operation"]["const"] == "linearized_stress"
    assert set(inspect_variant["required"]) == {"operation", "target"}
    visualization_definition = registry.definition(
        ANALYZE_VISUALIZATION_CAPABILITY_NAME
    )
    assert visualization_definition is not None
    visualization_operations = (
        (
            "create_table",
            {"operation", "analysis", "source", "label", "data"},
        ),
        (
            "create_histogram",
            {"operation", "analysis", "source", "label", "data", "view"},
        ),
        (
            "create_line_plot",
            {"operation", "analysis", "source", "label", "data", "view"},
        ),
    )
    for operation, required in visualization_operations:
        single = visualization_definition.provider_schema((operation,))
        variant = single["parameters"]["oneOf"][0]
        assert variant["properties"]["operation"]["const"] == operation
        assert set(variant["required"]) == required
    visualization_schema = visualization_definition.provider_schema(
        tuple(operation for operation, _required in visualization_operations)
    )
    assert visualization_schema["parameters"]["properties"]["operation"]["enum"] == [
        "create_table",
        "create_histogram",
        "create_line_plot",
    ]
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(
                ANALYZE_POST_CAPABILITY_NAME,
                ANALYZE_POST_FUNCTION_CAPABILITY_NAME,
                ANALYZE_INSPECT_CAPABILITY_NAME,
                ANALYZE_VISUALIZATION_CAPABILITY_NAME,
            ),
            schemas=(schema, function_schema, inspect_schema, visualization_schema),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _analysis_target(analysis) -> dict:
    state = analysis_state(analysis)
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
        "expected_member_count": state["member_count"],
    }


def _result_target(result) -> dict:
    state = result_reference_state(result)
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _publish_operation(document, manager, operation, resources=(), owners=()) -> None:
    manager._mark_timeline_operation(operation)
    for resource, owner in zip(resources, owners):
        manager._mark_timeline_resource(resource, owner)
    document.publishProvisionalTimelineOperationBlock(operation, resources, owners)


def _create_result_graph(document):
    from femcommands import manager

    document.openTransaction("Create post-pipeline source graph")
    try:
        model = document.addObject("Part::Feature", "PipelineModel")
        model.Label = "Post Pipeline Model"
        model.Shape = Part.makeBox(16.0, 10.0, 6.0)
        _publish_operation(document, manager, model)

        mesh = document.addObject("Fem::FemMeshObject", "PipelineMesh")
        mesh.Label = "Post Pipeline Mesh"
        fem_mesh = Fem.FemMesh()
        fem_mesh.addNode(0.0, 0.0, 0.0, 1)
        fem_mesh.addNode(16.0, 0.0, 0.0, 2)
        fem_mesh.addNode(0.0, 10.0, 0.0, 3)
        fem_mesh.addNode(0.0, 0.0, 6.0, 4)
        fem_mesh.addVolume([1, 2, 3, 4], 1)
        mesh.FemMesh = fem_mesh
        _publish_operation(document, manager, mesh)

        analysis = ObjectsFem.makeAnalysis(document, "PipelineAnalysis")
        analysis.Label = "Post Pipeline Analysis"
        analysis.addObject(model)
        analysis.addObject(mesh)
        _publish_operation(document, manager, analysis)

        result = ObjectsFem.makeResultMechanical(document, "PipelineResult")
        result.Label = "Post Pipeline Source Result"
        result.Mesh = mesh
        result.NodeNumbers = [1, 2, 3, 4]
        result.DisplacementVectors = [
            App.Vector(0.0, 0.0, 0.0),
            App.Vector(0.2, 0.0, 0.0),
            App.Vector(0.0, 0.4, 0.0),
            App.Vector(0.0, 0.0, 0.6),
        ]
        result.DisplacementLengths = [0.0, 0.2, 0.4, 0.6]
        result.vonMises = [10.0, 20.0, 40.0, 80.0]
        result.Temperature = [290.0, 310.0, 340.0, 370.0]
        stats = list(result.Stats)
        stats[6:10] = [0.0, 0.6, 10.0, 80.0]
        stats[20:22] = [290.0, 370.0]
        result.Stats = stats

        solver = ObjectsFem.makeSolverCalculiX(document, "PipelineSolver")
        solver.Label = "Post Pipeline Solver"
        analysis.addObject(result)
        analysis.addObject(solver)
        solver.Results = [result]
        _publish_operation(document, manager, solver, (result,), (solver,))
        assert document.recompute() is not False
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    model.ViewObject.Visibility = True
    result.ViewObject.Visibility = True
    mesh.ViewObject.Visibility = True
    return model, mesh, analysis, solver, result


def _visibility(document):
    return {
        str(obj.Name): bool(obj.ViewObject.Visibility)
        for obj in tuple(document.Objects)
        if getattr(obj, "ViewObject", None) is not None
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("FemWorkbench")
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-analyze-post-pipeline-"
        )
        output = Path(temporary.name) / "native-analyze-post-pipeline.FCStd"
        document = App.newDocument("NativeAnalyzePostPipelineGate")
        document.UndoMode = 1
        document.saveAs(str(output))
        VibeGui._connect_document_observer()
        controller, surface = _surface(Gui.getMainWindow())
        model, mesh, analysis, solver, result = _create_result_graph(document)
        _events(12)

        frozen = NativeSurfaceSnapshot.from_surface(surface)
        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-analyze-post-pipeline-gui")

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

        initial_result_target = _result_target(result)
        initial_analysis_target = _analysis_target(analysis)
        visibility_before = _visibility(document)
        timeline_before = tuple(obj.Name for obj in document.VibeCADTimeline.Operations)
        ids_before = {obj.Name: int(obj.ID) for obj in document.Objects}
        undo_before = int(document.UndoCount)
        revision_before = state_store.current_revision(str(document.Uid))
        response = dispatcher.call(
            ANALYZE_POST_CAPABILITY_NAME,
            json.dumps(
                {
                    "operation": "create_pipeline",
                    "analysis": initial_analysis_target,
                    "result": initial_result_target,
                    "label": "Verified Result Pipeline",
                },
                separators=(",", ":"),
            ),
            "native-analyze-post-pipeline-create",
        )
        assert response["ok"], response
        pipeline_state = response["created_pipeline"]
        pipeline_name = pipeline_state["object_name"]
        pipeline = document.getObject(pipeline_name)
        assert pipeline is not None
        pipeline_id = int(pipeline.ID)
        assert pipeline.Label == "Verified Result Pipeline"
        assert pipeline in tuple(analysis.Group)
        assert result in tuple(analysis.Group)
        assert pipeline_state["result_kind"] == "pipeline"
        assert pipeline_state["data_available"] is True
        assert pipeline_state["point_count"] == 4
        loaded_field_names = {field["name"] for field in pipeline_state["fields"]}
        assert loaded_field_names >= {
            "Displacement",
            "von Mises Stress",
            "Temperature",
        }, loaded_field_names
        assert tuple(pipeline.VibeCADTimelineReplacedInputs) == (result,)
        assert tuple(obj.Name for obj in document.VibeCADTimeline.Operations) == (
            *timeline_before,
            pipeline_name,
        )
        assert bool(pipeline.ViewObject.Visibility)
        assert not any(
            bool(obj.ViewObject.Visibility)
            for obj in document.Objects
            if obj is not pipeline and getattr(obj, "ViewObject", None) is not None
        )
        assert response["assistant_undo_available"] is True
        assert response["receipt"]["revision_before"] == revision_before
        assert response["receipt"]["revision_after"] == revision_before + 1
        assert int(document.UndoCount) == undo_before + 1
        assert document.getBookedTransactionID() == 0
        assert not document.HasPendingTransaction
        assert len(json.dumps(response, separators=(",", ":")).encode("utf-8")) < 16384
        assert "values" not in response["created_pipeline"]

        visibility_after_pipeline = _visibility(document)
        ids_after_pipeline = {obj.Name: int(obj.ID) for obj in document.Objects}
        branch_response = dispatcher.call(
            ANALYZE_POST_CAPABILITY_NAME,
            json.dumps(
                {
                    "operation": "create_branch",
                    "source": _result_target(pipeline),
                    "label": "Verified Serial Branch",
                    "mode": "serial",
                    "output": "passthrough",
                },
                separators=(",", ":"),
            ),
            "native-analyze-post-branch-create",
        )
        assert branch_response["ok"], branch_response
        branch_state = branch_response["created_branch"]
        branch_name = branch_state["object_name"]
        branch = document.getObject(branch_name)
        assert branch is not None
        branch_id = int(branch.ID)
        assert branch.Label == "Verified Serial Branch"
        assert branch in tuple(pipeline.Group)
        assert branch_state["result_kind"] == "branch_filter"
        assert branch_state["settings"]["Mode"] == "Serial"
        assert branch_state["settings"]["Output"] == "Passthrough"
        assert branch_state["data_available"] is True
        assert branch_state["point_count"] == 4
        assert tuple(branch.VibeCADTimelineReplacedInputs) == (pipeline,)
        assert tuple(obj.Name for obj in document.VibeCADTimeline.Operations) == (
            *timeline_before,
            pipeline_name,
            branch_name,
        )
        assert bool(branch.ViewObject.Visibility)
        assert not bool(pipeline.ViewObject.Visibility)
        assert int(document.UndoCount) == undo_before + 2
        assert branch_response["receipt"]["revision_after"] == (
            branch_response["receipt"]["revision_before"] + 1
        )
        assert len(
            json.dumps(branch_response, separators=(",", ":")).encode("utf-8")
        ) < 16384
        assert "values" not in branch_response["created_branch"]

        visibility_after_branch = _visibility(document)
        ids_after_branch = {obj.Name: int(obj.ID) for obj in document.Objects}
        warp_response = dispatcher.call(
            ANALYZE_POST_CAPABILITY_NAME,
            json.dumps(
                {
                    "operation": "create_warp",
                    "source": _result_target(branch),
                    "label": "Verified Displacement Warp",
                    "vector_field": "Displacement",
                    "factor": 0.01,
                },
                separators=(",", ":"),
            ),
            "native-analyze-post-warp-create",
        )
        assert warp_response["ok"], warp_response
        warp_state = warp_response["created_warp"]
        warp_name = warp_state["object_name"]
        warp = document.getObject(warp_name)
        assert warp is not None
        warp_id = int(warp.ID)
        assert warp.Label == "Verified Displacement Warp"
        assert warp in tuple(branch.Group)
        assert warp_state["result_kind"] == "filter"
        assert warp_state["settings"]["Vector"] == "Displacement"
        assert math.isclose(warp_state["settings"]["Factor"], 0.01)
        assert warp_state["point_count"] == 4
        warped_point = tuple(warp.getDataSet().GetPoint(1))
        assert math.isclose(warped_point[0], 16.002, abs_tol=1e-6), warped_point
        assert tuple(warp.VibeCADTimelineReplacedInputs) == (branch,)
        assert tuple(obj.Name for obj in document.VibeCADTimeline.Operations) == (
            *timeline_before,
            pipeline_name,
            branch_name,
            warp_name,
        )
        assert bool(warp.ViewObject.Visibility)
        assert not bool(branch.ViewObject.Visibility)
        assert int(document.UndoCount) == undo_before + 3
        assert warp_response["receipt"]["revision_after"] == (
            warp_response["receipt"]["revision_before"] + 1
        )
        assert len(
            json.dumps(warp_response, separators=(",", ":")).encode("utf-8")
        ) < 16384
        assert "values" not in warp_response["created_warp"]

        visibility_after_warp = _visibility(document)
        ids_after_warp = {obj.Name: int(obj.ID) for obj in document.Objects}
        clip_response = dispatcher.call(
            ANALYZE_POST_CAPABILITY_NAME,
            json.dumps(
                {
                    "operation": "create_scalar_clip",
                    "source": _result_target(warp),
                    "label": "Verified Stress Clip",
                    "scalar_field": "von Mises Stress",
                    "threshold": 30_000_000.0,
                    "inside_out": False,
                },
                separators=(",", ":"),
            ),
            "native-analyze-post-scalar-clip-create",
        )
        assert clip_response["ok"], clip_response
        clip_state = clip_response["created_scalar_clip"]
        clip_name = clip_state["object_name"]
        clip = document.getObject(clip_name)
        assert clip is not None
        clip_id = int(clip.ID)
        assert clip.Label == "Verified Stress Clip"
        assert tuple(branch.Group) == (warp, clip)
        assert clip_state["result_kind"] == "filter"
        assert clip_state["settings"]["Scalars"] == "von Mises Stress"
        assert math.isclose(clip_state["settings"]["Value"], 30_000_000.0)
        assert clip_state["settings"]["InsideOut"] is False
        assert clip_state["data_available"] is True
        assert clip_state["point_count"] > 0
        clipped_range = tuple(
            clip.getDataSet()
            .GetPointData()
            .GetArray("von Mises Stress")
            .GetRange()
        )
        assert clipped_range[0] >= 30_000_000.0 - 1e-6, clipped_range
        assert clip_response["selected_field"] == {
            "name": "von Mises Stress",
            "source_range": [10_000_000.0, 80_000_000.0],
            "unit": "Pa",
            "threshold": 30_000_000.0,
        }
        assert tuple(clip.VibeCADTimelineReplacedInputs) == (warp,)
        assert tuple(obj.Name for obj in document.VibeCADTimeline.Operations) == (
            *timeline_before,
            pipeline_name,
            branch_name,
            warp_name,
            clip_name,
        )
        assert bool(clip.ViewObject.Visibility)
        assert not bool(warp.ViewObject.Visibility)
        assert int(document.UndoCount) == undo_before + 4
        assert clip_response["receipt"]["revision_after"] == (
            clip_response["receipt"]["revision_before"] + 1
        )
        assert len(
            json.dumps(clip_response, separators=(",", ":")).encode("utf-8")
        ) < 16384
        assert "values" not in clip_response["created_scalar_clip"]

        visibility_after_clip = _visibility(document)
        ids_after_clip = {obj.Name: int(obj.ID) for obj in document.Objects}
        function_specs = (
            (
                "create_plane",
                "Verified Cut Plane",
                {"origin_mm": {"x": 8.0, "y": 5.0, "z": 3.0}, "normal": {"x": 0.0, "y": 0.0, "z": 2.0}},
                "Fem::FemPostPlaneFunction",
                {"PlaneOrigin": [8.0, 5.0, 3.0], "PlaneNormal": [0.0, 0.0, 1.0]},
            ),
            (
                "create_sphere",
                "Verified Clip Sphere",
                {"center_mm": {"x": 8.0, "y": 5.0, "z": 3.0}, "radius_mm": 4.0},
                "Fem::FemPostSphereFunction",
                {"SphereCenter": [8.0, 5.0, 3.0], "SphereRadius": 4.0},
            ),
            (
                "create_cylinder",
                "Verified Clip Cylinder",
                {
                    "center_mm": {"x": 8.0, "y": 5.0, "z": 3.0},
                    "axis": {"x": 0.0, "y": 3.0, "z": 0.0},
                    "radius_mm": 3.0,
                },
                "Fem::FemPostCylinderFunction",
                {
                    "CylinderCenter": [8.0, 5.0, 3.0],
                    "CylinderAxis": [0.0, 1.0, 0.0],
                    "CylinderRadius": 3.0,
                },
            ),
            (
                "create_box",
                "Verified Clip Box",
                {
                    "center_mm": {"x": 8.0, "y": 5.0, "z": 3.0},
                    "length_mm": 8.0,
                    "width_mm": 6.0,
                    "height_mm": 4.0,
                },
                "Fem::FemPostBoxFunction",
                {
                    "BoxCenter": [8.0, 5.0, 3.0],
                    "BoxLength": 8.0,
                    "BoxWidth": 6.0,
                    "BoxHeight": 4.0,
                },
            ),
        )
        function_records = []
        provider = None
        provider_name = ""
        provider_id = -1
        function_names = []
        for index, (operation, label, parameters, type_id, expected_settings) in enumerate(
            function_specs
        ):
            function_response = dispatcher.call(
                ANALYZE_POST_FUNCTION_CAPABILITY_NAME,
                json.dumps(
                    {
                        "operation": operation,
                        "pipeline": _result_target(pipeline),
                        "label": label,
                        **parameters,
                    },
                    separators=(",", ":"),
                ),
                f"native-analyze-post-function-{operation}",
            )
            assert function_response["ok"], function_response
            function_state = function_response["created_function"]
            function = document.getObject(function_state["object_name"])
            assert function is not None
            function_names.append(function.Name)
            function_records.append((function.Name, int(function.ID)))
            assert function.TypeId == type_id
            assert function.Label == label
            assert function_state["result_kind"] == "function"
            assert function_state["post_pipeline_owners"] == [pipeline.Name]
            for property_name, expected in expected_settings.items():
                actual = function_state["settings"][property_name]
                if isinstance(expected, list):
                    assert all(
                        math.isclose(value, target, abs_tol=1e-12)
                        for value, target in zip(actual, expected, strict=True)
                    )
                else:
                    assert math.isclose(actual, expected, abs_tol=1e-12)
            current_provider = document.getObject(
                function_response["function_provider"]["object_name"]
            )
            assert current_provider is not None
            if index == 0:
                assert function_response["provider_created"] is True
                provider = current_provider
                provider_name = provider.Name
                provider_id = int(provider.ID)
                assert str(provider.VibeCADTimelineRole) == "resource"
                assert provider.VibeCADTimelineOwner is pipeline
            else:
                assert function_response["provider_created"] is False
                assert current_provider is provider
            assert function in tuple(provider.Group)
            assert provider in tuple(pipeline.Group)
            assert tuple(provider.Group) == tuple(
                document.getObject(name) for name in function_names
            )
            expected_timeline = (
                *timeline_before,
                provider_name,
                pipeline_name,
                branch_name,
                warp_name,
                clip_name,
                *function_names,
            )
            assert tuple(obj.Name for obj in document.VibeCADTimeline.Operations) == (
                expected_timeline
            )
            assert int(document.UndoCount) == undo_before + 5 + index
            assert len(
                json.dumps(function_response, separators=(",", ":")).encode("utf-8")
            ) < 16384
            assert "values" not in function_response["created_function"]

        assert provider is not None
        assert math.isclose(
            document.getObject(function_names[0]).ViewObject.Scale,
            math.sqrt(16.0**2 + 10.0**2 + 6.0**2),
            abs_tol=1e-9,
        )

        ids_after_functions = {obj.Name: int(obj.ID) for obj in document.Objects}
        visibility_after_functions = _visibility(document)
        cut_response = dispatcher.call(
            ANALYZE_POST_CAPABILITY_NAME,
            json.dumps(
                {
                    "operation": "create_cut",
                    "source": _result_target(clip),
                    "function": _result_target(document.getObject(function_names[0])),
                    "label": "Verified Plane Cut",
                },
                separators=(",", ":"),
            ),
            "native-analyze-post-cut-create",
        )
        assert cut_response["ok"], cut_response
        cut_state = cut_response["created_cut"]
        cut_name = cut_state["object_name"]
        cut = document.getObject(cut_name)
        assert cut is not None
        cut_id = int(cut.ID)
        assert cut.Label == "Verified Plane Cut"
        assert cut.Function is document.getObject(function_names[0])
        assert tuple(branch.Group) == (warp, clip, cut)
        assert cut_state["data_available"] is True
        assert cut_state["point_count"] > 0
        assert tuple(cut.VibeCADTimelineReplacedInputs) == (clip,)
        assert bool(cut.ViewObject.Visibility)
        assert not bool(clip.ViewObject.Visibility)
        assert len(
            json.dumps(cut_response, separators=(",", ":")).encode("utf-8")
        ) < 16384

        region_response = dispatcher.call(
            ANALYZE_POST_CAPABILITY_NAME,
            json.dumps(
                {
                    "operation": "create_region_clip",
                    "source": _result_target(cut),
                    "function": _result_target(document.getObject(function_names[1])),
                    "label": "Verified Spherical Region",
                    "inside_out": False,
                    "cut_cells": True,
                },
                separators=(",", ":"),
            ),
            "native-analyze-post-region-create",
        )
        assert region_response["ok"], region_response
        region_state = region_response["created_region_clip"]
        region_name = region_state["object_name"]
        region = document.getObject(region_name)
        assert region is not None
        region_id = int(region.ID)
        assert region.Label == "Verified Spherical Region"
        assert region.Function is document.getObject(function_names[1])
        assert region.InsideOut is False
        assert region.CutCells is True
        assert tuple(branch.Group) == (warp, clip, cut, region)
        assert region_state["data_available"] is True
        assert region_state["point_count"] > 0
        assert tuple(region.VibeCADTimelineReplacedInputs) == (cut,)
        assert bool(region.ViewObject.Visibility)
        assert not bool(cut.ViewObject.Visibility)
        assert int(document.UndoCount) == undo_before + 10
        assert tuple(obj.Name for obj in document.VibeCADTimeline.Operations) == (
            *timeline_before,
            provider_name,
            pipeline_name,
            branch_name,
            warp_name,
            clip_name,
            *function_names,
            cut_name,
            region_name,
        )
        assert len(
            json.dumps(region_response, separators=(",", ":")).encode("utf-8")
        ) < 16384

        ids_after_region = {obj.Name: int(obj.ID) for obj in document.Objects}
        visibility_after_region = _visibility(document)
        contours_response = dispatcher.call(
            ANALYZE_POST_CAPABILITY_NAME,
            json.dumps(
                {
                    "operation": "create_contours",
                    "source": _result_target(region),
                    "label": "Verified Stress Contours",
                    "field": "von Mises Stress",
                    "component": "scalar",
                    "count": 3,
                    "color_by_field": True,
                    "smoothing": False,
                    "relaxation": 0.05,
                },
                separators=(",", ":"),
            ),
            "native-analyze-post-contours-create",
        )
        assert contours_response["ok"], contours_response
        contours_state = contours_response["created_contours"]
        contours_name = contours_state["object_name"]
        contours = document.getObject(contours_name)
        assert contours is not None
        contours_id = int(contours.ID)
        assert contours.Label == "Verified Stress Contours"
        assert contours.Field == "von Mises Stress"
        assert contours.VectorMode == "Not a vector"
        assert contours.NumberOfContours == 3
        assert contours.NoColor is False
        assert contours.EnableSmoothing is False
        assert math.isclose(contours.RelaxationFactor, 0.05)
        assert tuple(branch.Group) == (warp, clip, cut, region, contours)
        assert contours_state["data_available"] is True
        assert contours_state["point_count"] > 0
        assert contours.ViewObject.Field == "von Mises Stress"
        assert tuple(contours.VibeCADTimelineReplacedInputs) == (region,)
        assert bool(contours.ViewObject.Visibility)
        assert not bool(region.ViewObject.Visibility)
        assert int(document.UndoCount) == undo_before + 11
        assert len(
            json.dumps(contours_response, separators=(",", ":")).encode("utf-8")
        ) < 16384

        ids_after_contours = {obj.Name: int(obj.ID) for obj in document.Objects}
        visibility_after_contours = _visibility(document)
        glyph_response = dispatcher.call(
            ANALYZE_POST_CAPABILITY_NAME,
            json.dumps(
                {
                    "operation": "create_glyphs",
                    "source": _result_target(contours),
                    "label": "Verified Displacement Glyphs",
                    "glyph": "arrow",
                    "orientation": {
                        "mode": "vector_field",
                        "field": "Displacement",
                    },
                    "scaling": {
                        "mode": "vector_magnitude",
                        "field": "Displacement",
                        "factor": 2.0,
                    },
                    "sampling": {"mode": "all"},
                },
                separators=(",", ":"),
            ),
            "native-analyze-post-glyph-create",
        )
        assert glyph_response["ok"], glyph_response
        glyph_state = glyph_response["created_glyph"]
        glyph_name = glyph_state["object_name"]
        glyph_filter = document.getObject(glyph_name)
        assert glyph_filter is not None
        glyph_id = int(glyph_filter.ID)
        assert glyph_filter.Label == "Verified Displacement Glyphs"
        assert tuple(branch.Group) == (warp, clip, cut, region, contours, glyph_filter)
        assert str(glyph_filter.Glyph) == "Arrow"
        assert str(glyph_filter.OrientationData) == "Displacement"
        assert str(glyph_filter.ScaleData) == "Displacement"
        assert str(glyph_filter.VectorScaleMode) == "Scale by magnitude"
        assert math.isclose(float(glyph_filter.ScaleFactor), 2.0)
        assert str(glyph_filter.MaskMode) == "Use All"
        assert glyph_response["glyph"]["shape"] == "arrow"
        assert glyph_response["glyph"]["orientation"] == {
            "mode": "vector_field",
            "field": "Displacement",
        }
        assert glyph_response["glyph"]["scaling"] == {
            "mode": "vector_magnitude",
            "field": "Displacement",
            "factor": 2.0,
        }
        assert glyph_response["glyph"]["sampling"] == {"mode": "all"}
        assert glyph_response["glyph"]["source_point_count"] == contours_state[
            "point_count"
        ]
        assert glyph_response["glyph"]["maximum_glyph_locations"] == contours_state[
            "point_count"
        ]
        assert glyph_response["glyph"]["output_point_count"] > 0
        assert glyph_response["glyph"]["output_cell_count"] > 0
        assert tuple(glyph_filter.VibeCADTimelineReplacedInputs) == (contours,)
        assert bool(glyph_filter.ViewObject.Visibility)
        assert not bool(contours.ViewObject.Visibility)
        assert int(document.UndoCount) == undo_before + 12
        assert len(
            json.dumps(glyph_response, separators=(",", ":")).encode("utf-8")
        ) < 16384

        ids_after_glyph = {obj.Name: int(obj.ID) for obj in document.Objects}
        visibility_after_glyph = _visibility(document)
        calculator_response = dispatcher.call(
            ANALYZE_POST_CAPABILITY_NAME,
            json.dumps(
                {
                    "operation": "create_calculated_field",
                    "source": _result_target(pipeline),
                    "label": "Verified Stress in MPa",
                    "result_field": "Stress MPa",
                    "result_unit": "MPa",
                    "expression": [
                        {
                            "kind": "field",
                            "name": "von Mises Stress",
                            "component": "scalar",
                        },
                        {"kind": "number", "value": 1.0e-6},
                        {"kind": "operator", "operation": "multiply"},
                    ],
                    "invalid_values": {"mode": "reject"},
                },
                separators=(",", ":"),
            ),
            "native-analyze-post-calculator-create",
        )
        assert calculator_response["ok"], calculator_response
        calculator_state = calculator_response["created_calculator"]
        calculator_name = calculator_state["object_name"]
        calculator = document.getObject(calculator_name)
        assert calculator is not None
        calculator_id = int(calculator.ID)
        assert calculator.Label == "Verified Stress in MPa"
        assert calculator in tuple(pipeline.Group)
        assert str(calculator.FieldName) == "Stress MPa"
        assert str(calculator.ResultUnit) == "MPa"
        assert bool(calculator.ReplaceInvalid) is False
        assert calculator_response["result_field"]["name"] == "Stress MPa"
        assert calculator_response["result_field"]["value_type"] == "scalar"
        assert calculator_response["result_field"]["components"] == 1
        assert calculator_response["result_field"]["value_count"] == 4
        assert calculator_response["result_field"]["unit"] == "MPa"
        assert calculator_response["result_field"]["range"] == [10.0, 80.0]
        assert calculator_response["expression"] == {
            "notation": "reverse_polish_tokens",
            "token_count": 3,
            "result_type": "scalar",
            "referenced_fields": ["von Mises Stress"],
        }
        assert "native_function" not in calculator_response["expression"]
        assert tuple(getattr(calculator, "VibeCADTimelineReplacedInputs", ())) == ()
        assert not bool(pipeline.ViewObject.Visibility)
        assert bool(calculator.ViewObject.Visibility)
        assert int(document.UndoCount) == undo_before + 13
        assert len(
            json.dumps(calculator_response, separators=(",", ":")).encode("utf-8")
        ) < 16384

        ids_after_calculator = {obj.Name: int(obj.ID) for obj in document.Objects}
        visibility_after_calculator = _visibility(document)
        line_response = dispatcher.call(
            ANALYZE_POST_CAPABILITY_NAME,
            json.dumps(
                {
                    "operation": "create_line_sample",
                    "source": _result_target(calculator),
                    "label": "Verified Stress Line",
                    "field": "Stress MPa",
                    "component": "scalar",
                    "start_mm": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "end_mm": {"x": 8.0, "y": 0.0, "z": 0.0},
                    "resolution": 4,
                },
                separators=(",", ":"),
            ),
            "native-analyze-post-line-sample-create",
        )
        assert line_response["ok"], line_response
        line_state = line_response["created_line_sample"]
        line_name = line_state["object_name"]
        line_sample = document.getObject(line_name)
        assert line_sample is not None
        line_id = int(line_sample.ID)
        assert line_sample.Label == "Verified Stress Line"
        assert line_sample in tuple(pipeline.Group)
        assert str(line_sample.PlotData) == "Stress MPa"
        assert str(line_sample.PlotDataComponent) == "Scalar"
        assert str(line_sample.Unit) == "MPa"
        assert int(line_sample.Resolution) == 4
        assert tuple(getattr(line_sample, "VibeCADTimelineReplacedInputs", ())) == ()
        assert bool(calculator.ViewObject.Visibility)
        assert bool(line_sample.ViewObject.Visibility)
        assert line_response["sample"]["sample_count"] == 5
        assert line_response["sample"]["valid_sample_count"] == 5
        assert line_response["sample"]["distance_range_mm"] == [0.0, 8.0]
        assert line_response["sample"]["unit"] == "MPa"
        assert math.isclose(
            line_response["sample"]["value_range"][0],
            10.0,
            abs_tol=1e-6,
        )
        assert math.isclose(
            line_response["sample"]["value_range"][1],
            15.0,
            abs_tol=1e-6,
        )
        assert int(document.UndoCount) == undo_before + 14
        assert "XAxisData" not in json.dumps(line_response, separators=(",", ":"))
        assert "YAxisData" not in json.dumps(line_response, separators=(",", ":"))
        assert len(
            json.dumps(line_response, separators=(",", ":")).encode("utf-8")
        ) < 16384

        revision_before_linearization = state_store.current_revision(str(document.Uid))
        undo_before_linearization = int(document.UndoCount)
        linearized_response = dispatcher.call(
            ANALYZE_INSPECT_CAPABILITY_NAME,
            json.dumps(
                {
                    "operation": "linearized_stress",
                    "target": _result_target(line_sample),
                },
                separators=(",", ":"),
            ),
            "native-analyze-post-stress-linearization",
        )
        assert linearized_response["ok"], linearized_response
        assert linearized_response["field"] == "Stress MPa"
        assert linearized_response["unit"] == "MPa"
        assert linearized_response["sample_count"] == 5
        assert math.isclose(linearized_response["thickness_mm"], 8.0, abs_tol=1e-12)
        assert math.isclose(
            linearized_response["membrane"], 12.5, abs_tol=1e-12
        )
        assert math.isclose(
            linearized_response["membrane_plus_bending"]["first_surface"],
            10.0,
            abs_tol=1e-12,
        )
        assert math.isclose(
            linearized_response["membrane_plus_bending"]["second_surface"],
            15.0,
            abs_tol=1e-12,
        )
        assert max(abs(value) for value in linearized_response["peak_residual_range"]) < 1e-6
        assert "receipt" not in linearized_response
        assert int(document.UndoCount) == undo_before_linearization
        assert (
            state_store.current_revision(str(document.Uid))
            == revision_before_linearization
        )
        assert len(
            json.dumps(linearized_response, separators=(",", ":")).encode("utf-8")
        ) < 16384

        ids_after_line = {obj.Name: int(obj.ID) for obj in document.Objects}
        visibility_after_line = _visibility(document)
        point_response = dispatcher.call(
            ANALYZE_POST_CAPABILITY_NAME,
            json.dumps(
                {
                    "operation": "create_point_sample",
                    "source": _result_target(line_sample),
                    "label": "Verified Mid-Line Stress",
                    "field": "Stress MPa",
                    "point_mm": {"x": 4.0, "y": 0.0, "z": 0.0},
                },
                separators=(",", ":"),
            ),
            "native-analyze-post-point-sample-create",
        )
        assert point_response["ok"], point_response
        point_state = point_response["created_point_sample"]
        point_name = point_state["object_name"]
        point_sample = document.getObject(point_name)
        assert point_sample is not None
        point_id = int(point_sample.ID)
        assert point_sample.Label == "Verified Mid-Line Stress"
        assert point_sample in tuple(pipeline.Group)
        assert str(point_sample.FieldName) == "Stress MPa"
        assert str(point_sample.Unit) == "MPa"
        assert tuple(getattr(point_sample, "VibeCADTimelineReplacedInputs", ())) == ()
        assert bool(line_sample.ViewObject.Visibility)
        assert bool(point_sample.ViewObject.Visibility)
        assert point_response["sample"]["valid"] is True
        assert point_response["sample"]["component"] == "scalar"
        assert point_response["sample"]["unit"] == "MPa"
        assert math.isclose(
            point_response["sample"]["value"], 12.5, abs_tol=1e-12
        )
        assert int(document.UndoCount) == undo_before + 15
        assert "PointData" not in json.dumps(point_response, separators=(",", ":"))
        assert len(
            json.dumps(point_response, separators=(",", ":")).encode("utf-8")
        ) < 16384

        ids_after_point = {obj.Name: int(obj.ID) for obj in document.Objects}
        visibility_after_point = _visibility(document)
        table_response = dispatcher.call(
            ANALYZE_VISUALIZATION_CAPABILITY_NAME,
            json.dumps(
                {
                    "operation": "create_table",
                    "analysis": _analysis_target(analysis),
                    "source": _result_target(calculator),
                    "label": "Verified Stress Table",
                    "data": {
                        "mode": "field",
                        "value": {
                            "kind": "field",
                            "name": "Stress MPa",
                            "component": "scalar",
                        },
                        "all_frames": False,
                        "series_name": "Stress",
                    },
                },
                separators=(",", ":"),
            ),
            "native-analyze-post-table-create",
        )
        assert table_response["ok"], table_response
        table_state = table_response["created_visualization"]
        table_name = table_state["object_name"]
        table = document.getObject(table_name)
        table_extractor_name = table_response["extractor"]["object_name"]
        table_extractor = document.getObject(table_extractor_name)
        assert table is not None and table_extractor is not None
        table_id = int(table.ID)
        table_extractor_id = int(table_extractor.ID)
        assert table.Label == "Verified Stress Table"
        assert table in tuple(analysis.Group)
        assert tuple(table.Group) == (table_extractor,)
        assert table_extractor.Source is calculator
        assert str(table.Proxy.VisualizationType) == "Table"
        assert str(table_extractor.Proxy.ExtractionType) == "Field"
        assert str(table_extractor.Proxy.ExtractionDimension) == "1D"
        assert str(table_extractor.XField) == "Stress MPa"
        assert str(table_extractor.XComponent) == "Not a vector"
        assert str(table_extractor.VibeCADTimelineRole) == "resource"
        assert table_extractor.VibeCADTimelineOwner is table
        assert table_response["table"]["row_count"] == 4
        assert table_response["table"]["column_count"] == 1
        assert table_response["table"]["columns"][0]["range"] == [10.0, 80.0]
        assert table_response["extractor"]["data"]["value"]["unit"] == "MPa"
        table.ViewObject.Proxy.show_visualization()
        _events(8)
        assert table.ViewObject.Proxy._tableview.isVisible()
        assert table.ViewObject.Proxy._tableModel.rowCount(QtCore.QModelIndex()) == 4
        table.ViewObject.Proxy._tableview.close()
        assert int(document.UndoCount) == undo_before + 16
        assert len(json.dumps(table_response, separators=(",", ":")).encode()) < 16384
        assert "values" not in json.dumps(table_response, separators=(",", ":"))

        ids_after_table = {obj.Name: int(obj.ID) for obj in document.Objects}
        visibility_after_table = _visibility(document)
        histogram_response = dispatcher.call(
            ANALYZE_VISUALIZATION_CAPABILITY_NAME,
            json.dumps(
                {
                    "operation": "create_histogram",
                    "analysis": _analysis_target(analysis),
                    "source": _result_target(calculator),
                    "label": "Verified Stress Histogram",
                    "data": {
                        "mode": "field",
                        "value": {
                            "kind": "field",
                            "name": "Stress MPa",
                            "component": "scalar",
                        },
                        "all_frames": False,
                        "series_name": "Stress",
                    },
                    "view": {
                        "bins": 4,
                        "type": "bar",
                        "cumulative": False,
                        "bar_width": 0.8,
                        "hatch_line_width": 1.0,
                        "title": "Stress distribution",
                        "x_label": "Stress (MPa)",
                        "y_label": "Count",
                        "legend": {"show": True, "location": "best"},
                    },
                },
                separators=(",", ":"),
            ),
            "native-analyze-post-histogram-create",
        )
        assert histogram_response["ok"], histogram_response
        histogram_name = histogram_response["created_visualization"]["object_name"]
        histogram = document.getObject(histogram_name)
        histogram_extractor_name = histogram_response["extractor"]["object_name"]
        histogram_extractor = document.getObject(histogram_extractor_name)
        assert histogram is not None and histogram_extractor is not None
        histogram_id = int(histogram.ID)
        histogram_extractor_id = int(histogram_extractor.ID)
        assert histogram in tuple(analysis.Group)
        assert tuple(histogram.Group) == (histogram_extractor,)
        assert histogram_extractor.Source is calculator
        assert str(histogram.Proxy.VisualizationType) == "Histogram"
        assert int(histogram.ViewObject.Bins) == 4
        assert str(histogram.ViewObject.Type) == "bar"
        assert not bool(histogram.ViewObject.Cumulative)
        assert math.isclose(float(histogram.ViewObject.BarWidth), 0.8, abs_tol=1e-12)
        assert histogram_response["table"]["row_count"] == 4
        assert histogram_response["table"]["column_count"] == 1
        histogram.ViewObject.Proxy.show_visualization()
        _events(8)
        assert histogram.ViewObject.Proxy._plot.isVisible()
        assert len(histogram.ViewObject.Proxy._plot.axes.patches) == 4
        histogram.ViewObject.Proxy._plot.close()
        assert int(document.UndoCount) == undo_before + 17
        assert len(
            json.dumps(histogram_response, separators=(",", ":")).encode()
        ) < 16384

        ids_after_histogram = {obj.Name: int(obj.ID) for obj in document.Objects}
        visibility_after_histogram = _visibility(document)
        line_plot_response = dispatcher.call(
            ANALYZE_VISUALIZATION_CAPABILITY_NAME,
            json.dumps(
                {
                    "operation": "create_line_plot",
                    "analysis": _analysis_target(analysis),
                    "source": _result_target(line_sample),
                    "label": "Verified Stress Line Plot",
                    "data": {
                        "mode": "field",
                        "x": {
                            "kind": "field",
                            "name": "arc_length",
                            "component": "scalar",
                        },
                        "y": {
                            "kind": "field",
                            "name": "Stress MPa",
                            "component": "scalar",
                        },
                        "all_frames": False,
                        "series_name": "Stress along line",
                    },
                    "view": {
                        "scale": "linear",
                        "grid": True,
                        "title": "Stress along sample line",
                        "x_label": "Distance (mm)",
                        "y_label": "Stress (MPa)",
                        "legend": {"show": True, "location": "best"},
                    },
                },
                separators=(",", ":"),
            ),
            "native-analyze-post-line-plot-create",
        )
        assert line_plot_response["ok"], line_plot_response
        line_plot_name = line_plot_response["created_visualization"]["object_name"]
        line_plot = document.getObject(line_plot_name)
        line_plot_extractor_name = line_plot_response["extractor"]["object_name"]
        line_plot_extractor = document.getObject(line_plot_extractor_name)
        assert line_plot is not None and line_plot_extractor is not None
        line_plot_id = int(line_plot.ID)
        line_plot_extractor_id = int(line_plot_extractor.ID)
        assert line_plot in tuple(analysis.Group)
        assert tuple(line_plot.Group) == (line_plot_extractor,)
        assert line_plot_extractor.Source is line_sample
        assert str(line_plot.Proxy.VisualizationType) == "Lineplot"
        assert str(line_plot_extractor.Proxy.ExtractionDimension) == "2D"
        assert str(line_plot_extractor.XField) == "arc_length"
        assert str(line_plot_extractor.YField) == "Stress MPa"
        assert str(line_plot.ViewObject.Scale) == "linear"
        assert bool(line_plot.ViewObject.Grid)
        assert line_plot_response["table"]["row_count"] == 5
        assert line_plot_response["table"]["column_count"] == 2
        assert line_plot_response["extractor"]["data"]["x"]["unit"] == "mm"
        assert line_plot_response["extractor"]["data"]["y"]["unit"] == "MPa"
        line_plot.ViewObject.Proxy.show_visualization()
        _events(8)
        assert line_plot.ViewObject.Proxy._plot.isVisible()
        assert len(line_plot.ViewObject.Proxy._plot.axes.lines) == 1
        line_plot.ViewObject.Proxy._plot.close()
        assert int(document.UndoCount) == undo_before + 18
        assert len(
            json.dumps(line_plot_response, separators=(",", ":")).encode()
        ) < 16384
        assert tuple(obj.Name for obj in document.VibeCADTimeline.Operations) == (
            *timeline_before,
            provider_name,
            pipeline_name,
            branch_name,
            warp_name,
            clip_name,
            *function_names,
            cut_name,
            region_name,
            contours_name,
            glyph_name,
            calculator_name,
            line_name,
            point_name,
            table_extractor_name,
            table_name,
            histogram_extractor_name,
            histogram_name,
            line_plot_extractor_name,
            line_plot_name,
        )

        document.undo()
        _events(12)
        assert document.getObject(line_plot_name) is None
        assert document.getObject(line_plot_extractor_name) is None
        assert {obj.Name: int(obj.ID) for obj in document.Objects} == ids_after_histogram
        assert _visibility(document) == visibility_after_histogram

        document.undo()
        _events(12)
        assert document.getObject(histogram_name) is None
        assert document.getObject(histogram_extractor_name) is None
        assert {obj.Name: int(obj.ID) for obj in document.Objects} == ids_after_table
        assert _visibility(document) == visibility_after_table

        document.undo()
        _events(12)
        assert document.getObject(table_name) is None
        assert document.getObject(table_extractor_name) is None
        assert {obj.Name: int(obj.ID) for obj in document.Objects} == ids_after_point
        assert _visibility(document) == visibility_after_point

        document.undo()
        _events(12)
        assert document.getObject(point_name) is None
        assert {obj.Name: int(obj.ID) for obj in document.Objects} == ids_after_line
        assert _visibility(document) == visibility_after_line

        document.undo()
        _events(12)
        assert document.getObject(line_name) is None
        assert {obj.Name: int(obj.ID) for obj in document.Objects} == ids_after_calculator
        assert _visibility(document) == visibility_after_calculator

        document.undo()
        _events(12)
        assert document.getObject(calculator_name) is None
        assert {obj.Name: int(obj.ID) for obj in document.Objects} == ids_after_glyph
        assert _visibility(document) == visibility_after_glyph

        document.undo()
        _events(12)
        assert document.getObject(glyph_name) is None
        assert {obj.Name: int(obj.ID) for obj in document.Objects} == ids_after_contours
        assert _visibility(document) == visibility_after_contours

        document.undo()
        _events(12)
        assert document.getObject(contours_name) is None
        assert {obj.Name: int(obj.ID) for obj in document.Objects} == ids_after_region
        assert _visibility(document) == visibility_after_region

        document.undo()
        _events(12)
        assert document.getObject(region_name) is None
        document.undo()
        _events(12)
        assert document.getObject(cut_name) is None
        assert {obj.Name: int(obj.ID) for obj in document.Objects} == ids_after_functions
        assert _visibility(document) == visibility_after_functions

        for function_name, _function_id in reversed(function_records):
            document.undo()
            _events(12)
            assert document.getObject(function_name) is None
        assert document.getObject(provider_name) is None
        assert {obj.Name: int(obj.ID) for obj in document.Objects} == ids_after_clip
        assert _visibility(document) == visibility_after_clip
        assert tuple(obj.Name for obj in document.VibeCADTimeline.Operations) == (
            *timeline_before,
            pipeline_name,
            branch_name,
            warp_name,
            clip_name,
        )

        document.undo()
        _events(16)
        assert document.getObject(clip_name) is None
        assert {obj.Name: int(obj.ID) for obj in document.Objects} == ids_after_warp
        assert _visibility(document) == visibility_after_warp
        assert tuple(obj.Name for obj in document.VibeCADTimeline.Operations) == (
            *timeline_before,
            pipeline_name,
            branch_name,
            warp_name,
        )

        document.undo()
        _events(16)
        assert document.getObject(warp_name) is None
        assert {obj.Name: int(obj.ID) for obj in document.Objects} == ids_after_branch
        assert _visibility(document) == visibility_after_branch
        assert tuple(obj.Name for obj in document.VibeCADTimeline.Operations) == (
            *timeline_before,
            pipeline_name,
            branch_name,
        )

        document.undo()
        _events(16)
        assert document.getObject(branch_name) is None
        assert {obj.Name: int(obj.ID) for obj in document.Objects} == ids_after_pipeline
        assert _visibility(document) == visibility_after_pipeline
        assert tuple(obj.Name for obj in document.VibeCADTimeline.Operations) == (
            *timeline_before,
            pipeline_name,
        )

        document.undo()
        _events(16)
        assert document.getObject(pipeline_name) is None
        assert {obj.Name: int(obj.ID) for obj in document.Objects} == ids_before
        assert _visibility(document) == visibility_before
        assert tuple(obj.Name for obj in document.VibeCADTimeline.Operations) == timeline_before

        document.redo()
        _events(16)
        pipeline = document.getObject(pipeline_name)
        assert pipeline is not None and int(pipeline.ID) == pipeline_id
        assert document.getObject(result.Name) is result
        assert result_state(pipeline)["point_count"] == 4
        assert tuple(pipeline.VibeCADTimelineReplacedInputs) == (result,)
        assert bool(pipeline.ViewObject.Visibility)
        assert not bool(result.ViewObject.Visibility)

        document.redo()
        _events(16)
        branch = document.getObject(branch_name)
        assert branch is not None and int(branch.ID) == branch_id
        assert branch in tuple(pipeline.Group)
        assert result_state(branch)["point_count"] == 4
        assert tuple(branch.VibeCADTimelineReplacedInputs) == (pipeline,)
        assert bool(branch.ViewObject.Visibility)
        assert not bool(pipeline.ViewObject.Visibility)

        document.redo()
        _events(16)
        warp = document.getObject(warp_name)
        assert warp is not None and int(warp.ID) == warp_id
        assert warp in tuple(branch.Group)
        assert result_state(warp)["point_count"] == 4
        assert math.isclose(warp.getDataSet().GetPoint(1)[0], 16.002, abs_tol=1e-6)
        assert tuple(warp.VibeCADTimelineReplacedInputs) == (branch,)
        assert bool(warp.ViewObject.Visibility)
        assert not bool(branch.ViewObject.Visibility)

        document.redo()
        _events(16)
        clip = document.getObject(clip_name)
        assert clip is not None and int(clip.ID) == clip_id
        assert tuple(branch.Group) == (warp, clip)
        assert result_state(clip)["point_count"] > 0
        assert tuple(clip.VibeCADTimelineReplacedInputs) == (warp,)
        assert bool(clip.ViewObject.Visibility)
        assert not bool(warp.ViewObject.Visibility)

        for function_name, function_id in function_records:
            document.redo()
            _events(12)
            function = document.getObject(function_name)
            assert function is not None and int(function.ID) == function_id
        provider = document.getObject(provider_name)
        assert provider is not None and int(provider.ID) == provider_id
        assert provider in tuple(pipeline.Group)
        assert tuple(provider.Group) == tuple(
            document.getObject(name) for name in function_names
        )
        assert str(provider.VibeCADTimelineRole) == "resource"
        assert provider.VibeCADTimelineOwner is pipeline

        document.redo()
        _events(12)
        cut = document.getObject(cut_name)
        assert cut is not None and int(cut.ID) == cut_id
        assert cut.Function is document.getObject(function_names[0])
        assert result_state(cut)["point_count"] > 0
        document.redo()
        _events(12)
        region = document.getObject(region_name)
        assert region is not None and int(region.ID) == region_id
        assert region.Function is document.getObject(function_names[1])
        assert result_state(region)["point_count"] > 0
        assert tuple(branch.Group) == (warp, clip, cut, region)
        assert bool(region.ViewObject.Visibility)
        assert not bool(cut.ViewObject.Visibility)
        document.redo()
        _events(12)
        contours = document.getObject(contours_name)
        assert contours is not None and int(contours.ID) == contours_id
        assert result_state(contours)["point_count"] > 0
        assert tuple(branch.Group) == (warp, clip, cut, region, contours)
        assert tuple(contours.VibeCADTimelineReplacedInputs) == (region,)
        assert bool(contours.ViewObject.Visibility)
        assert not bool(region.ViewObject.Visibility)

        document.redo()
        _events(12)
        glyph_filter = document.getObject(glyph_name)
        assert glyph_filter is not None and int(glyph_filter.ID) == glyph_id
        assert tuple(branch.Group) == (
            warp,
            clip,
            cut,
            region,
            contours,
            glyph_filter,
        )
        assert str(glyph_filter.Glyph) == "Arrow"
        assert str(glyph_filter.OrientationData) == "Displacement"
        assert str(glyph_filter.ScaleData) == "Displacement"
        assert str(glyph_filter.VectorScaleMode) == "Scale by magnitude"
        assert result_state(glyph_filter)["point_count"] > 0
        assert tuple(glyph_filter.VibeCADTimelineReplacedInputs) == (contours,)
        assert bool(glyph_filter.ViewObject.Visibility)
        assert not bool(contours.ViewObject.Visibility)

        document.redo()
        _events(12)
        calculator = document.getObject(calculator_name)
        assert calculator is not None and int(calculator.ID) == calculator_id
        assert calculator in tuple(pipeline.Group)
        assert str(calculator.FieldName) == "Stress MPa"
        assert str(calculator.ResultUnit) == "MPa"
        calculator_fields = {
            field["name"]: field for field in result_state(calculator)["fields"]
        }
        assert calculator_fields["Stress MPa"]["unit"] == "MPa"
        assert calculator_fields["Stress MPa"]["range"] == [10.0, 80.0]
        assert tuple(getattr(calculator, "VibeCADTimelineReplacedInputs", ())) == ()
        assert bool(calculator.ViewObject.Visibility)
        assert not bool(pipeline.ViewObject.Visibility)

        document.redo()
        _events(12)
        line_sample = document.getObject(line_name)
        assert line_sample is not None and int(line_sample.ID) == line_id
        assert line_sample in tuple(pipeline.Group)
        assert tuple(getattr(line_sample, "VibeCADTimelineReplacedInputs", ())) == ()
        assert len(tuple(line_sample.XAxisData)) == 5
        assert len(tuple(line_sample.YAxisData)) == 5
        assert str(line_sample.Unit) == "MPa"
        assert bool(calculator.ViewObject.Visibility)
        assert bool(line_sample.ViewObject.Visibility)
        assert not bool(pipeline.ViewObject.Visibility)

        document.redo()
        _events(12)
        point_sample = document.getObject(point_name)
        assert point_sample is not None and int(point_sample.ID) == point_id
        assert point_sample in tuple(pipeline.Group)
        assert tuple(getattr(point_sample, "VibeCADTimelineReplacedInputs", ())) == ()
        assert len(tuple(point_sample.PointData)) == 1
        assert bool(line_sample.ViewObject.Visibility)
        assert bool(point_sample.ViewObject.Visibility)

        document.redo()
        _events(12)
        table = document.getObject(table_name)
        table_extractor = document.getObject(table_extractor_name)
        assert table is not None and int(table.ID) == table_id
        assert table_extractor is not None and int(table_extractor.ID) == table_extractor_id
        assert table in tuple(analysis.Group)
        assert tuple(table.Group) == (table_extractor,)
        assert table_extractor.Source is calculator
        assert table.Table.GetNumberOfRows() == 4
        assert table.Table.GetNumberOfColumns() == 1
        assert table_extractor.VibeCADTimelineOwner is table

        document.redo()
        _events(12)
        histogram = document.getObject(histogram_name)
        histogram_extractor = document.getObject(histogram_extractor_name)
        assert histogram is not None and int(histogram.ID) == histogram_id
        assert (
            histogram_extractor is not None
            and int(histogram_extractor.ID) == histogram_extractor_id
        )
        assert histogram in tuple(analysis.Group)
        assert tuple(histogram.Group) == (histogram_extractor,)
        assert histogram_extractor.Source is calculator
        assert histogram.Table.GetNumberOfRows() == 4
        assert histogram.Table.GetNumberOfColumns() == 1
        assert int(histogram.ViewObject.Bins) == 4
        assert histogram_extractor.VibeCADTimelineOwner is histogram

        document.redo()
        _events(12)
        line_plot = document.getObject(line_plot_name)
        line_plot_extractor = document.getObject(line_plot_extractor_name)
        assert line_plot is not None and int(line_plot.ID) == line_plot_id
        assert (
            line_plot_extractor is not None
            and int(line_plot_extractor.ID) == line_plot_extractor_id
        )
        assert line_plot in tuple(analysis.Group)
        assert tuple(line_plot.Group) == (line_plot_extractor,)
        assert line_plot_extractor.Source is line_sample
        assert line_plot.Table.GetNumberOfRows() == 5
        assert line_plot.Table.GetNumberOfColumns() == 2
        assert str(line_plot.ViewObject.Scale) == "linear"
        assert line_plot_extractor.VibeCADTimelineOwner is line_plot

        names = {
            "model": model.Name,
            "mesh": mesh.Name,
            "analysis": analysis.Name,
            "solver": solver.Name,
            "result": result.Name,
            "pipeline": pipeline.Name,
            "branch": branch.Name,
            "warp": warp.Name,
            "clip": clip.Name,
            "provider": provider.Name,
            "cut": cut.Name,
            "region": region.Name,
            "contours": contours.Name,
            "glyph": glyph_filter.Name,
            "calculator": calculator.Name,
            "line_sample": line_sample.Name,
            "point_sample": point_sample.Name,
            "table": table.Name,
            "table_extractor": table_extractor.Name,
            "histogram": histogram.Name,
            "histogram_extractor": histogram_extractor.Name,
            "line_plot": line_plot.Name,
            "line_plot_extractor": line_plot_extractor.Name,
            **{
                f"function_{index}": name
                for index, name in enumerate(function_names)
            },
        }
        document.recompute()
        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(output))
        _events(20)
        reopened_pipeline = document.getObject(names["pipeline"])
        reopened_branch = document.getObject(names["branch"])
        reopened_warp = document.getObject(names["warp"])
        reopened_clip = document.getObject(names["clip"])
        reopened_provider = document.getObject(names["provider"])
        reopened_cut = document.getObject(names["cut"])
        reopened_region = document.getObject(names["region"])
        reopened_contours = document.getObject(names["contours"])
        reopened_glyph = document.getObject(names["glyph"])
        reopened_calculator = document.getObject(names["calculator"])
        reopened_line_sample = document.getObject(names["line_sample"])
        reopened_point_sample = document.getObject(names["point_sample"])
        reopened_table = document.getObject(names["table"])
        reopened_table_extractor = document.getObject(names["table_extractor"])
        reopened_histogram = document.getObject(names["histogram"])
        reopened_histogram_extractor = document.getObject(
            names["histogram_extractor"]
        )
        reopened_line_plot = document.getObject(names["line_plot"])
        reopened_line_plot_extractor = document.getObject(
            names["line_plot_extractor"]
        )
        reopened_result = document.getObject(names["result"])
        reopened_analysis = document.getObject(names["analysis"])
        assert all(document.getObject(name) is not None for name in names.values())
        assert reopened_pipeline in tuple(reopened_analysis.Group)
        assert reopened_result in tuple(reopened_analysis.Group)
        assert reopened_branch in tuple(reopened_pipeline.Group)
        assert reopened_warp in tuple(reopened_branch.Group)
        assert tuple(reopened_branch.Group) == (
            reopened_warp,
            reopened_clip,
            reopened_cut,
            reopened_region,
            reopened_contours,
            reopened_glyph,
        )
        assert reopened_provider in tuple(reopened_pipeline.Group)
        assert reopened_calculator in tuple(reopened_pipeline.Group)
        assert reopened_line_sample in tuple(reopened_pipeline.Group)
        assert reopened_point_sample in tuple(reopened_pipeline.Group)
        assert reopened_table in tuple(reopened_analysis.Group)
        assert reopened_histogram in tuple(reopened_analysis.Group)
        assert reopened_line_plot in tuple(reopened_analysis.Group)
        assert tuple(reopened_table.Group) == (reopened_table_extractor,)
        assert tuple(reopened_histogram.Group) == (reopened_histogram_extractor,)
        assert tuple(reopened_line_plot.Group) == (reopened_line_plot_extractor,)
        assert reopened_table_extractor.Source is reopened_calculator
        assert reopened_histogram_extractor.Source is reopened_calculator
        assert reopened_line_plot_extractor.Source is reopened_line_sample
        assert reopened_table.Table.GetNumberOfRows() == 4
        assert reopened_table.Table.GetNumberOfColumns() == 1
        assert reopened_histogram.Table.GetNumberOfRows() == 4
        assert reopened_histogram.Table.GetNumberOfColumns() == 1
        assert reopened_line_plot.Table.GetNumberOfRows() == 5
        assert reopened_line_plot.Table.GetNumberOfColumns() == 2
        assert int(reopened_histogram.ViewObject.Bins) == 4
        assert str(reopened_line_plot.ViewObject.Scale) == "linear"
        assert reopened_table_extractor.VibeCADTimelineOwner is reopened_table
        assert reopened_histogram_extractor.VibeCADTimelineOwner is reopened_histogram
        assert reopened_line_plot_extractor.VibeCADTimelineOwner is reopened_line_plot
        assert tuple(reopened_provider.Group) == tuple(
            document.getObject(names[f"function_{index}"])
            for index in range(len(function_names))
        )
        assert reopened_provider.VibeCADTimelineOwner is reopened_pipeline
        assert tuple(reopened_pipeline.VibeCADTimelineReplacedInputs) == (
            reopened_result,
        )
        assert result_state(reopened_pipeline)["point_count"] == 4
        assert result_state(reopened_branch)["point_count"] == 4
        assert tuple(reopened_branch.VibeCADTimelineReplacedInputs) == (
            reopened_pipeline,
        )
        assert result_state(reopened_warp)["point_count"] == 4
        assert tuple(reopened_warp.VibeCADTimelineReplacedInputs) == (
            reopened_branch,
        )
        assert result_state(reopened_clip)["point_count"] > 0
        assert tuple(reopened_clip.VibeCADTimelineReplacedInputs) == (
            reopened_warp,
        )
        assert reopened_cut.Function is document.getObject(names["function_0"])
        assert reopened_region.Function is document.getObject(names["function_1"])
        assert result_state(reopened_cut)["point_count"] > 0
        assert result_state(reopened_region)["point_count"] > 0
        assert result_state(reopened_contours)["point_count"] > 0
        assert tuple(reopened_contours.VibeCADTimelineReplacedInputs) == (
            reopened_region,
        )
        assert str(reopened_glyph.Glyph) == "Arrow"
        assert str(reopened_glyph.OrientationData) == "Displacement"
        assert str(reopened_glyph.ScaleData) == "Displacement"
        assert str(reopened_glyph.VectorScaleMode) == "Scale by magnitude"
        assert result_state(reopened_glyph)["point_count"] > 0
        assert tuple(reopened_glyph.VibeCADTimelineReplacedInputs) == (
            reopened_contours,
        )
        assert str(reopened_calculator.FieldName) == "Stress MPa"
        assert str(reopened_calculator.ResultUnit) == "MPa"
        assert result_state(reopened_calculator)["point_count"] == 4
        reopened_calculator_fields = {
            field["name"]: field
            for field in result_state(reopened_calculator)["fields"]
        }
        assert reopened_calculator_fields["Stress MPa"]["unit"] == "MPa"
        assert reopened_calculator_fields["Stress MPa"]["range"] == [10.0, 80.0]
        assert tuple(
            getattr(reopened_calculator, "VibeCADTimelineReplacedInputs", ())
        ) == ()
        assert tuple(
            getattr(reopened_line_sample, "VibeCADTimelineReplacedInputs", ())
        ) == ()
        assert tuple(
            getattr(reopened_point_sample, "VibeCADTimelineReplacedInputs", ())
        ) == ()
        assert str(reopened_line_sample.PlotData) == "Stress MPa"
        assert str(reopened_line_sample.Unit) == "MPa"
        assert len(tuple(reopened_line_sample.XAxisData)) == 5
        assert len(tuple(reopened_line_sample.YAxisData)) == 5
        assert str(reopened_point_sample.FieldName) == "Stress MPa"
        assert str(reopened_point_sample.Unit) == "MPa"
        assert len(tuple(reopened_point_sample.PointData)) == 1
        assert bool(reopened_line_sample.ViewObject.Visibility)
        assert bool(reopened_point_sample.ViewObject.Visibility)
        assert bool(reopened_glyph.ViewObject.Visibility)
        assert not bool(reopened_contours.ViewObject.Visibility)
        print(
            "VIBECAD_NATIVE_ANALYZE_POST_PIPELINE_GUI_OK actions=19 "
            "exact_result=true exact_analysis=true exact_post_source=true data=true "
            "vector_field=true warp_geometry=true scalar_field=true range=true "
            "post_functions=4 normalized_directions=true provider_resource=true "
            "implicit_cut=true region_clip=true same_pipeline_functions=true "
            "contours=true contour_presentation=true glyphs=true bounded_glyphs=true "
            "source_preserving_samples=true "
            "typed_calculator=true durable_units=true stress_linearization=true "
            "table=true histogram=true line_plot=true rendered_visualizations=true "
            "compact_sample_results=true "
            "no_arrays=true history=true one_transaction_each=true undo_redo=true "
            "reopen=true",
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
