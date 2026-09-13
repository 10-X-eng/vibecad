# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact bounded reads for CAM Jobs, toolpaths, and loop inference."""

from __future__ import annotations

import hashlib
import math
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureFollowUpState import (
    is_simulation_result,
    simulation_result_state,
)
from VibeCADNativeManufactureReadiness import build_active_job_summary
from VibeCADNativeManufactureState import (
    candidate_model_state,
    is_job,
    job_state,
    operation_state,
    resolve_job_target,
    resolve_model_target,
    resolve_operation_target,
)


MAX_SANITY_ISSUES = 128
MAX_COMMAND_PARAMETERS = 32


def search_setup_options(
    *,
    category: str,
    query: str,
    offset: int,
    page_size: int,
) -> dict[str, Any]:
    """Read values accepted by the shared CAM setup editor."""

    try:
        from Path.Main import JobSetup

        options = JobSetup.search_setup_options(
            category,
            query=query,
            offset=offset,
            page_size=page_size,
        )
    except ValueError as exc:
        raise NativeManufactureError(
            str(exc),
            error_code="NATIVE_ARGUMENTS_INVALID",
        ) from exc
    except Exception as exc:
        raise NativeManufactureError(
            "The installed CAM setup catalogs could not be read.",
            error_code="NATIVE_MANUFACTURE_ENVIRONMENT_UNAVAILABLE",
        ) from exc
    return {"setup_options": options}


def list_setups(
    document: Any,
    *,
    query: str,
    offset: int,
    page_size: int,
) -> dict[str, Any]:
    """Return one searchable page of exact independent CAM setup targets."""

    normalized_query = str(query or "").strip().casefold()
    matches = [
        obj
        for obj in tuple(getattr(document, "Objects", ()) or ())
        if is_job(obj)
        and normalized_query
        in "\n".join(
            (
                str(getattr(obj, "Name", "") or ""),
                str(getattr(obj, "Label", "") or ""),
            )
        ).casefold()
    ] if normalized_query else [
        obj
        for obj in tuple(getattr(document, "Objects", ()) or ())
        if is_job(obj)
    ]
    start = min(int(offset), len(matches))
    stop = min(start + int(page_size), len(matches))
    items = []
    for job in matches[start:stop]:
        state = job_state(
            job,
            operation_limit=0,
            tool_limit=0,
            model_limit=0,
        )
        workflow = build_active_job_summary(document, job, state)
        item = dict(state)
        item.update(
            readiness=dict(workflow["readiness"]),
            toolpath_validity=dict(workflow["toolpath_validity"]),
        )
        items.append(item)
    return {
        "setups": {
            "query": normalized_query,
            "offset": start,
            "count": len(items),
            "total": len(matches),
            "next_offset": stop if stop < len(matches) else None,
            "items": items,
        }
    }


def list_remaining_stock(
    document: Any,
    *,
    query: str,
    offset: int,
    page_size: int,
) -> dict[str, Any]:
    """Return one searchable page of exact retained-stock targets."""

    normalized_query = str(query or "").strip().casefold()
    results = [
        obj
        for obj in tuple(getattr(document, "Objects", ()) or ())
        if is_simulation_result(obj)
    ]
    matches = [
        result
        for result in results
        if not normalized_query
        or normalized_query
        in "\n".join(
            (
                str(getattr(result, "Name", "") or ""),
                str(getattr(result, "Label", "") or ""),
            )
        ).casefold()
    ]
    start = min(int(offset), len(matches))
    stop = min(start + int(page_size), len(matches))
    items = [simulation_result_state(result) for result in matches[start:stop]]
    return {
        "remaining_stock": {
            "query": normalized_query,
            "offset": start,
            "count": len(items),
            "total": len(matches),
            "next_offset": stop if stop < len(matches) else None,
            "items": items,
        }
    }


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return round(result, 9) if math.isfinite(result) else None


