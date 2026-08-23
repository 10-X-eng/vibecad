# SPDX-License-Identifier: LGPL-2.1-or-later

"""Select Analyze provider families from persistent study state."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from VibeCADNativeCapabilityRegistry import (
    NativeProviderSurface,
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
        "analyze.assignment_view",
        "analyze.geometry",
        "analyze.mesh",
        "analyze.solver",
    }
)
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


def scope_analyze_provider_surface(
    surface: NativeProviderSurface,
    active_state: Mapping[str, Any],
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
    return project_native_provider_surface(surface, names)
