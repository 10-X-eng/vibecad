# SPDX-License-Identifier: LGPL-2.1-or-later

"""Contracts for bounded, cooperative document-thread execution."""

from __future__ import annotations

import pytest

import VibeCADCooperativeExecution as cooperative
from VibeCADCooperativeExecution import (
    CooperativeExecutionCancelled,
    run_document_thread_steps,
)
from VibeCADPerformance import PerformanceRecorder


def test_document_steps_return_to_dispatcher_between_every_slice() -> None:
    dispatches = []
    progress = []

    def steps():
        yield {"completed": 1, "total": 3}
        yield {"completed": 2, "total": 3}
        yield {"completed": 3, "total": 3}
        return {"ok": True, "published": 3}

    def dispatch(operation):
        dispatches.append(operation)
        return operation()

    result = run_document_thread_steps(
        steps(),
        dispatch=dispatch,
        progress_callback=progress.append,
    )

    assert result == {"ok": True, "published": 3}
    assert len(dispatches) == 4
    assert [event["completed"] for event in progress] == [1, 2, 3]


def test_document_steps_cancel_by_throwing_on_the_document_thread() -> None:
    dispatches = []
    finalized = []
    cancellation_checks = 0

    def steps():
        try:
            yield {"completed": 1, "total": 2}
            yield {"completed": 2, "total": 2}
            return {"ok": True}
        finally:
            finalized.append(True)

    def dispatch(operation):
        dispatches.append(operation)
        return operation()

    def cancelled() -> bool:
        nonlocal cancellation_checks
        cancellation_checks += 1
        return cancellation_checks > 2

    with pytest.raises(CooperativeExecutionCancelled):
        run_document_thread_steps(
            steps(),
            dispatch=dispatch,
            cancellation_check=cancelled,
        )

    assert finalized == [True]
    assert len(dispatches) == 2


def test_document_steps_report_any_slice_that_exceeds_its_budget() -> None:
    now = iter((0.0, 0.08, 0.08, 0.09))
    progress = []

    def steps():
        yield {"completed": 1, "total": 1}
        return {"ok": True}

    result = run_document_thread_steps(
        steps(),
        dispatch=lambda operation: operation(),
        progress_callback=progress.append,
        clock=lambda: next(now),
        slice_budget_seconds=0.05,
    )

    assert result == {"ok": True}
    assert progress[0]["event"] == "document_thread_slice_over_budget"
    assert progress[0]["elapsed_seconds"] == 0.08
    assert progress[1]["completed"] == 1


def test_document_steps_emit_opt_in_gui_spans_with_operation_identity(
    monkeypatch,
) -> None:
    recorder = PerformanceRecorder(enabled=True, capacity=10)
    monkeypatch.setattr(cooperative, "_document_thread_span_factory", recorder.span)

    def steps():
        yield {"phase": "publication_objects", "completed": 1, "total": 1}
        return {"ok": True}

    result = run_document_thread_steps(
        steps(),
        dispatch=lambda operation: operation(),
        trace_attributes={
            "operation_id": "job-1",
            "document_uid": "document-a",
            "capability": "vibescript.publish",
        },
    )

    assert result == {"ok": True}
    events = recorder.snapshot()
    assert [event["name"] for event in events] == [
        "document.apply_slice",
        "document.apply_slice",
    ]
    assert [event["args"]["slice_index"] for event in events] == [0, 1]
    assert events[0]["args"] == {
        "operation_id": "job-1",
        "document_uid": "document-a",
        "capability": "vibescript.publish",
        "slice_index": 0,
        "phase": "publication_objects",
        "completed": 1,
        "total": 1,
        "gui_thread": True,
        "outcome": "completed",
    }
    assert events[1]["args"]["completed_operation"] is True
    assert events[1]["args"]["gui_thread"] is True


def test_progress_failure_closes_steps_on_the_document_thread() -> None:
    dispatch_active = False
    finalized = []

    def steps():
        try:
            yield {"completed": 1, "total": 2}
            yield {"completed": 2, "total": 2}
        finally:
            finalized.append(dispatch_active)

    def dispatch(operation):
        nonlocal dispatch_active
        assert dispatch_active is False
        dispatch_active = True
        try:
            return operation()
        finally:
            dispatch_active = False

    def reject_progress(_event) -> None:
        raise RuntimeError("progress callback failed")

    with pytest.raises(RuntimeError, match="progress callback failed"):
        run_document_thread_steps(
            steps(),
            dispatch=dispatch,
            progress_callback=reject_progress,
        )

    assert finalized == [True]
