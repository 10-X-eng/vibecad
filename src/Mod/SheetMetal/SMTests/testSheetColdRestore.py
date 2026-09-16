# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run each class in its own GUI; the restore process must have cold imports."""

import importlib.abc
import json
import os
from pathlib import Path
import sys
import threading
import time
import unittest

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets


class CreateFixture(unittest.TestCase):
    def test_save_source_and_cut_history(self):
        from SMTests.live_sheet_flange_prompt import LiveSheetFlangePrompt
        fixture = LiveSheetFlangePrompt()
        self.addCleanup(fixture.doCleanups)
        document = fixture.create_input()
        document.saveAs(str(Path(os.environ["VIBECAD_TEST_OUTPUT"]) / "cold-sheet.FCStd"))


class TestColdRestore(unittest.TestCase):
    def restore_document(self):
        modules = ("SheetMetalSourceFeatures", "SheetMetalBaseShapeCmd",
                   "SheetMetalEditable", "SheetMetalCutHistory")
        for name in modules:
            self.assertFalse(name in sys.modules, f"{name} already imported; run this class alone")
        workbench = Gui.activeWorkbench().name()
        imports = []

        class ObserveImports(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname in modules:
                    imports.append((fullname, threading.get_ident()))
                return None

        observer = ObserveImports()
        sys.meta_path.insert(0, observer)
        self.addCleanup(lambda: sys.meta_path.remove(observer))
        # Recent Files uses the same asynchronous native open as File > Open.
        # A failed restore may show an error dialog. Record and dismiss it only
        # inside this private test process so the failing assertion can run.
        dialogs = []

        class RestoreDialogs(QtCore.QObject):
            dismiss = QtCore.Signal(object)

            def __init__(self):
                super().__init__()
                self.dismiss.connect(lambda widget: widget.accept(), QtCore.Qt.QueuedConnection)

            def eventFilter(self, watched, event):
                if event.type() == QtCore.QEvent.Show and isinstance(watched, QtWidgets.QMessageBox):
                    dialogs.append(watched.text())
                    self.dismiss.emit(watched)
                return False

        dialog_observer = RestoreDialogs()
        application = QtWidgets.QApplication.instance()
        application.installEventFilter(dialog_observer)
        self.addCleanup(lambda: application.removeEventFilter(dialog_observer))
        recent = App.ParamGet("User parameter:BaseApp/Preferences/RecentFiles")
        recent.SetString("MRU0", os.environ["VIBECAD_COLD_RESTORE_FIXTURE"])
        recent.SetInt("RecentFiles", 4)
        Gui.Command.get("Std_RecentFiles").ensureAction()
        Gui.runCommand("Std_RecentFiles", 0)
        while not App.listDocuments():
            QtCore.QCoreApplication.processEvents()
            time.sleep(.001)
        document, = App.listDocuments().values()

        def settle():
            while (document.Restoring or not document.isClosable()
                   or document.RecomputePending or document.Recomputing):
                QtCore.QCoreApplication.processEvents()
                time.sleep(.001)

        def close():
            settle()
            App.closeDocument(document.Name)

        self.addCleanup(close)
        settle()
        output = Path(os.environ["VIBECAD_TEST_OUTPUT"])
        (output / "imports.json").write_text(json.dumps({
            "main_thread": threading.get_ident(), "imports": imports}, indent=2))
        self.assertTrue(imports)
        self.assertNotEqual(imports[0][1], threading.get_ident(), imports)
        self.assertEqual(Gui.activeWorkbench().name(), workbench)
        missing = [obj.Name for obj in document.Objects
                   if "Proxy" in obj.PropertiesList and obj.Proxy is None]
        self.assertEqual(missing, [], "Restore lost Python feature history")
        self.assertEqual(dialogs, [])
        self.assertTrue(all(thread != threading.get_ident() for _, thread in imports), imports)
        return document, output, settle

    def test_restore_without_preloading_sheetmetal(self):
        document, output, settle = self.restore_document()

        import SheetMetalEditable as Editable
        import SheetMetalSourceFeatures as Features
        import SheetMetalBaseShapeCmd as BaseShape
        import SheetMetalPreparation as Preparation
        import SheetMetalHistoryOperations as Shared
        self.assertIs(BaseShape.mw, Gui.getMainWindow())
        self.assertIsInstance(document.BaseShape.Proxy, Features.BaseShape)
        states = [obj for obj in document.Objects
                  if isinstance(getattr(obj, "Proxy", None), Editable.PreparedSheetState)]
        self.assertEqual(len(states), 2)
        for obj in states:
            run = Preparation.start_preparation(obj, expected_revision=Shared.capture_revision(obj))
            while not run.future.done():
                QtCore.QCoreApplication.processEvents()
                time.sleep(.001)
            self.assertEqual(run.future.result()["input_hash"], obj.PreparedInputHash)
            settle()
            geometry = Editable.get_state_geometry(obj)
            self.assertTrue(geometry.folded.isValid())
            self.assertTrue(geometry.flat.isValid())
            self.assertIsNotNone(Gui.Command.get(obj.VibeCADTimelineEditCommand))
        Gui.activateWorkbench("SMWorkbench")
        for name in ("SheetMetal_BaseShape", "SheetMetal_AddBase", "SheetMetal_FromSolid",
                     "SheetMetal_AddWall", "SheetMetal_CreateFlange"):
            self.assertIsNotNone(Gui.Command.get(name), name)
        document.saveAs(str(output / "restored-sheet.FCStd"))


class TestFoldColdRestore(TestColdRestore):
    """Open the internal-fold fixture in a fresh process with no source preloads."""

    def test_restore_without_preloading_sheetmetal(self):
        document, output, settle = self.restore_document()
        import SheetMetalEditable as Editable
        import SheetMetalSourceFeatures as Features
        import SheetMetalPreparation as Preparation
        import SheetMetalHistoryOperations as Shared

        fold, = [obj for obj in document.Objects if isinstance(getattr(obj, "Proxy", None), Features.Fold)]
        sheet, = [obj for obj in document.Objects
                  if isinstance(getattr(obj, "Proxy", None), Editable.PreparedSheetState)
                  and obj.SourceFace[0] is fold]
        self.assertIs(fold.baseObject[0], document.BaseShape)
        self.assertIs(fold.BendLine, document.BendLine)
        self.assertEqual(float(fold.angle), 90)
        self.assertEqual(float(fold.kfactor), .3)
        self.assertEqual(dict(sheet.ExpressionEngine)["KFactor"], f"{fold.Name}.kfactor")
        for obj in (fold, sheet):
            self.assertIsNotNone(Gui.Command.get(obj.VibeCADTimelineEditCommand))
        run = Preparation.start_preparation(sheet, expected_revision=Shared.capture_revision(sheet))
        while not run.future.done():
            QtCore.QCoreApplication.processEvents()
            time.sleep(.001)
        self.assertEqual(run.future.result()["input_hash"], sheet.PreparedInputHash)
        settle()
        geometry = Editable.get_state_geometry(sheet)
        self.assertTrue(geometry.folded.isValid())
        self.assertTrue(geometry.flat.isValid())
        source = fold.baseObject[0].Shape
        for actual, expected in zip(sorted((geometry.flat.BoundBox.XLength, geometry.flat.BoundBox.YLength)),
                                    sorted((source.BoundBox.XLength, source.BoundBox.YLength))):
            self.assertAlmostEqual(actual, expected, places=4)
        document.saveAs(str(output / "restored-fold.FCStd"))


class TestTabColdRestore(TestColdRestore):
    def test_restore_without_preloading_sheetmetal(self):
        document, output, settle = self.restore_document()
        import SheetMetalEditable as Editable
        import SheetMetalSourceFeatures as Features
        import SheetMetalPreparation as Preparation
        import SheetMetalHistoryOperations as Shared
        import SheetMetalCutHistory as History

        fold, = [obj for obj in document.Objects if isinstance(getattr(obj, "Proxy", None), Features.Fold)]
        parent = fold.baseObject[0]
        self.assertIsInstance(parent.Proxy, History.ProfileCutFeature)
        sheet, = [obj for obj in document.Objects
                  if isinstance(getattr(obj, "Proxy", None), Editable.PreparedSheetState)
                  and getattr(obj, "SourceFace", None) and obj.SourceFace[0] is fold]
        self.assertIsNone(parent.Proxy._geometry)
        before = document.UndoCount, document.isTouched(), sheet.PreparedInputHash
        # Prepare the visible final tab first, as a user opening this file would.
        run = Preparation.start_preparation(sheet, expected_revision=Shared.capture_revision(sheet))
        while not run.future.done():
            QtCore.QCoreApplication.processEvents()
            time.sleep(.001)
        self.assertEqual(run.future.result()["input_hash"], sheet.PreparedInputHash)
        settle()
        geometry = Editable.get_state_geometry(sheet)
        self.assertTrue(geometry.folded.isValid())
        self.assertTrue(geometry.flat.isValid())
        self.assertAlmostEqual(geometry.flat.Volume, parent.FlatShape.Volume, places=4)
        self.assertEqual((document.UndoCount, document.isTouched(), sheet.PreparedInputHash), before)
        document.saveAs(str(output / "restored-tab.FCStd"))
