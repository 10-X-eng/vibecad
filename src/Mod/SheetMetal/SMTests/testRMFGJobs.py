# SPDX-License-Identifier: LGPL-2.1-or-later
"""Manufacturing writes retain immutable inputs and remote IDs across restarts."""

from pathlib import Path
from contextlib import closing
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from SheetMetalRMFGSnapshot import ExportSnapshot, QuoteRequest


class TestRMFGJobs(unittest.TestCase):
    def setUp(self):
        from SheetMetalRMFGJobs import JobStore
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)/"jobs.sqlite"
        self.store = JobStore(self.path)
        self.revision = {"document_uid": "doc", "document_session": "session",
            "object_name": "Sheet", "structural_revision": 4, "input_hash": "a"*64}
        self.export = ExportSnapshot(self.revision, b"folded STEP bytes")
        self.quote = QuoteRequest(self.export, "design_1", {"parts": [
            {"part_id": "part_1", "material_id": "steel_1"}]}, 10)

    def record(self, **changes):
        values = {"kind": "quote", "export": self.export,
                  "payload": {"items": self.quote.items()}, "operation_key": self.quote.operation_key}
        return self.store.record(**{**values, **changes})

    def test_restart_retains_export_configuration_quantity_and_retry_key(self):
        from SheetMetalRMFGJobs import JobStore
        original = self.record()
        loaded = JobStore(self.path).get(original.operation_key)
        self.assertEqual(loaded.kind, "quote")
        self.assertEqual(loaded.export.step_bytes, self.export.step_bytes)
        self.assertEqual(loaded.export.revision, self.revision)
        self.assertEqual(loaded.payload, {"items": self.quote.items()})
        self.assertEqual(loaded.operation_key, self.quote.operation_key)
        self.assertTrue(loaded.export.matches_revision(self.revision))
        self.assertFalse(loaded.export.matches_revision({**self.revision, "structural_revision": 6}))
        loaded.payload["items"][0]["quantity"] = 99
        self.assertEqual(loaded.payload["items"][0]["quantity"], 10)

    def test_retry_key_cannot_be_reused_for_changed_inputs(self):
        self.record()
        self.record()
        self.assertEqual(len(self.store.for_sheet("doc", "Sheet")), 1)
        for changes in ({"kind": "checkout"}, {"payload": {"items": []}},
                        {"export": ExportSnapshot(self.revision, b"different STEP")},
                        {"export": ExportSnapshot({**self.revision, "structural_revision": 5}, self.export.step_bytes)}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.record(**changes)
        self.assertEqual(self.store.get(self.quote.operation_key).payload, {"items": self.quote.items()})

    def test_pending_and_ready_remote_ids_survive_reopen_without_overwriting_observations(self):
        from SheetMetalRMFGJobs import JobStore
        key = self.record().operation_key
        responses = [{"id": "quote_1", "status": "processing"},
                     {"id": "quote_1", "status": "ready"},
                     {"id": "quote_1", "status": "processing"}]
        for response in responses:
            self.store.observe(key, response)
        loaded = JobStore(self.path).observations(key)
        self.assertEqual(loaded, responses)
        loaded[1]["status"] = "blocked"
        self.assertEqual(self.store.observations(key)[1]["status"], "ready")

    def test_sheet_listing_retains_sessions_but_excludes_other_documents(self):
        self.record()
        reopened = ExportSnapshot({**self.revision, "document_session": "reopened"}, self.export.step_bytes)
        newer = self.record(export=reopened, operation_key="new-request")
        other = ExportSnapshot({**self.revision, "document_uid": "other"}, self.export.step_bytes)
        self.record(export=other, operation_key="other-request")
        found = self.store.for_sheet("doc", "Sheet", limit=1)
        self.assertEqual([job.operation_key for job in found], [newer.operation_key])
        self.assertEqual(len(self.store.for_sheet("doc", "Sheet")), 2)

    def test_listing_never_reads_step_blobs_and_database_is_private(self):
        self.record()
        original = sqlite3.connect
        def connect(*args, **kwargs):
            connection = original(*args, **kwargs)
            def authorize(action, table, column, database, trigger):
                if action == sqlite3.SQLITE_READ and table == "exports" and column == "step":
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            connection.set_authorizer(authorize)
            return connection
        with patch.object(sqlite3, "connect", connect):
            result = self.store.for_sheet("doc", "Sheet")
        self.assertEqual(result[0].revision, self.revision)
        if os.name != "nt":
            self.assertEqual(self.path.stat().st_mode & 0o077, 0)

    def test_damaged_export_is_reported_without_discarding_the_job(self):
        key = self.record().operation_key
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE exports SET step=?", (b"damaged",))
        with self.assertRaisesRegex(RuntimeError, "integrity"):
            self.store.get(key)
        self.assertEqual(self.store.for_sheet("doc", "Sheet")[0].operation_key, key)

    def test_independent_processes_append_without_losing_remote_ids(self):
        key = self.record().operation_key
        code = ("import sys; from SheetMetalRMFGJobs import JobStore; "
                "store=JobStore(sys.argv[1]); "
                "[store.observe(sys.argv[2], {'worker':sys.argv[3], 'index':i}) for i in range(10)]")
        children = [subprocess.Popen([sys.executable, "-c", code, str(self.path), key, str(index)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE) for index in range(2)]
        for child in children:
            stdout, stderr = child.communicate()
            self.assertEqual(child.returncode, 0, (stdout, stderr))
        self.assertEqual(len(self.store.observations(key)), 20)

    def test_invalid_job_or_observation_never_replaces_existing_request(self):
        key = self.record().operation_key
        with self.assertRaises(ValueError):
            self.record(kind="payment")
        with self.assertRaises(ValueError):
            self.record(payload={"quantity": float("nan")})
        with self.assertRaises(KeyError):
            self.store.observe("missing", {"status": "ready"})
        self.assertEqual(self.store.observations(key), [])
        self.assertEqual(self.store.get(key).export.step_bytes, self.export.step_bytes)


class TestRMFGJobExecution(unittest.TestCase):
    def setUp(self):
        self.fixture = TestRMFGJobs()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.store = self.fixture.store

    def test_upload_is_saved_before_request_and_reopen_resumes_the_remote_id(self):
        from SheetMetalRMFGJobs import JobStore, run_saved
        job = self.store.record(kind="analyze", export=self.fixture.export,
                                payload={"filename": "bracket.step"}, operation_key="upload-key")
        client = Mock()
        def analyze(data, *, filename, operation_key):
            self.assertEqual(JobStore(self.fixture.path).get(operation_key).export.step_bytes, data)
            self.assertEqual(filename, "bracket.step")
            return {"id": "design_1", "status": "queued"}
        client.analyze.side_effect = analyze
        client.design.return_value = {"id": "design_1", "status": "ready"}
        self.assertEqual(run_saved(self.store, client, job.operation_key)["status"], "queued")
        self.assertEqual(run_saved(JobStore(self.fixture.path), client, job.operation_key)["status"], "ready")
        client.analyze.assert_called_once()
        client.design.assert_called_once_with("design_1")

    def test_uncertain_write_retains_the_same_payload_and_key_for_explicit_retry(self):
        from SheetMetalRMFGJobs import run_saved
        job = self.fixture.record()
        client = Mock()
        client.create_quote.side_effect = [OSError("connection lost"), {"id": "quote_1", "status": "processing"}]
        with self.assertRaises(OSError):
            run_saved(self.store, client, job.operation_key)
        self.assertEqual(self.store.observations(job.operation_key), [])
        run_saved(self.store, client, job.operation_key)
        self.assertEqual(client.create_quote.call_args_list[0], client.create_quote.call_args_list[1])
        self.assertEqual(client.create_quote.call_args.kwargs["operation_key"], job.operation_key)
        self.assertEqual(client.create_quote.call_args.args[0][0]["quantity"], 10)

    def test_reply_without_id_can_be_retried_with_the_original_saved_key(self):
        from SheetMetalRMFGJobs import run_saved
        job = self.fixture.record()
        client = Mock()
        client.create_quote.side_effect = [{"status": "processing"}, {"id": "quote_1", "status": "processing"}]
        with self.assertRaises(RuntimeError):
            run_saved(self.store, client, job.operation_key)
        result = run_saved(self.store, client, job.operation_key)
        self.assertEqual(result["id"], "quote_1")
        self.assertEqual(client.create_quote.call_args_list[0], client.create_quote.call_args_list[1])
        self.assertEqual(len(self.store.observations(job.operation_key)), 2)

    def test_dfm_and_checkout_resume_by_id_without_recreating_resources(self):
        from SheetMetalRMFGJobs import run_saved
        client = Mock()
        for kind, create, read, payload in (
                ("dfm", "create_dfm", "dfm", {"design_id": "design_1", "configuration": {"parts": []}}),
                ("checkout", "create_checkout", "cart", {"items": self.fixture.quote.items()})):
            with self.subTest(kind=kind):
                job = self.store.record(kind=kind, export=self.fixture.export, payload=payload,
                                        operation_key=kind+"-key")
                getattr(client, create).return_value = {"id": kind+"_1", "status": "ready"}
                getattr(client, read).return_value = {"id": kind+"_1", "status": "ready"}
                run_saved(self.store, client, job.operation_key)
                run_saved(self.store, client, job.operation_key)
                getattr(client, create).assert_called_once()
                getattr(client, read).assert_called_once_with(kind+"_1")

    def test_conflicting_saved_ids_are_reported_before_network_work(self):
        from SheetMetalRMFGJobs import run_saved
        job = self.fixture.record()
        self.store.observe(job.operation_key, {"id": "quote_1", "status": "ready"})
        self.store.observe(job.operation_key, {"id": "quote_2", "status": "ready"})
        client = Mock()
        with self.assertRaises(RuntimeError):
            run_saved(self.store, client, job.operation_key)
        self.assertEqual(client.mock_calls, [])


if __name__ == "__main__":
    unittest.main()
