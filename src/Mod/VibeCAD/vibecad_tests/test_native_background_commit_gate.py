# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import threading

import VibeCADNativeBackground as background_module
from VibeCADNativeBackground import NativeBackgroundCancelled, NativeBackgroundManager


def _install_waiting_job(manager: NativeBackgroundManager, job_id: str):
    job = background_module._Job(
        job_id=job_id,
        document_uid=f"document-{job_id}",
        capability_name="analyze.solve",
        phase="waiting_to_commit",
        progress_percent=90,
        progress_message="Waiting to commit",
    )
    with manager._lock:
        manager._jobs[job_id] = job
        manager._active_documents[job.document_uid] = job_id
    return job


def test_atomic_commit_gate_orders_racing_cancel_against_commit_claim() -> None:
    """An accepted cancel and a successful commit claim may never coexist."""

    for attempt in range(64):
        manager = NativeBackgroundManager()
        job_id = f"race-{attempt}"
        job = _install_waiting_job(manager, job_id)
        barrier = threading.Barrier(3)
        outcome: dict[str, object] = {}

        def claim_commit() -> None:
            barrier.wait()
            try:
                manager._enter_commit_gate(
                    job,
                    "committing",
                    95,
                    "Committing document change",
                )
            except NativeBackgroundCancelled:
                outcome["claim"] = "cancelled"
            else:
                outcome["claim"] = "committing"

        def request_cancel() -> None:
            barrier.wait()
            outcome["cancel_accepted"] = manager.cancel(job_id)

        claim_thread = threading.Thread(target=claim_commit)
        cancel_thread = threading.Thread(target=request_cancel)
        claim_thread.start()
        cancel_thread.start()
        barrier.wait()
        claim_thread.join(1.0)
        cancel_thread.join(1.0)

        assert not claim_thread.is_alive()
        assert not cancel_thread.is_alive()
        accepted = outcome["cancel_accepted"]
        claim = outcome["claim"]
        if accepted:
            assert claim == "cancelled"
            assert job.cancellation.is_set()
            assert job.phase != "committing"
        else:
            assert claim == "committing"
            assert job.phase == "committing"
            assert not job.cancellation.is_set()


def test_cancel_during_validation_prevents_document_mutation() -> None:
    manager = NativeBackgroundManager()
    validation_entered = threading.Event()
    release_validation = threading.Event()
    commits: list[object] = []

    def validate() -> None:
        validation_entered.set()
        assert release_validation.wait(1.0)

    submitted = manager.submit(
        document_uid="document-a",
        capability_name="analyze.solve",
        prepare=lambda _cancelled, _progress: {"solution": "ready"},
        validate_before_commit=validate,
        commit=lambda value: commits.append(value) or {"committed": True},
        dispatch_to_document_thread=lambda callback: callback(),
    )

    assert validation_entered.wait(1.0)
    assert manager.cancel(submitted.job_id) is True
    release_validation.set()

    cancelled = manager.wait(submitted.job_id, 2.0)
    assert cancelled.phase == "cancelled"
    assert cancelled.error is not None
    assert cancelled.error["error_code"] == "NATIVE_BACKGROUND_CANCELLED"
    assert commits == []


def test_commit_claim_makes_late_cancel_non_cancellable() -> None:
    manager = NativeBackgroundManager()
    commit_entered = threading.Event()
    release_commit = threading.Event()

    def commit(_value):
        commit_entered.set()
        assert release_commit.wait(1.0)
        return {"committed": True}

    submitted = manager.submit(
        document_uid="document-a",
        capability_name="analyze.solve",
        prepare=lambda _cancelled, _progress: {"solution": "ready"},
        validate_before_commit=lambda: None,
        commit=commit,
        dispatch_to_document_thread=lambda callback: callback(),
    )

    assert commit_entered.wait(1.0)
    snapshot = manager.snapshot(submitted.job_id)
    assert snapshot.phase == "committing"
    assert manager.cancel(submitted.job_id) is False
    release_commit.set()

    completed = manager.wait(submitted.job_id, 2.0)
    assert completed.phase == "completed"
    assert completed.result == {"committed": True}
