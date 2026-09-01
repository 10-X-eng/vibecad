# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import threading
import time

import pytest

import VibeCADNativeBackground as background_module
from VibeCADNativeBackground import (
    NativeBackgroundError,
    NativeBackgroundManager,
)
from VibeCADNativeBackgroundRuntime import _summary


def _callbacks(*, prepare, validate=lambda: None, commit=lambda value: value):
    return {
        "document_uid": "document-a",
        "capability_name": "mesh.generate",
        "prepare": prepare,
        "validate_before_commit": validate,
        "commit": commit,
        "dispatch_to_document_thread": lambda callback: callback(),
    }


def _wait_phase(manager, job_id: str, phase: str, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = manager.snapshot(job_id)
        if snapshot.phase == phase or snapshot.terminal:
            return snapshot
        time.sleep(0.005)
    raise AssertionError(f"job did not reach {phase}")


def test_submit_returns_while_detached_preparation_is_running() -> None:
    manager = NativeBackgroundManager()
    entered = threading.Event()
    release = threading.Event()

    def prepare(cancelled, progress):
        entered.set()
        progress(20, "Meshing detached geometry")
        while not release.wait(0.01):
            if cancelled():
                return {"cancelled": True}
        return {"mesh": "ready"}

    submitted = manager.submit(**_callbacks(prepare=prepare))

    assert entered.wait(1.0)
    assert manager.snapshot(submitted.job_id).phase == "preparing"
    release.set()
    completed = manager.wait(submitted.job_id, 2.0)
    assert completed.phase == "completed"
    assert completed.result == {"mesh": "ready"}


def test_running_job_summary_tells_provider_to_wait_without_inferring_a_hang() -> None:
    manager = NativeBackgroundManager()
    entered = threading.Event()
    release = threading.Event()

    def prepare(_cancelled, progress):
        progress(20, "Generating CalculiX input deck")
        entered.set()
        release.wait(1.0)
        return {"deck": "ready"}

    submitted = manager.submit(**_callbacks(prepare=prepare))
    assert entered.wait(1.0)
    summary = _summary(manager.snapshot(submitted.job_id))
    release.set()
    manager.wait(submitted.job_id, 2.0)

    assert summary["worker_state"] == "active"
    assert summary["recommended_poll_seconds"] >= 10
    assert summary["elapsed_seconds"] >= 0
    assert summary["seconds_since_progress"] >= 0
    assert summary["guidance"] == (
        "Continue waiting. Do not cancel an active job solely because its percent "
        "is unchanged."
    )


def test_completed_mutating_job_reports_its_document_change() -> None:
    manager = NativeBackgroundManager()
    entered = threading.Event()
    release = threading.Event()

    def prepare(_cancelled, _progress):
        entered.set()
        release.wait(1.0)
        return {"mesh": "ready"}

    submitted = manager.submit(
        **_callbacks(prepare=prepare),
        changes_document=True,
    )
    assert entered.wait(1.0)
    assert submitted.document_changed is False
    release.set()
    completed = manager.wait(submitted.job_id, 2.0)

    assert completed.document_changed is True


def test_completed_noop_resolves_to_no_document_change() -> None:
    manager = NativeBackgroundManager()
    submitted = manager.submit(
        **_callbacks(
            prepare=lambda _cancelled, _progress: {"prepared": True},
            commit=lambda _value: {"changed": False},
        ),
        changes_document=True,
        document_change_resolver=lambda result: bool(result["changed"]),
    )

    completed = manager.wait(submitted.job_id, 2.0)

    assert completed.phase == "completed"
    assert completed.document_changed is False


def test_cooperative_cancel_never_dispatches_a_commit() -> None:
    manager = NativeBackgroundManager()
    entered = threading.Event()
    commits = []

    def prepare(cancelled, progress):
        entered.set()
        progress(10, "Preparing")
        while not cancelled():
            time.sleep(0.005)
        return {"must_not_commit": True}

    submitted = manager.submit(
        **_callbacks(prepare=prepare, commit=lambda value: commits.append(value))
    )
    assert entered.wait(1.0)
    assert manager.cancel(submitted.job_id) is True

    cancelled = manager.wait(submitted.job_id, 2.0)
    assert cancelled.phase == "cancelled"
    assert cancelled.error["error_code"] == "NATIVE_BACKGROUND_CANCELLED"
    assert commits == []


def test_cancel_while_waiting_for_document_thread_skips_commit() -> None:
    manager = NativeBackgroundManager()
    dispatcher_entered = threading.Event()
    allow_dispatch = threading.Event()
    commits = []

    def dispatch(callback):
        dispatcher_entered.set()
        assert allow_dispatch.wait(1.0)
        return callback()

    submitted = manager.submit(
        document_uid="document-a",
        capability_name="analyze.solve",
        prepare=lambda _cancelled, _progress: {"solution": "ready"},
        validate_before_commit=lambda: None,
        commit=lambda value: commits.append(value),
        dispatch_to_document_thread=dispatch,
    )
    assert dispatcher_entered.wait(1.0)
    assert manager.cancel(submitted.job_id) is True
    allow_dispatch.set()

    cancelled = manager.wait(submitted.job_id, 2.0)
    assert cancelled.phase == "cancelled"
    assert commits == []


def test_surface_or_document_validation_failure_prevents_commit() -> None:
    class SurfaceChanged(RuntimeError):
        def failure(self):
            return {
                "error_code": "NATIVE_SURFACE_CHANGED",
                "message": "Resume from the current ribbon.",
                "current_surface": "mesh",
                "repair": {"resume_next_turn": True},
                "noisy_internal_field": "must not escape",
            }

    manager = NativeBackgroundManager()
    commits = []

    def validate():
        raise SurfaceChanged()

    submitted = manager.submit(
        **_callbacks(
            prepare=lambda _cancelled, _progress: {"mesh": "ready"},
            validate=validate,
            commit=lambda value: commits.append(value),
        )
    )
    failed = manager.wait(submitted.job_id, 2.0)

    assert failed.phase == "failed"
    assert failed.error == {
        "error_code": "NATIVE_SURFACE_CHANGED",
        "message": "Resume from the current ribbon.",
        "current_surface": "mesh",
        "repair": {"resume_next_turn": True},
    }
    assert commits == []


def test_long_background_failure_keeps_the_actionable_tail() -> None:
    class SolverFailed(RuntimeError):
        def failure(self):
            return {
                "error_code": "NATIVE_ANALYZE_SOLVER_BACKEND_FAILED",
                "message": (
                    "Openfoam stage 1 exited with code 1: "
                    + "banner " * 80
                    + "FATAL: patch Face2 is missing from the mesh"
                ),
            }

    manager = NativeBackgroundManager()
    submitted = manager.submit(
        **_callbacks(
            prepare=lambda _cancelled, _progress: (_ for _ in ()).throw(
                SolverFailed()
            )
        )
    )
    failed = manager.wait(submitted.job_id, 2.0)

    assert len(failed.error["message"]) <= 320
    assert failed.error["message"].startswith("Openfoam stage 1 exited")
    assert failed.error["message"].endswith(
        "FATAL: patch Face2 is missing from the mesh"
    )


def test_cleanup_receives_prepared_value_even_when_commit_validation_fails() -> None:
    manager = NativeBackgroundManager()
    cleaned = []

    submitted = manager.submit(
        **_callbacks(
            prepare=lambda _cancelled, _progress: {"artifact": "detached"},
            validate=lambda: (_ for _ in ()).throw(RuntimeError("stale")),
        ),
        cleanup=cleaned.append,
    )
    failed = manager.wait(submitted.job_id, 2.0)

    assert failed.phase == "failed"
    assert cleaned == [{"artifact": "detached"}]


def test_frozen_turn_change_during_preparation_prevents_commit(monkeypatch) -> None:
    import VibeCADNativeActionManifest as action_manifest_module
    from VibeCADNativeSurface import SURFACE_CHANGED
    from VibeCADNativeTurn import freeze_native_turn, require_frozen_native_turn
    from vibecad_tests.test_native_capability_registry import (
        _focused_inventory_by_surface,
        _register_complete,
    )
    from vibecad_tests.test_ribbon_surface import _Controller, _manifest

    controller = _Controller(_manifest(), revision=6)
    monkeypatch.setattr(
        action_manifest_module,
        "KNOWN_ACTIONS_BY_SURFACE",
        _focused_inventory_by_surface(),
    )
    registry = _register_complete()
    frozen = freeze_native_turn(controller, registry)
    release = threading.Event()
    commits = []

    def prepare(_cancelled, _progress):
        release.wait(1.0)
        return {"mesh": "ready"}

    manager = NativeBackgroundManager()
    submitted = manager.submit(
        document_uid="document-a",
        capability_name="mesh.generate",
        prepare=prepare,
        validate_before_commit=lambda: require_frozen_native_turn(
            frozen,
            controller,
            registry,
        ),
        commit=lambda value: commits.append(value),
        dispatch_to_document_thread=lambda callback: callback(),
    )
    controller.values["VibeCADActiveSurfaceRevision"] = 7
    release.set()
    failed = manager.wait(submitted.job_id, 2.0)

    assert failed.phase == "failed"
    assert failed.error["error_code"] == SURFACE_CHANGED
    assert commits == []


def test_close_document_cancels_its_active_preparation() -> None:
    manager = NativeBackgroundManager()
    entered = threading.Event()

    def prepare(cancelled, _progress):
        entered.set()
        while not cancelled():
            time.sleep(0.005)
        return {}

    submitted = manager.submit(**_callbacks(prepare=prepare))
    assert entered.wait(1.0)

    assert manager.cancel_document("document-a") is True
    assert manager.wait(submitted.job_id, 2.0).phase == "cancelled"


def test_only_one_background_operation_owns_a_document() -> None:
    manager = NativeBackgroundManager()
    release = threading.Event()

    def prepare(_cancelled, _progress):
        release.wait(1.0)
        return {"ready": True}

    submitted = manager.submit(**_callbacks(prepare=prepare))

    with pytest.raises(NativeBackgroundError, match="already has"):
        manager.submit(**_callbacks(prepare=lambda _cancelled, _progress: {}))

    release.set()
    manager.wait(submitted.job_id, 2.0)


def test_independent_resource_scopes_can_prepare_in_one_document() -> None:
    manager = NativeBackgroundManager()
    entered_a = threading.Event()
    entered_b = threading.Event()
    release = threading.Event()

    def prepare(entered):
        def run(_cancelled, _progress):
            entered.set()
            release.wait(1.0)
            return {"ready": True}

        return run

    first = manager.submit(
        **_callbacks(prepare=prepare(entered_a)),
        resource_scope="manufacture:SetupA",
    )
    second = manager.submit(
        **_callbacks(prepare=prepare(entered_b)),
        resource_scope="manufacture:SetupB",
    )

    assert entered_a.wait(1.0) and entered_b.wait(1.0)
    with pytest.raises(NativeBackgroundError, match="resource already has"):
        manager.submit(
            **_callbacks(prepare=lambda _cancelled, _progress: {}),
            resource_scope="manufacture:SetupA",
        )
    release.set()
    assert manager.wait(first.job_id, 2.0).phase == "completed"
    assert manager.wait(second.job_id, 2.0).phase == "completed"


def test_document_job_catalog_preserves_every_active_resource_scope() -> None:
    manager = NativeBackgroundManager()
    release = threading.Event()

    def prepare(_cancelled, _progress):
        release.wait(1.0)
        return {"ready": True}

    first = manager.submit(
        **_callbacks(prepare=prepare),
        resource_scope="manufacture:SetupA",
    )
    second = manager.submit(
        **_callbacks(prepare=prepare),
        resource_scope="manufacture:SetupB",
    )

    snapshots = manager.document_snapshots(
        "document-a",
        capability_prefix="mesh.",
        active_only=True,
    )
    assert {snapshot.job_id for snapshot in snapshots} == {
        first.job_id,
        second.job_id,
    }
    assert {snapshot.resource_scope for snapshot in snapshots} == {
        "manufacture:SetupA",
        "manufacture:SetupB",
    }
    release.set()
    manager.wait(first.job_id, 2.0)
    manager.wait(second.job_id, 2.0)


def test_document_scoped_work_conflicts_with_every_resource_scope() -> None:
    manager = NativeBackgroundManager()
    release = threading.Event()

    document_job = manager.submit(
        **_callbacks(
            prepare=lambda _cancelled, _progress: release.wait(1.0) or {}
        )
    )
    with pytest.raises(NativeBackgroundError, match="already has"):
        manager.submit(
            **_callbacks(prepare=lambda _cancelled, _progress: {}),
            resource_scope="manufacture:SetupA",
        )
    release.set()
    manager.wait(document_job.job_id, 2.0)

    release.clear()
    resource_job = manager.submit(
        **_callbacks(
            prepare=lambda _cancelled, _progress: release.wait(1.0) or {}
        ),
        resource_scope="manufacture:SetupA",
    )
    with pytest.raises(NativeBackgroundError, match="already has"):
        manager.submit(
            **_callbacks(prepare=lambda _cancelled, _progress: {}),
        )
    manager.cancel(resource_job.job_id)
    manager.wait(resource_job.job_id, 2.0)


def test_result_and_progress_contracts_are_bounded(monkeypatch) -> None:
    monkeypatch.setattr(background_module, "MAX_BACKGROUND_RESULT_BYTES", 20)
    manager = NativeBackgroundManager()
    def prepare(_cancelled, progress):
        progress(30, "x" * 500)
        return {"ready": True}

    submitted = manager.submit(
        **_callbacks(
            prepare=prepare,
            commit=lambda _value: {"payload": "too large for bound"},
        )
    )
    failed = manager.wait(submitted.job_id, 2.0)

    assert failed.phase == "failed"
    assert failed.error["error_code"] == "NATIVE_BACKGROUND_FAILED"
    assert len(failed.progress_message) <= 160


def test_progress_cannot_move_backwards() -> None:
    manager = NativeBackgroundManager()

    def prepare(_cancelled, progress):
        progress(40, "First")
        progress(20, "Backwards")
        return {}

    submitted = manager.submit(**_callbacks(prepare=prepare))
    failed = manager.wait(submitted.job_id, 2.0)

    assert failed.phase == "failed"
    assert failed.error["error_code"] == "NATIVE_BACKGROUND_FAILED"
