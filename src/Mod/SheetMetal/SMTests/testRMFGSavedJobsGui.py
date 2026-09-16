# SPDX-License-Identifier: LGPL-2.1-or-later
"""Private GUI restoration uses exact persisted revisions and shared controls."""

import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from PySide import QtWidgets

from SMTests import testRMFGManufacturingGui


class TestRMFGSavedJobsGui(unittest.TestCase):
    def setUp(self):
        import SheetMetalRMFGManufacturingGui as Manufacturing
        self.fixture = testRMFGManufacturingGui.TestRMFGManufacturingGui()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.fixture.analyze()
        self.fixture.controller.request_quote()
        self.fixture.wait()
        self.quote_key = self.fixture.controller.status()["quote_job"]
        self.analysis_key = self.fixture.controller.status()["upload_job"]
        self.client, self.model, self.gui = self.fixture.client, self.fixture.model, Manufacturing
        self.client.design.return_value = self.fixture.domain.design
        self.fixture.panel.close()
        self.controller = Manufacturing.Controller(self.model.sheet, self.fixture.domain.backend)
        self.panel = Manufacturing.Panel(self.controller)
        self.panel.show()
        self.addCleanup(self.panel.close)
        self.wait()
        self.client.reset_mock()

    def wait(self):
        self.fixture.fixture.wait_for(lambda: not self.controller.busy)

    def test_saved_quote_restores_panel_settings_without_reupload_or_document_changes(self):
        before = self.model.doc.UndoCount, self.model.sheet.PreparedInputHash
        self.panel.tabs.setCurrentIndex(1)
        self.panel.load_jobs_button.click()
        self.wait()
        self.assertEqual(len(self.controller.status()["jobs"]), 2)
        self.panel.jobs.setCurrentIndex(self.panel.jobs.findData(self.quote_key))
        self.panel.inspect_job_button.click()
        self.wait()
        self.assertTrue(self.panel.resume_job_button.isEnabled())
        self.panel.resume_job_button.click()
        self.wait()
        state = self.controller.status()
        self.assertEqual(state["quote_job"], self.quote_key)
        self.assertEqual(state["quantity"], 10)
        self.assertEqual(state["selections"], {"part_1": "steel_1"})
        self.assertTrue(state["can_checkout"])
        self.assertEqual(self.panel.quantity.value(), 10)
        self.client.analyze.assert_not_called()
        self.client.create_quote.assert_not_called()
        self.assertEqual(before, (self.model.doc.UndoCount, self.model.sheet.PreparedInputHash))
        QtWidgets.QApplication.processEvents()
        self.panel.grab().save(str(Path(os.environ["VIBECAD_TEST_OUTPUT"])/"rmfg-saved-jobs.png"))

    def test_stale_job_can_be_inspected_but_cannot_replace_the_current_quote(self):
        self.model.sheet.Label = "New revision"
        self.controller.list_jobs()
        self.wait()
        self.assertTrue(all(job["stale"] for job in self.controller.status()["jobs"]))
        self.controller.inspect_job(self.quote_key)
        self.wait()
        self.assertIn("Earlier revision", self.panel.saved_report.toPlainText())
        self.assertFalse(self.panel.resume_job_button.isEnabled())
        self.controller.resume_job(self.quote_key)
        self.wait()
        with self.assertRaises(ValueError):
            self.controller.future.result()
        self.assertIsNone(self.controller.status()["quote"])
        self.assertEqual(self.client.mock_calls, [])

    def test_saved_analysis_restores_the_original_upload_key(self):
        self.controller.resume_job(self.analysis_key)
        self.wait()
        self.assertEqual(self.controller.status()["upload_job"], self.analysis_key)
        self.assertEqual(self.controller.status()["design"]["id"], "design_1")
        self.client.analyze.assert_not_called()
        self.client.design.assert_called_once_with("design_1")

    def test_restoration_rejects_an_edit_while_remote_read_is_pending(self):
        self._pending_restore(lambda: setattr(self.model.sheet, "Label", "Changed during restore"))
        self.assertIsNone(self.controller.status()["quote"])

    def test_restoration_does_not_overwrite_quantity_changed_while_waiting(self):
        self._pending_restore(lambda: self.panel.quantity.setValue(11))
        self.assertEqual(self.controller.status()["quantity"], 11)
        self.assertIsNone(self.controller.status()["quote"])

    def _pending_restore(self, change):
        entered, release = threading.Event(), threading.Event()
        def quote(*args, **kwargs):
            entered.set()
            release.wait()
            return self.fixture.domain.quote()
        self.client.quote.side_effect = quote
        self.controller.resume_job(self.quote_key)
        try:
            self.fixture.fixture.wait_for(entered.is_set)
            change()
        finally:
            release.set()
        self.wait()
        with self.assertRaises(RuntimeError):
            self.controller.future.result()

    def test_document_close_releases_its_controller_and_panel(self):
        key = id(self.model.doc), self.model.sheet.Name
        with patch.object(self.gui, "_controllers", {key: self.controller}), \
                patch.object(self.gui, "_panels", {key: self.panel}):
            # Run this fixture's registered close exactly once; later outer
            # cleanups find its cleanup stack empty.
            self.model.doCleanups()
            QtWidgets.QApplication.processEvents()
            QtWidgets.QApplication.processEvents()
            self.assertTrue(self.controller._closed)
            self.assertFalse(self.panel.isVisible())
            self.assertNotIn(key, self.gui._controllers)
            self.assertNotIn(key, self.gui._panels)
