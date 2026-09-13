# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact retained-stock and inter-setup relationship state."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureState import is_job, job_state
from VibeCADNativeMeshState import mesh_object_state


_RESULT_PROPERTIES = frozenset(
    {
        "SimulationJob",
        "SimulationJobName",
        "SimulationJobStateSHA256",
        "SimulationOperationNames",
        "SimulationQuality",
        "SimulationResolution",
        "SimulationProgramSHA256",
    }
)
_RELATIONSHIP_PROPERTIES = frozenset(
    {
        "PreviousSetup",
        "RemainingStockResult",
        "RemainingStockSolid",
        "SetupRelationshipKind",
        "PreviousSetupStateSHA256",
        "RemainingStockResultStateSHA256",
        "MachiningProgramSHA256",
        "ConversionArtifactSHA256",
    }
)


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            dict(value),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _live(document: Any, obj: Any) -> bool:
    name = str(getattr(obj, "Name", "") or "")
    return bool(
        name
        and getattr(obj, "Document", None) is document
        and document.getObject(name) is obj
    )


def is_simulation_result(obj: Any) -> bool:
    properties = frozenset(
        str(name) for name in tuple(getattr(obj, "PropertiesList", ()) or ())
    )
    return bool(
        str(getattr(obj, "TypeId", "") or "") == "Mesh::FeaturePython"
        and _RESULT_PROPERTIES.issubset(properties)
    )


