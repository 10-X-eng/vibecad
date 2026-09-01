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
import unittest
from unittest import mock

import FreeCAD
import Part

import Path
import Path.Main.Job as PathJob
from CAMTests.PathTestUtils import PathTestBase

try:
    import ocl  # noqa: F401

    HAVE_OCL = True
except ImportError:
    try:
        import opencamlib as ocl  # noqa: F401

        HAVE_OCL = True
    except ImportError:
        HAVE_OCL = False

if HAVE_OCL:
    import Path.Op.SteepShallow as PathSteepShallow
    import Path.Op.SurfaceSupport as SurfaceSupport

Path.Log.setLevel(Path.Log.Level.INFO, Path.Log.thisModule())


BOX_SIZE = 30.0
BOX_HEIGHT = 5.0
SPHERE_RADIUS = 8.0


def _make_sphere_on_block(doc):
    """A block with a hemisphere poking out of its top face.

    The sphere center sits on the block's top face, so the upper half
    protrudes: near-vertical (steep) flanks around the equator and a
    near-flat (shallow) cap on top, plus the flat block top itself.
    """
    box = Part.makeBox(
        BOX_SIZE,
        BOX_SIZE,
        BOX_HEIGHT,
        FreeCAD.Vector(-BOX_SIZE / 2.0, -BOX_SIZE / 2.0, 0.0),
    )
    sphere = Part.makeSphere(SPHERE_RADIUS, FreeCAD.Vector(0.0, 0.0, BOX_HEIGHT))
    fused = box.fuse(sphere)
    obj = doc.addObject("Part::Feature", "SphereOnBlock")
    obj.Shape = fused
    return obj


def _model_surface_z(x, y):
    """Analytic model surface height at (x, y)."""
    rr = math.hypot(x, y)
    if rr < SPHERE_RADIUS:
        return BOX_HEIGHT + math.sqrt(SPHERE_RADIUS**2 - rr**2)
    if abs(x) <= BOX_SIZE / 2.0 and abs(y) <= BOX_SIZE / 2.0:
        return BOX_HEIGHT
    return 0.0


def _split_passes(commands):
    """Partition commands into cutting passes at G0 boundaries.

    Returns (passes, g0_zs). Each pass is a list of fully-qualified
    (x, y, z) G1 endpoints; g0_zs collects the Z of every rapid.
    """
    passes = []
    g0_zs = []
    cur = []
    x = y = z = None
    for c in commands:
        p = c.Parameters
        x = p.get("X", x)
        y = p.get("Y", y)
        z = p.get("Z", z)
        if c.Name in ("G0", "G00"):
            if z is not None:
                g0_zs.append(z)
            if cur:
                passes.append(cur)
                cur = []
        elif c.Name in ("G1", "G01"):
            cur.append((x, y, z))
    if cur:
        passes.append(cur)
    return passes, g0_zs


def _is_shallow_pass(pts):
    """Shallow parallel passes run along X at constant Y; steep contour
    passes vary Y. (Z cannot discriminate: shallow passes over flat
    regions are constant-Z too.)"""
    return len(set(round(p[1], 9) for p in pts)) == 1


