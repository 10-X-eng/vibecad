# SPDX-License-Identifier: LGPL-2.1-or-later

"""Versioned transactional metadata for durable Analysis job identity."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
from typing import Any, Callable, Iterator, Mapping


ANALYSIS_METADATA_SCHEMA_VERSION = 2
SUPPORTED_ANALYSIS_METADATA_MIGRATIONS = frozenset({(1, 2)})
MAX_PUBLICATION_EVIDENCE_BYTES = 64 * 1024
MAX_VERIFICATION_EVIDENCE_BYTES = 1024 * 1024
VERIFIED_PUBLICATION_INTENT_VERSION = "vibecad-analysis-publication-intent-v1"
VERIFIED_PUBLICATION_AUTHORIZATION_VERSION = (
    "vibecad-analysis-publication-authorization-v1"
)
VERIFIED_PUBLICATION_RECEIPT_VERSION = "vibecad-analysis-publication-receipt-v1"
MAX_DISCOVERABLE_ANALYSES = 4096
DEFAULT_MAXIMUM_ARTIFACTS_PER_ANALYSIS = 4096
DEFAULT_MAXIMUM_ARTIFACT_BYTES_PER_ANALYSIS = 4 * 1024 * 1024 * 1024
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


def _positive_integer(value: int, field: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    try:
        encoded = json.dumps(
            dict(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise AnalysisPersistenceError(
            "Publication evidence must be canonical JSON"
        ) from exc
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _artifact_references(record: Mapping[str, Any]) -> tuple[str, ...]:
    publication = record.get("publication")
    if not isinstance(publication, Mapping):
        raise AnalysisPersistenceError("Analysis publication metadata must be an object")
    intent = publication.get("intent")
    if intent is None:
        return ()
    if not isinstance(intent, Mapping):
        raise AnalysisPersistenceError("Publication intent must be an object")
    references = intent.get("artifact_references")
    if references is None:
        return ()
    if not isinstance(references, list):
        raise AnalysisPersistenceError("Publication artifact references must be a list")
    clean: list[str] = []
    for value in references:
        digest = str(value or "")
        if (
            value != digest
            or digest != digest.lower()
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise AnalysisPersistenceError(
                "Publication artifact references must be SHA-256 digests"
            )
        clean.append(digest)
    if len(clean) != len(set(clean)):
        raise AnalysisPersistenceError("Publication artifact references must be unique")
    return tuple(clean)


def _validate_verified_publication(record: Mapping[str, Any]) -> None:
    publication = record.get("publication")
    if not isinstance(publication, Mapping):
        raise AnalysisPersistenceError("Analysis publication metadata must be an object")
    intent = publication.get("intent")
    if not isinstance(intent, Mapping) or intent.get("schema_version") != (
        VERIFIED_PUBLICATION_INTENT_VERSION
    ):
        return
    if set(intent) != {
        "schema_version",
        "publication_descriptor",
        "publication_descriptor_sha256",
        "verification_receipt_sha256",
        "artifact_references",
        "currentness",
    }:
        raise AnalysisPersistenceError("Verified publication intent is invalid")
    if record.get("state") not in {"publishing", "succeeded"}:
        raise AnalysisPersistenceError(
            "Verified publication evidence is in an invalid lifecycle state"
        )
    descriptor = intent.get("publication_descriptor")
    if not isinstance(descriptor, Mapping) or set(descriptor) != {
        "publication_id",
        "analysis_id",
        "attempt",
        "domain_id",
        "adapter_id",
        "adapter_version",
        "source_document_uid",
        "frozen_dependency_sha256",
        "output_manifest_sha256",
        "provider_attempt_identity",
        "result_identity",
        "result_sha256",
    }:
        raise AnalysisPersistenceError("Verified publication descriptor is invalid")
    descriptor_sha256 = _canonical_sha256(descriptor)
    attempt = descriptor.get("attempt")
    verification_receipts = record.get("verification_receipts", [])
    verification = next(
        (
            item
            for item in verification_receipts
            if isinstance(item, Mapping) and item.get("attempt") == attempt
        ),
        None,
    )
    artifact_references = intent.get("artifact_references")
    currentness = intent.get("currentness")
    if (
        type(attempt) is not int
        or attempt < 1
        or attempt != len(record.get("attempts", []))
        or verification is None
        or intent.get("publication_descriptor_sha256") != descriptor_sha256
        or intent.get("verification_receipt_sha256")
        != _canonical_sha256(verification)
        or descriptor.get("analysis_id") != record.get("analysis_id")
        or descriptor.get("domain_id") != record.get("domain")
        or descriptor.get("adapter_id") != record.get("adapter_id")
        or descriptor.get("source_document_uid")
        != record.get("source_document_uid")
        or descriptor.get("frozen_dependency_sha256")
        != record.get("dependency_sha256")
        or descriptor.get("provider_attempt_identity")
        != verification.get("provider_attempt_identity")
        or descriptor.get("output_manifest_sha256")
        != verification.get("output_manifest_sha256")
        or descriptor.get("result_identity")
        != verification.get("result_identity")
        or descriptor.get("result_sha256") != verification.get("result_sha256")
        or artifact_references != verification.get("artifact_sha256")
        or currentness
        != {
            "current": True,
            "source_resolved": True,
            "changed_dependencies": [],
            "ambiguous_dependencies": [],
        }
        or not str(descriptor.get("publication_id") or "").strip()
        or not str(descriptor.get("adapter_version") or "").strip()
    ):
        raise AnalysisPersistenceError("Verified publication intent is invalid")
    authorization = publication.get("authorization")
    if not isinstance(authorization, Mapping) or set(authorization) != {
        "schema_version",
        "publication_id",
        "publication_descriptor_sha256",
        "authorization_id",
        "authorized_at",
    }:
        raise AnalysisPersistenceError(
            "Verified publication authorization is invalid"
        )
    if (
        authorization.get("schema_version")
        != VERIFIED_PUBLICATION_AUTHORIZATION_VERSION
        or authorization.get("publication_id") != descriptor.get("publication_id")
        or authorization.get("publication_descriptor_sha256")
        != descriptor_sha256
        or not str(authorization.get("authorization_id") or "").strip()
        or not str(authorization.get("authorized_at") or "").strip()
    ):
        raise AnalysisPersistenceError(
            "Verified publication authorization is invalid"
        )
    for evidence in (intent, authorization):
        try:
            encoded_evidence = json.dumps(
                dict(evidence),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise AnalysisPersistenceError(
                "Verified publication evidence is invalid"
            ) from exc
        if len(encoded_evidence.encode("utf-8")) > MAX_PUBLICATION_EVIDENCE_BYTES:
            raise AnalysisPersistenceError(
                "Verified publication evidence exceeds its bound"
            )
    receipt = publication.get("receipt")
    if receipt is None:
        return
    if not isinstance(receipt, Mapping) or set(receipt) != {
        "schema_version",
        "publication_id",
        "publication_descriptor_sha256",
        "analysis_id",
        "attempt",
        "provider_attempt_identity",
        "output_manifest_sha256",
        "result_identity",
        "result_sha256",
        "artifact_sha256",
        "authorization_id",
        "published_at",
        "mutation_result",
    }:
        raise AnalysisPersistenceError("Verified publication receipt is invalid")
    if (
        receipt.get("schema_version") != VERIFIED_PUBLICATION_RECEIPT_VERSION
        or receipt.get("publication_id") != descriptor.get("publication_id")
        or receipt.get("publication_descriptor_sha256") != descriptor_sha256
        or receipt.get("analysis_id") != record.get("analysis_id")
        or receipt.get("attempt") != attempt
        or receipt.get("provider_attempt_identity")
        != descriptor.get("provider_attempt_identity")
        or receipt.get("output_manifest_sha256")
        != descriptor.get("output_manifest_sha256")
        or receipt.get("result_identity") != descriptor.get("result_identity")
        or receipt.get("result_sha256") != descriptor.get("result_sha256")
        or receipt.get("artifact_sha256") != artifact_references
        or receipt.get("authorization_id")
        != authorization.get("authorization_id")
        or not str(receipt.get("published_at") or "").strip()
        or not isinstance(receipt.get("mutation_result"), Mapping)
        or record.get("state") not in {"publishing", "succeeded"}
    ):
        raise AnalysisPersistenceError("Verified publication receipt is invalid")


def _provider_recovery_snapshot(
    value: Mapping[str, Any] | None,
) -> dict[str, bool]:
    fields = {"reconnect_supported", "job_survives_client_exit"}
    if value is None:
        return {field: False for field in sorted(fields)}
    if not isinstance(value, Mapping) or set(value) != fields:
        raise AnalysisPersistenceError(
            "Provider capability snapshot must contain only recovery capabilities"
        )
    if any(type(value[field]) is not bool for field in fields):
        raise AnalysisPersistenceError(
            "Provider capability snapshot values must be booleans"
        )
    return {field: value[field] for field in sorted(fields)}


def analysis_provider_attempt_identity(
    *,
    analysis_id: str,
    attempt: int,
    provider_id: str,
    provider_job_id: str,
    output_manifest_sha256: str,
) -> str:
    """Return a path-free identity for one exact collected provider attempt."""

    clean_analysis_id = _clean_id(analysis_id, "analysis_id")
    if type(attempt) is not int or attempt < 1:
        raise AnalysisPersistenceError("attempt must be a positive integer")
    clean_provider_id = str(provider_id or "").strip()
    clean_job_id = str(provider_job_id or "").strip()
    digest = str(output_manifest_sha256 or "").lower()
    if not clean_provider_id or not clean_job_id:
        raise AnalysisPersistenceError(
            "Provider attempt identity requires provider and remote job identities"
        )
    if (
        len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise AnalysisPersistenceError(
            "Provider attempt identity requires an output manifest digest"
        )
    encoded = json.dumps(
        {
            "analysis_id": clean_analysis_id,
            "attempt": attempt,
            "output_manifest_sha256": digest,
            "provider_id": clean_provider_id,
            "provider_job_id": clean_job_id,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def new_job_record(
    *, analysis_id: str, domain: str, adapter_id: str, source_document_uid: str,
    prepared_analysis_sha256: str, dependency_sha256: str,
    input_manifest_sha256: str, execution_spec_sha256: str,
) -> dict[str, Any]:
    now = _utc_now()
    record = {
        "schema_version": ANALYSIS_METADATA_SCHEMA_VERSION,
        "schema_migrations": [],
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
        "verification_receipts": [],
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


def restart_disposition_for_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Classify one already-read durable snapshot without reopening its file."""

    if not isinstance(record, Mapping):
        raise AnalysisPersistenceError("Analysis metadata must be an object")
    analysis_id = _clean_id(record.get("analysis_id"), "analysis_id")
    state = record.get("state")
    if state not in KNOWN_STATES:
        raise AnalysisPersistenceError("Unknown Analysis lifecycle state")
    attempts = record.get("attempts")
    if not isinstance(attempts, list):
        raise AnalysisPersistenceError("Analysis attempts must be a list")
    latest = attempts[-1] if attempts and isinstance(attempts[-1], Mapping) else {}
    snapshot = latest.get("provider_capability_snapshot")
    snapshot = snapshot if isinstance(snapshot, Mapping) else {}
    reconnectable = (
        state == "running_remote"
        and latest.get("provider_kind") == "remote"
        and bool(str(latest.get("provider_job_id") or "").strip())
        and snapshot.get("reconnect_supported") is True
        and snapshot.get("job_survives_client_exit") is True
    )
    details: dict[str, Any] = {}
    if state in TERMINAL_STATES:
        action = "terminal"
        reason = "terminal_record"
    elif reconnectable:
        action = "reconnect_remote"
        reason = "persisted_provider_reconnect_evidence"
        details = {
            "attempt": latest.get("attempt"),
            "provider_id": latest.get("provider_id"),
            "provider_job_id": latest.get("provider_job_id"),
        }
    elif state in {"prepared", "running_local"}:
        action = "mark_interrupted"
        reason = "host_runtime_not_reattachable"
    elif state == "running_remote":
        action = "mark_interrupted"
        reason = "provider_reconnect_not_proven"
    elif state == "publishing":
        publication = record.get("publication")
        receipt = (
            publication.get("receipt")
            if isinstance(publication, Mapping)
            else None
        )
        if (
            isinstance(receipt, Mapping)
            and receipt.get("schema_version")
            == VERIFIED_PUBLICATION_RECEIPT_VERSION
        ):
            action = "finalize_publication_receipt"
            reason = "durable_publication_receipt_requires_terminal_transition"
        else:
            action = "publication_outcome_unknown"
            reason = "publication_receipt_requires_reconciliation"
    else:
        action = f"resume_{state}"
        reason = "durable_phase_requires_reconciliation"
    return {
        "analysis_id": analysis_id,
        "state": state,
        "action": action,
        "reason": reason,
        **details,
    }


