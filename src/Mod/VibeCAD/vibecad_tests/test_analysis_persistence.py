# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json
from pathlib import Path

import pytest

from VibeCADAnalysisPersistence import (
    AnalysisMetadataStore,
    AnalysisPersistenceError,
    AnalysisStoreBusy,
    new_job_record,
)


def _record(analysis_id: str = "analysis-1") -> dict:
    return new_job_record(
        analysis_id=analysis_id,
        domain="fem",
        adapter_id="vibecad.native.analyze.fem",
        source_document_uid="document-uid",
        prepared_analysis_sha256="a" * 64,
        dependency_sha256="b" * 64,
        input_manifest_sha256="c" * 64,
        execution_spec_sha256="d" * 64,
    )


def _advance(store: AnalysisMetadataStore, state: str, attempts: list) -> None:
    if state == "prepared":
        return
    running = "running_remote" if state == "running_remote" else "running_local"
    store.transition("analysis-1", running, reason="fixture", updates={"attempts": attempts})
    if state == running:
        return
    for next_state in ("collecting", "verifying", "waiting_to_publish", "publishing"):
        store.transition("analysis-1", next_state, reason="fixture")
        if state == next_state:
            return
    store.transition("analysis-1", state, reason="fixture")


def test_create_transition_backup_and_terminal_idempotence(tmp_path: Path) -> None:
    store = AnalysisMetadataStore(tmp_path)
    created = store.create(_record())
    running = store.transition(
        "analysis-1",
        "running_local",
        reason="provider_started",
        updates={"attempts": [{"attempt": 1, "provider_id": "local-process"}]},
    )
    finished = store.transition("analysis-1", "interrupted", reason="host_restart")

    assert created["state"] == "prepared"
    assert running["events"][-1]["sequence"] == 2
    assert finished["terminal_reason"] == "host_restart"
    assert store.transition("analysis-1", "interrupted", reason="duplicate") == finished
    with pytest.raises(AnalysisPersistenceError, match="cannot reopen"):
        store.transition("analysis-1", "running_local", reason="invalid")
    backup = json.loads((tmp_path / "backups" / "analysis-1.previous.json").read_text())
    assert backup["state"] == "running_local"


def test_read_only_discovery_is_exact_by_document_identity(tmp_path: Path) -> None:
    store = AnalysisMetadataStore(tmp_path)
    store.create(_record("analysis-2"))
    other = _record("analysis-1")
    other["source_document_uid"] = "other-document"
    store.create(other)

    assert [item["analysis_id"] for item in store.list_records()] == [
        "analysis-1", "analysis-2"
    ]
    assert [item["analysis_id"] for item in store.find_by_document_uid("document-uid")] == [
        "analysis-2"
    ]


def test_discovery_refuses_corrupt_or_misnamed_records(tmp_path: Path) -> None:
    store = AnalysisMetadataStore(tmp_path)
    store.create(_record())
    (store.records / "wrong-name.json").write_text(
        json.dumps(_record("different-id")), encoding="utf-8"
    )
    with pytest.raises(AnalysisPersistenceError, match="filename"):
        store.list_records()


def test_fault_before_replace_preserves_previous_durable_record(tmp_path: Path) -> None:
    baseline = AnalysisMetadataStore(tmp_path)
    baseline.create(_record())

    def fail(point, _record_value):
        if point == "before_replace":
            raise RuntimeError("simulated power loss")

    faulted = AnalysisMetadataStore(tmp_path, fault_injector=fail)
    with pytest.raises(RuntimeError, match="power loss"):
        faulted.transition("analysis-1", "running_local", reason="start")

    recovered = baseline.load("analysis-1")
    assert recovered["state"] == "prepared"
    assert recovered["events"][-1]["sequence"] == 1
    assert list((tmp_path / "records").glob("*.tmp")) == []


def test_one_writer_lock_refuses_competing_process_identity(tmp_path: Path) -> None:
    store = AnalysisMetadataStore(tmp_path)
    with store._writer():
        with pytest.raises(AnalysisStoreBusy):
            store.create(_record())


def test_writer_lock_is_released_without_stale_recovery_after_exception(tmp_path: Path) -> None:
    store = AnalysisMetadataStore(tmp_path)
    with pytest.raises(RuntimeError):
        with store._writer():
            raise RuntimeError("simulated process failure")
    assert store.create(_record())["state"] == "prepared"


@pytest.mark.parametrize(
    ("state", "attempts", "action"),
    (
        ("prepared", [], "mark_interrupted"),
        ("running_local", [{"attempt": 1}], "mark_interrupted"),
        ("running_remote", [{"provider_job_id": "remote-7"}], "reconnect_remote"),
        ("collecting", [], "resume_collecting"),
        ("verifying", [], "resume_verifying"),
        ("waiting_to_publish", [], "resume_waiting_to_publish"),
        ("publishing", [], "publication_outcome_unknown"),
        ("succeeded", [], "terminal"),
    ),
)
def test_restart_classification_never_guesses_success(
    tmp_path: Path,
    state: str,
    attempts: list,
    action: str,
) -> None:
    store = AnalysisMetadataStore(tmp_path / state)
    store.create(_record())
    _advance(store, state, attempts)
    assert store.restart_disposition("analysis-1") == {
        "analysis_id": "analysis-1",
        "state": state,
        "action": action,
    }


