# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real CalculiX thermomechanical result gate through shared FEM execution."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

from femexamples.thermomech_bimetal import setup
from femsolver.run import run_fem_solver
from VibeCADCore import get_service
import VibeCADGui as VibeGui
from VibeCADNativeAnalyzeResultState import result_state
from VibeCADNativeAnalyzeMechanicalResultBindings import _mechanical_fields
from VibeCADNativeAnalyzeThermalResultBindings import _temperature_field
from femcommands import manager as fem_manager


def _events(rounds: int = 8) -> None:
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
    temporary = tempfile.TemporaryDirectory(prefix="vibecad-thermomechanical-gate-")
    output = Path(temporary.name) / "thermomechanical-bimetal.FCStd"
    document = None
    exit_code = 1
    preferences = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/Ccx")
    old_ccx = preferences.GetString("ccxBinaryPath", "")
    try:
        preferences.SetString("ccxBinaryPath", "/usr/bin/ccx")
        Gui.activateWorkbench("FemWorkbench")
        document = App.newDocument("NativeAnalyzeThermomechanicalGate")
        document.UndoMode = 1
        document.saveAs(str(output))
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        setup(document, "ccxtools", test_mode=True)
        solver = document.getObject("CalculiXCcxTools")
        analysis = document.getObject("Analysis")
        assert solver is not None and analysis is not None
        solver_name = str(solver.Name)
        _publish_solver(document, solver)
        document.save()

        job_id = run_fem_solver(solver)
        assert isinstance(job_id, str) and job_id
        deadline = time.monotonic() + 180.0
        manager = get_service().native_background_manager()
        while time.monotonic() < deadline:
            _events(2)
            snapshot = manager.snapshot(job_id)
            if snapshot.terminal:
                break
            time.sleep(0.02)
        else:
            raise AssertionError("Thermomechanical solver timed out")
        assert snapshot.phase == "completed", snapshot.error
        result_summary = snapshot.result["result"]
        result_name = result_summary["object_name"]
        result = document.getObject(result_name)
        assert result is not None
        state = result_state(result)
        thermal_field = _temperature_field(state)
        mechanical_fields = {
            field["semantic"]: field
            for field in _mechanical_fields(state, solver_kind="calculix")
        }
        assert thermal_field is not None, state["fields"]
        assert "displacement_magnitude" in mechanical_fields, state["fields"]
        temperature = thermal_field["range"]
        displacement = mechanical_fields["displacement_magnitude"]["range"]
        assert 272.0 <= temperature[0] <= 274.0, temperature
        assert 370.0 <= temperature[1] <= 390.0, temperature
        assert 5.0 < displacement[1] < 10.0, displacement
        assert result in tuple(solver.Results or ())

        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(output))
        App.setActiveDocument(document.Name)
        _events(12)
        reopened_solver = document.getObject(solver_name)
        reopened_result = document.getObject(result_name)
        assert reopened_solver is not None and reopened_result is not None
        assert reopened_result in tuple(reopened_solver.Results or ())
        print(
            "VIBECAD_NATIVE_ANALYZE_THERMOMECHANICAL_GUI_OK "
            f"temperature_k={temperature[0]:.6g}:{temperature[1]:.6g} "
            f"max_displacement_mm={displacement[1]:.6g} "
            "real_calculix=true exact_import=true save_reopen=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        preferences.SetString("ccxBinaryPath", old_ccx)
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
