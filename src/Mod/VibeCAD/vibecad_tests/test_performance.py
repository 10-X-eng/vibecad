# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json

from VibeCADPerformance import EventLoopWatchdog, PerformanceRecorder
import VibeCADPerformanceGui


class _Clock:
    def __init__(self, values: list[int]):
        self._values = iter(values)

    def __call__(self) -> int:
        return next(self._values)


def test_performance_span_records_chrome_trace_fields():
    recorder = PerformanceRecorder(
        enabled=True,
        capacity=8,
        monotonic_ns=_Clock([1_000, 6_000]),
        wall_time_ns=lambda: 2_000_000,
        process_id=lambda: 17,
        thread_id=lambda: 23,
    )

    with recorder.span(
        "publication.apply",
        category="vibescript",
        gui_thread=True,
        attributes={"item_count": 3, "document_revision": "revision-a"},
    ):
        pass

    assert recorder.snapshot() == [
        {
            "name": "publication.apply",
            "cat": "vibescript",
            "ph": "X",
            "ts": 2_000.0,
            "dur": 5.0,
            "pid": 17,
            "tid": 23,
            "args": {
                "document_revision": "revision-a",
                "gui_thread": True,
                "item_count": 3,
                "outcome": "completed",
            },
        }
    ]


def test_performance_span_records_failure_without_hiding_exception():
    recorder = PerformanceRecorder(
        enabled=True,
        monotonic_ns=_Clock([10_000, 20_000]),
        wall_time_ns=lambda: 5_000_000,
    )

    try:
        with recorder.span("publication.commit", gui_thread=True):
            raise RuntimeError("commit failed")
    except RuntimeError as exc:
        assert str(exc) == "commit failed"
    else:  # pragma: no cover - this is an assertion guard
        raise AssertionError("The measured exception was swallowed.")

    event = recorder.snapshot()[0]
    assert event["args"]["outcome"] == "failed"
    assert event["args"]["exception_type"] == "RuntimeError"


def test_performance_recorder_is_bounded_and_reports_drops():
    recorder = PerformanceRecorder(enabled=True, capacity=2)
    recorder.record_complete("first", start_wall_us=1.0, duration_us=1.0)
    recorder.record_complete("second", start_wall_us=2.0, duration_us=1.0)
    recorder.record_complete("third", start_wall_us=3.0, duration_us=1.0)

    assert [event["name"] for event in recorder.snapshot()] == ["second", "third"]
    assert recorder.dropped_event_count == 1


def test_record_complete_derives_start_time_from_duration():
    recorder = PerformanceRecorder(
        enabled=True,
        wall_time_ns=lambda: 10_000_000,
    )

    recorder.record_complete("completed", duration_us=2_500.0)

    assert recorder.snapshot()[0]["ts"] == 7_500.0


def test_disabled_recorder_does_not_call_clocks_or_store_events():
    def fail_clock() -> int:
        raise AssertionError("A disabled recorder evaluated a clock.")

    recorder = PerformanceRecorder(
        enabled=False,
        monotonic_ns=fail_clock,
        wall_time_ns=fail_clock,
    )

    with recorder.span("disabled"):
        pass
    recorder.record_complete("disabled", start_wall_us=1.0, duration_us=2.0)

    assert recorder.snapshot() == []


def test_chrome_trace_export_is_explicit_and_valid(tmp_path):
    recorder = PerformanceRecorder(enabled=True)
    recorder.record_complete(
        "tree.folder_status",
        category="tree",
        start_wall_us=10.0,
        duration_us=4.5,
        process_id=7,
        thread_id=8,
        gui_thread=True,
        attributes={"folder_count": 12},
    )
    target = tmp_path / "trace.json"

    summary = recorder.export_chrome_trace(target)

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["traceEvents"] == recorder.snapshot()
    assert payload["metadata"]["dropped_event_count"] == 0
    assert summary == {"event_count": 1, "dropped_event_count": 0, "path": str(target)}


