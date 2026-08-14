# SPDX-License-Identifier: LGPL-2.1-or-later

"""Concise live state for the Manufacture ribbon."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureAreaState import area_snapshot
from VibeCADNativeManufactureJobState import capture_job_creation_environment
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
MAX_JOBS = 12
MAX_MODEL_CANDIDATES = 24
MAX_SNAPSHOT_JOB_ITEMS = 8
_NON_MODEL_SHAPE_TYPES = frozenset({"App::Line", "App::Plane", "App::Point"})


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


def build_manufacture_snapshot(
    document: Any,
    *,
    selection: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    objects = list(getattr(document, "Objects", []) or [])
    job_objects = [obj for obj in objects if is_job(obj)]
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
    candidates = []
    for obj in objects:
        if (
            id(obj) in resource_ids
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
            build_active_job_summary(
                document,
                active_job,
                job_states[id(active_job)],
            )
            if active_job is not None
            else None
        ),
        "model_candidate_count": len(candidates),
        "model_candidates": candidates[:MAX_MODEL_CANDIDATES],
        "model_candidates_truncated": len(candidates) > MAX_MODEL_CANDIDATES,
        "job_creation": capture_job_creation_environment().summary(),
    }
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
