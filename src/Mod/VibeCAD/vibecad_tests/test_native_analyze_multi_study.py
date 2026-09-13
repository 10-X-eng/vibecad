# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

import VibeCADNativeAnalyzeSolverBindings as solver_bindings
from VibeCADAnalyzeStudySetup import preferred_analysis
from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeInspect import list_studies
from VibeCADNativeAnalyzeInspectRuntime import _VARIANTS as INSPECT_VARIANTS
from VibeCADNativeAnalyzeInspectSchema import analyze_inspect_capability_definition
from VibeCADNativeAnalyzeLabels import assign_prepared_label
from VibeCADNativeAnalyzeCfdLifecycleSchema import ANALYZE_FLUID_MATERIAL
from VibeCADNativeAnalyzeModelRuntime import _FIELDS as MODEL_FIELDS
from VibeCADNativeAnalyzeModelSchema import (
    ANALYZE_CONFIGURE_STUDY,
    ANALYZE_CREATE_STUDY,
    analyze_model_capability_definition,
    analyze_study_lifecycle_capability_definitions,
)
from VibeCADNativeAnalyzeOwnership import (
    owning_study,
    study_history_operations,
    study_resource_scope,
)
from VibeCADNativeAnalyzeProviderScope import (
    _operation_scope,
    _solver_operations,
    analyze_provider_tool_names,
)
from VibeCADNativeAnalyzeStructuralLifecycleSchema import ANALYZE_GRAVITY
from VibeCADNativeAnalyzeSnapshot import _background_job_payloads
from VibeCADNativeAnalyzeSolverSchema import analyze_solver_capability_definition
from VibeCADNativeAnalyzeStudy import (
    configure_study_dependencies,
    configure_study_intent,
    evaluate_study_readiness,
    study_dependency_state,
)
from VibeCADNativeCapabilityRegistry import provider_visible_native_schema
from VibeCADNativeContextManifest import provider_context_actions_for_surface


class _Analysis:
    TypeId = "Fem::FemAnalysis"
    State = ()

    def __init__(self, name: str, object_id: int, label: str | None = None) -> None:
        self.Name = name
        self.ID = object_id
        self.Label = label or name
        self.Group = []
        self.PropertiesList = []
        self.Document = None
        self._property_types = {}

    def isDerivedFrom(self, type_id: str) -> bool:
        return type_id == "Fem::FemAnalysis"

    def addProperty(self, property_type, name, _group, _description):
        self.PropertiesList.append(name)
        self._property_types[name] = property_type

    def getTypeIdOfProperty(self, name):
        return self._property_types[name]


class _Document:
    def __init__(self, *objects) -> None:
        self.Uid = "document-a"
        self.Objects = list(objects)
        for obj in self.Objects:
            obj.Document = self

    def getObject(self, name):
        return next((obj for obj in self.Objects if obj.Name == name), None)


def _studies(count: int = 3):
    studies = tuple(
        _Analysis(f"Analysis{index}", index, f"Study {index}")
        for index in range(1, count + 1)
    )
    document = _Document(*studies)
    for study in studies:
        configure_study_intent(
            study,
            {"physics": ["mechanical"], "regime": "steady"},
        )
    return document, studies


def _variant(definition, operation):
    return next(item for item in definition.variants if item.operation == operation)


def test_study_discovery_is_paged_and_returns_exact_mutation_targets() -> None:
    document, studies = _studies(130)

    page = list_studies(document, offset=128, page_size=64)

    assert page["study_count"] == 130
    assert page["offset"] == 128
    assert page["returned_count"] == 2
    assert page["next_offset"] is None
    assert page["studies"][0]["analysis_name"] == studies[128].Name
    assert page["studies"][0]["analysis_target"]["object_name"] == studies[128].Name
    assert page["studies"][0]["analysis_target"]["expected_member_count"] == 0
    assert len(page["studies"][0]["analysis_target"]["expected_state_sha256"]) == 64


