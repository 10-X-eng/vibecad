# SPDX-License-Identifier: LGPL-2.1-or-later
"""Retained menu operations must also work with native async recompute."""

import importlib
import itertools
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtWidgets

from SMTests import testEditableSheet


class TestSheetLegacyOperations(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("SheetMenuOperations")
        self.doc.UndoMode = 1
        self.helper = testEditableSheet.TestEditableSheet()
        self.helper.doc = self.doc
        self.addCleanup(self.helper.close_document)
        Gui.Selection.clearSelection()
        self.addCleanup(Gui.Selection.clearSelection)
        self.base = self.doc.addObject("Part::Feature", "Plate")
        self.base.Shape = Part.makeBox(50, 40, 1.6)
        self.helper.recompute()

    def face(self, axis):
        return next(f"Face{i}" for i, face in enumerate(self.base.Shape.Faces, 1)
                    if face.normalAt(0, 0).dot(axis) > .99)

    def profile(self):
        sketch = self.doc.addObject("Sketcher::SketchObject", "TabSketch")
        sketch.AttachmentSupport = [(self.base, [self.face(App.Vector(0, 0, 1))])]
        sketch.Placement.Base = App.Vector(0, 0, 1.6)
        points = [App.Vector(x, y) for x, y in
                  ((45, 10), (60, 10), (60, 20), (45, 20), (45, 10))]
        for start, end in zip(points, points[1:]):
            sketch.addGeometry(Part.LineSegment(start, end), False)
        self.helper.recompute()
        return sketch

    def assert_solid(self, obj):
        self.assertNotIn("Invalid", obj.State, obj.State)
        self.assertFalse(obj.Shape.isNull())
        self.assertTrue(obj.Shape.isValid())
        self.assertEqual(len(obj.Shape.Solids), 1)

    def test_sketch_extension_recomputes_on_worker_and_hides_profile(self):
        from SheetMetalExtendCmd import SMExtrudeWall
        sketch = self.profile()
        obj = self.doc.addObject("Part::FeaturePython", "Extend")
        SMExtrudeWall(obj, self.base, [self.face(App.Vector(0, 0, 1))], sketch)
        self.helper.recompute()
        self.assert_solid(obj)
        self.assertAlmostEqual(obj.Shape.Volume, 50 * 40 * 1.6 + 10 * 10 * 1.6)
        self.assertFalse(sketch.ViewObject.Visibility)

    def test_unattached_sketch_disables_extension_without_exception(self):
        from SheetMetalExtendCmd import SMExtendBySketchCommandClass
        sketch = self.doc.addObject("Sketcher::SketchObject", "Unattached")
        Gui.Selection.addSelection(sketch)
        self.assertFalse(SMExtendBySketchCommandClass().IsActive())

    def test_edge_extension_and_reverse_cut_have_expected_volume(self):
        from SheetMetalExtendCmd import SMExtrudeWall
        obj = self.doc.addObject("Part::FeaturePython", "Extend")
        SMExtrudeWall(obj, self.base, [self.face(App.Vector(1, 0, 0))])
        obj.length = 10
        self.helper.recompute()
        self.assert_solid(obj)
        self.assertAlmostEqual(obj.Shape.Volume, 60 * 40 * 1.6)
        obj.reversed = True
        self.helper.recompute()
        self.assert_solid(obj)
        self.assertAlmostEqual(obj.Shape.Volume, 40 * 40 * 1.6)

    def test_all_hem_types_recompute_as_valid_solids(self):
        from SheetMetalHem import SMHem
        for kind in ("Flat", "Open", "Teardrop", "Rolled"):
            with self.subTest(kind=kind):
                obj = self.doc.addObject("Part::FeaturePython", "Hem")
                SMHem(obj, self.base, [self.face(App.Vector(1, 0, 0))])
                obj.HemType = kind
                obj.width, obj.radius = 10, 2
                self.helper.recompute()
                self.assert_solid(obj)
                self.assertGreater(obj.Shape.Volume, self.base.Shape.Volume)

    def test_menu_command_selection_checks_do_not_raise(self):
        modules = ("SheetMetalBaseCmd", "SheetMetalBaseShapeCmd", "SheetMetalBend",
                   "SheetMetalCmd", "SheetMetalHem", "SheetMetalCornerReliefCmd",
                   "SheetMetalExtendCmd", "SheetMetalFoldCmd", "SheetMetalFormingCmd",
                   "SheetMetalJunction", "SheetMetalRelief", "SheetMetalUnfoldCmd",
                   "SketchOnSheetMetalCmd", "SheetMetalSketch", "SheetMetalFromSolid",
                   "ExtrudedCutout")
        sketch = self.doc.addObject("Sketcher::SketchObject", "Unattached")
        checked = 0
        for name in modules:
            module = importlib.import_module(name)
            for cls in vars(module).values():
                if not isinstance(cls, type) or cls.__module__ != name:
                    continue
                if not all(hasattr(cls, attr) for attr in ("Activated", "IsActive", "GetResources")):
                    continue
                command = cls()
                for selection, subelement in ((None, ""), (self.base, ""), (sketch, ""),
                                               (self.base, "Edge1"), (self.base, "Vertex1")):
                    with self.subTest(command=cls.__name__, selection=getattr(selection, "Name", None),
                                      subelement=subelement):
                        Gui.Selection.clearSelection()
                        if selection is not None:
                            Gui.Selection.addSelection(selection, subelement)
                        command.IsActive()
                checked += 1
        self.assertGreaterEqual(checked, 18)

    def test_extension_menu_accept_cancel_and_undo(self):
        import SheetMetalExtendCmd as Extend
        previous = Gui.activeWorkbench().name()
        Gui.activateWorkbench("SMWorkbench")
        self.addCleanup(lambda: Gui.activateWorkbench(previous))
        self.addCleanup(Gui.Control.closeDialog)
        for accept in (False, True):
            with self.subTest(accept=accept):
                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(self.base, self.face(App.Vector(1, 0, 0)))
                panels = []
                original = Extend.SMExtendWallTaskPanel
                def capture(obj):
                    panel = original(obj)
                    panels.append(panel)
                    return panel
                before = len(self.doc.Objects)
                with patch.object(Extend, "SMExtendWallTaskPanel", side_effect=capture):
                    Gui.runCommand("SheetMetal_Extrude")
                self.assertEqual(len(panels), 1)
                panel = panels[0]
                self.helper.settle()
                self.assert_solid(panel.obj)
                name = panel.obj.Name
                self.assertTrue(panel.form.Length.setProperty("rawValue", 15.0))
                self.helper.settle()
                role = QtWidgets.QDialogButtonBox.Ok if accept else QtWidgets.QDialogButtonBox.Cancel
                buttons = [box.button(role) for box in Gui.getMainWindow().findChildren(QtWidgets.QDialogButtonBox)
                           if box.isVisible() and box.button(role) is not None]
                self.assertEqual(len(buttons), 1)
                buttons[0].click()
                self.helper.settle()
                if accept:
                    self.assert_solid(self.doc.getObject(name))
                    self.assertAlmostEqual(self.doc.getObject(name).Shape.Volume, 65 * 40 * 1.6)
                    self.doc.undo()
                    self.helper.settle()
                    self.assertIsNone(self.doc.getObject(name))
                    self.doc.redo()
                    self.helper.settle()
                    self.assert_solid(self.doc.getObject(name))
                else:
                    self.assertEqual(len(self.doc.Objects), before)
                    self.assertTrue(self.base.ViewObject.Visibility)
                self.assertFalse(self.doc.HasPendingTransaction)

    def test_sketch_extension_edit_undo_redo_and_reopen(self):
        from SheetMetalExtendCmd import SMExtrudeWall
        sketch = self.profile()
        obj = self.doc.addObject("Part::FeaturePython", "Extend")
        SMExtrudeWall(obj, self.base, [self.face(App.Vector(0, 0, 1))], sketch)
        self.helper.recompute()
        original = obj.Shape.Volume
        self.doc.openTransaction("Move tab profile")
        sketch.Placement.Base = App.Vector(2, 0, 1.6)
        self.doc.commitTransaction()
        self.helper.recompute()
        self.assert_solid(obj)
        edited = obj.Shape.Volume
        self.assertGreater(edited, original)
        self.doc.undo()
        self.helper.recompute()
        self.assert_solid(obj)
        self.assertAlmostEqual(obj.Shape.Volume, original)
        self.doc.redo()
        self.helper.recompute()
        self.assertAlmostEqual(obj.Shape.Volume, edited)
        with tempfile.TemporaryDirectory() as directory:
            filename = str(Path(directory) / "extension.FCStd")
            self.doc.saveAs(filename)
            self.helper.settle()
            App.closeDocument(self.doc.Name)
            self.doc = self.helper.doc = App.openDocument(filename)
            self.helper.settle()
            obj = self.doc.getObject("Extend")
            obj.touch()
            self.helper.recompute()
            self.assert_solid(obj)
            self.assertAlmostEqual(obj.Shape.Volume, edited)
            self.assertIs(obj.Sketch, self.doc.getObject("TabSketch"))

    def test_solid_bend_rounds_a_sharp_sheet_corner(self):
        from SheetMetalBend import SMSolidBend
        wall = Part.makeBox(1.6, 40, 25, App.Vector(48.4, 0, 0))
        self.base.Shape = self.base.Shape.fuse(wall).removeSplitter()
        edge = next(f"Edge{i}" for i, e in enumerate(self.base.Shape.Edges, 1)
                    if e.CenterOfMass.isEqual(App.Vector(50, 20, 0), 1e-7))
        obj = self.doc.addObject("Part::FeaturePython", "SolidBend")
        SMSolidBend(obj, self.base, [edge])
        obj.radius = 2
        self.helper.recompute()
        self.assert_solid(obj)
        self.assertEqual(sum(isinstance(f.Surface, Part.Cylinder) for f in obj.Shape.Faces), 2)

    def test_junction_opens_a_box_corner_without_losing_the_bottom(self):
        from SheetMetalJunction import SMJunction
        self.base.Shape = Part.makeBox(50, 40, 25).cut(
            Part.makeBox(46.8, 36.8, 25, App.Vector(1.6, 1.6, 1.6)))
        edge = next(f"Edge{i}" for i, e in enumerate(self.base.Shape.Edges, 1)
                    if e.CenterOfMass.isEqual(App.Vector(0, 0, 12.5), 1e-7))
        obj = self.doc.addObject("Part::FeaturePython", "Junction")
        SMJunction(obj, self.base, [edge])
        self.helper.recompute()
        self.assert_solid(obj)
        self.assertLess(obj.Shape.Volume, self.base.Shape.Volume)

    def test_solid_corner_relief_removes_material(self):
        from SheetMetalRelief import SMRelief
        obj = self.doc.addObject("Part::FeaturePython", "Relief")
        SMRelief(obj, self.base, ["Vertex1"])
        for size in (.5, 2, 4):
            with self.subTest(size=size):
                obj.relief = size
                self.helper.recompute()
                self.assert_solid(obj)
                self.assertAlmostEqual(obj.Shape.Volume,
                                       self.base.Shape.Volume - size * size * min(size, 1.6))

    def test_legacy_sketch_cut_and_extruded_cutout_remove_expected_material(self):
        from ExtrudedCutout import ExtrudedCutout
        from SketchOnSheetMetalCmd import SMSketchOnSheet
        sketch = self.profile()
        sketch.Placement.Base = App.Vector(-20, 0, 1.6)
        top = self.face(App.Vector(0, 0, 1))
        for kind in ("sketch_on_sheet", "extruded_cutout"):
            with self.subTest(kind=kind):
                obj = self.doc.addObject("Part::FeaturePython", "Cut")
                if kind == "sketch_on_sheet":
                    SMSketchOnSheet(obj, self.base, [top], sketch)
                else:
                    ExtrudedCutout(obj, sketch, (self.base, [top]))
                self.helper.recompute()
                self.assert_solid(obj)
                self.assertAlmostEqual(obj.Shape.Volume, (50 * 40 - 15 * 10) * 1.6)

    def test_forming_tool_creates_a_boss_and_can_be_suppressed(self):
        from SheetMetalFormingCmd import SMBendWall
        tool = self.doc.addObject("Part::Feature", "FormTool")
        tool.Shape = Part.makeCylinder(5, 5)
        tool_face = next(f"Face{i}" for i, f in enumerate(tool.Shape.Faces, 1)
                         if isinstance(f.Surface, Part.Plane) and f.normalAt(0, 0).z > .99)
        obj = self.doc.addObject("Part::FeaturePython", "Forming")
        SMBendWall(obj, self.base, [self.face(App.Vector(0, 0, 1))], tool, [tool_face])
        self.helper.recompute()
        self.assert_solid(obj)
        self.assertGreater(obj.Shape.BoundBox.ZLength, 1.6)
        obj.SuppressFeature = True
        self.helper.recompute()
        self.assert_solid(obj)
        self.assertAlmostEqual(obj.Shape.Volume, self.base.Shape.Volume)

    def test_extruded_cutout_missing_profile_is_not_reported_as_valid(self):
        from ExtrudedCutout import ExtrudedCutout
        sketch = self.profile()
        sketch.Placement.Base = App.Vector(-20, 0, 1.6)
        obj = self.doc.addObject("Part::FeaturePython", "Cut")
        ExtrudedCutout(obj, sketch, (self.base, [self.face(App.Vector(0, 0, 1))]))
        self.helper.recompute()
        self.assert_solid(obj)
        volume = obj.Shape.Volume
        self.doc.openTransaction("Break cut profile")
        obj.Sketch = None
        self.doc.commitTransaction()
        self.helper.recompute()
        self.assertIn("Invalid", obj.State)
        self.assertIn("Sketch", obj.getStatusString())
        self.doc.undo()
        self.helper.recompute()
        self.assert_solid(obj)
        self.assertIs(obj.Sketch, sketch)
        self.assertAlmostEqual(obj.Shape.Volume, volume)

    def test_bend_corner_relief_shapes_keep_a_valid_sheet(self):
        from SheetMetalBaseShapeCmd import SMBaseShape
        from SheetMetalCornerReliefCmd import SMCornerRelief
        source = self.doc.addObject("Part::FeaturePython", "Tub")
        SMBaseShape(source)
        source.shapeType = "Tub"
        source.width, source.length, source.height = 50, 70, 25
        source.thickness, source.radius = 1.6, 2
        self.helper.recompute()
        planar = max((f for f in source.Shape.Faces if isinstance(f.Surface, Part.Plane)),
                     key=lambda f: f.Area)
        edges = [(i, e) for i, e in enumerate(source.Shape.Edges, 1)
                 if any(e.isSame(item) for item in planar.Edges)
                 and any(isinstance(f.Surface, Part.Cylinder)
                         for f in source.Shape.ancestorsOfType(e, Part.Face))]
        pair = next((a, b) for a, b in itertools.combinations(edges, 2)
                    if abs(a[1].tangentAt(0).dot(b[1].tangentAt(0))) < .1)
        for kind in ("Circle", "Square", "Circle-Scaled", "Square-Scaled", "Weld", "Weld-Scaled"):
            with self.subTest(kind=kind):
                obj = self.doc.addObject("Part::FeaturePython", "BendRelief")
                SMCornerRelief(obj, source, [f"Edge{i}" for i, _ in pair])
                obj.ReliefSketch = kind
                obj.Size = 4
                self.helper.recompute()
                self.assert_solid(obj)
                if kind.startswith("Weld"):
                    # Weld relief reconstructs/fuses material at the corner;
                    # unlike a circular cut its total volume can increase.
                    self.assertNotAlmostEqual(obj.Shape.Volume, source.Shape.Volume)
                else:
                    self.assertLess(obj.Shape.Volume, source.Shape.Volume)

    def test_cutout_task_keeps_failed_edit_open_for_repair(self):
        import ExtrudedCutout as Cutout
        previous = Gui.activeWorkbench().name()
        Gui.activateWorkbench("SMWorkbench")
        self.addCleanup(lambda: Gui.activateWorkbench(previous))
        self.addCleanup(Gui.Control.closeDialog)
        sketch = self.profile()
        sketch.Placement.Base = App.Vector(-20, 0, 1.6)
        self.helper.recompute()
        Gui.Selection.addSelection(self.base, self.face(App.Vector(0, 0, 1)))
        Gui.Selection.addSelection(sketch)
        panels = []
        original = Cutout.SMExtrudedCutoutTaskPanel
        def capture(obj):
            panel = original(obj)
            panels.append(panel)
            return panel
        with patch.object(Cutout, "SMExtrudedCutoutTaskPanel", side_effect=capture):
            Gui.runCommand("SheetMetal_AddCutout")
        self.assertEqual(len(panels), 1)
        panel = panels[0]
        self.helper.settle()
        self.assert_solid(panel.obj)
        panel.obj.Sketch = None
        self.helper.recompute()
        self.assertIn("Invalid", panel.obj.State)
        def click_ok():
            buttons = [box.button(QtWidgets.QDialogButtonBox.Ok)
                       for box in Gui.getMainWindow().findChildren(QtWidgets.QDialogButtonBox)
                       if box.isVisible() and box.button(QtWidgets.QDialogButtonBox.Ok) is not None]
            self.assertEqual(len(buttons), 1)
            buttons[0].click()
            self.helper.settle()
        click_ok()
        self.assertTrue(Gui.Control.activeDialog(), "Failed edit closed instead of allowing repair")
        self.assertTrue(self.doc.HasPendingTransaction)
        panel.obj.Sketch = sketch
        self.helper.recompute()
        self.assert_solid(panel.obj)
        name = panel.obj.Name
        click_ok()
        self.assertFalse(Gui.Control.activeDialog())
        self.assertFalse(self.doc.HasPendingTransaction)
        self.assert_solid(self.doc.getObject(name))

    def test_legacy_base_shape_task_accept_cancel_and_undo(self):
        import SheetMetalBaseShapeCmd as BaseShape
        previous = Gui.activeWorkbench().name()
        Gui.activateWorkbench("SMWorkbench")
        self.addCleanup(lambda: Gui.activateWorkbench(previous))
        self.addCleanup(Gui.Control.closeDialog)
        for accept in (False, True):
            with self.subTest(accept=accept):
                panels = []
                original = BaseShape.BaseShapeTaskPanel
                def capture(obj):
                    panel = original(obj)
                    panels.append(panel)
                    return panel
                before = {obj.Name for obj in self.doc.Objects}
                with patch.object(BaseShape, "BaseShapeTaskPanel", side_effect=capture):
                    Gui.runCommand("SheetMetal_BaseShape")
                self.assertEqual(len(panels), 1)
                panel = panels[0]
                self.helper.settle()
                self.assert_solid(panel.obj)
                name = panel.obj.Name
                panel.form.chkNewBody.setChecked(True)
                role = QtWidgets.QDialogButtonBox.Ok if accept else QtWidgets.QDialogButtonBox.Cancel
                buttons = [box.button(role) for box in Gui.getMainWindow().findChildren(QtWidgets.QDialogButtonBox)
                           if box.isVisible() and box.button(role) is not None]
                self.assertEqual(len(buttons), 1)
                buttons[0].click()
                self.helper.settle()
                self.assertFalse(Gui.Control.activeDialog())
                self.assertFalse(self.doc.HasPendingTransaction)
                if accept:
                    self.assert_solid(self.doc.getObject(name))
                    self.assertIsNotNone(self.doc.getObject(name).getParentGeoFeatureGroup())
                    self.doc.undo()
                    self.helper.settle()
                    self.assertEqual({obj.Name for obj in self.doc.Objects}, before)
                    self.doc.redo()
                    self.helper.settle()
                    self.assert_solid(self.doc.getObject(name))
                else:
                    self.assertEqual({obj.Name for obj in self.doc.Objects}, before)

    def test_legacy_menu_tasks_accept_cancel_undo_and_redo(self):
        previous = Gui.activeWorkbench().name()
        Gui.activateWorkbench("SMWorkbench")
        self.addCleanup(lambda: Gui.activateWorkbench(previous))
        self.addCleanup(Gui.Control.closeDialog)
        sketch = self.profile()
        sketch.Placement.Base = App.Vector(-20, 0, 1.6)
        self.helper.recompute()
        edge = next(f"Edge{i}" for i, e in enumerate(self.base.Shape.Edges, 1)
                    if e.CenterOfMass.isEqual(App.Vector(50, 20, 1.6), 1e-7))
        top = self.face(App.Vector(0, 0, 1))
        solid = self.doc.addObject("Part::Feature", "Solid")
        solid.Shape = Part.makeBox(50, 40, 30)
        open_face = next(f"Face{i}" for i, f in enumerate(solid.Shape.Faces, 1)
                         if f.normalAt(0, 0).z > .99)
        tool = self.doc.addObject("Part::Feature", "FormTool")
        tool.Shape = Part.makeCylinder(5, 5)
        tool_face = next(f"Face{i}" for i, f in enumerate(tool.Shape.Faces, 1)
                         if isinstance(f.Surface, Part.Plane) and f.normalAt(0, 0).z > .99)
        self.helper.recompute()
        cases = (
            ("SheetMetalBaseCmd", "SMBaseBendTaskPanel", "SheetMetal_AddBase", [(sketch, "")]),
            ("SheetMetalFromSolid", "SMFromSolidTaskPanel", "SheetMetal_FromSolid", [(solid, open_face)]),
            ("SheetMetalHem", "SMHemTaskPanel", "SheetMetal_AddHem", [(self.base, edge)]),
            ("SheetMetalCmd", "SMBendWallTaskPanel", "SheetMetal_AddWall", [(self.base, edge)]),
            ("SheetMetalRelief", "SMReliefTaskPanel", "SheetMetal_AddRelief", [(self.base, "Vertex1")]),
            ("ExtrudedCutout", "SMExtrudedCutoutTaskPanel", "SheetMetal_AddCutout", [(self.base, top), (sketch, "")]),
            ("SketchOnSheetMetalCmd", "SMWrappedCutoutTaskPanel", "SheetMetal_SketchOnSheet", [(self.base, top), (sketch, "")]),
            ("SheetMetalFormingCmd", "SMFormingWallTaskPanel", "SheetMetal_Forming", [(self.base, top), (tool, tool_face)]),
        )
        for module_name, panel_name, command, selection in cases:
            for accept in (False, True):
                with self.subTest(command=command, accept=accept):
                    Gui.Selection.clearSelection()
                    for obj, sub in selection:
                        Gui.Selection.addSelection(obj, sub)
                    panels = []
                    module = importlib.import_module(module_name)
                    original = getattr(module, panel_name)
                    def capture(*args, **kwargs):
                        panel = original(*args, **kwargs)
                        panels.append(panel)
                        return panel
                    before = {obj.Name for obj in self.doc.Objects}
                    with patch.object(module, panel_name, side_effect=capture):
                        Gui.runCommand(command)
                    self.assertEqual(len(panels), 1)
                    panel = panels[0]
                    self.helper.settle()
                    self.assert_solid(panel.obj)
                    name, volume = panel.obj.Name, panel.obj.Shape.Volume
                    role = QtWidgets.QDialogButtonBox.Ok if accept else QtWidgets.QDialogButtonBox.Cancel
                    buttons = [box.button(role) for box in Gui.getMainWindow().findChildren(QtWidgets.QDialogButtonBox)
                               if box.isVisible() and box.button(role) is not None]
                    self.assertEqual(len(buttons), 1)
                    buttons[0].click()
                    self.helper.settle()
                    self.assertFalse(Gui.Control.activeDialog())
                    self.assertFalse(self.doc.HasPendingTransaction)
                    if accept:
                        self.assert_solid(self.doc.getObject(name))
                        self.doc.undo()
                        self.helper.settle()
                        self.assertEqual({obj.Name for obj in self.doc.Objects}, before)
                        self.doc.redo()
                        self.helper.settle()
                        self.assert_solid(self.doc.getObject(name))
                        self.assertAlmostEqual(self.doc.getObject(name).Shape.Volume, volume)
                        self.doc.undo()
                        self.helper.settle()
                    else:
                        self.assertEqual({obj.Name for obj in self.doc.Objects}, before)
