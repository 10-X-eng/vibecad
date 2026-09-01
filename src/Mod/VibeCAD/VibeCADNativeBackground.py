# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded off-thread preparation for expensive Native capabilities."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
import json
import secrets
import threading
import time
from typing import Any, Callable, Mapping


MAX_BACKGROUND_JOBS = 32
MAX_BACKGROUND_RESULT_BYTES = 32 * 1024
MAX_PROGRESS_MESSAGE_CHARS = 160
MAX_FAILURE_MESSAGE_CHARS = 320
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
    resource_scope: str
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
class _Job:
    job_id: str
    document_uid: str
    capability_name: str
    resource_scope: str = "document"
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


ProgressReporter = Callable[[int, str], None]
PrepareHandler = Callable[[Callable[[], bool], ProgressReporter], Any]
CommitHandler = Callable[[Any], Mapping[str, Any]]
DocumentThreadDispatcher = Callable[[Callable[[], Any]], Any]
CommitValidator = Callable[[], Any]
DiagnosticSink = Callable[[str, Exception], str | None]
CleanupHandler = Callable[[Any | None], None]
DocumentChangeResolver = Callable[[Mapping[str, Any]], bool]


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


def _bounded_failure_message(value: Any) -> str:
    message = str(value or "").strip()
    if len(message) <= MAX_FAILURE_MESSAGE_CHARS:
        return message
    head = message[:96].rstrip()
    separator = " ... "
    tail_length = MAX_FAILURE_MESSAGE_CHARS - len(head) - len(separator)
    return head + separator + message[-tail_length:].lstrip()


def _error_summary(exc: Exception, diagnostic_id: str | None) -> dict[str, Any]:
    failure = getattr(exc, "failure", None)
    if callable(failure):
        value = failure()
        if isinstance(value, Mapping):
            result = {
                "error_code": str(value.get("error_code") or "")[:80],
                "message": _bounded_failure_message(value.get("message")),
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
        self._active_resources: dict[tuple[str, str], str] = {}
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
        resource_scope: str = "document",
    ) -> NativeBackgroundSnapshot:
        uid = str(document_uid or "").strip()
        capability = str(capability_name or "").strip()
        if not uid or not capability:
            raise NativeBackgroundError(
                "A background Native job needs exact document and capability IDs."
            )
        scope = str(resource_scope or "document").strip()
        if not scope or len(scope) > 160 or any(ord(value) < 32 for value in scope):
            raise NativeBackgroundError(
                "A background Native resource scope must be 1 through 160 printable characters."
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
        if cleanup is not None and not callable(cleanup):
            raise TypeError("Native background cleanup must be callable")
        if type(changes_document) is not bool:
            raise TypeError("changes_document must be a boolean")
        if document_change_resolver is not None and not callable(document_change_resolver):
            raise TypeError("document_change_resolver must be callable or None")
        clean_finalize_message = str(finalize_message or "").strip()
        if len(clean_finalize_message) > MAX_PROGRESS_MESSAGE_CHARS:
            raise NativeBackgroundError(
                "A background Native finalization message exceeds its bound."
            )
        with self._lock:
            active_key = (uid, scope)
            conflict_keys = (
                tuple(
                    key for key in self._active_resources if key[0] == uid
                )
                if scope == "document"
                else ((uid, "document"), active_key)
            )
            for conflict_key in conflict_keys:
                active_job_id = self._active_resources.get(conflict_key)
                if active_job_id is None:
                    continue
                active_job = self._jobs.get(active_job_id)
                if active_job is not None and active_job.phase in _TERMINAL_PHASES:
                    self._active_resources.pop(conflict_key, None)
                    continue
                raise NativeBackgroundError(
                    (
                        "The exact document already has a background Native operation."
                        if scope == "document" or conflict_key[1] == "document"
                        else "The exact document resource already has a background Native operation."
                    )
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
            job = _Job(
                secrets.token_hex(16),
                uid,
                capability,
                scope,
                changes_document=changes_document,
            )
            self._jobs[job.job_id] = job
            self._active_resources[active_key] = job.job_id
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
        finalize_message: str,
        cleanup: CleanupHandler | None,
        document_change_resolver: DocumentChangeResolver | None,
    ) -> None:
        prepared = None
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
                self._set_progress(
                    job,
                    "finalizing" if finalize_message else "committing",
                    95,
                    finalize_message or "Committing document change",
                )
                return commit(prepared)

            result = dispatch_to_document_thread(apply)
            if document_change_resolver is not None:
                resolved_change = document_change_resolver(result)
                if type(resolved_change) is not bool:
                    raise NativeBackgroundError(
                        "A background document-change resolver must return a boolean."
                    )
                with self._lock:
                    job.changes_document = resolved_change
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
                active_key = (job.document_uid, job.resource_scope)
                if self._active_resources.get(active_key) == job.job_id:
                    self._active_resources.pop(active_key, None)
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
            job.progress_at = time.monotonic()

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._require_job_locked(job_id)
            if job.phase in _TERMINAL_PHASES or job.phase in {"committing", "finalizing"}:
                return False
            job.cancellation.set()
            return True

    def cancel_document(self, document_uid: str) -> bool:
        uid = str(document_uid or "").strip()
        with self._lock:
            job_ids = tuple(
                job_id
                for (document, _scope), job_id in self._active_resources.items()
                if document == uid
            )
        accepted = False
        for job_id in job_ids:
            accepted = self.cancel(job_id) or accepted
        return accepted

    def snapshot(self, job_id: str) -> NativeBackgroundSnapshot:
        with self._lock:
            job = self._require_job_locked(job_id)
            return self._snapshot_locked(job)

    def latest_document_snapshot(
        self,
        document_uid: str,
        *,
        capability_prefix: str = "",
    ) -> NativeBackgroundSnapshot | None:
        """Return the newest bounded job state for one exact document."""

        uid = str(document_uid or "").strip()
        prefix = str(capability_prefix or "").strip()
        if not uid:
            raise NativeBackgroundError("A background job lookup needs a document UID.")
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

    def document_snapshots(
        self,
        document_uid: str,
        *,
        capability_prefix: str = "",
        active_only: bool = False,
        limit: int = MAX_BACKGROUND_JOBS,
    ) -> tuple[NativeBackgroundSnapshot, ...]:
        """Return newest bounded jobs for one document without collapsing scopes."""

        uid = str(document_uid or "").strip()
        prefix = str(capability_prefix or "").strip()
        if not uid:
            raise NativeBackgroundError("A background job lookup needs a document UID.")
        if type(active_only) is not bool:
            raise TypeError("active_only must be a boolean")
        if type(limit) is not int or not 1 <= limit <= MAX_BACKGROUND_JOBS:
            raise NativeBackgroundError(
                f"A background job catalog limit must be 1 through {MAX_BACKGROUND_JOBS}."
            )
        with self._lock:
            jobs = tuple(
                candidate
                for candidate in reversed(tuple(self._jobs.values()))
                if candidate.document_uid == uid
                and (not prefix or candidate.capability_name.startswith(prefix))
                and (not active_only or candidate.phase not in _TERMINAL_PHASES)
            )[:limit]
            return tuple(self._snapshot_locked(job) for job in jobs)

    @staticmethod
    def _snapshot_locked(job: _Job) -> NativeBackgroundSnapshot:
        now = time.monotonic()
        result = json.loads(job.result_json) if job.result_json is not None else None
        return NativeBackgroundSnapshot(
            job_id=job.job_id,
            document_uid=job.document_uid,
            capability_name=job.capability_name,
            resource_scope=job.resource_scope,
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