def test_study_catalog_is_a_real_bounded_inspect_operation() -> None:
    schema = _variant(analyze_inspect_capability_definition(), "studies")

    assert INSPECT_VARIANTS["studies"] == frozenset({"offset", "page_size"})
    assert schema.parameters["properties"]["page_size"]["maximum"] == 64
    assert schema.parameters["properties"]["offset"]["default"] == 0
    assert schema.parameters["properties"]["page_size"]["default"] == 64
    assert schema.parameters.get("required", []) == []
    assert schema.transaction_behavior == "none"


def test_focused_study_tools_have_one_obvious_schema_each() -> None:
    definitions = {
        definition.name: definition
        for definition in analyze_study_lifecycle_capability_definitions()
    }

    create = definitions[ANALYZE_CREATE_STUDY].variants[0]
    configure = definitions[ANALYZE_CONFIGURE_STUDY].variants[0]
    assert create.action_ids == frozenset({"VibeCAD_AnalyzeCreateStudyFocused"})
    assert configure.action_ids == frozenset(
        {"VibeCAD_AnalyzeConfigureStudyFocused"}
    )
    assert set(create.parameters["properties"]) == {"label", "physics", "regime"}
    assert create.parameters["required"] == ["label", "physics", "regime"]
    assert set(configure.parameters["properties"]) == {
        "analysis_name",
        "physics",
        "regime",
    }
    assert configure.parameters["required"] == [
        "analysis_name",
        "physics",
        "regime",
    ]

    actions = {
        action.capability_family: action
        for action in provider_context_actions_for_surface("analyze")
    }
    assert actions[ANALYZE_CREATE_STUDY].source_command_id == "FEM_Analysis"
    assert (
        actions[ANALYZE_CONFIGURE_STUDY].source_command_id
        == "VibeCAD_AnalyzeStudySetup"
    )


def test_prepared_label_records_the_exact_host_assigned_value() -> None:
    @dataclass(frozen=True)
    class Prepared:
        label: str
        marker: str

    class UniqueLabelObject:
        def __init__(self) -> None:
            self._label = ""

        @property
        def Label(self) -> str:
            return self._label

        @Label.setter
        def Label(self, value: str) -> None:
            self._label = f"{value}001"

    prepared = Prepared(label="Force", marker="unchanged")

    assigned = assign_prepared_label(UniqueLabelObject(), prepared)

    assert prepared.label == "Force"
    assert assigned.label == "Force001"
    assert assigned.marker == "unchanged"


def test_study_dependencies_are_explicit_persistent_and_acyclic() -> None:
    _document, (loads, thermal, structural) = _studies()

    configure_study_dependencies(thermal, [loads])
    configure_study_dependencies(structural, [thermal])

    assert study_dependency_state(loads) == {"depends_on": []}
    assert study_dependency_state(thermal) == {"depends_on": [loads.Name]}
    assert study_dependency_state(structural) == {"depends_on": [thermal.Name]}
    with pytest.raises(NativeAnalyzeError, match="cycle"):
        configure_study_dependencies(loads, [structural])


def test_study_dependency_edit_has_one_exact_dedicated_contract() -> None:
    schema = _variant(
        analyze_model_capability_definition(),
        "update_study_dependencies",
    )

    assert MODEL_FIELDS["update_study_dependencies"][0] == frozenset(
        {"target", "depends_on"}
    )
    assert set(schema.parameters["properties"]) == {"target", "depends_on"}


def test_resource_ownership_is_exact_and_background_scope_is_per_study() -> None:
    document, (first, second, _third) = _studies()
    first_mesh = SimpleNamespace(Name="FirstMesh", Document=document)
    second_mesh = SimpleNamespace(Name="SecondMesh", Document=document)
    first.Group.append(first_mesh)
    second.Group.append(second_mesh)

    assert owning_study(document, first_mesh) is first
    assert owning_study(document, second_mesh) is second
    assert study_resource_scope(first) != study_resource_scope(second)