@unittest.skipUnless(HAVE_OCL, "OpenCamLib not available")
class TestPathSteepShallow(PathTestBase):
    """Integration tests for the Steep/Shallow 3D finishing operation."""

    def setUp(self):
        self.doc = FreeCAD.newDocument("TestSteepShallow")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def _build_op(self):
        part = _make_sphere_on_block(self.doc)
        job = PathJob.Create("Job_SteepShallow", [part])
        op = PathSteepShallow.Create("SteepShallowOp", parentJob=job)
        # Clear SetupSheet expression bindings before setting explicit values.
        for prop in ("StartDepth", "FinalDepth", "StepDown"):
            op.setExpression(prop, None)
        op.StartDepth = BOX_HEIGHT + SPHERE_RADIUS
        op.FinalDepth = 0.0
        op.StepDown = 2.0
        op.SlopeThreshold = 45.0
        op.StepOver = 1.5
        op.BoundaryOverlap = 0.5
        op.SampleInterval = 1.5
        op.LinearDeflection = 0.1
        op.ToolController.Tool.Diameter = 3.0
        self.doc.recompute()
        return op

    def test00_produces_both_pass_kinds(self):
        """The op emits both constant-Z steep passes and surface-following
        shallow passes on a sphere-on-block model."""
        op = self._build_op()
        commands = op.Path.Commands
        self.assertGreater(len(commands), 10, "expected a non-empty path")

        passes, _ = _split_passes(commands)
        steep = [p for p in passes if not _is_shallow_pass(p) and len(p) > 2]
        shallow = [p for p in passes if _is_shallow_pass(p) and len(p) > 2]
        self.assertGreater(len(steep), 0, "expected constant-Z steep contour passes")
        self.assertGreater(len(shallow), 0, "expected surface-following shallow passes")

        # Steep contour passes are each at a single Z level.
        for pts in steep:
            zs = set(round(p[2], 6) for p in pts)
            self.assertEqual(
                len(zs), 1, "steep pass should be constant-Z, got levels {}".format(zs)
            )

    def test01_no_command_below_surface(self):
        """No cutting command may dip below the model surface (no gouge).

        The tool tip CL height for any cutter is at or above the surface
        height directly beneath the tool control point, so comparing tip
        Z against the analytic surface is a valid no-gouge check up to
        tessellation tolerance.
        """
        op = self._build_op()
        passes, _ = _split_passes(op.Path.Commands)
        tol = 0.2  # tessellation deflection + interpolation slack
        for pts in passes:
            for x, y, z in pts:
                self.assertIsNotNone(z)
                self.assertGreaterEqual(
                    z,
                    _model_surface_z(x, y) - tol,
                    "command at ({:.2f}, {:.2f}) Z={:.3f} gouges the surface".format(x, y, z),
                )
                self.assertGreaterEqual(z, -1e-9, "command below FinalDepth")

    def test02_rapids_at_safe_height(self):
        """All linking rapids stay at or above the sampled surface top."""
        op = self._build_op()
        _, g0_zs = _split_passes(op.Path.Commands)
        self.assertGreater(len(g0_zs), 0)
        surface_top = BOX_HEIGHT + SPHERE_RADIUS
        for z in g0_zs:
            self.assertGreaterEqual(z, surface_top - 1e-6, "rapid below the surface top")

    def test03_invalid_parameters_yield_empty_path(self):
        """Invalid user parameters produce a console error and an empty
        path instead of raising out of the recompute."""
        op = self._build_op()
        op.SampleInterval = 0.0
        self.doc.recompute()
        cutting = [c for c in op.Path.Commands if c.Name in ("G1", "G01")]
        self.assertEqual(len(cutting), 0, "invalid SampleInterval should yield no cutting moves")

    def test04_cut_mode_reverses_traversal(self):
        """Conventional reverses every pass traversal relative to Climb."""
        op = self._build_op()
        climb_passes, _ = _split_passes(op.Path.Commands)
        op.CutMode = "Conventional"
        self.doc.recompute()
        conv_passes, _ = _split_passes(op.Path.Commands)
        self.assertEqual(len(climb_passes), len(conv_passes))
        for a, b in zip(climb_passes, conv_passes):
            fa = [(round(p[0], 6), round(p[1], 6), round(p[2], 6)) for p in a]
            fb = [(round(p[0], 6), round(p[1], 6), round(p[2], 6)) for p in b]
            self.assertEqual(fa, list(reversed(fb)), "pass not reversed under Conventional")

    def test05_rest_toggle_roundtrip_restores_baseline(self):
        """Enabling then disabling rest machining restores the exact
        baseline path (rest-off output is unaffected by the new props)."""
        op = self._build_op()
        self.assertFalse(op.UseRestMachining, "rest machining must default to off")
        baseline = [(c.Name, tuple(sorted(c.Parameters.items()))) for c in op.Path.Commands]
        op.UseRestMachining = True
        self.doc.recompute()
        op.UseRestMachining = False
        self.doc.recompute()
        restored = [(c.Name, tuple(sorted(c.Parameters.items()))) for c in op.Path.Commands]
        self.assertEqual(baseline, restored, "rest-off path differs from baseline")

    def test06_rest_machining_restricts_passes(self):
        """With a larger reference tool, rest passes are non-empty, are a
        strict reduction of the baseline, and stay near the concave
        junction ring the reference tool could not reach."""
        op = self._build_op()
        baseline_passes, _ = _split_passes(op.Path.Commands)
        baseline_pts = [p for pts in baseline_passes for p in pts]
        baseline_g1 = len(baseline_pts)

        op.UseRestMachining = True
        op.RestReferenceToolDiameter = 12.0
        self.doc.recompute()
        rest_passes, _ = _split_passes(op.Path.Commands)
        rest_pts = [p for pts in rest_passes for p in pts]

        self.assertGreater(len(rest_pts), 0, "rest machining should leave passes to cut")
        self.assertLess(len(rest_pts), baseline_g1, "rest passes should be fewer than baseline")

        # Legitimate rest material exists in exactly two regions: (a) the
        # concave junction ring where the sphere flank meets the flat top
        # (reference ball tangent point at hypot ~12.65, plus one grid
        # cell of boundary-overlap dilation), and (b) the outer grid band
        # at the block walls, where the heightfield steps down to
        # FinalDepth and the reference ball bridges the wall-base corner.
        # The baseline instead also covers the reachable flat top between
        # those regions.
        ring_bound = 16.0
        edge_band = BOX_SIZE / 2.0 - 1.5  # outer nodes plus one-cell dilation

        def in_rest_region(p):
            return math.hypot(p[0], p[1]) <= ring_bound or max(abs(p[0]), abs(p[1])) >= edge_band

        self.assertTrue(
            any(not in_rest_region(p) for p in baseline_pts),
            "baseline should cover reachable flat top outside the rest regions",
        )
        for x, y, z in rest_pts:
            self.assertTrue(
                in_rest_region((x, y, z)),
                "rest pass at ({:.2f}, {:.2f}) is outside both rest regions".format(x, y),
            )

    def test07_rest_invalid_reference_diameter_yields_empty_path(self):
        """A non-positive reference diameter logs an error and produces an
        empty path instead of raising out of the recompute."""
        op = self._build_op()
        op.UseRestMachining = True
        op.RestReferenceToolDiameter = 0.0
        self.doc.recompute()
        cutting = [c for c in op.Path.Commands if c.Name in ("G1", "G01")]
        self.assertEqual(
            len(cutting),
            0,
            "invalid RestReferenceToolDiameter should yield no cutting moves",
        )

    def test08_machines_every_job_model(self):
        """One operation covers every separated model owned by its Job."""
        first = self.doc.addObject("Part::Feature", "FirstModel")
        first.Shape = Part.makeBox(10.0, 10.0, 5.0)
        second = self.doc.addObject("Part::Feature", "SecondModel")
        second.Shape = Part.makeBox(
            10.0,
            10.0,
            5.0,
            FreeCAD.Vector(30.0, 0.0, 0.0),
        )
        job = PathJob.Create("Job_MultiModelSteepShallow", [first, second])
        op = PathSteepShallow.Create("MultiModelSteepShallow", parentJob=job)
        for prop in ("StartDepth", "FinalDepth", "StepDown"):
            op.setExpression(prop, None)
        op.StartDepth = 5.0
        op.FinalDepth = 0.0
        op.StepDown = 1.0
        op.SlopeThreshold = 90.0
        op.StepOver = 1.0
        op.SampleInterval = 1.0
        op.LinearDeflection = 0.1
        op.ToolController.Tool.Diameter = 2.0
        self.doc.recompute()

        passes, _ = _split_passes(op.Path.Commands)
        points = [point for path in passes for point in path]
        self.assertTrue(
            any(-1.0 <= x <= 11.0 for x, _y, _z in points),
            "first Job model was not machined",
        )
        self.assertTrue(
            any(29.0 <= x <= 41.0 for x, _y, _z in points),
            "second Job model was not machined",
        )

    def test09_unsupported_cutter_yields_empty_path(self):
        """An unsupported physical cutter is never replaced by a flat end mill."""
        op = self._build_op()

        class UnsupportedOCLTool:
            def __init__(self, *_args, **_kwargs):
                pass

            def getOclTool(self):
                return False

        with mock.patch.object(SurfaceSupport, "OCL_Tool", UnsupportedOCLTool):
            op.touch()
            self.doc.recompute()

        cutting = [c for c in op.Path.Commands if c.Name in ("G1", "G01")]
        self.assertEqual(
            cutting,
            [],
            "unsupported cutter must not generate a path with substitute geometry",
        )
