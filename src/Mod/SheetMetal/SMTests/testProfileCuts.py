# SPDX-License-Identifier: LGPL-2.1-or-later
"""Editable native sketch profiles shared by folded and developed geometry."""

import json
import math
import tempfile
import unittest
from pathlib import Path

import FreeCAD as App
import Part
import Sketcher

import SheetMetalEditable as Editable
from SMTests import testEditableSheet as document_tests
from SMTests import testEditGeometry as geometry_tests


class TestProfileCuts(unittest.TestCase):
    def setUp(self):
        self.fixture = document_tests.TestEditableSheet()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def slot(self):
        fixture = self.fixture
        _, _, flat = fixture.bend_pick()
        sketch = fixture.edit(lambda: Editable.create_cut_sketch(fixture.sheet))
        center = sketch.Placement.inverse().multVec(flat)
        self.assertAlmostEqual(center.z, 0, places=6)
        a, radius = 5, 4
        right, left = center+App.Vector(a, 0, 0), center-App.Vector(a, 0, 0)
        up = App.Vector(0, radius, 0)
        curves = [
            Part.ArcOfCircle(Part.Circle(right, App.Vector(0, 0, 1), radius),
                             -math.pi/2, math.pi/2),
            Part.LineSegment(right+up, left+up),
            Part.ArcOfCircle(Part.Circle(left, App.Vector(0, 0, 1), radius),
                             math.pi/2, 3*math.pi/2),
            Part.LineSegment(left-up, right-up),
        ]
        def draw():
            sketch.addGeometry(curves, False)
            for i in range(4):
                sketch.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i+1) % 4, 1))
            radius_index = sketch.addConstraint(Sketcher.Constraint("Radius", 0, radius))
            sketch.addConstraint(Sketcher.Constraint("Equal", 0, 2))
            sketch.addConstraint(Sketcher.Constraint("Horizontal", 1))
            return radius_index
        radius_index = fixture.edit(draw)
        self.assertNotIn("Invalid", sketch.State)
        self.assertEqual(len(sketch.Shape.Wires), 1)
        self.assertTrue(sketch.Shape.Wires[0].isClosed())
        return sketch, radius_index

    def test_constrained_slot_crosses_bend_and_updates_both_solids(self):
        fixture = self.fixture
        sketch, radius_index = self.slot()
        original = Editable.get_prepared(fixture.sheet)
        original_volume = original.flat.Volume
        operation = fixture.edit(lambda: Editable.add_profile_cut(fixture.sheet, sketch))
        fixture.assert_valid_pair()
        profile = Part.Face(sketch.Shape.Wires[0])
        edited = Editable.get_prepared(fixture.sheet)
        self.assertAlmostEqual(original_volume-edited.flat.Volume,
                               profile.Area*original.mapping.thickness, places=5)
        geometry_tests.TestEditGeometry().assert_cut_samples(original, edited, profile)
        fingerprint = fixture.sheet.PreparedInputHash
        cut_volume = edited.flat.Volume
        def resize():
            sketch.setDatum(radius_index, App.Units.Quantity("3 mm"))
            with self.assertRaises(RuntimeError):
                Editable.get_prepared(fixture.sheet)
        fixture.edit(resize)
        self.assertNotEqual(fixture.sheet.PreparedInputHash, fingerprint)
        self.assertGreater(fixture.sheet.FlatShape.Volume, cut_volume)
        self.assertEqual(json.loads(fixture.sheet.Definition)["operations"][0]["id"], operation)
        fixture.assert_valid_pair()

    def test_profile_link_and_history_share_one_undo_boundary(self):
        fixture = self.fixture
        sketch, _ = self.slot()
        original_volume = fixture.sheet.FlatShape.Volume
        before = fixture.doc.UndoCount
        fixture.edit(lambda: Editable.add_profile_cut(fixture.sheet, sketch))
        self.assertEqual(fixture.doc.UndoCount, before+1)
        cut_volume = fixture.sheet.FlatShape.Volume
        fixture.doc.undo()
        fixture.recompute()
        self.assertEqual(json.loads(fixture.sheet.Definition)["operations"], [])
        self.assertEqual(list(fixture.sheet.ProfileSources), [])
        self.assertIs(fixture.doc.getObject(sketch.Name), sketch)
        self.assertAlmostEqual(fixture.sheet.FlatShape.Volume, original_volume, places=6)
        fixture.doc.redo()
        fixture.recompute()
        self.assertEqual(list(fixture.sheet.ProfileSources), [sketch])
        self.assertAlmostEqual(fixture.sheet.FlatShape.Volume, cut_volume, places=6)
        fixture.assert_valid_pair()

    def test_save_reopen_keeps_live_sketch_dependency(self):
        fixture = self.fixture
        sketch, radius_index = self.slot()
        fixture.edit(lambda: Editable.add_profile_cut(fixture.sheet, sketch))
        definition, fingerprint = fixture.sheet.Definition, fixture.sheet.PreparedInputHash
        volume = fixture.sheet.FlatShape.Volume
        sketch_name = sketch.Name
        with tempfile.TemporaryDirectory() as directory:
            filename = str(Path(directory)/"profile-sheet.FCStd")
            fixture.doc.saveAs(filename)
            fixture.settle()
            App.closeDocument(fixture.doc.Name)
            fixture.doc = App.openDocument(filename)
            fixture.settle()
            fixture.sheet = fixture.doc.getObject("EditableSheet")
            sketch = fixture.doc.getObject(sketch_name)
            fixture.sheet.touch()
            fixture.recompute()
            self.assertEqual(fixture.sheet.Definition, definition)
            self.assertEqual(fixture.sheet.PreparedInputHash, fingerprint)
            self.assertEqual(list(fixture.sheet.ProfileSources), [sketch])
            fixture.edit(lambda: sketch.setDatum(radius_index, App.Units.Quantity("3 mm")))
            self.assertGreater(fixture.sheet.FlatShape.Volume, volume)
            fixture.assert_valid_pair()

    def test_open_profile_invalidates_and_undo_repairs_same_operation(self):
        fixture = self.fixture
        sketch, _ = self.slot()
        operation = fixture.edit(lambda: Editable.add_profile_cut(fixture.sheet, sketch))
        definition = fixture.sheet.Definition
        fingerprint = fixture.sheet.PreparedInputHash
        fixture.edit(lambda: sketch.delGeometry(3))
        self.assertIn("Invalid", fixture.sheet.State)
        self.assertEqual(fixture.sheet.PreparedInputHash, fingerprint)
        with self.assertRaises(RuntimeError):
            Editable.get_prepared(fixture.sheet)
        fixture.doc.undo()
        fixture.recompute()
        self.assertEqual(fixture.sheet.Definition, definition)
        self.assertEqual(json.loads(definition)["operations"][0]["id"], operation)
        fixture.assert_valid_pair()

    def test_remove_failed_profile_retains_sketch_and_drops_dependency(self):
        fixture = self.fixture
        sketch, _ = self.slot()
        original_volume = fixture.sheet.FlatShape.Volume
        operation = fixture.edit(lambda: Editable.add_profile_cut(fixture.sheet, sketch))
        fixture.edit(lambda: sketch.delGeometry(3))
        self.assertIn("Invalid", fixture.sheet.State)
        fixture.edit(lambda: Editable.remove_operation(fixture.sheet, operation))
        self.assertEqual(list(fixture.sheet.ProfileSources), [])
        self.assertIs(fixture.doc.getObject(sketch.Name), sketch)
        self.assertAlmostEqual(fixture.sheet.FlatShape.Volume, original_volume, places=6)
        fixture.assert_valid_pair()

    def test_foreign_profile_and_result_dependency_rejected_before_mutation(self):
        fixture = self.fixture
        sketch, _ = self.slot()
        definition = fixture.sheet.Definition
        other = App.newDocument("ForeignProfile")
        try:
            foreign = other.addObject("Sketcher::SketchObject", "SheetCutProfile")
            with self.assertRaisesRegex(RuntimeError, "own document"):
                Editable.add_profile_cut(fixture.sheet, foreign)
            fixture.edit(lambda: setattr(sketch, "AttachmentSupport", [(fixture.sheet, "Face1")]))
            with self.assertRaisesRegex(ValueError, "depend on the resulting sheet"):
                Editable.add_profile_cut(fixture.sheet, sketch)
            self.assertEqual(fixture.sheet.Definition, definition)
            self.assertEqual(list(fixture.sheet.ProfileSources), [])
        finally:
            fixture.settle(other)
            App.closeDocument(other.Name)

    def test_attached_profile_follows_upstream_thickness_and_bend_changes(self):
        fixture = self.fixture
        sketch, _ = self.slot()
        fixture.edit(lambda: Editable.add_profile_cut(fixture.sheet, sketch))
        definition = fixture.sheet.Definition
        fingerprint = fixture.sheet.PreparedInputHash
        def change():
            fixture.doc.BaseBend.Thickness = 2
            fixture.doc.BaseBend.Radius = 3
            fixture.doc.BaseBend.Length = 35
            fixture.sheet.KFactor = 0.35
        fixture.edit(change)
        fixture.assert_valid_pair()
        self.assertEqual(fixture.sheet.Definition, definition)
        self.assertNotEqual(fixture.sheet.PreparedInputHash, fingerprint)
        self.assertAlmostEqual(Editable.get_prepared(fixture.sheet).mapping.thickness, 2)

    def test_native_profile_in_transformed_body(self):
        fixture = self.fixture
        fixture.check_container("PartDesign::Body")
        sketch, radius_index = self.slot()
        parent = fixture.sheet.getParentGeoFeatureGroup()
        self.assertIs(sketch.getParentGeoFeatureGroup(), parent)
        fixture.edit(lambda: Editable.add_profile_cut(fixture.sheet, sketch))
        fixture.edit(lambda: sketch.setDatum(radius_index, App.Units.Quantity("3 mm")))
        fixture.assert_valid_pair()
        self.assertIs(fixture.sheet.getParentGeoFeatureGroup(), parent)

    def test_folded_and_flat_picks_resolve_to_the_same_sketch_coordinates(self):
        fixture = self.fixture
        region, folded, flat = fixture.bend_pick()
        sketch = fixture.edit(lambda: Editable.create_cut_sketch(fixture.sheet))
        from_flat = Editable.profile_coordinates(fixture.sheet, sketch, flat)
        from_folded = Editable.profile_coordinates(
            fixture.sheet, sketch, folded, representation="folded", region=region)
        self.assertLess((from_flat-from_folded).Length, 1e-6)
        self.assertAlmostEqual(from_flat.z, 0, places=6)
        self.assertLess((sketch.Placement.multVec(from_flat)-flat).Length, 1e-6)

    def test_recursive_copy_uses_copied_sketch_not_its_old_object_name(self):
        fixture = self.fixture
        sketch, radius_index = self.slot()
        fixture.edit(lambda: Editable.add_profile_cut(fixture.sheet, sketch))
        original_volume = fixture.sheet.FlatShape.Volume
        copy = fixture.edit(lambda: fixture.doc.copyObject(fixture.sheet, True))
        copy.touch()
        fixture.recompute()
        self.assertNotIn("Invalid", copy.State)
        copied_sketch = copy.ProfileSources[0]
        self.assertIsNot(copied_sketch, sketch)
        self.assertNotEqual(copied_sketch.Name, sketch.Name)
        self.assertAlmostEqual(copy.FlatShape.Volume, original_volume, places=6)
        fixture.edit(lambda: copied_sketch.setDatum(radius_index, App.Units.Quantity("3 mm")))
        self.assertGreater(copy.FlatShape.Volume, original_volume)
        self.assertAlmostEqual(fixture.sheet.FlatShape.Volume, original_volume, places=6)

    def test_folded_handle_edits_existing_sketch_through_its_cut(self):
        fixture = self.fixture
        region, folded, _ = fixture.bend_pick()
        sketch, _ = self.slot()
        operation = fixture.edit(lambda: Editable.add_profile_cut(fixture.sheet, sketch))
        fingerprint = fixture.sheet.PreparedInputHash
        with self.assertRaises(ValueError):
            Editable.profile_coordinates(fixture.sheet, sketch, folded,
                                         representation="folded", region=region)
        point = Editable.profile_coordinates(fixture.sheet, sketch, folded,
                                             representation="folded", region=region,
                                             allow_removed=True)
        fixture.edit(lambda: sketch.moveGeometry(0, 3, point))
        self.assertLess((sketch.Geometry[0].Center-point).Length, 1e-6)
        self.assertNotEqual(fixture.sheet.PreparedInputHash, fingerprint)
        self.assertEqual(json.loads(fixture.sheet.Definition)["operations"][0]["id"], operation)
        fixture.assert_valid_pair()

    def test_deleted_profile_is_missing_not_retargeted_and_operation_can_be_removed(self):
        fixture = self.fixture
        sketch, _ = self.slot()
        operation = fixture.edit(lambda: Editable.add_profile_cut(fixture.sheet, sketch))
        fingerprint = fixture.sheet.PreparedInputHash
        fixture.edit(lambda: fixture.doc.removeObject(sketch.Name))
        self.assertIn("Invalid", fixture.sheet.State)
        self.assertEqual(fixture.sheet.PreparedInputHash, fingerprint)
        with self.assertRaisesRegex(ValueError, "profile is missing"):
            Editable.get_prepared(fixture.sheet)
        fixture.edit(lambda: Editable.remove_operation(fixture.sheet, operation))
        fixture.assert_valid_pair()
        self.assertEqual(list(fixture.sheet.ProfileSources), [])

    def test_circular_only_feature_without_profile_property_can_add_native_profile(self):
        fixture = self.fixture
        # Reproduce the schema written before native profile cuts were added.
        fixture.sheet.removeProperty("ProfileSources")
        fixture.sheet.touch()
        fixture.recompute()
        fixture.assert_valid_pair()
        sketch, _ = self.slot()
        fixture.edit(lambda: Editable.add_profile_cut(fixture.sheet, sketch))
        self.assertEqual(list(fixture.sheet.ProfileSources), [sketch])
        fixture.assert_valid_pair()

    def test_profile_updates_do_not_clear_unrelated_custom_links(self):
        fixture = self.fixture
        sketch, _ = self.slot()
        fixture.sheet.addProperty("App::PropertyLink", "CutProfile_reference", "User")
        fixture.sheet.CutProfile_reference = fixture.doc.BaseBend
        fixture.recompute()
        fixture.edit(lambda: Editable.add_profile_cut(fixture.sheet, sketch))
        self.assertIs(fixture.sheet.CutProfile_reference, fixture.doc.BaseBend)

    def test_periodic_bspline_profile_crosses_bend_without_faceting(self):
        fixture = self.fixture
        _, _, flat = fixture.bend_pick()
        sketch = fixture.edit(lambda: Editable.create_cut_sketch(fixture.sheet))
        center = Editable.profile_coordinates(fixture.sheet, sketch, flat)
        points = [center+App.Vector(8*math.cos(i*math.pi/4), 4*math.sin(i*math.pi/4), 0)
                  for i in range(8)]
        curve = Part.BSplineCurve()
        curve.interpolate(points, PeriodicFlag=True)
        fixture.edit(lambda: sketch.addGeometry(curve, False))
        self.assertTrue(sketch.Shape.Wires[0].isClosed())
        self.assertIsInstance(sketch.Shape.Edges[0].Curve, Part.BSplineCurve)
        original = Editable.get_prepared(fixture.sheet)
        profile = Part.Face(sketch.Shape.Wires[0])
        fixture.edit(lambda: Editable.add_profile_cut(fixture.sheet, sketch))
        fixture.assert_valid_pair()
        expected = profile.Area*original.mapping.thickness
        # OCCT's default mass integration differs on periodic B-spline trims.
        # Independent VolumePropertiesGK(..., 1e-11, true, true) agreed on the
        # removed and profile-extrusion volumes within 7e-11 mm^3. Pair this
        # coarse mass comparison with direct geometric boundary checks below.
        self.assertAlmostEqual(original.flat.Volume-fixture.sheet.FlatShape.Volume,
                               expected, delta=expected*1e-3)
        edited = Editable.get_prepared(fixture.sheet)
        edge = sketch.Shape.Edges[0]
        region_faces = [(region, region.flat_face()) for region in original.mapping.regions]
        for i in range(128):
            parameter = edge.FirstParameter+(edge.LastParameter-edge.FirstParameter)*i/128
            flat = edge.valueAt(parameter)
            self.assertLess(min(boundary.distToShape(Part.Vertex(flat))[0]
                                for boundary in edited.flat.Edges), 1e-6)
            regions = [region for region, face in region_faces
                       if face.distToShape(Part.Vertex(flat))[0] < 1e-6]
            self.assertTrue(regions)
            for region in regions:
                folded = region.to_folded(flat)
                self.assertLess(min(boundary.distToShape(Part.Vertex(folded))[0]
                                    for boundary in edited.folded.Edges), 1e-6)
        geometry_tests.TestEditGeometry().assert_cut_samples(
            original, edited, profile)
