# SPDX-License-Identifier: LGPL-2.1-or-later

"""Qt event-loop heartbeat measurement for VibeCAD.

The timer deliberately does not enter a nested event loop or perform file I/O.
When Qt is starved, its next ordinary timeout measures the entire starvation gap.
"""

from __future__ import annotations

from typing import Any, Callable

from VibeCADPerformance import (
    EventLoopWatchdog,
    get_performance_recorder,
)

HEARTBEAT_INTERVAL_MS = 25
HEARTBEAT_FAILURE_THRESHOLD_MS = 100

_event_loop_timer: Any | None = None
_event_loop_watchdog: Any | None = None


def install_event_loop_watchdog(
    *,
    parent: Any | None = None,
    timer_factory: Callable[[Any], Any] | None = None,
    watchdog: Any | None = None,
) -> Any:
    """Install exactly one low-overhead heartbeat on FreeCAD's Qt loop."""

    global _event_loop_timer, _event_loop_watchdog
    if _event_loop_timer is not None:
        return _event_loop_timer

    recorder = get_performance_recorder()
    if watchdog is None and not recorder.enabled:
        return None

    if parent is None:
        import FreeCADGui as Gui

        parent = Gui.getMainWindow()
    if parent is None:
        raise RuntimeError("FreeCAD's main window is unavailable for the UI watchdog.")

    if timer_factory is None:
        from PySide import QtCore

        timer_factory = QtCore.QTimer
    if watchdog is None:
        watchdog = EventLoopWatchdog(
            expected_interval_ms=HEARTBEAT_INTERVAL_MS,
            failure_threshold_ms=HEARTBEAT_FAILURE_THRESHOLD_MS,
            recorder=recorder,
        )

    timer = timer_factory(parent)
    timer.setInterval(HEARTBEAT_INTERVAL_MS)
    timer.timeout.connect(watchdog.tick)
    timer.start()
    _event_loop_watchdog = watchdog
    _event_loop_timer = timer
    return timer


def event_loop_watchdog_summary() -> dict[str, Any]:
    if _event_loop_watchdog is None:
        return {
            "installed": False,
            "sample_count": 0,
        }
    return dict(_event_loop_watchdog.summary())


def _reset_event_loop_watchdog_for_tests() -> None:
    global _event_loop_timer, _event_loop_watchdog
    timer = _event_loop_timer
    _event_loop_timer = None
    _event_loop_watchdog = None
    stop = getattr(timer, "stop", None)
    if callable(stop):
        stop()
