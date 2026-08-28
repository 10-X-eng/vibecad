# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from VibeCADAnalysisArtifacts import ARTIFACT_MANIFEST_VERSION
from VibeCADAnalysisPersistence import (
    AnalysisMetadataStore,
    AnalysisPersistenceError,
    AnalysisProviderRecoveryCoordinator,
    AnalysisProviderRecoveryError,
    new_job_record,
)
from VibeCADAnalysisProviders import ProviderCapabilities


def _record() -> dict:
    return new_job_record(
        analysis_id="analysis-1",
        domain="fem",
        adapter_id="vibecad.native.analyze.fem",
        source_document_uid="document-uid",
        prepared_analysis_sha256="a" * 64,
        dependency_sha256="b" * 64,
        input_manifest_sha256="c" * 64,
        execution_spec_sha256="d" * 64,
    )


def _remote_store(tmp_path: Path) -> AnalysisMetadataStore:
    store = AnalysisMetadataStore(tmp_path)
    store.create(_record())
    store.begin_attempt(
        "analysis-1",
        provider_id="remote-fixture",
        provider_kind="remote",
        provider_job_id="remote-job-7",
        provider_capability_snapshot={
            "reconnect_supported": True,
            "job_survives_client_exit": True,
        },
    )
    return store


def _status(state: str, *, outputs_available: bool = False, failure_code=None) -> dict:
    return {
        "provider_job_id": "remote-job-7",
        "state": state,
        "outputs_available": outputs_available,
        "failure_code": failure_code,
    }


def _manifest() -> dict:
    return {
        "version": ARTIFACT_MANIFEST_VERSION,
        "artifacts": [{
            "role": "solver_output",
            "logical_name": "results",
            "media_type": "application/octet-stream",
            "relative_path": "outputs/result.dat",
            "byte_count": 12,
            "sha256": "e" * 64,
            "producer_id": "solver-fixture",
            "job_id": "analysis-1",
            "provider_id": "remote-fixture",
            "solver_id": "solver-fixture-v1",
            "source_correlation": "a" * 64,
            "exactness_class": "provider-claimed-unverified",
            "created_at": "2026-08-27T00:00:00Z",
        }],
    }


class _Provider:
    def __init__(self, status: dict, *, collection: dict | None = None) -> None:
        self.status_value = deepcopy(status)
        self.collection_value = deepcopy(collection)
        self.reconnect_value: dict | None = {"provider_job_id": "remote-job-7"}
        self.calls: list[tuple[str, str]] = []
        self.capabilities = ProviderCapabilities(
            provider_id="remote-fixture",
            location="remote",
            reconnect_supported=True,
            cancel_supported=True,
            log_streaming=False,
            execution_environment="fixture",
            job_survives_client_exit=True,
        )

    def describe_capabilities(self):
        self.calls.append(("capabilities", ""))
        return self.capabilities

    def reconnect(self, provider_job_id: str):
        self.calls.append(("reconnect", provider_job_id))
        return deepcopy(self.reconnect_value)

    def status(self, provider_job_id: str):
        self.calls.append(("status", provider_job_id))
        return deepcopy(self.status_value)

    def collect(self, provider_job_id: str):
        self.calls.append(("collect", provider_job_id))
        return deepcopy(self.collection_value)


def test_running_remote_job_is_reconnected_without_mutation_or_collection(
    tmp_path: Path,
) -> None:
    store = _remote_store(tmp_path)
    before = store.load("analysis-1")
    provider = _Provider(_status("running"))

    result = AnalysisProviderRecoveryCoordinator(store).reconcile_remote(
        "analysis-1", provider,
    )

    assert result.outcome == "running"
    assert result.reason == "provider_confirms_running"
    assert result.provider_state == "running"
    assert result.output_manifest is None
    assert result.publication_authorized is False
    assert result.record == before
    assert store.load("analysis-1") == before
    assert provider.calls == [
        ("capabilities", ""),
        ("reconnect", "remote-job-7"),
        ("status", "remote-job-7"),
    ]


def test_completed_remote_job_collects_a_bounded_manifest_and_stops_before_verification(
    tmp_path: Path,
) -> None:
    store = _remote_store(tmp_path)
    provider = _Provider(
        _status("completed", outputs_available=True), collection=_manifest(),
    )

    result = AnalysisProviderRecoveryCoordinator(store).reconcile_remote(
        "analysis-1", provider,
    )

    assert result.outcome == "collected"
    assert result.reason == "provider_outputs_collected"
    assert result.provider_state == "completed"
    assert result.output_manifest is not None
    assert result.record["state"] == "collecting"
    assert result.record["publication"] == {
        "intent": None, "authorization": None, "receipt": None,
    }
    assert result.publication_authorized is False
    assert result.record["provider_collection_receipts"] == [{
        "collected_at": result.record["provider_collection_receipts"][0]["collected_at"],
        "attempt": 1,
        "provider_id": "remote-fixture",
        "provider_job_id": "remote-job-7",
        "output_manifest_sha256": result.output_manifest.sha256,
    }]
    assert provider.calls[-1] == ("collect", "remote-job-7")


