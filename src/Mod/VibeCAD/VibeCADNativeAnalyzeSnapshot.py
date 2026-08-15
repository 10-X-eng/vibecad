# SPDX-License-Identifier: LGPL-2.1-or-later

"""Concise live state for the Analyze ribbon."""

from __future__ import annotations

from typing import Any

from VibeCADNativeAnalyzeState import analysis_state, material_state
from VibeCADNativeAnalyzeElementState import element_definition_state
from VibeCADNativeAnalyzeConstraintState import electromagnetic_constraint_state
from VibeCADNativeAnalyzeFluidState import fluid_constraint_state
from VibeCADNativeAnalyzeGeometricalState import geometrical_feature_state
from VibeCADNativeAnalyzeSupportState import support_condition_state
from VibeCADNativeAnalyzeConnectionState import connection_state
from VibeCADNativeAnalyzeLoadState import load_state
from VibeCADNativeAnalyzeThermalState import thermal_condition_state
from VibeCADNativeAnalyzeMeshState import fem_mesh_definition_state, fem_mesh_object_state
from VibeCADNativeAnalyzeMeshOutputState import mesh_filter_state
from VibeCADNativeAnalyzeMeshRefinementState import mesh_refinement_state
from VibeCADNativeAnalyzeSolverState import solver_state
from VibeCADNativeAnalyzeEquationState import equation_state
from VibeCADNativeAnalyzeResultState import result_reference_state
from VibeCADNativeAnalyzeResults import result_purge_state
from VibeCADNativeAnalyzeClipping import (
    clipping_face_source_state,
    clipping_state,
)
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeSnapshot import objects_of_type


MAX_ANALYSES = 16
MAX_MATERIALS = 32
MAX_GEOMETRY_SOURCES = 32
MAX_ELEMENT_DEFINITIONS = 32
MAX_ELECTROMAGNETIC_CONSTRAINTS = 32
MAX_FLUID_CONSTRAINTS = 32
MAX_GEOMETRICAL_FEATURES = 32
MAX_SUPPORT_CONDITIONS = 32
MAX_CONNECTIONS = 32
MAX_LOADS = 32
MAX_THERMAL_CONDITIONS = 32
MAX_MESH_DEFINITIONS = 16
MAX_MESH_REFINEMENTS = 32
MAX_FEM_MESH_OUTPUTS = 16
MAX_MESH_FILTERS = 16
MAX_SOLVERS = 16
MAX_EQUATIONS = 32
MAX_RESULTS = 16
MAX_WORKFLOW_MESHES = 8
MAX_WORKFLOW_SOLVERS = 8
MAX_WORKFLOW_RESULTS = 8


def _compact_mesh(state: dict[str, Any]) -> dict[str, Any]:
    return {
        key: state[key]
        for key in (
            "object_name",
            "label",
            "mesher",
            "backend",
            "generated",
            "topology",
            "state_sha256",
        )
        if key in state
    }


def _compact_solver(state: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: state[key]
        for key in (
            "object_name",
            "label",
            "solver_kind",
            "implementation",
            "suppressed",
            "result_count",
            "state_sha256",
        )
        if key in state
    }
    result["run_status"] = (
        "suppressed"
        if bool(state.get("suppressed"))
        else "results_available"
        if int(state.get("result_count", 0) or 0) > 0
        else "not_run"
    )
    return result


def _compact_result(state: dict[str, Any]) -> dict[str, Any]:
    return {
        key: state[key]
        for key in (
            "object_name",
            "label",
            "result_kind",
            "data_available",
            "point_count",
            "cell_count",
            "field_count",
            "field_names",
            "field_names_truncated",
            "state_sha256",
        )
        if key in state
    }


