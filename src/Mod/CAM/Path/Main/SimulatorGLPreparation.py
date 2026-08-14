# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI-independent geometry preparation shared by the GL CAM simulator."""

from __future__ import annotations

import math

from FreeCAD import Placement, Rotation, Vector
import Part


_EPSILON = 0.0001


def is_same(first: float, second: float) -> bool:
    """Return whether two profile coordinates coincide at simulator tolerance."""

    return abs(float(first) - float(second)) < _EPSILON


def radius_at(edge, parameter: float) -> float:
    """Return radial distance from the tool axis at one edge parameter."""

    point = edge.valueAt(parameter)
    return math.hypot(float(point.x), float(point.y))


def find_closest_edge(edges, radius: float, z: float):
    """Find the next connected edge in a revolved tool side profile."""

    for edge in edges:
        first = edge.FirstParameter
        last = edge.LastParameter
        first_radius = radius_at(edge, first)
        first_z = float(edge.valueAt(first).z)
        if is_same(radius, first_radius) and is_same(z, first_z):
            return edge, first, last
        last_radius = radius_at(edge, last)
        last_z = float(edge.valueAt(last).z)
        if is_same(radius, last_radius) and is_same(z, last_z):
            return edge, last, first
        # OCC can omit a flat circular edge. The simulator historically bridges
        # that gap to the next endpoint at the same height.
        if is_same(z, first_z):
            return edge, first, last
        if is_same(z, last_z):
            return edge, last, first
    return None, 0.0, 0.0


def find_topmost_edge(edges):
    """Return the oriented endpoint belonging to the highest side-profile edge."""

    highest_z = -math.inf
    result = (None, 0.0, 0.0)
    for edge in edges:
        first = edge.FirstParameter
        last = edge.LastParameter
        first_z = float(edge.valueAt(first).z)
        if first_z > highest_z:
            result = (edge, first, last)
            highest_z = first_z
        last_z = float(edge.valueAt(last).z)
        if last_z > highest_z:
            result = (edge, last, first)
            highest_z = last_z
    return result


def build_tool_profile(tool_shape, resolution: float) -> list[float]:
    """Build the GL simulator's ordered radius/Z profile from a detached shape."""

    try:
        step_size = float(resolution)
    except (TypeError, ValueError) as exc:
        raise ValueError("Tool-profile resolution must be one finite number") from exc
    if not math.isfinite(step_size) or step_size <= 0.0:
        raise ValueError("Tool-profile resolution must be positive and finite")
    if tool_shape is None or tool_shape.isNull() or not tool_shape.isValid():
        raise ValueError("GL simulation requires a valid ToolBit shape")

    shape = tool_shape.copy()
    shape.Placement = Placement(
        Vector(0, 0, 0),
        Rotation(Vector(0, 0, 1), 0),
        Vector(0, 0, 0),
    )
    remaining = [edge for edge in shape.Edges if not edge.isClosed()]
    edge, first, last = find_topmost_edge(remaining)
    if edge is None:
        raise ValueError("The ToolBit has no open revolved side profile")

    profile = [radius_at(edge, first), float(edge.valueAt(first).z)]
    end_radius = 0.0
    while edge is not None:
        remaining.remove(edge)
        if isinstance(edge.Curve, Part.Circle):
            segment_count = max(1, int(float(edge.Length) / step_size) + 1)
            parameter_step = (last - first) / segment_count
            parameter = first + parameter_step
            for _index in range(segment_count):
                end_radius = radius_at(edge, parameter)
                end_z = float(edge.valueAt(parameter).z)
                profile.extend((end_radius, end_z))
                parameter += parameter_step
        else:
            end_radius = radius_at(edge, last)
            end_z = float(edge.valueAt(last).z)
            profile.extend((end_radius, end_z))

        edge, first, last = find_closest_edge(remaining, end_radius, end_z)
        if edge is None:
            break
        start_radius = radius_at(edge, first)
        if not is_same(start_radius, end_radius):
            profile.extend((start_radius, float(edge.valueAt(first).z)))

    if len(profile) < 4 or len(profile) % 2:
        raise ValueError("The ToolBit produced an invalid simulator side profile")
    if any(not math.isfinite(value) for value in profile):
        raise ValueError("The ToolBit profile contains non-finite coordinates")
    return profile
