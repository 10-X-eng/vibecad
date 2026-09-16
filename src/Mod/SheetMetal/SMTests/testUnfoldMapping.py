# SPDX-License-Identifier: LGPL-2.1-or-later
"""Native geometry correspondence; run in the isolated SheetMetal GUI suite."""

import math
from types import SimpleNamespace
import unittest

import FreeCAD as App
import Part

from SheetMetalBaseCmd import smBase
from SheetMetalMapping import unfold_with_mapping
from SheetMetalNewUnfolder import BendAllowanceCalculator, unfold


class TestUnfoldMapping(unittest.TestCase):
    def calculator(self, factor=0.42):
        return BendAllowanceCalculator.from_single_value(factor, "ansi")

    def bracket(self, thickness=1.6, multiple=False):
        points = [App.Vector(0, 0), App.Vector(40, 0), App.Vector(40, 30)]
        if multiple:
            points.extend([App.Vector(65, 30), App.Vector(65, 55)])
        source = SimpleNamespace(
            Shape=Part.makePolygon(points), getGlobalPlacement=App.Placement
        )
        shape = smBase(thk=thickness, radius=2.0, length=30, MainObject=source)
        self.assertTrue(shape.isValid())
        self.assertEqual(len(shape.Solids), 1)
        return shape

    def root_faces(self, shape):
        return [i for i, face in enumerate(shape.Faces)
                if isinstance(face.Surface, Part.Plane) and face.Area > 200]

    def assert_point_equal(self, actual, expected):
        self.assertLess(actual.distanceToPoint(expected), 1e-6)

    def check_mapping(self, shape, root):
        result = unfold_with_mapping(shape, root, self.calculator())
        regions = {region.face_index: region for region in result.regions}
        self.assertIn(root, regions)
        root_face = shape.Faces[root]
        origin = root_face.Vertexes[0].Point
        normal = root_face.normalAt(0, 0)
        for region in result.regions:
            face = shape.Faces[region.face_index]
            u0, u1, v0, v1 = face.ParameterRange
            for uf, vf in ((0.0, 0.25), (0.2, 0.7), (0.5, 0.5), (1.0, 0.75)):
                folded = face.valueAt(u0 + (u1-u0)*uf, v0 + (v1-v0)*vf)
                flat = region.to_flat(folded)
                self.assertLess(abs((flat-origin).dot(normal)), 1e-6)
                self.assert_point_equal(region.to_folded(flat), folded)
        # Adjacent regions must meet at the SAME flat position, not merely
        # round-trip independently through mutually inverse wrong transforms.
        seam_count = 0
        for edge in shape.Edges:
            owners = [i for i in regions if any(
                edge.isSame(candidate) for candidate in shape.Faces[i].Edges)]
            if len(owners) != 2:
                continue
            seam_count += 1
            for fraction in (0.0, 0.3, 1.0):
                point = edge.valueAt(edge.FirstParameter + fraction *
                                     (edge.LastParameter-edge.FirstParameter))
                self.assert_point_equal(regions[owners[0]].to_flat(point),
                                        regions[owners[1]].to_flat(point))
        self.assertGreater(seam_count, 0)
        return result

    def test_both_skins_and_stationary_faces(self):
        shape = self.bracket()
        for root in self.root_faces(shape):
            with self.subTest(root=root):
                result = self.check_mapping(shape, root)
                self.assertEqual(len(result.regions), 3)

    def test_multiple_bends_and_arbitrary_placement(self):
        shape = self.bracket(multiple=True)
        placement = App.Placement(App.Vector(73, -51, 19),
                                  App.Rotation(App.Vector(1, 2, 3), 127))
        shape = shape.transformed(placement.toMatrix())
        for root in self.root_faces(shape):
            with self.subTest(root=root):
                result = self.check_mapping(shape, root)
                self.assertEqual(len(result.regions), 7)

    def test_bend_width_uses_material_and_thickness(self):
        for thickness in (0.8, 1.6, 3.0):
            for factor in (0.25, 0.42, 0.5):
                with self.subTest(thickness=thickness, factor=factor):
                    shape = self.bracket(thickness)
                    # Test both inner and outer skins; they need identical
                    # developed bend widths even though their radii differ.
                    for root in self.root_faces(shape):
                        result = unfold_with_mapping(shape, root, self.calculator(factor))
                        self.assertAlmostEqual(result.thickness, thickness, places=6)
                        region = next(r for r in result.regions if r.kind == "bend")
                        face = shape.Faces[region.face_index]
                        u0, u1, v0, v1 = face.ParameterRange
                        start = region.to_flat(face.valueAt(u0, (v0+v1)/2))
                        end = region.to_flat(face.valueAt(u1, (v0+v1)/2))
                        expected = (2.0 + factor*thickness) * math.pi/2
                        self.assertAlmostEqual(start.distanceToPoint(end), expected, places=6)

    def test_legacy_unfold_output_is_preserved(self):
        shape = self.bracket(multiple=True)
        root = self.root_faces(shape)[0]
        before = shape.exportBrepToString()
        edges, bends = unfold(shape, root, self.calculator())
        result = unfold_with_mapping(shape, root, self.calculator())
        self.assertEqual(len(edges), len(result.edges))
        self.assertEqual(len(bends), len(result.bends))
        for old, new in zip(edges, result.edges):
            self.assertAlmostEqual(old.Length, new.Length, places=7)
            self.assertLess(old.distToShape(new)[0], 1e-7)
            self.assert_point_equal(old.CenterOfMass, new.CenterOfMass)
        self.assertEqual(shape.exportBrepToString(), before)

    def test_outside_trim_and_off_surface_points_are_rejected(self):
        shape = Part.makeBox(30, 20, 2).cut(
            Part.makeCylinder(3, 2, App.Vector(15, 10, 0)))
        root = next(i for i, face in enumerate(shape.Faces)
                    if abs(face.CenterOfMass.z-2) < 1e-7 and face.Area > 500)
        result = unfold_with_mapping(shape, root, self.calculator())
        self.assertEqual(len(result.regions), 1)
        region = result.regions[0]
        self.assert_point_equal(region.to_folded(region.to_flat(App.Vector(2, 2, 2))),
                                App.Vector(2, 2, 2))
        for point in (App.Vector(15, 10, 2), App.Vector(60, 10, 2), App.Vector(2, 2, 3)):
            with self.subTest(point=point):
                with self.assertRaises(ValueError):
                    region.to_flat(point)
                with self.assertRaises(ValueError):
                    region.to_folded(point)

    def test_mapping_owns_its_shape_snapshot(self):
        shape = self.bracket()
        root = self.root_faces(shape)[0]
        result = unfold_with_mapping(shape, root, self.calculator())
        face = shape.Faces[root]
        point = face.Vertexes[0].Point
        region = next(r for r in result.regions if r.face_index == root)
        flat = region.to_flat(point)
        shape.translate(App.Vector(100, 200, 300))
        self.assert_point_equal(region.to_flat(point), flat)
        self.assert_point_equal(region.to_folded(flat), point)

    def test_invalid_root_is_explicit(self):
        shape = self.bracket()
        for root in (-1, len(shape.Faces)):
            with self.assertRaises(ValueError):
                unfold_with_mapping(shape, root, self.calculator())
        cylinder = next(i for i, face in enumerate(shape.Faces)
                        if isinstance(face.Surface, Part.Cylinder))
        with self.assertRaises(ValueError):
            unfold_with_mapping(shape, cylinder, self.calculator())
