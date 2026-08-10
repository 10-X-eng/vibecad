# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared exact connector graph and live-axis facts for coupled Assembly joints."""

from __future__ import annotations

import math
from typing import Any

from VibeCADNativeAssemblyJointConnectors import (
    JointConnectorSpec,
    placement_is_same,
)


AXIS_DOT_TOLERANCE = 1.0e-6
AXIS_LINE_TOLERANCE_MM = 1.0e-6


def reference_side(joint: Any, side: int) -> tuple[Any, tuple[str, str], Any] | None:
    """Read one complete two-path regular-joint connector side."""

    try:
        reference = getattr(joint, f"Reference{side}")
        component = reference[0]
        paths = tuple(str(path) for path in reference[1])
        offset = getattr(joint, f"Offset{side}")
        if component is None or len(paths) != 2:
            return None
        return component, paths, offset
    except (AttributeError, IndexError, ReferenceError, TypeError):
        return None


def side_matches_spec(
    joint: Any,
    side: int,
    component: Any,
    spec: JointConnectorSpec,
) -> bool:
    """Return whether one persisted side exactly equals one requested connector."""

    actual = reference_side(joint, side)
    return bool(
        actual is not None
        and actual[0] is component
        and actual[1] == (spec.element_path, spec.anchor_path)
        and placement_is_same(actual[2], spec.offset)
    )


def matching_spec_side(
    joint: Any,
    component: Any,
    spec: JointConnectorSpec,
) -> int:
    """Return the unique persisted side matching the connector, or zero."""

    matches = [
        side for side in (1, 2) if side_matches_spec(joint, side, component, spec)
    ]
    return matches[0] if len(matches) == 1 else 0


def sides_equal(first: Any, first_side: int, second: Any, second_side: int) -> bool:
    """Compare complete component, path, and attachment-offset identity."""

    left = reference_side(first, first_side)
    right = reference_side(second, second_side)
    return bool(
        left is not None
        and right is not None
        and left[0] is right[0]
        and left[1] == right[1]
        and placement_is_same(left[2], right[2])
    )


def joint_components(joint: Any) -> set[Any]:
    """Return the complete set of valid component objects referenced by a joint."""

    return {
        side[0]
        for side in (reference_side(joint, 1), reference_side(joint, 2))
        if side is not None
    }


def _global_side_frame(joint: Any, side: int) -> Any:
    import UtilsAssembly

    return UtilsAssembly.getJcsGlobalPlc(
        getattr(joint, f"Placement{side}"),
        getattr(joint, f"Reference{side}"),
    )


def _axis_and_origin(
    frame: Any,
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    import FreeCAD as App

    direction = frame.Rotation.multVec(App.Vector(0, 0, 1))
    origin = frame.Base
    axis = (float(direction.x), float(direction.y), float(direction.z))
    length = math.sqrt(sum(value * value for value in axis))
    if not math.isfinite(length) or length <= 1.0e-12:
        raise ValueError("Joint connector axis is degenerate.")
    normalized = tuple(value / length for value in axis)
    point = (float(origin.x), float(origin.y), float(origin.z))
    if not all(math.isfinite(value) for value in (*normalized, *point)):
        raise ValueError("Joint connector frame is non-finite.")
    return normalized, point


def _cross_length(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> float:
    cross = (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )
    return math.sqrt(sum(value * value for value in cross))


def axes_perpendicular(
    first_joint: Any,
    first_side: int,
    second_joint: Any,
    second_side: int,
) -> bool:
    """Prove two live connector Z axes are perpendicular."""

    try:
        first_axis, _first_origin = _axis_and_origin(
            _global_side_frame(first_joint, first_side)
        )
        second_axis, _second_origin = _axis_and_origin(
            _global_side_frame(second_joint, second_side)
        )
        dot = sum(
            left * right for left, right in zip(first_axis, second_axis, strict=True)
        )
        return abs(dot) < AXIS_DOT_TOLERANCE
    except (
        AttributeError,
        ImportError,
        IndexError,
        ReferenceError,
        RuntimeError,
        TypeError,
        ValueError,
    ):
        return False


def axes_collinear_directed(
    first_joint: Any,
    first_side: int,
    second_joint: Any,
    second_side: int,
) -> bool:
    """Prove two live connector Z axes share one directed infinite line."""

    try:
        first_axis, first_origin = _axis_and_origin(
            _global_side_frame(first_joint, first_side)
        )
        second_axis, second_origin = _axis_and_origin(
            _global_side_frame(second_joint, second_side)
        )
        dot = sum(
            left * right for left, right in zip(first_axis, second_axis, strict=True)
        )
        separation = tuple(
            right - left
            for left, right in zip(first_origin, second_origin, strict=True)
        )
        return (
            dot >= 1.0 - AXIS_DOT_TOLERANCE
            and _cross_length(separation, first_axis) <= AXIS_LINE_TOLERANCE_MM
        )
    except (
        AttributeError,
        ImportError,
        IndexError,
        ReferenceError,
        RuntimeError,
        TypeError,
        ValueError,
    ):
        return False
