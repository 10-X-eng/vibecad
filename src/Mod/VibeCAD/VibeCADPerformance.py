# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded performance tracing primitives shared by VibeCAD host code.

Recording is memory-only. Export is an explicit operation so an instrumented
GUI callback never performs trace-file I/O on the document thread.
"""

from __future__ import annotations

import copy
from collections import deque
from contextlib import contextmanager
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, Iterator, Mapping

DEFAULT_TRACE_CAPACITY = 50_000
DEFAULT_HEARTBEAT_HISTORY_CAPACITY = 4_096


def _environment_enabled() -> bool:
    value = str(os.environ.get("VIBECAD_PERFORMANCE_TRACE") or "").strip().lower()
    return value in {"1", "on", "true", "yes"}


class PerformanceRecorder:
    """Keep a bounded, thread-safe stream of Chrome Trace Event records."""

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        capacity: int = DEFAULT_TRACE_CAPACITY,
        monotonic_ns: Callable[[], int] = time.perf_counter_ns,
        wall_time_ns: Callable[[], int] = time.time_ns,
        process_id: Callable[[], int] = os.getpid,
        thread_id: Callable[[], int] = threading.get_native_id,
    ) -> None:
        if type(capacity) is not int or capacity < 1:
            raise ValueError("Performance trace capacity must be a positive integer.")
        self._enabled = _environment_enabled() if enabled is None else bool(enabled)
        self._capacity = capacity
        self._monotonic_ns = monotonic_ns
        self._wall_time_ns = wall_time_ns
        self._process_id = process_id
        self._thread_id = thread_id
        self._events: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._dropped_event_count = 0
        self._lock = threading.RLock()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def dropped_event_count(self) -> int:
        with self._lock:
            return self._dropped_event_count

    def _append(self, event: dict[str, Any]) -> None:
        with self._lock:
            if len(self._events) == self._capacity:
                self._dropped_event_count += 1
            self._events.append(event)

    @contextmanager
    def span(
        self,
        name: str,
        *,
        category: str = "vibecad",
        gui_thread: bool = False,
        attributes: Mapping[str, Any] | None = None,
    ) -> Iterator[None]:
        """Measure one scope without changing its exception behavior."""

        if not self._enabled:
            yield
            return

        start_monotonic_ns = self._monotonic_ns()
        start_wall_us = self._wall_time_ns() / 1_000.0
        process_id = self._process_id()
        thread_id = self._thread_id()
        outcome = "completed"
        exception_type = ""
        try:
            yield
        except BaseException as exc:
            outcome = "failed"
            exception_type = exc.__class__.__name__
            raise
        finally:
            end_monotonic_ns = self._monotonic_ns()
            details = dict(attributes or {})
            details["gui_thread"] = bool(gui_thread)
            details["outcome"] = outcome
            if exception_type:
                details["exception_type"] = exception_type
            self.record_complete(
                name,
                category=category,
                start_wall_us=start_wall_us,
                duration_us=max(0.0, (end_monotonic_ns - start_monotonic_ns) / 1_000.0),
                process_id=process_id,
                thread_id=thread_id,
                gui_thread=gui_thread,
                attributes=details,
            )

    def record_complete(
        self,
        name: str,
        *,
        category: str = "vibecad",
        start_wall_us: float | None = None,
        duration_us: float,
        process_id: int | None = None,
        thread_id: int | None = None,
        gui_thread: bool = False,
        attributes: Mapping[str, Any] | None = None,
    ) -> None:
        if not self._enabled:
            return
        details = dict(attributes or {})
        details.setdefault("gui_thread", bool(gui_thread))
        details.setdefault("outcome", "completed")
        duration = max(0.0, float(duration_us))
        start = (
            self._wall_time_ns() / 1_000.0 - duration
            if start_wall_us is None
            else float(start_wall_us)
        )
        event = {
            "name": str(name),
            "cat": str(category),
            "ph": "X",
            "ts": start,
            "dur": duration,
            "pid": self._process_id() if process_id is None else int(process_id),
            "tid": self._thread_id() if thread_id is None else int(thread_id),
            "args": details,
        }
        self._append(event)

    def record_instant(
        self,
        name: str,
        *,
        category: str = "vibecad",
        attributes: Mapping[str, Any] | None = None,
    ) -> None:
        if not self._enabled:
            return
        self._append(
            {
                "name": str(name),
                "cat": str(category),
                "ph": "i",
                "s": "t",
                "ts": self._wall_time_ns() / 1_000.0,
                "pid": self._process_id(),
                "tid": self._thread_id(),
                "args": dict(attributes or {}),
            }
        )

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            events = list(self._events)
        return copy.deepcopy(events)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self._dropped_event_count = 0

    def export_chrome_trace(self, path: str | os.PathLike[str]) -> dict[str, Any]:
        """Write a snapshot explicitly; callers must keep this off the GUI thread."""

        target = Path(path)
        with self._lock:
            events = list(self._events)
            dropped = self._dropped_event_count
        events = copy.deepcopy(events)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "traceEvents": events,
            "metadata": {"dropped_event_count": dropped},
        }
        target.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        return {
            "event_count": len(events),
            "dropped_event_count": dropped,
            "path": str(target),
        }


class EventLoopWatchdog:
    """Measure event-loop heartbeat gaps without entering a nested event loop."""

    def __init__(
        self,
        *,
        expected_interval_ms: float,
        failure_threshold_ms: float,
        history_capacity: int = DEFAULT_HEARTBEAT_HISTORY_CAPACITY,
        recorder: PerformanceRecorder | None = None,
        monotonic_ns: Callable[[], int] = time.perf_counter_ns,
    ) -> None:
        if expected_interval_ms <= 0:
            raise ValueError("The expected heartbeat interval must be positive.")
        if failure_threshold_ms < expected_interval_ms:
            raise ValueError(
                "The heartbeat failure threshold cannot be shorter than its interval."
            )
        if type(history_capacity) is not int or history_capacity < 1:
            raise ValueError("Heartbeat history capacity must be a positive integer.")
        self._expected_interval_ms = float(expected_interval_ms)
        self._failure_threshold_ms = float(failure_threshold_ms)
        self._recorder = recorder
        self._monotonic_ns = monotonic_ns
        self._last_tick_ns: int | None = None
        self._gaps_ms: deque[float] = deque(maxlen=history_capacity)
        self._sample_count = 0
        self._violation_count = 0
        self._maximum_gap_ms = 0.0
        self._lock = threading.RLock()

    def tick(self) -> dict[str, Any] | None:
        now_ns = self._monotonic_ns()
        with self._lock:
            previous_ns = self._last_tick_ns
            self._last_tick_ns = now_ns
            if previous_ns is None:
                return None
            gap_ms = max(0.0, (now_ns - previous_ns) / 1_000_000.0)
            self._gaps_ms.append(gap_ms)
            self._sample_count += 1
            self._maximum_gap_ms = max(self._maximum_gap_ms, gap_ms)
            if gap_ms <= self._failure_threshold_ms:
                return None
            self._violation_count += 1
            violation = {
                "gap_ms": gap_ms,
                "threshold_ms": self._failure_threshold_ms,
            }
        if self._recorder is not None:
            self._recorder.record_instant(
                "gui.heartbeat_gap",
                category="ui",
                attributes=violation,
            )
        return violation

    def summary(self) -> dict[str, Any]:
        with self._lock:
            ordered_gaps = sorted(self._gaps_ms)
            p99_gap_ms = (
                ordered_gaps[(99 * len(ordered_gaps) - 1) // 100]
                if ordered_gaps
                else 0.0
            )
            return {
                "expected_interval_ms": self._expected_interval_ms,
                "failure_threshold_ms": self._failure_threshold_ms,
                "sample_count": self._sample_count,
                "retained_gap_count": len(self._gaps_ms),
                "violation_count": self._violation_count,
                "maximum_gap_ms": self._maximum_gap_ms,
                "p99_gap_ms": p99_gap_ms,
            }


_global_recorder = PerformanceRecorder()


def get_performance_recorder() -> PerformanceRecorder:
    return _global_recorder
