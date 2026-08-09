# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded off-thread preparation for expensive Native capabilities."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
import json
import secrets
import threading
from typing import Any, Callable, Mapping


MAX_BACKGROUND_JOBS = 32
MAX_BACKGROUND_RESULT_BYTES = 32 * 1024
MAX_PROGRESS_MESSAGE_CHARS = 160
_TERMINAL_PHASES = frozenset({"completed", "cancelled", "failed"})


class NativeBackgroundCancelled(RuntimeError):
    """Cooperative cancellation before the document commit begins."""


class NativeBackgroundError(RuntimeError):
    """A background job cannot be scheduled or queried safely."""


@dataclass(frozen=True, slots=True)
class NativeBackgroundSnapshot:
    job_id: str
    document_uid: str
    capability_name: str
    phase: str
    progress_percent: int
    progress_message: str
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    cancel_requested: bool

    @property
    def terminal(self) -> bool:
        return self.phase in _TERMINAL_PHASES


@dataclass(slots=True)
class _Job:
    job_id: str
    document_uid: str
    capability_name: str
    phase: str = "queued"
    progress_percent: int = 0
    progress_message: str = "Queued"
    result_json: str | None = None
    error: dict[str, Any] | None = None
    cancellation: threading.Event = field(default_factory=threading.Event)
    completed: threading.Event = field(default_factory=threading.Event)


ProgressReporter = Callable[[int, str], None]
PrepareHandler = Callable[[Callable[[], bool], ProgressReporter], Any]
CommitHandler = Callable[[Any], Mapping[str, Any]]
DocumentThreadDispatcher = Callable[[Callable[[], Any]], Any]
CommitValidator = Callable[[], Any]
DiagnosticSink = Callable[[str, Exception], str | None]


