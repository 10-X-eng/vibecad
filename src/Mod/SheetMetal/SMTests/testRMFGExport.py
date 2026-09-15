# SPDX-License-Identifier: LGPL-2.1-or-later
"""Folded STEP export owns its geometry and never publishes a stale revision."""

import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
import tempfile
import threading
import sys
import unittest
from unittest.mock import Mock, patch

import Part
from PySide import QtCore

from SMTests import testPresentation


class TestDetachedBrep(unittest.TestCase):
    def test_native_export_preserves_source_and_roundtrips_geometry(self):
        shape = Part.makeBox(20, 30, 1.6).cut(Part.makeCylinder(3, 1.6))
        before = shape.exportBrepToString(True)
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory)/"detached-µ.brep")
            shape.exportBrepDetached(path)
            restored = Part.Shape()
            restored.importBrep(path)
        self.assertTrue(restored.isValid())
        self.assertEqual(len(restored.Solids), 1)
        self.assertAlmostEqual(restored.Volume, shape.Volume)
        self.assertEqual(shape.exportBrepToString(True), before)

    def test_native_export_reports_unwritable_destination(self):
        shape = Part.makeBox(1, 2, 3)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(Part.OCCError):
                shape.exportBrepDetached(str(Path(directory)/"missing"/"out.brep"))

    def test_native_serialization_allows_gui_python_callbacks(self):
        shape = Part.makeCompound([Part.makeBox(1+index/1000, 2, 3) for index in range(1000)])
        finished, errors, phase = threading.Event(), [], [False]
        callbacks = []
        with tempfile.TemporaryDirectory() as directory:
            def write():
                try:
                    phase[0] = True
                    shape.exportBrepDetached(str(Path(directory)/"large.brep"))
                except Exception as error:
                    errors.append(error)
                finally:
                    phase[0] = False
                    finished.set()
            # Prevent an ordinary Python bytecode timeslice between setting the
            # phase and entering C++; callbacks must run during its GIL release.
            interval = sys.getswitchinterval()
            sys.setswitchinterval(1)
            worker = threading.Thread(target=write)
            try:
                worker.start()
                while not finished.is_set():
                    QtCore.QCoreApplication.processEvents()
                    if phase[0]:
                        callbacks.append(True)
                    finished.wait(.001)
                worker.join()
            finally:
                sys.setswitchinterval(interval)
        if errors:
            raise errors[0]
        self.assertGreater(len(callbacks), 0, "Native serialization kept Python's GIL")