def test_event_loop_watchdog_records_only_threshold_violations():
    recorder = PerformanceRecorder(enabled=True)
    watchdog = EventLoopWatchdog(
        expected_interval_ms=25.0,
        failure_threshold_ms=100.0,
        recorder=recorder,
        monotonic_ns=_Clock([0, 25_000_000, 175_000_000, 200_000_000]),
    )

    assert watchdog.tick() is None
    assert watchdog.tick() is None
    violation = watchdog.tick()
    assert violation == {
        "gap_ms": 150.0,
        "threshold_ms": 100.0,
    }
    assert watchdog.tick() is None

    summary = watchdog.summary()
    assert summary["sample_count"] == 3
    assert summary["violation_count"] == 1
    assert summary["maximum_gap_ms"] == 150.0
    event = recorder.snapshot()[0]
    assert event["name"] == "gui.heartbeat_gap"
    assert event["ph"] == "i"
    assert event["args"] == violation


def test_event_loop_watchdog_keeps_bounded_gap_history():
    watchdog = EventLoopWatchdog(
        expected_interval_ms=10.0,
        failure_threshold_ms=50.0,
        history_capacity=2,
        monotonic_ns=_Clock([0, 10_000_000, 30_000_000, 60_000_000]),
    )

    for _ in range(4):
        watchdog.tick()

    summary = watchdog.summary()
    assert summary["sample_count"] == 3
    assert summary["retained_gap_count"] == 2
    assert summary["maximum_gap_ms"] == 30.0
    assert summary["p99_gap_ms"] == 30.0


class _Signal:
    def __init__(self):
        self.callback = None

    def connect(self, callback):
        self.callback = callback


class _Timer:
    def __init__(self, parent):
        self.parent = parent
        self.timeout = _Signal()
        self.interval_ms = None
        self.started = False

    def setInterval(self, interval_ms):
        self.interval_ms = interval_ms

    def start(self):
        self.started = True


class _Watchdog:
    def __init__(self):
        self.ticks = 0

    def tick(self):
        self.ticks += 1

    def summary(self):
        return {"sample_count": self.ticks}


def test_gui_watchdog_is_not_installed_when_tracing_is_disabled(monkeypatch):
    class _DisabledRecorder:
        enabled = False

    VibeCADPerformanceGui._reset_event_loop_watchdog_for_tests()
    monkeypatch.setattr(
        VibeCADPerformanceGui,
        "get_performance_recorder",
        lambda: _DisabledRecorder(),
    )

    def fail_timer_factory(_parent):
        raise AssertionError("A disabled watchdog allocated a timer.")

    assert (
        VibeCADPerformanceGui.install_event_loop_watchdog(
            parent="main-window",
            timer_factory=fail_timer_factory,
        )
        is None
    )
    assert VibeCADPerformanceGui.event_loop_watchdog_summary() == {
        "installed": False,
        "sample_count": 0,
    }


def test_gui_watchdog_installation_is_idempotent():
    VibeCADPerformanceGui._reset_event_loop_watchdog_for_tests()
    watchdog = _Watchdog()
    created = []

    def timer_factory(parent):
        timer = _Timer(parent)
        created.append(timer)
        return timer

    first = VibeCADPerformanceGui.install_event_loop_watchdog(
        parent="main-window",
        timer_factory=timer_factory,
        watchdog=watchdog,
    )
    second = VibeCADPerformanceGui.install_event_loop_watchdog(
        parent="ignored",
        timer_factory=timer_factory,
        watchdog=_Watchdog(),
    )

    assert first is second
    assert len(created) == 1
    assert first.parent == "main-window"
    assert first.interval_ms == 25
    assert first.started is True
    first.timeout.callback()
    assert watchdog.ticks == 1
    assert VibeCADPerformanceGui.event_loop_watchdog_summary() == {"sample_count": 1}
    VibeCADPerformanceGui._reset_event_loop_watchdog_for_tests()
