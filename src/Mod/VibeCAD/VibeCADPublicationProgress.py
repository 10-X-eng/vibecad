# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded publication progress for cooperative document mutation slices."""

from __future__ import annotations

import time
from typing import Any, Callable


class PublicationProgress:
    """Report monotonic output progress and bound GUI event-loop starvation."""

    def __init__(
        self,
        *,
        domain: str,
        total: int,
        callback: Callable[[dict[str, Any]], None] | None = None,
        event_yield: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        yield_interval_seconds: float = 0.05,
    ) -> None:
        self._domain = str(domain or "")
        self._total = max(0, int(total))
        self._callback = callback
        # Cooperative callers return to Qt between document slices.  Keep the
        # explicit hook for compatible headless tests and legacy callers, but
        # never enter a nested Qt event loop implicitly.
        self._event_yield = event_yield
        self._clock = clock
        self._yield_interval = max(0.01, float(yield_interval_seconds))
        self._last_completed = 0
        self._last_yield = 0.0

    def _emit(
        self,
        *,
        completed: int,
        phase: str,
        name: str = "",
        output_type: str = "",
    ) -> None:
        if self._callback is None:
            return
        self._callback(
            {
                "event": "vibescript_domain_publication_progress",
                "domain": self._domain,
                "phase": phase,
                "completed": completed,
                "total": self._total,
                "current_output": str(name or ""),
                "output_type": str(output_type or ""),
            }
        )

    def _yield_if_due(self, now: float) -> None:
        if self._event_yield is None:
            return
        if now - self._last_yield < self._yield_interval:
            return
        self._event_yield()
        # A Qt pass can itself take measurable time. Measure the next interval
        # from the completed pass so one slow callback cannot make every
        # following output immediately re-enter the event loop.
        self._last_yield = self._clock()

    def start(self) -> None:
        now = self._clock()
        self._last_yield = now
        self._emit(completed=0, phase="publishing")

    def checkpoint(self, completed: int, *, name: str, output_type: str) -> None:
        now = self._clock()
        bounded = min(self._total, max(0, int(completed)))
        if bounded > self._last_completed:
            self._last_completed = bounded
            self._emit(
                completed=bounded,
                phase="publishing",
                name=name,
                output_type=output_type,
            )
        self._yield_if_due(now)

    def finish(self) -> None:
        now = self._clock()
        self._last_completed = self._total
        self._emit(completed=self._total, phase="completed")
        self._yield_if_due(now)

    def fail(self) -> None:
        now = self._clock()
        self._emit(completed=self._last_completed, phase="failed")
        self._yield_if_due(now)
