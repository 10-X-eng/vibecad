# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from VibeCADAnalysisRuntime import AnalysisRuntimeError, AnalysisRuntimeManager
import VibeCADNativeAnalyzeSolverExecutionInput as solver_input


def _submit_waiting_job(
    manager: AnalysisRuntimeManager,
    *,
    release: threading.Event,
    entered: threading.Event,
    cleanup=None,
):
    def prepare(_cancelled, _progress):
        entered.set()
        assert release.wait(2.0)
        return {"ready": True}

    return manager.submit(
        document_uid="fem-lifecycle-document",
        capability_name="analyze.solver_execution.run",
        prepare=prepare,
        validate_before_commit=lambda: None,
        commit=lambda _prepared: {"committed": True},
        dispatch_to_document_thread=lambda callback: callback(),
        cleanup=cleanup,
        changes_document=True,
    )


def test_terminal_cleanup_cannot_release_a_new_document_owner() -> None:
    manager = AnalysisRuntimeManager(thread_name_prefix="VibeCADFEMBurnIn")
    first_prepare = threading.Event()
    release_first = threading.Event()
    first_cleanup = threading.Event()
    release_first_cleanup = threading.Event()

    def block_first_cleanup(_prepared) -> None:
        first_cleanup.set()
        assert release_first_cleanup.wait(2.0)

    first = _submit_waiting_job(
        manager,
        release=release_first,
        entered=first_prepare,
        cleanup=block_first_cleanup,
    )
    assert first_prepare.wait(1.0)
    release_first.set()
    assert first_cleanup.wait(1.0)
    assert manager.snapshot(first.job_id).phase == "completed"

    second_prepare = threading.Event()
    release_second = threading.Event()
    second = _submit_waiting_job(
        manager,
        release=release_second,
        entered=second_prepare,
    )
    assert second_prepare.wait(1.0)

    release_first_cleanup.set()
    assert manager.wait(first.job_id, 2.0).phase == "completed"

    third_prepare = threading.Event()
    release_third = threading.Event()
    unexpected_third = None
    try:
        with pytest.raises(AnalysisRuntimeError, match="already has"):
            unexpected_third = _submit_waiting_job(
                manager,
                release=release_third,
                entered=third_prepare,
            )
    finally:
        release_second.set()
        if unexpected_third is not None:
            release_third.set()
            manager.wait(unexpected_third.job_id, 2.0)
        manager.wait(second.job_id, 2.0)


@pytest.mark.parametrize(
    "outcome",
    ["completed", "failed", "cancelled"],
)
def test_repeated_terminal_paths_leave_no_job_thread_or_owner_leak(
    outcome: str,
) -> None:
    prefix = f"VibeCADFEMBurnIn-{outcome}"
    manager = AnalysisRuntimeManager(
        maximum_jobs=6,
        thread_name_prefix=prefix,
    )
    cleaned: list[int] = []
    committed: list[int] = []

    for attempt in range(18):
        entered = threading.Event()

        def prepare(cancelled, _progress, *, current=attempt):
            entered.set()
            if outcome == "failed":
                raise RuntimeError(f"representative FEM failure {current}")
            if outcome == "cancelled":
                while not cancelled():
                    time.sleep(0.001)
            return {"attempt": current}

        def commit(prepared):
            committed.append(prepared["attempt"])
            return {"attempt": prepared["attempt"]}

        submitted = manager.submit(
            document_uid="fem-lifecycle-document",
            capability_name="analyze.solver_execution.run",
            prepare=prepare,
            validate_before_commit=lambda: None,
            commit=commit,
            dispatch_to_document_thread=lambda callback: callback(),
            cleanup=lambda _prepared, current=attempt: cleaned.append(current),
            changes_document=True,
        )
        assert entered.wait(1.0)
        if outcome == "cancelled":
            assert manager.cancel(submitted.job_id) is True
        terminal = manager.wait(submitted.job_id, 2.0)
        assert terminal.phase == outcome
        assert terminal.worker_active is False
        with manager._lock:
            assert "fem-lifecycle-document" not in manager._active_documents
            assert len(manager._jobs) <= 6

    deadline = time.monotonic() + 2.0
    while any(
        thread.name.startswith(prefix) for thread in threading.enumerate()
    ) and time.monotonic() < deadline:
        time.sleep(0.005)

    assert cleaned == list(range(18))
    assert committed == (list(range(18)) if outcome == "completed" else [])
    assert not any(
        thread.name.startswith(prefix) for thread in threading.enumerate()
    )


def test_repeated_fem_workspaces_are_removed_after_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen = SimpleNamespace(path="frozen", size_bytes=1, sha256="a" * 64)
    monkeypatch.setattr(solver_input, "freeze_regular_file", lambda *_a, **_k: frozen)
    monkeypatch.setattr(solver_input, "resolve_freecadcmd", lambda: frozen)

    cleaned_paths = []
    for attempt in range(18):
        workspace = solver_input.create_solver_execution_workspace()
        path = workspace.path
        (path / f"case-{attempt}").mkdir()
        (path / f"case-{attempt}" / "bounded.log").write_text(
            "representative FEM output",
            encoding="utf-8",
        )
        assert path.is_dir()
        workspace.cleanup()
        cleaned_paths.append(path)

    assert len(set(cleaned_paths)) == 18
    assert all(not path.exists() for path in cleaned_paths)
