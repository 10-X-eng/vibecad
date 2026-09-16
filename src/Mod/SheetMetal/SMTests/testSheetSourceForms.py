# SPDX-License-Identifier: LGPL-2.1-or-later
"""Ribbon creation uses frozen user choices and the shared async source service."""

import unittest
import os
from pathlib import Path
from unittest.mock import patch

import FreeCAD as App
import FreeCADGui as Gui
import Part

from SMTests import testSheetSourceCreation


class TestSheetSourceForms(unittest.TestCase):
    def setUp(self):
        self.case = testSheetSourceCreation.TestSheetSourceCreation()
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.doc, self.model = self.case.doc, self.case.model
        self.presentation = self.case.fixture.fixture.fixture
        previous = Gui.activeWorkbench().name()
        self.addCleanup(lambda: Gui.activateWorkbench(previous))
        Gui.activateWorkbench("SMWorkbench")
        Gui.Selection.clearSelection()
        self.addCleanup(Gui.Selection.clearSelection)

    def command(self, name):
        import SheetMetalSourceCreationGui as Forms
        original, panels = Forms.SourceCreatePanel, []
        def panel(*args, **kwargs):
            result = original(*args, **kwargs)
            panels.append(result)
            self.addCleanup(result.reject)
            return result
        with patch.object(Forms, "SourceCreatePanel", side_effect=panel):
            Gui.runCommand(name)
        self.assertEqual(len(panels), 1)
        return panels[0]

    def finish(self, panel):
        if panel.run is None:
            self.fail(panel.message.text())
        self.assertFalse(self.doc.HasPendingTransaction)
        self.presentation.wait_for(panel.run.future.done)
        self.assertEqual(panel.run.status()["phase"], "ready", panel.run.status())
        return self.doc.getObject(panel.run.status()["object_name"])

    def test_ribbon_opens_creation_without_mutation_and_keeps_legacy_commands(self):
        from VibeCADRibbonSurface import read_active_ribbon_surface
        self.presentation.wait_for(lambda: read_active_ribbon_surface().surface_id == "sheet_metal")
        surface = read_active_ribbon_surface()
        commands = {action.command_id for action in surface.actions}
        self.assertTrue({"SheetMetal_CreateBaseShape", "SheetMetal_CreateFromSketch",
                         "SheetMetal_CreateFromSolid"}.issubset(commands))
        for legacy in ("SheetMetal_BaseShape", "SheetMetal_AddBase", "SheetMetal_FromSolid"):
            self.assertIsNotNone(Gui.Command.get(legacy))
        before = tuple(self.doc.Objects), self.doc.UndoCount
        panel = self.command("SheetMetal_CreateBaseShape")
        self.assertEqual(before, (tuple(self.doc.Objects), self.doc.UndoCount))
        self.assertFalse(self.doc.HasPendingTransaction)
        panel.fields["height"].setValue(32)
        self.assertTrue(panel.form.grab().save(str(
            Path(os.environ["VIBECAD_TEST_OUTPUT"])/"source-creation-form.png")))
        panel.create_button.click()
        obj = self.finish(panel)
        self.assertEqual(float(obj.height), 32)
        self.assertTrue(obj.Shape.isValid())
        self.assertEqual(obj.VibeCADTimelineEditCommand, "SheetMetal_EditSource")
        self.assertEqual(self.doc.UndoCount, before[1]+1)
        after = tuple(self.doc.Objects), self.doc.UndoCount
        panel.create()
        self.assertEqual(after, (tuple(self.doc.Objects), self.doc.UndoCount))

    def test_flange_ribbon_keeps_selected_boundary_and_uses_degrees(self):
        from VibeCADRibbonSurface import read_active_ribbon_surface
        source, arguments = self.case.flange_input()
        surface = read_active_ribbon_surface()
        self.assertIn("SheetMetal_CreateFlange", {action.command_id for action in surface.actions})
        Gui.Selection.addSelection(source, arguments["subelements"][0])
        before = tuple(self.doc.Objects), self.doc.UndoCount
        panel = self.command("SheetMetal_CreateFlange")
        self.assertEqual((tuple(self.doc.Objects), self.doc.UndoCount), before)
        self.assertIn("°", panel.fields["bend_angle"].suffix())
        panel.fields["bend_angle"].setValue(60)
        panel.fields["length"].setValue(25)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.model.sheet)
        panel.create_button.click()
        flange = self.finish(panel)
        self.assertEqual(flange.baseObject, (source, arguments["subelements"]))
        self.assertEqual(float(flange.angle), 60)
        self.assertEqual(float(flange.length), 25)
        self.assertTrue(flange.Shape.isValid())
        self.assertEqual(self.doc.UndoCount, before[1]+1)

    def test_sketch_creation_retains_the_selected_sketch_and_its_body(self):
        def source():
            body = self.doc.addObject("PartDesign::Body", "FormBody")
            sketch = self.doc.addObject("Sketcher::SketchObject", "FormSketch")
            body.addObject(sketch)
            sketch.addGeometry(Part.LineSegment(App.Vector(), App.Vector(40, 0)), False)
            sketch.addGeometry(Part.LineSegment(App.Vector(40, 0), App.Vector(40, 30)), False)
            return body, sketch
        body, sketch = self.model.edit(source)
        Gui.Selection.addSelection(sketch)
        panel = self.command("SheetMetal_CreateFromSketch")
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.model.sheet)
        panel.fields["length"].setValue(45)
        panel.create_button.click()
        obj = self.finish(panel)
        self.assertIs(obj.BendSketch, sketch)
        self.assertIs(obj.getParentGeoFeatureGroup(), body)
        self.assertIs(body.Tip, obj)
        self.assertEqual(float(obj.Length), 45)
        self.assertEqual(obj.ViewObject.Proxy.claimChildren(), [sketch])

    def test_internal_fold_ribbon_freezes_sheet_skin_and_sketch_selection(self):
        from VibeCADRibbonSurface import read_active_ribbon_surface
        from SMTests import testSheetFoldSource
        helper = testSheetFoldSource.TestSheetFoldSource()
        helper.fixture, helper.doc = self.case, self.doc
        source, sketch, arguments = helper.fold_input()
        self.assertIn("SheetMetal_CreateFold", {action.command_id for action in read_active_ribbon_surface().actions})
        Gui.Selection.addSelection(sketch)
        Gui.Selection.addSelection(source, arguments["subelements"][0])
        before = self.doc.UndoCount
        panel = self.command("SheetMetal_CreateFold")
        self.assertIn("°", panel.fields["bend_angle"].suffix())
        self.assertEqual(panel.fields["k_factor"].suffix(), "")
        panel.fields["bend_angle"].setValue(60)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.model.sheet)
        panel.create_button.click()
        fold = self.finish(panel)
        self.assertIs(fold.baseObject[0], source)
        self.assertIs(fold.BendLine, sketch)
        self.assertEqual(float(fold.angle), 60)
        self.assertTrue(fold.Shape.isValid())
        self.assertEqual(self.doc.UndoCount, before+1)

    def test_solid_conversion_retains_the_selected_faces(self):
        def source():
            obj = self.doc.addObject("Part::Feature", "FormSolid")
            obj.Shape = Part.makeBox(50, 40, 30)
            return obj
        solid = self.model.edit(source)
        face = next(f"Face{i+1}" for i, item in enumerate(solid.Shape.Faces) if item.normalAt(0, 0).z > .9)
        Gui.Selection.addSelection(solid, face)
        panel = self.command("SheetMetal_CreateFromSolid")
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.model.sheet)
        panel.create_button.click()
        obj = self.finish(panel)
        self.assertEqual(obj.baseObject, (solid, [face]))
        self.assertEqual(obj.ViewObject.Proxy.claimChildren(), [solid])

    def test_stale_creation_form_cannot_mutate_the_document(self):
        panel = self.command("SheetMetal_CreateBaseShape")
        before = tuple(self.doc.Objects), self.doc.UndoCount
        self.model.sheet.Label = "Changed while the form was open"
        panel.create_button.click()
        self.assertIsNone(panel.run)
        self.assertIn("changed", panel.message.text())
        self.assertEqual(before, (tuple(self.doc.Objects), self.doc.UndoCount))

    def test_form_cannot_redirect_creation_to_another_active_document(self):
        panel = self.command("SheetMetal_CreateBaseShape")
        other = App.newDocument("OtherSourceForm")
        self.addCleanup(lambda: self.case.fixture.close_other(other))
        before = tuple(self.doc.Objects), self.doc.UndoCount
        panel.create_button.click()
        self.assertIsNone(panel.run)
        self.assertEqual(before, (tuple(self.doc.Objects), self.doc.UndoCount))
        self.assertEqual(len(other.Objects), 0)

    def test_closing_a_pending_form_does_not_cancel_committed_geometry(self):
        panel = self.command("SheetMetal_CreateBaseShape")
        panel.create_button.click()
        self.assertIsNotNone(panel.run)
        panel.reject()
        obj = self.finish(panel)
        self.assertTrue(obj.Shape.isValid())

    def test_base_shape_can_use_an_explicit_part_container(self):
        container = self.model.edit(lambda: self.doc.addObject("App::Part", "FormPart"))
        panel = self.command("SheetMetal_CreateBaseShape")
        index = panel.container.findData(container.Name)
        self.assertGreaterEqual(index, 0)
        panel.container.setCurrentIndex(index)
        panel.create_button.click()
        obj = self.finish(panel)
        self.assertIs(obj.getParentGeoFeatureGroup(), container)

    def test_base_shape_controls_only_show_applicable_options(self):
        panel = self.command("SheetMetal_CreateBaseShape")
        self.assertFalse(panel.fields["height"].isHidden())
        self.assertTrue(panel.fields["flange_width"].isHidden())
        self.assertTrue(panel.fields["fill_gaps"].isHidden())
        panel.fields["shape_type"].setCurrentText("Flat")
        self.assertTrue(panel.fields["height"].isHidden())
        self.assertTrue(panel.fields["bend_radius"].isHidden())
        panel.fields["shape_type"].setCurrentText("Hat")
        self.assertFalse(panel.fields["height"].isHidden())
        self.assertFalse(panel.fields["bend_radius"].isHidden())
        self.assertFalse(panel.fields["flange_width"].isHidden())
        self.assertFalse(panel.fields["fill_gaps"].isHidden())
