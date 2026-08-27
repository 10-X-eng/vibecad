# SPDX-License-Identifier: LGPL-2.1-or-later

"""Versioned transactional metadata for durable Analysis job identity."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import sys
from typing import Any, Callable, Iterator, Mapping


ANALYSIS_METADATA_SCHEMA_VERSION = 1
TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled", "interrupted"})
KNOWN_STATES = frozenset({
    "prepared", "running_local", "running_remote", "collecting", "verifying",
    "waiting_to_publish", "publishing", *TERMINAL_STATES,
})
ALLOWED_TRANSITIONS = {
    "prepared": frozenset({"running_local", "running_remote", "cancelled", "failed", "interrupted"}),
    "running_local": frozenset({"collecting", "cancelled", "failed", "interrupted"}),
    "running_remote": frozenset({"collecting", "cancelled", "failed", "interrupted"}),
    "collecting": frozenset({"verifying", "cancelled", "failed", "interrupted"}),
    "verifying": frozenset({"waiting_to_publish", "cancelled", "failed", "interrupted"}),
    "waiting_to_publish": frozenset({"publishing", "cancelled", "failed"}),
    "publishing": frozenset({"succeeded", "failed"}),
}


class AnalysisPersistenceError(RuntimeError):
    pass


class AnalysisStoreBusy(AnalysisPersistenceError):
    pass


FaultInjector = Callable[[str, Mapping[str, Any]], None]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clean_id(value: Any, field: str) -> str:
    clean = str(value or "").strip()
    if not clean or clean in {".", ".."} or any(mark in clean for mark in "/\\:"):
        raise AnalysisPersistenceError(f"{field} is not a safe non-empty identifier")
    return clean


def new_job_record(
    *, analysis_id: str, domain: str, adapter_id: str, source_document_uid: str,
    prepared_analysis_sha256: str, dependency_sha256: str,
    input_manifest_sha256: str, execution_spec_sha256: str,
) -> dict[str, Any]:
    now = _utc_now()
    record = {
        "schema_version": ANALYSIS_METADATA_SCHEMA_VERSION,
        "analysis_id": _clean_id(analysis_id, "analysis_id"),
        "domain": str(domain or "").strip(),
        "adapter_id": str(adapter_id or "").strip(),
        "source_document_uid": str(source_document_uid or "").strip(),
        "prepared_analysis_sha256": str(prepared_analysis_sha256 or "").lower(),
        "dependency_sha256": str(dependency_sha256 or "").lower(),
        "input_manifest_sha256": str(input_manifest_sha256 or "").lower(),
        "execution_spec_sha256": str(execution_spec_sha256 or "").lower(),
        "state": "prepared",
        "created_at": now,
        "updated_at": now,
        "attempts": [],
        "artifacts": [],
        "currentness_evaluations": [],
        "publication": {"intent": None, "authorization": None, "receipt": None},
        "events": [{"sequence": 1, "at": now, "state": "prepared", "reason": "created"}],
        "terminal_reason": None,
    }
    for field in ("domain", "adapter_id", "source_document_uid"):
        if not record[field]:
            raise AnalysisPersistenceError(f"{field} must be non-empty")
    for field in (
        "prepared_analysis_sha256", "dependency_sha256",
        "input_manifest_sha256", "execution_spec_sha256",
    ):
        value = record[field]
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise AnalysisPersistenceError(f"{field} must be a SHA-256 digest")
    return record


class AnalysisMetadataStore:
    """One-writer JSON store with atomic replace, backup, and fault points."""

    def __init__(self, root: str | Path, *, fault_injector: FaultInjector | None = None) -> None:
        self.root = Path(root)
        self.records = self.root / "records"
        self.backups = self.root / "backups"
        self.lock_path = self.root / "writer.lock"
        if fault_injector is not None and not callable(fault_injector):
            raise TypeError("fault_injector must be callable")
        self.fault_injector = fault_injector

    def _path(self, analysis_id: str) -> Path:
        return self.records / f"{_clean_id(analysis_id, 'analysis_id')}.json"

    def _fault(self, point: str, record: Mapping[str, Any]) -> None:
        if self.fault_injector is not None:
            self.fault_injector(point, deepcopy(dict(record)))

    @contextmanager
    def _writer(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        stream = self.lock_path.open("a+b")
        try:
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            stream.close()
            raise AnalysisStoreBusy(
                "Another VibeCAD process owns Analysis metadata writes"
            ) from exc
        try:
            yield
        finally:
            try:
                stream.seek(0)
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            finally:
                stream.close()

    def create(self, record: Mapping[str, Any]) -> dict[str, Any]:
        candidate = self._validate(record)
        path = self._path(candidate["analysis_id"])
        with self._writer():
            if path.exists():
                raise AnalysisPersistenceError("Analysis record already exists")
            self._write_atomic(path, candidate, backup=False)
        return deepcopy(candidate)

    def load(self, analysis_id: str) -> dict[str, Any]:
        path = self._path(analysis_id)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise AnalysisPersistenceError("Analysis metadata is missing or corrupt") from exc
        return self._validate(value)

    def transition(
        self, analysis_id: str, state: str, *, reason: str,
        updates: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_state = str(state or "").strip()
        if clean_state not in KNOWN_STATES:
            raise AnalysisPersistenceError("Unknown Analysis lifecycle state")
        with self._writer():
            current = self.load(analysis_id)
            if current["state"] in TERMINAL_STATES:
                if current["state"] == clean_state:
                    return current
                raise AnalysisPersistenceError("A terminal Analysis record cannot reopen")
            if clean_state not in ALLOWED_TRANSITIONS[current["state"]]:
                raise AnalysisPersistenceError(
                    f"Invalid Analysis transition: {current['state']} -> {clean_state}"
                )
            candidate = deepcopy(current)
            for key, value in dict(updates or {}).items():
                if key in {"schema_version", "analysis_id", "created_at", "events"}:
                    raise AnalysisPersistenceError(f"Immutable metadata field: {key}")
                candidate[key] = deepcopy(value)
            now = _utc_now()
            candidate["state"] = clean_state
            candidate["updated_at"] = now
            candidate["terminal_reason"] = str(reason) if clean_state in TERMINAL_STATES else None
            candidate["events"].append({
                "sequence": len(candidate["events"]) + 1,
                "at": now,
                "state": clean_state,
                "reason": str(reason or "").strip(),
            })
            candidate = self._validate(candidate)
            self._write_atomic(self._path(analysis_id), candidate, backup=True)
            return deepcopy(candidate)

    def restart_disposition(self, analysis_id: str) -> dict[str, str]:
        record = self.load(analysis_id)
        state = record["state"]
        if state in TERMINAL_STATES:
            action = "terminal"
        elif state == "running_remote" and any(
            str(item.get("provider_job_id") or "").strip()
            for item in record["attempts"]
            if isinstance(item, Mapping)
        ):
            action = "reconnect_remote"
        elif state in {"prepared", "running_local"}:
            action = "mark_interrupted"
        elif state == "publishing":
            action = "publication_outcome_unknown"
        else:
            action = f"resume_{state}"
        return {"analysis_id": record["analysis_id"], "state": state, "action": action}

    def _write_atomic(self, path: Path, record: Mapping[str, Any], *, backup: bool) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.backups.mkdir(parents=True, exist_ok=True)
        self._fault("before_stage", record)
        encoded = json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            self._fault("after_stage", record)
            if backup and path.exists():
                backup_path = self.backups / f"{path.stem}.previous.json"
                backup_path.write_bytes(path.read_bytes())
            self._fault("before_replace", record)
            os.replace(temporary, path)
            self._fault("after_replace", record)
        finally:
            temporary.unlink(missing_ok=True)

    def _validate(self, value: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise AnalysisPersistenceError("Analysis metadata must be an object")
        record = deepcopy(dict(value))
        if record.get("schema_version") != ANALYSIS_METADATA_SCHEMA_VERSION:
            raise AnalysisPersistenceError("Unsupported Analysis metadata schema version")
        _clean_id(record.get("analysis_id"), "analysis_id")
        if record.get("state") not in KNOWN_STATES:
            raise AnalysisPersistenceError("Unknown Analysis lifecycle state")
        events = record.get("events")
        if not isinstance(events, list) or not events:
            raise AnalysisPersistenceError("Analysis events must be non-empty")
        if [item.get("sequence") for item in events] != list(range(1, len(events) + 1)):
            raise AnalysisPersistenceError("Analysis event sequence is not monotonic")
        return record
