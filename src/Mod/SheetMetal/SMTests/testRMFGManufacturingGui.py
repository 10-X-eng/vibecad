# SPDX-License-Identifier: LGPL-2.1-or-later
"""Real private GUI workflow with fake RMFG responses and real folded export."""

import copy
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from PySide import QtWidgets

from SMTests import testPresentation, testRMFGManufacturing


class TestRMFGManufacturingGui(unittest.TestCase):
    def setUp(self):
        import SheetMetalRMFGManufacturingGui as Manufacturing
        self.fixture = testPresentation.TestPresentation()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.model = self.fixture.fixture
        self.domain = testRMFGManufacturing.TestRMFGManufacturing()
        self.addCleanup(self.domain.doCleanups)
        self.domain.setUp()
        self.client = self.domain.client
        self.client.materials.return_value = {"data": self.domain.materials, "has_more": False}
        self.client.analyze.return_value = self.domain.design
        self.client.create_quote.side_effect = lambda *args, **kwargs: self.domain.quote()
        self.client.quote.side_effect = lambda *args, **kwargs: self.domain.quote()
        self.client.create_checkout.return_value = {"id": "cart_1", "status": "open",
                                                   "cart_url": "https://www.rmfg.com/cart/private-link"}
        self.controller = Manufacturing.Controller(self.model.sheet, self.domain.backend)
        self.opened = []
        self.panel = Manufacturing.Panel(self.controller, open_browser=lambda url: self.opened.append(str(url)) or True)
        self.panel.show()
        self.addCleanup(self.panel.close)
        self.wait()

    def wait(self):
        self.fixture.wait_for(lambda: not self.controller.busy)

    def analyze(self):
        self.panel.analyze_button.click()
        self.wait()
        self.assertEqual(self.controller.status()["design"]["status"], "ready")
        self.controller.set_material("part_1", "steel_1")
        self.panel.quantity.setValue(10)

    def reopen(self):
        import FreeCAD as App
        import SheetMetalRMFGManufacturingGui as Manufacturing
        self.panel.close()
        self.controller.close()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        filename = str(Path(directory.name)/"manufacturing.FCStd")
        self.model.doc.saveAs(filename)
        self.model.settle()
        App.closeDocument(self.model.doc.Name)
        self.model.doc = App.openDocument(filename)
        self.model.settle()
        self.model.sheet = self.model.doc.getObject("EditableSheet")
        self.fixture.view = self.model.sheet.ViewObject.Proxy
        self.fixture.wait_for(lambda: self.fixture.view.ready)
        self.controller = Manufacturing.Controller(self.model.sheet, self.domain.backend)
        self.addCleanup(self.controller.close)
        self.panel = Manufacturing.Panel(self.controller, open_browser=lambda url: self.opened.append(str(url)) or True)
        self.addCleanup(self.panel.close)
        self.panel.show()
        self.wait()
        self.assertIsNone(self.model.sheet.Proxy._geometry)

    def test_reopened_sheet_can_be_analyzed_with_real_export_and_no_manual_edit(self):
        import SheetMetalOperations as Operations
        self.reopen()
        before = (Operations.capture_revision(self.model.sheet), self.model.doc.UndoCount,
                  self.model.doc.isTouched())
        self.panel.analyze_button.click()
        self.wait()
        self.assertFalse(self.controller._last_error, self.controller.message)
        self.assertEqual(self.controller.status()["design"]["status"], "ready")
        self.client.analyze.assert_called_once()
        self.assertEqual((Operations.capture_revision(self.model.sheet), self.model.doc.UndoCount,
                          self.model.doc.isTouched()), before)

    def test_closing_during_preparation_prevents_export_and_upload(self):
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
        with patch.object(Preparation, "_build", side_effect=build), \
                patch.object(Export, "export_folded", side_effect=AssertionError("Late export")) as writer:
            self.panel.analyze_button.click()
            self.fixture.wait_for(entered.is_set)
            run = self.controller._export_run
            self.controller.close()
            release.set()
            self.fixture.wait_for(lambda: run._preparation_run.finished)
        writer.assert_not_called()
        self.client.analyze.assert_not_called()
        self.assertIsNone(self.model.sheet.Proxy._geometry)

    def test_panel_quotes_folded_geometry_without_document_or_view_changes(self):
        self.fixture.view.switch("flat")
        before = self.model.doc.UndoCount, self.model.sheet.PreparedInputHash
        self.analyze()
        self.panel.quote_button.click()
        self.wait()
        state = self.controller.status()
        self.assertEqual(state["quote"]["status"], "ready")
        self.assertTrue(state["can_checkout"])
        self.assertEqual((self.model.doc.UndoCount, self.model.sheet.PreparedInputHash), before)
        self.assertEqual(self.fixture.view.mode, "flat")
        QtWidgets.QApplication.processEvents()
        self.panel.grab().save(str(Path(os.environ["VIBECAD_TEST_OUTPUT"])/"rmfg-manufacturing.png"))
        self.panel.checkout_button.click()
        self.wait()
        self.assertEqual(self.opened, ["https://www.rmfg.com/cart/private-link"])
        self.assertNotIn("private-link", str(self.controller.status()))

    def test_document_edit_marks_quote_stale_and_blocks_checkout(self):
        self.analyze()
        self.panel.quote_button.click()
        self.wait()
        self.model.sheet.Label = "Changed after quoting"
        self.fixture.wait_for(lambda: self.controller.status()["stale"])
        QtWidgets.QApplication.processEvents()
        self.assertFalse(self.panel.checkout_button.isEnabled())
        with self.assertRaises(RuntimeError):
            self.controller.checkout()
        self.client.create_checkout.assert_not_called()

    def test_changed_quantity_discards_a_late_quote_without_blocking_gui(self):
        self.analyze()
        entered, release = threading.Event(), threading.Event()
        def quote(*args, **kwargs):
            entered.set()
            release.wait()
            return self.domain.quote()
        self.client.create_quote.side_effect = quote
        self.panel.quote_button.click()
        try:
            self.fixture.wait_for(entered.is_set)
            self.panel.quantity.setValue(11)
            self.panel.setWindowTitle("Responsive while quoting")
            self.assertEqual(self.panel.windowTitle(), "Responsive while quoting")
        finally:
            release.set()
        self.wait()
        self.assertFalse(self.controller.status()["can_checkout"])
        self.assertIsNone(self.controller.status()["quote"])

    def test_blocking_findings_are_visible_and_internal_findings_are_hidden(self):
        self.analyze()
        result = self.domain.quote()
        result["status"] = "blocked"
        result["items"][0]["dfm"]["parts"][0]["issues"] = [
            {"code": "visible", "message": "Hole 2 is too close to bend 1", "severity": "blocking"},
            {"code": "internal", "message": "Private reviewer note", "customer_visible": False}]
        self.client.create_quote.side_effect = None
        self.client.create_quote.return_value = result
        self.panel.quote_button.click()
        self.wait()
        self.assertIn("Hole 2", self.panel.report.toPlainText())
        self.assertNotIn("Private reviewer", self.panel.report.toPlainText())
        self.assertFalse(self.panel.checkout_button.isEnabled())

    def test_closing_panel_during_network_work_does_not_open_a_browser(self):
        self.analyze()
        self.panel.quote_button.click()
        self.wait()
        entered, release = threading.Event(), threading.Event()
        def checkout(*args, **kwargs):
            entered.set()
            release.wait()
            return {"id": "cart_1", "status": "open", "cart_url": "https://www.rmfg.com/cart/private-link"}
        self.client.create_checkout.side_effect = checkout
        self.panel.checkout_button.click()
        try:
            self.fixture.wait_for(entered.is_set)
            self.panel.close()
        finally:
            release.set()
        self.wait()
        self.assertEqual(self.opened, [])

    def test_clearing_material_removes_quote_eligibility_and_stales_the_quote(self):
        self.analyze()
        self.panel.quote_button.click()
        self.wait()
        combo = self.panel.table.cellWidget(0, 2)
        combo.setCurrentIndex(0)
        self.assertEqual(self.controller.status()["selections"], {})
        self.assertFalse(self.panel.quote_button.isEnabled())
        self.assertFalse(self.panel.checkout_button.isEnabled())
        self.assertTrue(self.controller.status()["quote"]["stale"])

    def test_rejected_busy_request_does_not_replace_the_running_job_identity(self):
        self.analyze()
        entered, release = threading.Event(), threading.Event()
        def quote(*args, **kwargs):
            entered.set()
            release.wait()
            return self.domain.quote()
        self.client.create_quote.side_effect = quote
        self.panel.quote_button.click()
        try:
            self.fixture.wait_for(entered.is_set)
            running_key = self.controller.status()["quote_job"]
            self.controller.set_quantity(11)
            with self.assertRaises(RuntimeError):
                self.controller.request_quote()
            self.assertEqual(self.controller.status()["quote_job"], running_key)
        finally:
            release.set()
        self.wait()

    def test_failed_replacement_quote_retries_its_saved_key(self):
        self.analyze()
        self.controller.request_quote()
        self.wait()
        old_key = self.controller.status()["quote_job"]
        self.client.create_quote.side_effect = ConnectionError("Uncertain response")
        self.controller.request_quote()
        self.wait()
        retry_key = self.controller.status()["quote_job"]
        self.assertNotEqual(retry_key, old_key)
        self.controller.request_quote()
        self.wait()
        self.assertEqual(self.controller.status()["quote_job"], retry_key)
        self.assertFalse(self.controller.status()["can_checkout"])

    def test_catalog_refresh_cannot_restore_checkout_for_a_superseded_quote(self):
        self.analyze()
        self.controller.request_quote()
        self.wait()
        self.client.create_quote.side_effect = ConnectionError("Uncertain response")
        self.controller.request_quote()
        self.wait()
        self.controller.load_materials()
        self.wait()
        self.assertTrue(self.controller.status()["quote"]["stale"])
        self.assertFalse(self.controller.status()["can_checkout"])

    def test_detected_thickness_heading_is_readable_at_the_default_size(self):
        QtWidgets.QApplication.processEvents()
        header = self.panel.table.horizontalHeader()
        label = self.panel.table.horizontalHeaderItem(1).text()
        self.assertGreaterEqual(header.sectionSize(1), header.fontMetrics().horizontalAdvance(label) + 20)
