# SPDX-License-Identifier: LGPL-2.1-or-later

"""Headless, queryable CAM stock-removal and protected-model analysis."""

from __future__ import annotations

import math
import pathlib

import FreeCAD
import Part
import Path
import Path.Geom
import PathScripts.PathUtils as PathUtils


_LINEAR_MOTION = {"G0", "G1", "G2", "G3"}
_CUTTING_MOTION = {"G1", "G2", "G3"}
_CANNED_CYCLES = {"G73", "G81", "G82", "G83", "G85"}
_NON_GEOMETRIC = {
    "G4",
    "G17",
    "G18",
    "G19",
    "G20",
    "G21",
    "G54",
    "G55",
    "G56",
    "G57",
    "G58",
    "G59",
    "G80",
    "G90",
    "G91",
    "G94",
    "G98",
    "G99",
    "M3",
    "M4",
    "M5",
    "M6",
    "M7",
    "M8",
    "M9",
}

_OCC_ENDPOINT_CONFUSION_MM = 1.0e-7
_FLOAT32_MIN_NORMAL = 2.0**-126
_FLOAT32_HALF_SUBNORMAL_STEP = 2.0**-150


def _canonical_command(name):
    value = str(name or "").strip().upper()
    if len(value) == 3 and value[0] in {"G", "M"} and value[1] == "0":
        return value[0] + value[2]
    return value


def _float32_half_ulp(value):
    """Return half of one binary32 step at a finite coordinate."""

    magnitude = abs(float(value))
    if not math.isfinite(magnitude):
        return math.inf
    if magnitude < _FLOAT32_MIN_NORMAL:
        return _FLOAT32_HALF_SUBNORMAL_STEP
    _fraction, exponent = math.frexp(magnitude)
    return 2.0 ** (exponent - 25)


def _simulator_endpoint_tolerance(geometric_end):
    """Bound PathSimulator's binary32 position storage plus OCC confusion."""

    axis_bounds = (
        _float32_half_ulp(coordinate) + _OCC_ENDPOINT_CONFUSION_MM
        for coordinate in (geometric_end.x, geometric_end.y, geometric_end.z)
    )
    return math.sqrt(sum(bound * bound for bound in axis_bounds))


def _simulator_endpoint_matches(simulated_end, geometric_end):
    if not all(
        math.isfinite(float(coordinate))
        for point in (simulated_end, geometric_end)
        for coordinate in (point.x, point.y, point.z)
    ):
        return False
    return float((simulated_end - geometric_end).Length) <= (
        _simulator_endpoint_tolerance(geometric_end)
    )


def _bounds(shape):
    if shape is None or shape.isNull():
        return None
    bb = shape.BoundBox
    values = [
        float(bb.XMin),
        float(bb.YMin),
        float(bb.ZMin),
        float(bb.XMax),
        float(bb.YMax),
        float(bb.ZMax),
    ]
    if not all(math.isfinite(value) for value in values):
        return None
    if bb.XLength < 0.0 or bb.YLength < 0.0 or bb.ZLength < 0.0:
        return None
    return {
        "min": [bb.XMin, bb.YMin, bb.ZMin],
        "max": [bb.XMax, bb.YMax, bb.ZMax],
        "size": [bb.XLength, bb.YLength, bb.ZLength],
    }


def _tool_shape_id(tool):
    raw = str(getattr(tool, "ShapeID", "") or "").strip()
    return pathlib.Path(raw).stem.casefold()


def _quantity(tool, name):
    if name not in getattr(tool, "PropertiesList", []):
        raise ValueError(f"Tool {tool.Name} does not expose required property {name}.")
    value = getattr(tool, name)
    return float(getattr(value, "Value", value))