def test_study_history_ignores_unrelated_study_operations() -> None:
    document, (first, second, _third) = _studies()
    first_mesh = SimpleNamespace(
        Name="FirstMesh",
        Document=document,
        VibeCADTimelineOwner=None,
    )
    first_refinement = SimpleNamespace(
        Name="FirstRefinement",
        Document=document,
        VibeCADTimelineOwner=first_mesh,
    )
    second_solver = SimpleNamespace(
        Name="SecondSolver",
        Document=document,
        VibeCADTimelineOwner=None,
    )
    second_result = SimpleNamespace(
        Name="SecondResult",
        Document=document,
        VibeCADTimelineOwner=second_solver,
    )
    first.Group.append(first_mesh)
    second.Group.append(second_solver)
    document.VibeCADTimeline = SimpleNamespace(
        Operations=[first_refinement, first_mesh, second_result, second_solver]
    )

    captured = study_history_operations(first)
    document.VibeCADTimeline.Operations.insert(-1, SimpleNamespace(
        Name="AnotherSecondResult",
        Document=document,
        VibeCADTimelineOwner=second_solver,
    ))

    assert captured == (first_refinement, first_mesh)
    assert study_history_operations(first) == captured


def test_study_history_changes_with_an_owned_resource() -> None:
    document, (first, _second, _third) = _studies()
    solver = SimpleNamespace(
        Name="Solver",
        Document=document,
        VibeCADTimelineOwner=None,
    )
    first.Group.append(solver)
    document.VibeCADTimeline = SimpleNamespace(Operations=[solver])
    captured = study_history_operations(first)

    result = SimpleNamespace(
        Name="Result",
        Document=document,
        VibeCADTimelineOwner=solver,
    )
    document.VibeCADTimeline.Operations.insert(0, result)

    assert captured == (solver,)
    assert study_history_operations(first) == (result, solver)


def test_selection_only_focuses_a_study_and_never_supplies_a_global_default() -> None:
    document, (first, second, _third) = _studies()
    member = SimpleNamespace(Name="Load", Document=document)
    second.Group.append(member)

    assert preferred_analysis(document, [member], previous_name="") is second
    assert preferred_analysis(document, [], previous_name=first.Name) is first
    assert preferred_analysis(document, [], previous_name="") is None


def test_selection_focuses_a_study_through_a_nested_solver_result() -> None:
    document, (first, second, _third) = _studies()
    solver = SimpleNamespace(Name="Solver", Document=document, InList=[])
    result = SimpleNamespace(Name="Result", Document=document, InList=[solver])
    first.Group.append(solver)
    document.Objects.extend((solver, result))

    assert preferred_analysis(document, [result], previous_name=second.Name) is first


def test_analyze_background_context_preserves_each_study_scope() -> None:
    jobs = tuple(
        SimpleNamespace(
            document_uid="document-a",
            job_id=f"job-{index}",
            capability_name="analyze.solver_execution.run",
            resource_scope=f"analyze:Analysis{index}",
            phase="preparing",
            progress_percent=20,
            progress_message="Running",
            terminal=False,
            cancel_requested=False,
            error=None,
            result=None,
        )
        for index in (1, 2)
    )

    values = _background_job_payloads("document-a", jobs)

    assert [value["resource_scope"] for value in values] == [
        "analyze:Analysis1",
        "analyze:Analysis2",
    ]


def test_running_study_does_not_disable_an_independent_study() -> None:
    domain = {
        "kind": "analyze",
        "analysis_count": 2,
        "provider_scope": {
            "analysis_count": 2,
            "undeclared_analysis_count": 0,
            "physics": ["mechanical"],
            "mesh_definition_count": 2,
            "generated_mesh_count": 2,
            "solver_count": 2,
            "result_count": 0,
        },
        "analysis_workflow_count": 2,
        "analysis_workflows": [
            {
                "analysis": {"object_name": "Analysis1", "focused": False},
                "study": {
                    "declared": True,
                    "physics": ["mechanical"],
                    "regime": "steady",
                },
                "study_inventory": {
                    "mesh_definition_count": 1,
                    "generated_mesh_count": 1,
                    "solver_count": 1,
                    "solver_kinds": ["calculix"],
                    "equation_kinds": [],
                    "result_count": 0,
                },
                "engineering_readiness": {
                    "ready_to_solve": True,
                    "blockers": [],
                },
            },
            {
                "analysis": {"object_name": "Analysis2", "focused": False},
                "study": {
                    "declared": True,
                    "physics": ["mechanical"],
                    "regime": "steady",
                },
                "study_inventory": {
                    "mesh_definition_count": 1,
                    "generated_mesh_count": 1,
                    "solver_count": 1,
                    "solver_kinds": ["calculix"],
                    "equation_kinds": [],
                    "result_count": 0,
                },
                "engineering_readiness": {
                    "ready_to_solve": True,
                    "blockers": [],
                },
            },
        ],
        "background_jobs": [
            {
                "job_id": "job-1",
                "resource_scope": "analyze:Analysis1",
                "terminal": False,
            }
        ],
    }
    available = (
        "analyze.generate_gmsh",
        "analyze.run_solver",
        "native.job",
    )

    assert set(analyze_provider_tool_names(domain, available)) == set(available)
    domain["analysis_workflows"][0]["analysis"]["focused"] = True
    assert set(analyze_provider_tool_names(domain, available)) == {"native.job"}


