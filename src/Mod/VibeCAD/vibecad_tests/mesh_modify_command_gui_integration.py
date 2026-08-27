# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-ribbon gate for shared background Mesh modification commands."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from native_mesh_modify_gui_support import add_sources, tetrahedron


def _process_events(rounds: int = 12) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _select(source) -> None:
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(source)
    _process_events(2)


def _latest(document, operation: str):
    return get_service().native_background_manager().latest_document_snapshot(
        str(document.Uid),
        capability_prefix=f"mesh.modify.{operation}.human",
    )


def _task_button(standard_button):
    for box in reversed(
        Gui.getMainWindow().findChildren(QtWidgets.QDialogButtonBox)
    ):
        button = box.button(standard_button)
        if button is not None and button.isVisible():
            return button
    return None


def _wait_job(document, operation: str):
    deadline = time.monotonic() + 5.0
    snapshot = None
    while time.monotonic() < deadline:
        _process_events(2)
        snapshot = _latest(document, operation)
        if snapshot is not None:
            break
        time.sleep(0.01)
    assert snapshot is not None
    deadline = time.monotonic() + 90.0
    while time.monotonic() < deadline:
        _process_events(2)
        snapshot = get_service().native_background_manager().snapshot(snapshot.job_id)
        if snapshot.terminal:
            break
        time.sleep(0.01)
    assert snapshot.phase == "completed", snapshot.error
    return snapshot


def _run_command(document, source, command: str, operation: str):
    _select(source)
    selected = tuple(Gui.Selection.getSelection())
    assert selected == (source,), (
        command,
        str(source.Name),
        tuple(str(item.Name) for item in selected),
        bool(source.Visibility),
    )
    actions = tuple(
        action
        for action in Gui.getMainWindow().findChildren(QtGui.QAction)
        if str(action.objectName()) == command
    )
    assert len(actions) == 1, (command, len(actions))
    assert actions[0].isEnabled(), (command, str(source.Name), bool(source.Visibility))
    started = time.monotonic()
    Gui.runCommand(command)
    dispatch_ms = int((time.monotonic() - started) * 1000)
    assert dispatch_ms < 250, dispatch_ms
    snapshot = _wait_job(document, operation)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        visible_progress = tuple(
            dialog
            for dialog in QtWidgets.QApplication.topLevelWidgets()
            if isinstance(dialog, QtWidgets.QProgressDialog)
            and dialog.isVisible()
            and str(dialog.windowTitle()) == "Mesh"
        )
        if not visible_progress:
            break
        _process_events(2)
        time.sleep(0.01)
    assert not visible_progress
    return dispatch_ms, snapshot


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("MeshWorkbench")
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-mesh-modify-command-")
        save_path = Path(temporary.name) / "mesh-modify-command.FCStd"
        document = App.newDocument("MeshModifyCommandGate")
        document.UndoMode = 1
        document.saveAs(str(save_path))
        VibeGui._ensure_document_thread_invoker()
        sources = add_sources(
            document,
            (
                ("Inconsistent", tetrahedron(inconsistent=True)),
                ("Coherent", tetrahedron(16.0)),
                ("Flip", tetrahedron(32.0)),
            ),
        )

        harmonize_ms, harmonized = _run_command(
            document,
            sources["Inconsistent"],
            "Mesh_HarmonizeNormals",
            "harmonize_normals",
        )
        assert harmonized.result["changed"] is True
        harmonized_name = harmonized.result["outputs"][0]["result"]["object_name"]
        harmonized_result = document.getObject(harmonized_name)
        assert harmonized_result is not None
        assert harmonized_result.TypeId == "Mesh::HarmonizeNormals"
        assert harmonized_result.Mesh.countNonUniformOrientedFacets() == 0

        history_before_noop = tuple(document.VibeCADTimeline.Operations)
        noop_ms, noop = _run_command(
            document,
            sources["Coherent"],
            "Mesh_HarmonizeNormals",
            "harmonize_normals",
        )
        assert noop.result["changed"] is False
        assert tuple(document.VibeCADTimeline.Operations) == history_before_noop
        assert sources["Coherent"].Visibility

        flip_ms, flipped = _run_command(
            document,
            sources["Flip"],
            "Mesh_FlipNormals",
            "flip_normals",
        )
        flipped_name = flipped.result["outputs"][0]["result"]["object_name"]
        flipped_result = document.getObject(flipped_name)
        assert flipped_result is not None and flipped_result.TypeId == "Mesh::FlipNormals"
        operation_names = tuple(obj.Name for obj in document.VibeCADTimeline.Operations)

        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        App.setActiveDocument(document.Name)
        _process_events(12)
        assert tuple(obj.Name for obj in document.VibeCADTimeline.Operations) == operation_names
        assert document.getObject(harmonized_name).isValid()
        assert document.getObject(flipped_name).isValid()

        App.closeDocument(document.Name)
        document = App.newDocument("MeshRemoveCommandGate")
        document.UndoMode = 1
        document.saveAs(str(Path(temporary.name) / "mesh-remove-command.FCStd"))
        remove_source = add_sources(
            document,
            (("RemoveAll", tetrahedron()),),
        )["RemoveAll"]
        _select(remove_source)
        Gui.runCommand("Mesh_RemoveComponents", 0)
        _process_events(8)
        select_all = Gui.getMainWindow().findChild(QtWidgets.QPushButton, "selectAll")
        delete = _task_button(QtWidgets.QDialogButtonBox.Ok)
        assert select_all is not None and delete is not None
        select_all.click()
        _process_events(4)
        started = time.monotonic()
        delete.click()
        remove_dispatch_ms = int((time.monotonic() - started) * 1000)
        assert remove_dispatch_ms < 250, remove_dispatch_ms
        removed = _wait_job(document, "remove_components")
        assert removed.result["changed"] is True
        removed_name = removed.result["outputs"][0]["result"]["object_name"]
        assert document.getObject(removed_name).Mesh.CountFacets == 0
        close = _task_button(QtWidgets.QDialogButtonBox.Close)
        assert close is not None
        close.click()
        _process_events(8)
        document.save()

        print(
            "VIBECAD_MESH_MODIFY_COMMAND_GUI_OK "
            f"harmonize_dispatch_ms={harmonize_ms} "
            f"noop_dispatch_ms={noop_ms} flip_dispatch_ms={flip_ms} "
            f"remove_dispatch_ms={remove_dispatch_ms} "
            "background=true noop=true empty=true reopen=true",
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
