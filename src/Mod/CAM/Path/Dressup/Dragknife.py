# SPDX-License-Identifier: LGPL-2.1-or-later

"""Headless drag-knife path generation shared by CAM GUI and Native mode."""

from __future__ import annotations

import math
from types import SimpleNamespace

import FreeCAD
import Path
import Path.Base.Util as PathUtil
import PathScripts.PathUtils as PathUtils
from lazy_loader.lazy_loader import LazyLoader
from PySide.QtCore import QT_TRANSLATE_NOOP


D = LazyLoader("DraftVecUtils", globals(), "DraftVecUtils")

MOVE_COMMANDS = frozenset(("G1", "G01", "G2", "G02", "G3", "G03"))
RAPID_COMMANDS = frozenset(("G0", "G00"))
ARC_COMMANDS = frozenset(("G2", "G3", "G02", "G03"))


def _path_with_job_center(owner, path=None):
    if isinstance(path, Path.Path):
        result = Path.Path(list(path.Commands or ()))
        result.Center = path.Center
    else:
        result = Path.Path(path) if path else Path.Path()
    job = None
    for candidate in (owner, getattr(owner, "Base", None)):
        if candidate is None:
            continue
        for finder in (PathUtils.findParentJob, PathUtil.timelineParentJob):
            try:
                job = finder(candidate)
            except (AttributeError, TypeError):
                job = None
            if job is not None:
                break
        if job is not None:
            break
    if job is not None:
        result.Center = job.Path.Center
    return result


def _float_property(value):
    return float(getattr(value, "Value", value))


def _arc_offsets(command):
    return (
        float(command.I) if command.I is not None else 0.0,
        float(command.J) if command.J is not None else 0.0,
    )


