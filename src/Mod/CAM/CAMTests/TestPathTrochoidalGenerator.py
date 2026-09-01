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

import Constants
import FreeCAD
import Path
import Path.Base.Generator.trochoidal as generator
import CAMTests.PathTestUtils as PathTestUtils

Path.Log.setLevel(Path.Log.Level.INFO, Path.Log.thisModule())
Path.Log.trackModule(Path.Log.thisModule())


def _resetArgs():
    return {
        "p1": FreeCAD.Vector(0, 0, -1),
        "p2": FreeCAD.Vector(30, 0, -1),
        "tool_diameter": 6.0,
        "slot_width": 12.0,
        "stepover": 1.5,
        "direction": "Climb",
    }


def _distanceToLine(point, p1, p2):
    """Perpendicular XY distance from point to the infinite line through p1, p2."""
    line = p2.sub(p1)
    length = math.hypot(line.x, line.y)
    return abs((point.x - p1.x) * line.y - (point.y - p1.y) * line.x) / length


def _projectOnLine(point, p1, p2):
    """Signed XY distance of point along the p1->p2 direction, measured from p1."""
    line = p2.sub(p1)
    length = math.hypot(line.x, line.y)
    return ((point.x - p1.x) * line.x + (point.y - p1.y) * line.y) / length


def _positions(cmds, start):
    """Yield the end position of every move command, tracking modal state."""
    pos = FreeCAD.Vector(start)
    result = []
    for cmd in cmds:
        params = cmd.Parameters
        pos = FreeCAD.Vector(
            params.get("X", pos.x), params.get("Y", pos.y), params.get("Z", pos.z)
        )
        result.append(FreeCAD.Vector(pos))
    return result