class TestRMFGExportChild(unittest.TestCase):
    def test_rejects_changed_brep_before_step_write(self):
        import SheetMetalRMFGExportChild as Child
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            Part.makeBox(1, 2, 3).exportBrep(str(workspace/"folded.brep"))
            (workspace/"request.json").write_text(json.dumps({
                "schema": "vibecad-rmfg-step-v1", "brep_sha256": "0"*64}))
            self.assertEqual(Child.run(workspace), 1)
            self.assertFalse((workspace/"folded.step").exists())
            result = json.loads((workspace/"result.json").read_text())
            self.assertIn("changed", result["message"])

    def test_rejects_multiple_solids_before_step_write(self):
        import SheetMetalRMFGExportChild as Child
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            source = workspace/"folded.brep"
            shape = Part.makeCompound([Part.makeBox(1, 2, 3), Part.makeBox(4, 5, 6)])
            shape.exportBrep(str(source))
            (workspace/"request.json").write_text(json.dumps({
                "schema": "vibecad-rmfg-step-v1", "brep_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}))
            self.assertEqual(Child.run(workspace), 1)
            self.assertFalse((workspace/"folded.step").exists())
            self.assertIn("one valid solid", json.loads((workspace/"result.json").read_text())["message"])


class TestRMFGExport(unittest.TestCase):
    def setUp(self):
        self.fixture = testPresentation.TestPresentation()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.model = self.fixture.fixture
        self.sheet, self.doc = self.model.sheet, self.model.doc

    def revision(self):
        import SheetMetalOperations as Operations
        return Operations.capture_revision(self.sheet).summary()

    def wait(self, run):
        self.fixture.wait_for(run.future.done)
        return run.future.result()

    def reopen(self):
        import FreeCAD as App
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        filename = str(Path(directory.name)/"export.FCStd")
        self.doc.saveAs(filename)
        self.model.settle()
        App.closeDocument(self.doc.Name)
        self.doc = self.model.doc = App.openDocument(filename)
        self.model.settle()
        self.sheet = self.model.sheet = self.doc.getObject("EditableSheet")
        self.fixture.view = self.sheet.ViewObject.Proxy
        self.fixture.wait_for(lambda: self.fixture.view.ready)
        self.assertIsNone(self.sheet.Proxy._geometry)

    def test_reopened_sheet_prepares_and_exports_real_folded_step_without_an_edit(self):
        import SheetMetalRMFGExport as Export
        self.reopen()
        self.fixture.view.switch("flat")
        revision, undo, touched = self.revision(), self.doc.UndoCount, self.doc.isTouched()
        folded, flat = self.sheet.Shape, self.sheet.FlatShape
        result = self.wait(Export.start_prepared_export(self.sheet, expected_revision=revision))
        self.assertTrue(result.matches_revision(revision))
        path = Path(os.environ["VIBECAD_TEST_OUTPUT"])/"reopened-folded.step"
        path.write_bytes(result.step_bytes)
        restored = Part.Shape()
        restored.read(str(path))
        self.assertTrue(restored.isValid())
        self.assertEqual(len(restored.Solids), 1)
        self.assertAlmostEqual(restored.Volume, folded.Volume, places=4)
        for axis in ("XLength", "YLength", "ZLength"):
            self.assertAlmostEqual(getattr(restored.BoundBox, axis), getattr(folded.BoundBox, axis))
        self.assertEqual((self.revision(), self.doc.UndoCount, self.doc.isTouched()), (revision, undo, touched))
        self.assertTrue(self.sheet.Shape.isSame(folded))
        self.assertTrue(self.sheet.FlatShape.isSame(flat))
        self.assertEqual(self.fixture.view.mode, "flat")

    def check_interrupted_preparation(self, *, cancel):
        import SheetMetalPreparation as Preparation
        import SheetMetalRMFGExport as Export
        self.reopen()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = Preparation._build
        def build(*args):
            entered.set()
            release.wait()
            return original(*args)
        writer = Mock(side_effect=AssertionError("Export must not start"))
        with patch.object(Preparation, "_build", side_effect=build):
            run = Export.start_prepared_export(self.sheet, expected_revision=self.revision(), exporter=writer)
            self.fixture.wait_for(entered.is_set)
            if cancel:
                self.assertTrue(run.future.cancel())
            else:
                self.doc.BaseBend.Thickness = 2
            release.set()
            self.fixture.wait_for(lambda: run._preparation_run.finished)
            self.fixture.wait_for(run.future.done)
        if cancel:
            self.assertTrue(run.future.cancelled())
        else:
            with self.assertRaises(RuntimeError):
                run.future.result()
        writer.assert_not_called()
        self.assertIsNone(self.sheet.Proxy._geometry)

    def test_cancel_during_preparation_never_starts_step_export(self):
        self.check_interrupted_preparation(cancel=True)

    def test_edit_during_preparation_never_starts_step_export(self):
        self.check_interrupted_preparation(cancel=False)

    def test_real_step_is_folded_even_when_the_display_is_flat(self):
        import SheetMetalRMFGExport as Export
        self.fixture.view.switch("flat")
        revision, undo = self.revision(), self.doc.UndoCount
        folded = self.sheet.Shape.copy()
        output = Path(os.environ["VIBECAD_TEST_OUTPUT"])
        @contextmanager
        def retained_workspace(**options):
            # Preserve the child log and neutral files even if the export fails.
            yield tempfile.mkdtemp(dir=output, **options)
        def exporter(shape, captured, executable):
            with patch.object(Export.tempfile, "TemporaryDirectory", retained_workspace):
                return Export.export_folded(shape, captured, executable)
        result = self.wait(Export.start_export(self.sheet, expected_revision=revision, exporter=exporter))
        self.assertTrue(result.matches_revision(revision))
        self.assertTrue(result.step_bytes.startswith(b"ISO-10303-21;"))
        path = Path(os.environ["VIBECAD_TEST_OUTPUT"])/"folded-export.step"
        path.write_bytes(result.step_bytes)
        restored = Part.Shape()
        restored.read(str(path))
        self.assertTrue(restored.isValid())
        self.assertEqual(len(restored.Solids), 1)
        self.assertAlmostEqual(restored.Volume, folded.Volume, places=4)
        for axis in ("XLength", "YLength", "ZLength"):
            self.assertAlmostEqual(getattr(restored.BoundBox, axis), getattr(folded.BoundBox, axis))
        self.assertEqual(self.revision(), revision)
        self.assertEqual(self.doc.UndoCount, undo)
        self.assertEqual(self.fixture.view.mode, "flat")

    def test_changed_revision_rejects_export_while_gui_remains_available(self):
        import SheetMetalRMFGExport as Export
        from SheetMetalRMFGSnapshot import ExportSnapshot
        entered, release = threading.Event(), threading.Event()
        threads = []
        def worker(shape, revision, executable):
            threads.append(threading.get_ident())
            entered.set()
            release.wait()
            return ExportSnapshot(revision, b"detached STEP test data")
        run = Export.start_export(self.sheet, expected_revision=self.revision(), exporter=worker)
        try:
            self.fixture.wait_for(entered.is_set)
            self.assertNotEqual(threads, [threading.get_ident()])
            self.assertFalse(run.future.done())
            self.sheet.Label = "Edited while exporting"
        finally:
            release.set()
        with self.assertRaisesRegex(RuntimeError, "changed|revision"):
            self.wait(run)

    def test_foreign_or_pending_revision_never_starts_export(self):
        import SheetMetalRMFGExport as Export
        revision = self.revision()
        revision["document_uid"] = "another-document"
        def worker(*args):
            self.fail("A rejected export started geometry work")
        with self.assertRaises(RuntimeError):
            Export.start_export(self.sheet, expected_revision=revision, exporter=worker)
        self.sheet.KFactor = .4
        with self.assertRaises(RuntimeError):
            Export.start_export(self.sheet, expected_revision=self.revision(), exporter=worker)

    def test_cancelled_consumer_cannot_receive_a_late_export(self):
        import SheetMetalRMFGExport as Export
        from SheetMetalRMFGSnapshot import ExportSnapshot
        entered, release = threading.Event(), threading.Event()
        def worker(shape, revision, executable):
            entered.set()
            release.wait()
            return ExportSnapshot(revision, b"detached STEP test data")
        run = Export.start_export(self.sheet, expected_revision=self.revision(), exporter=worker)
        try:
            self.fixture.wait_for(entered.is_set)
            self.assertTrue(run.future.cancel())
        finally:
            release.set()
        self.fixture.wait_for(lambda: run.finished)
        self.assertTrue(run.future.cancelled())
