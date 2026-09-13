# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the FreeCAD project.

################################################################################
#                                                                              #
#   FreeCAD is free software: you can redistribute it and/or modify            #
#   it under the terms of the GNU Lesser General Public License as             #
#   published by the Free Software Foundation, either version 2.1              #
#   of the License, or (at your option) any later version.                     #
#                                                                              #
#   FreeCAD is distributed in the hope that it will be useful,                 #
#   but WITHOUT ANY WARRANTY; without even the implied warranty                #
#   of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.                    #
#   See the GNU Lesser General Public License for more details.                #
#                                                                              #
#   You should have received a copy of the GNU Lesser General Public           #
#   License along with FreeCAD. If not, see https://www.gnu.org/licenses       #
#                                                                              #
################################################################################

import math

import FreeCAD
import Path

__title__ = "Trochoidal toolpath Generator"
__author__ = "FreeCAD CAM developers"
__url__ = "https://www.freecad.org"
__doc__ = "Generates a constant-engagement trochoidal toolpath along a straight slot"


if False:
    Path.Log.setLevel(Path.Log.Level.DEBUG, Path.Log.thisModule())
    Path.Log.trackModule(Path.Log.thisModule())
else:
    Path.Log.setLevel(Path.Log.Level.INFO, Path.Log.thisModule())


def generate(
    p1: FreeCAD.Vector,
    p2: FreeCAD.Vector,
    tool_diameter: float,
    slot_width: float,
    stepover: float,
    direction: str = "Climb",
) -> list:
    """generate(p1, p2, tool_diameter, slot_width, stepover, direction="Climb")

    Generate a trochoidal (constant-engagement) toolpath for a straight slot.

    The tool center travels a series of full circular loops whose centers
    advance monotonically along the centerline from p1 to p2.  Each loop has
    radius (slot_width - tool_diameter) / 2 so the machined width equals
    slot_width.  The machined slot extends slot_width / 2 beyond p1 and p2
    along the centerline direction; callers that need exact slot ends should
    inset the endpoints accordingly.

        p1: centerline point where the first loop is centered
        p2: centerline point where the last loop is centered (same Z as p1)
        tool_diameter: diameter of the milling tool
        slot_width: total machined width; must exceed tool_diameter
        stepover: advance of the loop center per loop; must be > 0 and
            at most the loop diameter (slot_width - tool_diameter) so that
            successive loops overlap.  The actual advance is distributed
            evenly and is never larger than the requested stepover.
        direction: "Climb" (G3/counter-clockwise loops) or
            "Conventional" (G2/clockwise loops), assuming an M3 spindle.

    Returns a list of Path.Command:
        commands[0]     G1 move to the start point of the first loop
        commands[1:]    pairs of 180 degree arcs (G2 or G3) forming full
                        loops, separated by G1 advance moves along the
                        already-machined centerline

    All commands are at the Z height of p1; depth passes and entry moves
    are the caller's responsibility.
    """

    if not isinstance(p1, FreeCAD.Vector):
        raise TypeError("'p1' is not a point")

    if not isinstance(p2, FreeCAD.Vector):
        raise TypeError("'p2' is not a point")

    if not Path.Geom.isRoughly(p1.z, p2.z):
        raise ValueError("p1 and p2 must be at the same height")

    if not isinstance(tool_diameter, (float, int)):
        raise TypeError("tool_diameter must be a float")

    if tool_diameter <= 0 or Path.Geom.isRoughly(tool_diameter, 0):
        raise ValueError("tool_diameter <= 0")

    if not isinstance(slot_width, (float, int)):
        raise TypeError("slot_width must be a float")

    if slot_width <= tool_diameter or Path.Geom.isRoughly(slot_width, tool_diameter):
        raise ValueError("slot_width must be greater than tool_diameter")

    if not isinstance(stepover, (float, int)):
        raise TypeError("stepover must be a float")

    if stepover <= 0 or Path.Geom.isRoughly(stepover, 0):
        raise ValueError("stepover <= 0")

    if not isinstance(direction, str):
        raise TypeError("direction must be a string")

    if direction not in ("Climb", "Conventional"):
        raise ValueError("Invalid value for parameter 'direction'")

    loop_radius = (slot_width - tool_diameter) / 2.0

    if stepover > 2 * loop_radius and not Path.Geom.isRoughly(
        stepover, 2 * loop_radius
    ):
        raise ValueError("stepover larger than loop diameter leaves uncut material")

    line = p2.sub(p1)
    length = math.hypot(line.x, line.y)

    if Path.Geom.isRoughly(length, 0):
        raise ValueError("p1 and p2 coincide")

    Path.Log.track(
        "(trochoidal: <{}, {}, {}> -> <{}, {}, {}>\n tool diameter {}\n slot width {}\n stepover {}\n direction {})".format(
            p1.x,
            p1.y,
            p1.z,
            p2.x,
            p2.y,
            p2.z,
            tool_diameter,
            slot_width,
            stepover,
            direction,
        )
    )

    # unit vector along the centerline (XY plane)
    dir_x = line.x / length
    dir_y = line.y / length
    z = p1.z

    # For an M3 spindle (clockwise seen from above) the cutter climb-mills
    # when the material sits on the right of the travel direction.  At the
    # leading point of a counter-clockwise (G3) loop the instantaneous
    # travel places the uncut front on the right, so Climb maps to G3.
    arc_cmd_name = "G3" if direction == "Climb" else "G2"

    # distribute the advance evenly so the last loop is centered exactly
    # at p2 and no single advance exceeds the requested stepover
    n_steps = max(1, math.ceil(round(length / stepover, 6)))
    actual_step = length / n_steps

    def loop_commands(cx: float, cy: float) -> list:
        """Full circle around (cx, cy) starting and ending at the rear
        point, split into two 180 degree arcs."""
        rear = (cx - loop_radius * dir_x, cy - loop_radius * dir_y)
        front = (cx + loop_radius * dir_x, cy + loop_radius * dir_y)
        cmd1 = Path.Command(
            arc_cmd_name,
            {
                "X": front[0],
                "Y": front[1],
                "Z": z,
                "I": loop_radius * dir_x,
                "J": loop_radius * dir_y,
            },
        )
        cmd2 = Path.Command(
            arc_cmd_name,
            {
                "X": rear[0],
                "Y": rear[1],
                "Z": z,
                "I": -loop_radius * dir_x,
                "J": -loop_radius * dir_y,
            },
        )
        return [cmd1, cmd2]

    commands = []

    # move to the rear point of the first loop
    start_x = p1.x - loop_radius * dir_x
    start_y = p1.y - loop_radius * dir_y
    commands.append(Path.Command("G1", {"X": start_x, "Y": start_y, "Z": z}))

    for i in range(n_steps + 1):
        cx = p1.x + i * actual_step * dir_x
        cy = p1.y + i * actual_step * dir_y
        if i > 0:
            # advance along the centerline; this segment lies inside
            # material already cleared by the previous loop because the
            # stepover never exceeds the loop diameter
            commands.append(
                Path.Command(
                    "G1",
                    {
                        "X": cx - loop_radius * dir_x,
                        "Y": cy - loop_radius * dir_y,
                        "Z": z,
                    },
                )
            )
        commands.extend(loop_commands(cx, cy))

    return commands
