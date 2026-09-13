# SPDX-License-Identifier: LGPL-2.1-or-later

"""Concise active-Job state and workflow readiness for Manufacture turns."""

from __future__ import annotations

import math
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufacturePostConfiguration import resolve_post_configuration
from VibeCADNativeManufactureState import (
    operation_active_state,
    operation_reference_state,
    tool_controller_state,
)
from VibeCADNativeTargets import read_current_selection


MAX_ACTIVE_JOB_OPERATIONS = 64
MAX_ACTIVE_JOB_TOOLS = 32
MAX_READINESS_ISSUES = 24
MAX_WORKFLOW_COMMANDS = 1_000_000


def _owned_resource_ids(job: Any) -> set[int]:
    model_group = tuple(getattr(getattr(job, "Model", None), "Group", ()) or ())
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
    base_object = getattr(getattr(job, "Proxy", None), "baseObject", None)
    if callable(base_object):
        for resource in model_group:
            try:
                public = base_object(job, resource)
            except Exception:
                continue
            if public is not None:
                result.add(id(public))
    return result


def _selected_objects(document: Any, selection: Mapping[str, Any]) -> tuple[Any, ...]:
    result = []
    for item in tuple(selection.get("items", ()) or ()):
        reference = item.get("object") if isinstance(item, Mapping) else None
        name = (
            str(reference.get("object_name") or "")
            if isinstance(reference, Mapping)
            else ""
        )
        obj = document.getObject(name) if name else None
        if obj is not None and getattr(obj, "Document", None) is document:
            result.append(obj)
    return tuple(result)


def resolve_active_job(
    document: Any,
    jobs: tuple[Any, ...],
    selection: Mapping[str, Any] | None,
) -> tuple[Any | None, str]:
    """Resolve only a human-selected Job or the sole unambiguous current Job."""

    if not jobs:
        return None, "no_job"
    if selection is not None:
        selected = dict(selection)
    else:
        try:
            selected = read_current_selection(document)
        except (AttributeError, ImportError, ReferenceError, RuntimeError):
            selected = {"items": []}
    selected_objects = _selected_objects(document, selected)
    if selected_objects:
        owners = []
        for job in jobs:
            resource_ids = _owned_resource_ids(job)
            if any(id(obj) in resource_ids for obj in selected_objects):
                owners.append(job)
        if len(owners) == 1:
            return owners[0], "selection"
        if len(owners) > 1:
            return None, "ambiguous_selection"
    if len(jobs) == 1:
        return jobs[0], "only_job"
    return None, "choose_job"


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return round(result, 9) if math.isfinite(result) else None


def _valid_shape(shape: Any, *, require_solid: bool = False) -> bool:
    try:
        valid = bool(
            shape is not None
            and callable(getattr(shape, "isNull", None))
            and not shape.isNull()
            and shape.isValid()
        )
        return valid and (
            not require_solid or bool(tuple(getattr(shape, "Solids", ()) or ()))
        )
    except Exception:
        return False


def _usable(document: Any, obj: Any) -> bool:
    reader = getattr(document, "isObjectUsableAtCurrentTimelinePosition", None)
    try:
        return bool(callable(reader) and reader(obj))
    except Exception:
        return False


def _valid_object(obj: Any) -> bool:
    try:
        return bool(obj.isValid())
    except Exception:
        return False


def _command_count(operation: Any) -> int:
    try:
        return max(0, int(getattr(getattr(operation, "Path", None), "Size", 0) or 0))
    except Exception:
        return 0


def _compact_tool(state: Mapping[str, Any], position: int) -> dict[str, Any]:
    tool = state.get("tool")
    compact_tool = None
    if isinstance(tool, Mapping):
        compact_tool = {
            key: tool[key]
            for key in (
                "object_name",
                "label",
                "state_sha256",
                "shape_type",
            )
            if key in tool
        }
    result = {
        "position": position,
        **{
            key: state[key]
            for key in (
                "object_name",
                "label",
                "state_sha256",
                "tool_number",
                "tool_length_offset",
                "spindle_speed_rpm",
                "spindle_direction",
            )
            if key in state
        },
    }
    if compact_tool is not None:
        result["tool"] = compact_tool
    return result


