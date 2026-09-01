# SPDX-License-Identifier: LGPL-2.1-or-later

"""Concise live state for the Analyze ribbon."""

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
from typing import Any, Mapping, Sequence
import uuid

from VibeCADNativeAnalyzeState import analysis_state, material_state
from VibeCADNativeAnalyzeElementState import element_definition_state
from VibeCADNativeAnalyzeConstraintState import electromagnetic_constraint_state
from VibeCADNativeAnalyzeFluidState import fluid_constraint_state
from VibeCADNativeAnalyzeGeometricalState import geometrical_feature_state
from VibeCADNativeAnalyzeSupportState import support_condition_state
from VibeCADNativeAnalyzeConnectionState import connection_state
from VibeCADNativeAnalyzeLoadState import load_state
from VibeCADNativeAnalyzeThermalState import thermal_condition_state
from VibeCADNativeAnalyzeMeshState import (
    fem_mesh_definition_context_state,
    fem_mesh_object_context_state,
    is_fem_mesh_definition,
)
from VibeCADNativeAnalyzeMeshOutputState import mesh_filter_state
from VibeCADNativeAnalyzeMeshRefinementState import mesh_refinement_state
from VibeCADNativeAnalyzeSolverState import solver_state
from VibeCADNativeAnalyzeEquationState import equation_state
from VibeCADNativeAnalyzeResultState import result_reference_state
from VibeCADNativeAnalyzeResults import result_purge_state
from VibeCADNativeAnalyzeStudy import STUDY_PHYSICS, study_intent_state
from VibeCADNativeAnalyzeStudyState import study_inventory, study_state
from VibeCADNativeAnalyzeClipping import (
    clipping_face_source_state,
    clipping_state,
)
from VibeCADNativeAnalyzeGeometrySources import active_analyze_geometry_sources
from VibeCADNativeMeshState import mesh_object_state, mesh_object_state_cache
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
    result = {
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
            "flow_boundaries",
            "flow_boundaries_truncated",
            "state_sha256",
        )
        if key in state
    }
    return result


