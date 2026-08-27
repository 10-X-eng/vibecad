# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import threading
from pathlib import Path

import VibeCADAnalysisRuntime as analysis_runtime
from VibeCADAnalysisRuntime import AnalysisRuntimeManager
from VibeCADAnalysisPersistence import AnalysisMetadataStore, DurableRuntimeLifecycle
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


def _durable_lifecycle(tmp_path: Path) -> tuple[AnalysisMetadataStore, DurableRuntimeLifecycle]:
    store = AnalysisMetadataStore(tmp_path)
    lifecycle = DurableRuntimeLifecycle(
        store,
        domain="fem",
        adapter_id="fixture-adapter",
        prepared_analysis_sha256="a" * 64,
        dependency_sha256="b" * 64,
        input_manifest_sha256="c" * 64,
        execution_spec_sha256="d" * 64,
    )
    return store, lifecycle


def test_opt_in_durable_runtime_records_exact_success_lifecycle(tmp_path: Path) -> None:
    store, lifecycle = _durable_lifecycle(tmp_path)
    manager = AnalysisRuntimeManager()
    submitted = manager.submit(
        document_uid="document-a",
        capability_name="analysis.example",
        prepare=lambda _cancelled, _progress: {"ready": True},
        validate_before_commit=lambda: None,
        commit=lambda _prepared: {"object": "Result"},
        dispatch_to_document_thread=lambda callback: callback(),
        durable_lifecycle=lifecycle,
    )
    completed = manager.wait(submitted.job_id, 2.0)
    record = store.load(submitted.job_id)

    assert completed.phase == "completed"
    assert record["state"] == "succeeded"
    assert record["analysis_id"] == submitted.job_id
    assert record["source_document_uid"] == "document-a"
    assert [event["state"] for event in record["events"]] == [
        "prepared", "running_local", "collecting", "verifying",
        "waiting_to_publish", "publishing", "succeeded",
    ]
    assert record["publication"]["receipt"]["compatibility_mode"] == (
        "legacy_inline_publication"
    )


def test_opt_in_durable_runtime_records_cancel_without_publication(tmp_path: Path) -> None:
    store, lifecycle = _durable_lifecycle(tmp_path)
    manager = AnalysisRuntimeManager()
    entered = threading.Event()

    def prepare(cancelled, _progress):
        entered.set()
        while not cancelled():
            threading.Event().wait(0.01)
        return None

    submitted = manager.submit(
        document_uid="document-a",
        capability_name="analysis.example",
        prepare=prepare,
        validate_before_commit=lambda: None,
        commit=lambda _prepared: {"unexpected": True},
        dispatch_to_document_thread=lambda callback: callback(),
        durable_lifecycle=lifecycle,
    )
    assert entered.wait(1.0)
    assert manager.cancel(submitted.job_id)
    completed = manager.wait(submitted.job_id, 2.0)
    record = store.load(submitted.job_id)

    assert completed.phase == "cancelled"
    assert record["state"] == "cancelled"
    assert record["publication"]["receipt"] is None
