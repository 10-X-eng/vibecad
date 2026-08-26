# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import threading

import VibeCADAnalysisRuntime as analysis_runtime
from VibeCADAnalysisRuntime import AnalysisRuntimeManager
import VibeCADNativeBackground as native_background
from VibeCADNativeBackground import NativeBackgroundManager
from vibecad_tests.test_analysis_facade_packaging import (
    assert_ci_packaged_facade_deployments,
)


def test_public_analysis_facades_ship_in_build_and_installed_trees() -> None:
    assert_ci_packaged_facade_deployments()


def test_generic_analysis_runtime_executes_prepare_validate_commit_lifecycle() -> None:
    manager = AnalysisRuntimeManager()
    calls: list[object] = []

    snapshot = manager.submit(
        document_uid="document-a",
        capability_name="analysis.example",
        prepare=lambda _cancelled, progress: (
            progress(30, "Preparing example"),
            {"prepared": True},
        )[1],
        validate_before_commit=lambda: calls.append("validate"),
        commit=lambda prepared: calls.append(prepared) or {"committed": True},
        dispatch_to_document_thread=lambda callback: callback(),
    )

    completed = manager.wait(snapshot.job_id, 2.0)
    assert completed.phase == "completed"
    assert completed.result == {"committed": True}
    assert calls == ["validate", {"prepared": True}]


def test_native_background_is_a_compatibility_facade_over_analysis_runtime() -> None:
    manager = NativeBackgroundManager()

    assert isinstance(manager, AnalysisRuntimeManager)
    assert native_background._Job is analysis_runtime._AnalysisJob

    entered = threading.Event()
    release = threading.Event()

    def prepare(cancelled, _progress):
        entered.set()
        while not release.wait(0.01):
            if cancelled():
                return {"cancelled": True}
        return {"ready": True}

    submitted = manager.submit(
        document_uid="document-a",
        capability_name="mesh.generate",
        prepare=prepare,
        validate_before_commit=lambda: None,
        commit=lambda value: value,
        dispatch_to_document_thread=lambda callback: callback(),
    )

    assert entered.wait(1.0)
    assert manager.snapshot(submitted.job_id).phase == "preparing"
    release.set()
    assert manager.wait(submitted.job_id, 2.0).result == {"ready": True}


def test_generic_runtime_keeps_atomic_cancel_commit_ordering() -> None:
    manager = AnalysisRuntimeManager()
    validation_entered = threading.Event()
    release_validation = threading.Event()
    commits: list[object] = []

    def validate() -> None:
        validation_entered.set()
        assert release_validation.wait(1.0)

    submitted = manager.submit(
        document_uid="document-a",
        capability_name="analysis.example",
        prepare=lambda _cancelled, _progress: {"ready": True},
        validate_before_commit=validate,
        commit=lambda value: commits.append(value) or {"committed": True},
        dispatch_to_document_thread=lambda callback: callback(),
    )

    assert validation_entered.wait(1.0)
    assert manager.cancel(submitted.job_id) is True
    release_validation.set()
    cancelled = manager.wait(submitted.job_id, 2.0)

    assert cancelled.phase == "cancelled"
    assert commits == []