@pytest.mark.parametrize(
    ("state", "failure_code", "expected_state", "expected_reason"),
    (
        ("failed", "REMOTE_SOLVER_FAILED", "failed", "provider_failed:REMOTE_SOLVER_FAILED"),
        ("cancelled", None, "cancelled", "provider_cancelled"),
    ),
)
def test_provider_terminal_status_becomes_explicit_terminal_attempt_evidence(
    tmp_path: Path,
    state: str,
    failure_code: str | None,
    expected_state: str,
    expected_reason: str,
) -> None:
    store = _remote_store(tmp_path)
    provider = _Provider(_status(state, failure_code=failure_code))

    result = AnalysisProviderRecoveryCoordinator(store).reconcile_remote(
        "analysis-1", provider,
    )

    assert result.outcome == expected_state
    assert result.reason == expected_reason
    assert result.record["state"] == expected_state
    assert result.record["terminal_reason"] == expected_reason
    assert result.record["attempts"][-1]["terminal_reason"] == expected_reason
    assert result.record["publication"]["receipt"] is None


def test_provider_confirmed_missing_reconnect_becomes_orphaned_interruption(
    tmp_path: Path,
) -> None:
    store = _remote_store(tmp_path)
    provider = _Provider(_status("running"))
    provider.reconnect_value = None

    result = AnalysisProviderRecoveryCoordinator(store).reconcile_remote(
        "analysis-1", provider,
    )

    assert result.outcome == "interrupted"
    assert result.reason == "provider_job_not_found"
    assert result.record["state"] == "interrupted"
    assert result.record["recovery_events"][-1]["failure_kind"] == "host_interrupted"
    assert provider.calls == [
        ("capabilities", ""),
        ("reconnect", "remote-job-7"),
    ]


@pytest.mark.parametrize("unavailable_method", ("reconnect", "status", "collect"))
def test_temporary_provider_failure_preserves_reconnectable_state(
    tmp_path: Path, unavailable_method: str,
) -> None:
    store = _remote_store(tmp_path)
    before = store.load("analysis-1")
    provider = _Provider(
        _status(
            "completed" if unavailable_method == "collect" else "running",
            outputs_available=unavailable_method == "collect",
        ),
        collection=_manifest(),
    )

    def unavailable(_provider_job_id: str):
        raise RuntimeError("temporary provider outage")

    setattr(provider, unavailable_method, unavailable)
    with pytest.raises(AnalysisProviderRecoveryError) as refused:
        AnalysisProviderRecoveryCoordinator(store).reconcile_remote(
            "analysis-1", provider,
        )
    assert refused.value.reason == "provider_unavailable"
    assert store.load("analysis-1") == before


def test_collection_can_repeat_after_a_precommit_host_interruption(
    tmp_path: Path,
) -> None:
    armed = {"value": False}

    def interrupt_before_replace(point: str, _record: dict) -> None:
        if armed["value"] and point == "before_replace":
            armed["value"] = False
            raise RuntimeError("simulated host interruption")

    store = AnalysisMetadataStore(tmp_path, fault_injector=interrupt_before_replace)
    store.create(_record())
    store.begin_attempt(
        "analysis-1",
        provider_id="remote-fixture",
        provider_kind="remote",
        provider_job_id="remote-job-7",
        provider_capability_snapshot={
            "reconnect_supported": True,
            "job_survives_client_exit": True,
        },
    )
    provider = _Provider(
        _status("completed", outputs_available=True), collection=_manifest(),
    )
    coordinator = AnalysisProviderRecoveryCoordinator(store)

    armed["value"] = True
    with pytest.raises(RuntimeError, match="simulated host interruption"):
        coordinator.reconcile_remote("analysis-1", provider)
    assert store.load("analysis-1")["state"] == "running_remote"
    assert "provider_collection_receipts" not in store.load("analysis-1")

    result = coordinator.reconcile_remote("analysis-1", provider)

    assert result.outcome == "collected"
    assert result.record["state"] == "collecting"
    assert provider.calls.count(("collect", "remote-job-7")) == 2


