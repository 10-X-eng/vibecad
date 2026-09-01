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

import numpy

import Path
import Path.Base.Generator.steep_shallow as generator
import CAMTests.PathTestUtils as PathTestUtils

Path.Log.setLevel(Path.Log.Level.INFO, Path.Log.thisModule())
Path.Log.trackModule(Path.Log.thisModule())


SAFE_HEIGHT = 20.0


def _hemisphere(radius=10.0, extent=15.0, n=61):
    """Hemisphere of `radius` on a flat plane at z=0.

    Analytically known slopes: steep near the equator, shallow at the
    cap and on the surrounding plane.
    """
    xs = numpy.linspace(-extent, extent, n)
    ys = numpy.linspace(-extent, extent, n)
    xx, yy = numpy.meshgrid(xs, ys, indexing="ij")
    rr = numpy.hypot(xx, yy)
    z = numpy.where(rr < radius, numpy.sqrt(numpy.maximum(radius**2 - rr**2, 0.0)), 0.0)
    return z, xs, ys


def _cone(height=8.0, extent=10.0, n=41):
    """45-degree cone: wall slope is exactly 45 degrees everywhere."""
    xs = numpy.linspace(-extent, extent, n)
    ys = numpy.linspace(-extent, extent, n)
    xx, yy = numpy.meshgrid(xs, ys, indexing="ij")
    rr = numpy.hypot(xx, yy)
    z = numpy.maximum(height - rr, 0.0)
    return z, xs, ys


SF_WALL_X = 20.0
SF_FILLET_R = 2.0
SF_WALL_H = 10.0


def _step_fillet(wall_x=SF_WALL_X, fillet_r=SF_FILLET_R, height=SF_WALL_H):
    """Floor at z=0 meeting a step of `height` at x=`wall_x` through an
    inside fillet of radius `fillet_r`, extruded along Y.

    A ball-end reference tool with radius > `fillet_r` cannot reach the
    fillet: with height > tool radius the wall is the binding contact,
    so its closing equals the surface everywhere except the corner band
    wall_x - tool_radius < x < wall_x.
    """
    xs = numpy.arange(0.0, 30.0 + 1e-9, 0.5)
    ys = numpy.arange(0.0, 10.0 + 1e-9, 0.5)
    profile = numpy.zeros_like(xs)
    for i, x in enumerate(xs):
        if x >= wall_x:
            profile[i] = height
        elif x > wall_x - fillet_r:
            profile[i] = fillet_r - numpy.sqrt(
                fillet_r**2 - (x - (wall_x - fillet_r)) ** 2
            )
    z = numpy.repeat(profile[:, None], ys.size, axis=1)
    return z, xs, ys


def _resetArgs():
    z, xs, ys = _hemisphere()
    return {
        "heightfield": z,
        "xs": xs,
        "ys": ys,
        "slope_threshold": 45.0,
        "stepdown": 2.0,
        "stepover": 1.0,
        "safe_height": SAFE_HEIGHT,
        "horiz_feed": 500.0,
        "vert_feed": 200.0,
    }


def _bilinear(z, xs, ys, x, y):
    """Bilinearly interpolated heightfield value at (x, y)."""
    i = int(numpy.searchsorted(xs, x, side="right") - 1)
    i = min(max(i, 0), xs.size - 2)
    j = int(numpy.searchsorted(ys, y, side="right") - 1)
    j = min(max(j, 0), ys.size - 2)
    tx = min(max((x - xs[i]) / (xs[i + 1] - xs[i]), 0.0), 1.0)
    ty = min(max((y - ys[j]) / (ys[j + 1] - ys[j]), 0.0), 1.0)
    return (
        z[i, j] * (1 - tx) * (1 - ty)
        + z[i + 1, j] * tx * (1 - ty)
        + z[i, j + 1] * (1 - tx) * ty
        + z[i + 1, j + 1] * tx * ty
    )


def _splitPasses(cmds):
    """Split the command list into cutting passes at G0 boundaries.

    Returns (passes, g0_zs): each pass is the list of (x, y, z) G1
    endpoints (plunge endpoint first), g0_zs are Z values of all rapids.
    """
    passes = []
    g0_zs = []
    cur = []
    x = y = z = None
    for cmd in cmds:
        params = cmd.Parameters
        x = params.get("X", x)
        y = params.get("Y", y)
        z = params.get("Z", z)
        if cmd.Name in ("G0", "G00"):
            g0_zs.append(z)
            if cur:
                passes.append(cur)
                cur = []
        else:
            cur.append((x, y, z))
    if cur:
        passes.append(cur)
    return passes, g0_zs


