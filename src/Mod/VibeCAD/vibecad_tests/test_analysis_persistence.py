# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json
from pathlib import Path

import pytest

from VibeCADAnalysisPersistence import (
    ANALYSIS_METADATA_SCHEMA_VERSION,
    AnalysisMetadataStore,
    AnalysisPersistenceError,
    AnalysisStoreBusy,
    analysis_user_data_root,
    discover_user_analysis_records,
    migrate_user_analysis_records,
    new_job_record,
    open_user_analysis_metadata_store,
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


def _legacy_v1_record(analysis_id: str = "analysis-1") -> dict:
    record = _record(analysis_id)
    record["schema_version"] = 1
    record.pop("schema_migrations", None)
    return record


def _advance(store: AnalysisMetadataStore, state: str, attempts: list) -> None:
    if state == "prepared":
        return
    if state == "running_remote":
        latest = attempts[-1]
        store.begin_attempt(
            "analysis-1", provider_id="remote", provider_kind="remote",
            provider_job_id=latest.get("provider_job_id", ""),
            provider_capability_snapshot=latest.get("provider_capability_snapshot"),
        )
        running = "running_remote"
    else:
        store.begin_attempt(
            "analysis-1", provider_id="local-process", provider_kind="local",
        )
        running = "running_local"
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
    running = store.begin_attempt(
        "analysis-1", provider_id="local-process", provider_kind="local",
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


def test_v1_record_migration_is_atomic_audited_and_idempotent(
    tmp_path: Path,
) -> None:
    store = AnalysisMetadataStore(tmp_path)
    store.records.mkdir(parents=True)
    path = store.records / "analysis-1.json"
    legacy = _legacy_v1_record()
    path.write_text(json.dumps(legacy), encoding="utf-8")

    with pytest.raises(AnalysisPersistenceError, match="requires migration"):
        store.load("analysis-1")

    migrated = store.migrate_record("analysis-1")
    assert ANALYSIS_METADATA_SCHEMA_VERSION == 2
    assert migrated["schema_version"] == 2
    assert migrated["schema_migrations"] == [{
        "from_version": 1,
        "to_version": 2,
        "at": migrated["schema_migrations"][0]["at"],
    }]
    assert migrated["analysis_id"] == legacy["analysis_id"]
    assert migrated["events"] == legacy["events"]
    assert migrated["updated_at"] == legacy["updated_at"]
    assert store.migrate_record("analysis-1") == migrated
    assert json.loads(
        (store.backups / "analysis-1.previous.json").read_text(encoding="utf-8")
    ) == legacy


@pytest.mark.parametrize(
    ("fault_point", "durable_version"),
    (
        ("before_stage", 1),
        ("after_stage", 1),
        ("before_replace", 1),
        ("after_replace", 2),
    ),
)
def test_migration_fault_points_have_defined_durable_outcome(
    tmp_path: Path, fault_point: str, durable_version: int,
) -> None:
    baseline = AnalysisMetadataStore(tmp_path)
    baseline.records.mkdir(parents=True)
    path = baseline.records / "analysis-1.json"
    legacy = _legacy_v1_record()
    path.write_text(json.dumps(legacy), encoding="utf-8")

    def fail(point, _record_value):
        if point == fault_point:
            raise RuntimeError(f"migration power loss: {fault_point}")

    faulted = AnalysisMetadataStore(tmp_path, fault_injector=fail)
    with pytest.raises(RuntimeError, match=fault_point):
        faulted.migrate_record("analysis-1")

    durable = json.loads(path.read_text(encoding="utf-8"))
    assert durable["schema_version"] == durable_version
    if durable_version == 1:
        assert durable == legacy
    assert list((tmp_path / "records").glob("*.tmp")) == []


def test_migration_refuses_unknown_versions_without_rewriting(tmp_path: Path) -> None:
    store = AnalysisMetadataStore(tmp_path)
    store.records.mkdir(parents=True)
    path = store.records / "analysis-1.json"
    future = _legacy_v1_record()
    future["schema_version"] = 999
    path.write_text(json.dumps(future), encoding="utf-8")

    with pytest.raises(AnalysisPersistenceError, match="no supported migration"):
        store.migrate_record("analysis-1")
    assert json.loads(path.read_text(encoding="utf-8")) == future

    mismatched = _legacy_v1_record("different-id")
    path.write_text(json.dumps(mismatched), encoding="utf-8")
    with pytest.raises(AnalysisPersistenceError, match="filename"):
        store.migrate_record("analysis-1")
    assert json.loads(path.read_text(encoding="utf-8")) == mismatched


@pytest.mark.parametrize(
    "history",
    (
        [{"from_version": 1, "to_version": 3, "at": "now"}],
        [{"from_version": 0, "to_version": 1, "at": "now"},
         {"from_version": 1, "to_version": 2, "at": "now"}],
        [{"from_version": True, "to_version": 2, "at": "now"}],
    ),
)
def test_current_record_refuses_unrecognized_migration_history(
    tmp_path: Path, history: list[dict],
) -> None:
    record = _record()
    record["schema_migrations"] = history
    with pytest.raises(AnalysisPersistenceError, match="migration history"):
        AnalysisMetadataStore(tmp_path).create(record)


def test_user_analysis_store_uses_central_app_data_and_global_discovery(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("VIBECAD_HOME", str(tmp_path / "vibecad-user-data"))
    expected = tmp_path / "vibecad-user-data" / "analysis-runtime"

    assert analysis_user_data_root() == expected
    store = open_user_analysis_metadata_store()
    store.create(_record("analysis-2"))
    other = _record("analysis-1")
    other["source_document_uid"] = "other-document"
    store.create(other)

    assert store.root == expected
    assert [
        item["analysis_id"] for item in discover_user_analysis_records()
    ] == ["analysis-1", "analysis-2"]
    assert [
        item["analysis_id"]
        for item in discover_user_analysis_records(document_uid="document-uid")
    ] == ["analysis-2"]
    assert migrate_user_analysis_records() == ()

    legacy_path = store.records / "analysis-legacy.json"
    legacy_path.write_text(
        json.dumps(_legacy_v1_record("analysis-legacy")), encoding="utf-8",
    )
    with pytest.raises(AnalysisPersistenceError, match="invalid record") as refused:
        discover_user_analysis_records()
    assert "requires migration" in str(refused.value.__cause__)
    assert json.loads(legacy_path.read_text(encoding="utf-8"))["schema_version"] == 1
    migrated = migrate_user_analysis_records()
    assert [item["analysis_id"] for item in migrated] == ["analysis-legacy"]
    assert [
        item["analysis_id"] for item in discover_user_analysis_records()
    ] == ["analysis-1", "analysis-2", "analysis-legacy"]


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
    ("state", "attempts", "action", "reason"),
    (
        ("prepared", [], "mark_interrupted", "host_runtime_not_reattachable"),
        ("running_local", [{"attempt": 1}], "mark_interrupted", "host_runtime_not_reattachable"),
        ("running_remote", [{"provider_job_id": "remote-7"}], "mark_interrupted", "provider_reconnect_not_proven"),
        ("collecting", [], "resume_collecting", "durable_phase_requires_reconciliation"),
        ("verifying", [], "resume_verifying", "durable_phase_requires_reconciliation"),
        ("waiting_to_publish", [], "resume_waiting_to_publish", "durable_phase_requires_reconciliation"),
        ("publishing", [], "publication_outcome_unknown", "publication_receipt_requires_reconciliation"),
        ("succeeded", [], "terminal", "terminal_record"),
    ),
)
def test_restart_classification_never_guesses_success(
    tmp_path: Path,
    state: str,
    attempts: list,
    action: str,
    reason: str,
) -> None:
    store = AnalysisMetadataStore(tmp_path / state)
    store.create(_record())
    _advance(store, state, attempts)
    assert store.restart_disposition("analysis-1") == {
        "analysis_id": "analysis-1",
        "state": state,
        "action": action,
        "reason": reason,
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
        provider_capability_snapshot={
            "reconnect_supported": True,
            "job_survives_client_exit": True,
        },
    )
    assert running["attempts"][-1]["provider_job_id"] == "kernel-123"
    assert store.restart_disposition("analysis-1") == {
        "analysis_id": "analysis-1",
        "state": "running_remote",
        "action": "reconnect_remote",
        "reason": "persisted_provider_reconnect_evidence",
        "attempt": 1,
        "provider_id": "kaggle",
        "provider_job_id": "kernel-123",
    }


def test_remote_reconnect_requires_latest_attempt_capability_evidence(tmp_path: Path) -> None:
    store = AnalysisMetadataStore(tmp_path)
    store.create(_record())
    store.begin_attempt(
        "analysis-1", provider_id="remote", provider_kind="remote",
        provider_job_id="job-1", provider_capability_snapshot={
            "reconnect_supported": True, "job_survives_client_exit": False,
        },
    )
    assert store.restart_disposition("analysis-1")["action"] == "mark_interrupted"


def test_old_reconnectable_attempt_never_authorizes_latest_attempt(tmp_path: Path) -> None:
    store = AnalysisMetadataStore(tmp_path)
    store.create(_record())
    store.begin_attempt(
        "analysis-1", provider_id="remote", provider_kind="remote",
        provider_job_id="job-1", provider_capability_snapshot={
            "reconnect_supported": True, "job_survives_client_exit": True,
        },
    )
    store.transition("analysis-1", "interrupted", reason="provider_failed")
    store.retry_interrupted(
        "analysis-1", expected_prepared_analysis_sha256="a" * 64,
        expected_dependency_sha256="b" * 64,
        expected_input_manifest_sha256="c" * 64,
        expected_execution_spec_sha256="d" * 64,
    )
    store.begin_attempt(
        "analysis-1", provider_id="remote", provider_kind="remote",
        provider_job_id="job-2",
    )
    assert store.restart_disposition("analysis-1")["action"] == "mark_interrupted"


def test_unrecoverable_restart_is_atomically_interrupted_and_audited(
    tmp_path: Path,
) -> None:
    store = AnalysisMetadataStore(tmp_path)
    store.create(_record())
    store.begin_attempt(
        "analysis-1", provider_id="local-process", provider_kind="local",
    )

    interrupted = store.interrupt_unrecoverable_after_restart("analysis-1")
    assert interrupted["state"] == "interrupted"
    assert interrupted["terminal_reason"] == "host_interrupted"
    assert interrupted["attempts"][-1]["terminal_reason"] == "host_interrupted"
    assert interrupted["recovery_events"] == [{
        "classified_at": interrupted["recovery_events"][0]["classified_at"],
        "previous_state": "running_local",
        "disposition": "orphaned",
        "failure_kind": "host_interrupted",
        "attempt": 1,
    }]
    assert store.interrupt_unrecoverable_after_restart("analysis-1") == interrupted

    retried = store.retry_interrupted(
        "analysis-1", expected_prepared_analysis_sha256="a" * 64,
        expected_dependency_sha256="b" * 64,
        expected_input_manifest_sha256="c" * 64,
        expected_execution_spec_sha256="d" * 64,
    )
    assert retried["recovery_events"] == interrupted["recovery_events"]


def test_expected_state_guard_refuses_stale_restart_write(tmp_path: Path) -> None:
    store = AnalysisMetadataStore(tmp_path)
    store.create(_record())
    before = store.begin_attempt(
        "analysis-1", provider_id="local-process", provider_kind="local",
    )
    with pytest.raises(AnalysisPersistenceError, match="state changed"):
        store.transition(
            "analysis-1", "collecting", reason="stale observation",
            expected_state="prepared",
        )
    assert store.load("analysis-1") == before


def test_reconnectable_remote_restart_is_read_only(tmp_path: Path) -> None:
    store = AnalysisMetadataStore(tmp_path)
    store.create(_record())
    before = store.begin_attempt(
        "analysis-1", provider_id="remote", provider_kind="remote",
        provider_job_id="job-1", provider_capability_snapshot={
            "reconnect_supported": True, "job_survives_client_exit": True,
        },
    )
    with pytest.raises(AnalysisPersistenceError, match="not unrecoverable"):
        store.interrupt_unrecoverable_after_restart("analysis-1")
    assert store.load("analysis-1") == before


@pytest.mark.parametrize(
    "snapshot",
    (
        {"reconnect_supported": 1, "job_survives_client_exit": True},
        {"reconnect_supported": True},
        {"reconnect_supported": True, "job_survives_client_exit": True,
         "credential": "must-not-persist"},
    ),
)
def test_provider_recovery_snapshot_is_bounded_and_inert(
    tmp_path: Path, snapshot: dict,
) -> None:
    store = AnalysisMetadataStore(tmp_path)
    store.create(_record())
    with pytest.raises(AnalysisPersistenceError, match="capability snapshot"):
        store.begin_attempt(
            "analysis-1", provider_id="remote", provider_kind="remote",
            provider_job_id="job-1", provider_capability_snapshot=snapshot,
        )


def test_persisted_recovery_evidence_fails_closed_when_malformed(
    tmp_path: Path,
) -> None:
    store = AnalysisMetadataStore(tmp_path)
    store.create(_record())
    store.begin_attempt(
        "analysis-1", provider_id="local-process", provider_kind="local",
    )
    store.interrupt_unrecoverable_after_restart("analysis-1")
    path = store.records / "analysis-1.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["recovery_events"][0]["attempt"] = 0
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(AnalysisPersistenceError, match="recovery evidence"):
        store.load("analysis-1")


def test_persisted_provider_snapshot_fails_closed_when_expanded(
    tmp_path: Path,
) -> None:
    store = AnalysisMetadataStore(tmp_path)
    store.create(_record())
    store.begin_attempt(
        "analysis-1", provider_id="remote", provider_kind="remote",
        provider_job_id="job-1",
    )
    path = store.records / "analysis-1.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["attempts"][0]["provider_capability_snapshot"]["credential"] = "secret"
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(AnalysisPersistenceError, match="capability snapshot"):
        store.load("analysis-1")


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


def test_artifact_metadata_enforces_per_analysis_count_and_byte_quotas(
    tmp_path: Path,
) -> None:
    store = AnalysisMetadataStore(
        tmp_path,
        maximum_artifacts_per_analysis=1,
        maximum_artifact_bytes_per_analysis=6,
    )
    store.create(_record())
    first = {"sha256": "e" * 64, "role": "solver_output", "byte_count": 5}
    second = {"sha256": "f" * 64, "role": "solver_output", "byte_count": 1}

    admitted = store.record_artifact("analysis-1", first, cleanup_eligible=True)
    assert store.record_artifact(
        "analysis-1", first, cleanup_eligible=True
    ) == admitted
    with pytest.raises(AnalysisPersistenceError, match="artifact count quota"):
        store.record_artifact("analysis-1", second, cleanup_eligible=True)

    store.tombstone_artifact("analysis-1", first["sha256"])
    assert store.record_artifact(
        "analysis-1", second, cleanup_eligible=True
    )["artifacts"][-1]["sha256"] == second["sha256"]

    oversized = {"sha256": "0" * 64, "role": "solver_output", "byte_count": 7}
    with pytest.raises(AnalysisPersistenceError, match="artifact byte quota"):
        store.record_artifact("analysis-1", oversized, cleanup_eligible=True)


def test_publication_artifact_references_must_be_live_and_remain_protected(
    tmp_path: Path,
) -> None:
    store = AnalysisMetadataStore(tmp_path)
    store.create(_record())
    digest = "e" * 64
    store.record_artifact(
        "analysis-1",
        {"sha256": digest, "role": "solver_output", "byte_count": 12},
        cleanup_eligible=True,
    )
    _advance(store, "publishing", [])
    authorization = {
        "kind": "human_selected_destination",
        "destination_paths_persisted": False,
    }

    with pytest.raises(AnalysisPersistenceError, match="unknown or tombstoned"):
        store.record_publication_evidence(
            "analysis-1",
            intent={"kind": "human_authorized_output", "artifact_references": ["f" * 64]},
            authorization=authorization,
        )

    recorded = store.record_publication_evidence(
        "analysis-1",
        intent={"kind": "human_authorized_output", "artifact_references": [digest]},
        authorization=authorization,
    )
    assert recorded["publication"]["intent"]["artifact_references"] == [digest]
    assert store.protected_artifact_sha256("analysis-1") == (digest,)
    with pytest.raises(AnalysisPersistenceError, match="publication evidence"):
        store.tombstone_artifact("analysis-1", digest)


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
