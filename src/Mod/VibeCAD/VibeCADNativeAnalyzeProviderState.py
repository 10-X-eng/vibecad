# SPDX-License-Identifier: LGPL-2.1-or-later

"""Decision-focused provider view of the durable Analyze snapshot."""

from __future__ import annotations

from typing import Any, Mapping


_COLLECTIONS = (
    ("materials", "material_count", "materials_truncated"),
    ("geometry_sources", "geometry_source_count", "geometry_sources_truncated"),
    (
        "element_definitions",
        "element_definition_count",
        "element_definitions_truncated",
    ),
    (
        "electromagnetic_constraints",
        "electromagnetic_constraint_count",
        "electromagnetic_constraints_truncated",
    ),
    ("fluid_constraints", "fluid_constraint_count", "fluid_constraints_truncated"),
    (
        "geometrical_features",
        "geometrical_feature_count",
        "geometrical_features_truncated",
    ),
    ("support_conditions", "support_condition_count", "support_conditions_truncated"),
    ("connections", "connection_count", "connections_truncated"),
    ("loads", "load_count", "loads_truncated"),
    ("thermal_conditions", "thermal_condition_count", "thermal_conditions_truncated"),
    ("mesh_definitions", "mesh_definition_count", "mesh_definitions_truncated"),
    ("mesh_refinements", "mesh_refinement_count", "mesh_refinements_truncated"),
    ("fem_mesh_outputs", "fem_mesh_output_count", "fem_mesh_outputs_truncated"),
    ("mesh_filters", "mesh_filter_count", "mesh_filters_truncated"),
    ("solvers", "solver_count", "solvers_truncated"),
    ("equations", "equation_count", "equations_truncated"),
    ("results", "result_count", "results_truncated"),
)


def _nonempty_values(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    result = {}
    for name, item in value.items():
        if item in (None, False, 0, "", [], {}):
            continue
        result[str(name)] = item
    return result


def _analysis_by_name(domain: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    result = {}
    for value in list(domain.get("analyses") or ()):
        if not isinstance(value, Mapping):
            continue
        name = str(value.get("object_name") or "")
        if name:
            result[name] = value
    return result


def _compact_study(
    workflow: Mapping[str, Any],
    analysis_state: Mapping[str, Any] | None,
) -> dict[str, Any]:
    workflow_analysis = workflow.get("analysis")
    if not isinstance(workflow_analysis, Mapping):
        workflow_analysis = {}
    source = analysis_state or workflow_analysis
    graph = workflow.get("graph")
    graph = graph if isinstance(graph, Mapping) else {}
    analysis = {
        name: source[name]
        for name in ("object_name", "label", "active", "state_sha256")
        if name in source
    }
    analysis["member_count"] = int(
        source.get("member_count", graph.get("member_count", 0)) or 0
    )
    result: dict[str, Any] = {
        "analysis": analysis,
        "intent": dict(workflow.get("study") or {}),
        "readiness": dict(workflow.get("engineering_readiness") or {}),
    }
    inventory = _nonempty_values(workflow.get("study_inventory"))
    if inventory:
        result["inventory"] = inventory
    runtimes = list(workflow.get("solver_runtimes") or ())
    if runtimes:
        result["solver_runtimes"] = runtimes
    for source_name, count_name in (
        ("meshes", "mesh_count"),
        ("solvers", "solver_count"),
        ("results", "result_count"),
    ):
        values = list(workflow.get(source_name) or ())
        if values:
            result[source_name] = values
        if workflow.get(source_name + "_truncated") is True:
            result[count_name] = int(workflow.get(count_name, len(values)) or 0)
            result[source_name + "_truncated"] = True
    result_graph = source.get("result_graph")
    if isinstance(result_graph, Mapping) and int(
        result_graph.get("object_count", 0) or 0
    ):
        result["result_graph"] = dict(result_graph)
    return result


def _object_names(values: list[Any]) -> set[str]:
    return {
        str(value.get("object_name") or "")
        for value in values
        if isinstance(value, Mapping) and value.get("object_name")
    }


def compact_analyze_provider_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """Remove duplicate and empty Analyze facts before provider serialization."""

    if not isinstance(state, Mapping) or state.get("surface_id") != "analyze":
        raise TypeError("state must be one Analyze Native snapshot")
    domain = state.get("domain")
    if not isinstance(domain, Mapping) or domain.get("kind") != "analyze":
        raise TypeError("state must contain one Analyze domain")
    analyses = _analysis_by_name(domain)
    workflows = [
        value
        for value in list(domain.get("analysis_workflows") or ())
        if isinstance(value, Mapping)
    ]
    studies = []
    for workflow in workflows:
        workflow_analysis = workflow.get("analysis")
        name = (
            str(workflow_analysis.get("object_name") or "")
            if isinstance(workflow_analysis, Mapping)
            else ""
        )
        studies.append(_compact_study(workflow, analyses.get(name)))

    compact_domain: dict[str, Any] = {
        "kind": "analyze",
        "study_count": int(domain.get("analysis_count", len(studies)) or 0),
    }
    if studies:
        compact_domain["studies"] = studies

    represented_names = {
        str(study["analysis"].get("object_name") or "") for study in studies
    }
    for values_name, count_name, truncated_name in _COLLECTIONS:
        values = list(domain.get(values_name) or ())
        if not values:
            continue
        compact_domain[values_name] = values
        represented_names.update(_object_names(values))
        if domain.get(truncated_name) is True:
            compact_domain[count_name] = int(domain.get(count_name, len(values)) or 0)
            compact_domain[truncated_name] = True

    run_status = domain.get("run_status")
    if isinstance(run_status, Mapping) and (
        str(run_status.get("phase") or "") != "idle"
        or int(run_status.get("solver_result_count", 0) or 0) > 0
    ):
        compact_domain["run_status"] = dict(run_status)
    clipping = domain.get("clipping")
    if isinstance(clipping, Mapping) and int(clipping.get("plane_count", 0) or 0):
        compact_domain["clipping"] = dict(clipping)

    result = {
        "surface_id": "analyze",
        "document": dict(state.get("document") or {}),
        "structural_revision": int(state.get("structural_revision", 0) or 0),
        "domain": compact_domain,
    }
    selection = state.get("selection")
    if isinstance(selection, Mapping) and selection.get("items"):
        result["selection"] = dict(selection)
    working_set = [
        value
        for value in list(state.get("working_set") or ())
        if isinstance(value, Mapping)
        and str(value.get("object_name") or "") not in represented_names
    ]
    if working_set:
        result["working_set"] = working_set
    return result
