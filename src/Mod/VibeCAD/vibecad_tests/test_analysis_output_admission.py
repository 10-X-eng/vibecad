# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from VibeCADAnalysisArtifacts import (
    ARTIFACT_MANIFEST_VERSION,
    ArtifactDescriptor,
    ArtifactManifest,
    ContentAddressedArtifactStore,
)
from VibeCADAnalysisPersistence import (
    AnalysisMetadataStore,
    AnalysisOutputAdmissionCoordinator,
    AnalysisOutputAdmissionError,
    new_job_record,
)


def _descriptor(content: bytes = b"verified output") -> ArtifactDescriptor:
    return ArtifactDescriptor(
        role="solver_output",
        logical_name="result",
        media_type="application/octet-stream",
        relative_path="outputs/result.dat",
        byte_count=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        producer_id="solver-fixture",
        job_id="analysis-1",
        provider_id="remote-fixture",
        solver_id="solver-fixture-v1",
        source_correlation="a" * 64,
        exactness_class="provider-claimed-unverified",
        created_at="2026-08-27T00:00:00Z",
    )


def _collecting_store(
    root: Path,
    manifest: ArtifactManifest,
    *,
    fault_injector=None,
) -> AnalysisMetadataStore:
    store = AnalysisMetadataStore(root, fault_injector=fault_injector)
    store.create(new_job_record(
        analysis_id="analysis-1",
        domain="fem",
        adapter_id="vibecad.native.analyze.fem",
        source_document_uid="document-uid",
        prepared_analysis_sha256="a" * 64,
        dependency_sha256="b" * 64,
        input_manifest_sha256="c" * 64,
        execution_spec_sha256="d" * 64,
    ))
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
    store.transition(
        "analysis-1",
        "collecting",
        reason="provider_outputs_collected",
        updates={"provider_collection_receipts": [{
            "collected_at": "2026-08-27T00:00:01Z",
            "attempt": 1,
            "provider_id": "remote-fixture",
            "provider_job_id": "remote-job-7",
            "output_manifest_sha256": manifest.sha256,
        }]},
        expected_state="running_remote",
    )
    return store


def test_returned_file_is_hash_verified_admitted_and_stops_at_verifying(
    tmp_path: Path,
) -> None:
    content = b"verified output"
    descriptor = _descriptor(content)
    manifest = ArtifactManifest(ARTIFACT_MANIFEST_VERSION, (descriptor,))
    transport = tmp_path / "transport"
    output = transport / descriptor.relative_path
    output.parent.mkdir(parents=True)
    output.write_bytes(content)
    store = _collecting_store(tmp_path / "metadata", manifest)
    artifacts = ContentAddressedArtifactStore(tmp_path / "objects")

    result = AnalysisOutputAdmissionCoordinator(store, artifacts).admit_collected(
        "analysis-1", manifest, transport,
    )

    assert result.outcome == "verifying"
    assert result.reason == "provider_outputs_admitted"
    assert result.publication_authorized is False
    assert result.record["state"] == "verifying"
    assert result.record["publication"]["receipt"] is None
    assert result.record["artifacts"][0]["sha256"] == descriptor.sha256
    assert artifacts.path_for(descriptor.sha256).read_bytes() == content


def test_hash_mismatch_fails_without_publication_or_false_admission(
    tmp_path: Path,
) -> None:
    descriptor = _descriptor()
    manifest = ArtifactManifest(ARTIFACT_MANIFEST_VERSION, (descriptor,))
    transport = tmp_path / "transport"
    output = transport / descriptor.relative_path
    output.parent.mkdir(parents=True)
    output.write_bytes(b"tampered")
    store = _collecting_store(tmp_path / "metadata", manifest)
    artifacts = ContentAddressedArtifactStore(tmp_path / "objects")

    result = AnalysisOutputAdmissionCoordinator(store, artifacts).admit_collected(
        "analysis-1", manifest, transport,
    )

    assert result.outcome == "failed"
    assert result.reason == "provider_output_hash_mismatch"
    assert result.record["state"] == "failed"
    assert result.record["artifacts"] == []
    assert result.record["publication"]["receipt"] is None
    assert not artifacts.path_for(descriptor.sha256).exists()