def test_focused_study_tool_choices_ignore_resources_owned_by_other_studies() -> None:
    domain = {
        "kind": "analyze",
        "analysis_count": 2,
        "provider_scope": {
            "analysis_count": 2,
            "undeclared_analysis_count": 0,
            "physics": ["mechanical", "fluid"],
            "mesh_definition_count": 1,
            "generated_mesh_count": 0,
            "solver_count": 0,
            "result_count": 0,
        },
        "analysis_workflow_count": 2,
        "analysis_workflows": [
            {
                "analysis": {"object_name": "Analysis1", "focused": False},
                "study": {
                    "declared": True,
                    "physics": ["mechanical"],
                    "regime": "steady",
                },
                "study_inventory": {
                    "material_count": 1,
                    "material_kinds": ["solid"],
                    "load_kinds": ["gravity"],
                    "mesh_definition_count": 1,
                    "mesh_kinds": ["gmsh"],
                    "generated_mesh_count": 0,
                    "solver_count": 0,
                    "result_count": 0,
                },
                "engineering_readiness": {
                    "ready_to_solve": False,
                    "blockers": ["missing_solver"],
                },
            },
            {
                "analysis": {"object_name": "Analysis2", "focused": True},
                "study": {
                    "declared": True,
                    "physics": ["mechanical", "fluid"],
                    "regime": "steady",
                },
                "study_inventory": {
                    "material_count": 0,
                    "material_kinds": [],
                    "fluid_constraint_kinds": [],
                    "load_kinds": [],
                    "mesh_definition_count": 0,
                    "mesh_kinds": [],
                    "generated_mesh_count": 0,
                    "solver_count": 0,
                    "result_count": 0,
                },
                "engineering_readiness": {
                    "ready_to_solve": False,
                    "blockers": [
                        "missing_fluid_material",
                        "missing_fluid_boundary",
                        "missing_mesh_definition",
                        "missing_generated_mesh",
                        "missing_solver",
                    ],
                },
            },
        ],
        "material_count": 1,
        "materials": [{"material_kind": "solid"}],
        "loads": [{"load_kind": "gravity"}],
        "mesh_definitions": [{"mesher": "gmsh"}],
    }

    assert ANALYZE_GRAVITY in analyze_provider_tool_names(
        domain,
        (ANALYZE_GRAVITY,),
    )
    assert _operation_scope(
        domain,
        {ANALYZE_FLUID_MATERIAL: ("create", "update")},
        (ANALYZE_FLUID_MATERIAL,),
        has_selection=True,
    )[ANALYZE_FLUID_MATERIAL] == ("create",)


def test_multiple_solver_configurations_are_valid_independent_run_targets() -> None:
    readiness = evaluate_study_readiness(
        {"declared": True, "physics": ["mechanical"], "regime": "steady"},
        {
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
            "mesh_definition_count": 2,
            "generated_mesh_count": 2,
            "solver_kinds": ["calculix", "elmer"],
            "solver_configuration_blockers": [],
            "result_count": 0,
        },
        {
            "calculix": {"solver": "calculix", "engine_ready": True},
            "elmer": {"solver": "elmer", "engine_ready": True},
        },
    )

    assert readiness["ready_to_solve"] is True
    assert readiness["blockers"] == []


