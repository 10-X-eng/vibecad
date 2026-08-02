# SPDX-License-Identifier: LGPL-2.1-or-later

"""Start, inspect, or cancel one asynchronous native CalculiX solve."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
from typing import Any

from VibeCADTools import tool_failure
from VibeCADTransactions import run_freecad_transaction
import VibeCADReferenceContracts as reference_contracts

from . import domain_runtime


_RESULT_SNAPSHOTS: dict[str, dict[str, str]] = {}

_START_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "const": "start",
            "description": "Prepare and start CalculiX.",
        },
        "analysis_name": {
            "type": "string",
            "minLength": 1,
            "description": "Exact FEM analysis name.",
        },
    },
    "required": ["action", "analysis_name"],
    "additionalProperties": False,
}

_STATUS_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "const": "status",
            "description": "Inspect an existing CalculiX solve.",
        },
        "operation_id": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Exact operation ID returned by operation.action='start'."
            ),
        },
    },
    "required": ["action", "operation_id"],
    "additionalProperties": False,
}

_CANCEL_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "const": "cancel",
            "description": "Cancel an existing CalculiX solve.",
        },
        "operation_id": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Exact operation ID returned by operation.action='start'."
            ),
        },
    },
    "required": ["action", "operation_id"],
    "additionalProperties": False,
}

_OPERATION_SCHEMA = {
    "description": (
        "Exactly one CalculiX lifecycle operation. Each action exposes only the "
        "fields that operation accepts."
    ),
    "oneOf": [_START_SCHEMA, _STATUS_SCHEMA, _CANCEL_SCHEMA],
}


TOOL_SPEC = {
    "name": "fem.solve",
    "description": (
        "Control one asynchronous CalculiX solve lifecycle through one exact "
        "operation object. Start performs structured prerequisite and input-writer "
        "checks and returns immediately; status polls its operation_id and cancel "
        "stops it. Completion reports exact process output and the exact result "
        "root and resources returned by this solve generation."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "FemWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {"operation": _OPERATION_SCHEMA},
        "required": ["operation"],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    operation: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(operation, dict):
        return _invalid(
            "operation must be an object.",
            failure_code="OPERATION_SCHEMA_INVALID",
            failure_stage="schema",
            requested={"operation": operation},
            required_changes=[{"operation": "one start, status, or cancel object"}],
        )
    clean_action = str(operation.get("action") or "").strip()
    if clean_action == "start":
        result = _start(service, str(operation.get("analysis_name") or "").strip())
    elif clean_action == "status":
        result = _status(service, str(operation.get("operation_id") or "").strip())
    elif clean_action == "cancel":
        result = _cancel(service, str(operation.get("operation_id") or "").strip())
    else:
        return _invalid(
            "operation.action must be one of: start, status, cancel.",
            failure_code="OPERATION_ACTION_INVALID",
            failure_stage="schema",
            requested={"operation": operation},
            allowed_values=["start", "status", "cancel"],
            required_changes=[{"operation.action": ["start", "status", "cancel"]}],
        )
    if not result.get("ok") and not result.get("requested"):
        result["requested"] = {"operation": operation}
    return result


def _start(service: Any, analysis_name: str) -> dict[str, Any]:
    analysis = service._get_fem_analysis(analysis_name)
    if analysis is None:
        return _invalid(f"FEM analysis not found by exact internal name: {analysis_name}")
    solver = _find_solver(analysis)
    if solver is None:
        return _invalid("The analysis must contain exactly one CalculiX solver.")
    active_operations = [
        member
        for member in list(getattr(analysis, "Group", []) or [])
        if str(getattr(member, "VibeCADOperationKind", "")) == "calculix"
        and str(getattr(member, "VibeCADOperationState", ""))
        in {"preparing", "starting", "running", "importing_results"}
    ]
    if active_operations:
        return _invalid(
            "This analysis already has an active CalculiX operation.",
            active_operations=[
                {
                    "solver": item.Name,
                    "operation_id": str(item.VibeCADOperationId),
                    "state": str(item.VibeCADOperationState),
                }
                for item in active_operations
            ],
        )
    prerequisites = _structured_prerequisites(analysis, solver)
    if not prerequisites["ready"]:
        return _invalid(
            "The analysis is not ready for CalculiX; no process was started.",
            prerequisites=prerequisites,
        )
    executable = _calculix_preflight()
    if not executable["ok"]:
        return _invalid(
            "CalculiX executable preflight failed; no process was started.",
            executable=executable,
        )
    before_results = _result_snapshot(analysis, solver)

    def start() -> dict[str, Any]:
        import FreeCAD as App
        from femsolver.calculix.calculixtools import CalculiXTools

        active = App.ActiveDocument
        target = active.getObject(analysis.Name) if active is not None else None
        current_solver = active.getObject(solver.Name) if active is not None else None
        if target is None or current_solver is None:
            raise RuntimeError("The analysis or solver no longer exists.")
        tool = CalculiXTools(current_solver)
        operation_id = tool.operation_id
        _add_operation_properties(current_solver, operation_id, target.Name, "preparing")
        _RESULT_SNAPSHOTS[operation_id] = before_results
        stages: list[dict[str, Any]] = []
        try:
            tool.operation_state = "preparing"
            tool.prepare()
            stages.append(
                {
                    "stage": "input_writer",
                    "status": "completed",
                    "input_file": str(tool.model_file),
                    "input_exists": os.path.isfile(str(tool.model_file)),
                    "input_size_bytes": os.path.getsize(str(tool.model_file))
                    if os.path.isfile(str(tool.model_file))
                    else 0,
                }
            )
            tool.operation_state = "starting"
            tool.compute()
            if not tool.process.waitForStarted(5000):
                raise RuntimeError(tool.process.errorString())
            stages.append({"stage": "external_process", "status": "started"})
            current_solver.VibeCADOperationState = "running"
        except Exception as exc:
            tool.operation_state = "failed"
            tool.operation_error = str(exc)
            current_solver.VibeCADOperationState = "failed"
            stages.append(
                {
                    "stage": "input_writer_or_process_start",
                    "status": "failed",
                    "native_error": str(exc),
                }
            )
        active.recompute()
        diagnostics = tool.process_diagnostics()
        return {
            "document": active.Name,
            "analysis": target.Name,
            "solver": current_solver.Name,
            "analysis_type": str(current_solver.AnalysisType),
            "solve_generation_id": operation_id,
            "operation_id": operation_id,
            "prerequisites": prerequisites,
            "executable_preflight": executable,
            "stages": stages,
            "process": diagnostics,
            "result_snapshot_before": before_results,
            "retained_solver": current_solver.Name,
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        process = result.get("process") or {}
        input_stage = next(
            (stage for stage in result.get("stages", []) if stage.get("stage") == "input_writer"),
            None,
        )
        checks = [
            {
                "name": "input_writer_completed",
                "ok": bool(input_stage)
                and input_stage.get("status") == "completed"
                and input_stage.get("input_exists") is True
                and int(input_stage.get("input_size_bytes", 0)) > 0,
                "stage": input_stage,
            },
            {
                "name": "process_started",
                "ok": process.get("operation_state")
                in {"running", "completed", "importing_results"},
                "process": process,
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Start CalculiX solve: {analysis.Name}", start, verifier=verify
    )
    result = (
        transaction.get("result", {})
        if isinstance(transaction, dict) and isinstance(transaction.get("result"), dict)
        else {}
    )
    return domain_runtime.build_mutation_result(
        transaction,
        extra={"operation": "start_solve", **result},
        next_action=(
            "Poll fem.solve with operation.action='status' and this operation_id; "
            "use operation.action='cancel' if the solve should stop."
        ),
    )


def _status(service: Any, operation_id: str) -> dict[str, Any]:
    operation = _operation(service, operation_id)
    if isinstance(operation, dict):
        return operation
    solver, tool, analysis = operation
    diagnostics = tool.process_diagnostics()
    state = diagnostics.get("operation_state")
    if state == "completed" and not bool(solver.VibeCADOperationFinalized):
        return _finalize_solve(analysis, solver, tool)
    if state in {"failed", "cancelled"}:
        _persist_state(solver, state)
        return {
            "ok": False,
            "operation": "solve_status",
            "operation_id": operation_id,
            "solve_generation_id": operation_id,
            "operation_state": state,
            "complete": True,
            "process": diagnostics,
            "retained_solver": solver.Name,
            "retry_same_call": False,
        }
    return {
        "ok": True,
        "operation": "solve_status",
        "operation_id": operation_id,
        "solve_generation_id": operation_id,
        "operation_state": state,
        "complete": False,
        "process": diagnostics,
        "next_action": "Poll status again later, or cancel this operation.",
    }


def _cancel(service: Any, operation_id: str) -> dict[str, Any]:
    operation = _operation(service, operation_id)
    if isinstance(operation, dict):
        return operation
    solver, tool, _analysis = operation
    from PySide.QtCore import QProcess

    if tool.cancel_requested and tool.process.state() != QProcess.ProcessState.NotRunning:
        diagnostics = tool.kill()
        action_taken = "kill_after_prior_cancel"
    else:
        diagnostics = tool.cancel()
        action_taken = "terminate_requested"
    _persist_state(solver, diagnostics.get("operation_state") or "cancel_requested")
    return {
        "ok": True,
        "operation": "cancel_solve",
        "operation_id": operation_id,
        "action_taken": action_taken,
        "process": diagnostics,
        "next_action": "Poll status until operation_state is cancelled or failed.",
    }


def _finalize_solve(analysis: Any, solver: Any, tool: Any) -> dict[str, Any]:
    operation_id = str(solver.VibeCADOperationId)
    before = _RESULT_SNAPSHOTS.get(operation_id)

    def finalize() -> dict[str, Any]:
        process = tool.process_diagnostics()
        try:
            exact_generation = _exact_imported_result_generation(
                solver,
                process,
            )
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            solver.VibeCADOperationState = "failed_provenance"
            solver.Document.recompute()
            return {
                "analysis": analysis.Name,
                "solver": solver.Name,
                "operation_id": operation_id,
                "failed_stage": {
                    "stage": "result_provenance",
                    "reason": "invalid_native_result_graph",
                    "native_error": str(exc),
                },
                "process": process,
                "created_results": [],
                "changed_results": [],
                "result_summaries": [],
            }

        after = _result_snapshot(analysis, solver)
        if exact_generation is None:
            if before is None:
                solver.VibeCADOperationState = "failed_provenance"
                solver.Document.recompute()
                return {
                    "analysis": analysis.Name,
                    "solver": solver.Name,
                    "operation_id": operation_id,
                    "failed_stage": {
                        "stage": "result_provenance",
                        "reason": "start_snapshot_unavailable",
                    },
                    "process": process,
                    "created_results": [],
                    "changed_results": [],
                    "result_summaries": [],
                }
            created = sorted(set(after) - set(before))
            changed = sorted(
                name
                for name in set(after) & set(before)
                if after[name] != before[name]
            )
            result_objects = [
                analysis.Document.getObject(name)
                for name in created + changed
            ]
            provenance_source = "legacy_result_snapshot"
        else:
            created = exact_generation["created_results"]
            changed = exact_generation["changed_results"]
            result_objects = exact_generation["result_objects"]
            provenance_source = "native_result_graph"
        summaries = [_result_summary(obj) for obj in result_objects]
        completeness = _result_completeness(
            str(solver.AnalysisType), created, changed, summaries
        )
        if completeness["complete"]:
            solver.VibeCADOperationState = "completed"
            solver.VibeCADOperationFinalized = True
        else:
            solver.VibeCADOperationState = "failed_result_completeness"
        solver.Document.recompute()
        return {
            "document": solver.Document.Name,
            "analysis": analysis.Name,
            "solver": solver.Name,
            "analysis_type": str(solver.AnalysisType),
            "operation_id": operation_id,
            "solve_generation_id": operation_id,
            "operation_state": str(solver.VibeCADOperationState),
            "complete": True,
            "process": process,
            "result_provenance_source": provenance_source,
            "result_snapshot_before": before,
            "result_snapshot_after": after,
            "created_results": created,
            "changed_results": changed,
            "result_summaries": summaries,
            "result_completeness": completeness,
            "failed_stage": None if completeness["complete"] else {
                "stage": "result_completeness",
                "details": completeness,
            },
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        process = result.get("process") or {}
        completeness = result.get("result_completeness") or {}
        checks = [
            {
                "name": "external_process_success",
                "ok": (process.get("process") or {}).get("exit_code") == 0
                and process.get("operation_state") == "completed",
                "process": process,
            },
            {
                "name": "generation_result_provenance",
                "ok": bool(result.get("created_results") or result.get("changed_results"))
                or str(solver.AnalysisType) == "check",
                "created_results": result.get("created_results"),
                "changed_results": result.get("changed_results"),
            },
            {
                "name": "result_completeness",
                "ok": completeness.get("complete") is True,
                "actual": completeness,
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Finalize CalculiX solve: {analysis.Name}", finalize, verifier=verify
    )
    _RESULT_SNAPSHOTS.pop(operation_id, None)
    result = (
        transaction.get("result", {})
        if isinstance(transaction, dict) and isinstance(transaction.get("result"), dict)
        else {}
    )
    return domain_runtime.build_mutation_result(
        transaction,
        extra={"operation": "finalize_solve", **result},
        next_action=(
            "Use only the result objects attributed to this solve generation; "
            "check stress/displacement convergence against a refined mesh."
        ),
    )


def _exact_imported_result_generation(
    solver: Any,
    process_diagnostics: dict[str, Any],
) -> dict[str, Any] | None:
    """Resolve the exact result identities returned by the native importer."""

    property_update = process_diagnostics.get("property_update")
    if not isinstance(property_update, dict) or "result_graph" not in property_update:
        return None
    if str(property_update.get("status") or "") != "completed":
        raise RuntimeError(
            "Native FEM result_graph requires a completed property update"
        )

    graph = property_update["result_graph"]
    if graph is None:
        return {
            "created_results": [],
            "changed_results": [],
            "result_objects": [],
        }
    if not isinstance(graph, dict):
        raise TypeError("Native FEM result_graph must be an object or null")

    document = solver.Document
    root_record = graph.get("root")
    resource_records = graph.get("resources")
    if not isinstance(root_record, dict) or not isinstance(resource_records, list):
        raise TypeError(
            "Native FEM result_graph requires one root object and a resources list"
        )

    def resolve(record: dict[str, Any], description: str) -> Any:
        if not isinstance(record, dict):
            raise TypeError(f"{description} identity must be an object")
        name = str(record.get("name") or "")
        object_id = record.get("id")
        if not name or isinstance(object_id, bool) or not isinstance(object_id, int):
            raise ValueError(
                f"{description} identity requires an exact name and integer id"
            )
        obj = document.getObject(object_id)
        if (
            obj is None
            or obj.Document is not document
            or int(obj.ID) != object_id
            or str(obj.Name) != name
        ):
            raise RuntimeError(f"{description} changed exact document identity")
        return obj

    root = resolve(root_record, "FEM result root")
    root_lifecycle = str(root_record.get("lifecycle") or "")
    if root_lifecycle not in {"created", "updated"}:
        raise ValueError("FEM result root lifecycle must be 'created' or 'updated'")
    if (
        "VibeCADTimelineRole" not in root.PropertiesList
        or root.getTypeIdOfProperty("VibeCADTimelineRole") != "App::PropertyString"
        or root.VibeCADTimelineRole != "operation"
        or "VibeCADResultSolver" not in root.PropertiesList
        or root.getTypeIdOfProperty("VibeCADResultSolver") != "App::PropertyLinkHidden"
        or root.VibeCADResultSolver is not solver
    ):
        raise RuntimeError("FEM result root lacks exact History and solver provenance")

    timeline = document.getObject("VibeCADTimeline")
    if timeline is None or timeline.TypeId != "App::DocumentTimeline":
        raise RuntimeError("FEM result root has no document History")
    timeline_operations = tuple(timeline.Operations)
    if timeline_operations.count(root) != 1:
        raise RuntimeError("FEM result root is absent from document History")

    def timeline_root(obj: Any) -> Any:
        current = obj
        visited = set()
        while (
            "VibeCADTimelineRole" in current.PropertiesList
            and current.getTypeIdOfProperty("VibeCADTimelineRole")
            == "App::PropertyString"
            and current.VibeCADTimelineRole == "resource"
        ):
            identity = (str(current.Name), int(current.ID))
            if identity in visited:
                raise RuntimeError("FEM result resource ownership is cyclic")
            visited.add(identity)
            if (
                "VibeCADTimelineOwner" not in current.PropertiesList
                or current.getTypeIdOfProperty("VibeCADTimelineOwner")
                != "App::PropertyLinkHidden"
            ):
                raise RuntimeError("FEM result resource ownership is malformed")
            owner = current.VibeCADTimelineOwner
            if (
                owner is None
                or owner.Document is not document
                or document.getObject(int(owner.ID)) is not owner
            ):
                raise RuntimeError("FEM result resource owner changed exact identity")
            current = owner
        return current

    resources = []
    resource_lifecycles = []
    identities = {(str(root.Name), int(root.ID))}
    for index, record in enumerate(resource_records):
        resource = resolve(record, f"FEM result resource {index}")
        identity = (str(resource.Name), int(resource.ID))
        if identity in identities:
            raise ValueError("Native FEM result_graph contains a duplicate identity")
        identities.add(identity)
        resource_lifecycle = str(record.get("lifecycle") or "")
        if resource_lifecycle not in {"created", "updated"}:
            raise ValueError(
                "A returned FEM result resource lifecycle must be "
                "'created' or 'updated'"
            )
        if (
            "VibeCADTimelineRole" not in resource.PropertiesList
            or resource.getTypeIdOfProperty("VibeCADTimelineRole")
            != "App::PropertyString"
            or resource.VibeCADTimelineRole != "resource"
            or "VibeCADTimelineOwner" not in resource.PropertiesList
            or resource.getTypeIdOfProperty("VibeCADTimelineOwner")
            != "App::PropertyLinkHidden"
            or resource not in timeline_operations
            or timeline_root(resource) is not root
        ):
            raise RuntimeError("FEM result resource lacks exact History ownership")
        resources.append(resource)
        resource_lifecycles.append(resource_lifecycle)

    if root_lifecycle == "created" and "updated" in resource_lifecycles:
        raise ValueError(
            "A newly created FEM result root cannot own an updated "
            "pre-existing resource"
        )

    owned_resources = [
        operation
        for operation in timeline_operations
        if operation is not root
        and "VibeCADTimelineRole" in operation.PropertiesList
        and operation.getTypeIdOfProperty("VibeCADTimelineRole")
        == "App::PropertyString"
        and operation.VibeCADTimelineRole == "resource"
        and timeline_root(operation) is root
    ]
    if owned_resources != resources:
        raise RuntimeError(
            "Native FEM result_graph does not exactly match the "
            "owned History resource block"
        )
    root_index = timeline_operations.index(root)
    if root_index < len(resources) or tuple(
        timeline_operations[root_index - len(resources) : root_index]
    ) != tuple(resources):
        raise RuntimeError(
            "Native FEM result resources are not the canonical "
            "resource-first History block"
        )

    created_objects = []
    changed_objects = []
    if root_lifecycle == "created":
        created_objects.append(root)
    else:
        changed_objects.append(root)
    for resource, lifecycle in zip(
        resources,
        resource_lifecycles,
        strict=True,
    ):
        result_group = (
            created_objects
            if lifecycle == "created"
            else changed_objects
        )
        result_group.append(resource)
    return {
        "created_results": [str(obj.Name) for obj in created_objects],
        "changed_results": [str(obj.Name) for obj in changed_objects],
        "result_objects": created_objects + changed_objects,
    }


def _structured_prerequisites(analysis: Any, solver: Any) -> dict[str, Any]:
    from femtools import membertools
    from femtools.checksanalysis import check_member_for_solver_calculix

    members = list(getattr(analysis, "Group", []) or [])
    analysis_type = str(getattr(solver, "AnalysisType", "") or "")
    meshes = [member for member in members if hasattr(member, "FemMesh")]
    materials = [member for member in members if isinstance(getattr(member, "Material", None), dict)]
    fixed = [member for member in members if _proxy_type(member) == "Fem::ConstraintFixed"]
    loads = [
        member
        for member in members
        if _proxy_type(member)
        in {"Fem::ConstraintForce", "Fem::ConstraintPressure", "Fem::ConstraintSelfWeight"}
    ]
    temperatures = [
        member for member in members if _proxy_type(member) == "Fem::ConstraintTemperature"
    ]
    missing: list[dict[str, Any]] = []
    if solver not in members:
        missing.append({"kind": "solver_membership", "solver": solver.Name})
    if len(meshes) != 1:
        missing.append(
            {"kind": "mesh_count", "required": 1, "actual": len(meshes)}
        )
    elif int(getattr(meshes[0].FemMesh, "NodeCount", 0) or 0) == 0:
        missing.append({"kind": "mesh_nodes", "mesh": meshes[0].Name})
    elif int(getattr(meshes[0].FemMesh, "VolumeCount", 0) or 0) == 0:
        missing.append({"kind": "mesh_volume_elements", "mesh": meshes[0].Name})
    elif str(
        getattr(meshes[0], reference_contracts.PROP_DERIVED_STATE, "") or ""
    ) == "stale":
        missing.append(
            {
                "kind": "stale_mesh",
                "mesh": meshes[0].Name,
                "reason": str(
                    getattr(
                        meshes[0], reference_contracts.PROP_STALE_REASON, ""
                    )
                    or "The source model changed."
                ),
                "required_action": "Regenerate the FEM mesh before solving.",
            }
        )
    if not materials:
        missing.append({"kind": "material"})
    if analysis_type in {"static", "frequency", "thermomech", "buckling"} and not fixed:
        missing.append({"kind": "fixed_support"})
    if analysis_type in {"static", "buckling"} and not loads:
        missing.append({"kind": "mechanical_load"})
    if analysis_type == "thermomech" and not temperatures:
        missing.append({"kind": "temperature_constraint"})
    try:
        native_message = str(
            check_member_for_solver_calculix(
                analysis,
                solver,
                meshes[0] if len(meshes) == 1 else None,
                membertools.AnalysisMember(analysis),
            )
            or ""
        )
    except Exception as exc:
        native_message = ""
        missing.append(
            {"kind": "native_prerequisite_check_error", "native_error": str(exc)}
        )
    return {
        "ready": not missing and not native_message,
        "analysis_type": analysis_type,
        "missing": missing,
        "native_check": {
            "status": "passed" if not native_message else "failed",
            "message": native_message,
        },
        "members": {
            "solver": solver.Name,
            "meshes": [item.Name for item in meshes],
            "materials": [item.Name for item in materials],
            "fixed_supports": [item.Name for item in fixed],
            "loads": [item.Name for item in loads],
            "temperatures": [item.Name for item in temperatures],
        },
    }


def _calculix_preflight() -> dict[str, Any]:
    from femsolver import settings

    configured = settings.get_binary("Calculix", silent=True)
    if not configured:
        return {"ok": False, "configured_path": configured, "resolved_path": None}
    resolved = shutil.which(configured)
    if resolved is None and os.path.isfile(configured) and os.access(configured, os.X_OK):
        resolved = os.path.abspath(configured)
    return {
        "ok": bool(resolved),
        "configured_path": configured,
        "resolved_path": resolved,
    }


def _find_solver(analysis: Any) -> Any:
    solvers = [
        member
        for member in list(getattr(analysis, "Group", []) or [])
        if "Solver" in str(getattr(member, "TypeId", ""))
    ]
    return solvers[0] if len(solvers) == 1 else None


def _proxy_type(obj: Any) -> str:
    return str(getattr(getattr(obj, "Proxy", None), "Type", "") or "")


def _add_operation_properties(solver: Any, operation_id: str, analysis_name: str, state: str) -> None:
    definitions = (
        ("App::PropertyString", "VibeCADOperationId"),
        ("App::PropertyString", "VibeCADOperationKind"),
        ("App::PropertyString", "VibeCADOperationAnalysis"),
        ("App::PropertyString", "VibeCADOperationState"),
        ("App::PropertyBool", "VibeCADOperationFinalized"),
    )
    for property_type, name in definitions:
        if not hasattr(solver, name):
            solver.addProperty(property_type, name, "VibeCAD Operation")
    solver.VibeCADOperationId = operation_id
    solver.VibeCADOperationKind = "calculix"
    solver.VibeCADOperationAnalysis = analysis_name
    solver.VibeCADOperationState = state
    solver.VibeCADOperationFinalized = False


def _operation(service: Any, operation_id: str) -> tuple[Any, Any, Any] | dict[str, Any]:
    if not operation_id:
        return _invalid("operation_id is required.")
    doc = service._active_document()
    if doc is None:
        return _invalid("No active document.")
    matches = [
        obj
        for obj in doc.Objects
        if str(getattr(obj, "VibeCADOperationKind", "")) == "calculix"
        and str(getattr(obj, "VibeCADOperationId", "")) == operation_id
    ]
    if len(matches) != 1:
        return _invalid(
            "No unique CalculiX operation matches operation_id.",
            operation_id=operation_id,
            match_count=len(matches),
        )
    solver = matches[0]
    analysis = doc.getObject(str(solver.VibeCADOperationAnalysis))
    tool = getattr(solver, "Tool", None)
    if analysis is None or tool is None or not hasattr(tool, "process_diagnostics"):
        return _invalid(
            "The solve runtime is no longer available; retained solver state "
            "remains inspectable but this process cannot be polled.",
            solver=solver.Name,
            persisted_state=str(solver.VibeCADOperationState),
        )
    return solver, tool, analysis


def _persist_state(solver: Any, state: str) -> None:
    def persist() -> dict[str, Any]:
        solver.VibeCADOperationState = str(state)
        solver.Document.recompute()
        return {"solver": solver.Name, "state": str(solver.VibeCADOperationState)}

    run_freecad_transaction(f"Persist CalculiX state: {state}", persist)


def _result_objects(analysis: Any, solver: Any) -> list[Any]:
    objects: dict[str, Any] = {}
    for obj in list(getattr(analysis, "Group", []) or []) + list(
        getattr(solver, "Results", []) or []
    ):
        if obj is not None and getattr(obj, "Name", None):
            if obj is solver or hasattr(obj, "FemMesh"):
                continue
            if (
                obj.isDerivedFrom("Fem::FemPostPipeline")
                or obj.isDerivedFrom("Fem::FemResultObject")
                or obj.isDerivedFrom("App::TextDocument")
            ):
                objects[obj.Name] = obj
    return [objects[name] for name in sorted(objects)]


def _result_snapshot(analysis: Any, solver: Any) -> dict[str, str]:
    return {obj.Name: _object_fingerprint(obj) for obj in _result_objects(analysis, solver)}


def _object_fingerprint(obj: Any) -> str:
    try:
        content = str(obj.Content)
    except Exception:
        content = json.dumps(
            {
                name: str(getattr(obj, name, ""))
                for name in sorted(list(getattr(obj, "PropertiesList", []) or []))
            },
            sort_keys=True,
        )
    try:
        semantic = json.dumps(_result_summary(obj), sort_keys=True, default=str)
    except Exception as exc:
        semantic = json.dumps({"summary_error": str(exc)}, sort_keys=True)
    return hashlib.sha256(
        (content + "\n" + semantic).encode("utf-8", errors="replace")
    ).hexdigest()


def _result_summary(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {"status": "error", "error": "result object no longer exists"}
    summary: dict[str, Any] = {
        "status": "ok",
        "object": obj.Name,
        "label": obj.Label,
        "type": obj.TypeId,
    }
    if obj.isDerivedFrom("App::TextDocument"):
        text = str(getattr(obj, "Text", "") or "")
        summary["text_output"] = {
            "character_count": len(text),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
        return summary
    if obj.isDerivedFrom("Fem::FemResultObject"):
        for property_name in ("vonMises", "DisplacementLengths", "Temperature"):
            values = [float(value) for value in list(getattr(obj, property_name, []) or [])]
            if values:
                summary[property_name] = _numeric_summary(values)
        return summary
    if obj.isDerivedFrom("Fem::FemPostPipeline"):
        try:
            summary["fields"] = _pipeline_fields(obj.Data)
        except Exception as exc:
            summary["status"] = "error"
            summary["native_error"] = str(exc)
        return summary
    return summary


def _pipeline_fields(data: Any) -> dict[str, Any]:
    from vtkmodules.util.numpy_support import vtk_to_numpy

    arrays: dict[str, list[Any]] = {}
    for dataset in _vtk_datasets(data):
        for association, field_data in (
            ("point", dataset.GetPointData()),
            ("cell", dataset.GetCellData()),
        ):
            if field_data is None:
                continue
            for index in range(field_data.GetNumberOfArrays()):
                array = field_data.GetArray(index)
                if array is None or not array.GetName():
                    continue
                arrays.setdefault(f"{association}:{array.GetName()}", []).append(
                    vtk_to_numpy(array)
                )
    result: dict[str, Any] = {}
    for name, chunks in arrays.items():
        import numpy as np

        values = np.concatenate(chunks, axis=0)
        finite = values[np.isfinite(values)]
        record: dict[str, Any] = {
            "tuple_count": int(values.shape[0]) if values.ndim else int(values.size),
            "component_count": int(values.shape[1]) if values.ndim > 1 else 1,
        }
        if finite.size:
            record.update(
                {
                    "minimum": float(finite.min()),
                    "maximum": float(finite.max()),
                }
            )
        if values.ndim > 1 and values.shape[1] in {2, 3}:
            lengths = np.linalg.norm(values, axis=1)
            finite_lengths = lengths[np.isfinite(lengths)]
            if finite_lengths.size:
                record["vector_magnitude"] = {
                    "minimum": float(finite_lengths.min()),
                    "maximum": float(finite_lengths.max()),
                }
        result[name] = record
    return result


def _vtk_datasets(data: Any):
    if data is None:
        return
    if hasattr(data, "GetNumberOfBlocks"):
        for index in range(data.GetNumberOfBlocks()):
            block = data.GetBlock(index)
            if block is not None:
                yield from _vtk_datasets(block)
    elif hasattr(data, "GetPointData"):
        yield data


def _numeric_summary(values: list[float]) -> dict[str, Any]:
    finite = [value for value in values if math.isfinite(value)]
    return {
        "count": len(values),
        "finite_count": len(finite),
        "minimum": min(finite) if finite else None,
        "maximum": max(finite) if finite else None,
    }


def _result_completeness(
    analysis_type: str,
    created: list[str],
    changed: list[str],
    summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    if analysis_type == "check":
        return {"complete": True, "required_fields": [], "found_fields": []}
    fields = {
        field_name.lower()
        for summary in summaries
        for field_name in (summary.get("fields") or {}).keys()
    }
    required_tokens = {
        "static": ["displacement", "stress"],
        "frequency": ["displacement"],
        "buckling": ["displacement"],
        "thermomech": ["temperature"],
    }.get(analysis_type, [])
    missing = [
        token for token in required_tokens if not any(token in field for field in fields)
    ]
    summary_errors = [summary for summary in summaries if summary.get("status") != "ok"]
    return {
        "complete": bool(created or changed) and not missing and not summary_errors,
        "required_fields": required_tokens,
        "found_fields": sorted(fields),
        "missing_fields": missing,
        "summary_errors": summary_errors,
    }


def _invalid(
    message: str,
    *,
    failure_code: str = "FEM_SOLVE_OPERATION_REJECTED",
    failure_stage: str = "precondition",
    requested: Any = None,
    observed: Any = None,
    candidates: Any = None,
    allowed_values: Any = None,
    required_changes: list[Any] | None = None,
    **details: Any,
) -> dict[str, Any]:
    return tool_failure(
        TOOL_SPEC["name"],
        failure_code,
        failure_stage,
        message,
        requested=requested,
        observed=observed,
        candidates=candidates,
        allowed_values=allowed_values,
        required_changes=required_changes,
        **details,
    )
