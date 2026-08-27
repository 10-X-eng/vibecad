# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled gate for exact protected-model CAM collision verification."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import traceback

import FreeCAD as App
import Part
import Path as CamPath
from PySide import QtCore, QtWidgets

import Path.Main.Gui.Job as PathJobGui
from VibeCADNativeManufactureSimulationResultInput import preflight_native_simulation
from VibeCADNativeManufactureSimulationResultWorker import execute_native_simulation
from VibeCADNativeManufactureState import job_state, operation_reference_state


def _target(state: dict) -> dict[str, str]:
    return {
        "object_name": str(state["object_name"]),
        "expected_state_sha256": str(state["state_sha256"]),
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    exit_code = 1
    try:
        document = App.newDocument("NativeManufactureVerificationGate")
        document.UndoMode = 1
        document.openTransaction("Create protected CAM model")
        model = document.addObject("Part::Feature", "ProtectedModel")
        model.Shape = Part.makeBox(48.0, 32.0, 8.0)
        document.publishProvisionalTimelineOperationBlock(model, (), ())
        assert document.recompute((model,), True, True) is not False
        document.commitTransaction()

        job = PathJobGui.Create([model], None, openTaskPanel=False)
        assert job is not None and job.Tools.Group
        job.Machine = "Generic LinuxCNC Mill"
        document.openTransaction("Create deliberate gouge path")
        operation = document.addObject("Path::Feature", "DeliberateGouge")
        operation.Label = "Deliberate protected-model gouge"
        operation.addProperty("App::PropertyBool", "Active")
        operation.Active = True
        operation.addProperty("App::PropertyLink", "ToolController")
        operation.ToolController = job.Tools.Group[0]
        operation.Path = CamPath.Path(
            [
                CamPath.Command("G0", {"X": 24.0, "Y": 16.0, "Z": 10.0}),
                CamPath.Command("G1", {"X": 24.0, "Y": 16.0, "Z": 4.0}),
                CamPath.Command("G0", {"X": -10.0, "Y": 16.0, "Z": 4.0}),
                CamPath.Command("G0", {"X": -10.0, "Y": 16.0, "Z": 10.0}),
                CamPath.Command("G0", {"X": 700.0, "Y": 16.0, "Z": 10.0}),
            ]
        )
        job.Proxy.addOperation(operation)
        document.publishProvisionalTimelineOperationBlock(operation, (), ())
        assert document.recompute(None, True, True) is not False
        document.commitTransaction()

        frozen = preflight_native_simulation(
            document,
            job=_target(job_state(job)),
            operations=[_target(operation_reference_state(operation))],
            quality=5,
        )
        worker_threads = []

        def execute():
            worker_threads.append(threading.get_ident())
            return execute_native_simulation(
                frozen,
                cancelled=lambda: False,
                progress=lambda _percent, _message: None,
            )

        with ThreadPoolExecutor(max_workers=1) as executor:
            prepared = executor.submit(execute).result(timeout=120.0)

        protected = prepared.verification["protected_model"]
        assert worker_threads and worker_threads[0] != threading.get_ident()
        assert protected["checked"] is True
        assert protected["collision"] is True
        assert protected["collision_command_count"] == 2
        assert protected["collisions_truncated"] is False
        assert len(protected["collisions"]) == 2
        collision = next(
            value for value in protected["collisions"] if value["rapid"] is False
        )
        assert collision["operation"] == operation.Name
        assert collision["command"] == "G1"
        assert collision["volume_mm3"] > 0.0
        assert collision["bounds_mm"] is not None
        rapid = prepared.verification["rapid_clearance"]
        assert rapid["protected_model_checked"] is True
        assert rapid["protected_model_collision"] is True
        assert rapid["collision_command_count"] == 1
        assert len(rapid["collisions"]) == 1
        assert rapid["collisions"][0]["command"] == "G0"
        assert rapid["current_stock_checked"] is False
        cycle_time = prepared.verification["cycle_time"]
        assert cycle_time["complete"] is False
        assert cycle_time["missing_operations"] == [operation.Name]
        assert "cycle_time" in prepared.verification["unavailable_checks"]
        machine_travel = prepared.verification["machine_travel"]
        assert machine_travel["configured"] is True
        assert machine_travel["axis_span_checked"] is True
        assert machine_travel["fits_axis_spans"] is False
        assert [value["axis"] for value in machine_travel["violations"]] == ["X"]
        assert machine_travel["violations"][0]["path_span"] == 710.0
        assert "machine_travel" not in prepared.verification["unavailable_checks"]
        assert "machine_travel_position" in prepared.verification["unavailable_checks"]

        print(
            "VIBECAD_NATIVE_MANUFACTURE_VERIFICATION_GUI_OK "
            "background=true protected_model=true gouge=true bounded=true "
            "machine_span=true",
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
