# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compact engineering completeness for one FEM study."""

from __future__ import annotations

from typing import Any, Callable

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeStudy import (
    evaluate_study_readiness,
    solver_configuration_blockers,
    study_dependency_state,
    study_intent_state,
)
from VibeCADNativeSnapshot import concise_object


_TARGET_INVALID = "NATIVE_ANALYZE_TARGET_TYPE_INVALID"


def _references(state: dict[str, Any]) -> set[str]:
    return {
        str(item.get("object_name") or "")
        for item in list(state.get("references") or ())
        if isinstance(item, dict) and item.get("object_name")
    }


def _read_member(
    member: Any,
    readers: tuple[tuple[str, Callable[[Any], dict[str, Any]]], ...],
) -> tuple[str, dict[str, Any]] | None:
    for category, reader in readers:
        try:
            return category, reader(member)
        except NativeAnalyzeError as exc:
            if exc.error_code != _TARGET_INVALID:
                raise
    return None


def study_inventory(
    analysis: Any,
    *,
    mesh_state_reader: Callable[[Any], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from VibeCADNativeAnalyzeAssignments import validate_assignments
    from VibeCADNativeAnalyzeConnectionState import connection_state
    from VibeCADNativeAnalyzeConstraintState import electromagnetic_constraint_state
    from VibeCADNativeAnalyzeElementState import element_definition_state
    from VibeCADNativeAnalyzeFluidState import fluid_constraint_state
    from VibeCADNativeAnalyzeGeometricalState import geometrical_feature_state
    from VibeCADNativeAnalyzeLoadState import load_state
    from VibeCADNativeAnalyzeState import material_state
    from VibeCADNativeAnalyzeMeshState import fem_mesh_definition_state
    from VibeCADNativeAnalyzeMeshRefinementState import mesh_refinement_state
    from VibeCADNativeAnalyzeSolverState import solver_state
    from VibeCADNativeAnalyzeSupportState import support_condition_state
    from VibeCADNativeAnalyzeThermalState import thermal_condition_state
    from VibeCADNativeAnalyzeEquationState import equation_state

    mesh_reader = mesh_state_reader or fem_mesh_definition_state
    readers = (
        ("material", material_state),
        ("element", element_definition_state),
        ("electromagnetic", electromagnetic_constraint_state),
        ("fluid", fluid_constraint_state),
        ("geometrical", geometrical_feature_state),
        ("support", support_condition_state),
        ("connection", connection_state),
        ("load", load_state),
        ("thermal", thermal_condition_state),
        ("mesh", mesh_reader),
        ("mesh_refinement", mesh_refinement_state),
        ("solver", solver_state),
    )
    states: dict[str, list[dict[str, Any]]] = {
        category: [] for category, _reader in readers
    }
    objects: dict[str, list[Any]] = {category: [] for category, _reader in readers}
    geometry_sources: set[str] = set()
    for member in tuple(getattr(analysis, "Group", ()) or ()):
        resolved = _read_member(member, readers)
        if resolved is None:
            continue
        category, state = resolved
        states[category].append(state)
        objects[category].append(member)
        geometry_sources.update(_references(state))
        source = state.get("source")
        if isinstance(source, dict) and source.get("object_name"):
            geometry_sources.add(str(source["object_name"]))

    equations = []
    active_solver_names = {
        str(state["object_name"])
        for state in states["solver"]
        if not bool(state.get("suppressed"))
    }
    for solver in objects["solver"]:
        for child in tuple(getattr(solver, "Group", ()) or ()):
            try:
                state = equation_state(child)
            except NativeAnalyzeError as exc:
                if exc.error_code == _TARGET_INVALID:
                    continue
                raise
            if str(state.get("solver") or "") in active_solver_names:
                equations.append(state)

    material_kinds = [str(state["material_kind"]) for state in states["material"]]

    def kinds(category: str, field: str) -> list[str]:
        return list(
            dict.fromkeys(
                str(state[field])
                for state in states[category]
                if str(state.get(field) or "")
            )
        )
    mechanical_material_count = 0
    thermal_material_count = 0
    transient_thermal_material_count = 0
    fluid_material_count = 0
    for state in states["material"]:
        kind = str(state["material_kind"])
        properties = dict(state.get("properties") or {})
        if kind in {"solid", "reinforced"} and all(
            name in properties for name in ("young_modulus_mpa", "poisson_ratio")
        ):
            mechanical_material_count += 1
        if float(properties.get("thermal_conductivity_w_m_k", 0) or 0) > 0:
            thermal_material_count += 1
            if all(
                float(properties.get(name, 0) or 0) > 0
                for name in ("density_kg_m3", "specific_heat_j_kg_k")
            ):
                transient_thermal_material_count += 1
        if kind == "fluid" and all(
            float(properties.get(name, 0) or 0) > 0
            for name in ("density_kg_m3", "kinematic_viscosity_m2_s")
        ):
            fluid_material_count += 1

    active_solver_states = [
        state for state in states["solver"] if not bool(state.get("suppressed"))
    ]
    intent = study_intent_state(analysis)
    solver_configurations = [
        {
            "object_name": str(state["object_name"]),
            "solver_kind": str(state["solver_kind"]),
            "blockers": solver_configuration_blockers(intent, [state]),
        }
        for state in active_solver_states
    ]
    configuration_blockers = list(
        dict.fromkeys(
            blocker
            for configuration in solver_configurations
            for blocker in configuration["blockers"]
        )
    )
    assignment_validation = validate_assignments(analysis)
    mesh_coverage = assignment_validation.get("mesh_coverage")
    return {
        "geometry_source_count": len(geometry_sources),
        "geometry_sources": sorted(geometry_sources),
        "material_count": len(states["material"]),
        "material_kinds": material_kinds,
        "mechanical_material_count": mechanical_material_count,
        "thermal_material_count": thermal_material_count,
        "transient_thermal_material_count": transient_thermal_material_count,
        "fluid_material_count": fluid_material_count,
        "equation_count": len(equations),
        "equation_kinds": [str(state["equation_kind"]) for state in equations],
        "element_definition_count": len(states["element"]),
        "element_definition_kinds": kinds("element", "element_definition_kind"),
        "support_count": len(states["support"]),
        "support_condition_kinds": kinds("support", "condition_kind"),
        "connection_count": len(states["connection"]),
        "connection_kinds": kinds("connection", "connection_kind"),
        "load_count": len(states["load"]),
        "load_kinds": kinds("load", "load_kind"),
        "thermal_condition_count": len(states["thermal"]),
        "thermal_condition_families": [
            str(state["thermal_mode"]) for state in states["thermal"]
        ],
        "fluid_constraint_count": len(states["fluid"]),
        "fluid_constraint_kinds": [
            str(state["constraint_kind"]) for state in states["fluid"]
        ],
        "electromagnetic_constraint_count": len(states["electromagnetic"]),
        "electromagnetic_constraint_kinds": kinds(
            "electromagnetic", "constraint_kind"
        ),
        "geometrical_feature_count": len(states["geometrical"]),
        "geometrical_feature_kinds": kinds("geometrical", "feature_kind"),
        "mesh_definition_count": len(states["mesh"]),
        "mesh_kinds": kinds("mesh", "mesher"),
        "generated_mesh_count": sum(
            bool(state.get("generated")) for state in states["mesh"]
        ),
        "mesh_refinement_count": len(states["mesh_refinement"]),
        "mesh_refinement_kinds": kinds(
            "mesh_refinement", "refinement_mode"
        ),
        "solver_count": len(states["solver"]),
        "solver_kinds": [str(state["solver_kind"]) for state in active_solver_states],
        "solver_configurations": solver_configurations,
        "solver_configuration_blockers": configuration_blockers,
        "assignment_validation_issue_count": int(
            assignment_validation.get("issue_count", 0) or 0
        ),
        "mesh_coverage_issue_count": int(
            mesh_coverage.get("issue_count", 0) or 0
        )
        if isinstance(mesh_coverage, dict)
        else 0,
        "result_count": sum(
            int(state.get("result_count", 0) or 0) for state in states["solver"]
        ),
    }


def study_state(
    analysis: Any,
    *,
    mesh_state_reader: Callable[[Any], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    intent = study_intent_state(analysis)
    inventory = study_inventory(analysis, mesh_state_reader=mesh_state_reader)
    solver_kinds = set(inventory["solver_kinds"])
    if solver_kinds:
        from femsolver.runtime import solver_runtime_statuses

        statuses = solver_runtime_statuses(solver_kinds)
    else:
        statuses = ()
    runtimes = {str(status["solver"]): status for status in statuses}
    return {
        "analysis": concise_object(analysis),
        "intent": intent,
        "dependencies": study_dependency_state(analysis),
        "inventory": inventory,
        "solver_runtimes": list(statuses),
        "readiness": evaluate_study_readiness(intent, inventory, runtimes),
    }
