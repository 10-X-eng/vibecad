# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled GUI gate for the shared human retained-simulation command."""

from __future__ import annotations

import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import Path.Main.Gui.Job as PathJobGui
import Path.Op.Gui.Profile as PathProfileGui
import Path.Op.Profile as PathProfile
from VibeCADCore import get_service
from VibeCADManufactureSimulationResultGui import (
    COMMAND_NAME,
    RetainSimulationResultCommand,
    ensure_command_registered,
    start_retained_simulation,
)


def _events(rounds: int = 1) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 20)


def _top_face(model):
    maximum_z = float(model.Shape.BoundBox.ZMax)
    for index, face in enumerate(model.Shape.Faces, start=1):
        if all(
            abs(float(vertex.Point.z) - maximum_z) <= 1.0e-9
            for vertex in face.Vertexes
        ):
            return f"Face{index}"
    raise AssertionError("Human simulation fixture has no top face")


def _fixture(document, prefix: str):
    document.openTransaction("Create human simulation model")
    model = document.addObject("Part::Feature", f"{prefix}Model")
    model.Shape = Part.makeBox(48.0, 32.0, 8.0)
    document.publishProvisionalTimelineOperationBlock(model, (), ())
    assert document.recompute((model,), True, True) is not False
    document.commitTransaction()
    job = PathJobGui.Create([model], None, openTaskPanel=False)
    document.openTransaction("Create human simulation operation")
    try:
        operation = PathProfile.Create(
            f"{prefix}Profile",
            parentJob=job,
            toolController=job.Tools.Group[0],
        )
        operation.Proxy.addBase(operation, model, _top_face(model))
        provider = PathProfileGui.PathOpGui.ViewProvider(
            operation.ViewObject,
            PathProfileGui.Command.res,
        )
        operation.ViewObject.Proxy = provider
        provider.deleteOnReject = False
        for name in (
            "StartDepth",
            "FinalDepth",
            "StepDown",
            "SafeHeight",
            "ClearanceHeight",
        ):
            operation.setExpression(name, None)
        operation.StartDepth = 8.0
        operation.FinalDepth = 0.0
        operation.StepDown = 1.0
        operation.SafeHeight = 9.0
        operation.ClearanceHeight = 10.0
        assert document.recompute(None, True, True) is not False
        document.publishProvisionalTimelineOperationBlock(operation, (), ())
    except Exception:
        document.abortTransaction()
        raise
    document.commitTransaction()
    return job, operation


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    exit_code = 1
    try:
        document = App.newDocument("NativeManufactureHumanSimulationGate")
        document.UndoMode = 1
        job, operation = _fixture(document, "First")
        other_job, other_operation = _fixture(document, "Second")
        ensure_command_registered()
        assert Gui.Command.get(COMMAND_NAME) is not None
        command = RetainSimulationResultCommand()
        Gui.Selection.clearSelection()
        assert command.IsActive() is False
        Gui.Selection.addSelection(operation)
        assert command.IsActive() is True
        Gui.Selection.addSelection(other_operation)
        assert command.IsActive() is False
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(operation)

        job_id = start_retained_simulation(
            job,
            operations=(operation,),
            quality=4,
        )
        manager = get_service().native_background_manager()
        deadline = time.monotonic() + 120.0
        while time.monotonic() < deadline:
            _events()
            snapshot = manager.snapshot(job_id)
            if snapshot.terminal:
                break
        else:
            raise AssertionError("Human retained simulation did not finish")
        assert snapshot.phase == "completed", snapshot.error
        receipt = dict(snapshot.result or {})["simulation_result"]
        result_name = str(dict(receipt["result"])["object_name"])
        result = document.getObject(result_name)
        assert result is not None
        assert result.SimulationJob is job
        assert result.SimulationJob is not other_job
        assert list(result.SimulationOperationNames) == [operation.Name]
        assert result.SimulationQuality == 4
        assert result.SimulationProtectedModelChecked is True
        assert document.UndoCount >= 1

        print(
            "VIBECAD_NATIVE_MANUFACTURE_HUMAN_SIMULATION_GUI_OK "
            "shared_service=true background=true multi_setup=true explicit_setup=true durable=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