def _radial_profile(tool, direction, position, axial_extension=0.0):
    """Create the exact supported cutter cross-section in a radial plane."""
    direction = FreeCAD.Vector(direction)
    direction.z = 0.0
    if direction.Length <= 1.0e-12:
        direction = FreeCAD.Vector(1.0, 0.0, 0.0)
    else:
        direction.normalize()
    radial = FreeCAD.Vector(direction.y, -direction.x, 0.0)
    center_bottom = FreeCAD.Vector(position)
    shape_id = _tool_shape_id(tool)
    diameter = _quantity(tool, "Diameter")
    if diameter <= 0.0:
        raise ValueError(f"Tool {tool.Name} has nonpositive Diameter {diameter}.")
    radius = diameter / 2.0

    if shape_id in {"endmill", "ballend"}:
        height = _quantity(tool, "CuttingEdgeHeight")
        if height <= 0.0:
            raise ValueError(
                f"Tool {tool.Name} has nonpositive CuttingEdgeHeight {height}."
            )
        center_top = center_bottom + FreeCAD.Vector(
            0.0, 0.0, height + float(axial_extension)
        )
        outer_top = center_top + radial * radius
        if shape_id == "endmill":
            outer_bottom = center_bottom + radial * radius
            return Part.Wire(
                [
                    Part.makeLine(center_bottom, outer_bottom),
                    Part.makeLine(outer_bottom, outer_top),
                    Part.makeLine(outer_top, center_top),
                ]
            )
        if height <= radius:
            raise ValueError(
                f"Ball-end tool {tool.Name} needs CuttingEdgeHeight greater than "
                f"its radius ({height} <= {radius})."
            )
        midpoint = center_bottom + radial * (radius * 0.5)
        midpoint.z += radius - math.sqrt(radius * radius - (radius * 0.5) ** 2)
        outer_sphere = center_bottom + radial * radius
        outer_sphere.z += radius
        return Part.Wire(
            [
                Part.Edge(Part.Arc(center_bottom, midpoint, outer_sphere)),
                Part.makeLine(outer_sphere, outer_top),
                Part.makeLine(outer_top, center_top),
            ]
        )

    if shape_id == "drill":
        length = _quantity(tool, "Length")
        tip_angle = _quantity(tool, "TipAngle")
        if length <= 0.0 or not 0.0 < tip_angle < 180.0:
            raise ValueError(
                f"Drill {tool.Name} needs positive Length and TipAngle between 0 and 180 degrees."
            )
        tip_height = radius / math.tan(math.radians(tip_angle / 2.0))
        if tip_height >= length:
            raise ValueError(
                f"Drill {tool.Name} tip height {tip_height} is not below Length {length}."
            )
        outer_tip = center_bottom + radial * radius + FreeCAD.Vector(0.0, 0.0, tip_height)
        center_top = center_bottom + FreeCAD.Vector(
            0.0, 0.0, length + float(axial_extension)
        )
        outer_top = center_top + radial * radius
        return Part.Wire(
            [
                Part.makeLine(center_bottom, outer_tip),
                Part.makeLine(outer_tip, outer_top),
                Part.makeLine(outer_top, center_top),
            ]
        )

    if shape_id in {"chamfer", "v-bit"}:
        height = _quantity(tool, "CuttingEdgeHeight")
        included_angle = _quantity(tool, "CuttingEdgeAngle")
        tip_radius = _quantity(tool, "TipDiameter") / 2.0
        if height <= 0.0 or not 0.0 < included_angle < 180.0:
            raise ValueError(
                f"Tool {tool.Name} needs positive CuttingEdgeHeight and "
                "CuttingEdgeAngle between 0 and 180 degrees."
            )
        if tip_radius < 0.0 or tip_radius >= radius:
            raise ValueError(
                f"Tool {tool.Name} needs TipDiameter smaller than Diameter."
            )
        cone_height = (radius - tip_radius) / math.tan(
            math.radians(included_angle / 2.0)
        )
        if cone_height > height:
            raise ValueError(
                f"Tool {tool.Name} cutting height {height} cannot contain its "
                f"specified cone height {cone_height}."
            )
        inner_bottom = center_bottom + radial * tip_radius
        outer_cone = center_bottom + radial * radius + FreeCAD.Vector(0.0, 0.0, cone_height)
        center_top = center_bottom + FreeCAD.Vector(
            0.0, 0.0, height + float(axial_extension)
        )
        outer_top = center_top + radial * radius
        return Part.Wire(
            [
                Part.makeLine(center_bottom, inner_bottom),
                Part.makeLine(inner_bottom, outer_cone),
                Part.makeLine(outer_cone, outer_top),
                Part.makeLine(outer_top, center_top),
            ]
        )

    raise ValueError(
        f"Tool shape {shape_id or '<missing>'} is not supported by native CAM analysis."
    )


def _near_planar_motion(edge, start, end, tolerance):
    """Project sub-tolerance line/arc tilt and return conservative Z growth."""

    z_span = float(edge.BoundBox.ZLength)
    if z_span > float(tolerance):
        return edge, FreeCAD.Vector(start), FreeCAD.Vector(end), 0.0
    base_z = min(float(start.z), float(end.z), float(edge.BoundBox.ZMin))
    start_point = FreeCAD.Vector(start.x, start.y, base_z)
    end_point = FreeCAD.Vector(end.x, end.y, base_z)
    curve_type = type(edge.Curve).__name__
    if curve_type in {"Line", "LineSegment"}:
        if (end_point - start_point).Length <= 1.0e-12:
            return edge, FreeCAD.Vector(start), FreeCAD.Vector(end), 0.0
        return Part.makeLine(start_point, end_point), start_point, end_point, z_span
    if curve_type != "Circle":
        return edge, FreeCAD.Vector(start), FreeCAD.Vector(end), 0.0
    first = float(edge.FirstParameter)
    last = float(edge.LastParameter)
    middle = edge.valueAt((first + last) / 2.0)
    middle_point = FreeCAD.Vector(middle.x, middle.y, base_z)
    span = abs(last - first)
    endpoint_distance = float((end_point - start_point).Length)
    if (
        span >= math.pi
        and abs(span - 2.0 * math.pi) <= 1.0e-6
        and endpoint_distance <= float(tolerance)
    ):
        curve = edge.Curve
        center = FreeCAD.Vector(curve.Center.x, curve.Center.y, base_z)
        projected = Part.makeCircle(
            float(curve.Radius),
            center,
            FreeCAD.Vector(0.0, 0.0, 1.0),
        )
        return projected, start_point, start_point, z_span
    projected = Part.Edge(Part.Arc(start_point, middle_point, end_point))
    return projected, start_point, end_point, z_span


