# SPDX-License-Identifier: LGPL-2.1-or-later
"""Saved manufacturing jobs remain useful without adopting another revision."""

import unittest
from unittest.mock import patch

from SheetMetalRMFGJobs import JobStore
from SMTests import testRMFGManufacturing


class TestRMFGSavedJobs(unittest.TestCase):
    def setUp(self):
        self.fixture = testRMFGManufacturing.TestRMFGManufacturing()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.backend, self.client = self.fixture.backend, self.fixture.client
        self.export = self.fixture.export
        self.uid = self.export.revision["document_uid"]
        self.name = self.export.revision["object_name"]
        self.client.analyze.return_value = self.fixture.design
        self.client.design.return_value = self.fixture.design
        self.client.create_quote.return_value = self.fixture.quote()
        self.client.quote.return_value = self.fixture.quote()
        self.backend.analyze(self.export, "analysis-key")
        self.backend.evaluate(self.export, self.fixture.design, self.fixture.materials,
                              self.fixture.selections, 10, "quote-key")
        self.client.reset_mock()

    def test_listing_is_paged_and_never_loads_step_or_contacts_rmfg(self):
        with patch.object(JobStore, "get", side_effect=AssertionError("STEP load")):
            first = self.backend.list_jobs(self.uid, self.name, offset=0, limit=1)
            second = self.backend.list_jobs(self.uid, self.name, offset=first["next_offset"], limit=1)
        self.assertEqual(first["jobs"][0]["operation_key"], "quote-key")
        self.assertEqual(first["next_offset"], 1)
        self.assertEqual(second["jobs"][0]["operation_key"], "analysis-key")
        self.assertIsNone(second["next_offset"])
        self.assertEqual(self.client.mock_calls, [])

    def test_inspection_is_local_and_removes_private_links_and_findings(self):
        quote = self.fixture.quote()
        quote["review_url"] = "https://www.rmfg.com/private-review"
        quote["items"][0]["dfm"]["parts"][0]["issues"] = [
            {"code": "private", "message": "Private reviewer note", "customer_visible": False},
            {"code": "hole", "message": "Hole too close to bend", "severity": "blocking"}]
        JobStore(self.backend.database).observe("quote-key", quote)
        with patch.object(JobStore, "get", side_effect=AssertionError("STEP load")):
            detail = self.backend.inspect_job(self.uid, self.name, "quote-key")
        self.assertEqual(detail["response"]["id"], "quote_1")
        self.assertEqual(detail["quantity"], 10)
        self.assertIn("Hole too close", str(detail))
        self.assertNotIn("Private reviewer", str(detail))
        self.assertNotIn("private-review", str(detail))
        self.assertEqual(self.client.mock_calls, [])

    def test_resuming_a_quote_uses_saved_bytes_settings_and_remote_reads(self):
        restored = self.backend.resume_job(self.uid, self.name, "quote-key", self.export.revision)
        self.assertEqual(restored["export"].step_bytes, self.export.step_bytes)
        self.assertEqual(restored["quantity"], 10)
        self.assertEqual(restored["selections"], self.fixture.selections)
        self.assertEqual(restored["response"]["status"], "ready")
        self.client.quote.assert_called_once_with("quote_1")
        self.client.design.assert_called_once_with("design_1")
        self.client.create_quote.assert_not_called()
        self.client.analyze.assert_not_called()

    def test_resuming_analysis_does_not_upload_again(self):
        result = self.backend.resume_job(self.uid, self.name, "analysis-key", self.export.revision)
        self.assertEqual(result["response"]["id"], "design_1")
        self.client.design.assert_called_once_with("design_1")
        self.client.analyze.assert_not_called()

    def test_stale_or_foreign_resume_is_rejected_before_loading_export_or_network(self):
        for uid, revision in (("other", self.export.revision),
            (self.uid, {**self.export.revision, "structural_revision": 999}),
            (self.uid, {**self.export.revision, "document_session": "reopened"})):
            with self.subTest(uid=uid, revision=revision), \
                    patch.object(JobStore, "get", side_effect=AssertionError("STEP load")), \
                    self.assertRaises(ValueError):
                self.backend.resume_job(uid, self.name, "quote-key", revision)
        self.assertEqual(self.client.mock_calls, [])
        with self.assertRaises(ValueError):
            self.backend.inspect_job("other", self.name, "quote-key")

    def test_uncertain_analysis_resume_retains_original_upload_key(self):
        store = JobStore(self.backend.database)
        store.record(kind="analyze", export=self.export, payload={"filename": "sheet.step"}, operation_key="uncertain")
        result = self.backend.resume_job(self.uid, self.name, "uncertain", self.export.revision)
        self.assertEqual(result["operation_key"], "uncertain")
        self.client.analyze.assert_called_once_with(self.export.step_bytes, filename="sheet.step", operation_key="uncertain")
