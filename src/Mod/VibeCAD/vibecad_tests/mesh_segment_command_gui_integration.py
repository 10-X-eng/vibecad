# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-ribbon gate for shared background Mesh segmentation commands."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Mesh
import MeshGui  # noqa: F401 - registers the Mesh ribbon commands
import ReverseEngineeringGui  # noqa: F401 - registers the reverse-engineering commands
from PySide import QtCore, QtGui, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from native_mesh_modify_gui_support import (
    add_sources,
    open_tetrahedron,
    tetrahedron,
    two_components,
)


def _process_events(rounds: int = 12) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _flat_patch(offset: float = 0.0):
    a = App.Vector(offset, 0.0, 0.0)
    b = App.Vector(offset + 10.0, 0.0, 0.0)
    c = App.Vector(offset + 10.0, 8.0, 0.0)
    d = App.Vector(offset, 8.0, 0.0)
    return Mesh.Mesh([(a, b, c), (a, c, d)])


def _select(*sources) -> None:
    Gui.Selection.clearSelection()
    for source in sources:
        Gui.Selection.addSelection(source)
    _process_events(2)


def _command_action(command: str):
    actions = tuple(
        action
        for action in Gui.getMainWindow().findChildren(QtGui.QAction)
        if str(action.objectName()) == command
    )
    assert len(actions) == 1, (command, len(actions))
    assert actions[0].isEnabled(), command
    return actions[0]


def _task_button(standard_button):
    for box in reversed(Gui.getMainWindow().findChildren(QtWidgets.QDialogButtonBox)):
        button = box.button(standard_button)
        if button is not None and button.isVisible() and button.isEnabled():
            return button
    return None


def _visible_widget(widget_type, name: str):
    widgets = tuple(
        widget
        for widget in Gui.getMainWindow().findChildren(widget_type, name)
        if widget.isVisible()
    )
    assert len(widgets) == 1, (name, len(widgets))
    return widgets[0]


def _latest(document, operation: str):
    return get_service().native_background_manager().latest_document_snapshot(
        str(document.Uid),
        capability_prefix=f"mesh.segment.{operation}.human",
    )


def _progress_dialog():
    dialogs = tuple(
        dialog
        for dialog in QtWidgets.QApplication.topLevelWidgets()
        if isinstance(dialog, QtWidgets.QProgressDialog)
        and dialog.isVisible()
        and str(dialog.windowTitle()) == "Mesh Segmentation"
    )
    return dialogs[-1] if dialogs else None


def _wait_job(document, operation: str):
    modal_messages: list[str] = []
    closer = QtCore.QTimer()
    closer.setInterval(10)

    def dismiss_failure() -> None:
        for dialog in QtWidgets.QApplication.topLevelWidgets():
            if isinstance(dialog, QtWidgets.QMessageBox) and dialog.isVisible():
                modal_messages.append(str(dialog.text()))
                dialog.accept()

    closer.timeout.connect(dismiss_failure)
    closer.start()
    deadline = time.monotonic() + 5.0
    snapshot = None
    while time.monotonic() < deadline:
        _process_events(2)
        snapshot = _latest(document, operation)
        if snapshot is not None:
            break
        time.sleep(0.01)
    assert snapshot is not None, operation
    deadline = time.monotonic() + 90.0
    while time.monotonic() < deadline:
        _process_events(2)
        snapshot = get_service().native_background_manager().snapshot(snapshot.job_id)
        if snapshot.terminal:
            break
        time.sleep(0.01)
    closer.stop()
    assert snapshot.phase == "completed", (snapshot.error, tuple(modal_messages))
    assert snapshot.changes_document is True
    assert isinstance(snapshot.result, dict)
    return snapshot


def _assert_progress_is_nonmodal() -> None:
    dialog = _progress_dialog()
    assert dialog is not None
    assert dialog.windowModality() == QtCore.Qt.NonModal
    assert dialog.findChildren(QtWidgets.QPushButton)


def _run_direct(document, sources, command: str, operation: str):
    selected = sources if isinstance(sources, tuple) else (sources,)
    _select(*selected)
    _command_action(command)
    started = time.monotonic()
    Gui.runCommand(command, 0)
    dispatch_ms = int((time.monotonic() - started) * 1000)
    assert dispatch_ms < 250, dispatch_ms
    _assert_progress_is_nonmodal()
    return dispatch_ms, _wait_job(document, operation)


def _configure_plane_task(*, tolerance: float) -> None:
    _visible_widget(QtWidgets.QGroupBox, "groupBoxPln").setChecked(True)
    _visible_widget(QtWidgets.QGroupBox, "groupBoxCyl").setChecked(False)
    _visible_widget(QtWidgets.QGroupBox, "groupBoxSph").setChecked(False)
    freeform = tuple(
        widget
        for widget in Gui.getMainWindow().findChildren(QtWidgets.QGroupBox, "groupBoxFree")
        if widget.isVisible()
    )
    if freeform:
        assert len(freeform) == 1
        freeform[0].setChecked(False)
    _visible_widget(QtWidgets.QSpinBox, "numPln").setValue(1)
    _visible_widget(QtWidgets.QDoubleSpinBox, "tolPln").setValue(tolerance)
    smoothing = tuple(
        widget
        for widget in Gui.getMainWindow().findChildren(QtWidgets.QCheckBox, "checkBoxSmooth")
        if widget.isVisible()
    )
    if smoothing:
        assert len(smoothing) == 1
        smoothing[0].setChecked(False)


