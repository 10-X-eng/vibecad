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
MAX_PUBLICATION_EVIDENCE_BYTES = 64 * 1024
MAX_DISCOVERABLE_ANALYSES = 4096
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

    def list_records(self) -> tuple[dict[str, Any], ...]:
        """Read every bounded durable record without acquiring write authority."""

        if not self.records.exists():
            return ()
        paths = tuple(sorted(self.records.glob("*.json"), key=lambda path: path.name))
        if len(paths) > MAX_DISCOVERABLE_ANALYSES:
            raise AnalysisPersistenceError(
                "Analysis metadata discovery exceeds its bounded record limit"
            )
        records = []
        for path in paths:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                record = self._validate(value)
            except (OSError, ValueError, AnalysisPersistenceError) as exc:
                raise AnalysisPersistenceError(
                    f"Analysis metadata discovery found an invalid record: {path.name}"
                ) from exc
            if path.stem != record["analysis_id"]:
                raise AnalysisPersistenceError(
                    f"Analysis metadata filename does not match its identity: {path.name}"
                )
            records.append(record)
        return tuple(records)

    def find_by_document_uid(self, document_uid: str) -> tuple[dict[str, Any], ...]:
        """Find exact records for one document identity; never infer by path or label."""

        identity = str(document_uid or "").strip()
        if not identity:
            raise AnalysisPersistenceError("document_uid must be non-empty")
        matches = []
        for record in self.list_records():
            source_uid = record.get("source_document_uid")
            if not isinstance(source_uid, str) or not source_uid.strip():
                raise AnalysisPersistenceError(
                    "Discovered Analysis metadata has no source document identity"
                )
            if source_uid == identity:
                matches.append(record)
        return tuple(matches)

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

    def begin_attempt(
        self,
        analysis_id: str,
        *,
        provider_id: str,
        provider_kind: str,
        provider_job_id: str = "",
    ) -> dict[str, Any]:
        kind = str(provider_kind or "").strip()
        if kind not in {"local", "remote"}:
            raise AnalysisPersistenceError("provider_kind must be local or remote")
        record = self.load(analysis_id)
        attempt = {
            "attempt": len(record["attempts"]) + 1,
            "provider_id": str(provider_id or "").strip(),
            "provider_kind": kind,
            "provider_job_id": str(provider_job_id or "").strip(),
            "started_at": _utc_now(),
            "terminal_reason": None,
        }
        if not attempt["provider_id"]:
            raise AnalysisPersistenceError("provider_id must be non-empty")
        return self.transition(
            analysis_id,
            "running_remote" if kind == "remote" else "running_local",
            reason="provider_attempt_started",
            updates={"attempts": [*record["attempts"], attempt]},
        )

    def retry_interrupted(
        self,
        analysis_id: str,
        *,
        expected_prepared_analysis_sha256: str,
        expected_dependency_sha256: str,
        expected_input_manifest_sha256: str,
        expected_execution_spec_sha256: str,
    ) -> dict[str, Any]:
        expected = {
            "prepared_analysis_sha256": expected_prepared_analysis_sha256,
            "dependency_sha256": expected_dependency_sha256,
            "input_manifest_sha256": expected_input_manifest_sha256,
            "execution_spec_sha256": expected_execution_spec_sha256,
        }
        with self._writer():
            current = self.load(analysis_id)
            if current["state"] != "interrupted":
                raise AnalysisPersistenceError("Only an interrupted analysis can retry")
            if any(current[key] != str(value).lower() for key, value in expected.items()):
                raise AnalysisPersistenceError("Retry identity does not match frozen analysis inputs")
            candidate = deepcopy(current)
            now = _utc_now()
            candidate["state"] = "prepared"
            candidate["updated_at"] = now
            candidate["terminal_reason"] = None
            candidate["events"].append({
                "sequence": len(candidate["events"]) + 1,
                "at": now,
                "state": "prepared",
                "reason": "retry_prepared",
            })
            candidate = self._validate(candidate)
            self._write_atomic(self._path(analysis_id), candidate, backup=True)
            return deepcopy(candidate)

    def record_artifact(
        self,
        analysis_id: str,
        descriptor: Mapping[str, Any],
        *,
        pinned: bool = False,
        cleanup_eligible: bool = False,
    ) -> dict[str, Any]:
        artifact = deepcopy(dict(descriptor))
        digest = str(artifact.get("sha256") or "").lower()
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise AnalysisPersistenceError("Artifact sha256 must be a digest")
        artifact["sha256"] = digest
        artifact["pinned"] = bool(pinned)
        artifact["cleanup_eligible"] = bool(cleanup_eligible)
        artifact["tombstoned_at"] = None
        with self._writer():
            current = self.load(analysis_id)
            if any(item.get("sha256") == digest for item in current["artifacts"]):
                return current
            candidate = deepcopy(current)
            candidate["artifacts"].append(artifact)
            self._append_metadata_event(candidate, "artifact_admitted")
            candidate = self._validate(candidate)
            self._write_atomic(self._path(analysis_id), candidate, backup=True)
            return deepcopy(candidate)

    def record_publication_evidence(
        self,
        analysis_id: str,
        *,
        intent: Mapping[str, Any],
        authorization: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Persist bounded publication intent/authorization before terminal receipt."""

        try:
            encoded_intent = json.dumps(
                dict(intent), sort_keys=True, separators=(",", ":"), allow_nan=False
            )
            encoded_authorization = json.dumps(
                dict(authorization),
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise AnalysisPersistenceError(
                "Publication evidence must be bounded JSON objects"
            ) from exc
        if (
            len(encoded_intent.encode("utf-8")) > MAX_PUBLICATION_EVIDENCE_BYTES
            or len(encoded_authorization.encode("utf-8"))
            > MAX_PUBLICATION_EVIDENCE_BYTES
        ):
            raise AnalysisPersistenceError("Publication evidence exceeds its bound")
        clean_intent = json.loads(encoded_intent)
        clean_authorization = json.loads(encoded_authorization)
        with self._writer():
            current = self.load(analysis_id)
            if current["state"] != "publishing":
                raise AnalysisPersistenceError(
                    "Publication evidence requires the publishing state"
                )
            publication = deepcopy(current["publication"])
            if publication["receipt"] is not None:
                raise AnalysisPersistenceError(
                    "Published Analysis evidence cannot be rewritten"
                )
            if (
                publication["intent"] is not None
                or publication["authorization"] is not None
            ):
                if (
                    publication["intent"] == clean_intent
                    and publication["authorization"] == clean_authorization
                ):
                    return current
                raise AnalysisPersistenceError(
                    "Publication intent or authorization cannot change"
                )
            publication["intent"] = clean_intent
            publication["authorization"] = clean_authorization
            candidate = deepcopy(current)
            candidate["publication"] = publication
            self._append_metadata_event(candidate, "publication_evidence_recorded")
            candidate = self._validate(candidate)
            self._write_atomic(self._path(analysis_id), candidate, backup=True)
            return deepcopy(candidate)

    def tombstone_artifact(self, analysis_id: str, sha256: str) -> dict[str, Any]:
        digest = str(sha256 or "").lower()
        with self._writer():
            current = self.load(analysis_id)
            candidate = deepcopy(current)
            match = next(
                (item for item in candidate["artifacts"] if item.get("sha256") == digest),
                None,
            )
            if match is None:
                raise AnalysisPersistenceError("Artifact identity is unknown")
            if match.get("pinned") or not match.get("cleanup_eligible"):
                raise AnalysisPersistenceError("Artifact is retained as engineering evidence")
            if match.get("tombstoned_at"):
                return current
            match["tombstoned_at"] = _utc_now()
            self._append_metadata_event(candidate, "artifact_tombstoned")
            candidate = self._validate(candidate)
            self._write_atomic(self._path(analysis_id), candidate, backup=True)
            return deepcopy(candidate)

    @staticmethod
    def _append_metadata_event(record: dict[str, Any], reason: str) -> None:
        now = _utc_now()
        record["updated_at"] = now
        record["events"].append({
            "sequence": len(record["events"]) + 1,
            "at": now,
            "state": record["state"],
            "reason": reason,
        })

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


class DurableRuntimeLifecycle:
    """Explicit opt-in bridge from in-memory orchestration to durable metadata."""

    def __init__(
        self,
        store: AnalysisMetadataStore,
        *,
        domain: str,
        adapter_id: str,
        prepared_analysis_sha256: str,
        dependency_sha256: str,
        input_manifest_sha256: str,
        execution_spec_sha256: str,
        provider_id: str = "local-process",
        provider_kind: str = "local",
        provider_job_id: str = "",
    ) -> None:
        self.store = store
        self.identity = {
            "domain": domain,
            "adapter_id": adapter_id,
            "prepared_analysis_sha256": prepared_analysis_sha256,
            "dependency_sha256": dependency_sha256,
            "input_manifest_sha256": input_manifest_sha256,
            "execution_spec_sha256": execution_spec_sha256,
        }
        self.provider_id = provider_id
        self.provider_kind = provider_kind
        self.provider_job_id = provider_job_id
        self.analysis_id = ""

    def submitted(self, job_id: str, document_uid: str, _capability_name: str) -> None:
        self.analysis_id = _clean_id(job_id, "job_id")
        self.store.create(new_job_record(
            analysis_id=self.analysis_id,
            source_document_uid=document_uid,
            **self.identity,
        ))

    def started(self) -> None:
        self.store.begin_attempt(
            self.analysis_id,
            provider_id=self.provider_id,
            provider_kind=self.provider_kind,
            provider_job_id=self.provider_job_id,
        )

    def prepared(self) -> None:
        for state, reason in (
            ("collecting", "provider_completed"),
            ("verifying", "outputs_collected"),
            ("waiting_to_publish", "outputs_verified"),
        ):
            self.store.transition(self.analysis_id, state, reason=reason)

    def publication_started(self) -> None:
        self.store.transition(
            self.analysis_id, "publishing", reason="legacy_inline_publication_started"
        )

    def succeeded(self, result_sha256: str) -> None:
        receipt = {
            "publication_id": f"legacy-inline-{self.analysis_id}",
            "analysis_id": self.analysis_id,
            "result_sha256": str(result_sha256),
            "compatibility_mode": "legacy_inline_publication",
        }
        current = self.store.load(self.analysis_id)
        publication = deepcopy(current["publication"])
        publication["receipt"] = receipt
        self.store.transition(
            self.analysis_id,
            "succeeded",
            reason="legacy_inline_published",
            updates={"publication": publication},
        )

    def failed(self, reason: str) -> None:
        current = self.store.load(self.analysis_id)
        if current["state"] == "publishing":
            return
        if current["state"] not in TERMINAL_STATES:
            self.store.transition(self.analysis_id, "failed", reason=reason)

    def cancelled(self) -> None:
        current = self.store.load(self.analysis_id)
        if current["state"] not in TERMINAL_STATES:
            self.store.transition(
                self.analysis_id, "cancelled", reason="cancelled_before_publication"
            )
