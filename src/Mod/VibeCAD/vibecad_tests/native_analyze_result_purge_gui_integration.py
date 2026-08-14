# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-host gate for exact, atomic FEM result-graph purge."""

from __future__ import annotations

import json
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
from VibeCADNativeAnalyzeResults import result_purge_state
from VibeCADNativeAnalyzeResultsSchema import ANALYZE_RESULTS_CAPABILITY_NAME
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
    assert "FEM_ResultsPurge" in surface.command_ids
    return controller, surface


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(ANALYZE_RESULTS_CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(("purge",))
    variant = schema["parameters"]["oneOf"][0]
    assert variant["properties"]["operation"]["const"] == "purge"
    assert set(variant["required"]) == {
        "operation",
        "analysis",
        "expected_result_graph_sha256",
        "expected_result_object_count",
    }
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(ANALYZE_RESULTS_CAPABILITY_NAME,),
            schemas=(schema,),
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


def _publish_operation(document, manager, operation, resources=(), owners=()) -> None:
    manager._mark_timeline_operation(operation)
    for resource, owner in zip(resources, owners):
        manager._mark_timeline_resource(resource, owner)
    document.publishProvisionalTimelineOperationBlock(operation, resources, owners)


def _create_base_graph(document):
    from femcommands import manager

    document.openTransaction("Create result purge base graph")
    try:
        model = document.addObject("Part::Feature", "PurgeGateModel")
        model.Label = "Retained Analysis Model"
        model.Shape = Part.makeBox(20.0, 10.0, 4.0)
        _publish_operation(document, manager, model)

        mesh = document.addObject("Fem::FemMeshObject", "PurgeGateMesh")
        mesh.Label = "Retained Analysis Mesh"
        fem_mesh = Fem.FemMesh()
        fem_mesh.addNode(0.0, 0.0, 0.0, 1)
        fem_mesh.addNode(20.0, 0.0, 0.0, 2)
        fem_mesh.addNode(0.0, 10.0, 0.0, 3)
        fem_mesh.addNode(0.0, 0.0, 4.0, 4)
        fem_mesh.addVolume([1, 2, 3, 4], 1)
        mesh.FemMesh = fem_mesh
        _publish_operation(document, manager, mesh)

        analysis = ObjectsFem.makeAnalysis(document, "PurgeGateAnalysis")
        analysis.Label = "Result Purge Analysis"
        analysis.addObject(mesh)
        _publish_operation(document, manager, analysis)

        solver = ObjectsFem.makeSolverElmer(document, "PurgeGateSolver")
        solver.Label = "Retained Elmer Solver"
        analysis.addObject(solver)
        equation = ObjectsFem.makeEquationElasticity(
            document,
            solver,
            "PurgeGateEquation",
        )
        equation.Label = "Retained Elasticity Equation"
        _publish_operation(document, manager, solver, (equation,), (solver,))

        assert document.recompute([model, mesh, analysis, equation, solver], True, True) \
            is not False
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    return model, mesh, analysis, solver, equation


def _create_solver_result_graph(document, analysis, solver):
    from femcommands import manager

    document.openTransaction("Create solver-owned result graph")
    try:
        reconciliation = manager._stage_timeline_result_graph(solver)
        root = document.addObject("Fem::FemPostPipeline", "SolverResultPipeline")
        root.Label = "Solver-Owned Result Root"
        output = document.addObject("App::TextDocument", "SolverResultOutput")
        output.Label = "Solver-Owned Output"
        output.Text = "verified solver output"
        analysis.addObject(root)
        analysis.addObject(output)
        solver.Results = [root, output]
        manager._finalize_timeline_result_graph(
            solver,
            root,
            (output,),
            root_is_new=True,
            reconciliation=reconciliation,
        )
        assert document.recompute([root, output, solver, analysis], True, True) is not False
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    return root, output


def _create_post_graph(document, analysis, solver_result):
    from femcommands import manager

    document.openTransaction("Create independent post-processing graph")
    try:
        pipeline = document.addObject("Fem::FemPostPipeline", "DisplayPipeline")
        pipeline.Label = "Independent Display Pipeline"
        pipeline.addProperty(
            "App::PropertyLink",
            "SourceResult",
            "Post Processing",
            "Exact source result",
        )
        pipeline.SourceResult = solver_result
        analysis.addObject(pipeline)
        warp = ObjectsFem.makePostVtkFilterWarp(document, pipeline, "DisplayWarp")
        warp.Label = "Independent Warp Filter"
        warp.Factor = 1.75
        _publish_operation(document, manager, pipeline, (warp,), (pipeline,))
        assert document.recompute([warp, pipeline, analysis], True, True) is not False
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    return pipeline, warp


def _add_late_result(document, analysis):
    document.openTransaction("Add concurrent result artifact")
    try:
        report = document.addObject("App::TextDocument", "LateResultReport")
        report.Label = "Late Result Report"
        report.Text = "newer result artifact"
        analysis.addObject(report)
        assert document.recompute([report, analysis], True, True) is not False
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    return report


def _exact_objects(document, names):
    return {name: document.getObject(name) for name in names}


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("FemWorkbench")
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-analyze-result-purge-"
        )
        output = Path(temporary.name) / "native-analyze-result-purge.FCStd"
        document = App.newDocument("NativeAnalyzeResultPurgeGate")
        document.UndoMode = 1
        document.saveAs(str(output))
        VibeGui._connect_document_observer()
        controller, surface = _surface(Gui.getMainWindow())
        model, mesh, analysis, solver, equation = _create_base_graph(document)
        solver_result, solver_output = _create_solver_result_graph(
            document,
            analysis,
            solver,
        )
        post_pipeline, post_filter = _create_post_graph(
            document,
            analysis,
            solver_result,
        )
        _events(16)

        frozen = NativeSurfaceSnapshot.from_surface(surface)
        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-analyze-result-purge-gui")

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
                ANALYZE_RESULTS_CAPABILITY_NAME,
                json.dumps(arguments, separators=(",", ":")),
                f"native-analyze-result-purge-{call_number}",
            )
            assert response.get("ok") is succeeds, response
            return response

        initial = result_purge_state(analysis)
        assert initial["purge_ready"]
        assert initial["solver_result_root_count"] == 1
        assert initial["ordinary_operation_count"] == 1
        assert initial["object_count"] == 4
        late_report = _add_late_result(document, analysis)
        _events(8)
        revision_before_stale = state_store.current_revision(str(document.Uid))
        stale = call(
            {
                "operation": "purge",
                "analysis": _analysis_target(analysis),
                "expected_result_graph_sha256": initial["graph_sha256"],
                "expected_result_object_count": initial["object_count"],
            },
            succeeds=False,
        )
        assert stale["error_code"] == "NATIVE_ANALYZE_STATE_STALE", stale
        assert state_store.current_revision(str(document.Uid)) == revision_before_stale

        current = result_purge_state(analysis)
        assert current["object_count"] == 5
        result_names = (
            solver_result.Name,
            solver_output.Name,
            post_pipeline.Name,
            post_filter.Name,
            late_report.Name,
        )
        result_ids = {name: int(document.getObject(name).ID) for name in result_names}
        retained_names = (model.Name, mesh.Name, analysis.Name, equation.Name, solver.Name)
        model_name, mesh_name, analysis_name, equation_name, solver_name = retained_names
        retained_ids = {name: int(document.getObject(name).ID) for name in retained_names}
        timeline_before = tuple(obj.Name for obj in document.VibeCADTimeline.Operations)
        undo_before = int(document.UndoCount)
        revision_before_purge = state_store.current_revision(str(document.Uid))
        purged = call(
            {
                "operation": "purge",
                "analysis": _analysis_target(analysis),
                "expected_result_graph_sha256": current["graph_sha256"],
                "expected_result_object_count": current["object_count"],
            }
        )
        assert purged["purged"] == {
            "object_count": 5,
            "result_graph_sha256": current["graph_sha256"],
        }
        assert purged["result_graph"]["object_count"] == 0
        assert purged["result_graph"]["purge_ready"] is False
        assert purged["assistant_undo_available"] is True
        assert int(document.UndoCount) == undo_before + 1
        assert purged["receipt"]["revision_after"] == revision_before_purge + 1
        assert {item["object_name"] for item in purged["receipt"]["deleted"]} == set(
            result_names
        )
        assert {item["object_name"] for item in purged["receipt"]["changed"]} == {
            analysis.Name,
            solver.Name,
        }
        assert all(document.getObject(name) is None for name in result_names)
        assert {
            name: int(document.getObject(name).ID) for name in retained_names
        } == retained_ids
        assert tuple(solver.Results) == ()
        assert equation in tuple(solver.Group)
        assert mesh in tuple(analysis.Group)
        assert tuple(obj.Name for obj in document.VibeCADTimeline.Operations) == retained_names
        assert document.getBookedTransactionID() == 0
        assert not document.HasPendingTransaction

        document.undo()
        _events(12)
        restored_results = _exact_objects(document, result_names)
        assert all(restored_results.values())
        assert {name: int(obj.ID) for name, obj in restored_results.items()} == result_ids
        restored_solver = document.getObject(solver.Name)
        restored_equation = document.getObject(equation.Name)
        assert tuple(obj.Name for obj in restored_solver.Results) == (
            solver_result.Name,
            solver_output.Name,
        )
        assert restored_equation in tuple(restored_solver.Group)
        assert tuple(obj.Name for obj in document.VibeCADTimeline.Operations) == timeline_before

        document.redo()
        _events(12)
        assert all(document.getObject(name) is None for name in result_names)
        assert tuple(document.getObject(solver.Name).Results) == ()
        assert tuple(obj.Name for obj in document.VibeCADTimeline.Operations) == retained_names

        document.recompute()
        document.save()
        document_name = document.Name
        App.closeDocument(document_name)
        document = App.openDocument(str(output))
        _events(20)
        reopened_analysis = document.getObject(analysis_name)
        reopened_solver = document.getObject(solver_name)
        assert result_purge_state(reopened_analysis)["object_count"] == 0
        assert tuple(reopened_solver.Results) == ()
        assert document.getObject(equation_name) in tuple(reopened_solver.Group)
        assert document.getObject(mesh_name) in tuple(reopened_analysis.Group)
        assert {
            name: int(document.getObject(name).ID) for name in retained_names
        } == retained_ids
        assert tuple(obj.Name for obj in document.VibeCADTimeline.Operations) == retained_names
        print(
            "VIBECAD_NATIVE_ANALYZE_RESULT_PURGE_GUI_OK "
            "action=1 exact_graph=true nested_solver_results=true "
            "post_processing=true stale_rejection=true retained_inputs=true "
            "history=true one_transaction=true undo_redo=true reopen=true",
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
