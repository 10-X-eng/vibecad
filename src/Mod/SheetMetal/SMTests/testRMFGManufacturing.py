# SPDX-License-Identifier: LGPL-2.1-or-later
"""Manufacturing decisions and checkout bind the exact model and catalog choices."""

import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from SheetMetalRMFGSnapshot import ExportSnapshot


class TestRMFGManufacturing(unittest.TestCase):
    def setUp(self):
        import SheetMetalRMFGManufacturing as Manufacturing
        self.module = Manufacturing
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.client = Mock()
        self.backend = Manufacturing.Backend(Path(self.directory.name)/"jobs.sqlite", self.client)
        self.export = ExportSnapshot({"document_uid": "doc", "document_session": "session",
            "object_name": "Sheet", "structural_revision": 4, "input_hash": "a"*64}, b"folded STEP")
        self.design = {"id": "design_1", "status": "ready", "parts": [
            {"id": "part_1", "name": "Bracket", "suggested_process": "sheet_metal", "instance_count": 2}]}
        self.materials = [{"id": "steel_1", "material": "Steel", "type": "cold_rolled",
                           "thickness_mm": 1.6, "bendable": True}]
        self.selections = {"part_1": "steel_1"}
        self.configuration = {"parts": [{"part_id": "part_1", "material_id": "steel_1"}]}

    def quote(self):
        return {"id": "quote_1", "status": "ready", "currency": "usd", "amount_total_cents": 2500,
            "expires_at": (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),
            "items": [{"design_id": "design_1", "quantity": 10, "status": "ready", "dfm": {
                "id": "dfm_1", "design_id": "design_1", "status": "ready",
                "configuration": copy.deepcopy(self.configuration), "parts": [{"part_id": "part_1", "issues": []}]}}]}

    def evaluate(self):
        self.client.create_quote.return_value = self.quote()
        return self.backend.evaluate(self.export, self.design, self.materials, self.selections, 10, "quote-key")

    def test_configuration_requires_every_part_and_observed_material_ids(self):
        build = self.module.configuration
        self.assertEqual(build(self.design, self.materials, self.selections), self.configuration)
        for selection in ({}, {"part_1": "guessed"}, {**self.selections, "other_part": "steel_1"}):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                build(self.design, self.materials, selection)
        design = copy.deepcopy(self.design)
        design["parts"][0]["suggested_process"] = "tube_laser"
        with self.assertRaises(ValueError):
            build(design, self.materials, self.selections)

    def test_quote_retains_design_quantity_and_uses_no_automatic_risk_acceptance(self):
        result = self.evaluate()
        items = self.client.create_quote.call_args.args[0]
        self.assertEqual(items, [{"design_id": "design_1", "quantity": 10, "configuration": self.configuration}])
        self.assertNotIn("accepted_risks", items[0]["configuration"])
        self.assertEqual(result["job_key"], "quote-key")

    def test_checkout_uses_ready_quote_exact_inputs_and_a_website_link(self):
        self.evaluate()
        self.client.quote.return_value = self.quote()
        self.client.create_checkout.return_value = {"id": "cart_1", "status": "open",
                                                   "cart_url": "https://www.rmfg.com/cart/private-link"}
        result = self.backend.checkout(self.export, self.configuration, 10, "quote-key", "checkout-key")
        self.assertEqual(result["url"], "https://www.rmfg.com/cart/private-link")
        self.assertEqual(self.client.create_checkout.call_args.args[0], self.client.create_quote.call_args.args[0])
        self.client.quote.assert_called_once_with("quote_1")
        self.assertFalse(any("pay" in str(call) for call in self.client.mock_calls))

    def test_stale_revision_or_settings_cannot_create_a_cart(self):
        self.evaluate()
        stale = ExportSnapshot({**self.export.revision, "structural_revision": 6}, self.export.step_bytes)
        for export, configuration, quantity in ((stale, self.configuration, 10),
                (self.export, self.configuration, 11), (self.export, {"parts": []}, 10)):
            with self.subTest(quantity=quantity), self.assertRaises(ValueError):
                self.backend.checkout(export, configuration, quantity, "quote-key", "checkout-key")
        self.client.create_checkout.assert_not_called()

    def test_checkout_rejects_blocked_expired_and_changed_server_configuration(self):
        self.evaluate()
        rejected = []
        for status in ("processing", "requires_input", "blocked", "expired", "failed"):
            value = self.quote()
            value["status"] = status
            rejected.append(value)
        value = self.quote()
        value["expires_at"] = "2000-01-01T00:00:00Z"
        rejected.append(value)
        value = self.quote()
        value["items"][0]["dfm"]["configuration"]["parts"][0]["material_id"] = "different"
        rejected.append(value)
        for quote in rejected:
            self.client.quote.return_value = quote
            with self.subTest(quote=quote), self.assertRaises(ValueError):
                self.backend.checkout(self.export, self.configuration, 10, "quote-key", "checkout-key")
        self.client.create_checkout.assert_not_called()

    def test_findings_omit_internal_messages_and_checkout_rejects_foreign_links(self):
        quote = self.quote()
        quote["items"][0]["dfm"]["parts"][0]["issues"] = [
            {"code": "visible", "message": "Move hole 2 away from bend 1", "severity": "blocking", "hole_id": 2},
            {"code": "internal", "message": "Private reviewer note", "customer_visible": False}]
        findings = self.module.findings(quote)
        self.assertTrue(any("Move hole" in item["message"] for item in findings))
        self.assertNotIn("Private reviewer", str(findings))
        for url in ("http://rmfg.com/cart/a", "https://rmfg.com.evil.example/cart/a", "https://user@rmfg.com/cart/a"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.module.checkout_url(url)