def _axisymmetric_tool_solid(
    tool,
    direction,
    position,
    *,
    axial_extension=0.0,
):
    """Build one supported cutter at ``position`` from its exact radial profile."""

    profile = _radial_profile(
        tool,
        direction,
        position,
        axial_extension=axial_extension,
    )
    vertices = list(profile.Vertexes)
    if len(vertices) < 2:
        raise RuntimeError(f"Tool profile for {tool.Name} has no boundary.")
    closed_profile = Part.Wire(
        list(profile.Edges)
        + [Part.makeLine(vertices[-1].Point, vertices[0].Point)]
    )
    solid = Part.Face(closed_profile).revolve(
        FreeCAD.Vector(position),
        FreeCAD.Vector(0.0, 0.0, 1.0),
        360.0,
    ).removeSplitter()
    if solid.isNull() or not solid.isValid() or len(solid.Solids) != 1:
        raise RuntimeError(f"Tool profile for {tool.Name} is not one valid solid.")
    return solid.Solids[0]


def _circle_half_plane_roots(edge, center, radial):
    """Return exact edge parameters where a profile circle meets radial zero."""

    curve = edge.Curve
    relative_center = FreeCAD.Vector(curve.Center) - center
    offset = float(relative_center.dot(radial))
    cosine = float(curve.Radius) * float(FreeCAD.Vector(curve.XAxis).dot(radial))
    sine = float(curve.Radius) * float(FreeCAD.Vector(curve.YAxis).dot(radial))
    amplitude = math.hypot(cosine, sine)
    if amplitude <= 1.0e-15:
        return []
    target = -offset / amplitude
    if target < -1.0 - 1.0e-12 or target > 1.0 + 1.0e-12:
        return []
    target = max(-1.0, min(1.0, target))
    phase = math.atan2(sine, cosine)
    delta = math.acos(target)
    first = float(edge.FirstParameter)
    last = float(edge.LastParameter)
    lower = min(first, last)
    upper = max(first, last)
    roots = []
    for base in (phase - delta, phase + delta):
        minimum_turn = int(math.floor((lower - base) / (2.0 * math.pi))) - 1
        maximum_turn = int(math.ceil((upper - base) / (2.0 * math.pi))) + 1
        for turn in range(minimum_turn, maximum_turn + 1):
            parameter = base + turn * 2.0 * math.pi
            if lower - 1.0e-12 <= parameter <= upper + 1.0e-12:
                roots.append(max(lower, min(upper, parameter)))
    unique = []
    for parameter in sorted(roots):
        if not unique or abs(parameter - unique[-1]) > 1.0e-11:
            unique.append(parameter)
    return unique


def _profile_edge_roots(edge, center, radial):
    curve_type = type(edge.Curve).__name__
    first = float(edge.FirstParameter)
    last = float(edge.LastParameter)
    first_value = float((edge.valueAt(first) - center).dot(radial))
    last_value = float((edge.valueAt(last) - center).dot(radial))
    if curve_type in {"Line", "LineSegment"}:
        denominator = last_value - first_value
        if abs(denominator) <= 1.0e-15:
            return []
        fraction = -first_value / denominator
        if -1.0e-12 <= fraction <= 1.0 + 1.0e-12:
            return [first + (last - first) * max(0.0, min(1.0, fraction))]
        return []
    if curve_type == "Circle":
        return _circle_half_plane_roots(edge, center, radial)
    raise RuntimeError(
        f"Tool profile curve {curve_type} cannot be clipped for circular motion."
    )


def _trimmed_profile_edge(edge, first, last):
    lower = min(float(first), float(last))
    upper = max(float(first), float(last))
    trimmed = edge.Curve.toShape(lower, upper)
    if float(last) < float(first):
        trimmed = trimmed.reversed()
    return trimmed


def _clipped_revolution_profile(start_profile, mirrored_profile, center, radial):
    """Clip a cutter section at its revolution axis without tessellation."""

    boundary = [
        (edge, float(edge.FirstParameter), float(edge.LastParameter))
        for edge in start_profile.Edges
    ]
    boundary.extend(
        (edge, float(edge.LastParameter), float(edge.FirstParameter))
        for edge in reversed(mirrored_profile.Edges)
    )
    kept = []
    for edge, traversal_start, traversal_end in boundary:
        roots = _profile_edge_roots(edge, center, radial)
        if traversal_end < traversal_start:
            roots.reverse()
        parameters = [traversal_start]
        parameters.extend(
            parameter
            for parameter in roots
            if min(traversal_start, traversal_end) + 1.0e-12
            < parameter
            < max(traversal_start, traversal_end) - 1.0e-12
        )
        parameters.append(traversal_end)
        for first, last in zip(parameters, parameters[1:]):
            if abs(float(last) - float(first)) <= 1.0e-12:
                continue
            middle = (float(first) + float(last)) / 2.0
            if float((edge.valueAt(middle) - center).dot(radial)) < -1.0e-12:
                continue
            kept.append(
                (
                    _trimmed_profile_edge(edge, first, last),
                    FreeCAD.Vector(edge.valueAt(first)),
                    FreeCAD.Vector(edge.valueAt(last)),
                )
            )
    if not kept:
        raise RuntimeError("Circular tool profile lies outside its revolution half-plane.")

    wire_edges = []
    join_tolerance = 1.0e-7
    for index, (edge, start, _end) in enumerate(kept):
        if index:
            previous_end = kept[index - 1][2]
            if float((start - previous_end).Length) > join_tolerance:
                wire_edges.append(Part.makeLine(previous_end, start))
        wire_edges.append(edge)
    final_end = kept[-1][2]
    initial_start = kept[0][1]
    if float((final_end - initial_start).Length) > join_tolerance:
        wire_edges.append(Part.makeLine(final_end, initial_start))
    wire = Part.Wire(wire_edges)
    if not wire.isClosed() or not wire.isValid():
        raise RuntimeError("Clipped circular tool profile is not one valid closed wire.")
    face = Part.Face(wire)
    if face.isNull() or not face.isValid():
        raise RuntimeError("Clipped circular tool profile is not one valid face.")
    return face


