# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for detached Native FEM solver execution."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import ObjectsFem
import VibeCADGui as VibeGui
import VibeCADNativeAnalyzeSolverExecution as execution_module
from VibeCADCore import get_service
from VibeCADNativeAnalyzeSolverExecutionSchema import (
    ANALYZE_SOLVER_EXECUTION_CAPABILITY_NAME,
)
from VibeCADNativeAnalyzeSolverState import solver_state
from VibeCADNativeBackgroundSchema import NATIVE_BACKGROUND_CAPABILITY_NAME
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
    index = next(i for i in range(tabs.count()) if str(tabs.tabData(i)) == "FemWorkbench")
    tabs.setCurrentIndex(index)
    _events(24)
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "analyze"
    assert "FEM_SolverRun" in surface.command_ids
    return controller, surface


def _turn(surface, registry) -> NativeTurnSnapshot:
    execution = registry.definition(ANALYZE_SOLVER_EXECUTION_CAPABILITY_NAME)
    jobs = registry.definition(NATIVE_BACKGROUND_CAPABILITY_NAME)
    assert execution is not None and jobs is not None
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(
                NATIVE_BACKGROUND_CAPABILITY_NAME,
                ANALYZE_SOLVER_EXECUTION_CAPABILITY_NAME,
            ),
            schemas=(
                jobs.provider_schema(("status", "cancel")),
                execution.provider_schema(("run",)),
            ),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _solver_target(state: dict) -> dict:
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _create_analysis(document):
    from femexamples.ccx_cantilever_faceload import setup

    setup(document, solvertype="ccxtools", test_mode=True)
    analysis = document.getObject("Analysis")
    old_solver = document.getObject("CalculiXCcxTools")
    analysis.removeObject(old_solver)
    document.removeObject(old_solver.Name)
    document.recompute()

    def create(factory, name, label):
        document.openTransaction("Create detached execution solver")
        try:
            solver = factory(document, name)
            solver.Label = label
            analysis.addObject(solver)
            from femcommands import manager

            manager._mark_timeline_operation(solver)
            document.publishProvisionalTimelineOperationBlock(solver, (), ())
            document.recompute([solver, analysis], True, True)
            document.commitTransaction()
        except Exception:
            document.abortTransaction()
            raise
        return solver

    pipeline = create(
        ObjectsFem.makeSolverCalculiX,
        "DetachedCalculiX",
        "Detached CalculiX",
    )
    ccx_tools = create(
        ObjectsFem.makeSolverCalculiXCcxTools,
        "DetachedCcxTools",
        "Detached CalculiX Standard",
    )
    return analysis, pipeline, ccx_tools


def _result_grid():
    from vtkmodules.vtkCommonCore import vtkDoubleArray, vtkPoints
    from vtkmodules.vtkCommonDataModel import vtkTetra, vtkUnstructuredGrid

    points = vtkPoints()
    for point in ((0, 0, 0), (10, 0, 0), (0, 10, 0), (0, 0, 10)):
        points.InsertNextPoint(*point)
    tetrahedron = vtkTetra()
    for index in range(4):
        tetrahedron.GetPointIds().SetId(index, index)
    grid = vtkUnstructuredGrid()
    grid.SetPoints(points)
    grid.InsertNextCell(tetrahedron.GetCellType(), tetrahedron.GetPointIds())
    displacement = vtkDoubleArray()
    displacement.SetName("Displacement")
    displacement.SetNumberOfComponents(3)
    for value in ((0, 0, 0), (0.1, 0, 0), (0, 0.2, 0), (0, 0, 0.3)):
        displacement.InsertNextTuple3(*value)
    grid.GetPointData().AddArray(displacement)
    return grid


