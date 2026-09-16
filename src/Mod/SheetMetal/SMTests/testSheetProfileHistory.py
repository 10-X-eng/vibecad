# SPDX-License-Identifier: LGPL-2.1-or-later
"""Native sketch cuts in the same folded/flat operation chain as holes."""

import tempfile
import math
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import FreeCAD as App
import FreeCADGui as Gui
import Part
import Sketcher
from PySide import QtCore, QtGui, QtWidgets

import SheetMetalEditable as Editable
import SheetMetalCutHistory as History
from SMTests import testPresentation, testProfileCuts, testEditGeometry


class TestSheetProfileHistory(unittest.TestCase):
    def setUp(self):
        self.fixture = testPresentation.TestPresentation()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.model = self.fixture.fixture
        self.base = self.model.sheet
        profiles = testProfileCuts.TestProfileCuts()
        profiles.fixture = self.model
        self.sketch, self.radius = profiles.slot()

    def cut(self, base=None):
        step = self.model.edit(lambda: History.create_profile_step(
            self.base if base is None else base, self.sketch))
        self.fixture.wait_for(lambda: step.ViewObject.Proxy.ready)
        return step

    def folded_skin_profile(self, attached=False, opposite=False):
        geometry = Editable.get_prepared(self.base)
        region = next(region for region in geometry.mapping.regions
                      if region.kind == "plane"
                      and abs(region._face.normalAt(0, 0).dot(geometry.normal)) < 0.5)
        center = region._face.CenterOfMass
        normal = region._face.normalAt(0, 0)
        if opposite:
            center = center-normal*geometry.mapping.thickness
        def create():
            sketch = self.model.doc.addObject("Sketcher::SketchObject", "FoldedProfile")
            if attached:
                sketch.AttachmentSupport = [(self.base, (f"Face{region.face_index+1}",))]
                sketch.MapMode = "FlatFace"
            else:
                sketch.Placement = App.Placement(center, App.Rotation(App.Vector(0, 0, 1), normal))
            return sketch
        sketch = self.model.edit(create)
        local = sketch.Placement.inverse().multVec(center)
        self.model.edit(lambda: sketch.addGeometry(Part.Circle(local, App.Vector(0, 0, 1), 2), False))
        return sketch

    def test_folded_skin_profile_maps_without_moving_the_authored_sketch(self):
        sketch = self.folded_skin_profile()
        before = sketch.Placement, self.base.FlatShape.Volume
        step = self.model.edit(lambda: History.create_profile_step(self.base, sketch))
        self.assertNotIn("Invalid", step.State)
        self.assertTrue(History.get_prepared(step).folded.isValid())
        self.assertTrue(History.get_prepared(step).flat.isValid())
        self.assertEqual(sketch.Placement, before[0])
        self.assertIs(History.get_profile(step), sketch)
        self.assertAlmostEqual(before[1]-step.FlatShape.Volume,
                               4*math.pi*self.base.Proxy._geometry.mapping.thickness, places=5)

    def test_folded_authored_sketch_coordinates_work_from_either_view_and_skin(self):
        for opposite in (False, True):
            with self.subTest(opposite=opposite):
                sketch = self.folded_skin_profile(opposite=opposite)
                step = self.model.edit(lambda: History.create_profile_step(self.base, sketch))
                geometry = step.Proxy._base_geometry
                region = next(region for region in geometry.mapping.regions
                              if region.kind == "plane"
                              and abs(region._face.normalAt(0, 0).dot(geometry.normal)) < .5)
                folded = region._face.CenterOfMass
                flat = region.to_flat(folded)
                expected = sketch.Geometry[0].Center
                for mode, point in (("folded", folded), ("flat", flat)):
                    local = History.profile_coordinates(step, point, representation=mode,
                                                        region=region.face_index)
                    self.assertLess((local-expected).Length, 1e-6)
                self.assertNotIn("Invalid", step.State)
                self.assertAlmostEqual(self.base.FlatShape.Volume-step.FlatShape.Volume,
                                       4*math.pi*geometry.mapping.thickness, places=5)

    def test_attached_folded_profile_survives_edit_undo_and_save_reopen(self):
        sketch = self.folded_skin_profile(attached=True)
        step = self.model.edit(lambda: History.create_profile_step(self.base, sketch))
        original = step.FlatShape.Volume
        self.model.edit(lambda: sketch.setDatum(sketch.addConstraint(
            Sketcher.Constraint("Radius", 0, 2)), App.Units.Quantity("3 mm")))
        self.assertLess(step.FlatShape.Volume, original)
        self.model.doc.undo()
        self.model.recompute()
        self.assertAlmostEqual(step.FlatShape.Volume, original, places=5)
        self.model.doc.redo()
        self.model.recompute()
        digest, volume = step.PreparedInputHash, step.FlatShape.Volume
        names = step.Name, sketch.Name, self.base.Name
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory)/"folded-profile.FCStd")
            self.model.doc.saveAs(path)
            self.model.settle()
            App.closeDocument(self.model.doc.Name)
            self.model.doc = App.openDocument(path)
            self.model.settle()
        step, sketch, self.base = (self.model.doc.getObject(name) for name in names)
        self.model.sheet = self.base
        self.model.edit(lambda: self.base.touch())
        self.assertEqual(step.PreparedInputHash, digest)
        self.assertAlmostEqual(step.FlatShape.Volume, volume, places=5)
        self.assertIs(History.get_profile(step), sketch)
        self.assertIs(sketch.AttachmentSupport[0][0], self.base)

    def test_replacement_attached_to_result_remains_a_real_rejected_cycle(self):
        step = self.cut()
        sketch = self.folded_skin_profile()
        self.model.edit(lambda: setattr(sketch, "AttachmentSupport", [(step, ("Face1",))]))
        self.assertIn(step, sketch.OutListRecursive)
        before = self.model.doc.UndoCount, step.PreparedInputHash
        with self.assertRaisesRegex(ValueError, "resulting sheet"):
            self.model.edit(lambda: History.replace_profile_step(step, sketch))
        self.assertEqual((self.model.doc.UndoCount, step.PreparedInputHash), before)
        self.assertIs(History.get_profile(step), self.sketch)

    def test_relief_on_folded_wall_can_be_bent_into_an_internal_tab(self):
        import SheetMetalSourceOperations as Sources
        import SheetMetalOperations as Operations
        profile = self.folded_skin_profile()
        outline = [(-8, 4), (-8, -10), (8, -10), (8, 4),
                   (7, 4), (7, -9), (-7, -9), (-7, 4)]
        points = [App.Vector(x, y, 0) for x, y in outline]

        def draw():
            profile.delGeometry(0)
            profile.addGeometry([Part.LineSegment(a, b)
                                 for a, b in zip(points, points[1:]+points[:1])], False)
            bend = self.model.doc.addObject("Sketcher::SketchObject", "WallBend")
            bend.Placement = profile.Placement
            bend.addGeometry(Part.LineSegment(App.Vector(-7.5, 0, 0),
                                              App.Vector(7.5, 0, 0)), False)
            return bend

        bend = self.model.edit(draw)
        cut = self.model.edit(lambda: History.create_profile_step(self.base, profile))
        self.assertNotIn("Invalid", cut.State)
        normal = profile.Placement.Rotation.multVec(App.Vector(0, 0, 1))
        face = next(f"Face{i}" for i, face in enumerate(cut.Shape.Faces, 1)
                    if isinstance(face.Surface, Part.Plane)
                    and face.normalAt(0, 0).dot(normal) > .99
                    and abs((face.CenterOfMass-profile.Placement.Base).dot(normal)) < 1e-6)
        arguments = {"operation": "fold_from_sketch", "object_name": cut.Name,
            "subelements": [face], "sketch_name": bend.Name, "bend_radius": 2,
            "bend_angle": 90, "invert": False, "invert_bend": False,
            "k_factor": self.base.KFactor, "position": "middle"}
        run = Sources.start(Sources.prepare(self.model.doc, arguments,
            expected_revision=Sources.capture_revision(self.model.doc)))
        self.fixture.wait_for(run.future.done)
        ready = run.future.result()
        self.assertEqual(ready["phase"], "ready", ready)
        fold = self.model.doc.getObject(ready["object_name"])
        self.assertIs(fold.baseObject[0], cut)
        self.assertIs(fold.BendLine, bend)
        self.assertTrue(fold.Shape.isValid())
        outside = profile.Placement.multVec(App.Vector(11, 8, -.8))
        inside = profile.Placement.multVec(App.Vector(0, -7, -.8))
        self.assertTrue(cut.Shape.isInside(outside, 1e-6, True))
        self.assertTrue(cut.Shape.isInside(inside, 1e-6, True))
        self.assertTrue(fold.Shape.isInside(outside, 1e-6, True))
        self.assertFalse(fold.Shape.isInside(inside, 1e-6, True))
        run = Operations.start_creation(fold, ready["source_geometry"]["reference_faces"][0]["name"],
            expected_revision=Operations.capture_source_revision(fold))
        self.fixture.wait_for(run.future.done)
        ready = run.future.result()
        self.assertEqual(ready["phase"], "ready", ready)
        sheet = self.model.doc.getObject(ready["object_name"])
        self.assertAlmostEqual(sheet.FlatShape.Volume, cut.FlatShape.Volume, places=4)

    def test_profile_attached_to_input_sheet_is_not_a_result_dependency_cycle(self):
        from SheetMetalHistoryOperations import prepare, capture_revision, start
        sketch = self.folded_skin_profile(attached=True)
        self.assertIn(self.base, sketch.OutListRecursive)
        prepared = prepare(self.base, {"operation": "add_profile", "profile": {
            "document_uid": self.model.doc.Uid, "object_name": sketch.Name}},
            expected_revision=capture_revision(self.base))
        run = start(prepared)
        self.fixture.wait_for(run.future.done)
        ready = run.future.result()
        self.assertEqual(ready["phase"], "ready", ready)
        step = self.model.doc.getObject(ready["object_name"])
        self.assertNotIn("Invalid", step.State)
        self.assertNotIn(step, sketch.OutListRecursive)
        self.assertIs(sketch.AttachmentSupport[0][0], self.base)
        self.assertIs(History.get_profile(step), sketch)

    def test_slot_is_native_history_and_shares_the_prepared_bend_mapping(self):
        from SheetMetalEditGeometry import SheetGeometry
        original = Editable.get_prepared(self.base)
        with patch.object(SheetGeometry, "prepare", side_effect=AssertionError("repeat Unfold")):
            step = self.cut()
        result = History.get_prepared(step)
        self.assertIs(step.BaseSheet, self.base)
        self.assertIs(History.get_profile(step), self.sketch)
        self.assertIs(result.mapping, original.mapping)
        self.assertEqual(step.VibeCADTimelineRole, "operation")
        self.assertEqual(list(step.VibeCADTimelineReplacedInputs), [self.base])
        self.assertFalse(self.base.Visibility)
        self.assertFalse(self.sketch.Visibility)
        profile = Editable._profile_face(self.sketch.Shape)
        self.assertAlmostEqual(original.flat.Volume-result.flat.Volume,
                               profile.Area*original.mapping.thickness, places=5)
        testEditGeometry.TestEditGeometry().assert_cut_samples(original, result, profile)

    def test_constraint_edit_updates_profile_and_downstream_hole_off_gui(self):
        from SheetMetalEditGeometry import SheetGeometry
        first = self.cut()
        center = next(region.flat_face().CenterOfMass for region in first.Proxy._geometry.mapping.regions
                      if region.kind == "plane")
        last = self.model.edit(lambda: History.create_circle_step(first, center, 2))
        before, identity = last.FlatShape.Volume, first.OperationId
        calls, original = [], SheetGeometry.cut
        def cut(geometry, profile):
            calls.append(threading.get_ident())
            return original(geometry, profile)
        def resize():
            self.sketch.setDatum(self.radius, App.Units.Quantity("3 mm"))
            with self.assertRaises(RuntimeError):
                History.get_prepared(last)
        with patch.object(SheetGeometry, "prepare", side_effect=AssertionError("repeat Unfold")), \
                patch.object(SheetGeometry, "cut", autospec=True, side_effect=cut):
            self.model.edit(resize)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(thread != threading.get_ident() for thread in calls))
        self.assertGreater(last.FlatShape.Volume, before)
        self.assertEqual(first.OperationId, identity)
        self.assertEqual([op["kind"] for op in History.definition(last)["operations"]],
                         ["profile", "circle"])
        self.model.doc.undo()
        self.model.recompute()
        self.assertAlmostEqual(last.FlatShape.Volume, before, places=6)
        self.model.doc.redo()
        self.model.recompute()
        self.assertGreater(last.FlatShape.Volume, before)

    def test_open_profile_is_invalid_and_suppression_can_bypass_it(self):
        step = self.cut()
        self.model.edit(lambda: self.sketch.delGeometry(3))
        self.assertIn("Invalid", step.State)
        with self.assertRaises(RuntimeError):
            History.get_prepared(step)
        self.model.edit(lambda: setattr(step, "Suppressed", True))
        self.assertNotIn("Invalid", step.State)
        self.assertAlmostEqual(step.Shape.Volume, self.base.Shape.Volume, places=6)
        self.model.doc.undo()
        self.model.doc.undo()
        self.model.recompute()
        self.assertTrue(History.get_prepared(step).folded.isValid())

    def test_profile_after_hole_and_upstream_thickness_share_one_chain(self):
        center = self.model.bend_pick()[2]
        hole = self.model.edit(lambda: History.create_circle_step(self.base, center, 2))
        step = self.cut(hole)
        mapping = step.Proxy._geometry.mapping
        self.assertIs(mapping, hole.Proxy._geometry.mapping)
        self.model.edit(lambda: setattr(self.model.doc.BaseBend, "Thickness", 2))
        self.assertIsNot(step.Proxy._geometry.mapping, mapping)
        self.assertIs(step.Proxy._geometry.mapping, hole.Proxy._geometry.mapping)
        self.assertAlmostEqual(step.Proxy._geometry.mapping.thickness, 2)
        self.assertTrue(History.get_prepared(step).flat.isValid())
        self.assertTrue(History.get_prepared(step).folded.isValid())

    def test_save_reopen_preserves_native_profile_link_and_rebuild_hash(self):
        step = self.cut()
        name, sketch_name, base_name = step.Name, self.sketch.Name, self.base.Name
        digest, volume = step.PreparedInputHash, step.FlatShape.Volume
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory)/"profile-history.FCStd")
            self.model.doc.saveAs(path)
            self.model.settle()
            App.closeDocument(self.model.doc.Name)
            self.model.doc = App.openDocument(path)
            self.model.settle()
        step = self.model.doc.getObject(name)
        self.sketch = self.model.doc.getObject(sketch_name)
        self.model.sheet = self.model.doc.getObject(base_name)
        self.assertIs(History.get_profile(step), self.sketch)
        self.model.sheet.touch()
        step.touch()
        self.model.recompute()
        self.assertEqual(step.PreparedInputHash, digest)
        self.model.edit(lambda: self.sketch.setDatum(self.radius, App.Units.Quantity("3 mm")))
        self.assertGreater(step.FlatShape.Volume, volume)

    def test_foreign_sketch_is_rejected_before_creating_an_operation(self):
        other = App.newDocument("OtherProfileHistory")
        def close_other():
            self.fixture.wait_for(lambda: not other.Recomputing and not other.RecomputePending
                                  and not other.PresentationUpdateActive)
            App.closeDocument(other.Name)
        self.addCleanup(close_other)
        foreign = other.addObject("Sketcher::SketchObject", "ForeignProfile")
        before = len(self.model.doc.Objects), self.model.doc.UndoCount
        with self.assertRaisesRegex(RuntimeError, "document"):
            self.model.edit(lambda: History.create_profile_step(self.base, foreign))
        self.assertEqual(before, (len(self.model.doc.Objects), self.model.doc.UndoCount))

    def test_folded_profile_coordinates_edit_the_same_native_sketch(self):
        step = self.cut()
        region, folded, flat = self.model.bend_pick()
        local = History.profile_coordinates(step, folded, representation="folded", region=region)
        expected = self.sketch.Placement.inverse().multVec(flat)
        self.assertLess((local-expected).Length, 1e-6)
        before = step.OperationId, len(self.model.doc.Objects), step.FlatShape.Volume
        self.model.edit(lambda: self.sketch.setDatum(self.radius, App.Units.Quantity("3 mm")))
        self.assertEqual(before[:2], (step.OperationId, len(self.model.doc.Objects)))
        self.assertGreater(step.FlatShape.Volume, before[2])

    def test_tree_and_history_open_the_linked_native_sketch(self):
        from SMTests.testSheetTree import TestSheetTree
        step = self.cut()
        gui = Gui.getDocument(self.model.doc.Name)
        def close_editor():
            if gui.getInEdit() is not None:
                gui.resetEdit()
            self.model.settle()
            Gui.Selection.clearSelection()
        self.addCleanup(close_editor)
        tree = TestSheetTree()
        tree.fixture, tree.model = self.fixture, self.model
        tree.sheet, tree.view = step, step.ViewObject.Proxy
        before = self.model.doc.UndoCount, step.Visibility, len(self.model.doc.Objects)
        tree.activate("representation:flat")
        self.assertEqual(tree.view.mode, "flat")
        tree.activate("representation:folded")
        self.assertEqual(tree.view.mode, "folded")
        self.assertEqual(before, (self.model.doc.UndoCount, step.Visibility, len(self.model.doc.Objects)))
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.base)
        tree.activate("profile")
        self.assertIsNotNone(gui.getInEdit())
        self.assertIs(gui.getInEdit().Object, self.sketch)
        close_editor()
        widget = Gui.getMainWindow().findChild(QtWidgets.QListWidget, "VibeCADFeatureTimelineItems")
        self.assertIsNotNone(widget)
        def item():
            return next((widget.item(i) for i in range(widget.count())
                         if widget.item(i).data(QtCore.Qt.UserRole) == step.Name), None)
        self.fixture.wait_for(lambda: item() is not None)
        widget.scrollToItem(item())
        position = QtCore.QPointF(widget.visualItemRect(item()).center())
        Gui.Selection.addSelection(self.base)
        for kind in (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease,
                     QtCore.QEvent.MouseButtonDblClick):
            event = QtGui.QMouseEvent(kind, position, position, QtCore.Qt.LeftButton,
                                     QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
            QtWidgets.QApplication.sendEvent(widget.viewport(), event)
        self.assertIsNotNone(gui.getInEdit())
        self.assertIs(gui.getInEdit().Object, self.sketch)

    def test_history_rollback_restores_the_input_and_keeps_the_authored_sketch(self):
        from SMTests.testSheetCutHistory import TestSheetCutHistory
        step = self.cut()
        helper = TestSheetCutHistory()
        helper.fixture, helper.model = self.fixture, self.model
        helper.button("Previous")
        self.assertFalse(step.Visibility)
        self.assertTrue(self.base.Visibility)
        self.assertIs(self.model.doc.getObject(self.sketch.Name), self.sketch)
        with self.assertRaisesRegex(RuntimeError, "History"):
            History.get_prepared(step)
        helper.button("End")
        self.assertTrue(step.Visibility)
        self.assertFalse(self.base.Visibility)
        self.assertTrue(History.get_prepared(step).flat.isValid())

    def test_missing_sketch_is_repairable_without_replacing_the_cut_identity(self):
        step = self.cut()
        center = next(region.flat_face().CenterOfMass for region in step.Proxy._geometry.mapping.regions
                      if region.kind == "plane")
        last = self.model.edit(lambda: History.create_circle_step(step, center, 2))
        last_volume = last.FlatShape.Volume
        identity, volume = step.OperationId, step.FlatShape.Volume
        self.model.edit(lambda: self.model.doc.removeObject(self.sketch.Name))
        self.assertIn("Invalid", step.State)
        with self.assertRaises((RuntimeError, ValueError)):
            History.get_prepared(step)
        profiles = testProfileCuts.TestProfileCuts()
        profiles.fixture = self.model
        replacement, _ = profiles.slot()
        count = len(self.model.doc.Objects)
        self.model.edit(lambda: History.replace_profile_step(step, replacement))
        self.assertIs(History.get_profile(step), replacement)
        self.assertEqual(step.OperationId, identity)
        self.assertEqual(len(self.model.doc.Objects), count)
        self.assertAlmostEqual(step.FlatShape.Volume, volume, places=6)
        self.assertTrue(History.get_prepared(step).folded.isValid())
        timeline = next(obj for obj in self.model.doc.Objects if obj.TypeId == "App::DocumentTimeline")
        operations = list(timeline.Operations)
        self.assertLess(operations.index(replacement), operations.index(step))
        self.assertLess(operations.index(step), operations.index(last))
        self.assertAlmostEqual(last.FlatShape.Volume, last_volume, places=6)
        self.assertTrue(History.get_prepared(last).flat.isValid())

    def test_sketch_edit_supersedes_pending_downstream_hole_completion(self):
        import weakref
        import SheetMetalOperations as Operations
        first = self.cut()
        center = next(region.flat_face().CenterOfMass for region in first.Proxy._geometry.mapping.regions
                      if region.kind == "plane")
        last = self.model.edit(lambda: History.create_circle_step(first, center, 2))
        unchanged = History.start_radius_edit(last, 2.5,
            expected_revision=Operations.capture_revision(last))
        self.fixture.wait_for(unchanged.future.done)
        self.assertEqual(unchanged.status()["phase"], "ready", unchanged.status())
        with patch.object(Operations, "_poll"):
            run = History.start_radius_edit(last, 3, expected_revision=Operations.capture_revision(last))
            self.model.settle()
            self.model.edit(lambda: self.sketch.setDatum(self.radius, App.Units.Quantity("3 mm")))
        Operations._poll(weakref.ref(run))
        self.assertTrue(run.future.done())
        self.assertEqual(run.status()["phase"], "superseded")
