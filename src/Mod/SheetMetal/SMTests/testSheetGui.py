# SPDX-License-Identifier: LGPL-2.1-or-later
"""Shared command panels in a private native GUI, with no MCP connection."""

import unittest
import os
from pathlib import Path
from unittest.mock import patch

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

from SMTests import testPresentation


class TestSheetGui(unittest.TestCase):
    def setUp(self):
        import SheetMetalGui
        self.gui = SheetMetalGui
        self.fixture = testPresentation.TestPresentation()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.model = self.fixture.fixture
        self.sheet = self.model.sheet
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.sheet)
        self.addCleanup(Gui.Selection.clearSelection)

    def panel(self, mode):
        panel = self.gui.SheetPanel(self.sheet, mode)
        self.addCleanup(panel.close)
        return panel

    def finish(self, panel):
        self.assertIsNotNone(panel.run, panel.message.text())
        self.fixture.wait_for(panel.run.future.done)
        self.assertEqual(panel.run.status()["phase"], "ready", panel.run.status())

    def test_material_panel_uses_shared_async_edit_and_one_undo(self):
        panel = self.panel("materials")
        Gui.Control.showDialog(panel)
        self.addCleanup(panel.reject)
        undo = self.model.doc.UndoCount
        panel.material.setText("Aluminum")
        panel.k_factor.setValue(.38)
        panel.apply_button.click()
        self.finish(panel)
        self.assertEqual(self.sheet.Material, "Aluminum")
        self.assertEqual(self.sheet.KFactor, .38)
        self.assertEqual(self.model.doc.UndoCount, undo+1)
        self.assertIn("Ready", panel.message.text())
        self.assertFalse(panel._closed)
        self.assertTrue(Gui.Control.activeDialog())

    def test_parameter_panel_rebuilds_both_representations(self):
        panel = self.panel("parameters")
        panel.parameters["thickness"].setValue(2)
        panel.parameters["flange_length"].setValue(38)
        panel.apply_button.click()
        self.finish(panel)
        self.model.assert_valid_pair()
        self.assertEqual(self.model.doc.BaseBend.Thickness.Value, 2)
        self.assertEqual(self.model.doc.BaseBend.Length.Value, 38)

    def test_stale_panel_requires_refresh_before_it_can_mutate(self):
        panel = self.panel("materials")
        self.model.edit(lambda: setattr(self.sheet, "Material", "User edit"))
        panel.material.setText("Stale overwrite")
        undo = self.model.doc.UndoCount
        panel.apply_button.click()
        self.assertIsNone(panel.run)
        self.assertEqual(self.sheet.Material, "User edit")
        self.assertEqual(self.model.doc.UndoCount, undo)
        self.assertIn("changed", panel.message.text())
        panel.refresh_button.click()
        self.assertEqual(panel.material.text(), "User edit")

    def test_canvas_pick_feeds_a_bend_cut_and_remove_repairs_it(self):
        from pivy import coin
        geometry = self.sheet.Proxy._geometry
        region, folded, flat = self.model.bend_pick()
        self.fixture.view.switch("flat")
        view = Gui.getDocument(self.model.doc.Name).activeView()
        view.setAnimationEnabled(False)
        view.setCameraOrientation(App.Rotation(App.Vector(0, 0, 1), geometry.normal))
        for obj in self.model.doc.Objects:
            if hasattr(obj, "Visibility"):
                obj.Visibility = obj is self.sheet
        view.fitAll()
        view.redraw()
        panel = self.panel("cuts")
        panel.pick_button.click()
        self.assertIsNotNone(panel._event)
        event = coin.SoMouseButtonEvent()
        event.setButton(coin.SoMouseButtonEvent.BUTTON1)
        event.setState(coin.SoButtonEvent.DOWN)
        event.setPosition(coin.SbVec2s(*view.getPointOnScreen(flat)))
        manager = view.getViewer().getSoRenderManager()
        action = coin.SoHandleEventAction(manager.getViewportRegion())
        action.setEvent(event)
        action.apply(manager.getSceneGraph())
        self.assertIsNotNone(panel.pick, panel.message.text())
        self.assertIsNone(panel._event)
        panel.radius.setValue(5)
        before = self.sheet.Shape.Volume
        panel.apply_button.click()
        self.finish(panel)
        self.assertLess(self.sheet.Shape.Volume, before)
        self.model.assert_valid_pair()
        self.assertEqual(panel.operations.count(), 1)
        panel.remove_button.click()
        self.finish(panel)
        self.assertAlmostEqual(self.sheet.Shape.Volume, before, places=6)

    def test_close_panel_during_geometry_keeps_edit_and_drops_ui_callback(self):
        panel = self.panel("materials")
        Gui.Control.showDialog(panel)
        self.addCleanup(panel.reject)
        panel.material.setText("Steel")
        panel.apply_button.click()
        run = panel.run
        panel.reject()
        self.assertFalse(Gui.Control.activeDialog())
        self.fixture.wait_for(run.future.done)
        self.assertEqual(run.status()["phase"], "ready")
        self.assertEqual(self.sheet.Material, "Steel")

    def test_selection_switch_does_not_redirect_an_open_panel(self):
        panel = self.panel("materials")
        other = App.newDocument("OtherSheetGuiTest")
        self.addCleanup(lambda: App.closeDocument(other.Name))
        panel.material.setText("Wrong active document")
        panel.apply_button.click()
        self.assertIsNone(panel.run)
        self.assertNotEqual(self.sheet.Material, "Wrong active document")

    def test_task_panel_opens_from_command_and_switches_views_without_undo(self):
        previous = Gui.activeWorkbench().name()
        self.addCleanup(lambda: Gui.activateWorkbench(previous))
        Gui.activateWorkbench("SMWorkbench")
        controller = Gui.getMainWindow().findChild(QtCore.QObject, "VibeCADRibbonController")
        self.fixture.wait_for(lambda: controller.property("VibeCADActiveSurfaceId") == "sheet_metal")
        self.gui.ensure_commands_registered()
        original = self.gui.SheetPanel
        panels = []
        def opened(*args):
            panel = original(*args)
            panels.append(panel)
            return panel
        with patch.object(self.gui, "SheetPanel", side_effect=opened):
            Gui.runCommand("SheetMetal_EditCuts")
        self.assertEqual(len(panels), 1)
        panel = panels[0]
        self.addCleanup(panel.reject)
        self.assertTrue(Gui.Control.activeDialog())
        undo = self.model.doc.UndoCount
        panel.flat_button.click()
        self.assertEqual(self.fixture.view.mode, "flat")
        panel.folded_button.click()
        self.assertEqual(self.fixture.view.mode, "folded")
        self.assertEqual(self.model.doc.UndoCount, undo)
        active = Gui.getDocument(self.model.doc.Name).activeView()
        active.setAnimationEnabled(False)
        active.viewAxonometric()
        active.fitAll()
        Gui.updateGui()
        active.redraw()
        render = str(Path(os.environ["VIBECAD_TEST_OUTPUT"])/"sheet-panel-model.png")
        active.saveImage(render, 800, 600, "White")
        image = QtGui.QImage(render)
        foreground = sum(image.pixelColor(x, y) != QtGui.QColor("white")
                         for x in range(100, 700, 4) for y in range(100, 500, 4))
        self.assertGreater(foreground, 500, "The model must remain rendered with the task panel open")
        QtWidgets.QApplication.sync()
        screen = Gui.getMainWindow().windowHandle().screen()
        self.assertTrue(screen.grabWindow(Gui.getMainWindow().winId()).save(
            str(Path(os.environ["VIBECAD_TEST_OUTPUT"])/"sheet-cuts-panel.png")))
        close_buttons = [box.button(QtWidgets.QDialogButtonBox.Close)
                         for box in Gui.getMainWindow().findChildren(QtWidgets.QDialogButtonBox)]
        button = next(button for button in close_buttons if button is not None and button.isVisible())
        button.click()
        self.assertFalse(Gui.Control.activeDialog())
        self.assertTrue(panel._closed)

    def test_native_dialog_buttons_are_an_integer_flag(self):
        panel = self.panel("materials")
        self.assertIsInstance(panel.getStandardButtons(), int)

    def test_native_dialog_closed_hook_is_callable_and_releases_picking(self):
        panel = self.panel("cuts")
        panel.arm_pick()
        self.assertIsNotNone(panel._event)
        self.assertTrue(callable(panel.closed))
        panel.closed()
        self.assertIsNone(panel._event)

    def test_stale_source_revision_rejects_creation_before_mutation(self):
        import SheetMetalOperations as Operations
        source = self.model.doc.BaseBend
        revision = Operations.capture_source_revision(source)
        self.model.edit(lambda: setattr(source, "Length", 42))
        before = self.model.doc.UndoCount, len(self.model.doc.Objects)
        with self.assertRaisesRegex(RuntimeError, "changed"):
            Operations.start_creation(source, f"Face{self.model.root}", expected_revision=revision)
        self.assertEqual(before, (self.model.doc.UndoCount, len(self.model.doc.Objects)))

    def test_native_commands_switch_cached_nodes_without_recompute_or_undo(self):
        self.gui.ensure_commands_registered()
        undo = self.model.doc.UndoCount
        nodes = self.fixture.view.cached_nodes
        with patch.object(self.sheet.Proxy, "execute", side_effect=AssertionError("recompute")):
            Gui.runCommand("SheetMetal_ViewFlat")
            self.assertEqual(self.fixture.view.mode, "flat")
            Gui.runCommand("SheetMetal_ViewFolded")
        self.assertEqual(self.fixture.view.cached_nodes, nodes)
        self.assertEqual(self.model.doc.UndoCount, undo)

    def test_create_command_consumes_exact_face_in_one_async_transaction(self):
        self.gui.ensure_commands_registered()
        self.model.edit(lambda: self.model.doc.removeObject(self.sheet.Name))
        source = self.model.doc.BaseBend
        source.Visibility = True
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source, f"Face{self.model.root}")
        undo = self.model.doc.UndoCount
        Gui.runCommand("SheetMetal_CreateEditable")
        self.sheet = self.model.sheet = self.model.doc.getObject("EditableSheet")
        self.assertIsNotNone(self.sheet)
        self.fixture.view = self.sheet.ViewObject.Proxy
        self.fixture.wait_for(lambda: self.fixture.view.ready)
        self.model.assert_valid_pair()
        self.assertEqual(self.model.doc.UndoCount, undo+1)
        self.assertFalse(source.Visibility)
        self.assertEqual(self.sheet.SourceFace, (source, [f"Face{self.model.root}"]))

    def test_existing_ribbon_publishes_sheetmetal_groups(self):
        from VibeCADRibbonSurface import read_active_ribbon_surface
        previous = Gui.activeWorkbench().name()
        self.addCleanup(lambda: Gui.activateWorkbench(previous))
        Gui.activateWorkbench("SMWorkbench")
        controller = Gui.getMainWindow().findChild(QtCore.QObject, "VibeCADRibbonController")
        self.fixture.wait_for(lambda: controller.property("VibeCADActiveSurfaceId") == "sheet_metal")
        surface = read_active_ribbon_surface(controller)
        self.assertEqual(surface.surface_id, "sheet_metal")
        labels = {group.label for group in surface.groups}
        self.assertTrue({"Create", "Bend/Form", "Cut/Relief", "Materials", "Folded/Flat"} <= labels)
