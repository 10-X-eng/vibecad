# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest
from types import SimpleNamespace

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeInspectSchema import analyze_inspect_capability_definition
from VibeCADNativeAnalyzeInspectRuntime import _VARIANTS as INSPECT_VARIANTS
from VibeCADNativeAnalyzeModelSchema import analyze_model_capability_definition
from VibeCADNativeAnalyzeModelRuntime import _arguments
from VibeCADNativeAnalyzeAnalysis import prepare_analysis_create
from VibeCADNativeAnalyzeStudy import (
    STUDY_INTENT_SCHEMA,
    configure_study_intent,
    solver_configuration_blockers,
    study_intent_state,
)
from VibeCADNativeAnalyzeStudy import evaluate_study_readiness, normalize_study_intent


def _variant(definition, operation):
    return next(item for item in definition.variants if item.operation == operation)


def test_study_schema_names_only_the_domains_the_study_solves() -> None:
    assert STUDY_INTENT_SCHEMA["properties"]["physics"]["description"] == (
        "Domains explicitly solved by this study."
    )


def test_analysis_creation_and_inspection_share_one_study_contract() -> None:
    model = analyze_model_capability_definition()
    create = _variant(model, "create_analysis")
    update = _variant(model, "update_study")
    inspect = _variant(analyze_inspect_capability_definition(), "study")

    assert set(create.parameters["properties"]) == {
        "label",
        "default_solver_policy",
        "study",
    }
    assert create.parameters["required"] == ["label", "default_solver_policy"]
    assert update.provider_supplemental is True
    assert update.parameters["properties"]["study"] == create.parameters["properties"]["study"]
    assert inspect.provider_supplemental is True


def test_study_intent_is_composable_and_exact() -> None:
    assert normalize_study_intent(
        {"physics": ["fluid", "thermal"], "regime": "steady"}
    ) == (("fluid", "thermal"), "steady")

    with pytest.raises(NativeAnalyzeError, match="physics values must be unique"):
        normalize_study_intent(
            {"physics": ["thermal", "thermal"], "regime": "steady"}
        )
    with pytest.raises(NativeAnalyzeError, match="only physics and regime"):
        normalize_study_intent(
            {"physics": ["mechanical"], "regime": "steady", "solver": "elmer"}
        )


def test_study_operations_reach_the_runtime_contract() -> None:
    operation, values = _arguments(
        {
            "operation": "create_analysis",
            "label": "Fan flow",
            "default_solver_policy": "none",
            "study": {"physics": ["fluid"], "regime": "steady"},
        }
    )
    assert operation == "create_analysis"
    assert values["study"]["physics"] == ["fluid"]

    operation, values = _arguments(
        {
            "operation": "update_study",
            "target": {
                "object_name": "Analysis",
                "expected_state_sha256": "a" * 64,
                "expected_member_count": 0,
            },
            "study": {"physics": ["mechanical"], "regime": "modal"},
        }
    )
    assert operation == "update_study"
    assert values["study"]["regime"] == "modal"
    assert INSPECT_VARIANTS["study"] == frozenset({"target"})


def test_analysis_preflight_and_document_share_persistent_study_intent() -> None:
    document = SimpleNamespace(Objects=[])
    prepared = prepare_analysis_create(
        document,
        label="Fan flow",
        default_solver_policy="none",
        study={"physics": ["fluid", "thermal"], "regime": "steady"},
    )
    assert prepared.study == (("fluid", "thermal"), "steady")

    class Analysis:
        def __init__(self):
            self.PropertiesList = []
            self._types = {}
            self._groups = {}

        def addProperty(self, property_type, name, group, _description):
            self.PropertiesList.append(name)
            self._types[name] = property_type
            self._groups[name] = group

        def getTypeIdOfProperty(self, name):
            return self._types[name]

        def getGroupOfProperty(self, name):
            return self._groups[name]

    analysis = Analysis()
    state = configure_study_intent(
        analysis,
        {"physics": list(prepared.study[0]), "regime": prepared.study[1]},
    )
    assert state == study_intent_state(analysis)
    assert state["physics"] == ["fluid", "thermal"]
    assert analysis.getGroupOfProperty("StudyPhysics") == "Study"


def test_study_readiness_uses_declared_physics_and_exact_solver_status() -> None:
    inventory = {
        "geometry_source_count": 1,
        "material_kinds": ["fluid", "solid"],
        "mechanical_material_count": 1,
        "thermal_material_count": 1,
        "transient_thermal_material_count": 1,
        "fluid_material_count": 1,
        "equation_kinds": ["flow", "heat"],
        "support_count": 0,
        "load_count": 0,
        "thermal_condition_count": 2,
        "thermal_condition_families": ["initial_temperature", "surface_heat_flux"],
        "fluid_constraint_count": 2,
        "fluid_constraint_kinds": ["fluid_boundary", "initial_pressure"],
        "electromagnetic_constraint_count": 0,
        "mesh_definition_count": 1,
        "generated_mesh_count": 1,
        "solver_kinds": ["elmer"],
        "result_count": 0,
    }
    runtimes = {
        "elmer": {"solver": "elmer", "engine_ready": True, "missing": []}
    }

    readiness = evaluate_study_readiness(
        {"declared": True, "physics": ["fluid", "thermal"], "regime": "transient"},
        inventory,
        runtimes,
    )

    assert readiness["ready_to_mesh"] is True
    assert readiness["ready_to_solve"] is True
    assert readiness["blockers"] == []

    inventory["fluid_constraint_kinds"] = ["initial_pressure"]
    readiness = evaluate_study_readiness(
        {"declared": True, "physics": ["fluid", "thermal"], "regime": "transient"},
        inventory,
        runtimes,
    )
    assert readiness["ready_to_solve"] is False
    assert readiness["blockers"] == ["missing_fluid_boundary"]


