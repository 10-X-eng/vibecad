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
from VibeCADNativeAnalyzeMaterialCreate import (
    create_material,
    prepare_material_create,
    verify_material_create,
)
from VibeCADNativeAnalyzeState import analysis_state
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeMutation import run_human_mutation


def _events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _create_geometry_sources(document):
    document.openTransaction("Create fluid geometry")
    try:
        first = document.addObject("Part::Box", "InletVolume")
        first.Label = "Inlet Volume"
        first.Length = 30.0
        first.Width = 20.0
        first.Height = 10.0
        second = document.addObject("Part::Box", "OutletVolume")
        second.Label = "Outlet Volume"
        second.Length = 30.0
        second.Width = 20.0
        second.Height = 10.0
        second.Placement.Base = App.Vector(0.0, 30.0, 0.0)
        assert document.recompute([first, second], True, True) is not False
        for source in (first, second):
            assert not source.Shape.isNull() and source.Shape.isValid()
            document.publishProvisionalTimelineOperationBlock(source, (), ())
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    return first, second


def _analysis_target(analysis) -> dict:
    state = analysis_state(analysis)
    return {
        "object_name": str(analysis.Name),
        "expected_state_sha256": str(state["state_sha256"]),
        "expected_member_count": int(state["member_count"]),
    }


def _reference(source) -> dict:
    return {
        "object_name": str(source.Name),
        "expected_state_sha256": str(mesh_object_state(source)["state_sha256"]),
        "subelements": ["Solid1"],
    }


def _create_fluid_material(document, analysis, source, label: str):
    prepared = prepare_material_create(
        document,
        str(document.Uid),
        kind="fluid",
        analysis=_analysis_target(analysis),
        label=label,
        references=[_reference(source)],
        properties={
            "name": "Air",
            "density_kg_m3": 1.225,
            "kinematic_viscosity_m2_s": 1.48e-5,
        },
    )
    result = run_human_mutation(
        document=document,
        transaction_name=f"Create {label}",
        mutate=lambda current: create_material(current, prepared),
        verify=verify_material_create,
    )
    return document.getObject(result["created_material"]["object_name"])


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
        first_source, second_source = _create_geometry_sources(document)
        undo_before_study = int(document.UndoCount)
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
        assert int(document.UndoCount) == undo_before_study + 1

        widget.physics_checks["thermal"].setChecked(True)
        widget.regime_combo.setCurrentIndex(widget.regime_combo.findData("transient"))
        widget.apply_button.click()
        _events(16)
        assert list(analysis.StudyPhysics) == ["thermal", "fluid"]
        assert str(analysis.StudyRegime) == "transient"
        assert int(document.UndoCount) == undo_before_study + 2

        document.undo()
        assert list(analysis.StudyPhysics) == ["fluid"]
        assert str(analysis.StudyRegime) == "steady"
        document.redo()
        assert list(analysis.StudyPhysics) == ["thermal", "fluid"]
        assert str(analysis.StudyRegime) == "transient"

        first_material = _create_fluid_material(
            document,
            analysis,
            first_source,
            "Inlet Air",
        )
        second_material = _create_fluid_material(
            document,
            analysis,
            second_source,
            "Outlet Air",
        )
        assert first_material is not None and second_material is not None
        widget.refresh()
        _events(12)
        assert widget.assignment_table.topLevelItemCount() == 2
        first_item = next(
            widget.assignment_table.topLevelItem(index)
            for index in range(widget.assignment_table.topLevelItemCount())
            if widget.assignment_table.topLevelItem(index).text(0) == "Inlet Air"
        )
        widget.assignment_table.setCurrentItem(first_item)
        widget.highlight_button.click()
        _events(8)
        selected = Gui.Selection.getSelectionEx(document.Name)
        assert len(selected) == 1
        assert selected[0].Object is first_source
        assert tuple(selected[0].SubElementNames) == ("Solid1",)

        first_source.ViewObject.Visibility = True
        second_source.ViewObject.Visibility = False
        widget.isolate_button.click()
        _events(8)
        assert first_source.ViewObject.Visibility
        assert not second_source.ViewObject.Visibility
        assert widget.restore_button.isEnabled()
        widget.restore_button.click()
        _events(8)
        assert first_source.ViewObject.Visibility
        assert not second_source.ViewObject.Visibility
        assert not widget.restore_button.isEnabled()

        widget._validate_assignments()
        assert widget.assignment_validation.text() == "2 assignments valid"

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
            "undo_redo=true reopen=true controls_visible=true "
            "assignments=true highlight=true isolate_restore=true validation=true",
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
