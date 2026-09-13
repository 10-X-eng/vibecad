# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared setup-scoped workpiece coordinate configuration."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping


_FRAME_FIELDS = frozenset({"origin_mm", "x_direction_hint", "z_direction"})
_VECTOR_FIELDS = frozenset({"x", "y", "z"})


def _sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            dict(value),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _vector(value: Any, name: str, *, direction: bool) -> dict[str, float]:
    if not isinstance(value, Mapping) or set(value) != _VECTOR_FIELDS:
        raise ValueError(f"{name} must contain exactly x, y, and z")
    result = {axis: float(value[axis]) for axis in ("x", "y", "z")}
    if any(not math.isfinite(number) for number in result.values()):
        raise ValueError(f"{name} must contain finite coordinates")
    if not direction:
        return result
    length = math.sqrt(sum(number * number for number in result.values()))
    if length <= 1.0e-12:
        raise ValueError(f"{name} must be a nonzero direction")
    return {axis: result[axis] / length for axis in ("x", "y", "z")}


def normalize_workpiece_frame(value: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one frame expressed in current document coordinates."""

    if not isinstance(value, Mapping) or set(value) != _FRAME_FIELDS:
        raise ValueError(
            "frame must contain origin_mm, x_direction_hint, and z_direction"
        )
    origin = _vector(value["origin_mm"], "origin_mm", direction=False)
    x_hint = _vector(
        value["x_direction_hint"],
        "x_direction_hint",
        direction=True,
    )
    z_axis = _vector(value["z_direction"], "z_direction", direction=True)
    dot = sum(x_hint[axis] * z_axis[axis] for axis in ("x", "y", "z"))
    projected = {
        axis: x_hint[axis] - dot * z_axis[axis]
        for axis in ("x", "y", "z")
    }
    projected_length = math.sqrt(
        sum(number * number for number in projected.values())
    )
    if projected_length <= 1.0e-12:
        raise ValueError("x_direction_hint must not be parallel to z_direction")
    return {
        "origin_mm": origin,
        "x_direction_hint": x_hint,
        "z_direction": z_axis,
    }


def _placement_state(obj: Any) -> dict[str, Any]:
    placement = obj.Placement
    return {
        "origin_mm": {
            axis: round(float(getattr(placement.Base, axis)), 9)
            for axis in ("x", "y", "z")
        },
        "quaternion": [round(float(value), 12) for value in placement.Rotation.Q],
    }


def workpiece_configuration_state(job: Any) -> dict[str, Any]:
    """Return every setup-owned model placement and its stock placement."""

    models = []
    for resource in tuple(getattr(getattr(job, "Model", None), "Group", ()) or ()):
        source = None
        try:
            source = job.Proxy.baseObject(job, resource)
        except Exception:
            pass
        models.append(
            {
                "resource_name": str(resource.Name),
                "source_name": str(getattr(source, "Name", "") or ""),
                "placement": _placement_state(resource),
            }
        )
    exact: dict[str, Any] = {"models": models}
    stock = getattr(job, "Stock", None)
    if stock is not None:
        exact["stock"] = {
            "resource_name": str(stock.Name),
            "placement": _placement_state(stock),
        }
    result = dict(exact)
    result["state_sha256"] = _sha256(exact)
    return result


def _frame_placement(frame: Mapping[str, Any]):
    import FreeCAD

    normalized = normalize_workpiece_frame(frame)
    origin = normalized["origin_mm"]
    x_hint = normalized["x_direction_hint"]
    z_value = normalized["z_direction"]
    z_axis = FreeCAD.Vector(z_value["x"], z_value["y"], z_value["z"])
    x_axis = FreeCAD.Vector(x_hint["x"], x_hint["y"], x_hint["z"])
    x_axis = x_axis - z_axis * x_axis.dot(z_axis)
    x_axis.normalize()
    y_axis = z_axis.cross(x_axis)
    y_axis.normalize()
    matrix = FreeCAD.Matrix()
    matrix.A11, matrix.A21, matrix.A31 = x_axis.x, x_axis.y, x_axis.z
    matrix.A12, matrix.A22, matrix.A32 = y_axis.x, y_axis.y, y_axis.z
    matrix.A13, matrix.A23, matrix.A33 = z_axis.x, z_axis.y, z_axis.z
    return FreeCAD.Placement(
        FreeCAD.Vector(origin["x"], origin["y"], origin["z"]),
        FreeCAD.Rotation(matrix),
    )


def apply_workpiece_frame(
    job: Any,
    frame: Mapping[str, Any],
    *,
    include_stock: bool,
) -> dict[str, Any]:
    """Map one current workpiece frame onto machine XYZ for one Job."""

    if not isinstance(include_stock, bool):
        raise ValueError("include_stock must be boolean")
    models = tuple(getattr(getattr(job, "Model", None), "Group", ()) or ())
    if not models:
        raise ValueError("the CAM setup has no workpiece models")
    transform = _frame_placement(frame).inverse()
    targets = list(models)
    stock = getattr(job, "Stock", None)
    if include_stock:
        if stock is None:
            raise ValueError("the CAM setup has no stock")
        targets.append(stock)
    for target in targets:
        target.Placement = transform.multiply(target.Placement)
    return workpiece_configuration_state(job)