def _planar_circular_axisymmetric_sweep(
    edge,
    tool,
    start,
    end,
    *,
    axial_extension=0.0,
):
    """Build the exact planar sweep of a supported axisymmetric cutter.

    Each horizontal cutter slice is a disk.  Its exact sweep along a circular
    arc is an annular sector clipped at radius zero plus the two endpoint disks.
    Constructing that solid explicitly also avoids OCC pipe/revolution failures
    when the cutter radius numerically touches or crosses the arc axis.
    """

    curve = edge.Curve
    center = FreeCAD.Vector(curve.Center)
    center.z = float(start.z)
    radial = FreeCAD.Vector(start) - center
    radial.z = 0.0
    if radial.Length <= 1.0e-12:
        raise RuntimeError("Circular tool path starts at its revolution axis.")
    radial.normalize()
    start_direction = edge.tangentAt(edge.FirstParameter)
    start_profile = _radial_profile(
        tool,
        start_direction,
        start,
        axial_extension=axial_extension,
    )
    rotation = FreeCAD.Matrix()
    rotation.move(FreeCAD.Vector(start).negative())
    rotation.rotateZ(math.pi)
    rotation.move(FreeCAD.Vector(start))
    mirrored_profile = start_profile.copy()
    mirrored_profile.transformShape(rotation, False, True)
    profile_face = _clipped_revolution_profile(
        start_profile,
        mirrored_profile,
        center,
        radial,
    )
    parameter_span = abs(float(edge.LastParameter) - float(edge.FirstParameter))
    full_circle = edge.isClosed() or abs(parameter_span - 2.0 * math.pi) <= 1.0e-7
    angle_degrees = 360.0 if full_circle else math.degrees(parameter_span)
    sector = profile_face.revolve(
        center,
        FreeCAD.Vector(curve.Axis),
        angle_degrees,
    ).removeSplitter()
    if sector.isNull() or not sector.isValid() or len(sector.Solids) != 1:
        raise RuntimeError("Circular tool sector is not one valid solid.")
    if full_circle:
        return sector.Solids[0]

    start_solid = _axisymmetric_tool_solid(
        tool,
        start_direction,
        start,
        axial_extension=axial_extension,
    )
    end_solid = _axisymmetric_tool_solid(
        tool,
        edge.tangentAt(edge.LastParameter),
        end,
        axial_extension=axial_extension,
    )
    caps = (start_solid, end_solid)
    swept = sector.fuse(caps, noElementMap=True).removeSplitter()
    if swept.isNull() or not swept.isValid() or len(swept.Solids) != 1:
        # Projected Path edges can differ from their controller placements by
        # a few nanometres.  OCC then occasionally leaves an empty compound at
        # the otherwise coincident sector/cap boundaries.  Retry only this
        # transient Boolean with a scale-bounded fuzzy tolerance; stock removal
        # still uses the native cutter and the returned sweep remains within
        # OpenCascade's modeling tolerance.
        cutter_radius = _quantity(tool, "Diameter") / 2.0
        boolean_tolerance = max(1.0e-7, cutter_radius * 1.0e-6)
        swept = sector.fuse(
            caps,
            boolean_tolerance,
            noElementMap=True,
        ).removeSplitter()
    if swept.isNull() or not swept.isValid() or len(swept.Solids) != 1:
        raise RuntimeError("Circular tool sweep is not one valid solid.")
    return swept.Solids[0]