def _parameter(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    number = _finite(value)
    if number is not None:
        return number
    if all(hasattr(value, axis) for axis in ("x", "y", "z")):
        vector = {axis: _finite(getattr(value, axis)) for axis in ("x", "y", "z")}
        if all(item is not None for item in vector.values()):
            return vector
    return str(value)[:160]


def _command_record(command: Any, index: int) -> dict[str, Any]:
    raw_parameters = dict(getattr(command, "Parameters", {}) or {})
    names = sorted(str(name) for name in raw_parameters)[:MAX_COMMAND_PARAMETERS]
    return {
        "index": index,
        "name": str(getattr(command, "Name", "") or "")[:32],
        "parameters": {name: _parameter(raw_parameters[name]) for name in names},
        "parameters_truncated": len(raw_parameters) > MAX_COMMAND_PARAMETERS,
    }


def _toolpath_digest(commands: tuple[Any, ...]) -> str:
    digest = hashlib.sha256()
    for command in commands:
        try:
            encoded = str(command.toGCode()).encode("utf-8")
        except Exception as exc:
            raise NativeManufactureError(
                "The CAM operation contains an unreadable placed command.",
                error_code="NATIVE_MANUFACTURE_TOOLPATH_INVALID",
            ) from exc
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def read_job(
    document: Any,
    target: Mapping[str, Any],
    *,
    operation_offset: int,
    page_size: int,
) -> dict[str, Any]:
    job, before = resolve_job_target(document, target)
    operations = list(getattr(getattr(job, "Operations", None), "Group", ()) or ())
    start = min(int(operation_offset), len(operations))
    stop = min(start + int(page_size), len(operations))
    summary = {
        key: value
        for key, value in before.items()
        if key not in {"operations", "tools"}
    }
    summary["tools"] = before.get("tools", ())
    summary["operation_page"] = {
        "offset": start,
        "count": stop - start,
        "total": len(operations),
        "next_offset": stop if stop < len(operations) else None,
        "items": [operation_state(operation) for operation in operations[start:stop]],
    }
    if job_state(job).get("state_sha256") != before.get("state_sha256"):
        raise NativeManufactureError(
            "The CAM Job changed while it was being read.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        )
    return {"job": summary}


def validate_job(document: Any, target: Mapping[str, Any]) -> dict[str, Any]:
    job, before = resolve_job_target(document, target)
    try:
        from Path.Main.Sanity.Sanity import CAMSanity

        issues, critical = CAMSanity.validate_job(job)
    except Exception as exc:
        raise NativeManufactureError(
            "The CAM Job sanity validator could not evaluate this exact graph."
        ) from exc
    records = []
    for issue in list(issues)[:MAX_SANITY_ISSUES]:
        record = dict(issue) if isinstance(issue, Mapping) else {"Note": str(issue)}
        records.append(
            {
                "severity": str(record.get("squawkType") or "INFO")[:32],
                "source": str(
                    record.get("Operator") or record.get("Source") or "CAM"
                )[:80],
                "message": str(record.get("Note") or record.get("message") or "")[:512],
            }
        )
    after = job_state(job)
    if after.get("state_sha256") != before.get("state_sha256"):
        raise NativeManufactureError(
            "The CAM Job changed while sanity validation was running.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        )
    return {
        "validation": {
            "job": {
                "object_name": before["object_name"],
                "label": before.get("label", before["object_name"]),
                "state_sha256": before["state_sha256"],
            },
            "issue_count": len(issues),
            "critical_count": len(critical),
            "issues": records,
            "issues_truncated": len(issues) > MAX_SANITY_ISSUES,
            "ready": not critical,
        }
    }


def inspect_toolpath(
    document: Any,
    target: Mapping[str, Any],
    *,
    offset: int,
    page_size: int,
) -> dict[str, Any]:
    operation, before = resolve_operation_target(document, target)
    try:
        import PathScripts.PathUtils as PathUtils

        commands = tuple(PathUtils.getPathWithPlacement(operation).Commands)
    except Exception as exc:
        raise NativeManufactureError(
            "The exact CAM operation has no readable placed toolpath.",
            error_code="NATIVE_MANUFACTURE_TOOLPATH_INVALID",
        ) from exc
    start = min(int(offset), len(commands))
    stop = min(start + int(page_size), len(commands))
    after = operation_state(operation)
    if after.get("state_sha256") != before.get("state_sha256"):
        raise NativeManufactureError(
            "The CAM operation changed while its toolpath was being read.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        )
    return {
        "toolpath": {
            "operation": before,
            "toolpath_sha256": _toolpath_digest(commands),
            "offset": start,
            "count": stop - start,
            "total": len(commands),
            "next_offset": stop if stop < len(commands) else None,
            "commands": [
                _command_record(command, index)
                for index, command in enumerate(commands[start:stop], start)
            ],
        }
    }


def _shape_element(shape: Any, name: str, expected: str) -> Any:
    try:
        element = shape.getElement(name)
    except Exception as exc:
        raise NativeManufactureError(
            f"The exact loop seed {name!r} no longer exists.",
            error_code="NATIVE_MANUFACTURE_TARGET_STALE",
        ) from exc
    if str(getattr(element, "ShapeType", "")) != expected:
        raise NativeManufactureError(
            f"The loop seed {name!r} is not a {expected.lower()}.",
            error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    return element


def detect_loop(
    document: Any,
    target: Mapping[str, Any],
    selection: Mapping[str, Any],
) -> dict[str, Any]:
    source, before = resolve_model_target(document, target)
    if not isinstance(selection, Mapping):
        raise NativeManufactureError(
            "Loop selection must be one typed object.",
            error_code="NATIVE_ARGUMENTS_INVALID",
        )
    shape = source.Shape
    kind = str(selection.get("kind") or "")
    names = None
    result_kind = "edges"
    edges = None
    try:
        import Path.Geom as PathGeom
        import PathScripts.PathUtils as PathUtils

        if kind == "all_edges":
            names = [f"Edge{index}" for index in range(1, len(shape.Edges) + 1)]
        elif kind == "faces":
            face_names = tuple(selection.get("faces") or ())
            faces = [_shape_element(shape, str(name), "Face") for name in face_names]
            edges = PathUtils.innerEdgesFromFace(source, faces[0])
            if not edges:
                if all(PathGeom.isVertical(face) for face in faces):
                    names = PathUtils.horizontalFaceLoops(source, faces)
                elif PathGeom.isHorizontal(faces[0]):
                    names = PathUtils.horizontalFacesAtHeight(
                        source,
                        faces[0].CenterOfMass.z,
                    )
                if names:
                    result_kind = "faces"
                if not names:
                    edges = [edge for face in faces for edge in face.Edges]
        elif kind == "edges":
            edge_names = tuple(selection.get("edges") or ())
            selected = [_shape_element(shape, str(name), "Edge") for name in edge_names]
            if len(selected) == 1:
                edges = PathUtils.horizontalEdgeLoop(source, selected[0])
            elif len(selected) == 2:
                edges = PathUtils.loopdetect(source, selected[0], selected[1])
                if not edges:
                    edges = PathUtils.tangentEdgeLoop(source, selected[0], selected[1])
            if not edges:
                edges = PathUtils.wiresdetect(source, selected)
        else:
            raise NativeManufactureError(
                "Loop selection kind must be all_edges, faces, or edges.",
                error_code="NATIVE_ARGUMENTS_INVALID",
            )
        if edges and not names:
            hashes = {edge.hashCode() for edge in edges}
            names = [
                f"Edge{index}"
                for index, edge in enumerate(shape.Edges, 1)
                if edge.hashCode() in hashes
            ]
    except NativeManufactureError:
        raise
    except Exception as exc:
        raise NativeManufactureError(
            "Closed-loop detection failed for the supplied exact geometry.",
            error_code="NATIVE_MANUFACTURE_LOOP_NOT_FOUND",
        ) from exc
    names = list(dict.fromkeys(str(name) for name in (names or ()) if str(name)))
    if not names:
        raise NativeManufactureError(
            "No closed edge loop could be inferred from that exact seed.",
            error_code="NATIVE_MANUFACTURE_LOOP_NOT_FOUND",
            repair={"accepted_selection_kinds": ["all_edges", "faces", "edges"]},
        )
    after = candidate_model_state(source)
    if after.get("state_sha256") != before.get("state_sha256"):
        raise NativeManufactureError(
            "The CAM model changed while loop detection was running.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        )
    return {
        "loop": {
            "object_name": before["object_name"],
            "source_state_sha256": before["state_sha256"],
            "selection": {
                "kind": result_kind,
                result_kind: names,
            },
            "element_count": len(names),
        }
    }
