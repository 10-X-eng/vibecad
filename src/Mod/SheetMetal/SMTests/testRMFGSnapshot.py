# SPDX-License-Identifier: LGPL-2.1-or-later
"""Manufacturing requests bind exact exported bytes to model and configuration."""

import unittest
from unittest.mock import Mock


class TestRMFGSnapshot(unittest.TestCase):
    def setUp(self):
        from SheetMetalRMFGSnapshot import ExportSnapshot, QuoteRequest
        self.ExportSnapshot, self.QuoteRequest = ExportSnapshot, QuoteRequest
        self.revision = {"document_uid": "document", "document_session": "session",
                         "object_name": "EditableSheet", "structural_revision": 4,
                         "input_hash": "a" * 64}
        self.data = b"ISO-10303-21;\nfolded STEP snapshot\nEND-ISO-10303-21;"
        self.export = ExportSnapshot(self.revision, self.data)
        self.configuration = {"parts": [{"part_id": "part_1", "material_id": "material_1"}]}

    def test_export_copies_revision_and_binds_the_exact_bytes(self):
        import hashlib
        self.revision["structural_revision"] = 99
        self.assertEqual(self.export.revision["structural_revision"], 4)
        revision = self.export.revision
        revision["document_uid"] = "other"
        self.assertEqual(self.export.revision["document_uid"], "document")
        self.assertEqual(self.export.step_sha256, hashlib.sha256(self.data).hexdigest())
        self.assertEqual(self.export.step_bytes, self.data)
        self.assertNotIn("folded STEP snapshot", repr(self.export))

    def test_model_changes_reopen_and_undo_aba_make_the_export_stale(self):
        self.assertTrue(self.export.matches_revision(self.revision))
        for field, value in (("document_uid", "other"), ("document_session", "reopened"),
                             ("object_name", "OtherSheet"), ("structural_revision", 5),
                             ("input_hash", "b" * 64)):
            with self.subTest(field=field):
                self.assertFalse(self.export.matches_revision({**self.revision, field: value}))
        self.assertFalse(self.export.matches_revision({**self.revision, "structural_revision": 6}))

    def test_quote_keeps_an_immutable_configuration_and_design_unit_quantity(self):
        request = self.QuoteRequest(self.export, "design_1", self.configuration, 10)
        self.configuration["parts"][0]["material_id"] = "changed"
        self.assertEqual(request.items(), [{"design_id": "design_1", "quantity": 10,
            "configuration": {"parts": [{"part_id": "part_1", "material_id": "material_1"}]}}])
        items = request.items()
        items[0]["quantity"] = 99
        self.assertEqual(request.items()[0]["quantity"], 10)
        client = Mock()
        request.submit(client)
        request.submit(client)
        self.assertEqual(client.create_quote.call_args_list[0], client.create_quote.call_args_list[1])
        self.assertEqual(client.create_quote.call_args.kwargs["operation_key"], request.operation_key)

    def test_request_identity_includes_configuration_quantity_design_and_export(self):
        request = self.QuoteRequest(self.export, "design_1", self.configuration, 10)
        same = self.QuoteRequest(self.export, "design_1", self.configuration, 10)
        self.assertEqual(request.fingerprint, same.fingerprint)
        self.assertNotEqual(request.operation_key, same.operation_key)
        changed = [self.QuoteRequest(self.export, "design_2", self.configuration, 10),
                   self.QuoteRequest(self.export, "design_1", self.configuration, 11),
                   self.QuoteRequest(self.export, "design_1", {"parts": []}, 10),
                   self.QuoteRequest(self.ExportSnapshot(self.revision, self.data + b"\n"),
                                     "design_1", self.configuration, 10),
                   self.QuoteRequest(self.ExportSnapshot({**self.revision, "structural_revision": 5}, self.data),
                                     "design_1", self.configuration, 10)]
        for other in changed:
            self.assertNotEqual(request.fingerprint, other.fingerprint)

    def test_stale_quote_check_does_not_accept_equivalent_geometry_after_an_edit(self):
        request = self.QuoteRequest(self.export, "design_1", self.configuration, 10)
        self.assertTrue(request.is_current(self.revision, "design_1", self.configuration, 10))
        self.assertFalse(request.is_current({**self.revision, "structural_revision": 5},
                                            "design_1", self.configuration, 10))
        self.assertFalse(request.is_current(self.revision, "design_1", self.configuration, 11))
        self.assertFalse(request.is_current(self.revision, "design_2", self.configuration, 10))
        self.assertFalse(request.is_current(self.revision, "design_1", {"parts": []}, 10))

    def test_invalid_snapshots_and_quantities_are_rejected(self):
        for revision in ({}, {**self.revision, "structural_revision": True},
                         {**self.revision, "structural_revision": -1},
                         {**self.revision, "input_hash": ""}):
            with self.subTest(revision=revision), self.assertRaises((ValueError, TypeError)):
                self.ExportSnapshot(revision, self.data)
        for quantity in (True, 0, -1, 1.5, 1_000_001):
            with self.subTest(quantity=quantity), self.assertRaises((ValueError, TypeError)):
                self.QuoteRequest(self.export, "design_1", self.configuration, quantity)


if __name__ == "__main__":
    unittest.main()
