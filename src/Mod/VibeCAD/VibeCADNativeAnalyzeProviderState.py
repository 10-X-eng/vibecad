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


def _analysis_target(source: Mapping[str, Any], member_count: int) -> dict[str, Any]:
    return {
        "object_name": str(source.get("object_name") or ""),
        "expected_state_sha256": str(source.get("state_sha256") or ""),
        "expected_member_count": member_count,
    }


def _compact_geometry_source(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    result = {
        str(name): item
        for name, item in value.items()
        if name not in {"object_name", "state_sha256", "clipping_face_target"}
    }
    result["source_name"] = str(value.get("object_name") or "")
    result["source_target"] = {
        "object_name": str(value.get("object_name") or ""),
        "expected_state_sha256": str(value.get("state_sha256") or ""),
    }
    return result


def _compact_resource(value: Any, *, name_key: str = "") -> Any:
    if not isinstance(value, Mapping):
        return value
    object_name = str(value.get("object_name") or "")
    state_sha256 = str(value.get("state_sha256") or "")
    if not object_name or not state_sha256:
        return value
    result = {
        str(name): item
        for name, item in value.items()
        if name not in {"object_name", "state_sha256"}
    }
    result["target"] = {
        "object_name": object_name,
        "expected_state_sha256": state_sha256,
    }
    if name_key:
        result[name_key] = object_name
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
    member_count = int(
        source.get("member_count", graph.get("member_count", 0)) or 0
    )
    intent = dict(workflow.get("study") or {})
    intent.pop("state_sha256", None)
    result: dict[str, Any] = {
        "analysis_name": str(source.get("object_name") or ""),
        "analysis_target": _analysis_target(source, member_count),
        "intent": intent,
        "readiness": dict(workflow.get("engineering_readiness") or {}),
    }
    dependencies = workflow.get("dependencies")
    if isinstance(dependencies, Mapping):
        result["dependencies"] = dict(dependencies)
    for name in ("label", "active", "focused"):
        if name in source:
            result[name] = source[name]
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

    represented_names = {str(study.get("analysis_name") or "") for study in studies}
    for values_name, count_name, truncated_name in _COLLECTIONS:
        raw_values = list(domain.get(values_name) or ())
        if not raw_values:
            continue
        values = (
            [_compact_geometry_source(value) for value in raw_values]
            if values_name == "geometry_sources"
            else [
                _compact_resource(
                    value,
                    name_key={
                        "materials": "material_name",
                        "support_conditions": "support_name",
                        "loads": "load_name",
                        "mesh_definitions": "mesh_name",
                        "solvers": "solver_name",
                        "results": "result_name",
                    }.get(values_name, ""),
                )
                for value in raw_values
            ]
        )
        compact_domain[values_name] = values
        represented_names.update(_object_names(raw_values))
        if domain.get(truncated_name) is True:
            compact_domain[count_name] = int(domain.get(count_name, len(values)) or 0)
            compact_domain[truncated_name] = True

    run_status = domain.get("run_status")
    if isinstance(run_status, Mapping) and (
        str(run_status.get("phase") or "") != "idle"
        or int(run_status.get("solver_result_count", 0) or 0) > 0
    ):
        compact_domain["run_status"] = dict(run_status)
    background_jobs = [
        dict(value)
        for value in list(domain.get("background_jobs") or ())
        if isinstance(value, Mapping)
    ]
    if background_jobs:
        compact_domain["background_jobs"] = background_jobs
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
