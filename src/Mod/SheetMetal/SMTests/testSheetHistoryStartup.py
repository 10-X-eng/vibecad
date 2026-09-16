# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run alone in a fresh GUI: restore must register History's sheet editor."""

from pathlib import Path
import tempfile
import unittest

import FreeCAD as App
import FreeCADGui as Gui

from SMTests import testPresentation


class TestSheetHistoryStartup(unittest.TestCase):
    def test_restore_registers_editor_without_activating_sheetmetal_workbench(self):
        import SheetMetalOperations as Operations
        self.assertIsNone(Gui.Command.get("SheetMetal_EditParameters"),
                          "Run this startup test alone in a fresh GUI")
        fixture = testPresentation.TestPresentation()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        model = fixture.fixture
        model.edit(lambda: model.doc.removeObject(model.sheet.Name))
        source = model.doc.BaseBend
        source.Visibility = True
        run = Operations.start_creation(source, f"Face{model.root}",
            expected_revision=Operations.capture_source_revision(source))
        model.sheet = model.doc.getObject(run.status()["object_name"])
        fixture.view = model.sheet.ViewObject.Proxy
        fixture.wait_for(run.future.done)
        self.assertEqual(run.status()["phase"], "ready", run.status())
        fixture.wait_for(lambda: fixture.view.ready)
        self.assertIsNone(Gui.Command.get("SheetMetal_EditParameters"))
        workbench = Gui.activeWorkbench().name()
        name = model.sheet.Name
        with tempfile.TemporaryDirectory() as directory:
            filename = str(Path(directory) / "sheet-startup.FCStd")
            model.doc.saveAs(filename)
            model.settle()
            App.closeDocument(model.doc.Name)
            model.doc = App.openDocument(filename)
            model.settle()
            model.sheet = model.doc.getObject(name)
            fixture.view = model.sheet.ViewObject.Proxy
            fixture.wait_for(lambda: fixture.view.ready)
            self.assertEqual(Gui.activeWorkbench().name(), workbench)
            command = Gui.Command.get(model.sheet.VibeCADTimelineEditCommand)
            self.assertIsNotNone(command)
            self.assertTrue(all(action.property("VibeCADTimelineOperationEditor")
                                for action in command.ensureAction()))
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(model.sheet)
            self.addCleanup(Gui.Selection.clearSelection)
            Gui.runCommand(model.sheet.VibeCADTimelineEditCommand)
            self.assertTrue(Gui.Control.activeDialog())
            Gui.Control.closeDialog()
