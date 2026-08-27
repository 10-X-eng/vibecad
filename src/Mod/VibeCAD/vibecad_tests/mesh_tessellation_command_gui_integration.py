# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI gate for the human Mesh From Shape task and estimator."""

from __future__ import annotations

from pathlib import Path
import tempfile
import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import MeshPartGui  # noqa: F401 - registers Mesh From Shape command
from PySide import QtCore, QtWidgets

from VibeCADCore import get_service
import VibeCADGui


def _events() -> None:
    Gui.updateGui()
    QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _visible_ok_button() -> QtWidgets.QPushButton | None:
    for box in Gui.getMainWindow().findChildren(QtWidgets.QDialogButtonBox):
        parent = box.parentWidget()
        while parent is not None:
            if parent.metaObject().className() == "Gui::TaskView::TaskView":
                break
            parent = parent.parentWidget()
        if parent is None:
            continue
        button = box.button(QtWidgets.QDialogButtonBox.Ok)
        if button is not None and button.isVisible() and button.isEnabled():
            return button
    return None


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        VibeCADGui._ensure_document_thread_invoker()
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-mesh-command-")
        path = Path(temporary.name) / "human-mesh-from-shape.FCStd"
        document = App.newDocument("HumanMeshFromShapeGate")
        source = document.addObject("Part::Box", "HumanMeshingSource")
        source.Length = 30.0
        source.Width = 20.0
        source.Height = 10.0
        assert document.recompute([source], True, True) is not False
        assert source.Shape.BoundBox.XLength == 30.0
        document.saveAs(str(path))

        Gui.activateWorkbench("MeshWorkbench")
        for _ in range(10):
            _events()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source)
        assert Gui.isCommandActive("Mesh_FromPartShape")
        Gui.runCommand("Mesh_FromPartShape", 0)
        for _ in range(10):
            _events()
        assert Gui.Control.activeDialog()

        tabs = Gui.getMainWindow().findChild(QtWidgets.QTabWidget, "stackedWidget")
        assert tabs is not None and tabs.isTabEnabled(1)
        tabs.setCurrentIndex(1)
        _events()
        estimate = Gui.getMainWindow().findChild(
            QtWidgets.QPushButton,
            "estimateMaximumEdgeLength",
        )
        maximum = Gui.getMainWindow().findChild(
            QtWidgets.QWidget,
            "spinMaximumEdgeLength",
        )
        assert estimate is not None and maximum is not None and estimate.isVisible()
        estimate.click()
        assert not estimate.isEnabled(), "edge estimate did not enter its background state"
        _events()
        heartbeats = 0
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline and not estimate.isEnabled():
            _events()
            heartbeats += 1
            time.sleep(0.01)
        assert estimate.isEnabled()
        estimated_value = float(maximum.text().split()[0])
        assert estimated_value == 3.0, maximum.text()

        button = _visible_ok_button()
        assert button is not None
        button.click()
        _events()
        assert not Gui.Control.activeDialog()

        manager = get_service().native_background_manager()
        deadline = time.monotonic() + 120.0
        snapshot = None
        while time.monotonic() < deadline:
            _events()
            snapshot = manager.latest_document_snapshot(
                str(document.Uid),
                capability_prefix="mesh.convert.shape_to_mesh.human",
            )
            if snapshot is not None and snapshot.terminal:
                break
            time.sleep(0.01)
        assert snapshot is not None and snapshot.phase == "completed", snapshot
        meshes = [obj for obj in document.Objects if obj.TypeId == "MeshPart::MeshFromShape"]
        assert len(meshes) == 1 and meshes[0].Mesh.CountFacets > 0

        print(
            "VIBECAD_MESH_TESSELLATION_COMMAND_GUI_OK "
            f"estimate_mm={float(estimated_value):g} facets={meshes[0].Mesh.CountFacets}",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        if Gui.Control.activeDialog():
            Gui.Control.closeDialog()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(500, _run)