def simulation_result_state(result: Any) -> dict[str, Any]:
    """Return exact retained Mesh and source-setup provenance."""

    document = getattr(result, "Document", None)
    if document is None or not _live(document, result) or not is_simulation_result(result):
        raise NativeManufactureError(
            "The target is not a current retained CAM material result.",
            error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    source_job = getattr(result, "SimulationJob", None)
    expected_job_state = str(
        getattr(result, "SimulationJobStateSHA256", "") or ""
    )
    if not _live(document, source_job) or not is_job(source_job):
        current_job_state = None
        source_current = False
    else:
        current_job_state = job_state(source_job)
        source_current = bool(
            len(expected_job_state) == 64
            and current_job_state.get("state_sha256") == expected_job_state
        )
    mesh_state = mesh_object_state(result)
    try:
        resolution = round(
            float(result.SimulationResolution.getValueAs("mm")),
            9,
        )
    except Exception:
        resolution = float("nan")
    program_sha256 = str(getattr(result, "SimulationProgramSHA256", "") or "")
    retained_shape = getattr(result, "RetainedStockShape", None)
    retained_shape_sha256 = str(
        getattr(result, "RetainedStockShapeSHA256", "") or ""
    )
    retained_solid_count = int(
        getattr(result, "RetainedStockSolidCount", 0) or 0
    )
    retained_shape_type = str(
        getattr(retained_shape, "ShapeType", "") or ""
    )
    retained_shape_available = bool(
        retained_shape is not None
        and not retained_shape.isNull()
        and retained_shape_type in {"Solid", "CompSolid", "Compound"}
        and retained_solid_count >= 1
        and len(tuple(retained_shape.Solids)) == retained_solid_count
        and len(retained_shape_sha256) == 64
    )
    try:
        verification = json.loads(
            str(getattr(result, "SimulationVerificationJSON", "") or "{}")
        )
    except (TypeError, ValueError):
        verification = {}
    if not isinstance(verification, Mapping):
        verification = {}
    valid_provenance = bool(
        math.isfinite(resolution)
        and resolution > 0.0
        and len(program_sha256) == 64
        and len(expected_job_state) == 64
    )
    state = {
        **{
            name: mesh_state[name]
            for name in (
                "object_name",
                "label",
                "type_id",
                "topology",
                "bounds",
                "geometry_revision",
            )
            if name in mesh_state
        },
        "source_setup": {
            "object_name": str(getattr(source_job, "Name", "") or ""),
            "label": str(getattr(source_job, "Label", "") or "")[:160],
            "expected_state_sha256": expected_job_state,
            "current_state_sha256": (
                str(current_job_state.get("state_sha256") or "")
                if current_job_state is not None
                else ""
            ),
        },
        "source_current": source_current,
        "provenance_valid": valid_provenance,
        "operation_names": list(
            getattr(result, "SimulationOperationNames", ()) or ()
        ),
        "quality": int(getattr(result, "SimulationQuality", 0) or 0),
        "resolution_mm": resolution if math.isfinite(resolution) else None,
        "program_sha256": program_sha256,
        "mesh_state_sha256": str(mesh_state.get("state_sha256") or ""),
        "retained_shape": {
            "available": retained_shape_available,
            "shape_type": retained_shape_type,
            "solid_count": retained_solid_count,
            "shape_sha256": retained_shape_sha256,
        },
        "verification": dict(verification),
    }
    state["state_sha256"] = _digest(state)
    return state


def resolve_simulation_result_target(
    document: Any,
    value: Mapping[str, Any],
) -> tuple[Any, dict[str, Any]]:
    if not isinstance(value, Mapping) or set(value) != {
        "object_name",
        "expected_state_sha256",
    }:
        raise NativeManufactureError(
            "remaining_stock must contain object_name and expected_state_sha256.",
            error_code="NATIVE_ARGUMENTS_INVALID",
        )
    name = str(value.get("object_name") or "").strip()
    result = document.getObject(name) if name else None
    current = simulation_result_state(result)
    expected = str(value.get("expected_state_sha256") or "")
    if current.get("state_sha256") != expected:
        raise NativeManufactureError(
            "The retained CAM material result changed after turn start.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
            repair={
                "remaining_stock": {
                    "object_name": name,
                    "expected_state_sha256": current.get("state_sha256"),
                }
            },
        )
    if not current["provenance_valid"]:
        raise NativeManufactureError(
            "The retained CAM material result has incomplete source provenance.",
            error_code="NATIVE_MANUFACTURE_REMAINING_STOCK_INVALID",
        )
    if not current["source_current"]:
        raise NativeManufactureError(
            "The setup that produced this retained stock has changed.",
            error_code="NATIVE_MANUFACTURE_REMAINING_STOCK_STALE",
            repair={"source_setup": current["source_setup"]},
        )
    return result, current


def setup_relationship_state(
    job: Any,
    *,
    _visited: frozenset[int] = frozenset(),
) -> dict[str, Any] | None:
    """Return explicit relationship currency, including upstream ancestry."""

    properties = frozenset(
        str(name) for name in tuple(getattr(job, "PropertiesList", ()) or ())
    )
    if not _RELATIONSHIP_PROPERTIES.issubset(properties):
        return None
    try:
        from Path.Main.JobRelationship import remaining_stock_relationship

        relationship = remaining_stock_relationship(job)
    except Exception as exc:
        raise NativeManufactureError(
            "The CAM setup relationship could not be read.",
            error_code="NATIVE_MANUFACTURE_RELATIONSHIP_INVALID",
        ) from exc
    if relationship is None:
        return None
    if id(job) in _visited:
        return {
            "kind": "remaining_stock",
            "current": False,
            "issue": "RELATIONSHIP_CYCLE",
        }
    visited = _visited | {id(job)}
    document = getattr(job, "Document", None)
    previous = relationship["previous_setup"]
    retained = relationship["remaining_stock_result"]
    solid = relationship["remaining_stock_solid"]
    current = True
    issues = []
    previous_state = None
    result_state = None
    if document is None or not _live(document, previous) or not is_job(previous):
        current = False
        issues.append("PREVIOUS_SETUP_MISSING")
    else:
        previous_state = job_state(previous)
        if previous_state.get("state_sha256") != relationship[
            "previous_setup_state_sha256"
        ]:
            current = False
            issues.append("PREVIOUS_SETUP_CHANGED")
        upstream = setup_relationship_state(previous, _visited=visited)
        if upstream is not None and not upstream.get("current"):
            current = False
            issues.append("PREVIOUS_SETUP_INPUT_STALE")
    if document is None or not _live(document, retained):
        current = False
        issues.append("REMAINING_STOCK_RESULT_MISSING")
    else:
        try:
            result_state = simulation_result_state(retained)
        except NativeManufactureError:
            current = False
            issues.append("REMAINING_STOCK_RESULT_INVALID")
        else:
            if result_state.get("state_sha256") != relationship[
                "remaining_stock_result_state_sha256"
            ]:
                current = False
                issues.append("REMAINING_STOCK_RESULT_CHANGED")
    stock = getattr(job, "Stock", None)
    if (
        document is None
        or not _live(document, solid)
        or stock is not solid
        or getattr(stock, "Source", None) is not retained
    ):
        current = False
        issues.append("REMAINING_STOCK_SOLID_CHANGED")
    if str(relationship["machining_program_sha256"]) != str(
        getattr(retained, "SimulationProgramSHA256", "") or ""
    ):
        current = False
        issues.append("MACHINING_PROGRAM_CHANGED")
    return {
        "kind": "remaining_stock",
        "current": current,
        "issues": issues,
        "previous_setup": {
            "object_name": str(getattr(previous, "Name", "") or ""),
            "expected_state_sha256": relationship[
                "previous_setup_state_sha256"
            ],
            "current_state_sha256": (
                str(previous_state.get("state_sha256") or "")
                if previous_state is not None
                else ""
            ),
        },
        "remaining_stock_result": {
            "object_name": str(getattr(retained, "Name", "") or ""),
            "expected_state_sha256": relationship[
                "remaining_stock_result_state_sha256"
            ],
            "current_state_sha256": (
                str(result_state.get("state_sha256") or "")
                if result_state is not None
                else ""
            ),
        },
        "remaining_stock_solid": {
            "object_name": str(getattr(solid, "Name", "") or ""),
            "artifact_sha256": relationship["conversion_artifact_sha256"],
        },
        "program_sha256": relationship["machining_program_sha256"],
    }