def test_live_provider_identity_and_capabilities_must_match_persisted_authority(
    tmp_path: Path,
) -> None:
    store = _remote_store(tmp_path)
    before = store.load("analysis-1")
    provider = _Provider(_status("running"))
    provider.capabilities = ProviderCapabilities(
        provider_id="different-provider",
        location="remote",
        reconnect_supported=True,
        cancel_supported=True,
        log_streaming=False,
        execution_environment="fixture",
        job_survives_client_exit=True,
    )

    with pytest.raises(AnalysisProviderRecoveryError) as refused:
        AnalysisProviderRecoveryCoordinator(store).reconcile_remote(
            "analysis-1", provider,
        )
    assert refused.value.reason == "provider_mismatch"
    assert store.load("analysis-1") == before
    assert provider.calls == [("capabilities", "")]


@pytest.mark.parametrize(
    "status",
    (
        _status("unknown"),
        _status("completed", outputs_available=False),
        _status("failed", failure_code="token=must-not-persist"),
        {**_status("running"), "credential": "must-not-enter-host-state"},
        {**_status("running"), "provider_job_id": "different-job"},
    ),
)
def test_malformed_or_mismatched_status_fails_attempt_without_persisting_payload(
    tmp_path: Path, status: dict,
) -> None:
    store = _remote_store(tmp_path)
    provider = _Provider(status)

    result = AnalysisProviderRecoveryCoordinator(store).reconcile_remote(
        "analysis-1", provider,
    )

    assert result.outcome == "failed"
    assert result.reason == "provider_status_invalid"
    assert result.record["state"] == "failed"
    assert "credential" not in str(result.record)
    assert "must-not-persist" not in str(result.record)


@pytest.mark.parametrize("invalid_field", ("sha256", "relative_path"))
def test_invalid_collected_manifest_is_an_integrity_failure_not_success(
    tmp_path: Path, invalid_field: str,
) -> None:
    store = _remote_store(tmp_path)
    manifest = _manifest()
    manifest["artifacts"][0][invalid_field] = (
        "not-a-digest" if invalid_field == "sha256" else "../escaped.dat"
    )
    provider = _Provider(
        _status("completed", outputs_available=True), collection=manifest,
    )

    result = AnalysisProviderRecoveryCoordinator(store).reconcile_remote(
        "analysis-1", provider,
    )

    assert result.outcome == "failed"
    assert result.reason == "provider_collection_invalid"
    assert result.record["state"] == "failed"
    assert result.record["publication"]["receipt"] is None
    assert "provider_collection_receipts" not in result.record


def test_tampered_provider_collection_receipt_is_refused_on_reload(
    tmp_path: Path,
) -> None:
    store = _remote_store(tmp_path)
    provider = _Provider(
        _status("completed", outputs_available=True), collection=_manifest(),
    )
    AnalysisProviderRecoveryCoordinator(store).reconcile_remote(
        "analysis-1", provider,
    )
    path = store.records / "analysis-1.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["provider_collection_receipts"][0]["provider_job_id"] = "other-job"
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(AnalysisPersistenceError, match="collection evidence"):
        store.load("analysis-1")


def test_collection_receipt_cannot_be_injected_before_collection_state(
    tmp_path: Path,
) -> None:
    store = _remote_store(tmp_path)
    path = store.records / "analysis-1.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["provider_collection_receipts"] = [{
        "collected_at": "2026-08-27T00:00:00Z",
        "attempt": 1,
        "provider_id": "remote-fixture",
        "provider_job_id": "remote-job-7",
        "output_manifest_sha256": "f" * 64,
    }]
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(AnalysisPersistenceError, match="collection evidence"):
        store.load("analysis-1")


def test_collection_respects_the_metadata_store_policy_bound(
    tmp_path: Path,
) -> None:
    store = AnalysisMetadataStore(
        tmp_path, maximum_artifact_bytes_per_analysis=8,
    )
    store.create(_record())
    store.begin_attempt(
        "analysis-1",
        provider_id="remote-fixture",
        provider_kind="remote",
        provider_job_id="remote-job-7",
        provider_capability_snapshot={
            "reconnect_supported": True,
            "job_survives_client_exit": True,
        },
    )
    provider = _Provider(
        _status("completed", outputs_available=True), collection=_manifest(),
    )

    result = AnalysisProviderRecoveryCoordinator(store).reconcile_remote(
        "analysis-1", provider,
    )

    assert result.outcome == "failed"
    assert result.reason == "provider_collection_invalid"
    assert "provider_collection_receipts" not in result.record


def test_non_reconnectable_record_is_refused_before_calling_provider(
    tmp_path: Path,
) -> None:
    store = AnalysisMetadataStore(tmp_path)
    store.create(_record())
    provider = _Provider(_status("running"))

    with pytest.raises(AnalysisProviderRecoveryError) as refused:
        AnalysisProviderRecoveryCoordinator(store).reconcile_remote(
            "analysis-1", provider,
        )
    assert refused.value.reason == "not_reconnectable"
    assert provider.calls == []
