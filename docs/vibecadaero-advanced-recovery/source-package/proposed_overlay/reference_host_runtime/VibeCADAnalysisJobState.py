"""Reference-only host Analysis Runtime lifecycle model.

This module is intentionally FreeCAD-independent and is NOT wired into upstream
VibeCAD. It proves the atomic cancellation-versus-publication gate required by
Pass 03 Correction 01. Its internal status/phase vocabulary is a proof model,
not a replacement for the current public NativeBackground phase surface.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Literal

Status = Literal[
    "queued", "running", "cancelling", "waiting_to_commit",
    "succeeded", "failed", "cancelled",
]
Phase = Literal[
    "queued", "running_solver", "finalizing", "waiting_to_commit",
    "committing", "completed",
]

_TERMINAL = {"succeeded", "failed", "cancelled"}
_CANCELABLE_PHASES = {"queued", "running_solver", "waiting_to_commit"}


@dataclass
class AnalysisJobState:
    job_id: str
    status: Status = "queued"
    phase: Phase = "queued"
    cancellation_requested: bool = False
    terminal_reason: str | None = None
    _lock: RLock = field(default_factory=RLock, repr=False, compare=False)

    def start(self) -> bool:
        with self._lock:
            if self.status in _TERMINAL or self.cancellation_requested:
                return False
            if self.phase != "queued":
                return False
            self.status = "running"
            self.phase = "running_solver"
            return True

    def provider_completed(self) -> bool:
        with self._lock:
            if self.status in _TERMINAL:
                return False
            if self.cancellation_requested:
                self._finish_cancelled("cancelled_before_publication")
                return False
            if self.phase != "running_solver":
                return False
            self.status = "waiting_to_commit"
            self.phase = "waiting_to_commit"
            return True

    def request_cancel(self) -> bool:
        """Linearizable cancellation request.

        True means cancellation won before publication ownership. False means
        the job is terminal or publication has become non-cancellable.
        """
        with self._lock:
            if self.status in _TERMINAL:
                return False
            if self.phase not in _CANCELABLE_PHASES:
                return False
            self.cancellation_requested = True
            self.status = "cancelling"
            if self.phase in {"queued", "waiting_to_commit"}:
                self._finish_cancelled("cancelled_before_publication")
            return True

    def acknowledge_running_cancel(self) -> bool:
        with self._lock:
            if self.status in _TERMINAL:
                return self.status == "cancelled"
            if not self.cancellation_requested:
                return False
            self._finish_cancelled("provider_cancelled")
            return True

    def try_begin_publication(self) -> bool:
        """Atomic cancellation-vs-publication ownership gate."""
        with self._lock:
            if self.status in _TERMINAL:
                return False
            if self.phase != "waiting_to_commit":
                return False
            if self.cancellation_requested:
                self._finish_cancelled("cancelled_before_publication")
                return False
            self.status = "running"
            self.phase = "committing"
            return True

    # Backward-compatible reference name used by earlier Correction-01 tests.
    def try_begin_commit(self) -> bool:
        return self.try_begin_publication()

    def succeed(self) -> bool:
        with self._lock:
            if self.status in _TERMINAL:
                return self.status == "succeeded"
            if self.phase != "committing":
                return False
            self.status = "succeeded"
            self.phase = "completed"
            self.terminal_reason = "published"
            return True

    def fail(self, reason: str) -> bool:
        with self._lock:
            if self.status in _TERMINAL:
                return self.status == "failed"
            self.status = "failed"
            self.phase = "completed"
            self.terminal_reason = reason
            return True

    def _finish_cancelled(self, reason: str) -> None:
        self.status = "cancelled"
        self.phase = "completed"
        self.terminal_reason = reason