def _canonical_result(result: Mapping[str, Any]) -> str:
    if not isinstance(result, Mapping):
        raise NativeBackgroundError("A background Native result must be an object.")
    try:
        encoded = json.dumps(
            dict(result),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise NativeBackgroundError(
            "A background Native result must be bounded JSON."
        ) from exc
    if len(encoded.encode("utf-8")) > MAX_BACKGROUND_RESULT_BYTES:
        raise NativeBackgroundError("A background Native result exceeds its bound.")
    return encoded


def _error_summary(exc: Exception, diagnostic_id: str | None) -> dict[str, Any]:
    failure = getattr(exc, "failure", None)
    if callable(failure):
        value = failure()
        if isinstance(value, Mapping):
            result = {
                "error_code": str(value.get("error_code") or "")[:80],
                "message": str(value.get("message") or "")[:320],
            }
            for key in ("current_surface", "current_revision", "exact_target"):
                if key in value and isinstance(value[key], (str, int)):
                    result[key] = value[key]
            repair = value.get("repair")
            if isinstance(repair, Mapping):
                try:
                    encoded_repair = json.dumps(
                        dict(repair),
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                except (TypeError, ValueError):
                    encoded_repair = ""
                if encoded_repair and len(encoded_repair.encode("utf-8")) <= 2048:
                    result["repair"] = json.loads(encoded_repair)
        else:
            result = {}
    elif isinstance(exc, NativeBackgroundCancelled):
        result = {
            "error_code": "NATIVE_BACKGROUND_CANCELLED",
            "message": "The background Native operation was cancelled before commit.",
        }
    else:
        result = {
            "error_code": "NATIVE_BACKGROUND_FAILED",
            "message": "The background Native operation failed before commit.",
        }
    if diagnostic_id:
        result["diagnostic_id"] = str(diagnostic_id)
    return result


class NativeBackgroundManager:
    """Prepare detached work off-thread and commit through the document thread."""

    def __init__(self, *, diagnostic_sink: DiagnosticSink | None = None) -> None:
        if diagnostic_sink is not None and not callable(diagnostic_sink):
            raise TypeError("diagnostic_sink must be callable")
        self._diagnostic_sink = diagnostic_sink
        self._jobs: OrderedDict[str, _Job] = OrderedDict()
        self._active_documents: dict[str, str] = {}
        self._lock = threading.RLock()

    def submit(
        self,
        *,
        document_uid: str,
        capability_name: str,
        prepare: PrepareHandler,
        validate_before_commit: CommitValidator,
        commit: CommitHandler,
        dispatch_to_document_thread: DocumentThreadDispatcher,
    ) -> NativeBackgroundSnapshot:
        uid = str(document_uid or "").strip()
        capability = str(capability_name or "").strip()
        if not uid or not capability:
            raise NativeBackgroundError(
                "A background Native job needs exact document and capability IDs."
            )
        if not all(
            callable(callback)
            for callback in (
                prepare,
                validate_before_commit,
                commit,
                dispatch_to_document_thread,
            )
        ):
            raise TypeError("Native background callbacks must be callable")
        with self._lock:
            active_job_id = self._active_documents.get(uid)
            if active_job_id is not None:
                active_job = self._jobs.get(active_job_id)
                if active_job is not None and active_job.phase in _TERMINAL_PHASES:
                    self._active_documents.pop(uid, None)
                else:
                    raise NativeBackgroundError(
                        "The exact document already has a background Native operation."
                    )
            if len(self._jobs) >= MAX_BACKGROUND_JOBS:
                removable = next(
                    (
                        job_id
                        for job_id, existing in self._jobs.items()
                        if existing.phase in _TERMINAL_PHASES
                    ),
                    None,
                )
                if removable is not None:
                    self._jobs.pop(removable, None)
            if len(self._jobs) >= MAX_BACKGROUND_JOBS:
                raise NativeBackgroundError(
                    "The bounded Native background queue is full."
                )
            job = _Job(secrets.token_hex(16), uid, capability)
            self._jobs[job.job_id] = job
            self._active_documents[uid] = job.job_id
            self._trim_jobs_locked()
        thread = threading.Thread(
            target=self._run,
            args=(
                job,
                prepare,
                validate_before_commit,
                commit,
                dispatch_to_document_thread,
            ),
            name=f"VibeCADNative-{job.job_id[:8]}",
            daemon=True,
        )
        thread.start()
        return self.snapshot(job.job_id)

    def _run(
        self,
        job: _Job,
        prepare: PrepareHandler,
        validate_before_commit: CommitValidator,
        commit: CommitHandler,
        dispatch_to_document_thread: DocumentThreadDispatcher,
    ) -> None:
        try:
            self._set_progress(job, "preparing", 1, "Preparing detached data")

            def report(percent: int, message: str) -> None:
                if job.cancellation.is_set():
                    raise NativeBackgroundCancelled()
                if type(percent) is not int or percent < 1 or percent > 90:
                    raise NativeBackgroundError(
                        "Background preparation progress must be between 1 and 90."
                    )
                with self._lock:
                    if percent < job.progress_percent:
                        raise NativeBackgroundError(
                            "Background preparation progress cannot move backwards."
                        )
                self._set_progress(job, "preparing", percent, message)

            prepared = prepare(job.cancellation.is_set, report)
            if job.cancellation.is_set():
                raise NativeBackgroundCancelled()
            self._set_progress(job, "waiting_to_commit", 90, "Waiting to commit")

            def apply() -> Mapping[str, Any]:
                if job.cancellation.is_set():
                    raise NativeBackgroundCancelled()
                validate_before_commit()
                if job.cancellation.is_set():
                    raise NativeBackgroundCancelled()
                self._set_progress(job, "committing", 95, "Committing document change")
                return commit(prepared)

            result = dispatch_to_document_thread(apply)
            encoded = _canonical_result(result)
            with self._lock:
                job.result_json = encoded
            self._set_progress(job, "completed", 100, "Completed")
        except Exception as exc:
            diagnostic_id = None
            if self._diagnostic_sink is not None:
                try:
                    diagnostic_id = self._diagnostic_sink(job.job_id, exc)
                except Exception:
                    diagnostic_id = None
            phase = (
                "cancelled"
                if isinstance(exc, NativeBackgroundCancelled)
                else "failed"
            )
            with self._lock:
                job.error = _error_summary(exc, diagnostic_id)
            self._set_progress(
                job,
                phase,
                job.progress_percent,
                "Cancelled" if phase == "cancelled" else "Failed",
            )
        finally:
            with self._lock:
                self._active_documents.pop(job.document_uid, None)
                job.completed.set()
                self._trim_jobs_locked()

    def _set_progress(
        self,
        job: _Job,
        phase: str,
        percent: int,
        message: str,
    ) -> None:
        clean_message = str(message or "").strip()
        if len(clean_message) > MAX_PROGRESS_MESSAGE_CHARS:
            clean_message = clean_message[:MAX_PROGRESS_MESSAGE_CHARS]
        with self._lock:
            job.phase = phase
            job.progress_percent = int(percent)
            job.progress_message = clean_message

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._require_job_locked(job_id)
            if job.phase in _TERMINAL_PHASES or job.phase == "committing":
                return False
            job.cancellation.set()
            return True

    def cancel_document(self, document_uid: str) -> bool:
        uid = str(document_uid or "").strip()
        with self._lock:
            job_id = self._active_documents.get(uid)
        return self.cancel(job_id) if job_id else False

    def snapshot(self, job_id: str) -> NativeBackgroundSnapshot:
        with self._lock:
            job = self._require_job_locked(job_id)
            result = json.loads(job.result_json) if job.result_json is not None else None
            return NativeBackgroundSnapshot(
                job_id=job.job_id,
                document_uid=job.document_uid,
                capability_name=job.capability_name,
                phase=job.phase,
                progress_percent=job.progress_percent,
                progress_message=job.progress_message,
                result=result,
                error=dict(job.error) if job.error is not None else None,
                cancel_requested=job.cancellation.is_set(),
            )

    def wait(self, job_id: str, timeout: float | None = None) -> NativeBackgroundSnapshot:
        with self._lock:
            job = self._require_job_locked(job_id)
        job.completed.wait(timeout)
        return self.snapshot(job_id)

    def _require_job_locked(self, job_id: str | None) -> _Job:
        clean = str(job_id or "").strip()
        job = self._jobs.get(clean)
        if job is None:
            raise NativeBackgroundError("The Native background job is unknown.")
        return job

    def _trim_jobs_locked(self) -> None:
        while len(self._jobs) > MAX_BACKGROUND_JOBS:
            removable = next(
                (
                    job_id
                    for job_id, job in self._jobs.items()
                    if job.phase in _TERMINAL_PHASES
                ),
                None,
            )
            if removable is None:
                return
            self._jobs.pop(removable, None)
