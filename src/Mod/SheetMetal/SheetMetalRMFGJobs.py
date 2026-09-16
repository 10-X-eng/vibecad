# SPDX-License-Identifier: LGPL-2.1-or-later
"""Durable RMFG request records; call on workers, never during GUI painting.

Commit inputs before network writes. Reload the same key and bytes after an
uncertain response. Responses are append-only observations, so a late pending
response cannot erase an earlier ready response or its remote ID. The workflow
owner interprets service states and checks current document authority before
submitting or publishing; these records do not grant that authority.

The private database contains model/configuration data and service responses,
never access/refresh credentials. Network requests and automatic retries are
deliberately outside this persistence layer.
"""

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from SheetMetalRMFGClient import _identifier
from SheetMetalRMFGSnapshot import ExportSnapshot


_KINDS = {"analyze", "dfm", "quote", "checkout"}
_MAX_JSON_BYTES = 2 * 1024 * 1024
_PAGE_BYTES = 8 * 1024 * 1024


def _json(value):
    if not isinstance(value, Mapping):
        raise TypeError("Manufacturing data must be a JSON object")
    encoded = json.dumps(dict(value), sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > _MAX_JSON_BYTES:
        raise ValueError("The manufacturing record exceeds its storage limit")
    return encoded


def _page(offset, limit):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Use a nonnegative offset and page limit from 1 to 100")


@dataclass(frozen=True)
class StoredJob:
    operation_key: str
    kind: str
    export: ExportSnapshot = field(repr=False)
    _payload_json: str = field(repr=False)

    @property
    def payload(self):
        return json.loads(self._payload_json)


@dataclass(frozen=True)
class JobSummary:
    operation_key: str
    kind: str
    step_sha256: str
    _revision_json: str = field(repr=False)

    @property
    def revision(self):
        return json.loads(self._revision_json)


class JobStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        with self._connection() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError("This RMFG job database requires a newer application")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS exports (
                    id TEXT PRIMARY KEY, revision_json TEXT NOT NULL,
                    step_sha256 TEXT NOT NULL, step BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation_key TEXT UNIQUE NOT NULL, kind TEXT NOT NULL,
                    export_id TEXT NOT NULL REFERENCES exports(id), payload_json TEXT NOT NULL,
                    document_uid TEXT NOT NULL, object_name TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS sheet_jobs ON jobs(document_uid, object_name, sequence);
                CREATE TABLE IF NOT EXISTS observations (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation_key TEXT NOT NULL REFERENCES jobs(operation_key),
                    response_json TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS job_observations ON observations(operation_key, sequence);
                PRAGMA user_version=1;
            """)

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def record(self, *, kind, export, payload, operation_key):
        if kind not in _KINDS:
            raise ValueError("Unsupported RMFG manufacturing operation")
        key = _identifier(operation_key, "operation_key")
        if not isinstance(export, ExportSnapshot):
            raise TypeError("A manufacturing request requires its exact export snapshot")
        body, revision = _json(payload), _json(export.revision)
        export_id = hashlib.sha256((revision + "\0" + export.step_sha256).encode("utf-8")).hexdigest()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT kind, export_id, payload_json FROM jobs WHERE operation_key=?", (key,)).fetchone()
            if previous is not None:
                if tuple(previous) != (kind, export_id, body):
                    raise ValueError("An operation key cannot be reused for changed manufacturing inputs")
            else:
                connection.execute("INSERT OR IGNORE INTO exports VALUES (?, ?, ?, ?)",
                                   (export_id, revision, export.step_sha256, export.step_bytes))
                connection.execute("""INSERT INTO jobs
                    (operation_key, kind, export_id, payload_json, document_uid, object_name)
                    VALUES (?, ?, ?, ?, ?, ?)""", (key, kind, export_id, body,
                        export.revision["document_uid"], export.revision["object_name"]))
        return StoredJob(key, kind, export, body)

    def get(self, operation_key):
        with self._connection() as connection:
            row = connection.execute("""SELECT j.operation_key, j.kind, j.payload_json,
                e.revision_json, e.step_sha256, e.step FROM jobs j
                JOIN exports e ON e.id=j.export_id WHERE j.operation_key=?""", (operation_key,)).fetchone()
        if row is None:
            raise KeyError(operation_key)
        export = ExportSnapshot(json.loads(row["revision_json"]), row["step"])
        if export.step_sha256 != row["step_sha256"]:
            raise RuntimeError("The saved manufacturing export failed its integrity check")
        return StoredJob(row["operation_key"], row["kind"], export, row["payload_json"])

    def for_sheet(self, document_uid, object_name, *, offset=0, limit=20):
        """List metadata across saved sessions without loading STEP blobs."""
        _page(offset, limit)
        with self._connection() as connection:
            rows = connection.execute("""SELECT j.operation_key, j.kind, e.step_sha256, e.revision_json
                FROM jobs j JOIN exports e ON e.id=j.export_id
                WHERE j.document_uid=? AND j.object_name=? ORDER BY j.sequence DESC LIMIT ? OFFSET ?""",
                (document_uid, object_name, limit, offset)).fetchall()
        return [JobSummary(row["operation_key"], row["kind"], row["step_sha256"], row["revision_json"])
                for row in rows]

    def summary(self, operation_key):
        with self._connection() as connection:
            row = connection.execute("""SELECT j.operation_key, j.kind, e.step_sha256, e.revision_json
                FROM jobs j JOIN exports e ON e.id=j.export_id WHERE j.operation_key=?""",
                (operation_key,)).fetchone()
        if row is None:
            raise KeyError(operation_key)
        return JobSummary(row["operation_key"], row["kind"], row["step_sha256"], row["revision_json"])

    def request_payload(self, operation_key):
        """Read saved configuration without materializing the STEP blob."""
        with self._connection() as connection:
            row = connection.execute("SELECT payload_json FROM jobs WHERE operation_key=?", (operation_key,)).fetchone()
        if row is None:
            raise KeyError(operation_key)
        return json.loads(row[0])

    def last_response(self, operation_key):
        """Read the latest local observation, not proof of current remote status."""
        with self._connection() as connection:
            row = connection.execute("SELECT response_json FROM observations WHERE operation_key=? ORDER BY sequence DESC LIMIT 1",
                                     (operation_key,)).fetchone()
        return None if row is None else json.loads(row[0])

    def observe(self, operation_key, response):
        encoded = _json(response)
        with self._connection() as connection:
            if connection.execute("SELECT 1 FROM jobs WHERE operation_key=?", (operation_key,)).fetchone() is None:
                raise KeyError(operation_key)
            connection.execute("INSERT INTO observations (operation_key, response_json) VALUES (?, ?)",
                               (operation_key, encoded))

    def observations(self, operation_key, *, offset=0, limit=100):
        """Read append-order pages, also capped by bytes; advance by returned count."""
        _page(offset, limit)
        result, size = [], 0
        with self._connection() as connection:
            rows = connection.execute("""SELECT response_json FROM observations
                WHERE operation_key=? ORDER BY sequence LIMIT ? OFFSET ?""", (operation_key, limit, offset))
            for row in rows:
                size += len(row[0].encode("utf-8"))
                if size > _PAGE_BYTES:
                    break
                result.append(json.loads(row[0]))
        return result


def _remote_id(response):
    try:
        return _identifier(response.get("id"), "resource id")
    except (AttributeError, ValueError) as error:
        raise RuntimeError("RMFG returned no usable durable resource ID") from error


def run_saved(store, client, operation_key):
    """Submit saved inputs once, or read the already recorded remote resource.

    This synchronous function belongs on a worker. Each invocation makes one
    request. An uncertain write leaves its saved key/inputs available for an
    explicit retry; it never creates a replacement job or repeats automatically.
    The GUI/native owner still checks document revision and configuration before
    calling and before publishing, and interprets remote manufacturing states.
    """
    summary = store.summary(operation_key)
    if summary.kind not in _KINDS:
        raise RuntimeError("The saved manufacturing operation is unsupported")
    resource_id, offset = None, 0
    while True:
        page = store.observations(operation_key, offset=offset)
        if not page:
            break
        for response in page:
            try:
                current = _remote_id(response)
            except RuntimeError:
                # A malformed/ID-less response is uncertain, not permission to
                # allocate a new key. This explicit invocation may retry the
                # original persisted request if no usable ID was ever saved.
                continue
            if resource_id is not None and resource_id != current:
                raise RuntimeError("Saved responses disagree about this manufacturing resource")
            resource_id = current
        offset += len(page)
    if resource_id is not None:
        reader = getattr(client, {"analyze": "design", "dfm": "dfm",
                                  "quote": "quote", "checkout": "cart"}[summary.kind])
        response = reader(resource_id)
    else:
        job = store.get(operation_key)
        payload = job.payload
        if job.kind == "analyze":
            response = client.analyze(job.export.step_bytes, filename=payload["filename"],
                                      operation_key=operation_key)
        elif job.kind == "dfm":
            response = client.create_dfm(payload["design_id"], payload["configuration"],
                                         operation_key=operation_key)
        elif job.kind == "quote":
            response = client.create_quote(payload["items"], operation_key=operation_key)
        else:
            response = client.create_checkout(payload["items"], operation_key=operation_key)
    # Preserve remote evidence before publishing, including a malformed reply
    # that requires diagnosis. A failed save cannot be reported as completed.
    store.observe(operation_key, response)
    if resource_id is not None and _remote_id(response) != resource_id:
        raise RuntimeError("RMFG returned a different manufacturing resource")
    _remote_id(response)
    return response
