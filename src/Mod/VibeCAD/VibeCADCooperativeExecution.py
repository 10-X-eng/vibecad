# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared cooperative execution for thread-affine FreeCAD document work.

Heavy preparation belongs on a worker.  Live FreeCAD document access remains
on its owning thread, but a long logical operation must expose resumable steps
so Qt regains its event loop between every bounded mutation slice.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
import time
from typing import Any

DEFAULT_DOCUMENT_SLICE_BUDGET_SECONDS = 0.05
_document_thread_span_factory: Callable[..., Any] | None = None


class CooperativeExecutionCancelled(RuntimeError):
    """Cancellation injected between two atomic document-thread slices."""


def _set_document_thread_span_factory(factory: Callable[..., Any] | None) -> None:
    """Connect opt-in tracing without importing it on the normal hot path."""

    if factory is not None and not callable(factory):
        raise TypeError("A document-thread span factory must be callable or None.")
    global _document_thread_span_factory
    _document_thread_span_factory = factory


def _advance(steps: Iterator[Any]) -> tuple[bool, Any]:
    try:
        return False, next(steps)
    except StopIteration as completed:
        return True, completed.value


def _cancel(steps: Iterator[Any]) -> None:
    cancellation = CooperativeExecutionCancelled(
        "The operation was cancelled between document mutation slices."
    )
    try:
        steps.throw(cancellation)
    except (CooperativeExecutionCancelled, StopIteration):
        pass
    raise cancellation


def _emit(callback: Callable[[dict[str, Any]], None] | None, event: Any) -> None:
    if callback is None or not isinstance(event, Mapping):
        return
    callback(dict(event))


def run_document_thread_steps(
    steps: Iterator[Any],
    *,
    dispatch: Callable[[Callable[[], Any]], Any] | None,
    cancellation_check: Callable[[], bool] | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    slice_budget_seconds: float = DEFAULT_DOCUMENT_SLICE_BUDGET_SECONDS,
    trace_attributes: Mapping[str, Any] | None = None,
) -> Any:
    """Drive one resumable document job from its background owner.

    ``dispatch`` must queue one callable to the document thread and wait for
    just that callable.  It is deliberately invoked once per iterator step;
    the Qt event loop therefore regains control without re-entrant
    ``processEvents()`` calls.  A missing dispatcher preserves headless and
    legacy direct-call behavior.
    """

    if not hasattr(steps, "__next__") or not hasattr(steps, "throw"):
        raise TypeError("Document-thread steps must be a resumable iterator.")
    if dispatch is not None and not callable(dispatch):
        raise TypeError("dispatch must be callable or None")
    budget = max(0.001, float(slice_budget_seconds))
    invoke = dispatch or (lambda operation: operation())

    slice_index = 0

    def advance_or_cancel() -> tuple[bool, Any]:
        nonlocal slice_index
        # Check again inside the dispatched callable.  Cancellation can arrive
        # after a worker queues a slice but before Qt starts executing it.
        if cancellation_check is not None and cancellation_check():
            _cancel(steps)
        span_factory = _document_thread_span_factory
        if span_factory is None:
            return _advance(steps)
        attributes = dict(trace_attributes or {})
        attributes["slice_index"] = slice_index
        slice_index += 1
        with span_factory(
            "document.apply_slice",
            category="ui",
            gui_thread=True,
            attributes=attributes,
        ):
            completed, value = _advance(steps)
            if isinstance(value, Mapping):
                for key in ("phase", "completed", "total", "output_type"):
                    if key in value and isinstance(value[key], (str, int)):
                        attributes[key] = value[key]
            if completed:
                attributes["completed_operation"] = True
            return completed, value

    try:
        while True:
            if cancellation_check is not None and cancellation_check():
                invoke(lambda: _cancel(steps))
            started = clock()
            completed, value = invoke(advance_or_cancel)
            elapsed = clock() - started
            if elapsed > budget:
                _emit(
                    progress_callback,
                    {
                        "event": "document_thread_slice_over_budget",
                        "elapsed_seconds": round(elapsed, 6),
                        "budget_seconds": budget,
                    },
                )
            if completed:
                return value
            _emit(progress_callback, value)
    except CooperativeExecutionCancelled:
        # _cancel() throws through the generator on the document thread, so its
        # cleanup has already run at the only thread-safe boundary.
        raise
    except BaseException:
        # Worker-side progress, timing, or dispatch plumbing can fail after a
        # slice leaves the generator suspended.  Never let CPython finalize
        # that generator on the worker: its finally blocks release live
        # FreeCAD document guards and must execute on the document thread.
        try:
            invoke(steps.close)
        except BaseException:
            # Preserve the operation's original exception. A dispatcher which
            # can no longer reach the owning thread cannot be repaired here.
            pass
        raise