def test_returned_file_larger_than_its_descriptor_fails_as_a_bound_violation(
    tmp_path: Path,
) -> None:
    descriptor = _descriptor(b"x")
    manifest = ArtifactManifest(ARTIFACT_MANIFEST_VERSION, (descriptor,))
    transport = tmp_path / "transport"
    output = transport / descriptor.relative_path
    output.parent.mkdir(parents=True)
    output.write_bytes(b"x" * (2 * 1024 * 1024))
    store = _collecting_store(tmp_path / "metadata", manifest)

    result = AnalysisOutputAdmissionCoordinator(
        store, ContentAddressedArtifactStore(tmp_path / "objects"),
    ).admit_collected("analysis-1", manifest, transport)

    assert result.outcome == "failed"
    assert result.reason == "provider_output_bounds_exceeded"
    assert result.record["artifacts"] == []


def test_missing_transport_output_preserves_collecting_for_truthful_retry(
    tmp_path: Path,
) -> None:
    descriptor = _descriptor()
    manifest = ArtifactManifest(ARTIFACT_MANIFEST_VERSION, (descriptor,))
    store = _collecting_store(tmp_path / "metadata", manifest)
    before = store.load("analysis-1")

    with pytest.raises(AnalysisOutputAdmissionError) as refused:
        AnalysisOutputAdmissionCoordinator(
            store, ContentAddressedArtifactStore(tmp_path / "objects"),
        ).admit_collected("analysis-1", manifest, tmp_path / "missing")
    assert refused.value.reason == "transport_unavailable"
    assert store.load("analysis-1") == before


def test_manifest_must_match_the_exact_collection_receipt_before_file_access(
    tmp_path: Path,
) -> None:
    descriptor = _descriptor()
    collected = ArtifactManifest(ARTIFACT_MANIFEST_VERSION, (descriptor,))
    different = ArtifactManifest(
        ARTIFACT_MANIFEST_VERSION,
        (_descriptor(b"different returned output"),),
    )
    store = _collecting_store(tmp_path / "metadata", collected)
    before = store.load("analysis-1")

    with pytest.raises(AnalysisOutputAdmissionError) as refused:
        AnalysisOutputAdmissionCoordinator(
            store, ContentAddressedArtifactStore(tmp_path / "objects"),
        ).admit_collected("analysis-1", different, tmp_path / "missing")
    assert refused.value.reason == "collection_receipt_mismatch"
    assert store.load("analysis-1") == before


def test_admission_resumes_idempotently_after_pretransition_crash(
    tmp_path: Path,
) -> None:
    armed = {"value": False}

    def interrupt_verifying(point: str, record: dict) -> None:
        if (
            armed["value"]
            and point == "before_replace"
            and record.get("state") == "verifying"
        ):
            armed["value"] = False
            raise RuntimeError("simulated phase crash")

    content = b"verified output"
    descriptor = _descriptor(content)
    manifest = ArtifactManifest(ARTIFACT_MANIFEST_VERSION, (descriptor,))
    transport = tmp_path / "transport"
    output = transport / descriptor.relative_path
    output.parent.mkdir(parents=True)
    output.write_bytes(content)
    store = _collecting_store(
        tmp_path / "metadata", manifest, fault_injector=interrupt_verifying,
    )
    artifacts = ContentAddressedArtifactStore(tmp_path / "objects")
    coordinator = AnalysisOutputAdmissionCoordinator(store, artifacts)

    armed["value"] = True
    with pytest.raises(RuntimeError, match="simulated phase crash"):
        coordinator.admit_collected("analysis-1", manifest, transport)
    assert store.load("analysis-1")["state"] == "collecting"
    assert len(store.load("analysis-1")["artifacts"]) == 1

    result = coordinator.admit_collected("analysis-1", manifest, transport)

    assert result.record["state"] == "verifying"
    assert len(result.record["artifacts"]) == 1