def _operation_record(
    document: Any,
    operation: Any,
    position: int,
    state: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    failure = None
    current = dict(state) if isinstance(state, Mapping) else None
    if current is None:
        try:
            current = operation_reference_state(operation)
        except NativeManufactureError as exc:
            failure = {
                "code": exc.error_code,
                "message": str(exc)[:256],
                "object_name": str(getattr(operation, "Name", "") or ""),
            }
            current = {
                "object_name": str(getattr(operation, "Name", "") or ""),
                "label": str(getattr(operation, "Label", "") or "")[:160],
                "type_id": str(getattr(operation, "TypeId", "") or ""),
            }
    active = operation_active_state(operation)
    path = getattr(operation, "Path", None)
    command_count = _command_count(operation)
    object_valid = _valid_object(operation)
    history_usable = _usable(document, operation)
    toolpath_valid = bool(
        failure is None
        and object_valid
        and history_usable
        and path is not None
        and command_count > 0
    )
    result = {
        "position": position,
        **{
            key: current[key]
            for key in (
                "object_name",
                "label",
                "type_id",
                "state_sha256",
                "tool_controller",
            )
            if key in current
        },
        "active": active,
        "command_count": command_count,
        "toolpath_valid": toolpath_valid,
    }
    if not toolpath_valid:
        result["toolpath_issue"] = (
            failure["code"]
            if failure is not None
            else (
                "OBJECT_INVALID"
                if not object_valid
                else "HISTORY_UNAVAILABLE"
                if not history_usable
                else "TOOLPATH_EMPTY"
            )
        )
    return result, failure


def _tool_issue(job: Any, operation: Any) -> dict[str, Any] | None:
    try:
        import Path.Dressup.Utils as PathDressup

        controller = PathDressup.toolController(operation)
        controllers = tuple(getattr(getattr(job, "Tools", None), "Group", ()) or ())
        if controller not in controllers:
            raise ValueError("controller is not owned by the Job")
        tool = getattr(controller, "Tool", None)
        shape = getattr(tool, "Shape", None)
        tool_number = int(getattr(controller, "ToolNumber", 0) or 0)
        diameter = _finite(getattr(getattr(tool, "Diameter", None), "Value", None))
        if (
            tool is None
            or not _valid_object(controller)
            or not _valid_object(tool)
            or not _valid_shape(shape)
            or tool_number < 1
            or diameter is None
            or diameter <= 0.0
        ):
            raise ValueError("controller or ToolBit is invalid")
        return None
    except Exception:
        return {
            "code": "SIMULATION_TOOL_INVALID",
            "message": "The active operation has no usable Job-owned ToolBit.",
            "object_name": str(getattr(operation, "Name", "") or ""),
        }


def _post_readiness(job: Any, common_issues: list[dict[str, Any]]) -> dict[str, Any]:
    issues = list(common_issues)
    postprocessor = None
    machine_configured = False
    try:
        configuration = resolve_post_configuration(job)
        postprocessor = configuration.postprocessor_name
        machine_configured = configuration.use_machine_flow
        from Path.Post.Processor import PostProcessorFactory

        source = PostProcessorFactory.resolve_post_processor_path(postprocessor)
        if not source:
            issues.append(
                {
                    "code": "POSTPROCESSOR_UNAVAILABLE",
                    "message": "The configured postprocessor is unavailable.",
                }
            )
        elif not PostProcessorFactory.is_modern_post_processor(
            source,
            postprocessor,
        ):
            issues.append(
                {
                    "code": "POSTPROCESSOR_UNSUPPORTED",
                    "message": "The configured postprocessor is not a modern class-based processor.",
                }
            )
    except NativeManufactureError as exc:
        issues.append({"code": exc.error_code, "message": str(exc)[:256]})
    return {
        "ready": not issues,
        "postprocessor": postprocessor,
        "machine_configured": machine_configured,
        "issues": issues[:MAX_READINESS_ISSUES],
        "issues_truncated": len(issues) > MAX_READINESS_ISSUES,
        "exact_preflight_required": True,
    }


def build_active_job_summary(
    document: Any,
    job: Any,
    job_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Return bounded active-Job facts without running simulation or posting."""

    operations = tuple(getattr(getattr(job, "Operations", None), "Group", ()) or ())
    tools = tuple(getattr(getattr(job, "Tools", None), "Group", ()) or ())
    published_operation_states = {
        str(value.get("object_name") or ""): value
        for value in tuple(job_snapshot.get("operations", ()) or ())
        if isinstance(value, Mapping)
    }
    published_tool_states = {
        str(value.get("object_name") or ""): value
        for value in tuple(job_snapshot.get("tools", ()) or ())
        if isinstance(value, Mapping)
    }

    operation_records = []
    operation_failures = {}
    for position, operation in enumerate(operations[:MAX_ACTIVE_JOB_OPERATIONS]):
        record, failure = _operation_record(
            document,
            operation,
            position,
            published_operation_states.get(str(operation.Name)),
        )
        operation_records.append(record)
        if failure is not None:
            operation_failures[str(getattr(operation, "Name", "") or "")] = failure

    tool_records = []
    for position, controller in enumerate(tools[:MAX_ACTIVE_JOB_TOOLS]):
        state = published_tool_states.get(str(controller.Name))
        if state is None:
            try:
                state = tool_controller_state(controller)
            except NativeManufactureError as exc:
                tool_records.append(
                    {
                        "position": position,
                        "object_name": str(controller.Name),
                        "label": str(getattr(controller, "Label", "") or "")[:160],
                        "readable": False,
                        "issue": exc.error_code,
                    }
                )
                continue
        compact = _compact_tool(state, position)
        compact["readable"] = True
        tool_records.append(compact)

    active_records = [value for value in operation_records if value["active"]]
    active_operation_count = sum(
        1 for operation in operations if operation_active_state(operation)
    )
    active_commands = sum(
        _command_count(operation)
        for operation in operations
        if operation_active_state(operation)
    )
    common_issues = []
    relationship = job_snapshot.get("relationship")
    if isinstance(relationship, Mapping) and relationship.get("current") is not True:
        common_issues.append(
            {
                "code": "REMAINING_STOCK_STALE",
                "message": "The retained stock no longer matches its previous setup.",
            }
        )
    if not active_operation_count:
        common_issues.append(
            {
                "code": "NO_ACTIVE_OPERATIONS",
                "message": "The active Job has no active operation.",
            }
        )
    if len(operations) > MAX_ACTIVE_JOB_OPERATIONS:
        common_issues.append(
            {
                "code": "OPERATION_LIMIT_EXCEEDED",
                "message": "The active Job exceeds the 64-operation workflow bound.",
            }
        )
    for record in active_records:
        if not record["toolpath_valid"]:
            failure = operation_failures.get(str(record.get("object_name") or ""))
            common_issues.append(
                failure
                or {
                    "code": str(record.get("toolpath_issue") or "TOOLPATH_INVALID"),
                    "message": "An active operation has no valid current toolpath.",
                    "object_name": record.get("object_name"),
                }
            )
    if active_commands > MAX_WORKFLOW_COMMANDS:
        common_issues.append(
            {
                "code": "COMMAND_LIMIT_EXCEEDED",
                "message": "Active toolpaths exceed the one-million-command workflow bound.",
            }
        )

    stock = getattr(job, "Stock", None)
    stock_shape = getattr(stock, "Shape", None)
    stock_ready = bool(
        stock is not None
        and _usable(document, stock)
        and _valid_shape(stock_shape, require_solid=True)
    )
    simulation_issues = list(common_issues)
    if not stock_ready:
        simulation_issues.append(
            {
                "code": "STOCK_INVALID",
                "message": "The active Job has no valid solid stock shape.",
            }
        )
    for model in tuple(getattr(getattr(job, "Model", None), "OutList", ()) or ()):
        if not _valid_shape(getattr(model, "Shape", None)):
            simulation_issues.append(
                {
                    "code": "MODEL_SHAPE_INVALID",
                    "message": "A Job model resource has no valid simulation shape.",
                    "object_name": str(getattr(model, "Name", "") or ""),
                }
            )
    for operation, record in zip(
        operations[:MAX_ACTIVE_JOB_OPERATIONS],
        operation_records,
        strict=True,
    ):
        if record["active"] and record["toolpath_valid"]:
            issue = _tool_issue(job, operation)
            if issue is not None:
                simulation_issues.append(issue)

    stock_summary = dict(job_snapshot.get("stock") or {})
    stock_summary.update(
        present=stock is not None,
        valid_solid=stock_ready,
        solid_count=len(tuple(getattr(stock_shape, "Solids", ()) or ()))
        if stock_shape is not None
        else 0,
    )
    return {
        **{
            key: job_snapshot[key]
            for key in ("object_name", "label", "type_id", "state_sha256")
            if key in job_snapshot
        },
        "stock": stock_summary,
        "machine": dict(job_snapshot.get("machine") or {}),
        **(
            {"relationship": dict(relationship)}
            if isinstance(relationship, Mapping)
            else {}
        ),
        "tools": tool_records,
        "tools_truncated": len(tools) > MAX_ACTIVE_JOB_TOOLS,
        "ordered_operations": operation_records,
        "operations_truncated": len(operations) > MAX_ACTIVE_JOB_OPERATIONS,
        "toolpath_validity": {
            "all_active_valid": bool(active_operation_count)
            and len(operations) <= MAX_ACTIVE_JOB_OPERATIONS
            and all(value["toolpath_valid"] for value in active_records),
            "active_operation_count": active_operation_count,
            "active_command_count": active_commands,
            "invalid_active_count": sum(
                1 for value in active_records if not value["toolpath_valid"]
            ),
            "uninspected_active_count": max(
                0,
                active_operation_count - len(active_records),
            ),
        },
        "readiness": {
            "simulation": {
                "ready": not simulation_issues,
                "stock_ready": stock_ready,
                "issues": simulation_issues[:MAX_READINESS_ISSUES],
                "issues_truncated": len(simulation_issues) > MAX_READINESS_ISSUES,
                "exact_preflight_required": True,
            },
            "post": _post_readiness(job, common_issues),
        },
    }
