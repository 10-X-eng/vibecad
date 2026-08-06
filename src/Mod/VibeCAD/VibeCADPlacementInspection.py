# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, read-only coordinate frames for planned VibeScript geometry."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from typing import Any


class PlacementInspectionError(ValueError):
    """A planned placement cannot be resolved without guessing."""

    def __init__(self, message: str, *, code: str = "PLACEMENT_INVALID") -> None:
        self.code = str(code)
        super().__init__(str(message))


_PRINCIPAL_SKETCH_FRAMES: dict[
    str,
    tuple[
        tuple[float, float, float],
        tuple[float, float, float],
        tuple[float, float, float],
    ],
] = {
    # Local sketch X, local sketch Y, local sketch normal in global coordinates.
    "XY": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "XZ": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0)),
    "YZ": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
}


def _number(value: Any, *, name: str) -> float:
    if isinstance(value, bool):
        raise PlacementInspectionError(f"{name} must be a finite number.")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise PlacementInspectionError(f"{name} must be a finite number.") from exc
    if not math.isfinite(result):
        raise PlacementInspectionError(f"{name} must be a finite number.")
    return result


def _vector(value: Any, *, name: str, nonzero: bool = False) -> list[float]:
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or len(value) != 3
    ):
        raise PlacementInspectionError(f"{name} must be [x, y, z].")
    result = [
        _number(item, name=f"{name}[{index}]") for index, item in enumerate(value)
    ]
    if nonzero and _length(result) <= 1.0e-12:
        raise PlacementInspectionError(f"{name} must be non-zero.")
    return result


def _length(value: Sequence[float]) -> float:
    return math.sqrt(sum(float(item) * float(item) for item in value))


def _unit(value: Sequence[float]) -> list[float]:
    magnitude = _length(value)
    return [float(item) / magnitude for item in value]


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(float(a) * float(b) for a, b in zip(left, right))


def _cross(left: Sequence[float], right: Sequence[float]) -> list[float]:
    return [
        float(left[1]) * float(right[2]) - float(left[2]) * float(right[1]),
        float(left[2]) * float(right[0]) - float(left[0]) * float(right[2]),
        float(left[0]) * float(right[1]) - float(left[1]) * float(right[0]),
    ]


def _oriented_axes(
    axis_direction: Sequence[float],
    x_direction: Sequence[float],
    *,
    axis_name: str,
) -> tuple[list[float], list[float], list[float]]:
    z_axis = _unit(axis_direction)
    raw_x = [
        float(item) - _dot(x_direction, z_axis) * axis
        for item, axis in zip(x_direction, z_axis)
    ]
    if _length(raw_x) <= 1.0e-12:
        raise PlacementInspectionError(
            f"x_direction must not be parallel to {axis_name}."
        )
    x_axis = _unit(raw_x)
    y_axis = _unit(_cross(z_axis, x_axis))
    return x_axis, y_axis, z_axis


def _matrix(
    origin: Sequence[float],
    x_axis: Sequence[float],
    y_axis: Sequence[float],
    z_axis: Sequence[float],
) -> list[float]:
    """Return the row-major local-to-global affine matrix used by FreeCAD."""

    return [
        float(x_axis[0]),
        float(y_axis[0]),
        float(z_axis[0]),
        float(origin[0]),
        float(x_axis[1]),
        float(y_axis[1]),
        float(z_axis[1]),
        float(origin[1]),
        float(x_axis[2]),
        float(y_axis[2]),
        float(z_axis[2]),
        float(origin[2]),
        0.0,
        0.0,
        0.0,
        1.0,
    ]


