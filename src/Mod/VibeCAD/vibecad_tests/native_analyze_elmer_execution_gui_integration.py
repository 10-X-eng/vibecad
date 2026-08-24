# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real Elmer gate through the human Analyze solver command."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import FemGui
import VibeCADAnalyzeSolverGui as SolverGui
import VibeCADGui as VibeGui
from femcommands import manager as fem_manager
from femexamples.boxanalysis_static import setup


def _events(rounds: int = 12) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _publish_solver(document, solver) -> None:
    fem_manager._mark_timeline_operation(solver)
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None:
        timeline = document.addObject("App::DocumentTimeline", "VibeCADTimeline")
    timeline.Operations = [solver]
    assert document.recompute([solver], True, True) is not False


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    temporary = tempfile.TemporaryDirectory(prefix="vibecad-elmer-gate-")
    document = None
    poll_timer = QtCore.QTimer()
    heartbeat = QtCore.QTimer()
    exit_code = 1
    ticks = 0
    run_number = 1
    first_result_names: tuple[str, ...] = ()
    started = time.monotonic()
    preferences = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/Elmer")
    original = {
        "elmerBinaryPath": preferences.GetString("elmerBinaryPath", ""),
        "gridBinaryPath": preferences.GetString("gridBinaryPath", ""),
        "NumberOfTasks": preferences.GetInt("NumberOfTasks", 1),
        "ThreadsPerTask": preferences.GetInt("ThreadsPerTask", 1),
    }

    def finish(code: int) -> None:
        nonlocal exit_code
        exit_code = code
        poll_timer.stop()
        heartbeat.stop()
        for key, value in original.items():
            if isinstance(value, int):
                preferences.SetInt(key, value)
            else:
                preferences.SetString(key, value)
        if document is not None and document.Name in App.listDocuments():
            if document.FileName:
                document.save()
            App.closeDocument(document.Name)
        temporary.cleanup()
        application.exit(code)

    try:
        assert callable(getattr(SolverGui, "run_solver_detached", None))
        preferences.SetString(
            "elmerBinaryPath",
            "/opt/elmer-26.2/bin/ElmerSolver",
        )
        preferences.SetString(
            "gridBinaryPath",
            "/opt/elmer-26.2/bin/ElmerGrid",
        )
        preferences.SetInt("NumberOfTasks", 1)
        preferences.SetInt("ThreadsPerTask", 1)

        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        document = App.newDocument("NativeAnalyzeElmerGate")
        document.UndoMode = 1
        save_path = Path(temporary.name) / "native-analyze-elmer.FCStd"
        document.saveAs(str(save_path))
        document.openTransaction("Create Elmer acceptance study")
        try:
            setup(document, "elmer", test_mode=True)
            solver = document.getObject("SolverElmer")
            analysis = document.getObject("Analysis")
            assert solver is not None and analysis is not None
            _publish_solver(document, solver)
            document.commitTransaction()
        except Exception:
            document.abortTransaction()
            raise
        document.save()

        Gui.activateWorkbench("FemWorkbench")
        FemGui.setActiveAnalysis(analysis)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(solver)
        _events(20)
        assert Gui.isCommandActive("FEM_SolverRun")
        Gui.runCommand("FEM_SolverRun")
        _events(8)
        assert len(SolverGui._ACTIVE_RUNS) == 1
        runner = next(iter(SolverGui._ACTIVE_RUNS.values()))
        job_id = str(runner.job_id)
        assert job_id

        def beat() -> None:
            nonlocal ticks
            ticks += 1

        def poll() -> None:
            nonlocal document, first_result_names, job_id, run_number, runner
            try:
                if time.monotonic() - started > 90:
                    raise AssertionError("Elmer acceptance gate timed out")
                snapshot = runner.manager.snapshot(job_id)
                if not snapshot.terminal:
                    return
                assert snapshot.phase == "completed", snapshot.error
                assert ticks >= 3
                result = dict(snapshot.result or {})
                assert result["execution"]["backend"] == "elmer"
                assert result["execution"]["input_file_count"] >= 3
                result_name = result["result"]["object_name"]
                live_result = document.getObject(result_name)
                assert live_result is not None
                assert live_result.isDerivedFrom("Fem::FemPostPipeline")
                assert live_result.Data.GetNumberOfPoints() > 0
                assert live_result.Data.GetNumberOfCells() > 0
                assert live_result in tuple(solver.Results or ())
                result_names = tuple(str(item.Name) for item in solver.Results)
                assert result_name in result_names
                assert any(
                    document.getObject(name).isDerivedFrom("App::TextDocument")
                    for name in result_names
                )
                if run_number == 1:
                    first_result_names = result_names
                    run_number = 2
                    job_id = SolverGui.run_solver_detached(solver)
                    runner = SolverGui._ACTIVE_RUNS[job_id]
                    return
                assert result_names == first_result_names
                solver_name = str(solver.Name)
                document.save()
                App.closeDocument(document.Name)
                document = App.openDocument(str(save_path))
                App.setActiveDocument(document.Name)
                reopened_solver = document.getObject(solver_name)
                assert reopened_solver is not None
                assert tuple(str(item.Name) for item in reopened_solver.Results) == (
                    result_names
                )
                print(
                    "VIBECAD_NATIVE_ANALYZE_ELMER_GUI_OK "
                    "real_grid=true real_solver=true responsive=true "
                    "exact_import=true rerun=true save_reopen=true",
                    flush=True,
                )
                finish(0)
            except Exception:
                traceback.print_exc(file=sys.__stderr__)
                finish(1)

        heartbeat.setInterval(40)
        heartbeat.timeout.connect(beat)
        heartbeat.start()
        poll_timer.setInterval(100)
        poll_timer.timeout.connect(poll)
        poll_timer.start()
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
        finish(1)


QtCore.QTimer.singleShot(1000, _run)