class AnalysisMetadataStore:
    """One-writer JSON store with atomic replace, backup, and fault points."""

    def __init__(
        self,
        root: str | Path,
        *,
        fault_injector: FaultInjector | None = None,
        maximum_artifacts_per_analysis: int = DEFAULT_MAXIMUM_ARTIFACTS_PER_ANALYSIS,
        maximum_artifact_bytes_per_analysis: int = DEFAULT_MAXIMUM_ARTIFACT_BYTES_PER_ANALYSIS,
    ) -> None:
        self.root = Path(root)
        self.records = self.root / "records"
        self.backups = self.root / "backups"
        self.lock_path = self.root / "writer.lock"
        if fault_injector is not None and not callable(fault_injector):
            raise TypeError("fault_injector must be callable")
        self.fault_injector = fault_injector
        self.maximum_artifacts_per_analysis = _positive_integer(
            maximum_artifacts_per_analysis, "maximum_artifacts_per_analysis"
        )
        self.maximum_artifact_bytes_per_analysis = _positive_integer(
            maximum_artifact_bytes_per_analysis,
            "maximum_artifact_bytes_per_analysis",
        )

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

    def migrate_record(self, analysis_id: str) -> dict[str, Any]:
        """Atomically migrate one supported legacy record under write authority."""

        path = self._path(analysis_id)
        with self._writer():
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise AnalysisPersistenceError(
                    "Analysis metadata is missing or corrupt"
                ) from exc
            if not isinstance(value, Mapping):
                raise AnalysisPersistenceError("Analysis metadata must be an object")
            stored_analysis_id = _clean_id(value.get("analysis_id"), "analysis_id")
            if stored_analysis_id != _clean_id(analysis_id, "analysis_id"):
                raise AnalysisPersistenceError(
                    "Analysis metadata filename does not match its identity"
                )
            version = value.get("schema_version")
            if version == ANALYSIS_METADATA_SCHEMA_VERSION:
                return self._validate(value)
            if (version, ANALYSIS_METADATA_SCHEMA_VERSION) not in (
                SUPPORTED_ANALYSIS_METADATA_MIGRATIONS
            ):
                raise AnalysisPersistenceError(
                    "Analysis metadata has no supported migration to the current schema"
                )
            if version == 1:
                if "schema_migrations" in value:
                    raise AnalysisPersistenceError(
                        "Legacy Analysis metadata has an invalid migration history"
                    )
                candidate = deepcopy(dict(value))
                candidate["schema_version"] = ANALYSIS_METADATA_SCHEMA_VERSION
                candidate["schema_migrations"] = [{
                    "from_version": 1,
                    "to_version": ANALYSIS_METADATA_SCHEMA_VERSION,
                    "at": _utc_now(),
                }]
            else:  # pragma: no cover - registry and branch remain intentionally paired.
                raise AnalysisPersistenceError(
                    "Analysis metadata migration is not implemented"
                )
            candidate = self._validate(candidate)
            self._write_atomic(path, candidate, backup=True)
            return deepcopy(candidate)

    def migrate_records(self) -> tuple[dict[str, Any], ...]:
        """Migrate every supported per-root record, atomically one record at a time."""

        if not self.records.exists():
            return ()
        paths = tuple(sorted(self.records.glob("*.json"), key=lambda path: path.name))
        if len(paths) > MAX_DISCOVERABLE_ANALYSES:
            raise AnalysisPersistenceError(
                "Analysis metadata discovery exceeds its bounded record limit"
            )
        migrated = []
        for path in paths:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise AnalysisPersistenceError(
                    f"Analysis metadata migration found an invalid record: {path.name}"
                ) from exc
            if not isinstance(value, Mapping):
                raise AnalysisPersistenceError(
                    f"Analysis metadata migration found an invalid record: {path.name}"
                )
            analysis_id = _clean_id(value.get("analysis_id"), "analysis_id")
            if path.stem != analysis_id:
                raise AnalysisPersistenceError(
                    f"Analysis metadata filename does not match its identity: {path.name}"
                )
            if value.get("schema_version") == ANALYSIS_METADATA_SCHEMA_VERSION:
                self._validate(value)
                continue
            migrated.append(self.migrate_record(analysis_id))
        return tuple(migrated)

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
        expected_state: str | None = None,
    ) -> dict[str, Any]:
        clean_state = str(state or "").strip()
        if clean_state not in KNOWN_STATES:
            raise AnalysisPersistenceError("Unknown Analysis lifecycle state")
        with self._writer():
            current = self.load(analysis_id)
            if expected_state is not None and current["state"] != expected_state:
                raise AnalysisPersistenceError(
                    "Analysis state changed before the requested transition"
                )
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

    def restart_disposition(self, analysis_id: str) -> dict[str, Any]:
        return restart_disposition_for_record(self.load(analysis_id))

    def interrupt_unrecoverable_after_restart(
        self, analysis_id: str,
    ) -> dict[str, Any]:
        """Persist a truthful host-interrupted outcome for an orphaned runtime."""

        current = self.load(analysis_id)
        recovery_events = current.get("recovery_events", [])
        if (
            current["state"] == "interrupted"
            and isinstance(recovery_events, list)
            and recovery_events
            and recovery_events[-1].get("failure_kind") == "host_interrupted"
        ):
            return current
        disposition = restart_disposition_for_record(current)
        if disposition["action"] != "mark_interrupted":
            raise AnalysisPersistenceError(
                "Analysis restart disposition is not unrecoverable"
            )
        attempts = deepcopy(current["attempts"])
        if attempts:
            attempts[-1]["terminal_reason"] = "host_interrupted"
        recovery_event = {
            "classified_at": _utc_now(),
            "previous_state": current["state"],
            "disposition": "orphaned",
            "failure_kind": "host_interrupted",
            "attempt": attempts[-1]["attempt"] if attempts else None,
        }
        return self.transition(
            analysis_id,
            "interrupted",
            reason="host_interrupted",
            updates={
                "attempts": attempts,
                "recovery_events": [*recovery_events, recovery_event],
            },
            expected_state=current["state"],
        )

    def interrupt_missing_provider_job_after_restart(
        self, analysis_id: str,
    ) -> dict[str, Any]:
        """Record that an authorized reconnect found no surviving remote job."""

        current = self.load(analysis_id)
        disposition = restart_disposition_for_record(current)
        if disposition["action"] != "reconnect_remote":
            raise AnalysisPersistenceError(
                "Analysis restart disposition does not authorize reconnect"
            )
        attempts = deepcopy(current["attempts"])
        attempts[-1]["terminal_reason"] = "provider_job_not_found"
        recovery_events = current.get("recovery_events", [])
        recovery_event = {
            "classified_at": _utc_now(),
            "previous_state": current["state"],
            "disposition": "orphaned",
            "failure_kind": "host_interrupted",
            "attempt": attempts[-1]["attempt"],
        }
        return self.transition(
            analysis_id,
            "interrupted",
            reason="provider_job_not_found",
            updates={
                "attempts": attempts,
                "recovery_events": [*recovery_events, recovery_event],
            },
            expected_state=current["state"],
        )

    def begin_attempt(
        self,
        analysis_id: str,
        *,
        provider_id: str,
        provider_kind: str,
        provider_job_id: str = "",
        provider_capability_snapshot: Mapping[str, Any] | None = None,
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
            "provider_capability_snapshot": _provider_recovery_snapshot(
                provider_capability_snapshot
            ),
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
            expected_state=record["state"],
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
        expected_state: str | None = None,
    ) -> dict[str, Any]:
        artifact = deepcopy(dict(descriptor))
        digest = str(artifact.get("sha256") or "").lower()
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise AnalysisPersistenceError("Artifact sha256 must be a digest")
        byte_count = artifact.get("byte_count")
        if byte_count is not None and (type(byte_count) is not int or byte_count < 0):
            raise AnalysisPersistenceError(
                "Artifact byte_count must be a non-negative integer"
            )
        quota_bytes = 0 if byte_count is None else byte_count
        artifact["sha256"] = digest
        artifact["pinned"] = bool(pinned)
        artifact["cleanup_eligible"] = bool(cleanup_eligible)
        artifact["tombstoned_at"] = None
        with self._writer():
            current = self.load(analysis_id)
            if expected_state is not None and current["state"] != expected_state:
                raise AnalysisPersistenceError(
                    "Analysis state changed before artifact admission"
                )
            existing = next(
                (item for item in current["artifacts"] if item.get("sha256") == digest),
                None,
            )
            if existing is not None:
                if existing == artifact:
                    return current
                raise AnalysisPersistenceError(
                    "Artifact identity cannot be reused with different metadata"
                )
            active = [
                item for item in current["artifacts"] if not item.get("tombstoned_at")
            ]
            active_bytes = 0
            for item in active:
                existing_bytes = item.get("byte_count")
                if existing_bytes is not None and (
                    type(existing_bytes) is not int or existing_bytes < 0
                ):
                    raise AnalysisPersistenceError(
                        "Existing artifact metadata has an invalid byte count"
                    )
                active_bytes += 0 if existing_bytes is None else existing_bytes
            if active_bytes + quota_bytes > self.maximum_artifact_bytes_per_analysis:
                raise AnalysisPersistenceError("Analysis artifact byte quota exceeded")
            if len(active) + 1 > self.maximum_artifacts_per_analysis:
                raise AnalysisPersistenceError("Analysis artifact count quota exceeded")
            candidate = deepcopy(current)
            candidate["artifacts"].append(artifact)
            self._append_metadata_event(candidate, "artifact_admitted")
            candidate = self._validate(candidate)
            self._write_atomic(self._path(analysis_id), candidate, backup=True)
            return deepcopy(candidate)

    def protected_artifact_sha256(self, analysis_id: str) -> tuple[str, ...]:
        """Return exact live artifact identities that cleanup must retain."""

        record = self.load(analysis_id)
        referenced = set(_artifact_references(record))
        protected = {
            item["sha256"]
            for item in record["artifacts"]
            if not item.get("tombstoned_at")
            and (
                item.get("pinned")
                or not item.get("cleanup_eligible")
                or item["sha256"] in referenced
            )
        }
        return tuple(sorted(protected))

    def record_verification_receipt(
        self,
        analysis_id: str,
        receipt: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Persist one bounded, write-once domain verification receipt."""

        try:
            encoded = json.dumps(
                dict(receipt),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise AnalysisPersistenceError(
                "Verification receipt must be a bounded JSON object"
            ) from exc
        if len(encoded.encode("utf-8")) > MAX_VERIFICATION_EVIDENCE_BYTES:
            raise AnalysisPersistenceError("Verification receipt exceeds its bound")
        clean_receipt = json.loads(encoded)
        attempt = clean_receipt.get("attempt")
        with self._writer():
            current = self.load(analysis_id)
            if current["state"] != "verifying":
                raise AnalysisPersistenceError(
                    "Verification receipt requires the verifying state"
                )
            receipts = deepcopy(current.get("verification_receipts", []))
            existing = next(
                (
                    item
                    for item in receipts
                    if isinstance(item, Mapping) and item.get("attempt") == attempt
                ),
                None,
            )
            if existing is not None:
                if existing == clean_receipt:
                    return current
                raise AnalysisPersistenceError(
                    "Domain verification evidence cannot be rewritten"
                )
            candidate = deepcopy(current)
            candidate["verification_receipts"] = [*receipts, clean_receipt]
            self._append_metadata_event(candidate, "domain_verification_recorded")
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

    def record_publication_receipt(
        self,
        analysis_id: str,
        receipt: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Persist one bounded, write-once verified-publication receipt."""

        try:
            encoded = json.dumps(
                dict(receipt),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise AnalysisPersistenceError(
                "Publication receipt must be a bounded JSON object"
            ) from exc
        if len(encoded.encode("utf-8")) > MAX_PUBLICATION_EVIDENCE_BYTES:
            raise AnalysisPersistenceError("Publication receipt exceeds its bound")
        clean_receipt = json.loads(encoded)
        with self._writer():
            current = self.load(analysis_id)
            publication = deepcopy(current["publication"])
            intent = publication.get("intent")
            authorization = publication.get("authorization")
            if (
                clean_receipt.get("schema_version")
                != VERIFIED_PUBLICATION_RECEIPT_VERSION
                or not isinstance(intent, Mapping)
                or intent.get("schema_version")
                != VERIFIED_PUBLICATION_INTENT_VERSION
                or not isinstance(authorization, Mapping)
                or authorization.get("schema_version")
                != VERIFIED_PUBLICATION_AUTHORIZATION_VERSION
            ):
                raise AnalysisPersistenceError(
                    "Verified publication receipt requires exact durable evidence"
                )
            existing = publication.get("receipt")
            if existing is not None:
                if existing == clean_receipt:
                    return current
                raise AnalysisPersistenceError(
                    "Published Analysis evidence cannot be rewritten"
                )
            if current["state"] != "publishing":
                raise AnalysisPersistenceError(
                    "Publication receipt requires the publishing state"
                )
            publication["receipt"] = clean_receipt
            candidate = deepcopy(current)
            candidate["publication"] = publication
            self._append_metadata_event(candidate, "publication_receipt_recorded")
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
            if digest in set(_artifact_references(current)):
                raise AnalysisPersistenceError(
                    "Artifact is retained by publication evidence"
                )
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
        version = record.get("schema_version")
        if version != ANALYSIS_METADATA_SCHEMA_VERSION:
            if (version, ANALYSIS_METADATA_SCHEMA_VERSION) in (
                SUPPORTED_ANALYSIS_METADATA_MIGRATIONS
            ):
                raise AnalysisPersistenceError(
                    "Analysis metadata requires migration before use"
                )
            raise AnalysisPersistenceError("Unsupported Analysis metadata schema version")
        migrations = record.get("schema_migrations")
        if not isinstance(migrations, list):
            raise AnalysisPersistenceError(
                "Analysis schema migration history must be a list"
            )
        previous_to = None
        for migration in migrations:
            if not isinstance(migration, Mapping) or set(migration) != {
                "from_version", "to_version", "at",
            }:
                raise AnalysisPersistenceError(
                    "Analysis schema migration history is invalid"
                )
            from_version = migration.get("from_version")
            to_version = migration.get("to_version")
            if (
                type(from_version) is not int
                or type(to_version) is not int
                or (previous_to is not None and from_version != previous_to)
                or to_version != from_version + 1
                or (from_version, to_version)
                not in SUPPORTED_ANALYSIS_METADATA_MIGRATIONS
                or not str(migration.get("at") or "").strip()
            ):
                raise AnalysisPersistenceError(
                    "Analysis schema migration history is not monotonic"
                )
            previous_to = to_version
        if migrations and previous_to != ANALYSIS_METADATA_SCHEMA_VERSION:
            raise AnalysisPersistenceError(
                "Analysis schema migration history does not reach the current version"
            )
        _clean_id(record.get("analysis_id"), "analysis_id")
        if record.get("state") not in KNOWN_STATES:
            raise AnalysisPersistenceError("Unknown Analysis lifecycle state")
        events = record.get("events")
        if not isinstance(events, list) or not events:
            raise AnalysisPersistenceError("Analysis events must be non-empty")
        if [item.get("sequence") for item in events] != list(range(1, len(events) + 1)):
            raise AnalysisPersistenceError("Analysis event sequence is not monotonic")
        attempts = record.get("attempts")
        if not isinstance(attempts, list):
            raise AnalysisPersistenceError("Analysis attempts must be a list")
        required_attempt_fields = {
            "attempt", "provider_id", "provider_kind", "provider_job_id",
            "started_at", "terminal_reason",
        }
        for expected_attempt, attempt in enumerate(attempts, start=1):
            if not isinstance(attempt, Mapping):
                raise AnalysisPersistenceError("Analysis attempt state is invalid")
            fields = set(attempt)
            if fields not in (
                required_attempt_fields,
                required_attempt_fields | {"provider_capability_snapshot"},
            ):
                raise AnalysisPersistenceError("Analysis attempt state is invalid")
            if (
                attempt.get("attempt") != expected_attempt
                or not str(attempt.get("provider_id") or "").strip()
                or attempt.get("provider_kind") not in {"local", "remote"}
                or not isinstance(attempt.get("provider_job_id"), str)
                or not str(attempt.get("started_at") or "").strip()
                or (
                    attempt.get("terminal_reason") is not None
                    and not isinstance(attempt.get("terminal_reason"), str)
                )
            ):
                raise AnalysisPersistenceError("Analysis attempt state is invalid")
            snapshot = attempt.get("provider_capability_snapshot")
            if snapshot is not None:
                _provider_recovery_snapshot(snapshot)
        recovery_events = record.get("recovery_events", [])
        if not isinstance(recovery_events, list):
            raise AnalysisPersistenceError("Analysis recovery evidence is invalid")
        for recovery in recovery_events:
            if not isinstance(recovery, Mapping) or set(recovery) != {
                "classified_at", "previous_state", "disposition", "failure_kind",
                "attempt",
            }:
                raise AnalysisPersistenceError("Analysis recovery evidence is invalid")
            previous_state = recovery.get("previous_state")
            attempt_number = recovery.get("attempt")
            if (
                not str(recovery.get("classified_at") or "").strip()
                or previous_state not in KNOWN_STATES - TERMINAL_STATES
                or recovery.get("disposition") != "orphaned"
                or recovery.get("failure_kind") != "host_interrupted"
                or (
                    attempt_number is None
                    and previous_state != "prepared"
                )
                or (
                    attempt_number is not None
                    and (
                        type(attempt_number) is not int
                        or attempt_number < 1
                        or attempt_number > len(attempts)
                    )
                )
            ):
                raise AnalysisPersistenceError("Analysis recovery evidence is invalid")
        collection_receipts = record.get("provider_collection_receipts", [])
        if not isinstance(collection_receipts, list):
            raise AnalysisPersistenceError(
                "Analysis provider collection evidence is invalid"
            )
        if collection_receipts and record.get("state") in {
            "prepared", "running_local", "running_remote",
        }:
            raise AnalysisPersistenceError(
                "Analysis provider collection evidence is invalid"
            )
        if collection_receipts and not any(
            event.get("state") == "collecting" for event in events
        ):
            raise AnalysisPersistenceError(
                "Analysis provider collection evidence is invalid"
            )
        collected_attempts: set[int] = set()
        for receipt in collection_receipts:
            if not isinstance(receipt, Mapping) or set(receipt) != {
                "collected_at", "attempt", "provider_id", "provider_job_id",
                "output_manifest_sha256",
            }:
                raise AnalysisPersistenceError(
                    "Analysis provider collection evidence is invalid"
                )
            attempt_number = receipt.get("attempt")
            digest = str(receipt.get("output_manifest_sha256") or "")
            if (
                type(attempt_number) is not int
                or attempt_number < 1
                or attempt_number > len(attempts)
                or attempt_number in collected_attempts
                or not str(receipt.get("collected_at") or "").strip()
                or attempts[attempt_number - 1]["provider_kind"] != "remote"
                or not attempts[attempt_number - 1]["provider_job_id"]
                or receipt.get("provider_id") != attempts[attempt_number - 1]["provider_id"]
                or receipt.get("provider_job_id")
                != attempts[attempt_number - 1]["provider_job_id"]
                or digest != digest.lower()
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise AnalysisPersistenceError(
                    "Analysis provider collection evidence is invalid"
                )
            collected_attempts.add(attempt_number)
        verification_receipts = record.get("verification_receipts", [])
        if not isinstance(verification_receipts, list):
            raise AnalysisPersistenceError(
                "Analysis domain verification evidence is invalid"
            )
        if verification_receipts and not any(
            event.get("state") == "verifying" for event in events
        ):
            raise AnalysisPersistenceError(
                "Analysis domain verification evidence is invalid"
            )
        if verification_receipts and record.get("state") in {
            "prepared", "running_local", "running_remote", "collecting",
        }:
            raise AnalysisPersistenceError(
                "Analysis domain verification evidence is invalid"
            )
        verified_attempts: set[int] = set()
        for receipt in verification_receipts:
            if not isinstance(receipt, Mapping) or set(receipt) != {
                "verified_at", "analysis_id", "attempt",
                "provider_attempt_identity", "output_manifest_sha256",
                "artifact_sha256", "result_identity", "result_sha256",
                "result_envelope",
            }:
                raise AnalysisPersistenceError(
                    "Analysis domain verification evidence is invalid"
                )
            attempt_number = receipt.get("attempt")
            if (
                type(attempt_number) is not int
                or attempt_number < 1
                or attempt_number > len(attempts)
                or attempt_number in verified_attempts
                or receipt.get("analysis_id") != record.get("analysis_id")
                or not str(receipt.get("verified_at") or "").strip()
            ):
                raise AnalysisPersistenceError(
                    "Analysis domain verification evidence is invalid"
                )
            collection = next(
                (
                    item
                    for item in collection_receipts
                    if item.get("attempt") == attempt_number
                ),
                None,
            )
            attempt = attempts[attempt_number - 1]
            if collection is None:
                raise AnalysisPersistenceError(
                    "Analysis domain verification evidence is invalid"
                )
            manifest_digest = str(receipt.get("output_manifest_sha256") or "")
            expected_attempt_identity = analysis_provider_attempt_identity(
                analysis_id=record["analysis_id"],
                attempt=attempt_number,
                provider_id=attempt["provider_id"],
                provider_job_id=attempt["provider_job_id"],
                output_manifest_sha256=manifest_digest,
            )
            artifact_sha256 = receipt.get("artifact_sha256")
            if (
                manifest_digest != collection.get("output_manifest_sha256")
                or receipt.get("provider_attempt_identity")
                != expected_attempt_identity
                or not isinstance(artifact_sha256, list)
                or not artifact_sha256
                or any(
                    not isinstance(digest, str)
                    or digest != digest.lower()
                    or len(digest) != 64
                    or any(
                        character not in "0123456789abcdef"
                        for character in digest
                    )
                    for digest in artifact_sha256
                )
                or len(artifact_sha256) != len(set(artifact_sha256))
            ):
                raise AnalysisPersistenceError(
                    "Analysis domain verification evidence is invalid"
                )
            envelope_value = receipt.get("result_envelope")
            if not isinstance(envelope_value, Mapping):
                raise AnalysisPersistenceError(
                    "Analysis domain verification evidence is invalid"
                )
            try:
                from tool_impl.engineering_contracts import EngineeringResultEnvelope

                envelope = EngineeringResultEnvelope.from_dict(envelope_value)
                canonical = envelope.to_canonical_json()
            except Exception as exc:
                raise AnalysisPersistenceError(
                    "Analysis domain verification evidence is invalid"
                ) from exc
            result_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            envelope_artifacts = [item.digest for item in envelope.artifacts]
            recorded_artifacts = record.get("artifacts")
            if not isinstance(recorded_artifacts, list):
                raise AnalysisPersistenceError(
                    "Analysis domain verification evidence is invalid"
                )
            active_artifacts = {
                item.get("sha256"): item
                for item in recorded_artifacts
                if isinstance(item, Mapping) and not item.get("tombstoned_at")
            }
            if (
                receipt.get("result_identity") != envelope.result_id.canonical
                or receipt.get("result_sha256") != result_digest
                or envelope.domain != record.get("domain")
                or envelope.adapter_id != record.get("adapter_id")
                or envelope.provider_attempt_id != expected_attempt_identity
                or envelope.source_identity.kind != "document"
                or envelope.source_identity.value != record.get("source_document_uid")
                or envelope.dependency_digest != record.get("dependency_sha256")
                or envelope.currentness != "current"
                or envelope.publication_state != "unpublished"
                or envelope_artifacts != artifact_sha256
                or any(
                    item.domain != record.get("domain")
                    for item in envelope.findings
                )
                or any(
                    descriptor.digest not in active_artifacts
                    or active_artifacts[descriptor.digest].get("byte_count")
                    != descriptor.byte_size
                    for descriptor in envelope.artifacts
                )
                or any(
                    evidence.digest not in set(artifact_sha256)
                    for finding in envelope.findings
                    for evidence in finding.evidence
                )
            ):
                raise AnalysisPersistenceError(
                    "Analysis domain verification evidence is invalid"
                )
            verified_attempts.add(attempt_number)
        artifacts = record.get("artifacts")
        if not isinstance(artifacts, list):
            raise AnalysisPersistenceError("Analysis artifacts must be a list")
        digests: set[str] = set()
        active_digests: set[str] = set()
        for artifact in artifacts:
            if not isinstance(artifact, Mapping):
                raise AnalysisPersistenceError("Analysis artifact metadata must be an object")
            digest = str(artifact.get("sha256") or "")
            if (
                digest != digest.lower()
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise AnalysisPersistenceError("Artifact sha256 must be a digest")
            if digest in digests:
                raise AnalysisPersistenceError("Analysis artifact identities must be unique")
            digests.add(digest)
            byte_count = artifact.get("byte_count")
            if byte_count is not None and (
                type(byte_count) is not int or byte_count < 0
            ):
                raise AnalysisPersistenceError(
                    "Artifact byte_count must be a non-negative integer"
                )
            if not artifact.get("tombstoned_at"):
                active_digests.add(digest)
        references = _artifact_references(record)
        if any(digest not in active_digests for digest in references):
            raise AnalysisPersistenceError(
                "Publication artifact reference is unknown or tombstoned"
            )
        _validate_verified_publication(record)
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
        provider_capability_snapshot: Mapping[str, Any] | None = None,
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
        self.provider_capability_snapshot = provider_capability_snapshot
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
            provider_capability_snapshot=self.provider_capability_snapshot,
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
