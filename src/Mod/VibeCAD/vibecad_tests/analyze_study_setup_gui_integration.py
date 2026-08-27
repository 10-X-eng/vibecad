# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI gate for the human Analyze Study Setup surface."""

from __future__ import annotations

import json
from copy import deepcopy
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
        assert (
            widget.results_browser.objectName()
            == "VibeCADEngineeringResultsPanel"
        )
        assert widget.results_browser.property("vibeEngineeringSurface") is True
        assert (
            widget.results_browser.summary_label.property("vibeResultCard")
            is True
        )
        assert (
            widget.results_browser.findChild(
                QtWidgets.QGroupBox, "VibeCADEngineeringFieldsCard"
            )
            is not None
        )
        assert (
            widget.results_browser.findChild(
                QtWidgets.QGroupBox, "VibeCADEngineeringStatusCard"
            )
            is not None
        )
        assert widget.results_browser.field_combo.count() == 0
        assert (
            widget.results_browser.findChild(
                QtWidgets.QDoubleSpinBox,
                "VibeCADEngineeringDeformationScale",
            )
            is widget.results_browser.deformation_scale
        )
        assert not widget.results_browser.deformation_scale.isEnabled()
        assert (
            widget.results_browser.findChild(
                QtWidgets.QGroupBox, "VibeCADEngineeringPerformanceCard"
            )
            is not None
        )
        assert (
            widget.results_browser.findChild(
                QtWidgets.QGroupBox, "VibeCADEngineeringComparisonCard"
            )
            is not None
        )
        assert widget.results_browser.result_combo.count() == 0
        assert widget.geometry_browser.source_combo.count() == 2
        assert widget.geometry_browser.face_table.topLevelItemCount() == 6
        widget.geometry_browser.face_table.setCurrentItem(
            widget.geometry_browser.face_table.topLevelItem(0)
        )
        widget.geometry_browser.highlight_button.click()
        _events(8)
        selected_face = Gui.Selection.getSelectionEx(document.Name)
        assert len(selected_face) == 1
        assert selected_face[0].Object is first_source
        assert tuple(selected_face[0].SubElementNames) == ("Face1",)
        first_source.ViewObject.Visibility = True
        second_source.ViewObject.Visibility = False
        widget.geometry_browser.isolate_button.click()
        _events(8)
        assert first_source.ViewObject.Visibility
        assert not second_source.ViewObject.Visibility
        assert widget.geometry_browser.restore_button.isEnabled()
        widget.geometry_browser.restore_button.click()
        _events(8)
        assert first_source.ViewObject.Visibility
        assert not second_source.ViewObject.Visibility

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
        flow_result = document.addObject("Fem::FemPostPipeline", "FlowResult")
        flow_result.Label = "Fan Flow Result"
        flow_result.addProperty(
            "App::PropertyString",
            "VibeCADOpenFOAMSummary",
            "Results",
        )
        flow_summary = {
            "format_version": 1,
            "pressure_unit": "Pa",
            "velocity_unit": "m/s",
            "density_kg_m3": 1.2,
            "kinematic_viscosity_m2_s": 1.5e-5,
            "turbulence_model": "laminar",
            "converged": True,
            "pressure_range_pa": [0.0, 12.0],
            "velocity_magnitude_range_m_s": [0.0, 2.0],
            "maximum_velocity_m_s": 2.0,
            "boundaries": [
                {
                    "name": "inlet",
                    "kind": "inlet_velocity",
                    "area_m2": 1.0,
                    "geometric_area_m2": 1.0,
                    "pressure_area_average_pa": 12.0,
                    "velocity_area_average_m_s": [2.0, 0.0, 0.0],
                    "outward_volumetric_flow_rate_m3_s": -2.0,
                    "outward_mass_flow_rate_kg_s": -2.4,
                    "condition": {
                        "kind": "inlet_velocity",
                        "velocity_m_s": 2.0,
                        "turbulence": {"kind": "none"},
                    },
                },
                {
                    "name": "outlet",
                    "kind": "outlet_static_pressure",
                    "area_m2": 1.0,
                    "geometric_area_m2": 1.0,
                    "pressure_area_average_pa": 0.0,
                    "velocity_area_average_m_s": [2.0, 0.0, 0.0],
                    "outward_volumetric_flow_rate_m3_s": 2.0,
                    "outward_mass_flow_rate_kg_s": 2.4,
                    "condition": {
                        "kind": "outlet_static_pressure",
                        "pressure_pa": 0.0,
                        "turbulence": {"kind": "none"},
                    },
                },
            ],
        }
        flow_result.VibeCADOpenFOAMSummary = json.dumps(flow_summary)
        analysis.addObject(flow_result)
        candidate_result = document.addObject(
            "Fem::FemPostPipeline", "CandidateFlowResult"
        )
        candidate_result.Label = "Candidate Flow Result"
        candidate_result.addProperty(
            "App::PropertyString",
            "VibeCADOpenFOAMSummary",
            "Results",
        )
        candidate_summary = deepcopy(flow_summary)
        candidate_summary["boundaries"][0]["pressure_area_average_pa"] = 10.0
        candidate_result.VibeCADOpenFOAMSummary = json.dumps(candidate_summary)
        analysis.addObject(candidate_result)
        widget.refresh()
        _events(12)
        assert widget.results_browser.result_combo.count() == 2
        assert widget.results_browser.field_combo.count() >= 2
        assert widget.results_browser.field_table.topLevelItemCount() >= 2
        assert widget.results_browser.show_field_button.isEnabled()
        assert not widget.results_browser.deformation_scale.isEnabled()
        field_units = {
            widget.results_browser.field_table.topLevelItem(index).text(3)
            for index in range(
                widget.results_browser.field_table.topLevelItemCount()
            )
        }
        assert "Pa" in field_units
        assert "m/s" in field_units
        assert all(
            label.text() == "Unavailable"
            for label in widget.results_browser.status_labels.values()
        )
        assert widget.results_browser.upstream_combo.count() == 2
        assert widget.results_browser.downstream_combo.count() == 2
        assert widget.results_browser.flow_boundary_combo.count() == 2
        widget.results_browser.upstream_combo.setCurrentIndex(
            widget.results_browser.upstream_combo.findData("inlet")
        )
        widget.results_browser.downstream_combo.setCurrentIndex(
            widget.results_browser.downstream_combo.findData("outlet")
        )
        widget.results_browser.flow_boundary_combo.setCurrentIndex(
            widget.results_browser.flow_boundary_combo.findData("outlet")
        )
        widget.results_browser.measure_button.click()
        assert "GFA 1 m²" in widget.results_browser.performance_label.text()
        assert "EFA 0.447214 m²" in widget.results_browser.performance_label.text()
        assert "Cd 0.447214" in widget.results_browser.performance_label.text()
        assert widget.results_browser.compare_result_combo.count() == 2
        assert widget.results_browser.compare_button.isEnabled()
        widget.results_browser.compare_upstream_combo.setCurrentIndex(
            widget.results_browser.compare_upstream_combo.findData("inlet")
        )
        widget.results_browser.compare_downstream_combo.setCurrentIndex(
            widget.results_browser.compare_downstream_combo.findData("outlet")
        )
        widget.results_browser.compare_flow_combo.setCurrentIndex(
            widget.results_browser.compare_flow_combo.findData("outlet")
        )
        widget.results_browser.compare_button.click()
        assert "Δp -2 Pa" in widget.results_browser.comparison_label.text()
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
            "geometry_faces=true face_highlight=true face_isolate_restore=true "
            "assignments=true highlight=true isolate_restore=true validation=true",
            "results=true comparison=true",
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
