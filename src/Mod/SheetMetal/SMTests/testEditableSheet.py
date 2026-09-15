# SPDX-License-Identifier: LGPL-2.1-or-later
"""Shared feature history in real documents, including native async recompute."""

import json
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import FreeCAD as App
import Part
from PySide import QtCore

import SheetMetalEditable as Editable
from SheetMetalBaseCmd import SMBaseBend
from VibeCADNativeTransaction import _OwnedDocumentTransaction


class TestEditableSheet(unittest.TestCase):
    def settle(self, document=None):
        document = self.doc if document is None else document
        for _ in range(10000):
            QtCore.QCoreApplication.processEvents()
            if (not document.Recomputing and not document.RecomputePending
                    and not document.CooperativeMutationActive and document.isClosable()):
                return
            time.sleep(0.001)
        self.fail("Private document did not finish its native recompute")

    def recompute(self):
        self.doc.recomputeAsync()
        self.settle()

    def setUp(self):
        self.doc = App.newDocument("EditableSheetTest")
        self.addCleanup(self.close_document)
        self.doc.UndoMode = 1
        sketch = self.doc.addObject("Sketcher::SketchObject", "Profile")
        sketch.addGeometry(Part.LineSegment(App.Vector(), App.Vector(40, 0)), False)
        sketch.addGeometry(Part.LineSegment(App.Vector(40, 0), App.Vector(40, 30)), False)
        source = self.doc.addObject("Part::FeaturePython", "BaseBend")
        SMBaseBend(source, sketch)
        source.Thickness = 1.6
        source.Radius = 2
        source.Length = 30
        self.recompute()
        self.root = next(i+1 for i, f in enumerate(source.Shape.Faces)
                         if isinstance(f.Surface, Part.Plane) and f.Area > 200)
        self.sheet = Editable.create_sheet(source, f"Face{self.root}")
        self.recompute()
        self.assert_valid_pair()

    def close_document(self):
        self.settle()
        App.closeDocument(self.doc.Name)

    def edit(self, action):
        transaction = _OwnedDocumentTransaction(self.doc, "Edit sheet")
        try:
            value = action()
            transaction.commit()
        except Exception:
            transaction.abort()
            raise
        self.recompute()
        return value

    def assert_valid_pair(self):
        self.assertNotIn("Invalid", self.sheet.State)
        for shape in (self.sheet.Shape, self.sheet.FlatShape):
            self.assertTrue(shape.isValid())
            self.assertEqual(len(shape.Solids), 1)

    def bend_pick(self):
        geometry = Editable.get_prepared(self.sheet)
        region = next(r for r in geometry.mapping.regions if r.kind == "bend")
        face = self.sheet.SourceFace[0].Shape.Faces[region.face_index]
        u0, u1, v0, v1 = face.ParameterRange
        point = face.valueAt((u0+u1)/2, (v0+v1)/2)
        return region.face_index, point, region.to_flat(point)

    def test_shared_cut_history_undo_redo_and_reverse_edit(self):
        region, folded, flat = self.bend_pick()
        original = self.sheet.FlatShape.Volume
        undo_count = self.doc.UndoCount
        operation = self.edit(lambda: Editable.add_circle_cut(self.sheet, flat, 7))
        self.assertEqual(self.doc.UndoCount, undo_count+1)
        cut_volume = self.sheet.FlatShape.Volume
        self.assertLess(cut_volume, original)
        definition = json.loads(self.sheet.Definition)
        self.assertEqual(definition["operations"][0]["id"], operation)
        self.doc.undo()
        self.recompute()
        self.assertEqual(json.loads(self.sheet.Definition)["operations"], [])
        self.assertAlmostEqual(self.sheet.FlatShape.Volume, original, places=6)
        self.doc.redo()
        self.recompute()
        self.assertAlmostEqual(self.sheet.FlatShape.Volume, cut_volume, places=6)
        # Edit the SAME operation using a pick on the uncut base sheet. This
        # moves the existing feature; it must not create a second cut history.
        self.edit(lambda: Editable.update_circle_cut(
            self.sheet, operation, center=folded, radius=5,
            representation="folded", region=region))
        self.assertEqual(len(json.loads(self.sheet.Definition)["operations"]), 1)
        self.assertGreater(self.sheet.FlatShape.Volume, cut_volume)
        self.assertLess(self.sheet.FlatShape.Volume, original)
        self.assert_valid_pair()

    def test_save_reopen_keeps_definition_and_both_shapes(self):
        _, _, flat = self.bend_pick()
        operation = self.edit(lambda: Editable.add_circle_cut(self.sheet, flat, 6))
        definition = self.sheet.Definition
        volumes = (self.sheet.Shape.Volume, self.sheet.FlatShape.Volume)
        input_hash = self.sheet.PreparedInputHash
        with tempfile.TemporaryDirectory() as directory:
            filename = str(Path(directory)/"shared-sheet.FCStd")
            self.doc.saveAs(filename)
            self.settle()
            App.closeDocument(self.doc.Name)
            self.doc = App.openDocument(filename)
            self.settle()
            self.sheet = self.doc.getObject("EditableSheet")
            self.assertEqual(self.sheet.Definition, definition)
            self.assertEqual(self.sheet.PreparedInputHash, input_hash)
            self.assertAlmostEqual(self.sheet.Shape.Volume, volumes[0], places=5)
            self.assertAlmostEqual(self.sheet.FlatShape.Volume, volumes[1], places=5)
            self.sheet.touch()
            self.recompute()
            self.assertEqual(self.sheet.PreparedInputHash, input_hash)
            self.edit(lambda: Editable.update_circle_cut(self.sheet, operation, radius=4))
            self.assertGreater(self.sheet.FlatShape.Volume, volumes[1])
            self.assert_valid_pair()

    def test_persistence_precision_export_preserves_default_and_source(self):
        source = self.doc.BaseBend.Shape
        original = source.exportBrepToString()
        persisted = source.exportBrepToString(True)
        self.assertEqual(source.exportBrepToString(), original)
        self.assertEqual(source.exportBrepToString(False), original)
        with tempfile.TemporaryDirectory() as directory:
            filename = str(Path(directory)/"precision.FCStd")
            self.doc.saveAs(filename)
            self.settle()
            with zipfile.ZipFile(filename) as archive:
                self.assertEqual(archive.read("BaseBend.Shape.brp").decode(), persisted)

    def test_binary_save_reopen_preserves_prepared_input_fingerprint(self):
        preferences = App.ParamGet("User parameter:BaseApp/Preferences/Document")
        previous = preferences.GetBool("SaveBinaryBrep", False)
        self.addCleanup(preferences.SetBool, "SaveBinaryBrep", previous)
        preferences.SetBool("SaveBinaryBrep", True)
        _, _, flat = self.bend_pick()
        self.edit(lambda: Editable.add_circle_cut(self.sheet, flat, 6))
        input_hash = self.sheet.PreparedInputHash
        with tempfile.TemporaryDirectory() as directory:
            filename = str(Path(directory)/"binary-sheet.FCStd")
            self.doc.saveAs(filename)
            self.settle()
            with zipfile.ZipFile(filename) as archive:
                self.assertIn("BaseBend.Shape.bin", archive.namelist())
            App.closeDocument(self.doc.Name)
            self.doc = App.openDocument(filename)
            self.settle()
            self.sheet = self.doc.getObject("EditableSheet")
            self.sheet.touch()
            self.recompute()
            self.assertEqual(self.sheet.PreparedInputHash, input_hash)
            self.assert_valid_pair()

    def test_source_motion_changes_fingerprint_even_with_same_volume(self):
        fingerprint = self.sheet.PreparedInputHash
        volume = self.sheet.Shape.Volume
        def move_source():
            placement = self.doc.BaseBend.Placement
            placement.Base.x += 2
            self.doc.BaseBend.Placement = placement
        self.edit(move_source)
        self.assertNotEqual(self.sheet.PreparedInputHash, fingerprint)
        self.assertAlmostEqual(self.sheet.Shape.Volume, volume, places=6)
        self.assert_valid_pair()

    def check_container(self, type_id):
        outer = self.doc.addObject("App::Part", "SheetAssembly")
        container = self.doc.addObject(type_id, "SheetContainer")
        outer.addObject(container)
        source = self.doc.addObject("Part::Feature", "ContainerSource")
        source.Shape = self.doc.BaseBend.Shape.copy()
        container.addObject(source)
        outer.Placement = App.Placement(App.Vector(70, -30, 12),
                                        App.Rotation(App.Vector(1, 2, 3), 35))
        container.Placement = App.Placement(App.Vector(-8, 4, 20),
                                            App.Rotation(App.Vector(0, 0, 1), 25))
        self.recompute()
        self.sheet = Editable.create_sheet(source, f"Face{self.root}", name="ContainerSheet")
        self.recompute()
        self.assertIs(self.sheet.getParentGeoFeatureGroup(), container)
        self.assert_valid_pair()
        parent = container.getGlobalPlacement()
        expected = parent.multVec(source.Shape.Vertexes[0].Point)
        actual = parent.multVec(self.sheet.Shape.Vertexes[0].Point)
        self.assertLess((actual-expected).Length, 1e-6)
        geometry = Editable.get_prepared(self.sheet)
        fingerprint = self.sheet.PreparedInputHash
        # Moving the whole container changes the scene transform, not the
        # local sheet geometry or its manufacture-ready definition.
        placement = outer.Placement
        placement.Base.x += 25
        outer.Placement = placement
        self.settle()
        self.assertIs(Editable.get_prepared(self.sheet), geometry)
        self.assertEqual(self.sheet.PreparedInputHash, fingerprint)
        expected = container.getGlobalPlacement().multVec(source.Shape.Vertexes[0].Point)
        actual = container.getGlobalPlacement().multVec(self.sheet.Shape.Vertexes[0].Point)
        self.assertLess((actual-expected).Length, 1e-6)
        region, folded, _ = self.bend_pick()
        operation = self.edit(lambda: Editable.add_circle_cut(
            self.sheet, folded, 4, representation="folded", region=region))
        self.assert_valid_pair()
        definition = self.sheet.Definition
        fingerprint = self.sheet.PreparedInputHash
        container_name = container.Name
        sheet_name = self.sheet.Name
        with tempfile.TemporaryDirectory() as directory:
            filename = str(Path(directory)/"placed-sheet.FCStd")
            self.doc.saveAs(filename)
            self.settle()
            App.closeDocument(self.doc.Name)
            self.doc = App.openDocument(filename)
            self.settle()
            self.sheet = self.doc.getObject(sheet_name)
            self.assertIs(self.sheet.getParentGeoFeatureGroup(),
                          self.doc.getObject(container_name))
            self.sheet.touch()
            self.recompute()
            self.assertEqual(self.sheet.Definition, definition)
            self.assertEqual(self.sheet.PreparedInputHash, fingerprint)
            self.edit(lambda: Editable.update_circle_cut(self.sheet, operation, radius=3))
            self.assert_valid_pair()

    def test_nested_part_uses_source_frame_without_baking_scene_transforms(self):
        self.check_container("App::Part")

    def test_body_uses_source_frame_without_baking_scene_transforms(self):
        self.check_container("PartDesign::Body")

    def test_native_reparenting_moves_the_shared_definition_with_its_source(self):
        self.check_container("App::Part")
        source = self.sheet.SourceFace[0]
        previous_hash = self.sheet.PreparedInputHash
        other = self.doc.addObject("App::Part", "OtherFrame")
        other.Placement.Base = App.Vector(-90, 0, 0)
        other.addObject(source)
        self.recompute()
        self.assertIs(source.getParentGeoFeatureGroup(), other)
        self.assertIs(self.sheet.getParentGeoFeatureGroup(), other)
        self.assertEqual(self.sheet.PreparedInputHash, previous_hash)
        self.assert_valid_pair()

    def test_upstream_parameters_and_material_rebuild_the_same_history(self):
        _, _, flat = self.bend_pick()
        operation = self.edit(lambda: Editable.add_circle_cut(self.sheet, flat, 5))
        definition = self.sheet.Definition
        old_hash = self.sheet.PreparedInputHash
        old_volumes = self.sheet.Shape.Volume, self.sheet.FlatShape.Volume
        def change():
            self.doc.BaseBend.Thickness = 2
            self.doc.BaseBend.Radius = 3
            self.doc.BaseBend.Length = 35
            self.sheet.KFactor = 0.35
            self.sheet.Material = "5052 Aluminum"
        self.edit(change)
        self.assertEqual(self.sheet.Definition, definition)
        self.assertEqual(json.loads(definition)["operations"][0]["id"], operation)
        self.assertNotEqual(self.sheet.PreparedInputHash, old_hash)
        self.assertNotEqual((self.sheet.Shape.Volume, self.sheet.FlatShape.Volume), old_volumes)
        self.assertAlmostEqual(Editable.get_prepared(self.sheet).mapping.thickness, 2)
        self.assert_valid_pair()

    def test_active_document_does_not_redirect_edits(self):
        _, _, flat = self.bend_pick()
        other = App.newDocument("OtherEditableSheet")
        sentinel = other.addObject("Part::Box", "EditableSheet")
        before = [(obj.Name, obj.TypeId) for obj in other.Objects]
        try:
            self.edit(lambda: Editable.add_circle_cut(self.sheet, flat, 5))
            self.assertIs(App.ActiveDocument, other)
            self.assertIs(self.sheet.SourceFace[0].Document, self.doc)
            self.assertEqual(sentinel.TypeId, "Part::Box")
            self.assertEqual([(obj.Name, obj.TypeId) for obj in other.Objects], before)
            self.assert_valid_pair()
        finally:
            self.settle(other)
            App.closeDocument(other.Name)

    def test_pending_inputs_reject_old_geometry_and_stale_edit(self):
        _, _, flat = self.bend_pick()
        old_hash = self.sheet.PreparedInputHash
        Editable.add_circle_cut(self.sheet, flat, 5, expected_input_hash=old_hash)
        with self.assertRaises(RuntimeError):
            Editable.get_prepared(self.sheet)
        self.recompute()
        before = self.sheet.Definition
        with self.assertRaises(RuntimeError):
            Editable.add_circle_cut(self.sheet, flat, 3, expected_input_hash=old_hash)
        self.assertEqual(self.sheet.Definition, before)

    def test_invalid_definition_is_rejected_before_document_mutation(self):
        before = self.sheet.Definition
        for definition in ({"version": 999, "operations": []},
                           {"version": 1, "operations": [{"id": "x", "kind": "circle",
                                                          "center": [0, 0], "radius": -2}]}):
            with self.assertRaises(ValueError):
                Editable.set_definition(self.sheet, definition)
            self.assertEqual(self.sheet.Definition, before)

    def test_unrelated_feature_with_definition_property_is_not_editable(self):
        other = self.doc.addObject("App::FeaturePython", "Unrelated")
        other.addProperty("App::PropertyString", "Definition")
        other.Definition = "leave this unrelated feature alone"
        with self.assertRaises(RuntimeError):
            Editable.set_definition(other, {"version": 1, "operations": []})
        self.assertEqual(other.Definition, "leave this unrelated feature alone")

    def test_changed_upstream_parameter_invalidates_prepared_geometry(self):
        self.doc.BaseBend.Thickness = 2
        with self.assertRaises(RuntimeError):
            Editable.get_prepared(self.sheet)
        self.recompute()
        self.assertAlmostEqual(Editable.get_prepared(self.sheet).mapping.thickness, 2)

    def test_profile_edit_changes_flange_length_and_bend_angle(self):
        _, _, flat = self.bend_pick()
        self.edit(lambda: Editable.add_circle_cut(self.sheet, flat, 5))
        definition = self.sheet.Definition
        volumes = self.sheet.Shape.Volume, self.sheet.FlatShape.Volume
        def change_profile():
            self.doc.Profile.moveGeometry(1, 2, App.Vector(50, 35, 0))
            # The source solid has not recomputed yet; a dirty upstream sketch
            # must invalidate its sheet even while the old BRep is still present.
            with self.assertRaises(RuntimeError):
                Editable.get_prepared(self.sheet)
        self.edit(change_profile)
        self.assertEqual(self.sheet.Definition, definition)
        self.assertNotEqual((self.sheet.Shape.Volume, self.sheet.FlatShape.Volume), volumes)
        self.assert_valid_pair()
        self.assertIsNotNone(Editable.get_prepared(self.sheet))

    def test_missing_source_face_does_not_publish_old_geometry_as_current(self):
        previous_hash = self.sheet.PreparedInputHash
        self.sheet.SourceFace = self.doc.BaseBend, ["Face99999"]
        self.recompute()
        self.assertIn("Invalid", self.sheet.State)
        with self.assertRaises(RuntimeError):
            Editable.get_prepared(self.sheet)
        self.assertEqual(self.sheet.PreparedInputHash, previous_hash)

    def test_native_async_recompute_prepares_geometry_off_the_gui_thread(self):
        gui_thread = threading.get_ident()
        threads = []
        original = Editable.SheetGeometry.prepare
        def prepare(*args, **kwargs):
            threads.append(threading.get_ident())
            return original(*args, **kwargs)
        with patch.object(Editable.SheetGeometry, "prepare", side_effect=prepare):
            self.sheet.touch()
            self.recompute()
        self.assertEqual(len(threads), 1)
        self.assertNotEqual(threads[0], gui_thread)
        self.assert_valid_pair()

    def test_failed_cut_can_be_repaired_without_a_prepared_failed_shape(self):
        _, _, flat = self.bend_pick()
        operation = self.edit(lambda: Editable.add_circle_cut(self.sheet, flat, 100))
        self.assertIn("Invalid", self.sheet.State)
        with self.assertRaises(RuntimeError):
            Editable.get_prepared(self.sheet)
        self.edit(lambda: Editable.update_circle_cut(self.sheet, operation, radius=4))
        self.assert_valid_pair()
        self.assertIsNotNone(Editable.get_prepared(self.sheet))

    def test_remove_failed_cut_retains_one_undoable_history(self):
        _, _, flat = self.bend_pick()
        original_volume = self.sheet.FlatShape.Volume
        operation = self.edit(lambda: Editable.add_circle_cut(self.sheet, flat, 100))
        self.assertIn("Invalid", self.sheet.State)
        before = self.doc.UndoCount
        self.edit(lambda: Editable.remove_operation(self.sheet, operation))
        self.assertEqual(self.doc.UndoCount, before+1)
        self.assertEqual(json.loads(self.sheet.Definition)["operations"], [])
        self.assertAlmostEqual(self.sheet.FlatShape.Volume, original_volume, places=6)
        self.assert_valid_pair()