class _FakeResultImporter:
    def __init__(self, solver, *, replace_existing=False) -> None:
        self.solver = solver
        self.replace_existing = replace_existing

    def update_properties(self):
        from femcommands.manager import _stage_timeline_result_graph

        replacement_roots = (
            tuple(
                result
                for result in tuple(self.solver.Results or ())
                if getattr(result, "VibeCADTimelineOwner", None) is self.solver
            )
            if self.replace_existing
            else ()
        )
        reconciliation = _stage_timeline_result_graph(
            self.solver,
            replacement_roots=replacement_roots,
        )
        document = self.solver.Document
        root = document.addObject(
            "Fem::FemPostPipeline",
            self.solver.Name + "Result",
        )
        root.Label = "Detached Verified Result"
        root.Data = _result_grid()
        output = document.addObject(
            "App::TextDocument",
            self.solver.Name + "Output",
        )
        output.Label = "Detached Verified Output"
        output.Text = "Detached solver output"
        analysis = self.solver.getParentGroup()
        analysis.addObject(root)
        analysis.addObject(output)
        results = list(self.solver.Results)
        results.extend((root, output))
        self.solver.Results = results
        return root, (output,), True, reconciliation


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    poll_timer = QtCore.QTimer()
    tick_timer = QtCore.QTimer()
    exit_code = 1
    tick_count = 0
    active_status_seen = False
    original_require_binary = None
    original_import_tool = execution_module._import_tool

    def finish(code: int) -> None:
        nonlocal exit_code
        exit_code = code
        poll_timer.stop()
        tick_timer.stop()
        if original_require_binary is not None:
            from femsolver import settings

            settings.require_binary = original_require_binary
        execution_module._import_tool = original_import_tool
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(code)

    try:
        Gui.activateWorkbench("FemWorkbench")
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-analyze-solver-execution-"
        )
        temporary_path = Path(temporary.name)
        fake_solver = temporary_path / "fake-ccx"
        fake_solver.write_text(
            "#!/bin/sh\nsleep 0.25\nprintf 'solver completed'\n",
            encoding="utf-8",
        )
        fake_solver.chmod(0o700)
        output = temporary_path / "native-analyze-solver-execution.FCStd"
        document = App.newDocument("NativeAnalyzeSolverExecutionGate")
        document.UndoMode = 1
        document.saveAs(str(output))
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        _analysis, pipeline_solver, ccx_tools_solver = _create_analysis(document)
        _events(24)
        controller, surface = _surface(Gui.getMainWindow())
        solvers = (pipeline_solver, ccx_tools_solver)

        from femsolver import settings

        original_require_binary = settings.require_binary
        settings.require_binary = lambda name: (
            str(fake_solver) if name == "Calculix" else original_require_binary(name)
        )
        execution_module._import_tool = lambda request: _FakeResultImporter(
            request.target.solver
        )

        frozen = NativeSurfaceSnapshot.from_surface(surface)
        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        run_id = "native-analyze-solver-execution-gui"
        ledger.begin_run(run_id)

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
            background_manager=service.native_background_manager(),
            document_thread_dispatch=VibeGui._dispatch_to_document_thread,
            run_id=run_id,
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

        def call(tool: str, arguments: dict) -> dict:
            nonlocal call_number
            call_number += 1
            result = dispatcher.call(
                tool,
                json.dumps(arguments, separators=(",", ":")),
                f"native-analyze-solver-execution-{call_number}",
            )
            assert result.get("ok") is True, result
            return result

        solver_index = 0
        completed_results = []

        def start_solver(target_solver) -> str:
            start = call(
                ANALYZE_SOLVER_EXECUTION_CAPABILITY_NAME,
                {
                    "operation": "run",
                    "target": _solver_target(solver_state(target_solver)),
                    "timeout_seconds": 30,
                },
            )
            next_call = start["next"]
            assert next_call["tool"] == NATIVE_BACKGROUND_CAPABILITY_NAME
            assert next_call["operation"] == "status"
            assert next_call["job_id"] == start["job"]["job_id"]
            return start["job"]["job_id"]

        job_id = start_solver(solvers[solver_index])
        ledger.end_run(run_id)

        def tick() -> None:
            nonlocal tick_count
            tick_count += 1

        def poll() -> None:
            nonlocal document, job_id, solver_index, active_status_seen
            try:
                status = call(
                    NATIVE_BACKGROUND_CAPABILITY_NAME,
                    {"operation": "status", "job_id": job_id},
                )["job"]
                if not status["terminal"]:
                    domain = service.native_active_snapshot()["domain"]
                    run_status = domain["run_status"]
                    assert run_status["job_id"] == job_id
                    assert run_status["capability"] == (
                        "analyze.solver_execution.run"
                    )
                    assert domain["analysis_workflow_count"] == 1
                    workflow = domain["analysis_workflows"][0]
                    assert workflow["readiness"]["ready"] is True
                    assert workflow["readiness"]["generated_mesh_count"] >= 1
                    assert workflow["solver_count"] == 2
                    if run_status["terminal"] is False:
                        active_status_seen = True
                    return
                assert status["phase"] == "completed", status
                result = status["result"]
                solver = solvers[solver_index]
                expected_implementation = (
                    "pipeline" if solver_index == 0 else "ccx_tools"
                )
                assert result["result"]["solver"] == solver.Name
                assert result["execution"]["backend"] == "calculix"
                assert result["execution"]["implementation"] == expected_implementation
                assert result["execution"]["input_file_count"] > 0
                assert len(result["execution"]["input_sha256"]) == 64
                result_name = result["result"]["object_name"]
                result_object = document.getObject(result_name)
                assert result_object is not None
                domain = service.native_active_snapshot()["domain"]
                run_status = domain["run_status"]
                assert run_status["job_id"] == job_id
                assert run_status["phase"] == "completed"
                assert run_status["terminal"] is True
                assert run_status["solver"] == solver.Name
                assert run_status["result_object"] == result_name
                assert run_status["backend"] == "calculix"
                workflow = domain["analysis_workflows"][0]
                assert workflow["readiness"]["ready"] is True
                assert workflow["result_count"] >= 1
                encoded_domain = json.dumps(domain, separators=(",", ":"))
                assert "DisplacementVectors" not in encoded_domain
                assert "NodeNumbers" not in encoded_domain
                result_resources = result["result"]["resources"]
                assert len(result_resources) == 1
                output_object = document.getObject(
                    result_resources[0]["object_name"]
                )
                assert output_object is not None
                output_name = str(output_object.Name)
                assert result_object.VibeCADTimelineRole == "resource"
                assert result_object.VibeCADTimelineOwner is solver
                assert output_object.VibeCADTimelineRole == "resource"
                assert output_object.VibeCADTimelineOwner is result_object
                completed_results.append(
                    (
                        str(solver.Name),
                        result_name,
                        output_name,
                    )
                )
                assert tick_count >= 2
                assert active_status_seen

                if solver_index + 1 < len(solvers):
                    solver_index += 1
                    job_id = start_solver(solvers[solver_index])
                    return

                timeline = tuple(document.VibeCADTimeline.Operations)
                for solver_name, root_name, output_name in completed_results:
                    live_solver = document.getObject(solver_name)
                    live_root = document.getObject(root_name)
                    live_output = document.getObject(output_name)
                    index = timeline.index(live_solver)
                    assert timeline[index - 2 : index] == (
                        live_output,
                        live_root,
                    )

                old_solver_name, old_root_name, old_output_name = (
                    completed_results[0]
                )
                replacement_solver = document.getObject(old_solver_name)
                document.openTransaction("Replace exact FEM result graph")
                try:
                    replacement_graph = _FakeResultImporter(
                        replacement_solver,
                        replace_existing=True,
                    ).update_properties()
                    (
                        replacement_root,
                        replacement_resources,
                        replacement_is_new,
                        replacement_reconciliation,
                    ) = replacement_graph
                    from femcommands.manager import (
                        _finalize_timeline_result_graph,
                    )

                    _finalize_timeline_result_graph(
                        replacement_solver,
                        replacement_root,
                        replacement_resources,
                        root_is_new=replacement_is_new,
                        reconciliation=replacement_reconciliation,
                    )
                    document.recompute()
                    document.commitTransaction()
                except Exception:
                    document.abortTransaction()
                    raise
                replacement_root_name = str(replacement_root.Name)
                replacement_output_name = str(replacement_resources[0].Name)
                completed_results[0] = (
                    old_solver_name,
                    replacement_root_name,
                    replacement_output_name,
                )
                assert document.getObject(old_root_name) is None
                assert document.getObject(old_output_name) is None

                document.undo()
                assert document.getObject(old_root_name) is not None
                assert document.getObject(old_output_name) is not None
                assert document.getObject(replacement_root_name) is None
                assert document.getObject(replacement_output_name) is None
                document.redo()
                restored = document.getObject(replacement_root_name)
                restored_output = document.getObject(replacement_output_name)
                assert restored is not None and restored_output is not None
                assert restored.VibeCADTimelineOwner is replacement_solver
                assert restored_output.VibeCADTimelineOwner is restored
                document.recompute()
                document.save()
                App.closeDocument(document.Name)
                reopened = App.openDocument(str(output))
                document = reopened
                reopened_timeline = tuple(reopened.VibeCADTimeline.Operations)
                for solver_name, root_name, output_name in completed_results:
                    reopened_solver = reopened.getObject(solver_name)
                    reopened_result = reopened.getObject(root_name)
                    reopened_output = reopened.getObject(output_name)
                    assert reopened_result.VibeCADTimelineOwner is reopened_solver
                    assert reopened_output.VibeCADTimelineOwner is reopened_result
                    assert reopened_result in reopened_timeline
                print(
                    "VIBECAD_NATIVE_ANALYZE_SOLVER_EXECUTION_GUI_OK "
                    "action=1 implementations=2 background=true ui_responsive=true exact_input=true "
                    "job_status=true workflow_readiness=true concise_snapshot=true "
                    "exact_commit=true history=true undo_redo=true reopen=true",
                    flush=True,
                )
                finish(0)
            except Exception:
                traceback.print_exc(file=sys.__stderr__)
                finish(1)

        tick_timer.timeout.connect(tick)
        tick_timer.start(5)
        poll_timer.timeout.connect(poll)
        poll_timer.start(20)
        QtCore.QTimer.singleShot(30000, lambda: finish(1))
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
        finish(1)


QtCore.QTimer.singleShot(1000, _run)
