# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from VibeCADNativeAnalyzeProviderState import compact_analyze_provider_state


def _state(*, with_study: bool) -> dict:
    analysis = {
        "object_name": "Analysis",
        "label": "Airflow",
        "active": True,
        "state_sha256": "analysis-state",
        "member_count": 0,
        "result_graph": {
            "object_count": 0,
            "graph_sha256": "empty-graph",
        },
    }
    workflow = {
        "analysis": {
            "object_name": "Analysis",
            "label": "Airflow",
            "active": True,
            "state_sha256": "analysis-state",
        },
        "graph": {"member_count": 0, "result_object_count": 0},
        "study": {
            "declared": True,
            "physics": ["fluid"],
            "regime": "steady",
            "state_sha256": "study-state",
        },
        "study_inventory": {
            "geometry_source_count": 0,
            "geometry_sources": [],
            "material_count": 0,
            "fluid_material_count": 0,
            "fluid_constraint_count": 0,
            "fluid_constraint_kinds": [],
            "mesh_definition_count": 0,
            "generated_mesh_count": 0,
            "solver_count": 0,
            "solver_kinds": [],
            "result_count": 0,
        },
        "engineering_readiness": {
            "ready_to_mesh": False,
            "ready_to_solve": False,
            "blockers": [
                "missing_geometry",
                "missing_fluid_material",
                "missing_fluid_boundary",
                "missing_mesh_definition",
                "missing_generated_mesh",
                "missing_solver",
            ],
        },
        "solver_runtimes": [],
        "meshes": [],
        "solvers": [],
        "results": [],
    }
    domain = {
        "kind": "analyze",
        "analysis_count": 1 if with_study else 0,
        "analyses": [analysis] if with_study else [],
        "analyses_truncated": False,
        "analysis_workflow_count": 1 if with_study else 0,
        "analysis_workflows": [workflow] if with_study else [],
        "analysis_workflows_truncated": False,
        "provider_scope": {
            "analysis_count": 1 if with_study else 0,
            "undeclared_analysis_count": 0,
            "physics": ["fluid"] if with_study else [],
            "mesh_definition_count": 0,
            "generated_mesh_count": 0,
            "solver_count": 0,
            "result_count": 0,
        },
        "run_status": {
            "phase": "idle",
            "terminal": True,
            "solver_result_count": 0,
        },
        "geometry_source_count": 0,
        "geometry_sources": [],
        "geometry_sources_truncated": False,
        "materials": [],
        "materials_truncated": False,
        "fluid_constraints": [],
        "fluid_constraints_truncated": False,
        "mesh_definitions": [],
        "mesh_definitions_truncated": False,
        "solvers": [],
        "solvers_truncated": False,
        "results": [],
        "results_truncated": False,
        "clipping": {
            "plane_count": 0,
            "planes": [],
            "planes_truncated": False,
            "state_sha256": "empty-clipping",
        },
    }
    return {
        "surface_id": "analyze",
        "document": {"document_uid": "document-a", "document_name": "Fan"},
        "structural_revision": 4,
        "domain": domain,
        "working_set": [analysis] if with_study else [],
    }


def test_blank_analyze_provider_state_contains_only_the_next_decision() -> None:
    compact = compact_analyze_provider_state(_state(with_study=False))

    assert compact == {
        "surface_id": "analyze",
        "document": {"document_uid": "document-a", "document_name": "Fan"},
        "structural_revision": 4,
        "domain": {"kind": "analyze", "study_count": 0},
    }


def test_declared_study_is_not_duplicated_or_padded_with_empty_collections() -> None:
    compact = compact_analyze_provider_state(_state(with_study=True))

    assert set(compact["domain"]) == {"kind", "study_count", "studies"}
    study = compact["domain"]["studies"][0]
    assert study["analysis_name"] == "Analysis"
    assert study["analysis_target"] == {
        "object_name": "Analysis",
        "expected_state_sha256": "analysis-state",
        "expected_member_count": 0,
    }
    assert study["label"] == "Airflow"
    assert study["active"] is True
    assert study["intent"]["physics"] == ["fluid"]
    assert "state_sha256" not in study["intent"]
    assert study["readiness"]["blockers"] == [
        "missing_geometry",
        "missing_fluid_material",
        "missing_fluid_boundary",
        "missing_mesh_definition",
        "missing_generated_mesh",
        "missing_solver",
    ]
    assert "inventory" not in study
    assert "working_set" not in compact


def test_geometry_source_is_published_as_one_ready_to_use_exact_target() -> None:
    state = _state(with_study=True)
    state["domain"]["geometry_source_count"] = 1
    state["domain"]["geometry_sources"] = [
        {
            "object_name": "FluidDomain",
            "label": "Fluid domain",
            "state_sha256": "shape-state",
            "clipping_face_target": {
                "object_name": "FluidDomain",
                "object_id": 42,
                "face_count": 6,
                "state_sha256": "clipping-state",
            },
            "topology": {"solids": 1, "faces": 6},
        }
    ]

    source = compact_analyze_provider_state(state)["domain"]["geometry_sources"][0]

    assert source == {
        "label": "Fluid domain",
        "source_name": "FluidDomain",
        "source_target": {
            "object_name": "FluidDomain",
            "expected_state_sha256": "shape-state",
        },
        "topology": {"solids": 1, "faces": 6},
    }


def test_study_resources_publish_one_ready_to_use_exact_target() -> None:
    state = _state(with_study=True)
    state["domain"]["fluid_constraints"] = [
        {
            "object_name": "Inlet",
            "label": "Inlet",
            "constraint_kind": "fluid_boundary",
            "state_sha256": "constraint-state",
            "definition": {"condition": {"kind": "inlet_velocity"}},
        }
    ]

    constraint = compact_analyze_provider_state(state)["domain"][
        "fluid_constraints"
    ][0]

    assert constraint == {
        "label": "Inlet",
        "constraint_kind": "fluid_boundary",
        "target": {
            "object_name": "Inlet",
            "expected_state_sha256": "constraint-state",
        },
        "definition": {"condition": {"kind": "inlet_velocity"}},
    }


def test_mesh_and_solver_resources_publish_their_focused_names() -> None:
    state = _state(with_study=True)
    state["domain"]["mesh_definitions"] = [
        {
            "object_name": "FEMMeshGmsh",
            "label": "Flow mesh",
            "state_sha256": "mesh-state",
            "mesher": "gmsh",
        }
    ]
    state["domain"]["solvers"] = [
        {
            "object_name": "SolverOpenFOAM",
            "label": "OpenFOAM",
            "state_sha256": "solver-state",
            "solver_kind": "openfoam",
        }
    ]

    domain = compact_analyze_provider_state(state)["domain"]

    assert domain["mesh_definitions"][0]["mesh_name"] == "FEMMeshGmsh"
    assert domain["solvers"][0]["solver_name"] == "SolverOpenFOAM"


def test_flow_result_publishes_the_name_required_by_focused_tools() -> None:
    state = _state(with_study=True)
    state["domain"]["results"] = [
        {
            "object_name": "SolverOpenFOAMResult",
            "label": "OpenFOAM Flow Result",
            "state_sha256": "result-state",
            "result_kind": "pipeline",
            "flow_results": True,
        }
    ]

    result = compact_analyze_provider_state(state)["domain"]["results"][0]

    assert result["result_name"] == "SolverOpenFOAMResult"
