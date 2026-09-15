# SPDX-License-Identifier: LGPL-2.1-or-later
"""Real through-cuts spanning planar and cylindrical sheet regions."""

import math
import unittest

import FreeCAD as App
import Part

from SMTests import testUnfoldMapping as fixtures
from SheetMetalEditGeometry import SheetGeometry


# Millimeters: 0.1 micrometer for OCCT's offset side walls. The original
# sheet-surface boundary is checked more tightly at 1e-6 mm. These are geometry
# acceptance bounds, not tessellation settings or manufacturing tolerances.
WALL_TOLERANCE = 1e-4


class TestEditGeometry(unittest.TestCase):
    def prepare(self, *, multiple=False, factor=0.42, thickness=1.6, reverse=False):
        fixture = fixtures.TestUnfoldMapping()
        shape = fixture.bracket(thickness, multiple=multiple)
        roots = fixture.root_faces(shape)
        root = roots[-1] if reverse else roots[0]
        return SheetGeometry.prepare(shape, root, fixture.calculator(factor))

    def bend_center(self, geometry):
        region = next(r for r in geometry.mapping.regions if r.kind == "bend")
        face = geometry.folded.Faces[region.face_index]
        u0, u1, v0, v1 = face.ParameterRange
        folded = face.valueAt((u0+u1)/2, (v0+v1)/2)
        return region, folded, region.to_flat(folded)

    def circle(self, center, normal, radius):
        return Part.Face(Part.Wire(Part.makeCircle(radius, center, normal)))

    def assert_valid_sheet(self, shape):
        self.assertTrue(shape.isValid())
        self.assertEqual(len(shape.Solids), 1)
        self.assertGreater(shape.Volume, 0)

    def assert_cut_samples(self, original, edited, profile):
        removed, retained = 0, 0
        for region in original.mapping.regions:
            face = original.folded.Faces[region.face_index]
            u0, u1, v0, v1 = face.ParameterRange
            for ui in range(1, 20):
                for vi in range(1, 10):
                    u, v = u0+(u1-u0)*ui/20, v0+(v1-v0)*vi/10
                    surface = face.valueAt(u, v)
                    flat = region.to_flat(surface)
                    middle = surface-face.normalAt(u, v)*original.mapping.thickness/2
                    if min(edge.distToShape(Part.Vertex(flat))[0]
                           for edge in profile.Edges) < 1e-6:
                        # Cut boundaries are shared with remaining material;
                        # classify them explicitly rather than as removed volume.
                        self.assertLess(min(f.distToShape(Part.Vertex(middle))[0]
                                            for f in edited.folded.Faces), WALL_TOLERANCE)
                        continue
                    in_cut = profile.distToShape(Part.Vertex(flat))[0] < 1e-6
                    self.assertEqual(edited.folded.isInside(middle, 1e-6, True), not in_cut,
                                     f"wrong folded cut at {middle}, developed {flat}")
                    if in_cut:
                        removed += 1
                    else:
                        retained += 1
        self.assertGreater(removed, 5)
        self.assertGreater(retained, 5)

    def test_circular_hole_crosses_both_bend_seams(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                original = self.prepare(reverse=reverse)
                _, _, center = self.bend_center(original)
                profile = self.circle(center, original.normal, 7)
                before = original.folded.exportBrepToString()
                edited = original.cut(profile)
                self.assert_valid_sheet(edited.folded)
                self.assert_valid_sheet(edited.flat)
                self.assertAlmostEqual(original.flat.Volume-edited.flat.Volume,
                                       math.pi*49*1.6, places=5)
                self.assertLess(edited.folded.Volume, original.folded.Volume)
                self.assert_cut_samples(original, edited, profile)
                self.assertEqual(original.folded.exportBrepToString(), before)

    def test_bend_cut_volume_matches_neutral_axis_development(self):
        for thickness, factor in ((0.8, 0.25), (1.6, 0.42), (3.0, 0.5)):
            with self.subTest(thickness=thickness, factor=factor):
                original = self.prepare(thickness=thickness, factor=factor)
                region, _, center = self.bend_center(original)
                # This small hole lies wholly on the bend. Its through-thickness
                # volume has an independent analytical radial Jacobian.
                profile = self.circle(center, original.normal, 0.5)
                edited = original.cut(profile)
                self.assert_valid_sheet(edited.folded)
                flat_volume = math.pi*0.25*thickness
                expected = flat_volume*(2+thickness/2)/(2+factor*thickness)
                # Shape.Volume uses OCCT's non-adaptive integration. Adaptive
                # integration at 1e-11 confirmed these solids within 1.3e-6 mm³
                # of the analytical values; default Volume differs by up to
                # 9.4e-5 relatively. Pair this coarse mass check with the tight
                # through-thickness boundary checks below.
                self.assertAlmostEqual(original.folded.Volume-edited.folded.Volume,
                                       expected, delta=expected*1e-3)
                direction = original.mapping.bends[0].line.tangentAt(0)
                across = original.normal.cross(direction)
                face = original.folded.Faces[region.face_index]
                for i in range(32):
                    angle = i*2*math.pi/32
                    flat = center+(direction*math.cos(angle)+across*math.sin(angle))*0.5
                    folded = region.to_folded(flat)
                    self.assertLess(min(e.distToShape(Part.Vertex(folded))[0]
                                        for e in edited.folded.Edges), 1e-6)
                    u, v = face.Surface.parameter(folded)
                    for depth in (0.25, 0.5, 0.75):
                        middle = folded-face.normalAt(u, v)*thickness*depth
                        self.assertLess(min(f.distToShape(Part.Vertex(middle))[0]
                                            for f in edited.folded.Faces), WALL_TOLERANCE)

    def test_reverse_pick_and_repeated_cuts_share_geometry(self):
        original = self.prepare(multiple=True)
        region, folded_pick, flat_pick = self.bend_center(original)
        direct = original.cut(self.circle(flat_pick, original.normal, 5))
        from_folded = original.cut(self.circle(
            original.to_flat(region.face_index, folded_pick), original.normal, 5))
        self.assertLess(direct.folded.cut(from_folded.folded).Volume, 1e-6)
        self.assertLess(from_folded.folded.cut(direct.folded).Volume, 1e-6)
        with self.assertRaises(ValueError):
            direct.to_flat(region.face_index, folded_pick)
        with self.assertRaises(ValueError):
            direct.to_folded(region.face_index, flat_pick)
        other = next(r for r in original.mapping.regions
                     if r.kind == "bend" and r.face_index != region.face_index)
        face = original.folded.Faces[other.face_index]
        u0, u1, v0, v1 = face.ParameterRange
        point = face.valueAt((u0+u1)/2, (v0+v1)/2)
        second = direct.cut(self.circle(other.to_flat(point), original.normal, 4))
        self.assert_valid_sheet(second.folded)
        self.assert_valid_sheet(second.flat)
        self.assertLess(second.flat.Volume, direct.flat.Volume)
        self.assertLess(second.folded.Volume, direct.folded.Volume)

    def test_curved_notch_retains_material_inside_the_profile(self):
        original = self.prepare()
        _, _, center = self.bend_center(original)
        # A crescent exercises a curved profile without detaching an island.
        outer = self.circle(center, original.normal, 7)
        direction = original.mapping.bends[0].line.tangentAt(0)
        inner = self.circle(center+direction*3, original.normal, 6)
        profile = outer.cut(inner)
        edited = original.cut(profile)
        self.assert_valid_sheet(edited.folded)
        self.assert_cut_samples(original, edited, profile)

    def test_off_plane_and_non_intersecting_cuts_are_explicit(self):
        original = self.prepare()
        _, _, center = self.bend_center(original)
        for profile in (self.circle(center+original.normal, original.normal, 5),
                        self.circle(center+App.Vector(1000, 1000, 1000), original.normal, 5)):
            with self.assertRaises(ValueError):
                original.cut(profile)