def test_corrupt_unknown_or_nonmonotonic_records_are_refused(tmp_path: Path) -> None:
    store = AnalysisMetadataStore(tmp_path)
    store.create(_record())
    path = tmp_path / "records" / "analysis-1.json"
    value = json.loads(path.read_text())
    value["events"][0]["sequence"] = 9
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(AnalysisPersistenceError, match="not monotonic"):
        store.load("analysis-1")


def test_invalid_lifecycle_jump_is_refused_without_a_write(tmp_path: Path) -> None:
    store = AnalysisMetadataStore(tmp_path)
    store.create(_record())
    with pytest.raises(AnalysisPersistenceError, match="prepared -> succeeded"):
        store.transition("analysis-1", "succeeded", reason="guessed")
    assert store.load("analysis-1")["state"] == "prepared"


def test_retry_creates_new_attempt_only_for_exact_interrupted_identity(tmp_path: Path) -> None:
    store = AnalysisMetadataStore(tmp_path)
    store.create(_record())
    first = store.begin_attempt(
        "analysis-1", provider_id="local-process", provider_kind="local"
    )
    assert first["attempts"][0]["attempt"] == 1
    store.transition("analysis-1", "interrupted", reason="host_restart")

    with pytest.raises(AnalysisPersistenceError, match="does not match"):
        store.retry_interrupted(
            "analysis-1",
            expected_prepared_analysis_sha256="f" * 64,
            expected_dependency_sha256="b" * 64,
            expected_input_manifest_sha256="c" * 64,
            expected_execution_spec_sha256="d" * 64,
        )
    retried = store.retry_interrupted(
        "analysis-1",
        expected_prepared_analysis_sha256="a" * 64,
        expected_dependency_sha256="b" * 64,
        expected_input_manifest_sha256="c" * 64,
        expected_execution_spec_sha256="d" * 64,
    )
    second = store.begin_attempt(
        "analysis-1", provider_id="local-process", provider_kind="local"
    )
    assert retried["events"][-1]["reason"] == "retry_prepared"
    assert [item["attempt"] for item in second["attempts"]] == [1, 2]


def test_remote_attempt_persists_reconnect_identity(tmp_path: Path) -> None:
    store = AnalysisMetadataStore(tmp_path)
    store.create(_record())
    running = store.begin_attempt(
        "analysis-1",
        provider_id="kaggle",
        provider_kind="remote",
        provider_job_id="kernel-123",
    )
    assert running["attempts"][-1]["provider_job_id"] == "kernel-123"
    assert store.restart_disposition("analysis-1")["action"] == "reconnect_remote"


def test_artifact_tombstone_is_idempotent_and_evidence_aware(tmp_path: Path) -> None:
    store = AnalysisMetadataStore(tmp_path)
    store.create(_record())
    descriptor = {"sha256": "e" * 64, "role": "solver_output", "byte_count": 12}
    admitted = store.record_artifact(
        "analysis-1", descriptor, cleanup_eligible=True
    )
    assert admitted["artifacts"][0]["tombstoned_at"] is None
    tombstoned = store.tombstone_artifact("analysis-1", "e" * 64)
    assert tombstoned["artifacts"][0]["tombstoned_at"]
    assert store.tombstone_artifact("analysis-1", "e" * 64) == tombstoned

    store.record_artifact(
        "analysis-1",
        {"sha256": "f" * 64, "role": "input"},
        pinned=True,
        cleanup_eligible=True,
    )
    with pytest.raises(AnalysisPersistenceError, match="engineering evidence"):
        store.tombstone_artifact("analysis-1", "f" * 64)


def test_publication_evidence_is_write_once_and_path_free(tmp_path: Path) -> None:
    store = AnalysisMetadataStore(tmp_path)
    store.create(_record())
    _advance(store, "publishing", [])
    intent = {"kind": "human_authorized_output", "output_count": 1}
    authorization = {
        "kind": "human_selected_destination",
        "destination_paths_persisted": False,
    }

    recorded = store.record_publication_evidence(
        "analysis-1", intent=intent, authorization=authorization
    )

    assert recorded["publication"] == {
        "intent": intent,
        "authorization": authorization,
        "receipt": None,
    }
    assert store.record_publication_evidence(
        "analysis-1", intent=intent, authorization=authorization
    ) == recorded
    with pytest.raises(AnalysisPersistenceError, match="cannot change"):
        store.record_publication_evidence(
            "analysis-1",
            intent={**intent, "output_count": 2},
            authorization=authorization,
        )
    with pytest.raises(AnalysisPersistenceError, match="exceeds"):
        store.record_publication_evidence(
            "analysis-1",
            intent={"oversized": "x" * (64 * 1024)},
            authorization=authorization,
        )


@pytest.mark.parametrize(
    ("fault_point", "durable_state"),
    (
        ("before_stage", "prepared"),
        ("after_stage", "prepared"),
        ("before_replace", "prepared"),
        ("after_replace", "running_local"),
    ),
)
def test_transition_fault_points_have_defined_durable_outcome(
    tmp_path: Path, fault_point: str, durable_state: str,
) -> None:
    baseline = AnalysisMetadataStore(tmp_path)
    baseline.create(_record())

    def inject(point, _value):
        if point == fault_point:
            raise RuntimeError(point)

    faulted = AnalysisMetadataStore(tmp_path, fault_injector=inject)
    with pytest.raises(RuntimeError, match=fault_point):
        faulted.transition("analysis-1", "running_local", reason="start")
    assert baseline.load("analysis-1")["state"] == durable_state