def test_solver_creation_remains_available_for_an_established_study() -> None:
    domain = {
        "kind": "analyze",
        "analysis_count": 1,
        "analysis_workflow_count": 1,
        "analysis_workflows": [
            {
                "analysis": {"object_name": "Analysis1", "focused": True},
                "study": {
                    "declared": True,
                    "physics": ["mechanical"],
                    "regime": "steady",
                },
                "study_inventory": {
                    "solver_count": 1,
                    "solver_kinds": ["calculix"],
                },
                "engineering_readiness": {
                    "ready_to_solve": True,
                    "blockers": [],
                },
            }
        ],
        "provider_scope": {
            "analysis_count": 1,
            "undeclared_analysis_count": 0,
            "physics": ["mechanical"],
            "mesh_definition_count": 1,
            "generated_mesh_count": 1,
            "solver_count": 1,
            "result_count": 0,
        },
    }

    assert set(
        _solver_operations(
            domain,
            (
                "create_calculix",
                "create_elmer",
                "create_mystran",
                "create_z88",
            ),
        )
    ) == {
        "create_calculix",
        "create_elmer",
        "create_mystran",
        "create_z88",
    }


def test_solver_creation_accepts_a_current_study_name_or_an_exact_target() -> None:
    schema = _variant(analyze_solver_capability_definition(), "create_calculix")

    assert set(schema.parameters["properties"]) == {"analysis", "label"}
    assert schema.parameters["required"] == ["analysis", "label"]
    assert [
        branch["type"]
        for branch in schema.parameters["properties"]["analysis"]["anyOf"]
    ] == ["string", "object"]
    exact = schema.provider_parameters()
    validator = Draft202012Validator(exact)
    assert not list(
        validator.iter_errors(
            {
                "operation": "create_calculix",
                "analysis": "Analysis001",
                "label": "CalculiX",
            }
        )
    )
    assert not list(
        validator.iter_errors(
            {
                "operation": "create_calculix",
                "analysis": {
                    "object_name": "Analysis001",
                    "expected_state_sha256": "a" * 64,
                    "expected_member_count": 4,
                },
                "label": "CalculiX",
            }
        )
    )


def test_solver_name_binding_resolves_the_current_exact_target(monkeypatch) -> None:
    runtime = object.__new__(solver_bindings.NativeAnalyzeSolverRuntime)
    target = {
        "object_name": "Analysis001",
        "expected_state_sha256": "b" * 64,
        "expected_member_count": 5,
    }
    captured = {}

    monkeypatch.setattr(
        solver_bindings,
        "current_analysis_target",
        lambda actual_runtime, name: target
        if actual_runtime is runtime and name == "Analysis001"
        else None,
    )

    def execute(actual_runtime, arguments, *, ticket):
        assert actual_runtime is runtime
        captured.update(arguments)
        assert ticket == "ticket"
        return {"created_solver": {"object_name": "SolverCalculiX"}}

    monkeypatch.setattr(solver_bindings.NativeAnalyzeSolverRuntime, "execute", execute)

    result = solver_bindings._execute(
        SimpleNamespace(
            runtime=runtime,
            ticket="ticket",
            arguments={
                "operation": "create_calculix",
                "analysis": "Analysis001",
                "label": "CalculiX",
            },
        )
    )

    assert captured == {
        "operation": "create_calculix",
        "analysis": target,
        "label": "CalculiX",
    }
    assert result["analysis_name"] == "Analysis001"


def test_solver_provider_surface_preserves_the_required_study_choice() -> None:
    definition = analyze_solver_capability_definition()
    surface_schema = provider_visible_native_schema(
        definition.provider_schema(("create_calculix", "create_elmer"))
    )["parameters"]
    validator = Draft202012Validator(surface_schema)

    assert list(
        validator.iter_errors(
            {"operation": "create_calculix", "label": "CalculiX"}
        )
    )
    assert not list(
        validator.iter_errors(
            {
                "operation": "create_calculix",
                "analysis": "Analysis001",
                "label": "CalculiX",
            }
        )
    )
