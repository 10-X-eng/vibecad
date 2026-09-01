# SPDX-License-Identifier: LGPL-2.1-or-later

"""Select Analyze provider families from persistent study state."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from VibeCADNativeAnalyzeFluidCreateSchema import (
    ANALYZE_EDIT_FLUID_BOUNDARY,
    ANALYZE_FLUID_BOUNDARY,
    ANALYZE_INITIAL_PRESSURE,
    ANALYZE_INITIAL_VELOCITY,
)
from VibeCADNativeAnalyzeCfdLifecycleSchema import (
    ANALYZE_FLUID_MATERIAL,
    ANALYZE_OPENFOAM_SOLVER,
)
from VibeCADNativeAnalyzeMeshLifecycleSchema import (
    ANALYZE_EDIT_GMSH_MESH,
    ANALYZE_FLOW_MESH,
    ANALYZE_GENERATE_GMSH,
    ANALYZE_GMSH_MESH,
    ANALYZE_SOLID_MESH,
)
from VibeCADNativeAnalyzeLocalMeshSchema import (
    ANALYZE_EDIT_LOCAL_MESH_SIZE,
    ANALYZE_LOCAL_MESH_SIZE,
)
from VibeCADNativeAnalyzeRunSchema import ANALYZE_RUN_SOLVER
from VibeCADNativeAnalyzeSolidDomainSchema import ANALYZE_SOLID_DOMAIN
from VibeCADNativeAnalyzeFlowResultSchema import (
    ANALYZE_COMPARE_FLOW,
    ANALYZE_FLOW_PERFORMANCE,
    ANALYZE_FLOW_RESULT,
    ANALYZE_SHOW_FLOW,
)
from VibeCADNativeAnalyzeMechanicalResultSchema import (
    ANALYZE_MECHANICAL_RESULTS,
    ANALYZE_SHOW_MECHANICAL,
)
from VibeCADNativeAnalyzeThermalResultSchema import (
    ANALYZE_SHOW_TEMPERATURE,
    ANALYZE_TEMPERATURE_RESULTS,
)
from VibeCADNativeAnalyzeInspectSchema import ANALYZE_MATERIAL_CATALOG
from VibeCADNativeAnalyzeStructuralLifecycleSchema import (
    ANALYZE_CATALOG_MATERIAL,
    ANALYZE_CENTRIFUGAL,
    ANALYZE_CUSTOM_MATERIAL,
    ANALYZE_EDIT_CENTRIFUGAL,
    ANALYZE_EDIT_DISPLACEMENT_SUPPORT,
    ANALYZE_EDIT_FIXED_SUPPORT,
    ANALYZE_EDIT_FORCE,
    ANALYZE_EDIT_GRAVITY,
    ANALYZE_EDIT_PRESSURE,
    ANALYZE_EDIT_RIGID_COUPLING,
    ANALYZE_EDIT_SPRING_SUPPORT,
    ANALYZE_DISPLACEMENT_SUPPORT,
    ANALYZE_FIXED_SUPPORT,
    ANALYZE_FORCE,
    ANALYZE_GRAVITY,
    ANALYZE_PRESSURE,
    ANALYZE_RIGID_COUPLING,
    ANALYZE_SOLID_REGION_MATERIAL,
    ANALYZE_SOLID_MATERIAL,
    ANALYZE_SPRING_SUPPORT,
)
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityRegistry,
    NativeProviderSurface,
    _provider_schema_operations,
    project_native_provider_operations,
    project_native_provider_surface,
)


_SHARED = frozenset(
    {
        "core.capture_view_screenshot",
        "document.query",
        "document.save",
        "document.undo",
        "object.properties",
        "selection.query",
        "view.control",
    }
)
_SETUP = frozenset({"analyze.model", ANALYZE_MATERIAL_CATALOG})
_STUDY = frozenset(
    {
        "analyze.mesh",
    }
)

_ASSIGNMENT_COUNT_NAMES = (
    "material_count",
    "element_definition_count",
    "electromagnetic_constraint_count",
    "fluid_constraint_count",
    "geometrical_feature_count",
    "support_condition_count",
    "connection_count",
    "load_count",
    "thermal_condition_count",
    "mesh_definition_count",
    "mesh_refinement_count",
)
_ASSIGNMENT_TRUNCATION_NAMES = (
    "materials_truncated",
    "element_definitions_truncated",
    "electromagnetic_constraints_truncated",
    "fluid_constraints_truncated",
    "geometrical_features_truncated",
    "support_conditions_truncated",
    "connections_truncated",
    "loads_truncated",
    "thermal_conditions_truncated",
    "mesh_definitions_truncated",
    "mesh_refinements_truncated",
)

_LIVE_KIND_SOURCES = {
    "analyze.connection": ("connections", "connection_kind", "connections_truncated"),
    "analyze.electromagnetic": (
        "electromagnetic_constraints",
        "constraint_kind",
        "electromagnetic_constraints_truncated",
    ),
    "analyze.fluid": (
        "fluid_constraints",
        "constraint_kind",
        "fluid_constraints_truncated",
    ),
    "analyze.geometrical": (
        "geometrical_features",
        "feature_kind",
        "geometrical_features_truncated",
    ),
    "analyze.geometry": (
        "element_definitions",
        "element_definition_kind",
        "element_definitions_truncated",
    ),
    "analyze.load": ("loads", "load_kind", "loads_truncated"),
    "analyze.mesh_refinement": (
        "mesh_refinements",
        "refinement_mode",
        "mesh_refinements_truncated",
    ),
    "analyze.structured_mesh": (
        "mesh_refinements",
        "refinement_mode",
        "mesh_refinements_truncated",
    ),
    "analyze.support": (
        "support_conditions",
        "condition_kind",
        "support_conditions_truncated",
    ),
    "analyze.thermal": (
        "thermal_conditions",
        "thermal_mode",
        "thermal_conditions_truncated",
    ),
}
_PHYSICS = {
    "mechanical": frozenset(
        {
            "analyze.geometry",
            "analyze.geometrical",
            "analyze.connection",
            "analyze.solver",
            ANALYZE_SOLID_MESH,
            ANALYZE_CATALOG_MATERIAL,
            ANALYZE_CUSTOM_MATERIAL,
            ANALYZE_SOLID_REGION_MATERIAL,
            ANALYZE_FIXED_SUPPORT,
            ANALYZE_EDIT_FIXED_SUPPORT,
            ANALYZE_RIGID_COUPLING,
            ANALYZE_EDIT_RIGID_COUPLING,
            ANALYZE_DISPLACEMENT_SUPPORT,
            ANALYZE_EDIT_DISPLACEMENT_SUPPORT,
            ANALYZE_SPRING_SUPPORT,
            ANALYZE_EDIT_SPRING_SUPPORT,
            ANALYZE_FORCE,
            ANALYZE_EDIT_FORCE,
            ANALYZE_PRESSURE,
            ANALYZE_EDIT_PRESSURE,
            ANALYZE_GRAVITY,
            ANALYZE_EDIT_GRAVITY,
            ANALYZE_CENTRIFUGAL,
            ANALYZE_EDIT_CENTRIFUGAL,
        }
    ),
    "thermal": frozenset(
        {
            "analyze.thermal",
            "analyze.connection",
            "analyze.solver",
            ANALYZE_SOLID_MESH,
            ANALYZE_CATALOG_MATERIAL,
            ANALYZE_CUSTOM_MATERIAL,
            ANALYZE_SOLID_REGION_MATERIAL,
        }
    ),
    "fluid": frozenset(
        {
            "analyze.fluid",
            ANALYZE_INITIAL_VELOCITY,
            ANALYZE_INITIAL_PRESSURE,
            ANALYZE_FLUID_BOUNDARY,
            ANALYZE_EDIT_FLUID_BOUNDARY,
            ANALYZE_FLUID_MATERIAL,
            ANALYZE_OPENFOAM_SOLVER,
            ANALYZE_FLOW_MESH,
        }
    ),
    "electromagnetic": frozenset(
        {"analyze.electromagnetic", "analyze.solver", ANALYZE_SOLID_MESH}
    ),
}
_MESH_SETUP = frozenset(
    {
        ANALYZE_EDIT_GMSH_MESH,
        ANALYZE_GENERATE_GMSH,
        ANALYZE_LOCAL_MESH_SIZE,
        ANALYZE_EDIT_LOCAL_MESH_SIZE,
        "analyze.mesh_field",
        "analyze.mesh_refinement",
        "analyze.structured_mesh",
    }
)
_SOLVER_SETUP = frozenset({"analyze.solver_control"})
def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _source_dimensions(domain: Mapping[str, Any]) -> set[str] | None:
    """Return exact element dimensions represented by the source shapes."""

    if domain.get("geometry_sources_truncated") is True:
        return None
    source_count = _nonnegative_int(domain.get("geometry_source_count"))
    sources = domain.get("geometry_sources")
    if source_count is None or not isinstance(sources, list):
        return None
    if len(sources) != source_count:
        return None
    dimensions: set[str] = set()
    for source in sources:
        if not isinstance(source, Mapping):
            return None
        topology = source.get("topology")
        if not isinstance(topology, Mapping):
            return None
        solids = _nonnegative_int(topology.get("solids"))
        faces = _nonnegative_int(topology.get("faces"))
        edges = _nonnegative_int(topology.get("edges"))
        if solids is None or faces is None or edges is None:
            return None
        if solids:
            dimensions.add("solid")
        elif faces:
            dimensions.add("shell")
        elif edges:
            dimensions.add("beam")
    return dimensions


def _solid_material_coverage_complete(
    domain: Mapping[str, Any],
    analysis_count: int,
) -> bool:
    """Return true only when every exact solid in one study has material."""

    if (
        analysis_count != 1
        or domain.get("geometry_sources_truncated") is True
        or domain.get("materials_truncated") is True
    ):
        return False
    source_count = _nonnegative_int(domain.get("geometry_source_count"))
    material_count = _nonnegative_int(domain.get("material_count"))
    sources = domain.get("geometry_sources")
    materials = domain.get("materials")
    if (
        source_count is None
        or material_count is None
        or not isinstance(sources, list)
        or not isinstance(materials, list)
        or len(sources) != source_count
        or len(materials) != material_count
    ):
        return False
    expected: set[tuple[str, str]] = set()
    for source in sources:
        if not isinstance(source, Mapping):
            return False
        source_name = str(source.get("source_name") or "")
        topology = source.get("topology")
        solid_count = (
            _nonnegative_int(topology.get("solids"))
            if isinstance(topology, Mapping)
            else None
        )
        if not source_name or solid_count is None:
            return False
        expected.update(
            (source_name, f"Solid{index}")
            for index in range(1, solid_count + 1)
        )
    if not expected:
        return False
    covered: set[tuple[str, str]] = set()
    for material in materials:
        if not isinstance(material, Mapping):
            return False
        if material.get("material_kind") != "solid":
            continue
        references = material.get("references")
        if not isinstance(references, list):
            return False
        for reference in references:
            if not isinstance(reference, Mapping):
                return False
            object_name = str(reference.get("object_name") or "")
            subelements = reference.get("subelements")
            if not object_name or not isinstance(subelements, list):
                return False
            for raw_name in subelements:
                name = str(raw_name)
                if "." in name:
                    prefix, name = name.split(".", 1)
                    if prefix != object_name:
                        continue
                covered.add((object_name, name))
    return expected <= covered


def _has_multi_solid_source(domain: Mapping[str, Any]) -> bool | None:
    source_count = _nonnegative_int(domain.get("geometry_source_count"))
    sources = domain.get("geometry_sources")
    if source_count is None or not isinstance(sources, list):
        return None
    for source in sources:
        topology = source.get("topology") if isinstance(source, Mapping) else None
        solid_count = (
            _nonnegative_int(topology.get("solids"))
            if isinstance(topology, Mapping)
            else None
        )
        if solid_count is None:
            return None
        if solid_count > 1:
            return True
    if domain.get("geometry_sources_truncated") is True or len(sources) != source_count:
        return None
    return False


def _published_scope(
    domain: Mapping[str, Any],
    analysis_count: int,
) -> tuple[set[str], int, int, int, int] | None:
    value = domain.get("provider_scope")
    if not isinstance(value, Mapping):
        return None
    scope_count = _nonnegative_int(value.get("analysis_count"))
    undeclared = _nonnegative_int(value.get("undeclared_analysis_count"))
    physics = value.get("physics")
    counts = tuple(
        _nonnegative_int(value.get(name))
        for name in (
            "mesh_definition_count",
            "generated_mesh_count",
            "solver_count",
            "result_count",
        )
    )
    if (
        scope_count != analysis_count
        or undeclared is None
        or undeclared > analysis_count
        or not isinstance(physics, list)
        or len(physics) != len(set(physics))
        or any(name not in _PHYSICS for name in physics)
        or any(count is None for count in counts)
    ):
        return None
    return set(physics), *(int(count) for count in counts)


def _engineering_readiness(
    domain: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...] | None:
    analysis_count = _nonnegative_int(domain.get("analysis_count"))
    workflows = domain.get("analysis_workflows")
    if (
        analysis_count is None
        or domain.get("analysis_workflows_truncated") is True
        or not isinstance(workflows, list)
        or len(workflows) != analysis_count
    ):
        return None
    readiness = []
    for workflow in workflows:
        value = (
            workflow.get("engineering_readiness")
            if isinstance(workflow, Mapping)
            else None
        )
        blockers = value.get("blockers") if isinstance(value, Mapping) else None
        if (
            not isinstance(value, Mapping)
            or type(value.get("ready_to_solve")) is not bool
            or not isinstance(blockers, list)
            or any(not isinstance(blocker, str) for blocker in blockers)
        ):
            return None
        readiness.append(value)
    return tuple(readiness)


def _solver_creation_ready(domain: Mapping[str, Any]) -> bool | None:
    readiness = _engineering_readiness(domain)
    if readiness is None:
        return None
    return any(
        "missing_solver" in value["blockers"]
        and set(value["blockers"]) == {"missing_solver"}
        for value in readiness
    )


def _solver_run_ready(domain: Mapping[str, Any]) -> bool | None:
    readiness = _engineering_readiness(domain)
    if readiness is None:
        return None
    return any(value["ready_to_solve"] is True for value in readiness)


def _solid_material_required(
    domain: Mapping[str, Any],
    physics: set[str],
) -> bool | None:
    readiness = _engineering_readiness(domain)
    if readiness is None:
        return None
    blockers = set()
    if "mechanical" in physics:
        blockers.add("missing_mechanical_material")
    if "thermal" in physics:
        blockers.add("missing_thermal_material")
    return any(blockers.intersection(value["blockers"]) for value in readiness)


def _fully_conformal_shared_domain(domain: Mapping[str, Any]) -> bool:
    if (
        domain.get("geometry_sources_truncated") is True
        or _nonnegative_int(domain.get("geometry_source_count")) != 1
        or _nonnegative_int(domain.get("connection_count")) != 0
    ):
        return False
    sources = domain.get("geometry_sources")
    return bool(
        isinstance(sources, list)
        and len(sources) == 1
        and isinstance(sources[0], Mapping)
        and sources[0].get("interface_mode") == "shared"
        and sources[0].get("all_solids_conformal") is True
    )


def analyze_provider_tool_names(
    domain: Mapping[str, Any],
    available_tool_names: Sequence[str],
) -> tuple[str, ...]:
    """Return the exact provider subset for one Analyze snapshot."""

    allowed = set(_SHARED | _SETUP)
    if not isinstance(domain, Mapping) or domain.get("kind") != "analyze":
        return tuple(name for name in available_tool_names if name in allowed)

    geometry_source_count = (
        _nonnegative_int(domain.get("geometry_source_count")) or 0
    )
    if geometry_source_count > 0:
        allowed.add("analyze.faces")
    if geometry_source_count > 1:
        allowed.add(ANALYZE_SOLID_DOMAIN)

    analysis_count = _nonnegative_int(domain.get("analysis_count"))
    if analysis_count is None:
        return tuple(name for name in available_tool_names if name in allowed)

    published = _published_scope(domain, analysis_count)
    if published is not None:
        physics, mesh_count, generated_mesh_count, solver_count, result_count = (
            published
        )
    else:
        workflow_count = _nonnegative_int(domain.get("analysis_workflow_count"))
        workflows = domain.get("analysis_workflows")
        if (
            workflow_count != analysis_count
            or not isinstance(workflows, list)
            or len(workflows) != min(analysis_count, len(workflows))
        ):
            return tuple(name for name in available_tool_names if name in allowed)
        physics = set()
        mesh_count = 0
        generated_mesh_count = 0
        solver_count = 0
        result_count = 0
        for workflow in workflows:
            if not isinstance(workflow, Mapping):
                return tuple(name for name in available_tool_names if name in allowed)
            study = workflow.get("study")
            inventory = workflow.get("study_inventory")
            if not isinstance(study, Mapping) or not isinstance(inventory, Mapping):
                return tuple(name for name in available_tool_names if name in allowed)
            if study.get("declared") is True:
                values = study.get("physics")
                if not isinstance(values, list) or any(
                    value not in _PHYSICS for value in values
                ):
                    return tuple(
                        name for name in available_tool_names if name in allowed
                    )
                physics.update(values)
            counts = tuple(
                _nonnegative_int(inventory.get(name))
                for name in (
                    "mesh_definition_count",
                    "generated_mesh_count",
                    "solver_count",
                    "result_count",
                )
            )
            if any(value is None for value in counts):
                return tuple(name for name in available_tool_names if name in allowed)
            mesh_count += int(counts[0])
            generated_mesh_count += int(counts[1])
            solver_count += int(counts[2])
            result_count += int(counts[3])

    if physics:
        allowed.update(_STUDY)
        for name in physics:
            allowed.update(_PHYSICS[name])
        if _fully_conformal_shared_domain(domain):
            allowed.discard("analyze.connection")
        if physics == {"fluid"}:
            allowed.discard("analyze.inspect")
            allowed.discard("analyze.mesh")
            fluid_kinds, fluid_constraints_truncated = _collection_kinds(
                domain,
                "fluid_constraints",
                "constraint_kind",
                "fluid_constraints_truncated",
            )
            if "initial_flow_velocity" in fluid_kinds:
                allowed.discard(ANALYZE_INITIAL_VELOCITY)
            if "initial_pressure" in fluid_kinds:
                allowed.discard(ANALYZE_INITIAL_PRESSURE)
            if (
                "fluid_boundary" not in fluid_kinds
                and not fluid_constraints_truncated
            ):
                allowed.discard(ANALYZE_EDIT_FLUID_BOUNDARY)
            solver_kinds, _solvers_truncated = _collection_kinds(
                domain,
                "solvers",
                "solver_kind",
                "solvers_truncated",
            )
            if "openfoam" in solver_kinds:
                allowed.discard(ANALYZE_OPENFOAM_SOLVER)
        load_kinds, loads_truncated = _collection_kinds(
            domain,
            "loads",
            "load_kind",
            "loads_truncated",
        )
        if not loads_truncated:
            for kind, edit_tool in (
                ("force", ANALYZE_EDIT_FORCE),
                ("pressure", ANALYZE_EDIT_PRESSURE),
                ("gravity", ANALYZE_EDIT_GRAVITY),
                ("centrifugal", ANALYZE_EDIT_CENTRIFUGAL),
            ):
                if kind not in load_kinds:
                    allowed.discard(edit_tool)
            if "gravity" in load_kinds:
                allowed.discard(ANALYZE_GRAVITY)
        support_kinds, supports_truncated = _collection_kinds(
            domain,
            "support_conditions",
            "condition_kind",
            "support_conditions_truncated",
        )
        if not supports_truncated:
            for kind, edit_tool in (
                ("fixed", ANALYZE_EDIT_FIXED_SUPPORT),
                ("rigid_body", ANALYZE_EDIT_RIGID_COUPLING),
                ("displacement", ANALYZE_EDIT_DISPLACEMENT_SUPPORT),
                ("spring", ANALYZE_EDIT_SPRING_SUPPORT),
            ):
                if kind not in support_kinds:
                    allowed.discard(edit_tool)
        material_required = _solid_material_required(domain, physics)
        if material_required is False or (
            material_required is None
            and _solid_material_coverage_complete(domain, analysis_count)
        ):
            allowed.discard(ANALYZE_SOLID_MATERIAL)
            allowed.discard(ANALYZE_SOLID_REGION_MATERIAL)
            allowed.discard(ANALYZE_CATALOG_MATERIAL)
            allowed.discard(ANALYZE_CUSTOM_MATERIAL)
        elif _has_multi_solid_source(domain) is False:
            allowed.discard(ANALYZE_SOLID_REGION_MATERIAL)
    assignment_count = sum(
        _nonnegative_int(domain.get(name)) or 0
        for name in _ASSIGNMENT_COUNT_NAMES
    )
    if (
        any(domain.get(name) is True for name in _ASSIGNMENT_TRUNCATION_NAMES)
        or generated_mesh_count > 0
        or (_nonnegative_int(domain.get("fem_mesh_output_count")) or 0) > 0
        or ("mechanical" in physics and result_count > 0)
    ):
        allowed.add("analyze.inspect")
    if assignment_count:
        allowed.add("analyze.assignment_view")
    if mesh_count:
        allowed.discard(ANALYZE_SOLID_MESH)
        allowed.discard(ANALYZE_FLOW_MESH)
        allowed.update(_MESH_SETUP)
        refinement_kinds, refinements_truncated = _collection_kinds(
            domain,
            "mesh_refinements",
            "refinement_mode",
            "mesh_refinements_truncated",
        )
        if "region" not in refinement_kinds and not refinements_truncated:
            allowed.discard(ANALYZE_EDIT_LOCAL_MESH_SIZE)
        mesh_kinds, mesh_kinds_truncated = _collection_kinds(
            domain,
            "mesh_definitions",
            "mesher",
            "mesh_definitions_truncated",
        )
        if "gmsh" not in mesh_kinds and not mesh_kinds_truncated:
            allowed.discard(ANALYZE_EDIT_GMSH_MESH)
    if generated_mesh_count:
        allowed.add("analyze.mesh_output")
    if solver_count:
        allowed.update(_SOLVER_SETUP)
        solver_kinds, solvers_truncated = _collection_kinds(
            domain,
            "solvers",
            "solver_kind",
            "solvers_truncated",
        )
        if "elmer" in solver_kinds or solvers_truncated:
            allowed.add("analyze.equation")
    if (
        solver_count
        and generated_mesh_count
        and _solver_setup_complete(domain, physics)
        and _solver_run_ready(domain) is not False
    ):
        allowed.add(ANALYZE_RUN_SOLVER)
    if result_count:
        if "fluid" in physics:
            allowed.update(
                {
                    ANALYZE_FLOW_RESULT,
                    ANALYZE_SHOW_FLOW,
                    ANALYZE_FLOW_PERFORMANCE,
                }
            )
            if result_count >= 2:
                allowed.add(ANALYZE_COMPARE_FLOW)
        if "mechanical" in physics:
            allowed.update({ANALYZE_MECHANICAL_RESULTS, ANALYZE_SHOW_MECHANICAL})
        if "thermal" in physics:
            allowed.update({ANALYZE_TEMPERATURE_RESULTS, ANALYZE_SHOW_TEMPERATURE})
    run_status = domain.get("run_status")
    job_statuses: list[Mapping[str, Any]] = []
    if isinstance(run_status, Mapping):
        background_jobs = run_status.get("background_jobs")
        if isinstance(background_jobs, list):
            job_statuses = [
                job for job in background_jobs if isinstance(job, Mapping)
            ]
        elif str(run_status.get("job_id") or ""):
            job_statuses = [run_status]
    observable_jobs = [
        job
        for job in job_statuses
        if str(job.get("phase") or "idle") != "idle"
        and str(job.get("job_id") or "")
    ]
    if observable_jobs:
        allowed.add("native.job")
    active_jobs = [job for job in observable_jobs if job.get("terminal") is False]
    if active_jobs:
        allowed.discard(ANALYZE_GENERATE_GMSH)
        if any(
            not str(job.get("resource_scope") or "").startswith("analyze:")
            for job in active_jobs
        ):
            allowed.discard(ANALYZE_RUN_SOLVER)

    return tuple(name for name in available_tool_names if name in allowed)


def _collection_kinds(
    domain: Mapping[str, Any],
    collection_name: str,
    kind_name: str,
    truncated_name: str,
) -> tuple[set[str], bool]:
    values = domain.get(collection_name)
    kinds = (
        {
            str(value.get(kind_name) or "")
            for value in values
            if isinstance(value, Mapping)
        }
        if isinstance(values, list)
        else set()
    )
    kinds.discard("")
    return kinds, domain.get(truncated_name) is True


def _solver_setup_complete(domain: Mapping[str, Any], physics: set[str]) -> bool:
    solvers, solvers_truncated = _collection_kinds(
        domain,
        "solvers",
        "solver_kind",
        "solvers_truncated",
    )
    if solvers_truncated or len(solvers) != 1:
        return False
    if "elmer" not in solvers:
        return True
    equations, equations_truncated = _collection_kinds(
        domain,
        "equations",
        "equation_kind",
        "equations_truncated",
    )
    if equations_truncated:
        return False
    if "mechanical" in physics and not equations.intersection(
        {"elasticity", "deformation"}
    ):
        return False
    if "thermal" in physics and "heat" not in equations:
        return False
    if "electromagnetic" in physics and not equations.intersection(
        {
            "electrostatic",
            "electric_force",
            "magnetodynamic",
            "magnetodynamic_2d",
            "static_current",
        }
    ):
        return False
    return True


def _keep_exact_updates(
    operations: Sequence[str],
    kinds: set[str],
    truncated: bool,
) -> tuple[str, ...]:
    return tuple(
        operation
        for operation in operations
        if not operation.startswith("update_")
        or truncated
        or operation.removeprefix("update_") in kinds
    )


def _physics_state(
    domain: Mapping[str, Any],
) -> tuple[set[str], int] | None:
    analysis_count = _nonnegative_int(domain.get("analysis_count"))
    if analysis_count is None:
        return None
    published = _published_scope(domain, analysis_count)
    if published is None:
        return None
    return set(published[0]), analysis_count


def _model_operations(
    domain: Mapping[str, Any],
    available: Sequence[str],
) -> tuple[str, ...]:
    state = _physics_state(domain)
    wanted = {"create_analysis"}
    if state is not None and state[1] > 0:
        physics = state[0]
        wanted.add("update_study")
        if "mechanical" in physics:
            wanted.add("create_reinforced_material")
        material_count = _nonnegative_int(domain.get("material_count")) or 0
        if material_count and physics != {"fluid"}:
            wanted.add("update_material")
        material_kinds, materials_truncated = _collection_kinds(
            domain,
            "materials",
            "material_kind",
            "materials_truncated",
        )
        if "mechanical" in physics and (
            materials_truncated or material_kinds.intersection({"solid", "reinforced"})
        ):
            wanted.add("create_nonlinear_material")
    return tuple(operation for operation in available if operation in wanted)


def _solver_operations(
    domain: Mapping[str, Any],
    available: Sequence[str],
) -> tuple[str, ...]:
    state = _physics_state(domain)
    if state is None or state[1] == 0:
        return ()
    if _solver_creation_ready(domain) is False:
        return ()
    physics = state[0]
    wanted = set()
    if physics.intersection({"mechanical", "thermal"}):
        wanted.update({"create_calculix", "create_elmer"})
    if "mechanical" in physics:
        wanted.update({"create_mystran", "create_z88"})
    if physics.intersection({"fluid", "electromagnetic"}):
        wanted.add("create_elmer")
    return tuple(operation for operation in available if operation in wanted)


def _geometry_operations(
    domain: Mapping[str, Any],
    available: Sequence[str],
) -> tuple[str, ...]:
    state = _physics_state(domain)
    if state is None or "mechanical" not in state[0]:
        return ()
    dimensions = _source_dimensions(domain)
    wanted = set()
    if dimensions is None or "beam" in dimensions:
        wanted.update({"create_beam_section", "create_beam_rotation"})
    if dimensions is None or "shell" in dimensions:
        wanted.add("create_shell_thickness")
    kinds, truncated = _collection_kinds(
        domain,
        "element_definitions",
        "element_definition_kind",
        "element_definitions_truncated",
    )
    if truncated:
        wanted.update(
            {
                "update_beam_section",
                "update_beam_rotation",
                "update_shell_thickness",
            }
        )
    else:
        wanted.update(
            f"update_{kind}"
            for kind in kinds
            if kind in {"beam_section", "beam_rotation", "shell_thickness"}
        )
    return tuple(operation for operation in available if operation in wanted)


def _inspect_operations(
    domain: Mapping[str, Any],
    available: Sequence[str],
) -> tuple[str, ...]:
    state = _physics_state(domain)
    wanted = set()
    if state is not None and state[1] > 0:
        if any(domain.get(name) is True for name in _ASSIGNMENT_TRUNCATION_NAMES):
            wanted.update({"assignments", "validate_assignments"})
        generated_mesh_count = _published_scope(domain, state[1])
        if (
            generated_mesh_count is not None
            and generated_mesh_count[2] > 0
        ) or (_nonnegative_int(domain.get("fem_mesh_output_count")) or 0) > 0:
            wanted.add("fem_mesh_elements")
        if "mechanical" in state[0] and (
            _nonnegative_int(domain.get("result_count")) or 0
        ) > 0:
            wanted.add("linearized_stress")
    return tuple(operation for operation in available if operation in wanted)


def _operation_scope(
    domain: Mapping[str, Any],
    authorized_operations: Mapping[str, Sequence[str]],
    tool_names: Sequence[str],
    *,
    has_selection: bool,
) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for name in tool_names:
        available = tuple(authorized_operations.get(name, ()))
        if not available:
            continue
        if name == "view.control":
            result[name] = tuple(
                operation
                for operation in available
                if operation != "capture_selection" or has_selection
            )
            continue
        if name == "analyze.model":
            result[name] = _model_operations(domain, available)
            continue
        if name == ANALYZE_FLUID_MATERIAL:
            operation = (
                "update"
                if (_nonnegative_int(domain.get("material_count")) or 0) > 0
                else "create"
            )
            result[name] = tuple(
                value for value in available if value == operation
            )
            continue
        if name == ANALYZE_GMSH_MESH:
            kinds, _truncated = _collection_kinds(
                domain,
                "mesh_definitions",
                "mesher",
                "mesh_definitions_truncated",
            )
            result[name] = tuple(
                value for value in available if value == "create" and "gmsh" not in kinds
            )
            continue
        if name == "analyze.inspect":
            result[name] = _inspect_operations(domain, available)
            continue
        if name == "analyze.solver":
            result[name] = _solver_operations(domain, available)
            continue
        if name == "analyze.geometry":
            result[name] = _geometry_operations(domain, available)
            continue
        if name == "analyze.fluid":
            kinds, truncated = _collection_kinds(
                domain,
                "fluid_constraints",
                "constraint_kind",
                "fluid_constraints_truncated",
            )
            result[name] = tuple(
                operation
                for operation in available
                if operation.startswith("update_")
                and operation != "update_fluid_boundary"
                and (
                    truncated
                    or operation.removeprefix("update_") in kinds
                )
            )
            continue
        source = _LIVE_KIND_SOURCES.get(name)
        if source is not None:
            kinds, truncated = _collection_kinds(domain, *source)
            scoped = _keep_exact_updates(available, kinds, truncated)
            if name == "analyze.support":
                scoped = tuple(
                    operation for operation in scoped if operation != "create_fixed"
                )
            elif name == "analyze.load":
                scoped = tuple(
                    operation
                    for operation in scoped
                    if operation not in {"create_force", "update_force"}
                )
            elif name == "analyze.mesh_refinement":
                scoped = tuple(
                    operation
                    for operation in scoped
                    if operation not in {"create_region", "update_region"}
                )
            result[name] = scoped
            continue
        if name == "analyze.mesh":
            kinds, truncated = _collection_kinds(
                domain,
                "mesh_definitions",
                "mesher",
                "mesh_definitions_truncated",
            )
            result[name] = tuple(
                operation
                for operation in available
                if operation.startswith("update_")
                and operation != "update_gmsh"
                and (truncated or operation.rsplit("_", 1)[-1] in kinds)
            )
            continue
        if name == "analyze.mesh_field":
            truncated = domain.get("mesh_refinements_truncated") is True
            kinds = {
                str(value.get("definition", {}).get("kind") or "")
                for value in list(domain.get("mesh_refinements") or ())
                if isinstance(value, Mapping)
                and value.get("refinement_mode") in {"manipulate", "advanced"}
                and isinstance(value.get("definition"), Mapping)
            }
            kinds.discard("")
            result[name] = _keep_exact_updates(
                available,
                kinds,
                truncated,
            )
            continue
        if name == "analyze.solver_control":
            kinds, truncated = _collection_kinds(
                domain,
                "solvers",
                "solver_kind",
                "solvers_truncated",
            )
            result[name] = _keep_exact_updates(available, kinds, truncated)
    return result


def scope_analyze_provider_surface(
    surface: NativeProviderSurface,
    active_state: Mapping[str, Any],
    *,
    registry: NativeCapabilityRegistry | None = None,
) -> NativeProviderSurface:
    """Project a validated Analyze surface from its exact active snapshot."""

    if not isinstance(surface, NativeProviderSurface):
        raise TypeError("surface must be a NativeProviderSurface")
    if not surface.available or surface.snapshot.surface_id != "analyze":
        return surface
    domain = active_state.get("domain") if isinstance(active_state, Mapping) else None
    names = analyze_provider_tool_names(
        domain if isinstance(domain, Mapping) else {},
        surface.tool_names,
    )
    projected = project_native_provider_surface(surface, names)
    if registry is None:
        return projected
    selection = active_state.get("selection")
    has_selection = bool(
        isinstance(selection, Mapping)
        and isinstance(selection.get("items"), list)
        and selection["items"]
    )
    return project_native_provider_operations(
        projected,
        registry,
        _operation_scope(
            domain if isinstance(domain, Mapping) else {},
            {
                name: _provider_schema_operations(schema)
                for name, schema in zip(
                    projected.tool_names,
                    projected.schemas,
                    strict=True,
                )
            },
            projected.tool_names,
            has_selection=has_selection,
        ),
    )
