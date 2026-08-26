# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native compatibility facade over the host-owned Analysis Runtime."""

from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Any, Callable, Mapping

from tool_impl.analysis_runtime import (
    AnalysisRuntimeManager,
    AnalysisRuntimeMessages,
    _AnalysisJob,
    _TERMINAL_PHASES,
)


MAX_BACKGROUND_JOBS = 32
MAX_BACKGROUND_RESULT_BYTES = 32 * 1024
MAX_PROGRESS_MESSAGE_CHARS = 160
MAX_FAILURE_MESSAGE_CHARS = 320


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


# Preserve the migration-window private shape used by current tests/callers.
_Job = _AnalysisJob


ProgressReporter = Callable[[int, str], None]
PrepareHandler = Callable[[Callable[[], bool], ProgressReporter], Any]
CommitHandler = Callable[[Any], Mapping[str, Any]]
DocumentThreadDispatcher = Callable[[Callable[[], Any]], Any]
CommitValidator = Callable[[], Any]
DiagnosticSink = Callable[[str, Exception], str | None]
CleanupHandler = Callable[[Any | None], None]


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


_NATIVE_MESSAGES = AnalysisRuntimeMessages(
    identifiers_required=(
        "A background Native job needs exact document and capability IDs."
    ),
    callbacks_required="Native background callbacks must be callable",
    cleanup_required="Native background cleanup must be callable",
    finalization_message_too_long=(
        "A background Native finalization message exceeds its bound."
    ),
    document_busy=(
        "The exact document already has a background Native operation."
    ),
    queue_full="The bounded Native background queue is full.",
    progress_range=(
        "Background preparation progress must be between 1 and 90."
    ),
    progress_backwards=(
        "Background preparation progress cannot move backwards."
    ),
    document_lookup_requires_uid=(
        "A background job lookup needs a document UID."
    ),
    unknown_job="The Native background job is unknown.",
)


class NativeBackgroundManager(AnalysisRuntimeManager):
    """Compatibility surface preserving current Native background behavior."""

    def __init__(self, *, diagnostic_sink: DiagnosticSink | None = None) -> None:
        super().__init__(
            diagnostic_sink=diagnostic_sink,
            maximum_jobs=MAX_BACKGROUND_JOBS,
            maximum_result_bytes=MAX_BACKGROUND_RESULT_BYTES,
            maximum_progress_message_chars=MAX_PROGRESS_MESSAGE_CHARS,
            error_class=NativeBackgroundError,
            cancelled_class=NativeBackgroundCancelled,
            messages=_NATIVE_MESSAGES,
            thread_name_prefix="VibeCADNative",
        )

    def _encode_result(self, result: Mapping[str, Any]) -> str:
        # Keep the public Native result contract and monkeypatchable bound exact.
        return _canonical_result(result)

    def _summarize_error(
        self,
        exc: Exception,
        diagnostic_id: str | None,
    ) -> dict[str, Any]:
        return _error_summary(exc, diagnostic_id)

    def _snapshot_locked(self, job: _Job) -> NativeBackgroundSnapshot:
        now = time.monotonic()
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
            changes_document=job.changes_document,
            elapsed_seconds=max(0, int(now - job.submitted_at)),
            seconds_since_progress=max(0, int(now - job.progress_at)),
            worker_active=not job.completed.is_set(),
        )