def _swept_tool(tool, command, start):
    edge = Path.Geom.edgeForCmd(command, start)
    if edge is None:
        end = Path.Geom.commandEndPoint(command, FreeCAD.Vector(start))
        if float((end - FreeCAD.Vector(start)).Length) <= 1.0e-12:
            return None, end
        raise ValueError(
            f"{command.Name} targets {list(end)} but native edge construction failed."
        )
    end = edge.valueAt(edge.LastParameter)
    if _tool_shape_id(tool) == "endmill":
        radius = _quantity(tool, "Diameter") / 2.0
        height = _quantity(tool, "CuttingEdgeHeight")
        z_span = float(edge.BoundBox.ZLength)
        # PathSimulator returns positions through single-precision placement
        # state, so a native planar arc can acquire tens of nanometres of Z
        # drift between consecutive commands.  Sending that near-planar edge
        # through OCC's general pipe-shell builder creates unorientable cap
        # slivers.  Project only sub-tolerance drift to one plane and enlarge
        # the cutter extrusion by the same span, which conservatively contains
        # the exact swept volume.  Real helical/ramping moves remain on the
        # general path below.
        planar_tolerance = max(1.0e-7, radius * 1.0e-8)
        near_planar = z_span <= planar_tolerance
        base_z = min(float(start.z), float(end.z), float(edge.BoundBox.ZMin))
        sweep_height = height + z_span
        if near_planar and type(edge.Curve).__name__ in {"Line", "LineSegment"}:
            direction = end - FreeCAD.Vector(start)
            direction.z = 0.0
            if direction.Length <= 1.0e-12:
                raise RuntimeError(f"Linear move {command.Name} has zero XY length.")
            direction.normalize()
            normal = FreeCAD.Vector(-direction.y, direction.x, 0.0) * radius
            start_point = FreeCAD.Vector(start.x, start.y, base_z)
            end_point = FreeCAD.Vector(end.x, end.y, base_z)
            polygon = Part.makePolygon(
                [
                    start_point + normal,
                    end_point + normal,
                    end_point - normal,
                    start_point - normal,
                    start_point + normal,
                ]
            )
            center_prism = Part.Face(polygon).extrude(
                FreeCAD.Vector(0.0, 0.0, sweep_height)
            )
            start_cap = Part.makeCylinder(radius, sweep_height, start_point)
            end_cap = Part.makeCylinder(radius, sweep_height, end_point)
            swept_solid = center_prism.fuse([start_cap, end_cap]).removeSplitter()
            if (
                swept_solid.isNull()
                or not swept_solid.isValid()
                or len(swept_solid.Solids) != 1
            ):
                raise RuntimeError(
                    f"Exact linear endmill sweep for {command.Name} is not one valid solid."
                )
            return swept_solid, end
        if near_planar and type(edge.Curve).__name__ == "Circle":
            swept_solid = _planar_circular_endmill_sweep(
                edge,
                radius,
                sweep_height,
                base_z=base_z,
            )
            return swept_solid, end
        if near_planar:
            edge_z = float(edge.Vertexes[0].Point.z)
            planar_edge = edge.copy()
            planar_edge.translate(FreeCAD.Vector(0.0, 0.0, base_z - edge_z))
            swept_area = Part.Wire(planar_edge).makeOffset2D(
                radius,
                join=0,
                fill=True,
                openResult=True,
            )
            swept_solid = swept_area.extrude(
                FreeCAD.Vector(0.0, 0.0, sweep_height)
            )
            if (
                swept_solid.isNull()
                or not swept_solid.isValid()
                or len(swept_solid.Solids) != 1
            ):
                raise RuntimeError(
                    f"Exact planar endmill sweep for {command.Name} is not one valid solid."
                )
            return swept_solid.removeSplitter(), end
    generic_radius = _quantity(tool, "Diameter") / 2.0
    motion_edge, motion_start, motion_end, axial_extension = _near_planar_motion(
        edge,
        FreeCAD.Vector(start),
        end,
        max(1.0e-7, generic_radius * 1.0e-8),
    )
    delta = motion_end - motion_start
    if (
        not motion_edge.isClosed()
        and math.hypot(float(delta.x), float(delta.y)) <= 1.0e-12
    ):
        lower = (
            FreeCAD.Vector(start)
            if float(start.z) <= float(end.z)
            else FreeCAD.Vector(end)
        )
        solid = _axisymmetric_tool_solid(
            tool,
            FreeCAD.Vector(1.0, 0.0, 0.0),
            lower,
            axial_extension=abs(float(delta.z)),
        )
        return solid, end
    if type(motion_edge.Curve).__name__ == "Circle":
        return (
            _planar_circular_axisymmetric_sweep(
                motion_edge,
                tool,
                motion_start,
                motion_end,
                axial_extension=axial_extension,
            ),
            end,
        )
    start_direction = motion_edge.tangentAt(motion_edge.FirstParameter)
    end_direction = motion_edge.tangentAt(motion_edge.LastParameter)
    start_profile = _radial_profile(
        tool,
        start_direction,
        motion_start,
        axial_extension=axial_extension,
    )
    rotation = FreeCAD.Matrix()
    rotation.move(motion_start.negative())
    rotation.rotateZ(math.pi)
    rotation.move(motion_start)
    mirrored_profile = start_profile.copy()
    mirrored_profile.transformShape(rotation, False, True)
    full_profile = Part.Wire([start_profile, mirrored_profile])
    path_wire = Part.Wire(motion_edge)
    if motion_edge.isClosed():
        solid = path_wire.makePipeShell([full_profile], True, True).removeSplitter()
        if solid.isNull() or not solid.isValid() or len(solid.Solids) != 1:
            raise RuntimeError(
                f"Closed-path tool sweep for {command.Name} is not one valid solid."
            )
        return solid, end
    path_shell = path_wire.makePipeShell([full_profile], False, True)
    start_cap = start_profile.revolve(
        motion_start,
        FreeCAD.Vector(0.0, 0.0, 1.0),
        -180.0,
    )
    end_profile = _radial_profile(
        tool,
        end_direction,
        motion_end,
        axial_extension=axial_extension,
    )
    end_cap = end_profile.revolve(
        motion_end,
        FreeCAD.Vector(0.0, 0.0, 1.0),
        180.0,
    )
    shell = Part.makeShell(start_cap.Faces + path_shell.Faces + end_cap.Faces)
    solid = Part.makeSolid(shell).removeSplitter()
    if solid.isNull() or not solid.isValid() or len(solid.Solids) != 1:
        raise RuntimeError(f"Tool sweep for {command.Name} is not one valid solid.")
    return solid, end


