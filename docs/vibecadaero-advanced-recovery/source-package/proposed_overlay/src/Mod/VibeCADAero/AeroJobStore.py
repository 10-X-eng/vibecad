# SPDX-License-Identifier: LGPL-2.1-or-later
"""Persistent, solver-neutral lifecycle records for long-running Aero jobs.

Pass 03 Correction 01 retains this implementation only as **TRANSITIONAL / REFERENCE ONLY** lifecycle/domain-payload semantics.  It is NOT the target production job
authority.  The canonical target is one host-owned VibeCAD Analysis Runtime
extracted non-destructively from Native Background + detached FEM execution,
with FEM as the first parity-proven client and Aero as the second.

Native previews remain short-lived CAD mutation authorization. CFD/remote jobs
are evidence-producing work that may outlive Native sessions. This reference
performs no CAD mutation and no license/purpose enforcement.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable, Mapping

JOB_STORE_SCHEMA = "vibecad.aero.jobs/1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class LifecycleState(str, Enum):
    PREPARED = "prepared"
    QUEUED = "queued"
    UPLOADING = "uploading"
    SUBMITTED = "submitted"
    RUNNING = "running"
    DOWNLOADING = "downloading"
    PARSING = "parsing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ORPHANED = "orphaned"


TERMINAL_STATES = frozenset(
    {LifecycleState.SUCCEEDED, LifecycleState.FAILED, LifecycleState.CANCELLED}
)

_ALLOWED: dict[LifecycleState, frozenset[LifecycleState]] = {
    LifecycleState.PREPARED: frozenset({LifecycleState.QUEUED, LifecycleState.UPLOADING, LifecycleState.SUBMITTED, LifecycleState.RUNNING, LifecycleState.CANCELLED, LifecycleState.FAILED}),
    LifecycleState.QUEUED: frozenset({LifecycleState.UPLOADING, LifecycleState.SUBMITTED, LifecycleState.RUNNING, LifecycleState.CANCELLED, LifecycleState.FAILED}),
    LifecycleState.UPLOADING: frozenset({LifecycleState.SUBMITTED, LifecycleState.CANCELLED, LifecycleState.FAILED}),
    LifecycleState.SUBMITTED: frozenset({LifecycleState.RUNNING, LifecycleState.DOWNLOADING, LifecycleState.CANCELLED, LifecycleState.FAILED, LifecycleState.ORPHANED}),
    LifecycleState.RUNNING: frozenset({LifecycleState.DOWNLOADING, LifecycleState.PARSING, LifecycleState.SUCCEEDED, LifecycleState.CANCELLED, LifecycleState.FAILED, LifecycleState.ORPHANED}),
    LifecycleState.DOWNLOADING: frozenset({LifecycleState.PARSING, LifecycleState.SUCCEEDED, LifecycleState.CANCELLED, LifecycleState.FAILED, LifecycleState.ORPHANED}),
    LifecycleState.PARSING: frozenset({LifecycleState.SUCCEEDED, LifecycleState.FAILED}),
    LifecycleState.ORPHANED: frozenset({LifecycleState.SUBMITTED, LifecycleState.RUNNING, LifecycleState.DOWNLOADING, LifecycleState.FAILED, LifecycleState.CANCELLED}),
    LifecycleState.SUCCEEDED: frozenset(),
    LifecycleState.FAILED: frozenset(),
    LifecycleState.CANCELLED: frozenset(),
}


@dataclass(frozen=True)
class AeroJobRecord:
    job_id: str
    case_id: str
    solver_backend: str
    compute_provider: str
    document_uid: str
    captured_native_revision: int
    geometry_revision: str
    state: LifecycleState = LifecycleState.PREPARED
    provider_job_id: str | None = None
    workdir: str | None = None
    result_path: str | None = None
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)
    attempt: int = 1
    progress: float | None = None
    error: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        for label, value in (
            ("job_id", self.job_id),
            ("case_id", self.case_id),
            ("solver_backend", self.solver_backend),
            ("compute_provider", self.compute_provider),
            ("document_uid", self.document_uid),
            ("geometry_revision", self.geometry_revision),
        ):
            if not str(value).strip():
                raise ValueError(f"{label} is required")
        if type(self.captured_native_revision) is not int or self.captured_native_revision < 0:
            raise ValueError("captured_native_revision must be non-negative")
        if self.attempt < 1:
            raise ValueError("attempt must be >= 1")
        if self.progress is not None and not 0.0 <= float(self.progress) <= 1.0:
            raise ValueError("progress must be between 0 and 1")

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def stale_against(self, *, native_revision: int, geometry_revision: str) -> bool:
        return (
            int(native_revision) != self.captured_native_revision
            or str(geometry_revision) != self.geometry_revision
        )


class AeroJobStore:
    """Atomic JSON persistence for bounded-size job metadata (not solver fields)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()

    def _load_payload(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"schema": JOB_STORE_SCHEMA, "jobs": []}
        loaded = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict) or loaded.get("schema") != JOB_STORE_SCHEMA:
            raise ValueError("Aero job store schema is invalid")
        if not isinstance(loaded.get("jobs"), list):
            raise ValueError("Aero job store jobs must be a list")
        return loaded

    @staticmethod
    def _from_dict(raw: Mapping[str, Any]) -> AeroJobRecord:
        data = dict(raw)
        data["state"] = LifecycleState(str(data.get("state") or LifecycleState.PREPARED.value))
        record = AeroJobRecord(**data)
        record.validate()
        return record

    def list(self) -> list[AeroJobRecord]:
        return [self._from_dict(raw) for raw in self._load_payload()["jobs"]]

    def get(self, job_id: str) -> AeroJobRecord | None:
        for record in self.list():
            if record.job_id == job_id:
                return record
        return None

    def save(self, records: Iterable[AeroJobRecord]) -> None:
        items = list(records)
        seen: set[str] = set()
        serialized: list[dict[str, Any]] = []
        for record in items:
            record.validate()
            if record.job_id in seen:
                raise ValueError(f"duplicate job_id: {record.job_id}")
            seen.add(record.job_id)
            raw = asdict(record)
            raw["state"] = record.state.value
            raw["metadata"] = dict(record.metadata)
            serialized.append(raw)
        payload = {"schema": JOB_STORE_SCHEMA, "jobs": serialized}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            try:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            except OSError:
                pass

    def put(self, record: AeroJobRecord) -> AeroJobRecord:
        records = self.list()
        replaced = False
        for index, existing in enumerate(records):
            if existing.job_id == record.job_id:
                records[index] = record
                replaced = True
                break
        if not replaced:
            records.append(record)
        self.save(records)
        return record

    def transition(
        self,
        job_id: str,
        state: LifecycleState | str,
        *,
        provider_job_id: str | None = None,
        progress: float | None = None,
        result_path: str | None = None,
        error: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> AeroJobRecord:
        current = self.get(job_id)
        if current is None:
            raise KeyError(job_id)
        target = LifecycleState(state)
        if target != current.state and target not in _ALLOWED[current.state]:
            raise ValueError(f"illegal Aero job transition: {current.state.value} -> {target.value}")
        merged_metadata = dict(current.metadata)
        merged_metadata.update(dict(metadata or {}))
        data = asdict(current)
        data.update(
            state=target,
            provider_job_id=provider_job_id if provider_job_id is not None else current.provider_job_id,
            progress=progress if progress is not None else current.progress,
            result_path=result_path if result_path is not None else current.result_path,
            error=error,
            metadata=merged_metadata,
            updated_at=_utc_now(),
        )
        data["state"] = target
        updated = AeroJobRecord(**data)
        return self.put(updated)
