# SPDX-License-Identifier: LGPL-2.1-or-later

"""Concise live state for the Manufacture ribbon."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureAreaState import area_snapshot
from VibeCADNativeManufactureJobState import capture_job_creation_environment
from VibeCADNativeManufactureFollowUpState import (
    is_simulation_result,
    setup_relationship_state,
    simulation_result_state,
)
from VibeCADNativeManufacturePropertyBag import property_bag_snapshot
from VibeCADNativeManufactureReadiness import (
    build_active_job_summary,
    resolve_active_job,
)
from VibeCADNativeManufactureToolState import capture_tool_catalog
from VibeCADNativeManufactureState import (
    candidate_model_state,
    is_job,
    job_state,
)
from VibeCADNativeRobotState import (
    NativeRobotStateError,
    capture_robot_setup_state,
)
from VibeCADNativeRobotToolState import (
    NativeRobotToolStateError,
    capture_robot_tool_shape_inventory,
)
from VibeCADNativeRobotTrajectoryState import (
    NativeRobotTrajectoryStateError,
    capture_robot_trajectory_state,
)
import VibeCADScriptedPublication as ScriptedPublication


MAX_JOBS = 12
MAX_MODEL_CANDIDATES = 24
MAX_SNAPSHOT_JOB_ITEMS = 8
MAX_REMAINING_STOCK_RESULTS = 24
_NON_MODEL_SHAPE_TYPES = frozenset({"App::Line", "App::Plane", "App::Point"})


def _partdesign_publication_identity(obj: Any, role: str) -> tuple[str, str] | None:
    if (
        str(getattr(obj, "VibeCADScriptedRole", "") or "") != role
        or str(getattr(obj, "VibeCADScriptedEngine", "") or "")
        != "vibescript:partdesign"
    ):
        return None
    model_id = str(getattr(obj, "VibeCADScriptedModelId", "") or "").strip()
    output_key = str(getattr(obj, "VibeCADScriptedOutputKey", "") or "").strip()
    return (model_id, output_key) if model_id and output_key else None


def _shadowed_partdesign_publication_ids(objects: tuple[Any, ...]) -> set[int]:
    """Hide compatibility links when their native Part Design Body is present."""

    body_identities = {
        identity
        for obj in objects
        if str(getattr(obj, "TypeId", "") or "") == "PartDesign::Body"
        if (
            identity := _partdesign_publication_identity(
                obj,
                ScriptedPublication.ROLE_IMPLEMENTATION,
            )
        )
        is not None
    }
    return {
        id(obj)
        for obj in objects
        if str(getattr(obj, "TypeId", "") or "") == "App::Link"
        if _partdesign_publication_identity(
            obj,
            ScriptedPublication.ROLE_PUBLICATION,
        )
        in body_identities
    }


def _active_simulation_state(document: Any) -> dict[str, str] | None:
    """Read the exact Native-owned CAM task, when this document owns it."""

    try:
        from Path.Main.Gui import SimulatorGL
    except ImportError:
        return None
    if not SimulatorGL.owns_active_prepared_simulation(document):
        return None
    simulation = SimulatorGL.active_prepared_simulation()
    simulation_id = str(getattr(simulation, "nativeSimulationId", "") or "")
    job = getattr(simulation, "job", None)
    if (
        simulation is None
        or len(simulation_id) != 32
        or any(character not in "0123456789abcdef" for character in simulation_id)
        or job is None
        or getattr(job, "Document", None) is not document
        or not str(getattr(job, "Name", "") or "")
    ):
        raise NativeManufactureError(
            "The active Native CAM simulation has invalid task identity.",
            error_code="NATIVE_MANUFACTURE_STATE_INVALID",
        )
    return {
        "mode": "gl",
        "simulation_id": simulation_id,
        "job": str(job.Name),
    }


def _contained_resources(job: Any) -> set[int]:
    """Return CAM-owned resources without following clones back to public models."""

    pending = [
        job,
        getattr(job, "Model", None),
        getattr(job, "Tools", None),
        getattr(job, "Operations", None),
        getattr(job, "SetupSheet", None),
        getattr(job, "Stock", None),
    ]
    result: set[int] = set()
    while pending:
        obj = pending.pop()
        if obj is None or id(obj) in result:
            continue
        result.add(id(obj))
        pending.extend(tuple(getattr(obj, "Group", ()) or ()))
        for property_name in ("Tool", "BitBody", "Origin"):
            child = getattr(obj, property_name, None)
            if child is not None:
                pending.append(child)
        origin = getattr(obj, "Origin", None)
        if origin is not None:
            pending.extend(tuple(getattr(origin, "OriginFeatures", ()) or ()))
    return result


def _focused_setup_state(
    exact_state: Mapping[str, Any],
    workflow: Mapping[str, Any],
) -> dict[str, Any]:
    """Add targeting facts omitted by the richer workflow presentation."""

    result = dict(workflow)
    for name in (
        "counts",
        "models",
        "models_truncated",
        "postprocessor",
        "configuration",
    ):
        if name in exact_state:
            result[name] = exact_state[name]
    return result


def _background_job_states(
    document: Any,
    background_jobs: tuple[Any, ...],
) -> list[dict[str, Any]]:
    document_uid = str(getattr(document, "Uid", "") or "")
    result = []
    for job in background_jobs:
        if str(getattr(job, "document_uid", "") or "") != document_uid:
            raise NativeManufactureError(
                "Manufacture background status belongs to another document.",
                error_code="NATIVE_MANUFACTURE_STATE_INVALID",
            )
        result.append(
            {
                "job_id": str(job.job_id),
                "capability": str(job.capability_name),
                "resource_scope": str(job.resource_scope),
                "phase": str(job.phase),
                "progress_percent": int(job.progress_percent),
                "progress_message": str(job.progress_message)[:160],
                "terminal": bool(job.terminal),
                "cancel_requested": bool(job.cancel_requested),
            }
        )
    return result


def build_manufacture_snapshot(
    document: Any,
    *,
    selection: Mapping[str, Any] | None = None,
    background_jobs: tuple[Any, ...] = (),
) -> dict[str, Any]:
    if not isinstance(background_jobs, tuple):
        raise TypeError("background_jobs must be a tuple")
    objects = list(getattr(document, "Objects", []) or [])
    shadowed_publication_ids = _shadowed_partdesign_publication_ids(tuple(objects))
    job_objects = [obj for obj in objects if is_job(obj)]
    retained_results = [obj for obj in objects if is_simulation_result(obj)]
    resource_ids: set[int] = set()
    for job in job_objects:
        resource_ids.update(_contained_resources(job))
    job_states = {
        id(obj): job_state(
            obj,
            operation_limit=MAX_SNAPSHOT_JOB_ITEMS,
            tool_limit=MAX_SNAPSHOT_JOB_ITEMS,
            model_limit=MAX_SNAPSHOT_JOB_ITEMS,
        )
        for obj in job_objects[:MAX_JOBS]
    }
    for job in job_objects[:MAX_JOBS]:
        relationship = setup_relationship_state(job)
        if relationship is not None:
            job_states[id(job)]["relationship"] = relationship
    job_workflows = {
        id(obj): build_active_job_summary(
            document,
            obj,
            job_states[id(obj)],
        )
        for obj in job_objects[:MAX_JOBS]
    }
    for object_id, workflow in job_workflows.items():
        job_states[object_id].update(
            readiness=dict(workflow["readiness"]),
            toolpath_validity=dict(workflow["toolpath_validity"]),
        )
    jobs = [job_states[id(obj)] for obj in job_objects[:MAX_JOBS]]
    active_job, active_job_resolution = resolve_active_job(
        document,
        tuple(job_objects),
        selection,
    )
    if active_job is not None and id(active_job) not in job_states:
        job_states[id(active_job)] = job_state(
            active_job,
            operation_limit=MAX_SNAPSHOT_JOB_ITEMS,
            tool_limit=MAX_SNAPSHOT_JOB_ITEMS,
            model_limit=MAX_SNAPSHOT_JOB_ITEMS,
        )
        relationship = setup_relationship_state(active_job)
        if relationship is not None:
            job_states[id(active_job)]["relationship"] = relationship
        job_workflows[id(active_job)] = build_active_job_summary(
            document,
            active_job,
            job_states[id(active_job)],
        )
    candidates = []
    for obj in objects:
        if (
            id(obj) in resource_ids
            or id(obj) in shadowed_publication_ids
            or str(getattr(obj, "TypeId", "")) in _NON_MODEL_SHAPE_TYPES
        ):
            continue
        try:
            state = candidate_model_state(obj)
            view = getattr(obj, "ViewObject", None)
            state["job_create_replaces_in_history"] = bool(
                getattr(view, "Visibility", False)
            )
            candidates.append(state)
        except NativeManufactureError:
            continue
    result = {
        "kind": "manufacture",
        "job_count": len(job_objects),
        "jobs": jobs,
        "jobs_truncated": len(job_objects) > MAX_JOBS,
        "active_job_resolution": active_job_resolution,
        "active_job": (
            _focused_setup_state(
                job_states[id(active_job)],
                job_workflows[id(active_job)],
            )
            if active_job is not None
            else None
        ),
        "model_candidate_count": len(candidates),
        "model_candidates": candidates[:MAX_MODEL_CANDIDATES],
        "model_candidates_truncated": len(candidates) > MAX_MODEL_CANDIDATES,
        "job_creation": capture_job_creation_environment().summary(),
        "remaining_stock_result_count": len(retained_results),
        "remaining_stock_results": [
            simulation_result_state(result)
            for result in retained_results[:MAX_REMAINING_STOCK_RESULTS]
        ],
        "remaining_stock_results_truncated": len(retained_results)
        > MAX_REMAINING_STOCK_RESULTS,
        "background_jobs": _background_job_states(document, background_jobs),
    }
    active_simulation = _active_simulation_state(document)
    if active_simulation is not None:
        result["active_simulation"] = active_simulation
    result.update(property_bag_snapshot(document))
    result.update(area_snapshot(document))
    tool_catalog = capture_tool_catalog()
    result["tool_catalog"] = tool_catalog.page(0, 8)
    try:
        result["robot_setup"] = capture_robot_setup_state(document).summary()
    except NativeRobotStateError as exc:
        result["robot_setup"] = {
            "available": False,
            "reason": str(exc)[:256],
        }
    try:
        result["robot_tool_shapes"] = capture_robot_tool_shape_inventory(
            document
        ).summary()
    except NativeRobotToolStateError as exc:
        result["robot_tool_shapes"] = {
            "available": False,
            "reason": str(exc)[:256],
        }
    try:
        result["robot_trajectories"] = capture_robot_trajectory_state(
            document
        ).summary()
    except NativeRobotTrajectoryStateError as exc:
        result["robot_trajectories"] = {
            "available": False,
            "reason": str(exc)[:256],
        }
    return result