def _planar_circular_endmill_sweep(
    edge,
    tool_radius,
    tool_height,
    *,
    base_z=None,
):
    curve = edge.Curve
    path_radius = float(curve.Radius)
    if path_radius <= 0.0:
        raise RuntimeError("Circular tool path has nonpositive radius.")
    center = FreeCAD.Vector(curve.Center)
    z = (
        float(edge.valueAt(edge.FirstParameter).z)
        if base_z is None
        else float(base_z)
    )
    center.z = z
    outer_radius = path_radius + float(tool_radius)
    inner_radius = max(0.0, path_radius - float(tool_radius))
    radial_tolerance = max(1.0e-7, outer_radius * 1.0e-9)
    if inner_radius <= radial_tolerance:
        # At the native modeling tolerance the swept disk reaches the arc
        # center.  Keeping a sub-tolerance inner arc creates a needle-shaped
        # face that OCC cannot orient reliably during the cap union.
        inner_radius = 0.0

    # OCC can retain a generated full-circle move as an open trimmed edge when
    # its numerically reconstructed end point differs from the start by a few
    # tenths of a micron.  Building that almost-closed move as a sector creates
    # a sliver whose coincident caps are unorientable during fusion.  Require
    # both a complete angular turn and coincident end points before using the
    # exact annular sweep; a genuinely short arc with coincident points must not
    # be promoted to a full circle.
    first_parameter = float(edge.FirstParameter)
    last_parameter = float(edge.LastParameter)
    parameter_span = abs(last_parameter - first_parameter)
    start_point = edge.valueAt(first_parameter)
    end_point = edge.valueAt(last_parameter)
    endpoint_tolerance = max(
        radial_tolerance,
        (path_radius + float(tool_radius)) * 1.0e-7,
    )
    complete_turn = (
        parameter_span >= math.pi
        and abs(parameter_span - 2.0 * math.pi) <= 1.0e-6
        and float((end_point - start_point).Length) <= endpoint_tolerance
    )
    if edge.isClosed() or complete_turn:
        swept = Part.makeCylinder(outer_radius, tool_height, center)
        if inner_radius > 1.0e-12:
            swept = swept.cut(Part.makeCylinder(inner_radius, tool_height, center))
        if swept.isNull() or not swept.isValid() or len(swept.Solids) != 1:
            raise RuntimeError("Full-circle endmill sweep is not one valid solid.")
        return swept.removeSplitter()

    middle_parameter = (first_parameter + last_parameter) / 2.0

    def radial_point(parameter, radius):
        point = edge.valueAt(parameter)
        radial = FreeCAD.Vector(point.x - center.x, point.y - center.y, 0.0)
        if radial.Length <= 1.0e-12:
            raise RuntimeError("Circular tool path contains a point at its center.")
        radial.normalize()
        return center + radial * radius

    outer_start = radial_point(first_parameter, outer_radius)
    outer_middle = radial_point(middle_parameter, outer_radius)
    outer_end = radial_point(last_parameter, outer_radius)
    outer_arc = Part.Edge(Part.Arc(outer_start, outer_middle, outer_end))
    boundary = [outer_arc]
    if inner_radius > 1.0e-12:
        inner_start = radial_point(first_parameter, inner_radius)
        inner_middle = radial_point(middle_parameter, inner_radius)
        inner_end = radial_point(last_parameter, inner_radius)
        boundary.extend(
            [
                Part.makeLine(outer_end, inner_end),
                Part.Edge(Part.Arc(inner_end, inner_middle, inner_start)),
                Part.makeLine(inner_start, outer_start),
            ]
        )
    else:
        boundary.extend(
            [
                Part.makeLine(outer_end, center),
                Part.makeLine(center, outer_start),
            ]
        )
    sector = Part.Face(Part.Wire(boundary)).extrude(
        FreeCAD.Vector(0.0, 0.0, tool_height)
    )
    start = edge.valueAt(first_parameter)
    end = edge.valueAt(last_parameter)
    swept = sector.fuse(
        [
            Part.makeCylinder(tool_radius, tool_height, start),
            Part.makeCylinder(tool_radius, tool_height, end),
        ]
    ).removeSplitter()
    if swept.isNull() or not swept.isValid() or len(swept.Solids) != 1:
        raise RuntimeError("Circular endmill sweep is not one valid solid.")
    return swept