def _analysis_workflows(
    analyses: list[Any],
    summarized: list[dict[str, Any]],
    mesh_states: dict[str, dict[str, Any]],
    solver_states: dict[str, dict[str, Any]],
    result_states: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    summary_by_name = {state["object_name"]: state for state in summarized}
    workflows = []
    for analysis in analyses[:MAX_ANALYSES]:
        name = str(analysis.Name)
        analysis_summary = summary_by_name[name]
        member_names = {
            str(member.Name) for member in tuple(getattr(analysis, "Group", ()) or ())
        }
        all_member_meshes = [
            state
            for state in mesh_states.values()
            if state.get("object_name") in member_names
        ]
        all_member_solvers = [
            state
            for state in solver_states.values()
            if state.get("analysis") == name
        ]
        all_member_results = [
            state
            for state in result_states.values()
            if name in tuple(state.get("analysis_owners", ()) or ())
            or state.get("object_name") in member_names
        ]
        member_meshes = [
            _compact_mesh(state)
            for state in all_member_meshes[:MAX_WORKFLOW_MESHES]
        ]
        member_solvers = [
            _compact_solver(state)
            for state in all_member_solvers[:MAX_WORKFLOW_SOLVERS]
        ]
        member_results = [
            _compact_result(state)
            for state in all_member_results[:MAX_WORKFLOW_RESULTS]
        ]
        generated_meshes = sum(
            bool(state.get("generated")) for state in all_member_meshes
        )
        runnable_solvers = sum(
            not bool(state.get("suppressed")) for state in all_member_solvers
        )
        blockers = []
        if not all_member_solvers:
            blockers.append("missing_solver")
        elif not runnable_solvers:
            blockers.append("all_solvers_suppressed")
        if not generated_meshes:
            blockers.append("missing_generated_mesh")
        result_graph = dict(analysis_summary["result_graph"])
        workflows.append(
            {
                "analysis": {
                    "object_name": name,
                    "label": analysis_summary.get("label", name),
                    "active": bool(analysis_summary.get("active")),
                    "state_sha256": analysis_summary["state_sha256"],
                },
                "graph": {
                    "member_count": analysis_summary["member_count"],
                    "member_counts": analysis_summary["member_counts"],
                    "result_object_count": result_graph["object_count"],
                    "result_graph_sha256": result_graph["graph_sha256"],
                },
                "readiness": {
                    "scope": "analysis_graph",
                    "ready": not blockers,
                    "generated_mesh_count": generated_meshes,
                    "runnable_solver_count": runnable_solvers,
                    "blockers": blockers,
                },
                "meshes": member_meshes,
                "mesh_count": len(all_member_meshes),
                "meshes_truncated": len(all_member_meshes) > len(member_meshes),
                "solvers": member_solvers,
                "solver_count": len(all_member_solvers),
                "solvers_truncated": len(all_member_solvers) > len(member_solvers),
                "results": member_results,
                "result_count": len(all_member_results),
                "results_truncated": len(all_member_results) > len(member_results),
            }
        )
    return workflows


def _run_status(
    document: Any,
    solvers: list[dict[str, Any]],
    background_job: Any | None,
) -> dict[str, Any]:
    result_count = sum(int(state.get("result_count", 0) or 0) for state in solvers)
    if background_job is None:
        return {
            "phase": "idle",
            "terminal": True,
            "solver_result_count": result_count,
        }
    if str(getattr(background_job, "document_uid", "") or "") != str(document.Uid):
        raise RuntimeError("Analyze background status belongs to another document.")
    result = {
        "job_id": str(background_job.job_id),
        "capability": str(background_job.capability_name),
        "phase": str(background_job.phase),
        "progress_percent": int(background_job.progress_percent),
        "progress_message": str(background_job.progress_message)[:160],
        "terminal": bool(background_job.terminal),
        "cancel_requested": bool(background_job.cancel_requested),
        "solver_result_count": result_count,
    }
    error = getattr(background_job, "error", None)
    if isinstance(error, dict):
        result["error"] = {
            key: str(error[key])[:320]
            for key in ("error_code", "message")
            if key in error
        }
    payload = getattr(background_job, "result", None)
    if isinstance(payload, dict):
        solver = payload.get("solver")
        output = payload.get("result")
        execution = payload.get("execution")
        if isinstance(solver, dict) and solver.get("object_name"):
            result["solver"] = str(solver["object_name"])
        if isinstance(output, dict) and output.get("object_name"):
            result["result_object"] = str(output["object_name"])
        if isinstance(execution, dict):
            result["backend"] = str(execution.get("backend") or "")[:80]
            result["implementation"] = str(
                execution.get("implementation") or ""
            )[:80]
    return result


def _active_analysis(document: Any) -> Any | None:
    try:
        import FemGui

        analysis = FemGui.getActiveAnalysis()
        return analysis if getattr(analysis, "Document", None) is document else None
    except (ImportError, AttributeError, RuntimeError):
        return None


def _materials(document: Any) -> tuple[int, list[dict[str, Any]]]:
    result = []
    count = 0
    for obj in list(getattr(document, "Objects", ()) or ()):
        try:
            state = material_state(obj)
        except Exception:
            continue
        count += 1
        if len(result) < MAX_MATERIALS:
            result.append(state)
    return count, result


def _geometry_sources(document: Any) -> tuple[int, list[dict[str, Any]]]:
    result = []
    count = 0
    try:
        import PartGui
    except ImportError:
        return 0, result
    for obj in list(getattr(document, "Objects", ()) or ()):
        shape = getattr(obj, "Shape", None)
        if shape is None:
            continue
        try:
            if (
                shape.isNull()
                or not shape.isValid()
                or not PartGui.isModelingObjectActive(obj)
            ):
                continue
            state = mesh_object_state(obj)
            topology = dict(state.get("topology") or {})
            if not any(
                int(topology.get(name, 0) or 0) > 0
                for name in ("solids", "faces", "edges")
            ):
                continue
            state["clipping_face_target"] = clipping_face_source_state(obj)
        except Exception:
            continue
        count += 1
        if len(result) < MAX_GEOMETRY_SOURCES:
            result.append(state)
    return count, result


def _element_definitions(document: Any) -> tuple[int, list[dict[str, Any]]]:
    result = []
    count = 0
    for obj in list(getattr(document, "Objects", ()) or ()):
        try:
            state = element_definition_state(obj)
        except Exception:
            continue
        count += 1
        if len(result) < MAX_ELEMENT_DEFINITIONS:
            result.append(state)
    return count, result


def _electromagnetic_constraints(document: Any) -> tuple[int, list[dict[str, Any]]]:
    result = []
    count = 0
    for obj in list(getattr(document, "Objects", ()) or ()):
        try:
            state = electromagnetic_constraint_state(obj)
        except Exception:
            continue
        count += 1
        if len(result) < MAX_ELECTROMAGNETIC_CONSTRAINTS:
            result.append(state)
    return count, result


def _fluid_constraints(document: Any) -> tuple[int, list[dict[str, Any]]]:
    result = []
    count = 0
    for obj in list(getattr(document, "Objects", ()) or ()):
        try:
            state = fluid_constraint_state(obj)
        except Exception:
            continue
        count += 1
        if len(result) < MAX_FLUID_CONSTRAINTS:
            result.append(state)
    return count, result


def _geometrical_features(document: Any) -> tuple[int, list[dict[str, Any]]]:
    result = []
    count = 0
    for obj in list(getattr(document, "Objects", ()) or ()):
        try:
            state = geometrical_feature_state(obj)
        except Exception:
            continue
        count += 1
        if len(result) < MAX_GEOMETRICAL_FEATURES:
            result.append(state)
    return count, result


def _support_conditions(document: Any) -> tuple[int, list[dict[str, Any]]]:
    result = []
    count = 0
    for obj in list(getattr(document, "Objects", ()) or ()):
        try:
            state = support_condition_state(obj)
        except Exception:
            continue
        count += 1
        if len(result) < MAX_SUPPORT_CONDITIONS:
            result.append(state)
    return count, result


def _connections(document: Any) -> tuple[int, list[dict[str, Any]]]:
    result = []
    count = 0
    for obj in list(getattr(document, "Objects", ()) or ()):
        try:
            state = connection_state(obj)
        except Exception:
            continue
        count += 1
        if len(result) < MAX_CONNECTIONS:
            result.append(state)
    return count, result


def _loads(document: Any) -> tuple[int, list[dict[str, Any]]]:
    result = []
    count = 0
    for obj in list(getattr(document, "Objects", ()) or ()):
        try:
            state = load_state(obj)
        except Exception:
            continue
        count += 1
        if len(result) < MAX_LOADS:
            result.append(state)
    return count, result


def _thermal_conditions(document: Any) -> tuple[int, list[dict[str, Any]]]:
    result = []
    count = 0
    for obj in list(getattr(document, "Objects", ()) or ()):
        try:
            state = thermal_condition_state(obj)
        except Exception:
            continue
        count += 1
        if len(result) < MAX_THERMAL_CONDITIONS:
            result.append(state)
    return count, result


def _mesh_definitions(
    document: Any,
) -> tuple[int, list[dict[str, Any]], dict[str, dict[str, Any]]]:
    result = []
    states = {}
    count = 0
    for obj in list(getattr(document, "Objects", ()) or ()):
        try:
            state = fem_mesh_definition_state(obj)
        except Exception:
            continue
        count += 1
        states[state["object_name"]] = state
        if len(result) < MAX_MESH_DEFINITIONS:
            result.append(state)
    return count, result, states


def _mesh_refinements(document: Any) -> tuple[int, list[dict[str, Any]]]:
    result = []
    count = 0
    for obj in list(getattr(document, "Objects", ()) or ()):
        try:
            state = mesh_refinement_state(obj)
        except Exception:
            continue
        count += 1
        if len(result) < MAX_MESH_REFINEMENTS:
            result.append(state)
    return count, result


def _fem_mesh_outputs(document: Any) -> tuple[int, list[dict[str, Any]]]:
    result = []
    count = 0
    for obj in list(getattr(document, "Objects", ()) or ()):
        try:
            state = fem_mesh_object_state(obj)
        except Exception:
            continue
        try:
            fem_mesh_definition_state(obj)
        except Exception:
            pass
        else:
            continue
        count += 1
        if len(result) < MAX_FEM_MESH_OUTPUTS:
            result.append(state)
    return count, result


def _mesh_filters(document: Any) -> tuple[int, list[dict[str, Any]]]:
    result = []
    count = 0
    for obj in list(getattr(document, "Objects", ()) or ()):
        try:
            state = mesh_filter_state(obj)
        except Exception:
            continue
        count += 1
        if len(result) < MAX_MESH_FILTERS:
            result.append(state)
    return count, result


def _solvers(
    document: Any,
) -> tuple[int, list[dict[str, Any]], dict[str, dict[str, Any]]]:
    result = []
    states = {}
    count = 0
    for obj in list(getattr(document, "Objects", ()) or ()):
        try:
            state = solver_state(obj)
        except Exception:
            continue
        count += 1
        states[state["object_name"]] = state
        if len(result) < MAX_SOLVERS:
            result.append(state)
    return count, result, states


def _equations(document: Any) -> tuple[int, list[dict[str, Any]]]:
    result = []
    count = 0
    for obj in list(getattr(document, "Objects", ()) or ()):
        try:
            state = equation_state(obj)
        except Exception:
            continue
        count += 1
        if len(result) < MAX_EQUATIONS:
            result.append(state)
    return count, result


def _results(
    document: Any,
) -> tuple[int, list[dict[str, Any]], dict[str, dict[str, Any]]]:
    result = []
    states = {}
    count = 0
    for obj in list(getattr(document, "Objects", ()) or ()):
        try:
            state = result_reference_state(obj)
        except Exception:
            continue
        count += 1
        states[state["object_name"]] = state
        if len(result) < MAX_RESULTS:
            result.append(state)
    return count, result, states


def build_analyze_snapshot(
    document: Any,
    *,
    background_job: Any | None = None,
) -> dict[str, Any]:
    analyses = objects_of_type(document, "Fem::FemAnalysis")
    active = _active_analysis(document)
    summarized = []
    for value in analyses[:MAX_ANALYSES]:
        state = analysis_state(value)
        state["active"] = value is active
        result_graph = result_purge_state(value)
        state["result_graph"] = {
            key: result_graph[key]
            for key in (
                "object_count",
                "solver_result_root_count",
                "ordinary_operation_count",
                "purge_ready",
                "blockers",
                "graph_sha256",
            )
        }
        summarized.append(state)
    material_count, materials = _materials(document)
    geometry_count, geometry = _geometry_sources(document)
    element_count, elements = _element_definitions(document)
    constraint_count, constraints = _electromagnetic_constraints(document)
    fluid_count, fluid_constraints = _fluid_constraints(document)
    geometrical_count, geometrical_features = _geometrical_features(document)
    support_count, support_conditions = _support_conditions(document)
    connection_count, connections = _connections(document)
    load_count, loads = _loads(document)
    thermal_count, thermal_conditions = _thermal_conditions(document)
    mesh_count, mesh_definitions, mesh_states = _mesh_definitions(document)
    refinement_count, mesh_refinements = _mesh_refinements(document)
    output_count, fem_mesh_outputs = _fem_mesh_outputs(document)
    filter_count, mesh_filters = _mesh_filters(document)
    solver_count, solvers, solver_states = _solvers(document)
    equation_count, equations = _equations(document)
    result_count, results, result_states = _results(document)
    workflows = _analysis_workflows(
        analyses,
        summarized,
        mesh_states,
        solver_states,
        result_states,
    )
    try:
        clipping = clipping_state(document)
    except Exception:
        clipping = {"available": False}
    return {
        "kind": "analyze",
        "analysis_count": len(analyses),
        "analyses": summarized,
        "analyses_truncated": len(analyses) > len(summarized),
        "analysis_workflow_count": len(analyses),
        "analysis_workflows": workflows,
        "analysis_workflows_truncated": len(analyses) > len(workflows),
        "run_status": _run_status(
            document,
            list(solver_states.values()),
            background_job,
        ),
        "material_count": material_count,
        "materials": materials,
        "materials_truncated": material_count > len(materials),
        "geometry_source_count": geometry_count,
        "geometry_sources": geometry,
        "geometry_sources_truncated": geometry_count > len(geometry),
        "element_definition_count": element_count,
        "element_definitions": elements,
        "element_definitions_truncated": element_count > len(elements),
        "electromagnetic_constraint_count": constraint_count,
        "electromagnetic_constraints": constraints,
        "electromagnetic_constraints_truncated": constraint_count > len(constraints),
        "fluid_constraint_count": fluid_count,
        "fluid_constraints": fluid_constraints,
        "fluid_constraints_truncated": fluid_count > len(fluid_constraints),
        "geometrical_feature_count": geometrical_count,
        "geometrical_features": geometrical_features,
        "geometrical_features_truncated": geometrical_count > len(geometrical_features),
        "support_condition_count": support_count,
        "support_conditions": support_conditions,
        "support_conditions_truncated": support_count > len(support_conditions),
        "connection_count": connection_count,
        "connections": connections,
        "connections_truncated": connection_count > len(connections),
        "load_count": load_count,
        "loads": loads,
        "loads_truncated": load_count > len(loads),
        "thermal_condition_count": thermal_count,
        "thermal_conditions": thermal_conditions,
        "thermal_conditions_truncated": thermal_count > len(thermal_conditions),
        "mesh_definition_count": mesh_count,
        "mesh_definitions": mesh_definitions,
        "mesh_definitions_truncated": mesh_count > len(mesh_definitions),
        "mesh_refinement_count": refinement_count,
        "mesh_refinements": mesh_refinements,
        "mesh_refinements_truncated": refinement_count > len(mesh_refinements),
        "fem_mesh_output_count": output_count,
        "fem_mesh_outputs": fem_mesh_outputs,
        "fem_mesh_outputs_truncated": output_count > len(fem_mesh_outputs),
        "mesh_filter_count": filter_count,
        "mesh_filters": mesh_filters,
        "mesh_filters_truncated": filter_count > len(mesh_filters),
        "solver_count": solver_count,
        "solvers": solvers,
        "solvers_truncated": solver_count > len(solvers),
        "equation_count": equation_count,
        "equations": equations,
        "equations_truncated": equation_count > len(equations),
        "result_count": result_count,
        "results": results,
        "results_truncated": result_count > len(results),
        "clipping": clipping,
    }
