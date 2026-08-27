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
                CamPath.Command("G0", {"X": 24.0, "Y": 16.0, "Z": 10.0}),
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
        assert protected["collision_command_count"] == 1
        assert protected["collisions_truncated"] is False
        assert len(protected["collisions"]) == 1
        collision = protected["collisions"][0]
        assert collision["operation"] == operation.Name
        assert collision["command"] == "G1"
        assert collision["volume_mm3"] > 0.0
        assert collision["bounds_mm"] is not None

        print(
            "VIBECAD_NATIVE_MANUFACTURE_VERIFICATION_GUI_OK "
            "background=true protected_model=true gouge=true bounded=true",
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