def _expanded_cycle(command, position):
    params = dict(command.Parameters)
    missing = [name for name in ("X", "Y", "Z", "R") if params.get(name) is None]
    if missing:
        raise ValueError(
            f"Canned cycle {command.Name} is missing parameters: {', '.join(missing)}."
        )
    x = float(params["X"])
    y = float(params["Y"])
    z = float(params["Z"])
    retract = float(params["R"])
    return [
        Path.Command("G0", {"Z": retract}),
        Path.Command("G0", {"X": x, "Y": y, "Z": retract}),
        Path.Command("G1", {"X": x, "Y": y, "Z": z}),
        Path.Command("G0", {"X": x, "Y": y, "Z": retract}),
    ]


def analyze_operation(
    job,
    operation,
    simulation_resolution_mm,
    volume_tolerance=1.0e-7,
):
    """Simulate one generated operation and return structured native facts.

    Stock removal is computed by FreeCAD's native PathSimulator height field.
    Protected-model collision uses OpenCascade cutter-sweep intersections.
    Holder/fixture collision is reported separately when those shapes are not
    represented by the standard CAM job.
    """
    stock_obj = getattr(job, "Stock", None)
    stock = getattr(stock_obj, "Shape", None)
    if stock is None or stock.isNull() or not stock.isValid() or len(stock.Solids) != 1:
        raise ValueError("CAM analysis requires one valid solid stock shape.")
    controller = getattr(operation, "ToolController", None)
    tool = getattr(controller, "Tool", None)
    if controller is None or tool is None:
        raise ValueError("CAM analysis requires an explicit tool controller and tool bit.")
    model_objects = list(getattr(getattr(job, "Model", None), "Group", []) or [])
    model_shapes = [obj.Shape for obj in model_objects]
    if not model_shapes or any(shape.isNull() or not shape.isValid() for shape in model_shapes):
        raise ValueError("CAM analysis requires valid protected model shapes in the job.")
    protected_model = Part.makeCompound(model_shapes)
    commands = list(PathUtils.getPathWithPlacement(operation).Commands)
    if not commands:
        raise ValueError("CAM analysis requires a generated nonempty toolpath.")
    resolution = float(simulation_resolution_mm)
    if not math.isfinite(resolution) or resolution <= 0.0:
        raise ValueError("simulation_resolution_mm must be finite and positive.")
    grid_x = int(math.ceil(stock.BoundBox.XLength / resolution)) + 1
    grid_y = int(math.ceil(stock.BoundBox.YLength / resolution)) + 1
    grid_cells = grid_x * grid_y
    if grid_cells > 4_000_000:
        minimum_resolution = math.sqrt(
            stock.BoundBox.XLength * stock.BoundBox.YLength / 4_000_000.0
        )
        raise ValueError(
            "Requested CAM simulation exceeds 4,000,000 height-field cells; "
            f"use simulation_resolution_mm >= {minimum_resolution:.6g}."
        )
    tool_shape = getattr(tool, "Shape", None)
    if (
        tool_shape is None
        or tool_shape.isNull()
        or not tool_shape.isValid()
        or len(tool_shape.Solids) != 1
    ):
        raise ValueError("CAM analysis requires one valid solid tool-bit shape.")
    import PathSimulator

    simulator = PathSimulator.PathSim()
    simulator.BeginSimulation(stock, resolution)
    simulator.SetToolShape(tool_shape, resolution)
    endpoint_tolerance = 0.0

    position = FreeCAD.Placement(
        FreeCAD.Vector(0.0, 0.0, stock.BoundBox.ZMax),
        FreeCAD.Rotation(),
    )
    executed = []
    unsupported = []
    collision_commands = []
    collision_shapes = []
    cut_sweep_count = 0

    def apply(command, source_index, rapid):
        nonlocal position, cut_sweep_count, endpoint_tolerance
        start = FreeCAD.Vector(position.Base)
        try:
            sweep, geometric_end = _swept_tool(tool, command, start)
        except Exception as exc:
            raise RuntimeError(
                f"Command {source_index} {_canonical_command(command.Name)} "
                f"with parameters {dict(command.Parameters)} failed tool-sweep "
                f"construction: {exc}"
            ) from exc
        position = simulator.ApplyCommand(position, command)
        command_endpoint_tolerance = _simulator_endpoint_tolerance(geometric_end)
        endpoint_tolerance = max(endpoint_tolerance, command_endpoint_tolerance)
        if not _simulator_endpoint_matches(position.Base, geometric_end):
            raise RuntimeError(
                f"Native simulator endpoint {list(position.Base)} diverged from "
                f"Path.Geom endpoint {list(geometric_end)} at command {source_index} "
                f"with parameters {dict(command.Parameters)}."
            )
        if sweep is None:
            executed.append(
                {
                    "source_index": source_index,
                    "command": _canonical_command(command.Name),
                    "rapid": bool(rapid),
                    "no_motion": True,
                    "sweep_bounds": None,
                }
            )
            return
        overlap = sweep.common(protected_model)
        overlap_volume = float(overlap.Volume) if not overlap.isNull() else 0.0
        if overlap_volume > float(volume_tolerance):
            collision_shapes.append(overlap)
            collision_commands.append(
                {
                    "command_index": source_index,
                    "command": _canonical_command(command.Name),
                    "rapid": bool(rapid),
                    "protected_model_volume_mm3": overlap_volume,
                    "bounds": _bounds(overlap),
                }
            )
        if not rapid:
            cut_sweep_count += 1
        executed.append(
            {
                "source_index": source_index,
                "command": _canonical_command(command.Name),
                "rapid": bool(rapid),
                "sweep_bounds": _bounds(sweep),
            }
        )

    for index, command in enumerate(commands):
        name = _canonical_command(command.Name)
        if not name or name.startswith("("):
            continue
        if name in _LINEAR_MOTION:
            apply(command, index, rapid=name == "G0")
            continue
        if name in _CANNED_CYCLES:
            for expanded in _expanded_cycle(command, position.Base):
                apply(expanded, index, rapid=_canonical_command(expanded.Name) == "G0")
            continue
        if name in _NON_GEOMETRIC:
            continue
        unsupported.append({"command_index": index, "command": name})

    if unsupported:
        return {
            "complete": False,
            "stage": "command_interpretation",
            "error": {
                "code": "unsupported_path_commands",
                "message": "The generated path contains commands not supported by native analysis.",
            },
            "unsupported_commands": unsupported,
            "executed_sweeps": len(executed),
        }
    if cut_sweep_count == 0:
        return {
            "complete": False,
            "stage": "stock_removal",
            "error": {
                "code": "no_cutting_sweeps",
                "message": "The path contains no analyzable cutting sweeps.",
            },
            "unsupported_commands": [],
            "executed_sweeps": len(executed),
        }

    simulation_stats = simulator.GetSimulationStats()
    if int(simulation_stats.get("unsupported_commands", 0) or 0) != 0:
        return {
            "complete": False,
            "stage": "native_stock_simulation",
            "error": {
                "code": "native_simulator_unsupported_commands",
                "message": "PathSimulator received unsupported motion commands.",
            },
            "native_simulation": simulation_stats,
        }
    if int(simulation_stats.get("cut_commands", 0) or 0) != cut_sweep_count:
        return {
            "complete": False,
            "stage": "native_stock_simulation",
            "error": {
                "code": "native_simulator_command_mismatch",
                "message": "PathSimulator cutting-command count differs from interpreted path state.",
            },
            "expected_cut_commands": cut_sweep_count,
            "native_simulation": simulation_stats,
        }
    collision_shape = None
    collision_volume = 0.0
    collision_volume_aggregation = "none"
    collision_aggregation_warning = ""
    if collision_shapes:
        # A pocket can yield hundreds of mutually overlapping command
        # intersections.  A monolithic OCC multiFuse is both unbounded and can
        # create an invalid result from otherwise valid inputs.  Preserve the
        # exact union for a bounded set; otherwise report a clearly identified
        # conservative sum and compound bounds.  The collision verdict remains
        # conservative in either case and each exact per-command volume is
        # retained in ``commands`` below.
        collision_shape = Part.makeCompound(collision_shapes)
        collision_volume = sum(float(shape.Volume) for shape in collision_shapes)
        collision_volume_aggregation = "per_command_sum_conservative"
        if len(collision_shapes) <= 32:
            try:
                union = collision_shapes[0]
                if len(collision_shapes) > 1:
                    union = union.multiFuse(collision_shapes[1:]).removeSplitter()
                if union.isNull() or not union.isValid():
                    raise RuntimeError("OCC returned a null or invalid union")
                collision_shape = union
                collision_volume = float(union.Volume)
                collision_volume_aggregation = "occ_union"
            except Exception as exc:
                collision_aggregation_warning = (
                    "Exact bounded OCC union was unavailable; using the "
                    f"conservative per-command sum: {type(exc).__name__}: {exc}"
                )
    return {
        "complete": True,
        "stage": "complete",
        "error": None,
        "simulation_scope": "single_operation_against_initial_job_stock",
        "command_count": len(commands),
        "executed_sweeps": len(executed),
        "cutting_sweeps": cut_sweep_count,
        "unsupported_commands": [],
        "stock": {
            "object": stock_obj.Name,
            "method": "PathSimulator height field",
            "volume_method": "height_field_cell_quadrature",
            "endpoint_tolerance_mm": endpoint_tolerance,
            **simulation_stats,
        },
        "collision": {
            "protected_model_checked": True,
            "method": "OpenCascade cutter-sweep intersection",
            "volume_tolerance_mm3": float(volume_tolerance),
            "protected_model_collision": collision_volume > float(volume_tolerance),
            "protected_model_volume_mm3": collision_volume,
            "protected_model_volume_aggregation": collision_volume_aggregation,
            "protected_model_aggregation_warning": collision_aggregation_warning,
            "protected_model_bounds": _bounds(collision_shape),
            "commands": collision_commands,
            "holder_checked": False,
            "fixture_checked": False,
            "unavailable_checks": [
                "holder collision: the job has no holder shape",
                "fixture collision: the job has no fixture shapes",
            ],
        },
    }