def _isShallowPass(pts):
    """Shallow parallel passes run along X at constant Y; steep contour
    passes vary Y. Note constant-Z is NOT a valid discriminator: shallow
    passes over flat regions are also at constant Z."""
    return len(set(round(p[1], 9) for p in pts)) == 1


class TestPathSteepShallowGenerator(PathTestUtils.PathTestBase):
    def test00(self):
        """Test basic steep/shallow generator return"""
        args = _resetArgs()
        cmds = generator.generate(**args)
        self.assertTrue(cmds)
        self.assertTrue(isinstance(cmds, list))
        self.assertTrue(all(isinstance(cmd, Path.Command) for cmd in cmds))
        # only rapids and straight feed moves are emitted
        self.assertTrue(all(cmd.Name in ("G0", "G1") for cmd in cmds))
        # program ends with a retract to safe height
        self.assertEqual(cmds[-1].Name, "G0")
        self.assertRoughly(cmds[-1].Parameters["Z"], SAFE_HEIGHT)

    def test01(self):
        """Test value and type checking"""
        # heightfield must be a numeric 2-D array matching (len(xs), len(ys))
        args = _resetArgs()
        args["heightfield"] = "surface"
        self.assertRaises(TypeError, generator.generate, **args)

        args = _resetArgs()
        args["heightfield"] = args["xs"]
        self.assertRaises(ValueError, generator.generate, **args)

        args = _resetArgs()
        args["heightfield"] = args["heightfield"][:-1]
        self.assertRaises(ValueError, generator.generate, **args)

        # grid coordinates must be 1-D, strictly increasing, 2+ points
        args = _resetArgs()
        args["xs"] = args["xs"][::-1]
        self.assertRaises(ValueError, generator.generate, **args)

        args = _resetArgs()
        args["heightfield"] = args["heightfield"][:1]
        args["xs"] = args["xs"][:1]
        self.assertRaises(ValueError, generator.generate, **args)

        # slope_threshold is an angle within 0..90 degrees
        args = _resetArgs()
        args["slope_threshold"] = -1.0
        self.assertRaises(ValueError, generator.generate, **args)
        args["slope_threshold"] = 90.5
        self.assertRaises(ValueError, generator.generate, **args)
        args["slope_threshold"] = "45"
        self.assertRaises(TypeError, generator.generate, **args)
        args["slope_threshold"] = True
        self.assertRaises(TypeError, generator.generate, **args)

        # stepdown and stepover are lengths and can not be 0 or negative
        args = _resetArgs()
        args["stepdown"] = 0.0
        self.assertRaises(ValueError, generator.generate, **args)
        args = _resetArgs()
        args["stepover"] = -1.0
        self.assertRaises(ValueError, generator.generate, **args)

        # boundary_overlap must not be negative
        args = _resetArgs()
        args["boundary_overlap"] = -0.5
        self.assertRaises(ValueError, generator.generate, **args)

        # safe_height must clear the surface
        args = _resetArgs()
        args["safe_height"] = 5.0
        self.assertRaises(ValueError, generator.generate, **args)

        # feeds must be positive
        args = _resetArgs()
        args["horiz_feed"] = 0.0
        self.assertRaises(ValueError, generator.generate, **args)
        args = _resetArgs()
        args["vert_feed"] = -200.0
        self.assertRaises(ValueError, generator.generate, **args)

        # direction should be a string "Climb" or "Conventional"
        args = _resetArgs()
        args["direction"] = "climb"
        self.assertRaises(ValueError, generator.generate, **args)
        args["direction"] = 3
        self.assertRaises(TypeError, generator.generate, **args)

        # an entirely-NaN heightfield has no surface to follow
        args = _resetArgs()
        args["heightfield"] = numpy.full_like(args["heightfield"], float("nan"))
        self.assertRaises(ValueError, generator.generate, **args)

    def test02(self):
        """Test mixed hemisphere run emits both strategies without gouging"""
        args = _resetArgs()
        z, xs, ys = args["heightfield"], args["xs"], args["ys"]
        cmds = generator.generate(**args)
        passes, g0_zs = _splitPasses(cmds)

        # linking rapids never dip below safe height
        for g0_z in g0_zs:
            self.assertTrue(g0_z >= SAFE_HEIGHT - 1e-9)

        kinds = ["shallow" if _isShallowPass(pts) else "steep" for pts in passes]
        self.assertIn("steep", kinds)
        self.assertIn("shallow", kinds)

        # steep passes are all emitted before shallow passes
        first_shallow = kinds.index("shallow")
        self.assertNotIn("steep", kinds[first_shallow:])

        for pts, kind in zip(passes, kinds):
            if kind == "steep":
                # constant-Z contour pass
                zs = set(round(p[2], 9) for p in pts)
                self.assertEqual(len(zs), 1)
            else:
                # surface-following pass: on the CL surface within tolerance
                for x, y, zz in pts[1:]:
                    self.assertRoughly(zz, _bilinear(z, xs, ys, x, y), error=1e-6)
            # never below the sampled surface: gouge-free
            for x, y, zz in pts:
                self.assertTrue(zz >= _bilinear(z, xs, ys, x, y) - 1e-6)

    def test03(self):
        """Test threshold degenerate cases: 0 pure waterline, 90 pure surface"""
        args = _resetArgs()
        z, xs, ys = args["heightfield"], args["xs"], args["ys"]

        args["slope_threshold"] = 0.0
        passes, _ = _splitPasses(generator.generate(**args))
        self.assertTrue(passes)
        for pts in passes:
            zs = set(round(p[2], 9) for p in pts)
            self.assertEqual(len(zs), 1)

        args["slope_threshold"] = 90.0
        passes, _ = _splitPasses(generator.generate(**args))
        self.assertTrue(passes)
        for pts in passes:
            self.assertTrue(_isShallowPass(pts))
            for x, y, zz in pts[1:]:
                self.assertRoughly(zz, _bilinear(z, xs, ys, x, y), error=1e-6)

    def test04(self):
        """Test boundary overlap extends shallow passes past the boundary"""
        args = _resetArgs()
        z, xs, ys = args["heightfield"], args["xs"], args["ys"]
        cell = xs[1] - xs[0]
        overlap = 1.0

        def capRunExtent(cmds):
            """Max |x| of the shallow pass crossing the cap at y == 0."""
            passes, _ = _splitPasses(cmds)
            best = None
            for pts in passes:
                if not _isShallowPass(pts):
                    continue
                if all(abs(p[1]) < 1e-9 for p in pts) and any(
                    abs(p[0]) < cell for p in pts
                ):
                    best = max(abs(p[0]) for p in pts)
            return best

        base = capRunExtent(generator.generate(**args))
        args["boundary_overlap"] = overlap
        cmds = generator.generate(**args)
        extended = capRunExtent(cmds)

        self.assertIsNotNone(base)
        self.assertIsNotNone(extended)
        # the central cap run reaches at least `overlap` further into the
        # steep region, but no more than one extra grid cell beyond it
        growth = extended - base
        self.assertTrue(growth >= overlap - 1e-9)
        self.assertTrue(growth <= overlap + cell + 1e-9)

        # the extended passes remain gouge-free
        passes, _ = _splitPasses(cmds)
        for pts in passes:
            for x, y, zz in pts:
                self.assertTrue(zz >= _bilinear(z, xs, ys, x, y) - 1e-6)

    def test05(self):
        """Test Conventional reverses every pass traversal from Climb"""
        args = _resetArgs()
        climb, g0_climb = _splitPasses(generator.generate(**args))
        args["direction"] = "Conventional"
        conv, g0_conv = _splitPasses(generator.generate(**args))

        self.assertEqual(len(climb), len(conv))
        for a, b in zip(climb, conv):
            pts_a = [(round(p[0], 9), round(p[1], 9), round(p[2], 9)) for p in a]
            pts_b = [(round(p[0], 9), round(p[1], 9), round(p[2], 9)) for p in b]
            self.assertEqual(pts_a, list(reversed(pts_b)))

        # linking remains at safe height in both directions
        for g0_z in g0_climb + g0_conv:
            self.assertTrue(g0_z >= SAFE_HEIGHT - 1e-9)

    def test06(self):
        """Test 45 degree cone wall classification brackets the threshold"""
        z, xs, ys = _cone()
        args = _resetArgs()
        args["heightfield"], args["xs"], args["ys"] = z, xs, ys

        # threshold below the wall angle: the wall is steep -> contours
        args["slope_threshold"] = 40.0
        passes, _ = _splitPasses(generator.generate(**args))
        self.assertTrue(any(not _isShallowPass(pts) for pts in passes))

        # threshold above the wall angle: everything is shallow
        args["slope_threshold"] = 50.0
        passes, _ = _splitPasses(generator.generate(**args))
        self.assertTrue(passes)
        self.assertTrue(all(_isShallowPass(pts) for pts in passes))

    def test07(self):
        """Test NaN heightfield entries are tolerated as minimum height"""
        args = _resetArgs()
        z = args["heightfield"].copy()
        z[0, 0] = float("nan")
        z[-1, -1] = float("nan")
        args["heightfield"] = z
        cmds = generator.generate(**args)
        self.assertTrue(cmds)
        # NaN cells collapse to the minimum finite height: no output
        # position may lie below it
        z_min = numpy.nanmin(z)
        for cmd in cmds:
            if cmd.Name == "G1" and "Z" in cmd.Parameters:
                self.assertTrue(cmd.Parameters["Z"] >= z_min - 1e-9)

    def test08(self):
        """Test rest disabled (None) output is identical to a plain run"""
        args = _resetArgs()
        base = generator.generate(**args)
        args["rest_reference_diameter"] = None
        rest_off = generator.generate(**args)

        self.assertEqual(len(base), len(rest_off))
        for a, b in zip(base, rest_off):
            self.assertEqual(a.Name, b.Name)
            self.assertEqual(a.Parameters, b.Parameters)

    def test09(self):
        """Test rest mask and passes stay in the unreachable corner band"""
        z, xs, ys = _step_fillet()
        reference_diameter = 12.0
        reference_radius = reference_diameter / 2.0
        cell = float(xs[1] - xs[0])
        overlap = 1.0

        # the mask covers (part of) the corner band and nothing else:
        # with wall height > tool radius the wall is the binding contact,
        # so the reference ball machines everything at or left of
        # wall_x - radius and the plateau at or right of wall_x
        mask = generator._rest_mask(z, xs, ys, reference_diameter)
        self.assertTrue(mask.any())
        band_lo = SF_WALL_X - reference_radius
        for i in numpy.argwhere(mask.any(axis=1)).ravel():
            self.assertTrue(band_lo - cell - 1e-9 <= xs[i] <= SF_WALL_X + cell + 1e-9)
        # the fillet region itself carries rest material
        fillet_cols = (xs > SF_WALL_X - SF_FILLET_R) & (xs < SF_WALL_X)
        self.assertTrue(mask[fillet_cols, :].any())

        # emitted passes stay within the mask dilated by boundary_overlap
        # (plus one cell of contour interpolation margin)
        args = _resetArgs()
        args["heightfield"], args["xs"], args["ys"] = z, xs, ys
        args["boundary_overlap"] = overlap
        args["rest_reference_diameter"] = reference_diameter
        passes, _ = _splitPasses(generator.generate(**args))
        self.assertTrue(passes)
        x_lo = band_lo - overlap - 2 * cell - 1e-9
        x_hi = SF_WALL_X + overlap + 2 * cell + 1e-9
        for pts in passes:
            for x, y, zz in pts:
                self.assertTrue(x_lo <= x <= x_hi)

    def test10(self):
        """Test flat plane yields zero rest passes for any reference tool"""
        args = _resetArgs()
        args["heightfield"] = numpy.full_like(args["heightfield"], 3.0)
        for diameter in (0.5, 6.0, 40.0):
            args["rest_reference_diameter"] = diameter
            cmds = generator.generate(**args)
            self.assertEqual([cmd for cmd in cmds if cmd.Name == "G1"], [])

    def test11(self):
        """Test rest_reference_diameter value and type checking"""
        args = _resetArgs()
        args["rest_reference_diameter"] = "6"
        self.assertRaises(TypeError, generator.generate, **args)
        args["rest_reference_diameter"] = True
        self.assertRaises(TypeError, generator.generate, **args)
        args["rest_reference_diameter"] = 0.0
        self.assertRaises(ValueError, generator.generate, **args)
        args["rest_reference_diameter"] = -6.0
        self.assertRaises(ValueError, generator.generate, **args)

    def test12(self):
        """Test rest passes remain gouge-free on step and hemisphere"""
        for z, xs, ys in (_step_fillet(), _hemisphere()):
            args = _resetArgs()
            args["heightfield"], args["xs"], args["ys"] = z, xs, ys
            args["boundary_overlap"] = 1.0
            args["rest_reference_diameter"] = 12.0
            passes, g0_zs = _splitPasses(generator.generate(**args))
            for g0_z in g0_zs:
                self.assertTrue(g0_z >= SAFE_HEIGHT - 1e-9)
            for pts in passes:
                for x, y, zz in pts:
                    self.assertTrue(zz >= _bilinear(z, xs, ys, x, y) - 1e-6)