def _run_task(document, source, command: str, operation: str, *, tolerance: float):
    _select(source)
    _command_action(command)
    opened = time.monotonic()
    Gui.runCommand(command, 0)
    open_ms = int((time.monotonic() - opened) * 1000)
    assert open_ms < 250, open_ms
    _process_events(8)
    assert Gui.Control.activeDialog()
    _configure_plane_task(tolerance=tolerance)
    accept = _task_button(QtWidgets.QDialogButtonBox.Ok)
    assert accept is not None
    started = time.monotonic()
    accept.click()
    dispatch_ms = int((time.monotonic() - started) * 1000)
    assert dispatch_ms < 250, dispatch_ms
    assert not Gui.Control.activeDialog()
    _assert_progress_is_nonmodal()
    snapshot = _wait_job(document, operation)
    return open_ms, dispatch_ms, snapshot


def _result_names(snapshot) -> tuple[str, ...]:
    segmentation = snapshot.result["segmentation"]
    result = segmentation.get("result")
    names = [result["object_name"]] if isinstance(result, dict) else []
    names.extend(item["object_name"] for item in segmentation.get("results", ()))
    controller = segmentation.get("operation_controller")
    if isinstance(controller, dict):
        names.append(controller["object_name"])
    return tuple(names)


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-mesh-segment-command-")
        save_path = Path(temporary.name) / "mesh-segment-command.FCStd"
        document = App.newDocument("MeshSegmentCommandGate")
        document.UndoMode = 1
        document.saveAs(str(save_path))
        VibeGui._ensure_document_thread_invoker()
        Gui.activateWorkbench("MeshWorkbench")
        sources = add_sources(
            document,
            (
                ("Components", two_components()),
                ("Curvature", _flat_patch(50.0)),
                ("BestFit", Mesh.createBox(10.0, 10.0, 10.0)),
                ("MergeA", tetrahedron(80.0)),
                ("MergeB", tetrahedron(100.0)),
                ("ReverseComponents", two_components()),
                ("OpenBoundary", open_tetrahedron(140.0)),
            ),
        )
        _process_events(12)
        initial_operation_names = tuple(
            obj.Name for obj in document.VibeCADTimeline.Operations
        )

        split_ms, split = _run_direct(
            document,
            sources["Components"],
            "Mesh_SplitComponents",
            "split_components",
        )
        curve_open_ms, curve_ms, curvature = _run_task(
            document,
            sources["Curvature"],
            "Mesh_Segmentation",
            "mesh_segmentation",
            tolerance=100.0,
        )
        fit_open_ms, fit_ms, best_fit = _run_task(
            document,
            sources["BestFit"],
            "Mesh_SegmentationBestFit",
            "segmentation_best_fit",
            tolerance=0.01,
        )
        merge_ms, merge = _run_direct(
            document,
            (sources["MergeA"], sources["MergeB"]),
            "Mesh_Merge",
            "merge",
        )
        reverse_components_ms, reverse_components = _run_direct(
            document,
            sources["ReverseComponents"],
            "Reen_SegmentationFromComponents",
            "segmentation_from_components",
        )
        boundary_ms, boundary = _run_direct(
            document,
            sources["OpenBoundary"],
            "Reen_MeshBoundary",
            "mesh_boundary",
        )

        result_names = tuple(
            name
            for snapshot in (
                split,
                curvature,
                best_fit,
                merge,
                reverse_components,
                boundary,
            )
            for name in _result_names(snapshot)
        )
        assert result_names
        assert all(document.getObject(name) is not None for name in result_names)
        assert all(
            not sources[name].Visibility
            for name in (
                "Components",
                "Curvature",
                "BestFit",
                "MergeA",
                "MergeB",
                "ReverseComponents",
            )
        )
        assert sources["OpenBoundary"].Visibility
        detached_results = tuple(document.getObject(name) for name in result_names)
        assert all(
            not bool(result.UpdateFromSource)
            for result in detached_results
            if hasattr(result, "UpdateFromSource")
        )
        boundary_object = document.getObject(
            boundary.result["segmentation"]["results"][0]["object_name"]
        )
        assert boundary_object.TypeId == "MeshPart::Boundary"
        assert boundary_object.Shape.isValid() and len(boundary_object.Shape.Edges) > 0
        operation_names = tuple(obj.Name for obj in document.VibeCADTimeline.Operations)
        assert operation_names[: len(initial_operation_names)] == initial_operation_names
        assert operation_names[len(initial_operation_names) :] == result_names, (
            operation_names[len(initial_operation_names) :],
            result_names,
        )

        document.undo()
        _process_events(8)
        assert document.getObject(result_names[-1]) is None
        document.redo()
        _process_events(8)
        assert document.getObject(result_names[-1]) is not None
        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        App.setActiveDocument(document.Name)
        _process_events(12)
        assert tuple(obj.Name for obj in document.VibeCADTimeline.Operations) == operation_names
        assert all(document.getObject(name) is not None for name in result_names)
        assert all(document.getObject(name).isValid() for name in result_names)

        print(
            "VIBECAD_MESH_SEGMENT_COMMAND_GUI_OK "
            f"split_dispatch_ms={split_ms} "
            f"curvature_open_ms={curve_open_ms} curvature_dispatch_ms={curve_ms} "
            f"best_fit_open_ms={fit_open_ms} best_fit_dispatch_ms={fit_ms} "
            f"merge_dispatch_ms={merge_ms} "
            f"reverse_components_dispatch_ms={reverse_components_ms} "
            f"boundary_dispatch_ms={boundary_ms} "
            "background=true nonmodal=true undo_redo=true reopen=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if Gui.Control.activeDialog():
            Gui.Control.closeDialog()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
