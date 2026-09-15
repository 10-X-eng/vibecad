# SPDX-License-Identifier: MIT
"""Run in a separate FreeCAD GUI test process with SheetMetal on sys.path.

These tests create and close their own synthetic document. See SMOKE.md.
"""
import unittest
from unittest.mock import patch
import tempfile
from pathlib import Path
import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore
import preset_update
import bend_actions
import pending_unfold


class TestPresetDocument(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("PresetProbe")
        self.doc.UndoMode = 1

    def settle(self):
        for _ in range(10000):
            QtCore.QCoreApplication.processEvents()
            if (not self.doc.Recomputing and not self.doc.RecomputePending
                    and not self.doc.CooperativeMutationActive and self.doc.isClosable()):
                return
        self.fail("Document did not reach its recompute completion boundary")

    def tearDown(self):
        self.settle()
        pending_unfold.clear_pending_unfold_sync()
        Gui.Selection.clearSelection()
        App.closeDocument(self.doc.Name)

    def test_native_transaction_single_undo(self):
        def edit():
            obj = self.doc.addObject("Part::Box", "PresetBox")
            obj.Length = 8
        preset_update.run_document_update(self.doc, edit)
        self.settle()
        self.assertEqual(self.doc.UndoCount, 1)
        self.assertEqual(self.doc.getBookedTransactionID(), 0)
        self.assertFalse(self.doc.CooperativeMutationActive)
        self.assertAlmostEqual(self.doc.PresetBox.Shape.Volume, 800)
        self.doc.undo()
        self.assertIsNone(self.doc.getObject("PresetBox"))

    def test_real_material_lookup_after_async_apply(self):
        from SheetMetalKfactor import KFactorLookupTable
        entry = dict(t=0.063, r=0.063, k=0.42, bd=0.1)
        bend_actions.apply_preset(entry, mat_name="5052 Aluminum", apply_bends=False)
        self.settle()
        sheet = self.doc.getObject("material_SCS_5052_063")
        self.assertIsNotNone(sheet)
        self.assertAlmostEqual(float(sheet.get("B2")), 0.42)
        table = KFactorLookupTable(sheet.Label)
        self.assertEqual(table.k_factor_lookup[1.0], 0.42)
        self.assertEqual(self.doc.UndoCount, 1)
        self.doc.undo()
        self.assertIsNone(self.doc.getObject("material_SCS_5052_063"))
        self.assertIsNone(pending_unfold.get_pending())

    def test_late_unfold_properties_retain_exact_object(self):
        pending_unfold.remember_pending_unfold_sync(0.42, "", self.doc.Name)
        obj = self.doc.addObject("App::FeaturePython", "Unfold")
        self.assertIs(self.doc.getObject(obj.Name), obj)
        obj.addProperty("App::PropertyFloat", "KFactor")
        obj.addProperty("App::PropertyString", "MaterialSheet")
        obj.KFactor = 0.4
        for _ in range(5):
            QtCore.QCoreApplication.processEvents()
        self.settle()
        self.assertAlmostEqual(obj.KFactor, 0.42)

    def test_real_sheetmetal_bend_and_unfold(self):
        self._check_real_sheetmetal_bend_and_unfold()

    def test_real_unfold_recompute_survives_active_document_change(self):
        self._check_real_sheetmetal_bend_and_unfold(switch_document=True)

    def test_v2_generated_sketches_stay_in_the_owner_document(self):
        import SheetMetalTools
        with patch.object(SheetMetalTools, "use_old_unfolder", return_value=False):
            self._check_real_sheetmetal_bend_and_unfold(
                switch_document=True, generate_sketch=True)

    def test_v1_generated_sketches_stay_in_the_owner_document(self):
        import SheetMetalTools
        with patch.object(SheetMetalTools, "use_old_unfolder", return_value=True):
            self._check_real_sheetmetal_bend_and_unfold(
                switch_document=True, generate_sketch=True)

    def _check_real_sheetmetal_bend_and_unfold(self, switch_document=False, generate_sketch=False):
        import Part
        import Sketcher
        from SheetMetalBaseCmd import SMBaseBend
        from SheetMetalUnfoldCmd import SMUnfold, NewUnfolderAvailable
        self.assertTrue(NewUnfolderAvailable)
        sketch = self.doc.addObject("Sketcher::SketchObject", "Profile")
        sketch.addGeometry(Part.LineSegment(App.Vector(0, 0, 0), App.Vector(40, 0, 0)), False)
        sketch.addGeometry(Part.LineSegment(App.Vector(40, 0, 0), App.Vector(40, 30, 0)), False)
        bend = self.doc.addObject("Part::FeaturePython", "BaseBend")
        SMBaseBend(bend, sketch)
        bend.Thickness = 1.6
        bend.Length = 30
        self.doc.recompute()
        self.settle()
        self.assertTrue(bend.Shape.isValid())
        self.assertGreater(bend.Shape.Volume, 0)
        plane_faces = [(face.Area, i + 1) for i, face in enumerate(bend.Shape.Faces)
                       if isinstance(face.Surface, Part.Plane)]
        face = "Face%d" % max(plane_faces)[1]
        unfold = self.doc.addObject("Part::FeaturePython", "Unfold")
        SMUnfold(unfold, bend, [face])
        unfold.MaterialSheet = "_manual"
        unfold.GenerateSketch = generate_sketch
        unfold.SeparateSketchLayers = generate_sketch
        self.doc.recompute()
        self.settle()
        self.assertTrue(unfold.Shape.isValid())
        self.assertGreater(unfold.Shape.Volume, 0)
        owned_sketch_names = list(unfold.UnfoldSketches)
        if generate_sketch:
            self.assertTrue(owned_sketch_names)
        other = App.newDocument("OtherPresetDocument")
        other_name = other.Name
        self.addCleanup(lambda: App.closeDocument(other_name)
                        if other_name in App.listDocuments() else None)
        # Same names in another document must not be reused or overwritten.
        for name in owned_sketch_names:
            unrelated = other.addObject("Sketcher::SketchObject", name)
            unrelated.addGeometry(Part.LineSegment(App.Vector(), App.Vector(999, 0, 0)), False)
        other.recompute()
        unrelated_before = [(obj.Name, str(getattr(obj, "Geometry", None))) for obj in other.Objects]
        App.setActiveDocument(self.doc.Name)
        Gui.Selection.addSelection(bend)
        self.settle()
        bend_actions.apply_preset(dict(t=0.063, r=0.063, k=0.42, bd=0.1), mat_name="5052 Aluminum")
        if switch_document:
            App.setActiveDocument(other.Name)
        self.settle()
        self.assertIsNone(other.getObject("material_SCS_5052_063"))
        unrelated_after = [(obj.Name, str(getattr(obj, "Geometry", None))) for obj in other.Objects]
        active_after = App.ActiveDocument.Name
        other_name = other.Name
        App.closeDocument(other.Name)
        self.assertEqual(unrelated_before, unrelated_after)
        if switch_document:
            self.assertEqual(active_after, other_name)
        if generate_sketch:
            self.assertTrue(unfold.UnfoldSketches)
            for name in unfold.UnfoldSketches:
                self.assertIsNotNone(self.doc.getObject(name))
        App.setActiveDocument(self.doc.Name)
        self.assertAlmostEqual(bend.Radius.Value, 0.063 * 25.4)
        self.assertAlmostEqual(unfold.KFactor, 0.42)
        self.assertEqual(unfold.MaterialSheet, "material_SCS_5052_063")
        self.assertTrue(unfold.Shape.isValid())
        self.assertGreater(unfold.Shape.Volume, 0)
        self.assertNotIn("Invalid", unfold.State)
        with tempfile.TemporaryDirectory() as directory:
            filename = str(Path(directory) / "preset.FCStd")
            self.doc.saveAs(filename)
            self.settle()
            App.closeDocument(self.doc.Name)
            self.doc = App.openDocument(filename)
            self.settle()
            self.assertAlmostEqual(self.doc.BaseBend.Radius.Value, 0.063 * 25.4)
            self.assertEqual(self.doc.Unfold.MaterialSheet, "material_SCS_5052_063")
            self.assertTrue(self.doc.Unfold.Shape.isValid())
            self.assertGreater(self.doc.Unfold.Shape.Volume, 0)

    def test_real_sheetmetal_toolbar_is_installed_once(self):
        import sheetmetal_integration
        import SheetMetalTools
        previous_workbench = Gui.activeWorkbench().name()
        if "SMWorkbench" not in Gui.listWorkbenches():
            init_path = Path(SheetMetalTools.mod_path) / "InitGui.py"
            namespace = {"Workbench": Gui.Workbench, "__file__": str(init_path)}
            exec(compile(init_path.read_text(), str(init_path), "exec"), namespace)
        for _ in range(3):
            Gui.activateWorkbench("SMWorkbench")
            sheetmetal_integration.setup()
            self.settle()
            Gui.activateWorkbench(previous_workbench)
        Gui.activateWorkbench("SMWorkbench")
        wb = Gui.getWorkbench("SMWorkbench")
        self.assertEqual(wb.getToolbarItems()["Bend Presets"], ["SCS_ShowCustomPresets"])