def _analysis_workflows(
    analyses: list[Any],
    summarized: list[dict[str, Any]],
    mesh_states: dict[str, dict[str, Any]],
    solver_states: dict[str, dict[str, Any]],
    result_states: dict[str, dict[str, Any]],
    study_states: dict[str, dict[str, Any]],
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
        study = study_states[name]
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
                "study": study["intent"],
                "study_inventory": study["inventory"],
                "solver_runtimes": study["solver_runtimes"],
                "engineering_readiness": study["readiness"],
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


def _provider_scope(
    analyses: list[Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    physics = set()
    undeclared = 0
    totals = {
        "mesh_definition_count": 0,
        "generated_mesh_count": 0,
        "solver_count": 0,
        "result_count": 0,
    }
    states = {}
    for index, analysis in enumerate(analyses):
        if index < MAX_ANALYSES:
            state = study_state(analysis)
            states[str(analysis.Name)] = state
            intent = state["intent"]
            inventory = state["inventory"]
        else:
            intent = study_intent_state(analysis)
            inventory = study_inventory(analysis)
        if intent.get("declared") is True:
            physics.update(intent["physics"])
        else:
            undeclared += 1
        for name in totals:
            totals[name] += int(inventory[name])
    return (
        {
            "analysis_count": len(analyses),
            "undeclared_analysis_count": undeclared,
            "physics": [name for name in STUDY_PHYSICS if name in physics],
            **totals,
        },
        states,
    )


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
    if isinstance(background_job, (tuple, list)):
        jobs = [
            _run_status(document, solvers, job)
            for job in background_job
        ]
        if not jobs:
            return {
                "phase": "idle",
                "terminal": True,
                "solver_result_count": result_count,
            }
        return {
            "phase": "completed" if all(job["terminal"] for job in jobs) else "running",
            "terminal": all(job["terminal"] for job in jobs),
            "background_jobs": jobs,
            "solver_result_count": result_count,
        }
    if str(getattr(background_job, "document_uid", "") or "") != str(document.Uid):
        raise RuntimeError("Analyze background status belongs to another document.")
    result = {
        "job_id": str(background_job.job_id),
        "capability": str(background_job.capability_name),
        "resource_scope": str(getattr(background_job, "resource_scope", "") or ""),
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


def _geometry_sources(
    document: Any,
    *,
    filter_analysis_sources: bool = True,
    validate_brep: bool = True,
    include_clipping_face_targets: bool = True,
    validation_artifact_root: str = "",
) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]]]:
    result = []
    artifacts = []
    count = 0
    for obj in active_analyze_geometry_sources(
        document,
        filter_analysis_sources=filter_analysis_sources,
        validate_brep=validate_brep,
    ):
        try:
            state = mesh_object_state(obj)
            topology = state.get("topology")
            if not isinstance(topology, Mapping) or not any(
                isinstance(value, int) and value > 0
                for value in topology.values()
            ):
                continue
            if include_clipping_face_targets:
                state["clipping_face_target"] = clipping_face_source_state(
                    obj,
                    prevalidated=validate_brep,
                )
            if validation_artifact_root:
                identity = hashlib.sha256(
                    (
                        f"{getattr(document, 'Uid', '')}\0{getattr(obj, 'Name', '')}"
                        f"\0{state.get('state_sha256', '')}"
                    ).encode("utf-8")
                ).hexdigest()
                root = Path(validation_artifact_root)
                root.mkdir(parents=True, exist_ok=True)
                shape_path = root / f"{identity}.brep"
                obj.Shape.exportBrep(str(shape_path))
                if not shape_path.is_file() or shape_path.stat().st_size <= 0:
                    raise OSError("BREP validation capture produced no artifact.")
                state["_brep_validation_identity"] = identity
                artifacts.append(
                    {
                        "identity": identity,
                        "shape_path": str(shape_path),
                    }
                )
            if bool(getattr(obj, "VibeCADAnalysisDomain", False)):
                mode = str(getattr(obj, "AnalysisInterfaceMode", "") or "")
                state["interface_mode"] = mode
                shape = obj.Shape
                state["all_solids_conformal"] = bool(
                    mode == "shared"
                    and len(shape.CompSolids) == 1
                    and len(shape.CompSolids[0].Solids) == len(shape.Solids)
                )
                if not filter_analysis_sources:
                    state["_is_analysis_domain"] = True
                    state["_analysis_source_names"] = [
                        str(getattr(source, "Name", "") or "")
                        for source in tuple(
                            getattr(obj, "AnalysisSources", ()) or ()
                        )
                        if str(getattr(source, "Name", "") or "")
                    ]
        except Exception:
            continue
        count += 1
        if len(result) < MAX_GEOMETRY_SOURCES:
            result.append(state)
    return count, result, artifacts


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
            state = fem_mesh_definition_context_state(obj)
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
        if is_fem_mesh_definition(obj):
            continue
        try:
            state = fem_mesh_object_context_state(obj)
        except Exception:
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


_COLLECTION_LIMITS = {
    "materials": MAX_MATERIALS,
    "geometry_sources": MAX_GEOMETRY_SOURCES,
    "element_definitions": MAX_ELEMENT_DEFINITIONS,
    "electromagnetic_constraints": MAX_ELECTROMAGNETIC_CONSTRAINTS,
    "fluid_constraints": MAX_FLUID_CONSTRAINTS,
    "geometrical_features": MAX_GEOMETRICAL_FEATURES,
    "support_conditions": MAX_SUPPORT_CONDITIONS,
    "connections": MAX_CONNECTIONS,
    "loads": MAX_LOADS,
    "thermal_conditions": MAX_THERMAL_CONDITIONS,
    "mesh_definitions": MAX_MESH_DEFINITIONS,
    "mesh_refinements": MAX_MESH_REFINEMENTS,
    "fem_mesh_outputs": MAX_FEM_MESH_OUTPUTS,
    "mesh_filters": MAX_MESH_FILTERS,
    "solvers": MAX_SOLVERS,
    "equations": MAX_EQUATIONS,
    "results": MAX_RESULTS,
}


def _background_job_payload(
    document_uid: str,
    background_job: Any | None,
) -> dict[str, Any] | list[dict[str, Any]] | None:
    if background_job is None:
        return None
    if isinstance(background_job, (tuple, list)):
        payloads = [
            _background_job_payload(document_uid, job)
            for job in background_job
        ]
        return [payload for payload in payloads if isinstance(payload, dict)]
    if str(getattr(background_job, "document_uid", "") or "") != document_uid:
        raise RuntimeError("Analyze background status belongs to another document.")
    result: dict[str, Any] = {
        "job_id": str(background_job.job_id),
        "capability": str(background_job.capability_name),
        "resource_scope": str(getattr(background_job, "resource_scope", "") or ""),
        "phase": str(background_job.phase),
        "progress_percent": int(background_job.progress_percent),
        "progress_message": str(background_job.progress_message)[:160],
        "terminal": bool(background_job.terminal),
        "cancel_requested": bool(background_job.cancel_requested),
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


def begin_analyze_snapshot_capture(
    document: Any,
    *,
    background_job: Any | None = None,
    defer_brep_validation: bool = False,
    validate_brep: bool = True,
    analysis_artifact_names: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Capture only detached identity needed to schedule bounded reads."""

    if type(defer_brep_validation) is not bool:
        raise TypeError("defer_brep_validation must be a boolean")
    if type(validate_brep) is not bool:
        raise TypeError("validate_brep must be a boolean")
    if defer_brep_validation and not validate_brep:
        raise ValueError(
            "defer_brep_validation cannot be enabled when validate_brep is false"
        )
    document_uid = str(getattr(document, "Uid", "") or "").strip()
    if not document_uid:
        raise RuntimeError("Analyze context requires an exact document UID.")
    from VibeCADNativeGeometrySources import (
        drawing_analysis_artifact_names,
        is_analyze_context_object,
    )

    analysis_artifacts = (
        drawing_analysis_artifact_names(document)
        if analysis_artifact_names is None
        else analysis_artifact_names
    )
    objects = tuple(
        obj
        for obj in tuple(getattr(document, "Objects", ()) or ())
        if is_analyze_context_object(
            document,
            obj,
            analysis_artifact_names=analysis_artifacts,
        )
    )
    object_names = [str(getattr(obj, "Name", "") or "") for obj in objects]
    if any(not name for name in object_names) or len(object_names) != len(
        set(object_names)
    ):
        raise RuntimeError("Analyze context requires unique live object names.")
    analyses = objects_of_type(
        SimpleNamespace(Objects=objects),
        "Fem::FemAnalysis",
    )
    active = _active_analysis(document)
    validation_root = (
        str(
            Path(tempfile.gettempdir())
            / f"vibecad-analyze-validation-{uuid.uuid4().hex}"
        )
        if validate_brep and defer_brep_validation
        else ""
    )
    return {
        "document_uid": document_uid,
        "object_names": object_names,
        "analysis_names": [str(value.Name) for value in analyses],
        "active_analysis_name": (
            str(getattr(active, "Name", "") or "") if active is not None else ""
        ),
        "background_job": _background_job_payload(document_uid, background_job),
        "validate_brep": validate_brep,
        "defer_brep_validation": defer_brep_validation,
        "geometry_validation_artifact_root": validation_root,
    }


def _analysis_capture_record(
    analysis: Any,
    *,
    active_analysis_name: str,
    include_summary: bool,
) -> dict[str, Any]:
    if include_summary:
        summary = analysis_state(analysis)
        summary["active"] = str(analysis.Name) == active_analysis_name
        result_graph = result_purge_state(analysis)
        summary["result_graph"] = {
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
        state = study_state(
            analysis,
            mesh_state_reader=fem_mesh_definition_context_state,
        )
        member_names = [
            str(member.Name)
            for member in tuple(getattr(analysis, "Group", ()) or ())
        ]
    else:
        summary = None
        state = {
            "intent": study_intent_state(analysis),
            "inventory": study_inventory(
                analysis,
                mesh_state_reader=fem_mesh_definition_context_state,
            ),
        }
        member_names = []
    return {
        "object_name": str(analysis.Name),
        "summary": summary,
        "study": state,
        "member_names": member_names,
    }


def capture_analyze_snapshot_batch(
    document: Any,
    request: Mapping[str, Any],
    object_names: Sequence[str],
) -> dict[str, Any]:
    """Read one bounded batch while reusing immutable source topology state."""

    with mesh_object_state_cache():
        return _capture_analyze_snapshot_batch(document, request, object_names)


def _capture_analyze_snapshot_batch(
    document: Any,
    request: Mapping[str, Any],
    object_names: Sequence[str],
) -> dict[str, Any]:
    """Read one bounded object-name batch on the owning document thread."""

    document_uid = str(getattr(document, "Uid", "") or "")
    if document_uid != str(request.get("document_uid") or ""):
        raise RuntimeError("Analyze context request belongs to another document.")
    get_object = getattr(document, "getObject", None)
    if not callable(get_object):
        raise RuntimeError("Analyze context document cannot resolve exact objects.")
    objects = []
    for raw_name in object_names:
        name = str(raw_name or "")
        obj = get_object(name)
        if obj is None or getattr(obj, "Document", None) is not document:
            raise RuntimeError(
                f"Analyze context object {name!r} changed during capture."
            )
        objects.append(obj)
    batch = SimpleNamespace(Uid=document_uid, Objects=objects)
    analysis_names = [str(name) for name in list(request.get("analysis_names") or [])]
    analysis_indexes = {name: index for index, name in enumerate(analysis_names)}
    analyses = []
    for obj in objects:
        name = str(getattr(obj, "Name", "") or "")
        index = analysis_indexes.get(name)
        if index is None:
            continue
        analyses.append(
            _analysis_capture_record(
                obj,
                active_analysis_name=str(request.get("active_analysis_name") or ""),
                include_summary=index < MAX_ANALYSES,
            )
        )

    material_count, materials = _materials(batch)
    validate_brep = bool(request.get("validate_brep", True))
    defer_brep_validation = validate_brep and bool(
        request.get("defer_brep_validation", False)
    )
    geometry_count, geometry, geometry_validation_artifacts = _geometry_sources(
        batch,
        filter_analysis_sources=False,
        validate_brep=validate_brep and not defer_brep_validation,
        include_clipping_face_targets=validate_brep and not defer_brep_validation,
        validation_artifact_root=(
            str(request.get("geometry_validation_artifact_root") or "")
            if defer_brep_validation
            else ""
        ),
    )
    element_count, elements = _element_definitions(batch)
    constraint_count, constraints = _electromagnetic_constraints(batch)
    fluid_count, fluid_constraints = _fluid_constraints(batch)
    geometrical_count, geometrical_features = _geometrical_features(batch)
    support_count, support_conditions = _support_conditions(batch)
    connection_count, connections = _connections(batch)
    load_count, loads = _loads(batch)
    thermal_count, thermal_conditions = _thermal_conditions(batch)
    mesh_count, mesh_definitions, mesh_states = _mesh_definitions(batch)
    refinement_count, mesh_refinements = _mesh_refinements(batch)
    output_count, fem_mesh_outputs = _fem_mesh_outputs(batch)
    filter_count, mesh_filters = _mesh_filters(batch)
    solver_count, solvers, solver_states = _solvers(batch)
    equation_count, equations = _equations(batch)
    result_count, results, result_states = _results(batch)
    return {
        "analyses": analyses,
        "material_count": material_count,
        "materials": materials,
        "geometry_source_count": geometry_count,
        "geometry_sources": geometry,
        "geometry_validation_artifacts": geometry_validation_artifacts,
        "element_definition_count": element_count,
        "element_definitions": elements,
        "electromagnetic_constraint_count": constraint_count,
        "electromagnetic_constraints": constraints,
        "fluid_constraint_count": fluid_count,
        "fluid_constraints": fluid_constraints,
        "geometrical_feature_count": geometrical_count,
        "geometrical_features": geometrical_features,
        "support_condition_count": support_count,
        "support_conditions": support_conditions,
        "connection_count": connection_count,
        "connections": connections,
        "load_count": load_count,
        "loads": loads,
        "thermal_condition_count": thermal_count,
        "thermal_conditions": thermal_conditions,
        "mesh_definition_count": mesh_count,
        "mesh_definitions": mesh_definitions,
        "mesh_states": mesh_states,
        "mesh_refinement_count": refinement_count,
        "mesh_refinements": mesh_refinements,
        "fem_mesh_output_count": output_count,
        "fem_mesh_outputs": fem_mesh_outputs,
        "mesh_filter_count": filter_count,
        "mesh_filters": mesh_filters,
        "solver_count": solver_count,
        "solvers": solvers,
        "solver_states": solver_states,
        "equation_count": equation_count,
        "equations": equations,
        "result_count": result_count,
        "results": results,
        "result_states": result_states,
    }


def validate_analyze_snapshot_geometry(
    request: Mapping[str, Any],
    parts: Sequence[Mapping[str, Any]],
    cancellation_check: Any | None,
    progress_callback: Any | None,
) -> list[dict[str, Any]]:
    """Validate captured geometry off-thread and keep exact valid sources."""

    copied = [dict(part) for part in parts]
    if request.get("defer_brep_validation") is not True:
        return copied
    artifacts = [
        dict(artifact)
        for part in copied
        for artifact in list(part.get("geometry_validation_artifacts") or [])
        if isinstance(artifact, Mapping)
    ]
    if progress_callback is not None:
        progress_callback(87, f"Validating geometry 0 of {len(artifacts)}")
    from VibeCADGeometry import validate_brep_artifacts_parallel

    validations = validate_brep_artifacts_parallel(
        artifacts,
        cancellation_check=cancellation_check,
    )
    valid = {
        str(value.get("identity") or "")
        for value in validations
        if value.get("ok") is True and value.get("valid") is True
    }
    for part in copied:
        sources = []
        for raw in list(part.get("geometry_sources") or []):
            if not isinstance(raw, Mapping):
                continue
            state = dict(raw)
            identity = str(state.pop("_brep_validation_identity", "") or "")
            if identity in valid:
                sources.append(state)
        part["geometry_sources"] = sources
        part["geometry_source_count"] = len(sources)
        part.pop("geometry_validation_artifacts", None)
    if progress_callback is not None:
        progress_callback(
            89,
            f"Validated geometry {len(valid)} of {len(artifacts)}",
        )
    return copied


def discard_analyze_snapshot_capture(request: Mapping[str, Any]) -> None:
    root = str(request.get("geometry_validation_artifact_root") or "").strip()
    if root:
        shutil.rmtree(Path(root), ignore_errors=True)


def capture_analyze_clipping(
    document: Any,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    if str(getattr(document, "Uid", "") or "") != str(
        request.get("document_uid") or ""
    ):
        raise RuntimeError("Analyze clipping request belongs to another document.")
    try:
        return dict(clipping_state(document))
    except Exception:
        return {"available": False}


def _merged_collection(
    parts: Sequence[Mapping[str, Any]],
    values_name: str,
    count_name: str,
) -> tuple[int, list[dict[str, Any]]]:
    count = sum(int(part.get(count_name, 0) or 0) for part in parts)
    values = [
        dict(value)
        for part in parts
        for value in list(part.get(values_name) or [])
        if isinstance(value, Mapping)
    ]
    return count, values[: _COLLECTION_LIMITS[values_name]]


def _provider_scope_from_records(
    records: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    physics = set()
    undeclared = 0
    totals = {
        "mesh_definition_count": 0,
        "generated_mesh_count": 0,
        "solver_count": 0,
        "result_count": 0,
    }
    states = {}
    for record in records:
        name = str(record.get("object_name") or "")
        state = record.get("study")
        if not name or not isinstance(state, Mapping):
            raise RuntimeError("Analyze study capture is incomplete.")
        intent = state.get("intent")
        inventory = state.get("inventory")
        if not isinstance(intent, Mapping) or not isinstance(inventory, Mapping):
            raise RuntimeError("Analyze study capture is incomplete.")
        if record.get("summary") is not None:
            states[name] = dict(state)
        if intent.get("declared") is True:
            physics.update(list(intent.get("physics") or []))
        else:
            undeclared += 1
        for field in totals:
            totals[field] += int(inventory.get(field, 0) or 0)
    return (
        {
            "analysis_count": len(records),
            "undeclared_analysis_count": undeclared,
            "physics": [name for name in STUDY_PHYSICS if name in physics],
            **totals,
        },
        states,
    )


def _analysis_workflows_from_records(
    records: Sequence[Mapping[str, Any]],
    mesh_states: Mapping[str, dict[str, Any]],
    solver_states: Mapping[str, dict[str, Any]],
    result_states: Mapping[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    workflows = []
    for record in records[:MAX_ANALYSES]:
        analysis_summary = record.get("summary")
        study = record.get("study")
        if not isinstance(analysis_summary, Mapping) or not isinstance(study, Mapping):
            raise RuntimeError("Analyze workflow capture is incomplete.")
        name = str(record.get("object_name") or "")
        member_names = set(str(value) for value in record.get("member_names") or [])
        all_member_meshes = [
            state
            for state in mesh_states.values()
            if state.get("object_name") in member_names
        ]
        all_member_solvers = [
            state for state in solver_states.values() if state.get("analysis") == name
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
                "study": dict(study["intent"]),
                "study_inventory": dict(study["inventory"]),
                "solver_runtimes": list(study.get("solver_runtimes") or []),
                "engineering_readiness": dict(study.get("readiness") or {}),
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


def finish_analyze_snapshot_capture(
    request: Mapping[str, Any],
    parts: Sequence[Mapping[str, Any]],
    clipping: Mapping[str, Any],
) -> dict[str, Any]:
    """Assemble detached batch data without touching live FreeCAD objects."""

    records = [
        dict(record)
        for part in parts
        for record in list(part.get("analyses") or [])
        if isinstance(record, Mapping)
    ]
    expected_analyses = [str(name) for name in request.get("analysis_names") or []]
    if [str(record.get("object_name") or "") for record in records] != expected_analyses:
        raise RuntimeError("Analyze studies changed during context capture.")
    provider_scope, _study_states = _provider_scope_from_records(records)
    mesh_states = {
        str(name): dict(value)
        for part in parts
        for name, value in dict(part.get("mesh_states") or {}).items()
        if isinstance(value, Mapping)
    }
    solver_states = {
        str(name): dict(value)
        for part in parts
        for name, value in dict(part.get("solver_states") or {}).items()
        if isinstance(value, Mapping)
    }
    result_states = {
        str(name): dict(value)
        for part in parts
        for name, value in dict(part.get("result_states") or {}).items()
        if isinstance(value, Mapping)
    }
    workflows = _analysis_workflows_from_records(
        records,
        mesh_states,
        solver_states,
        result_states,
    )
    summarized = [
        dict(record["summary"])
        for record in records[:MAX_ANALYSES]
        if isinstance(record.get("summary"), Mapping)
    ]
    collections = {}
    collection_fields = (
        ("materials", "material_count"),
        ("element_definitions", "element_definition_count"),
        ("electromagnetic_constraints", "electromagnetic_constraint_count"),
        ("fluid_constraints", "fluid_constraint_count"),
        ("geometrical_features", "geometrical_feature_count"),
        ("support_conditions", "support_condition_count"),
        ("connections", "connection_count"),
        ("loads", "load_count"),
        ("thermal_conditions", "thermal_condition_count"),
        ("mesh_definitions", "mesh_definition_count"),
        ("mesh_refinements", "mesh_refinement_count"),
        ("fem_mesh_outputs", "fem_mesh_output_count"),
        ("mesh_filters", "mesh_filter_count"),
        ("solvers", "solver_count"),
        ("equations", "equation_count"),
        ("results", "result_count"),
    )
    for values_name, count_name in collection_fields:
        count, values = _merged_collection(parts, values_name, count_name)
        collections[count_name] = count
        collections[values_name] = values
        collections[values_name + "_truncated"] = count > len(values)

    geometry = [
        dict(value)
        for part in parts
        for value in list(part.get("geometry_sources") or [])
        if isinstance(value, Mapping)
    ]
    hidden_domain_sources = {
        name
        for value in geometry
        if value.get("_is_analysis_domain") is True
        for name in list(value.get("_analysis_source_names") or [])
    }
    geometry = [
        value
        for value in geometry
        if value.get("_is_analysis_domain") is True
        or str(value.get("object_name") or "") not in hidden_domain_sources
    ]
    for value in geometry:
        value.pop("_is_analysis_domain", None)
        value.pop("_analysis_source_names", None)
    collections["geometry_source_count"] = len(geometry)
    collections["geometry_sources"] = geometry[:MAX_GEOMETRY_SOURCES]
    collections["geometry_sources_truncated"] = (
        collections["geometry_source_count"] > len(collections["geometry_sources"])
    )

    result_count = sum(
        int(state.get("result_count", 0) or 0) for state in solver_states.values()
    )
    background_job = request.get("background_job")
    if isinstance(background_job, Mapping):
        run_status = dict(background_job)
    elif isinstance(background_job, list):
        terminal = all(bool(job.get("terminal")) for job in background_job)
        run_status = {
            "phase": "completed" if terminal else "running",
            "terminal": terminal,
            "background_jobs": [dict(job) for job in background_job],
        }
    else:
        run_status = {"phase": "idle", "terminal": True}
    run_status["solver_result_count"] = result_count
    analysis_count = len(expected_analyses)
    return {
        "kind": "analyze",
        "analysis_count": analysis_count,
        "analyses": summarized,
        "analyses_truncated": analysis_count > len(summarized),
        "analysis_workflow_count": analysis_count,
        "analysis_workflows": workflows,
        "analysis_workflows_truncated": analysis_count > len(workflows),
        "provider_scope": provider_scope,
        "run_status": run_status,
        **collections,
        "clipping": dict(clipping),
    }


def build_analyze_snapshot(
    document: Any,
    *,
    background_job: Any | None = None,
) -> dict[str, Any]:
    request = begin_analyze_snapshot_capture(
        document,
        background_job=background_job,
    )
    part = capture_analyze_snapshot_batch(
        document,
        request,
        request["object_names"],
    )
    clipping = capture_analyze_clipping(document, request)
    return finish_analyze_snapshot_capture(request, [part], clipping)