class TestPathTrochoidalGenerator(PathTestUtils.PathTestBase):
    def test00(self):
        """Test basic trochoidal generator return"""
        args = _resetArgs()
        cmds = generator.generate(**args)
        self.assertTrue(cmds)
        self.assertTrue(isinstance(cmds, list))
        self.assertTrue(all(isinstance(cmd, Path.Command) for cmd in cmds))

        # first command is a straight move to the start point
        self.assertIn(
            cmds[0].Name, Constants.GCODE_MOVE_STRAIGHT, "Init move should be G1"
        )

        # only feed moves are emitted: G1 advances and arc loops
        allowed = (
            set(Constants.GCODE_MOVE_STRAIGHT)
            | set(Constants.GCODE_MOVE_CW)
            | set(Constants.GCODE_MOVE_CCW)
        )
        self.assertTrue(all(cmd.Name in allowed for cmd in cmds))

    def test01(self):
        """Test value and type checking"""
        # p1/p2 must be vectors
        args = _resetArgs()
        args["p1"] = ""
        self.assertRaises(TypeError, generator.generate, **args)
        args["p1"] = (0, 0, 0)
        self.assertRaises(TypeError, generator.generate, **args)

        args = _resetArgs()
        args["p2"] = [30, 0, -1]
        self.assertRaises(TypeError, generator.generate, **args)

        # endpoints must share the same height
        args = _resetArgs()
        args["p2"] = FreeCAD.Vector(30, 0, 0)
        self.assertRaises(ValueError, generator.generate, **args)

        # endpoints must not coincide
        args = _resetArgs()
        args["p2"] = FreeCAD.Vector(args["p1"])
        self.assertRaises(ValueError, generator.generate, **args)

        # tool_diameter is a length and can not be 0 or negative
        args = _resetArgs()
        args["tool_diameter"] = 0
        self.assertRaises(ValueError, generator.generate, **args)
        args["tool_diameter"] = -6
        self.assertRaises(ValueError, generator.generate, **args)
        args["tool_diameter"] = "6"
        self.assertRaises(TypeError, generator.generate, **args)

        # slot_width must exceed tool_diameter
        args = _resetArgs()
        args["slot_width"] = args["tool_diameter"]
        self.assertRaises(ValueError, generator.generate, **args)
        args["slot_width"] = args["tool_diameter"] - 1
        self.assertRaises(ValueError, generator.generate, **args)
        args["slot_width"] = "12"
        self.assertRaises(TypeError, generator.generate, **args)

        # stepover is a length and can not be 0 or negative
        args = _resetArgs()
        args["stepover"] = 0
        self.assertRaises(ValueError, generator.generate, **args)
        args["stepover"] = -1.5
        self.assertRaises(ValueError, generator.generate, **args)
        args["stepover"] = "1.5"
        self.assertRaises(TypeError, generator.generate, **args)

        # stepover larger than the loop diameter leaves uncut material
        args = _resetArgs()
        args["stepover"] = (args["slot_width"] - args["tool_diameter"]) + 0.1
        self.assertRaises(ValueError, generator.generate, **args)

        # direction should be a string "Climb" or "Conventional"
        args = _resetArgs()
        args["direction"] = "climb"
        self.assertRaises(ValueError, generator.generate, **args)
        args["direction"] = "CW"
        self.assertRaises(ValueError, generator.generate, **args)
        args["direction"] = 3
        self.assertRaises(TypeError, generator.generate, **args)

    def test02(self):
        """Test arc direction mapping"""
        args = _resetArgs()

        # Climb maps to counter-clockwise loops (G3) for an M3 spindle
        args["direction"] = "Climb"
        arcs = [cmd for cmd in generator.generate(**args) if cmd.Name not in ("G1",)]
        self.assertTrue(arcs)
        self.assertTrue(all(cmd.Name in Constants.GCODE_MOVE_CCW for cmd in arcs))

        # Conventional maps to clockwise loops (G2)
        args["direction"] = "Conventional"
        arcs = [cmd for cmd in generator.generate(**args) if cmd.Name not in ("G1",)]
        self.assertTrue(arcs)
        self.assertTrue(all(cmd.Name in Constants.GCODE_MOVE_CW for cmd in arcs))

    def test03(self):
        """Test geometry constraints of generated positions"""
        args = _resetArgs()
        cmds = generator.generate(**args)
        loop_radius = (args["slot_width"] - args["tool_diameter"]) / 2.0

        positions = _positions(cmds, args["p1"])
        for pos in positions:
            # every commanded position stays at slot depth
            self.assertRoughly(pos.z, args["p1"].z)
            # the tool center never strays beyond the loop radius from
            # the centerline, keeping the machined width at slot_width
            dist = _distanceToLine(pos, args["p1"], args["p2"])
            self.assertTrue(dist <= loop_radius + 1e-6)

        # arc endpoints span the full advance range: rear of the first
        # loop to front of the last loop
        projections = [_projectOnLine(pos, args["p1"], args["p2"]) for pos in positions]
        length = args["p2"].sub(args["p1"]).Length
        self.assertRoughly(min(projections), -loop_radius)
        self.assertRoughly(max(projections), length + loop_radius)

    def test04(self):
        """Test loop centers advance monotonically with bounded stepover"""
        args = _resetArgs()
        cmds = generator.generate(**args)
        loop_radius = (args["slot_width"] - args["tool_diameter"]) / 2.0

        # each G1 advance move (after the initial positioning move) targets
        # the rear point of the next loop; recover the loop centers from them
        advances = [cmd for cmd in cmds if cmd.Name in Constants.GCODE_MOVE_STRAIGHT]
        centers = []
        for cmd in advances:
            rear = FreeCAD.Vector(
                cmd.Parameters["X"], cmd.Parameters["Y"], cmd.Parameters["Z"]
            )
            centers.append(_projectOnLine(rear, args["p1"], args["p2"]) + loop_radius)

        # first loop centered at p1, last loop centered at p2
        length = args["p2"].sub(args["p1"]).Length
        self.assertRoughly(centers[0], 0)
        self.assertRoughly(centers[-1], length)

        # monotonic advance, never exceeding the requested stepover
        for prev, curr in zip(centers, centers[1:]):
            self.assertTrue(curr > prev)
            self.assertTrue(curr - prev <= args["stepover"] + 1e-6)

    def test05(self):
        """Test diagonal slot geometry"""
        args = _resetArgs()
        args["p1"] = FreeCAD.Vector(2, 3, -2)
        args["p2"] = FreeCAD.Vector(17, 23, -2)
        cmds = generator.generate(**args)
        loop_radius = (args["slot_width"] - args["tool_diameter"]) / 2.0

        positions = _positions(cmds, args["p1"])
        for pos in positions:
            self.assertRoughly(pos.z, args["p1"].z)
            dist = _distanceToLine(pos, args["p1"], args["p2"])
            self.assertTrue(dist <= loop_radius + 1e-6)

        # arcs must carry center offsets consistent with the loop radius
        for cmd in cmds:
            if cmd.Name in ("G1",):
                continue
            params = cmd.Parameters
            radius = math.hypot(params["I"], params["J"])
            self.assertRoughly(radius, loop_radius)

    def test06(self):
        """Test short slot produces a single advance step"""
        args = _resetArgs()
        args["p2"] = FreeCAD.Vector(1, 0, -1)
        cmds = generator.generate(**args)

        advances = [cmd for cmd in cmds if cmd.Name in Constants.GCODE_MOVE_STRAIGHT]
        # initial positioning move plus exactly one advance
        self.assertEqual(len(advances), 2)

        arcs = [cmd for cmd in cmds if cmd.Name not in ("G1",)]
        # two loops of two arcs each
        self.assertEqual(len(arcs), 4)