def _result(
    operation: str,
    origin: Sequence[float],
    x_axis: Sequence[float],
    y_axis: Sequence[float],
    z_axis: Sequence[float],
    *,
    source_arguments: Mapping[str, Any],
) -> dict[str, Any]:
    is_sketch = operation == "sketch"
    result: dict[str, Any] = {
        "ok": True,
        "tool": "vibescript.read_placement",
        "operation": operation,
        "coordinate_system": "right-handed",
        "origin_mm": [float(item) for item in origin],
        "local_axes_in_global_coordinates": {
            "x": [float(item) for item in x_axis],
            "y": [float(item) for item in y_axis],
            "z": [float(item) for item in z_axis],
        },
        "local_to_global_matrix_row_major": _matrix(
            origin,
            x_axis,
            y_axis,
            z_axis,
        ),
        "point_mapping": ("global = origin_mm + local_x*x + local_y*y + local_z*z"),
        "source_arguments": dict(source_arguments),
    }
    if is_sketch:
        result["sketch_mapping"] = (
            "api 2D point [u, v] maps to origin_mm + u*local_x + v*local_y"
        )
        result["positive_feature_direction"] = [float(item) for item in z_axis]
        result["default_subtractive_direction"] = [-float(item) for item in z_axis]
        result["linear_feature_directions"] = {
            "along_normal": [float(item) for item in z_axis],
            "opposite_normal": [-float(item) for item in z_axis],
            "symmetric": [
                [float(item) for item in z_axis],
                [-float(item) for item in z_axis],
            ],
        }
    else:
        result["dimension_mapping"] = {
            "length": "local +X",
            "width": "local +Y",
            "height": "local +Z",
        }
        if operation == "wedge":
            result["ridge_mapping"] = (
                "ridge_x is measured on local X and the ridge runs along local Y"
            )
    return result


def read_placement(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve one exact planned sketch, box, or wedge frame without CAD mutation."""

    operation = str(arguments.get("operation") or "").strip().lower()
    if operation not in {"sketch", "box", "wedge"}:
        raise PlacementInspectionError("operation must be sketch, box, or wedge.")

    if operation == "sketch":
        placement = arguments.get("placement")
        plane_offset = _number(
            arguments.get("plane_offset_mm", 0.0),
            name="plane_offset_mm",
        )
        if placement is not None:
            if not isinstance(placement, Mapping) or set(placement) != {
                "origin",
                "normal",
                "x_direction",
            }:
                raise PlacementInspectionError(
                    "placement must contain exactly origin, normal, and x_direction."
                )
            if abs(plane_offset) > 1.0e-12:
                raise PlacementInspectionError(
                    "plane_offset_mm cannot be combined with an explicit placement."
                )
            origin = _vector(placement["origin"], name="placement.origin")
            normal = _vector(
                placement["normal"],
                name="placement.normal",
                nonzero=True,
            )
            x_direction = _vector(
                placement["x_direction"],
                name="placement.x_direction",
                nonzero=True,
            )
            x_axis, y_axis, z_axis = _oriented_axes(
                normal,
                x_direction,
                axis_name="placement.normal",
            )
            return _result(
                operation,
                origin,
                x_axis,
                y_axis,
                z_axis,
                source_arguments={
                    "placement": {
                        "origin": origin,
                        "normal": z_axis,
                        "x_direction": x_axis,
                    }
                },
            )

        plane = str(arguments.get("plane") or "XY").strip().upper()
        frame = _PRINCIPAL_SKETCH_FRAMES.get(plane)
        if frame is None:
            raise PlacementInspectionError("plane must be XY, XZ, or YZ.")
        x_axis, y_axis, z_axis = ([float(item) for item in axis] for axis in frame)
        origin = [plane_offset * item for item in z_axis]
        return _result(
            operation,
            origin,
            x_axis,
            y_axis,
            z_axis,
            source_arguments={
                "plane": plane,
                "plane_offset_mm": plane_offset,
            },
        )

    origin = _vector(arguments.get("origin", [0.0, 0.0, 0.0]), name="origin")
    direction = _vector(
        arguments.get("direction", [0.0, 0.0, 1.0]),
        name="direction",
        nonzero=True,
    )
    raw_x_direction = arguments.get("x_direction")
    if raw_x_direction is None:
        unit_direction = _unit(direction)
        if any(
            abs(actual - expected) > 1.0e-12
            for actual, expected in zip(unit_direction, (0.0, 0.0, 1.0))
        ):
            raise PlacementInspectionError(
                "An oriented box or wedge requires x_direction to resolve roll exactly. "
                "Pass the same explicit x_direction to api.box/api.wedge.",
                code="PLACEMENT_UNDERSPECIFIED",
            )
        raw_x_direction = [1.0, 0.0, 0.0]
    x_direction = _vector(
        raw_x_direction,
        name="x_direction",
        nonzero=True,
    )
    x_axis, y_axis, z_axis = _oriented_axes(
        direction,
        x_direction,
        axis_name="direction",
    )
    return _result(
        operation,
        origin,
        x_axis,
        y_axis,
        z_axis,
        source_arguments={
            "origin": origin,
            "direction": z_axis,
            "x_direction": x_axis,
        },
    )
