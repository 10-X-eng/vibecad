# SPDX-License-Identifier: LGPL-2.1-or-later

"""Domain-neutral in-memory orchestration for prepared VibeCAD Analysis work."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
import hashlib
import json
import secrets
import threading
import time
from typing import Any, Callable, Mapping


DEFAULT_MAX_RUNTIME_JOBS = 32
DEFAULT_MAX_RESULT_BYTES = 32 * 1024
DEFAULT_MAX_PROGRESS_MESSAGE_CHARS = 160
_TERMINAL_PHASES = frozenset({"completed", "cancelled", "failed"})


class AnalysisRuntimeCancelled(RuntimeError):
    """Cooperative cancellation accepted before the publication gate."""


class AnalysisRuntimeError(RuntimeError):
    """An Analysis runtime job cannot be scheduled or queried safely."""


@dataclass(frozen=True, slots=True)
class AnalysisRuntimeSnapshot:
    job_id: str
    document_uid: str
    capability_name: str
    phase: str
    progress_percent: int
    progress_message: str
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    cancel_requested: bool
    changes_document: bool = False
    elapsed_seconds: int = 0
    seconds_since_progress: int = 0
    worker_active: bool = False

    @property
    def terminal(self) -> bool:
        return self.phase in _TERMINAL_PHASES

    @property
    def document_changed(self) -> bool:
        return self.phase == "completed" and self.changes_document


@dataclass(slots=True)
class _AnalysisJob:
    job_id: str
    document_uid: str
    capability_name: str
    phase: str = "queued"
    progress_percent: int = 0
    progress_message: str = "Queued"
    result_json: str | None = None
    error: dict[str, Any] | None = None
    changes_document: bool = False
    cancellation: threading.Event = field(default_factory=threading.Event)
    completed: threading.Event = field(default_factory=threading.Event)
    submitted_at: float = field(default_factory=time.monotonic)
    progress_at: float = field(default_factory=time.monotonic)


@dataclass(frozen=True, slots=True)
class AnalysisRuntimeMessages:
    identifiers_required: str = (
        "An Analysis runtime job needs exact document and capability IDs."
    )
    callbacks_required: str = "Analysis runtime callbacks must be callable"
    cleanup_required: str = "Analysis runtime cleanup must be callable"
    finalization_message_too_long: str = (
        "An Analysis runtime finalization message exceeds its bound."
    )
    document_busy: str = "The exact document already has an active Analysis job."
    queue_full: str = "The bounded Analysis runtime queue is full."
    progress_range: str = (
        "Analysis preparation progress must be between 1 and 90."
    )
    progress_backwards: str = (
        "Analysis preparation progress cannot move backwards."
    )
    document_lookup_requires_uid: str = (
        "An Analysis job lookup needs a document UID."
    )
    document_change_resolver_boolean: str = (
        "An Analysis document-change resolver must return a boolean."
    )
    unknown_job: str = "The Analysis runtime job is unknown."


ProgressReporter = Callable[[int, str], None]
PrepareHandler = Callable[[Callable[[], bool], ProgressReporter], Any]
CommitHandler = Callable[[Any], Mapping[str, Any]]
DocumentThreadDispatcher = Callable[[Callable[[], Any]], Any]
CommitValidator = Callable[[], Any]
DiagnosticSink = Callable[[str, Exception], str | None]
CleanupHandler = Callable[[Any | None], None]
DocumentChangeResolver = Callable[[Mapping[str, Any]], bool]


class AnalysisRuntimeManager:
    """Prepare detached work off-thread and publish through the document thread."""

    def __init__(
        self,
        *,
        diagnostic_sink: DiagnosticSink | None = None,
        maximum_jobs: int = DEFAULT_MAX_RUNTIME_JOBS,
        maximum_result_bytes: int = DEFAULT_MAX_RESULT_BYTES,
        maximum_progress_message_chars: int = DEFAULT_MAX_PROGRESS_MESSAGE_CHARS,
        error_class: type[Exception] = AnalysisRuntimeError,
        cancelled_class: type[Exception] = AnalysisRuntimeCancelled,
        messages: AnalysisRuntimeMessages | None = None,
        thread_name_prefix: str = "VibeCADAnalysis",
    ) -> None:
        if diagnostic_sink is not None and not callable(diagnostic_sink):
            raise TypeError("diagnostic_sink must be callable")
        if type(maximum_jobs) is not int or maximum_jobs < 1:
            raise ValueError("maximum_jobs must be a positive integer")
        if type(maximum_result_bytes) is not int or maximum_result_bytes < 1:
            raise ValueError("maximum_result_bytes must be a positive integer")
        if (
            type(maximum_progress_message_chars) is not int
            or maximum_progress_message_chars < 1
        ):
            raise ValueError(
                "maximum_progress_message_chars must be a positive integer"
            )
        if not isinstance(error_class, type) or not issubclass(error_class, Exception):
            raise TypeError("error_class must be an Exception type")
        if (
            not isinstance(cancelled_class, type)
            or not issubclass(cancelled_class, Exception)
        ):
            raise TypeError("cancelled_class must be an Exception type")
        prefix = str(thread_name_prefix or "").strip()
        if not prefix:
            raise ValueError("thread_name_prefix must be non-empty")

        self._diagnostic_sink = diagnostic_sink
        self._maximum_jobs = maximum_jobs
        self._maximum_result_bytes = maximum_result_bytes
        self._maximum_progress_message_chars = maximum_progress_message_chars
        self._error_class = error_class
        self._cancelled_class = cancelled_class
        self._messages = messages or AnalysisRuntimeMessages()
        self._thread_name_prefix = prefix
        self._jobs: OrderedDict[str, _AnalysisJob] = OrderedDict()
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
        finalize_message: str | None = None,
        cleanup: CleanupHandler | None = None,
        changes_document: bool = False,
        document_change_resolver: DocumentChangeResolver | None = None,
        durable_lifecycle: Any | None = None,
    ) -> AnalysisRuntimeSnapshot:
        uid = str(document_uid or "").strip()
        capability = str(capability_name or "").strip()
        if not uid or not capability:
            raise self._error_class(self._messages.identifiers_required)
        if not all(
            callable(callback)
            for callback in (
                prepare,
                validate_before_commit,
                commit,
                dispatch_to_document_thread,
            )
        ):
            raise TypeError(self._messages.callbacks_required)
        if cleanup is not None and not callable(cleanup):
            raise TypeError(self._messages.cleanup_required)
        if type(changes_document) is not bool:
            raise TypeError("changes_document must be a boolean")
        if document_change_resolver is not None and not callable(
            document_change_resolver
        ):
            raise TypeError("document_change_resolver must be callable or None")
        if durable_lifecycle is not None:
            required = (
                "submitted", "started", "prepared", "publication_started",
                "succeeded", "failed", "cancelled",
            )
            if any(not callable(getattr(durable_lifecycle, name, None)) for name in required):
                raise TypeError("durable_lifecycle does not implement the Analysis lifecycle")
        clean_finalize_message = str(finalize_message or "").strip()
        if len(clean_finalize_message) > self._maximum_progress_message_chars:
            raise self._error_class(self._messages.finalization_message_too_long)

        with self._lock:
            active_job_id = self._active_documents.get(uid)
            if active_job_id is not None:
                active_job = self._jobs.get(active_job_id)
                if active_job is not None and active_job.phase in _TERMINAL_PHASES:
                    self._active_documents.pop(uid, None)
                else:
                    raise self._error_class(self._messages.document_busy)
            if len(self._jobs) >= self._maximum_jobs:
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
            if len(self._jobs) >= self._maximum_jobs:
                raise self._error_class(self._messages.queue_full)

            job = _AnalysisJob(
                secrets.token_hex(16),
                uid,
                capability,
                changes_document=changes_document,
            )
            if durable_lifecycle is not None:
                durable_lifecycle.submitted(job.job_id, uid, capability)
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
                clean_finalize_message,
                cleanup,
                document_change_resolver,
                durable_lifecycle,
            ),
            name=f"{self._thread_name_prefix}-{job.job_id[:8]}",
            daemon=True,
        )
        thread.start()
        return self.snapshot(job.job_id)

    def _run(
        self,
        job: _AnalysisJob,
        prepare: PrepareHandler,
        validate_before_commit: CommitValidator,
        commit: CommitHandler,
        dispatch_to_document_thread: DocumentThreadDispatcher,
        finalize_message: str,
        cleanup: CleanupHandler | None,
        document_change_resolver: DocumentChangeResolver | None,
        durable_lifecycle: Any | None,
    ) -> None:
        prepared = None
        try:
            if durable_lifecycle is not None:
                durable_lifecycle.started()
            self._set_progress(job, "preparing", 1, "Preparing detached data")

            def report(percent: int, message: str) -> None:
                if job.cancellation.is_set():
                    raise self._cancelled_class()
                if type(percent) is not int or percent < 1 or percent > 90:
                    raise self._error_class(self._messages.progress_range)
                with self._lock:
                    if percent < job.progress_percent:
                        raise self._error_class(self._messages.progress_backwards)
                self._set_progress(job, "preparing", percent, message)

            prepared = prepare(job.cancellation.is_set, report)
            if job.cancellation.is_set():
                raise self._cancelled_class()
            if durable_lifecycle is not None:
                durable_lifecycle.prepared()
            self._set_progress(job, "waiting_to_commit", 90, "Waiting to commit")

            def apply() -> Mapping[str, Any]:
                if job.cancellation.is_set():
                    raise self._cancelled_class()
                validate_before_commit()
                self._enter_commit_gate(
                    job,
                    "finalizing" if finalize_message else "committing",
                    95,
                    finalize_message or "Committing document change",
                )
                if durable_lifecycle is not None:
                    durable_lifecycle.publication_started()
                return commit(prepared)

            result = dispatch_to_document_thread(apply)
            if document_change_resolver is not None:
                resolved_change = document_change_resolver(result)
                if type(resolved_change) is not bool:
                    raise self._error_class(
                        self._messages.document_change_resolver_boolean
                    )
                with self._lock:
                    job.changes_document = resolved_change
            encoded = self._encode_result(result)
            if durable_lifecycle is not None:
                durable_lifecycle.succeeded(
                    hashlib.sha256(encoded.encode("utf-8")).hexdigest()
                )
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
                if isinstance(exc, self._cancelled_class)
                else "failed"
            )
            if durable_lifecycle is not None:
                try:
                    if phase == "cancelled":
                        durable_lifecycle.cancelled()
                    else:
                        durable_lifecycle.failed(type(exc).__name__)
                except Exception as durable_exc:
                    if self._diagnostic_sink is not None:
                        try:
                            self._diagnostic_sink(job.job_id, durable_exc)
                        except Exception:
                            pass
            with self._lock:
                job.error = self._summarize_error(exc, diagnostic_id)
            self._set_progress(
                job,
                phase,
                job.progress_percent,
                "Cancelled" if phase == "cancelled" else "Failed",
            )
        finally:
            if cleanup is not None:
                try:
                    cleanup(prepared)
                except Exception as exc:
                    if self._diagnostic_sink is not None:
                        try:
                            self._diagnostic_sink(job.job_id, exc)
                        except Exception:
                            pass
            with self._lock:
                if self._active_documents.get(job.document_uid) == job.job_id:
                    self._active_documents.pop(job.document_uid, None)
                job.completed.set()
                self._trim_jobs_locked()

    def _encode_result(self, result: Mapping[str, Any]) -> str:
        if not isinstance(result, Mapping):
            raise self._error_class("An Analysis runtime result must be an object.")
        try:
            encoded = json.dumps(
                dict(result),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as exc:
            raise self._error_class(
                "An Analysis runtime result must be bounded JSON."
            ) from exc
        if len(encoded.encode("utf-8")) > self._maximum_result_bytes:
            raise self._error_class("An Analysis runtime result exceeds its bound.")
        return encoded

    def _summarize_error(
        self,
        exc: Exception,
        diagnostic_id: str | None,
    ) -> dict[str, Any]:
        failure = getattr(exc, "failure", None)
        result: dict[str, Any]
        if callable(failure):
            value = failure()
            if isinstance(value, Mapping):
                result = {
                    "error_code": str(value.get("error_code") or "")[:80],
                    "message": str(value.get("message") or "")[:320],
                }
            else:
                result = {}
        elif isinstance(exc, self._cancelled_class):
            result = {
                "error_code": "ANALYSIS_RUNTIME_CANCELLED",
                "message": "The Analysis operation was cancelled before publication.",
            }
        else:
            result = {
                "error_code": "ANALYSIS_RUNTIME_FAILED",
                "message": "The Analysis operation failed before publication.",
            }
        if diagnostic_id:
            result["diagnostic_id"] = str(diagnostic_id)
        return result

    def _enter_commit_gate(
        self,
        job: _AnalysisJob,
        phase: str,
        percent: int,
        message: str,
    ) -> None:
        clean_message = str(message or "").strip()
        if len(clean_message) > self._maximum_progress_message_chars:
            clean_message = clean_message[: self._maximum_progress_message_chars]
        with self._lock:
            if job.phase in _TERMINAL_PHASES or job.cancellation.is_set():
                raise self._cancelled_class()
            job.phase = phase
            job.progress_percent = int(percent)
            job.progress_message = clean_message
            job.progress_at = time.monotonic()

    def _set_progress(
        self,
        job: _AnalysisJob,
        phase: str,
        percent: int,
        message: str,
    ) -> None:
        clean_message = str(message or "").strip()
        if len(clean_message) > self._maximum_progress_message_chars:
            clean_message = clean_message[: self._maximum_progress_message_chars]
        with self._lock:
            job.phase = phase
            job.progress_percent = int(percent)
            job.progress_message = clean_message
            job.progress_at = time.monotonic()

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._require_job_locked(job_id)
            if job.phase in _TERMINAL_PHASES or job.phase in {
                "committing",
                "finalizing",
            }:
                return False
            job.cancellation.set()
            return True

    def cancel_document(self, document_uid: str) -> bool:
        uid = str(document_uid or "").strip()
        with self._lock:
            job_id = self._active_documents.get(uid)
        return self.cancel(job_id) if job_id else False

    def snapshot(self, job_id: str) -> AnalysisRuntimeSnapshot:
        with self._lock:
            job = self._require_job_locked(job_id)
            return self._snapshot_locked(job)

    def latest_document_snapshot(
        self,
        document_uid: str,
        *,
        capability_prefix: str = "",
    ) -> AnalysisRuntimeSnapshot | None:
        uid = str(document_uid or "").strip()
        prefix = str(capability_prefix or "").strip()
        if not uid:
            raise self._error_class(self._messages.document_lookup_requires_uid)
        with self._lock:
            job = next(
                (
                    candidate
                    for candidate in reversed(tuple(self._jobs.values()))
                    if candidate.document_uid == uid
                    and (
                        not prefix
                        or candidate.capability_name.startswith(prefix)
                    )
                ),
                None,
            )
            return self._snapshot_locked(job) if job is not None else None

    def _snapshot_locked(self, job: _AnalysisJob) -> AnalysisRuntimeSnapshot:
        now = time.monotonic()
        result = json.loads(job.result_json) if job.result_json is not None else None
        return AnalysisRuntimeSnapshot(
            job_id=job.job_id,
            document_uid=job.document_uid,
            capability_name=job.capability_name,
            phase=job.phase,
            progress_percent=job.progress_percent,
            progress_message=job.progress_message,
            result=result,
            error=dict(job.error) if job.error is not None else None,
            cancel_requested=job.cancellation.is_set(),
            changes_document=job.changes_document,
            elapsed_seconds=max(0, int(now - job.submitted_at)),
            seconds_since_progress=max(0, int(now - job.progress_at)),
            worker_active=not job.completed.is_set(),
        )

    def wait(
        self,
        job_id: str,
        timeout: float | None = None,
    ) -> AnalysisRuntimeSnapshot:
        with self._lock:
            job = self._require_job_locked(job_id)
        job.completed.wait(timeout)
        return self.snapshot(job_id)

    def _require_job_locked(self, job_id: str | None) -> _AnalysisJob:
        clean = str(job_id or "").strip()
        job = self._jobs.get(clean)
        if job is None:
            raise self._error_class(self._messages.unknown_job)
        return job

    def _trim_jobs_locked(self) -> None:
        while len(self._jobs) > self._maximum_jobs:
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
