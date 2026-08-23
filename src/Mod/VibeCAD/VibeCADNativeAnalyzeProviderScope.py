# SPDX-License-Identifier: LGPL-2.1-or-later

"""Select Analyze provider families from persistent study state."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityRegistry,
    NativeProviderSurface,
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
_SETUP = frozenset({"analyze.model", "analyze.inspect"})
_STUDY = frozenset(
    {
        "analyze.geometry",
        "analyze.mesh",
        "analyze.solver",
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
            "analyze.geometrical",
            "analyze.support",
            "analyze.connection",
            "analyze.load",
        }
    ),
    "thermal": frozenset({"analyze.thermal", "analyze.connection"}),
    "fluid": frozenset({"analyze.fluid"}),
    "electromagnetic": frozenset({"analyze.electromagnetic"}),
}
_MESH_SETUP = frozenset(
    {
        "analyze.mesh_field",
        "analyze.mesh_refinement",
        "analyze.structured_mesh",
    }
)
_SOLVER_SETUP = frozenset({"analyze.solver_control", "analyze.equation"})
_RESULTS = frozenset(
    {
        "analyze.results",
        "analyze.presentation",
        "analyze.post",
        "analyze.post_function",
        "analyze.visualization",
    }
)


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


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


def analyze_provider_tool_names(
    domain: Mapping[str, Any],
    available_tool_names: Sequence[str],
) -> tuple[str, ...]:
    """Return the exact provider subset for one Analyze snapshot."""

    allowed = set(_SHARED | _SETUP)
    if not isinstance(domain, Mapping) or domain.get("kind") != "analyze":
        return tuple(name for name in available_tool_names if name in allowed)

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
    assignment_count = sum(
        _nonnegative_int(domain.get(name)) or 0
        for name in _ASSIGNMENT_COUNT_NAMES
    )
    if assignment_count:
        allowed.add("analyze.assignment_view")
    if mesh_count:
        allowed.update(_MESH_SETUP)
    if generated_mesh_count:
        allowed.add("analyze.mesh_output")
    if solver_count:
        allowed.update(_SOLVER_SETUP)
    if solver_count and generated_mesh_count:
        allowed.add("analyze.solver_execution")
    if result_count:
        allowed.update(_RESULTS)

    return tuple(name for name in available_tool_names if name in allowed)


def _definition_operations(
    registry: NativeCapabilityRegistry,
    name: str,
) -> tuple[str, ...]:
    definition = registry.definition(name)
    if definition is None:
        return ()
    return tuple(variant.operation for variant in definition.variants)


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
        if physics.intersection({"mechanical", "thermal", "electromagnetic"}):
            wanted.add("create_solid_material")
        if "mechanical" in physics:
            wanted.add("create_reinforced_material")
        if "fluid" in physics:
            wanted.add("create_fluid_material")

        material_count = _nonnegative_int(domain.get("material_count")) or 0
        if material_count:
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


_INSPECT_COUNT_BY_OPERATION = {
    "material": "material_count",
    "element_definition": "element_definition_count",
    "electromagnetic_constraint": "electromagnetic_constraint_count",
    "fluid_constraint": "fluid_constraint_count",
    "geometrical_feature": "geometrical_feature_count",
    "support_condition": "support_condition_count",
    "connection": "connection_count",
    "load": "load_count",
    "thermal_condition": "thermal_condition_count",
    "fem_mesh_definition": "mesh_definition_count",
    "mesh_refinement": "mesh_refinement_count",
    "solver": "solver_count",
    "equation": "equation_count",
    "result": "result_count",
}


def _inspect_operations(
    domain: Mapping[str, Any],
    available: Sequence[str],
) -> tuple[str, ...]:
    state = _physics_state(domain)
    wanted = {"material_catalog"}
    if state is not None and state[1] > 0:
        wanted.update({"study", "analysis"})
        if any(
            (_nonnegative_int(domain.get(name)) or 0)
            for name in _ASSIGNMENT_COUNT_NAMES
        ):
            wanted.update({"assignments", "validate_assignments"})
        for operation, count_name in _INSPECT_COUNT_BY_OPERATION.items():
            if (_nonnegative_int(domain.get(count_name)) or 0) > 0:
                wanted.add(operation)
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
    registry: NativeCapabilityRegistry,
    tool_names: Sequence[str],
) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for name in tool_names:
        available = _definition_operations(registry, name)
        if not available:
            continue
        if name == "analyze.model":
            result[name] = _model_operations(domain, available)
            continue
        if name == "analyze.inspect":
            result[name] = _inspect_operations(domain, available)
            continue
        source = _LIVE_KIND_SOURCES.get(name)
        if source is not None:
            kinds, truncated = _collection_kinds(domain, *source)
            result[name] = _keep_exact_updates(available, kinds, truncated)
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
                if operation.startswith("create_")
                or truncated
                or operation.rsplit("_", 1)[-1] in kinds
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
    return project_native_provider_operations(
        projected,
        registry,
        _operation_scope(
            domain if isinstance(domain, Mapping) else {},
            registry,
            projected.tool_names,
        ),
    )
