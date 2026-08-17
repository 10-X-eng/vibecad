# SPDX-License-Identifier: LGPL-2.1-or-later

"""Aero is a first-class ribbon surface and turn-start assistant context."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from VibeCADAeroContext import document_aero_summary
from VibeCADCore import VibeCADService
from VibeCADNativeActionManifest import (
    KNOWN_ACTIONS_BY_SURFACE,
    OPTIONAL_ACTIONS_BY_SURFACE,
    classify_native_surface,
)
from VibeCADRibbonSurface import RibbonSurface, SURFACE_IDS


REPO = Path(__file__).resolve().parents[4]


def test_aero_surface_id_is_registered() -> None:
    assert "aero" in SURFACE_IDS
    assert "aero" in KNOWN_ACTIONS_BY_SURFACE
    assert "aero" in OPTIONAL_ACTIONS_BY_SURFACE
    assert set(OPTIONAL_ACTIONS_BY_SURFACE) == set(KNOWN_ACTIONS_BY_SURFACE)
    for command in (
        "VibeCADAero_Analyze",
        "VibeCADAero_Section",
        "VibeCADAero_VLM",
        "VibeCADAero_ExportJSBSim",
        "VibeCADAero_Report",
    ):
        assert command in KNOWN_ACTIONS_BY_SURFACE["aero"]
        assert command in OPTIONAL_ACTIONS_BY_SURFACE["model"]


def test_cpp_ribbon_places_aero_tab_after_parameters() -> None:
    ribbon = (REPO / "src/Gui/VibeCADRibbon.cpp").read_text(encoding="utf-8")
    assert 'constexpr std::array<DomainDefinition, 8> domains' in ribbon
    parameters = ribbon.index(
        '{"Parameters", "SpreadsheetWorkbench", "parameters"}'
    )
    aero = ribbon.index('{"Aero", "VibeCADAeroWorkbench", "aero"}')
    drawing = ribbon.index('{"Drawing", "TechDrawWorkbench", "drawing"}')
    manufacture = ribbon.index(
        '{"Manufacture", "CAMWorkbench", "manufacture"}'
    )
    assert drawing < parameters < aero
    assert manufacture < drawing
    assert "aeroGroups" in ribbon
    assert "VibeCADAeroWorkspaceHost" in ribbon
    assert "VibeCADAero_Analyze" in ribbon
    assert "isAeroTab" in ribbon
    assert "modelPageGroups" in ribbon
    assert 'QObject::tr("Aero")' in ribbon
    assert 'if (activeWorkbench == "VibeCADAeroWorkbench")' not in ribbon
    assert "return aeroGroups();" not in ribbon
    activate = ribbon[ribbon.index("void activateDomain(int index)") :]
    activate = activate[: activate.index("void syncDomainToWorkbench")]
    assert "isAeroTabIndex(index)" in activate
    assert "activateWorkbench(workbench" in activate
    assert activate.index("isAeroTabIndex(index)") < activate.index(
        "activateWorkbench(workbench"
    )


def _aero_manifest() -> dict[str, object]:
    def action(command_id: str, label: str) -> dict[str, object]:
        return {
            "command_id": command_id,
            "kind": "command",
            "label": label,
            "available": True,
        }

    return {
        "schema_version": 1,
        "surface_id": "aero",
        "groups": [
            {
                "label": "View",
                "actions": [
                    action("Std_ViewFitAll", "Fit all"),
                    action("Std_ViewIsometric", "Isometric"),
                    action("VibeCAD_ToggleGrid", "Grid"),
                ],
            },
            {
                "label": "Actions",
                "actions": [
                    action("VibeCADAero_Analyze", "Analyze"),
                    action("VibeCADAero_Section", "Section"),
                    action("VibeCADAero_VLM", "VLM"),
                    action("VibeCADAero_ExportJSBSim", "JSBSim"),
                    action("VibeCADAero_Report", "Report"),
                ],
            },
            {
                "label": "Inspect",
                "actions": [
                    action("Std_Measure", "Measure"),
                    action("Std_MassProperties", "Mass"),
                    action("Inspection_VisualInspection", "Visual"),
                    action("Inspection_InspectElement", "Element"),
                    action("Part_CheckGeometry", "Check"),
                ],
            },
        ],
    }


def test_aero_surface_classifies_without_unknown_actions() -> None:
    surface = RibbonSurface.from_manifest(_aero_manifest(), revision=1)
    plans = classify_native_surface(surface)
    assert [plan.command_id for plan in plans] == list(surface.command_ids)
    human = {
        plan.command_id
        for plan in plans
        if plan.classification.human_only
    }
    assert {
        "VibeCADAero_Analyze",
        "VibeCADAero_Section",
        "VibeCADAero_VLM",
        "VibeCADAero_ExportJSBSim",
        "VibeCADAero_Report",
    } <= human


def test_python_surface_list_includes_aero() -> None:
    source = (REPO / "src/Mod/VibeCAD/VibeCADRibbonSurface.py").read_text(
        encoding="utf-8"
    )
    assert '"aero"' in source


def test_document_aero_summary_reads_report_and_config() -> None:
    report = SimpleNamespace(
        Name="AeroReport",
        Label="AeroReport",
        CL=1.516,
        CD=0.242,
        CM=0.733,
        CLalpha=7.3,
        Cmalpha=4.68,
        PitchUnstable=True,
        Re=25000.0,
        V_loaf=4.19,
        P_hover=24.2,
        P_cruise=1.51,
        Source="AeroBuildup",
        Airfoil="e63",
        GeometrySource="AeroConfig",
        JSBSimPlantPath="/tmp/jsbsim/voider.xml",
        span_mm=500.0,
        chord_mm=90.0,
        gap_c=1.4,
        stagger_c=1.15,
        decalage_deg=2.0,
        auw_g=149.6,
        alpha_deg=4.0,
        n_props=2.0,
        prop_diameter_mm=178.0,
        thrust_to_weight=1.9,
    )
    config = SimpleNamespace(
        Name="AeroConfig",
        Label="AeroConfig",
        vehicle_type="tailsitter",
        airfoil="e63",
        span_mm=500.0,
        chord_mm=90.0,
        gap_c=1.4,
        stagger_c=1.15,
        decalage_deg=2.0,
        auw_g=149.6,
        alpha_deg=4.0,
        n_props=2.0,
        prop_diameter_mm=178.0,
        thrust_to_weight=1.9,
    )

    def get_object(name: str):
        return {"AeroReport": report, "AeroConfig": config}.get(name)

    doc = SimpleNamespace(
        Name="Voider",
        Objects=[config, report],
        getObject=get_object,
        JSBSimPlantPath="/tmp/jsbsim/voider.xml",
    )
    summary = document_aero_summary(doc)
    assert summary["available"] is True
    assert summary["vehicle_type"] == "tailsitter"
    assert summary["airfoil"] == "e63"
    assert summary["CL"] == 1.516
    assert summary["CD"] == 0.242
    assert summary["CM"] == 0.733
    assert summary["CLalpha"] == 7.3
    assert summary["Cmalpha"] == 4.68
    assert summary["PitchUnstable"] is True
    assert summary["Re"] == 25000.0
    assert summary["V_loaf"] == 4.19
    assert summary["P_hover"] == 24.2
    assert summary["P_cruise"] == 1.51
    assert summary["source"] == "AeroBuildup"
    assert summary["jsbsim_path"] == "/tmp/jsbsim/voider.xml"
    assert summary["geometry_source"] == "AeroConfig"
    assert summary["geometry"]["span_mm"] == 500.0
    assert summary["geometry"]["chord_mm"] == 90.0
    assert "trace" not in summary
    assert "solver_log" not in summary


def test_document_aero_summary_without_solve_keeps_config_geometry() -> None:
    config = SimpleNamespace(
        Name="AeroConfig",
        Label="AeroConfig",
        vehicle_type="airplane",
        airfoil="e63",
        span_mm=800.0,
        chord_mm=120.0,
        gap_c=1.2,
        stagger_c=1.0,
        decalage_deg=1.0,
        auw_g=250.0,
        alpha_deg=3.0,
        n_props=1.0,
        prop_diameter_mm=200.0,
        thrust_to_weight=0.4,
    )
    doc = SimpleNamespace(
        Name="Plane",
        Objects=[config],
        getObject=lambda name: config if name == "AeroConfig" else None,
    )
    summary = document_aero_summary(doc)
    assert summary["available"] is False
    assert summary["vehicle_type"] == "airplane"
    assert summary["airfoil"] == "e63"
    assert summary["geometry"]["span_mm"] == 800.0
    assert summary["geometry"]["chord_mm"] == 120.0
    assert "CL" not in summary


def test_provider_context_summary_includes_aero_when_report_exists() -> None:
    report = SimpleNamespace(
        Name="AeroReport",
        Label="AeroReport",
        CL=0.77,
        CD=0.04,
        CM=-0.14,
        CLalpha=4.8,
        Cmalpha=-0.7,
        PitchUnstable=False,
        Re=40000.0,
        V_loaf=7.1,
        P_hover=17.0,
        P_cruise=3.5,
        Source="NeuralFoil",
        Airfoil="e63",
        GeometrySource="AeroConfig",
        JSBSimPlantPath="",
        span_mm=500.0,
        chord_mm=90.0,
    )
    config = SimpleNamespace(
        Name="AeroConfig",
        Label="AeroConfig",
        vehicle_type="multirotor",
        airfoil="e63",
        span_mm=500.0,
        chord_mm=90.0,
        n_props=4.0,
        prop_diameter_mm=178.0,
        thrust_to_weight=1.9,
    )
    doc = SimpleNamespace(
        Name="Drone",
        Uid="doc-aero",
        Objects=[config, report],
        getObject=lambda name: {
            "AeroReport": report,
            "AeroConfig": config,
        }.get(name),
    )
    service = object.__new__(VibeCADService)
    service.active_workbench_name = lambda: "VibeCADAeroWorkbench"
    service.modeling_engine = lambda: "vibescript"
    service._active_document = lambda: doc
    service.provider_turn_document_summary = lambda: {
        "name": "Drone",
        "uid": "doc-aero",
        "object_count": 2,
        "edit_object": None,
    }
    service.provider_turn_selection_summary = lambda: {
        "selection_count": 0,
        "selection": [],
    }
    service.view_screenshot_summary = lambda: {"captured": False}
    service.provider_reference_image_attachments = lambda: {
        "count": 0,
        "images": [],
    }

    context = service.provider_context_summary()
    assert "aero" in context
    assert context["aero"]["available"] is True
    assert context["aero"]["vehicle_type"] == "multirotor"
    assert context["aero"]["CL"] == 0.77
    assert context["aero"]["source"] == "NeuralFoil"
    assert context["aero"]["PitchUnstable"] is False
