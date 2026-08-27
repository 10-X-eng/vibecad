# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real ribbon-command gate for non-blocking Mesh import."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Mesh
from PySide import QtCore, QtWidgets

from VibeCADCore import get_service


def _process_events(rounds: int = 4) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _tetrahedron(offset: float = 0.0):
    a = App.Vector(offset, 0.0, 0.0)
    b = App.Vector(offset + 8.0, 0.0, 0.0)
    c = App.Vector(offset, 7.0, 0.0)
    d = App.Vector(offset, 0.0, 6.0)
    return Mesh.Mesh([(a, c, b), (a, b, d), (b, c, d), (c, a, d)])


def _large_grid(columns: int = 400, rows: int = 400):
    facets = []
    for row in range(rows):
        y0 = float(row)
        y1 = float(row + 1)
        for column in range(columns):
            x0 = float(column)
            x1 = float(column + 1)
            lower_left = App.Vector(x0, y0, 0.0)
            lower_right = App.Vector(x1, y0, 0.0)
            upper_left = App.Vector(x0, y1, 0.0)
            upper_right = App.Vector(x1, y1, 0.0)
            facets.append((lower_left, lower_right, upper_right))
            facets.append((lower_left, upper_right, upper_left))
    return Mesh.Mesh(facets)


def _accept_files(paths: tuple[Path, ...]) -> None:
    attempts = {"remaining": 2000}

    def accept() -> None:
        for widget in QtWidgets.QApplication.topLevelWidgets():
            if not isinstance(widget, QtWidgets.QFileDialog) or not widget.isVisible():
                continue
            widget.setDirectory(str(paths[0].parent))
            line_edit = widget.findChild(QtWidgets.QLineEdit, "fileNameEdit")
            if line_edit is None:
                continue
            line_edit.setText(" ".join(f'"{path.name}"' for path in paths))
            widget.accept()
            return
        attempts["remaining"] -= 1
        if attempts["remaining"] > 0:
            QtCore.QTimer.singleShot(5, accept)

    QtCore.QTimer.singleShot(0, accept)


def _latest(document, previous: str = ""):
    return get_service().native_background_manager().latest_document_snapshot(
        str(document.Uid),
        capability_prefix="mesh.io.import_mesh.human",
    )


def _wait(document, job_id: str, *, timeout: float = 60.0):
    manager = get_service().native_background_manager()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _process_events(2)
        snapshot = manager.snapshot(job_id)
        if snapshot.terminal:
            _process_events(8)
            return snapshot
        time.sleep(0.01)
    raise AssertionError(f"Background Mesh import {job_id} did not finish")


def _run() -> None:
    document = None
    temporary = None
    preference = App.ParamGet("User parameter:BaseApp/Preferences/Dialog")
    native_dialog_before = preference.GetBool("DontUseNativeDialog", False)
    exit_code = 1
    try:
        Gui.activateWorkbench("MeshWorkbench")
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-mesh-import-command-")
        root = Path(temporary.name)
        document = App.newDocument("MeshImportCommandGate")
        document.UndoMode = 1
        document.saveAs(str(root / "mesh-import-command.FCStd"))
        preference.SetBool("DontUseNativeDialog", True)

        large_path = root / "cancel-large.stl"
        _large_grid().write(str(large_path))
        _accept_files((large_path,))
        started_at = time.monotonic()
        Gui.runCommand("Mesh_Import", 0)
        dispatch_seconds = time.monotonic() - started_at
        assert dispatch_seconds < 2.0, dispatch_seconds
        cancelled_job = _latest(document)
        assert cancelled_job is not None and not cancelled_job.terminal
        assert get_service().native_background_manager().cancel(cancelled_job.job_id)
        cancelled = _wait(document, cancelled_job.job_id)
        assert cancelled.phase == "cancelled", cancelled
        assert not any(obj.TypeId == "Mesh::Feature" for obj in document.Objects)

        first_path = root / "first.stl"
        second_path = root / "second.stl"
        _tetrahedron().write(str(first_path))
        _tetrahedron(20.0).write(str(second_path))
        undo_before = int(document.UndoCount)
        _accept_files((first_path, second_path))
        Gui.runCommand("Mesh_Import", 0)
        started = _latest(document, cancelled.job_id)
        assert started is not None and started.job_id != cancelled.job_id
        completed = _wait(document, started.job_id)
        assert completed.phase == "completed", completed.error
        result = dict(completed.result or {})
        output_names = tuple(result.get("output_names") or ())
        assert len(output_names) == 2
        outputs = tuple(document.getObject(name) for name in output_names)
        assert all(outputs) and all(obj.Mesh.CountFacets == 4 for obj in outputs)
        assert int(document.UndoCount) == undo_before + 1
        controllers = [
            obj for obj in document.Objects if obj.TypeId == "Mesh::OutputGroup"
        ]
        assert len(controllers) == 1
        assert list(controllers[0].ExternalInputs) == [first_path.name, second_path.name]
        assert set(controllers[0].Group) == set(outputs)

        document.undo()
        _process_events(8)
        assert all(document.getObject(name) is None for name in output_names)
        document.redo()
        _process_events(8)
        assert all(document.getObject(name) is not None for name in output_names)

        document.save()
        document_name = document.Name
        App.closeDocument(document_name)
        document = App.openDocument(str(root / "mesh-import-command.FCStd"))
        _process_events(8)
        reopened = tuple(document.getObject(name) for name in output_names)
        assert all(reopened) and all(obj.Mesh.CountFacets == 4 for obj in reopened)
        reopened_group = next(
            obj for obj in document.Objects if obj.TypeId == "Mesh::OutputGroup"
        )
        assert list(reopened_group.ExternalInputs) == [first_path.name, second_path.name]

        print(
            "VIBECAD_MESH_IMPORT_COMMAND_GUI_OK "
            f"dispatch_ms={int(dispatch_seconds * 1000)} cancelled=true files=2 "
            "undo_redo=true reopen=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        preference.SetBool("DontUseNativeDialog", native_dialog_before)
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        QtCore.QTimer.singleShot(0, lambda: QtWidgets.QApplication.exit(exit_code))


QtCore.QTimer.singleShot(0, _run)