class ObjectDressup:
    """Parametric drag-knife proxy with deterministic, task-free generation."""

    def __init__(self, obj):
        obj.addProperty(
            "App::PropertyLink",
            "Base",
            "Path",
            QT_TRANSLATE_NOOP("App::Property", "The base toolpath to modify"),
        )
        obj.addProperty(
            "App::PropertyAngle",
            "filterAngle",
            "Path",
            QT_TRANSLATE_NOOP(
                "App::Property",
                "Angles less than filter angle will not receive corner actions",
            ),
        )
        obj.addProperty(
            "App::PropertyFloat",
            "offset",
            "Path",
            QT_TRANSLATE_NOOP(
                "App::Property",
                "Distance the point trails behind the spindle",
            ),
        )
        obj.addProperty(
            "App::PropertyFloat",
            "pivotheight",
            "Path",
            QT_TRANSLATE_NOOP(
                "App::Property",
                "Height to raise during corner action",
            ),
        )
        obj.Proxy = self

    def dumps(self):
        return None

    def loads(self, state):
        return None

    def onChanged(self, obj, prop):
        if prop == "Path" and obj.ViewObject:
            obj.ViewObject.signalChangeIcon()

    def _location(self):
        location = getattr(self, "_generation_location", None)
        if location is None:
            raise RuntimeError("Drag-knife path generation is not active")
        return location

    def _increment(self, name):
        stats = getattr(self, "_generation_stats", None)
        if stats is None:
            raise RuntimeError("Drag-knife path generation is not active")
        stats[name] += 1

    def shortcut(self, queue):
        """Return the shorter corner-twist direction, CW or CCW."""

        location = self._location()
        if queue[1].Name in ARC_COMMANDS:
            i_offset, j_offset = _arc_offsets(queue[1])
            arc_location = FreeCAD.Vector(
                queue[2].x + i_offset,
                queue[2].y + j_offset,
                location["Z"],
            )
            radius_vector = arc_location.sub(queue[1].Placement.Base)
            previous_vector = radius_vector.cross(FreeCAD.Vector(0, 0, 1))
        else:
            previous_vector = queue[1].Placement.Base.sub(queue[2].Placement.Base)

        if queue[0].Name in ARC_COMMANDS:
            i_offset, j_offset = _arc_offsets(queue[0])
            arc_location = FreeCAD.Vector(
                queue[1].x + i_offset,
                queue[1].y + j_offset,
                location["Z"],
            )
            radius_vector = queue[1].Placement.Base.sub(arc_location)
            current_vector = radius_vector.cross(FreeCAD.Vector(0, 0, 1))
        else:
            current_vector = queue[0].Placement.Base.sub(queue[1].Placement.Base)

        if (
            current_vector.x * previous_vector.y
            - current_vector.y * previous_vector.x
            >= 0
        ):
            return "CW"
        return "CCW"

    def segmentAngleXY(self, previous, current, endpos=False, currentZ=0):
        """Return the XY tangent angle of one line or arc segment in radians."""

        if current.Name in ARC_COMMANDS:
            i_offset, j_offset = _arc_offsets(current)
            arc_location = FreeCAD.Vector(
                previous.x + i_offset,
                previous.y + j_offset,
                currentZ,
            )
            radius_vector = (
                arc_location.sub(current.Placement.Base)
                if endpos
                else arc_location.sub(previous.Placement.Base)
            )
            vector = radius_vector.cross(FreeCAD.Vector(0, 0, 1))
            if current.Name in ("G2", "G02"):
                vector = D.rotate2D(vector, math.pi)
        else:
            vector = current.Placement.Base.sub(previous.Placement.Base)
        return D.angle(
            vector,
            FreeCAD.Base.Vector(1, 0, 0),
            FreeCAD.Base.Vector(0, 0, -1),
        )

    def getIncidentAngle(self, queue):
        angle_at_end = math.degrees(self.segmentAngleXY(queue[2], queue[1], True))
        angle_at_start = math.degrees(self.segmentAngleXY(queue[1], queue[0]))
        incident = (angle_at_start - angle_at_end + 360) % 360
        return 360 - incident if incident > 180 else incident

    def arcExtension(self, obj, queue):
        location = self._location()
        offset = _float_property(obj.offset)
        i_offset, j_offset = _arc_offsets(queue[1])
        center = FreeCAD.Base.Vector(
            queue[2].x + i_offset,
            queue[2].y + j_offset,
            location["Z"],
        )
        radius = math.hypot(i_offset, j_offset)
        if radius <= 0.0:
            raise ValueError("Drag-knife source arc has zero radius")
        theta = math.atan2(queue[1].y - center.y, queue[1].x - center.x)
        theta += (-offset if queue[1].Name in ("G2", "G02") else offset) / radius
        end_x = center.x + radius * math.cos(theta)
        end_y = center.y + radius * math.sin(theta)
        offset_vector = center.sub(queue[1].Placement.Base)
        command = Path.Command(
            queue[1].Name,
            {
                "I": offset_vector.x,
                "J": offset_vector.y,
                "X": end_x,
                "Y": end_y,
            },
        )
        location.update(command.Parameters)
        self._increment("arc_extension_count")
        return [command], None

    def arcTwist(self, obj, queue, lastXY, twistCW=False):
        location = self._location()
        pivot_height = _float_property(obj.pivotheight)
        offset = _float_property(obj.offset)
        arc_direction = "G2" if twistCW else "G3"
        cutting_depth = location["Z"]
        retract = Path.Command("G0", {"Z": pivot_height})
        location.update(retract.Parameters)
        i_offset, j_offset = _arc_offsets(queue[0])
        arc_center = FreeCAD.Base.Vector(
            queue[1].x + i_offset,
            queue[1].y + j_offset,
            location["Z"],
        )
        corner = queue[1].Placement.Base
        radius = math.hypot(i_offset, j_offset)
        if radius <= 0.0:
            raise ValueError("Drag-knife source arc has zero radius")
        radius_vector = corner.sub(arc_center)
        segment_angle = D.angle(
            radius_vector,
            FreeCAD.Base.Vector(1, 0, 0),
            FreeCAD.Base.Vector(0, 0, -1),
        )
        theta = offset / radius
        new_angle = (
            segment_angle + theta
            if queue[1].Name in ("G2", "G02")
            else segment_angle - theta
        )
        endpoint = FreeCAD.Base.Vector(
            arc_center.x + radius * math.cos(new_angle),
            arc_center.y + radius * math.sin(new_angle),
            location["Z"],
        )
        offset_vector = corner.sub(lastXY)
        twist = Path.Command(
            arc_direction,
            {
                "X": endpoint.x,
                "Y": endpoint.y,
                "I": offset_vector.x,
                "J": offset_vector.y,
            },
        )
        location.update(twist.Parameters)
        plunge = Path.Command("G1", {"Z": cutting_depth})
        location.update(plunge.Parameters)
        replacement_offset = arc_center.sub(endpoint)
        replacement = Path.Command(
            queue[0].Name,
            {
                "X": queue[0].X,
                "Y": queue[0].Y,
                "I": replacement_offset.x,
                "J": replacement_offset.y,
            },
        )
        self._increment("arc_twist_count")
        return [retract, twist, plunge], replacement

    def lineExtension(self, obj, queue):
        location = self._location()
        offset = _float_property(obj.offset)
        vector = queue[1].Placement.Base.sub(queue[2].Placement.Base)
        segment_angle = D.angle(
            vector,
            FreeCAD.Base.Vector(1, 0, 0),
            FreeCAD.Base.Vector(0, 0, -1),
        )
        command = Path.Command(
            "G1",
            {
                "X": location["X"] + math.cos(segment_angle) * offset,
                "Y": location["Y"] + math.sin(segment_angle) * offset,
            },
        )
        location.update(command.Parameters)
        self._increment("line_extension_count")
        return [command], None

    def lineTwist(self, obj, queue, lastXY, twistCW=False):
        location = self._location()
        pivot_height = _float_property(obj.pivotheight)
        offset = _float_property(obj.offset)
        arc_direction = "G2" if twistCW else "G3"
        cutting_depth = location["Z"]
        retract = Path.Command("G0", {"Z": pivot_height})
        location.update(retract.Parameters)
        corner = queue[1].Placement.Base
        vector = queue[0].Placement.Base.sub(queue[1].Placement.Base)
        segment_angle = D.angle(
            vector,
            FreeCAD.Base.Vector(1, 0, 0),
            FreeCAD.Base.Vector(0, 0, -1),
        )
        end_x = queue[1].x + math.cos(segment_angle) * offset
        end_y = queue[1].y + math.sin(segment_angle) * offset
        offset_vector = corner.sub(lastXY)
        twist = Path.Command(
            arc_direction,
            {
                "X": end_x,
                "Y": end_y,
                "I": offset_vector.x,
                "J": offset_vector.y,
            },
        )
        location.update(twist.Parameters)
        plunge = Path.Command("G1", {"Z": cutting_depth})
        location.update(plunge.Parameters)
        self._increment("line_twist_count")
        return [retract, twist, plunge], None

    def generate(self, obj):
        """Return the exact compensated path and bounded generation metadata."""

        base = getattr(obj, "Base", None)
        if (
            base is None
            or not base.isDerivedFrom("Path::Feature")
            or not getattr(base, "Path", None)
            or not base.Path.Commands
        ):
            return _path_with_job_center(obj), {
                "input_command_count": 0,
                "corner_candidate_count": 0,
                "corner_action_count": 0,
                "corner_action_depths_mm": (),
                "line_extension_count": 0,
                "arc_extension_count": 0,
                "line_twist_count": 0,
                "arc_twist_count": 0,
                "output_command_count": 0,
            }

        previous_location = getattr(self, "_generation_location", None)
        previous_stats = getattr(self, "_generation_stats", None)
        location = {"X": 0.0, "Y": 0.0, "Z": 0.0}
        stats = {
            "input_command_count": len(tuple(base.Path.Commands or ())),
            "corner_candidate_count": 0,
            "corner_action_count": 0,
            "corner_action_depths_mm": set(),
            "line_extension_count": 0,
            "arc_extension_count": 0,
            "line_twist_count": 0,
            "arc_twist_count": 0,
            "output_command_count": 0,
        }
        self._generation_location = location
        self._generation_stats = stats
        output = []
        queue = []
        filter_angle = _float_property(obj.filterAngle)
        try:
            for current in PathUtils.getPathWithPlacement(base).Commands:
                replacement = None
                if current.Name not in MOVE_COMMANDS | RAPID_COMMANDS:
                    output.append(current)
                    continue

                if current.x is None:
                    current.x = location["X"]
                if current.y is None:
                    current.y = location["Y"]
                if current.z is None:
                    current.z = location["Z"]

                if current.Name in RAPID_COMMANDS:
                    if current.z > _float_property(obj.pivotheight) and len(queue) == 3:
                        exit_queue = [current, *queue]
                        if exit_queue[1].Name in ("G01", "G1"):
                            generated, _replacement = self.lineExtension(obj, exit_queue)
                        elif exit_queue[1].Name in ARC_COMMANDS:
                            generated, _replacement = self.arcExtension(obj, exit_queue)
                        else:
                            generated = []
                        output.extend(generated)
                    output.append(current)
                    location.update(current.Parameters)
                    queue = []
                    continue

                changed_xy = False
                if queue:
                    if current.x != queue[0].x or current.y != queue[0].y:
                        queue.insert(0, current)
                        if len(queue) > 3:
                            queue.pop()
                        changed_xy = True
                else:
                    queue = [current]

                if current.z != location["Z"]:
                    output.append(current)
                    location.update(current.Parameters)
                    continue

                if changed_xy and len(queue) == 3:
                    stats["corner_candidate_count"] += 1
                    incident_angle = self.getIncidentAngle(queue)
                    if abs(incident_angle) >= filter_angle:
                        stats["corner_action_count"] += 1
                        stats["corner_action_depths_mm"].add(
                            round(float(location["Z"]), 9)
                        )
                        twist_clockwise = self.shortcut(queue) == "CW"
                        if queue[1].Name in ("G01", "G1"):
                            generated, replacement = self.lineExtension(obj, queue)
                        elif queue[1].Name in ARC_COMMANDS:
                            generated, replacement = self.arcExtension(obj, queue)
                        else:
                            generated = []
                        output.extend(generated)
                        last_xy = generated[-1].Placement.Base if generated else None
                        if last_xy is not None and queue[0].Name in ("G01", "G1"):
                            generated, replacement = self.lineTwist(
                                obj,
                                queue,
                                last_xy,
                                twist_clockwise,
                            )
                            output.extend(generated)
                        elif last_xy is not None and queue[0].Name in ARC_COMMANDS:
                            generated, replacement = self.arcTwist(
                                obj,
                                queue,
                                last_xy,
                                twist_clockwise,
                            )
                            output.extend(generated)
                output.append(current if replacement is None else replacement)
                location.update(current.Parameters)

            result = _path_with_job_center(obj, Path.Path(output))
            stats["output_command_count"] = len(tuple(result.Commands or ()))
            stats["corner_action_depths_mm"] = tuple(
                sorted(stats["corner_action_depths_mm"])
            )
            return result, dict(stats)
        finally:
            if previous_location is None:
                self.__dict__.pop("_generation_location", None)
            else:
                self._generation_location = previous_location
            if previous_stats is None:
                self.__dict__.pop("_generation_stats", None)
            else:
                self._generation_stats = previous_stats

    def execute(self, obj):
        if not PathUtil.activeForOp(obj):
            obj.Path = _path_with_job_center(obj)
            return
        path, stats = self.generate(obj)
        self.lastGenerationStats = stats
        obj.Path = path


def generatePathWithMetadata(
    base,
    *,
    filter_angle_degrees,
    offset_mm,
    pivot_height_mm,
):
    """Generate drag-knife compensation without document mutation."""

    values = (
        float(filter_angle_degrees),
        float(offset_mm),
        float(pivot_height_mm),
    )
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Drag-knife settings must be finite")
    if not 0.0 <= values[0] <= 180.0:
        raise ValueError("Drag-knife filter angle must be between 0 and 180 degrees")
    if not 0.0 < values[1] <= 100.0:
        raise ValueError("Drag-knife offset must be greater than zero and at most 100 mm")
    if not 0.0 <= values[2] <= 100.0:
        raise ValueError("Drag-knife pivot height must be between 0 and 100 mm")
    settings = SimpleNamespace(
        Base=base,
        filterAngle=values[0],
        offset=values[1],
        pivotheight=values[2],
    )
    proxy = object.__new__(ObjectDressup)
    return proxy.generate(settings)