def test_study_readiness_does_not_claim_solver_availability() -> None:
    readiness = evaluate_study_readiness(
        {"declared": True, "physics": ["mechanical"], "regime": "steady"},
        {
            "geometry_source_count": 1,
            "material_kinds": ["solid"],
            "mechanical_material_count": 1,
            "thermal_material_count": 0,
            "transient_thermal_material_count": 0,
            "fluid_material_count": 0,
            "equation_kinds": [],
            "support_count": 1,
            "load_count": 1,
            "thermal_condition_count": 0,
            "thermal_condition_families": [],
            "fluid_constraint_count": 0,
            "fluid_constraint_kinds": [],
            "electromagnetic_constraint_count": 0,
            "mesh_definition_count": 1,
            "generated_mesh_count": 1,
            "solver_kinds": ["calculix"],
            "result_count": 0,
        },
        {"calculix": {"solver": "calculix", "engine_ready": False, "missing": ["ccx"]}},
    )
    assert readiness["ready_to_mesh"] is True
    assert readiness["ready_to_solve"] is False
    assert readiness["blockers"] == ["solver_runtime_unavailable:calculix"]


def test_calculix_thermal_study_requires_an_initial_temperature() -> None:
    inventory = {
        "geometry_source_count": 1,
        "mechanical_material_count": 1,
        "thermal_material_count": 1,
        "transient_thermal_material_count": 1,
        "fluid_material_count": 0,
        "equation_kinds": [],
        "support_count": 0,
        "load_count": 0,
        "thermal_condition_count": 2,
        "thermal_condition_families": [
            "boundary_temperature",
            "boundary_temperature",
        ],
        "fluid_constraint_count": 0,
        "fluid_constraint_kinds": [],
        "electromagnetic_constraint_count": 0,
        "mesh_definition_count": 1,
        "generated_mesh_count": 1,
        "solver_kinds": ["calculix"],
        "result_count": 0,
    }
    runtime = {
        "calculix": {"solver": "calculix", "engine_ready": True, "missing": []}
    }

    incomplete = evaluate_study_readiness(
        {"declared": True, "physics": ["thermal"], "regime": "steady"},
        inventory,
        runtime,
    )
    inventory["thermal_condition_count"] = 3
    inventory["thermal_condition_families"].append("initial_temperature")
    complete = evaluate_study_readiness(
        {"declared": True, "physics": ["thermal"], "regime": "steady"},
        inventory,
        runtime,
    )

    assert incomplete["ready_to_solve"] is False
    assert incomplete["blockers"] == ["missing_initial_temperature"]
    assert complete["ready_to_solve"] is True
    assert complete["blockers"] == []


def test_calculix_thermal_solver_configuration_matches_the_study() -> None:
    steady_thermal = {
        "declared": True,
        "physics": ["thermal"],
        "regime": "steady",
    }
    default_solver = [
        {
            "solver_kind": "calculix",
            "suppressed": False,
            "settings": {
                "AnalysisType": "static",
                "ThermoMechSteadyState": False,
                "ThermoMechType": "coupled",
            },
        }
    ]
    thermal_solver = [
        {
            "solver_kind": "calculix",
            "suppressed": False,
            "settings": {
                "AnalysisType": "thermomech",
                "ThermoMechSteadyState": True,
                "ThermoMechType": "pure heat transfer",
            },
        }
    ]

    assert solver_configuration_blockers(steady_thermal, default_solver) == [
        "calculix_requires_thermomech",
        "calculix_requires_pure_heat_transfer",
        "calculix_requires_steady_thermal",
    ]
    assert solver_configuration_blockers(steady_thermal, thermal_solver) == []

    coupled_solver = [
        {
            "solver_kind": "calculix",
            "suppressed": False,
            "settings": {
                "AnalysisType": "thermomech",
                "ThermoMechSteadyState": True,
                "ThermoMechType": "coupled",
            },
        }
    ]
    assert solver_configuration_blockers(
        {
            "declared": True,
            "physics": ["mechanical", "thermal"],
            "regime": "steady",
        },
        coupled_solver,
    ) == []


def test_elmer_mechanical_study_requires_a_mechanical_equation() -> None:
    inventory = {
        "geometry_source_count": 1,
        "mechanical_material_count": 1,
        "thermal_material_count": 0,
        "transient_thermal_material_count": 0,
        "fluid_material_count": 0,
        "equation_kinds": [],
        "support_count": 1,
        "load_count": 1,
        "thermal_condition_count": 0,
        "thermal_condition_families": [],
        "fluid_constraint_count": 0,
        "fluid_constraint_kinds": [],
        "electromagnetic_constraint_count": 0,
        "mesh_definition_count": 1,
        "generated_mesh_count": 1,
        "solver_kinds": ["elmer"],
        "result_count": 0,
    }
    runtime = {"elmer": {"solver": "elmer", "engine_ready": True, "missing": []}}

    missing = evaluate_study_readiness(
        {"declared": True, "physics": ["mechanical"], "regime": "steady"},
        inventory,
        runtime,
    )
    inventory["equation_kinds"] = ["elasticity"]
    complete = evaluate_study_readiness(
        {"declared": True, "physics": ["mechanical"], "regime": "steady"},
        inventory,
        runtime,
    )

    assert missing["ready_to_solve"] is False
    assert "missing_mechanical_equation" in missing["blockers"]
    assert complete["ready_to_solve"] is True
