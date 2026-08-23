# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI gate for the human Analyze Study Setup surface."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADAnalyzeStudyGui import DOCK_NAME, StudySetupWidget
from VibeCADAnalyzeStudySetup import analyses_in_document


def _events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    temporary = tempfile.TemporaryDirectory(prefix="vibecad-study-setup-")
    document = None
    exit_code = 1
    try:
        VibeGui._connect_document_observer()
        document = App.newDocument("StudySetupGate")
        document.UndoMode = 1
        save_path = Path(temporary.name) / "study-setup.FCStd"
        document.saveAs(str(save_path))
        Gui.activateWorkbench("FemWorkbench")
        _events(24)

        assert Gui.isCommandActive("VibeCAD_AnalyzeStudySetup")
        Gui.runCommand("VibeCAD_AnalyzeStudySetup")
        _events()
        dock = Gui.getMainWindow().findChild(QtWidgets.QDockWidget, DOCK_NAME)
        assert dock is not None and dock.isVisible()
        widget = dock.widget()
        assert isinstance(widget, StudySetupWidget)
        assert widget.apply_button.isVisible()

        widget.label_edit.setText("Fan Flow")
        widget.physics_checks["mechanical"].setChecked(False)
        widget.physics_checks["fluid"].setChecked(True)
        widget.regime_combo.setCurrentIndex(widget.regime_combo.findData("steady"))
        widget.apply_button.click()
        _events(24)

        analyses = analyses_in_document(document)
        assert len(analyses) == 1
        analysis = analyses[0]
        assert str(analysis.Label) == "Fan Flow"
        assert list(analysis.StudyPhysics) == ["fluid"]
        assert str(analysis.StudyRegime) == "steady"
        assert widget.analysis_combo.currentData() == analysis.Name
        assert widget.apply_button.text() == "Update Study"
        assert int(document.UndoCount) == 1

        widget.physics_checks["thermal"].setChecked(True)
        widget.regime_combo.setCurrentIndex(widget.regime_combo.findData("transient"))
        widget.apply_button.click()
        _events(16)
        assert list(analysis.StudyPhysics) == ["thermal", "fluid"]
        assert str(analysis.StudyRegime) == "transient"
        assert int(document.UndoCount) == 2

        document.undo()
        assert list(analysis.StudyPhysics) == ["fluid"]
        assert str(analysis.StudyRegime) == "steady"
        document.redo()
        assert list(analysis.StudyPhysics) == ["thermal", "fluid"]
        assert str(analysis.StudyRegime) == "transient"

        analysis_name = str(analysis.Name)
        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        App.setActiveDocument(document.Name)
        _events(12)
        reopened = document.getObject(analysis_name)
        assert reopened is not None
        assert list(reopened.StudyPhysics) == ["thermal", "fluid"]
        assert str(reopened.StudyRegime) == "transient"
        print(
            "VIBECAD_ANALYZE_STUDY_SETUP_GUI_OK "
            "ribbon=true create=true update=true exact_operations=true "
            "undo_redo=true reopen=true controls_visible=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        dock = Gui.getMainWindow().findChild(QtWidgets.QDockWidget, DOCK_NAME)
        if dock is not None:
            dock.close()
        if document is not None and document.Name in App.listDocuments():
            if document.FileName:
                document.save()
            App.closeDocument(document.Name)
        temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
