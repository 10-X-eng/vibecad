# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from dataclasses import replace
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
    AnalysisDomainVerificationCoordinator,
    AnalysisDomainVerificationError,
    AnalysisMetadataStore,
    AnalysisPersistenceError,
    DomainVerifierUnavailable,
    new_job_record,
)
from VibeCADEngineeringContracts import (
    ContentDescriptor,
    EngineeringIdentity,
    EngineeringResultEnvelope,
    FindingEnvelope,
    ProvenanceEdge,
    ProvenanceGraph,
    ProvenanceNode,
    canonical_payload,
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


def _verifying_fixture(
    tmp_path: Path,
    *,
    fault_injector=None,
) -> tuple[AnalysisMetadataStore, ContentAddressedArtifactStore, ArtifactManifest]:
    content = b"verified output"
    descriptor = _descriptor(content)
    manifest = ArtifactManifest(ARTIFACT_MANIFEST_VERSION, (descriptor,))
    store = AnalysisMetadataStore(tmp_path / "metadata", fault_injector=fault_injector)
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
    artifacts = ContentAddressedArtifactStore(tmp_path / "objects")
    source = tmp_path / "transport" / descriptor.relative_path
    source.parent.mkdir(parents=True)
    source.write_bytes(content)
    artifacts.admit(source, descriptor)
    store.record_artifact(
        "analysis-1",
        {name: getattr(descriptor, name) for name in descriptor.__dataclass_fields__},
        expected_state="collecting",
    )
    store.transition(
        "analysis-1",
        "verifying",
        reason="provider_outputs_admitted",
        expected_state="collecting",
    )
    return store, artifacts, manifest


def _identity(kind: str, value: str, owner: str) -> EngineeringIdentity:
    return EngineeringIdentity("vibecad", owner, kind, value, "1")


def _result(request) -> EngineeringResultEnvelope:
    descriptor = request.artifacts[0].descriptor
    source = _identity("document", request.source_document_uid, "native")
    activity = _identity("activity", "verification-1", request.domain)
    result = _identity("result", "result-1", request.domain)
    artifact = ContentDescriptor(
        descriptor.media_type,
        "sha256",
        descriptor.sha256,
        descriptor.byte_count,
        descriptor.role,
        "vibecad-analysis-artifact-v1",
    )
    finding = FindingEnvelope(
        "finding-1",
        "fixture-rule",
        "domain-verifier-fixture",
        request.domain,
        "pass",
        "note",
        "bounded",
        "Fixture-only domain verification evidence",
        (source,),
        (artifact,),
        "",
        "current",
        "model-unqualified",
    )
    graph = ProvenanceGraph(
        "verification-graph-1",
        (
            ProvenanceNode(
                source.canonical,
                "entity",
                canonical_payload({"role": "source"}),
            ),
            ProvenanceNode(
                activity.canonical,
                "activity",
                canonical_payload({"role": "verification"}),
            ),
            ProvenanceNode(
                result.canonical,
                "entity",
                canonical_payload({"role": "result"}),
            ),
        ),
        (
            ProvenanceEdge("used-source", "used", activity.canonical, source.canonical),
            ProvenanceEdge(
                "generated-result",
                "generated",
                result.canonical,
                activity.canonical,
            ),
        ),
    )
    return EngineeringResultEnvelope(
        1,
        0,
        result,
        activity,
        request.domain,
        request.adapter_id,
        request.provider_attempt_identity,
        "solved",
        "model-unqualified",
        "current",
        "unpublished",
        source,
        request.dependency_sha256,
        (artifact,),
        canonical_payload({"fixture_count": 1}),
        (finding,),
        graph,
        canonical_payload({"fixture_only": True}),
    )


def test_exact_admitted_outputs_are_reverified_and_stop_waiting_to_publish(
    tmp_path: Path,
) -> None:
    store, artifacts, manifest = _verifying_fixture(tmp_path)
    requests = []

    def verify_domain(request):
        requests.append(request)
        return _result(request)

    result = AnalysisDomainVerificationCoordinator(store, artifacts).verify(
        "analysis-1", manifest, verify_domain=verify_domain,
    )

    assert len(requests) == 1
    assert requests[0].artifacts[0].path == artifacts.path_for(
        manifest.artifacts[0].sha256
    )
    assert result.outcome == "waiting_to_publish"
    assert result.reason == "domain_outputs_verified"
    assert result.record["state"] == "waiting_to_publish"
    assert result.result_envelope.verification_verdict == "model-unqualified"
    assert result.publication_authorized is False
    assert result.record["publication"] == {
        "intent": None, "authorization": None, "receipt": None,
    }
    receipt = result.record["verification_receipts"][0]
    assert receipt["analysis_id"] == "analysis-1"
    assert receipt["attempt"] == 1
    assert receipt["output_manifest_sha256"] == manifest.sha256
    assert receipt["artifact_sha256"] == [manifest.artifacts[0].sha256]
    assert receipt["result_identity"] == result.result_envelope.result_id.canonical


def test_domain_verifier_unavailable_preserves_verifying_for_retry(
    tmp_path: Path,
) -> None:
    store, artifacts, manifest = _verifying_fixture(tmp_path)
    before = store.load("analysis-1")

    def unavailable(_request):
        raise DomainVerifierUnavailable("fixture verifier is offline")

    with pytest.raises(AnalysisDomainVerificationError) as refused:
        AnalysisDomainVerificationCoordinator(store, artifacts).verify(
            "analysis-1", manifest, verify_domain=unavailable,
        )

    assert refused.value.reason == "domain_verifier_unavailable"
    assert store.load("analysis-1") == before


def test_manifest_mismatch_is_refused_before_artifact_or_domain_access(
    tmp_path: Path,
) -> None:
    store, artifacts, manifest = _verifying_fixture(tmp_path)
    different = ArtifactManifest(
        ARTIFACT_MANIFEST_VERSION,
        (_descriptor(b"different output"),),
    )
    called = []
    before = store.load("analysis-1")

    with pytest.raises(AnalysisDomainVerificationError) as refused:
        AnalysisDomainVerificationCoordinator(store, artifacts).verify(
            "analysis-1",
            different,
            verify_domain=lambda request: called.append(request),
        )

    assert refused.value.reason == "verification_manifest_mismatch"
    assert called == []
    assert store.load("analysis-1") == before
    assert manifest.sha256 != different.sha256


def test_immutable_artifact_drift_fails_terminal_without_domain_or_publication(
    tmp_path: Path,
) -> None:
    store, artifacts, manifest = _verifying_fixture(tmp_path)
    artifacts.path_for(manifest.artifacts[0].sha256).write_bytes(b"tampered")
    called = []

    result = AnalysisDomainVerificationCoordinator(store, artifacts).verify(
        "analysis-1", manifest, verify_domain=lambda request: called.append(request),
    )

    assert result.outcome == "failed"
    assert result.reason == "verification_artifact_integrity_failed"
    assert result.record["state"] == "failed"
    assert result.record["publication"]["receipt"] is None
    assert result.publication_authorized is False
    assert called == []


def test_missing_immutable_artifact_preserves_verifying_for_storage_recovery(
    tmp_path: Path,
) -> None:
    store, artifacts, manifest = _verifying_fixture(tmp_path)
    artifacts.path_for(manifest.artifacts[0].sha256).unlink()
    before = store.load("analysis-1")
    called = []

    with pytest.raises(AnalysisDomainVerificationError) as refused:
        AnalysisDomainVerificationCoordinator(store, artifacts).verify(
            "analysis-1",
            manifest,
            verify_domain=lambda request: called.append(request),
        )

    assert refused.value.reason == "verification_storage_unavailable"
    assert called == []
    assert store.load("analysis-1") == before


def test_waiting_receipt_replay_rechecks_artifacts_before_publication_readiness(
    tmp_path: Path,
) -> None:
    store, artifacts, manifest = _verifying_fixture(tmp_path)
    coordinator = AnalysisDomainVerificationCoordinator(store, artifacts)
    completed = coordinator.verify(
        "analysis-1", manifest, verify_domain=lambda request: _result(request)
    )
    assert completed.record["state"] == "waiting_to_publish"
    artifacts.path_for(manifest.artifacts[0].sha256).write_bytes(b"tampered")

    replay = coordinator.verify(
        "analysis-1",
        manifest,
        verify_domain=lambda _request: pytest.fail(
            "a waiting receipt must replay without recomputing verification"
        ),
    )

    assert replay.outcome == "failed"
    assert replay.reason == "verification_artifact_integrity_failed"
    assert replay.record["state"] == "failed"
    assert replay.record["publication"]["receipt"] is None


@pytest.mark.parametrize(
    "mutate",
    (
        lambda envelope: replace(envelope, domain="aero"),
        lambda envelope: replace(envelope, adapter_id="different-adapter"),
        lambda envelope: replace(envelope, provider_attempt_id="different-attempt"),
        lambda envelope: replace(envelope, dependency_digest="e" * 64),
        lambda envelope: replace(
            envelope,
            source_identity=EngineeringIdentity(
                "vibecad", "native", "document", "different-document", "1"
            ),
        ),
        lambda envelope: replace(envelope, publication_state="published"),
        lambda envelope: replace(envelope, currentness="stale"),
        lambda envelope: replace(envelope, artifacts=()),
    ),
)
def test_mismatched_domain_receipt_fails_closed(
    tmp_path: Path,
    mutate,
) -> None:
    store, artifacts, manifest = _verifying_fixture(tmp_path)

    def mismatched(request):
        return mutate(_result(request))

    result = AnalysisDomainVerificationCoordinator(store, artifacts).verify(
        "analysis-1", manifest, verify_domain=mismatched,
    )

    assert result.outcome == "failed"
    assert result.reason == "domain_verification_invalid"
    assert result.record["state"] == "failed"
    assert result.record.get("verification_receipts", []) == []
    assert result.record["publication"]["receipt"] is None


def test_persisted_verification_receipt_resumes_without_rerunning_domain_verifier(
    tmp_path: Path,
) -> None:
    armed = {"value": False}

    def interrupt_transition(point: str, record: dict) -> None:
        if (
            armed["value"]
            and point == "before_replace"
            and record.get("state") == "waiting_to_publish"
        ):
            armed["value"] = False
            raise RuntimeError("simulated verification transition crash")

    store, artifacts, manifest = _verifying_fixture(
        tmp_path, fault_injector=interrupt_transition,
    )
    calls = []

    def verify_domain(request):
        calls.append(request)
        return _result(request)

    coordinator = AnalysisDomainVerificationCoordinator(store, artifacts)
    armed["value"] = True
    with pytest.raises(RuntimeError, match="simulated verification transition crash"):
        coordinator.verify("analysis-1", manifest, verify_domain=verify_domain)

    interrupted = store.load("analysis-1")
    assert interrupted["state"] == "verifying"
    assert len(interrupted["verification_receipts"]) == 1
    assert len(calls) == 1
    conflicting = dict(interrupted["verification_receipts"][0])
    conflicting["result_identity"] = "different-result"
    with pytest.raises(
        AnalysisPersistenceError, match="evidence cannot be rewritten"
    ):
        store.record_verification_receipt("analysis-1", conflicting)

    result = coordinator.verify(
        "analysis-1",
        manifest,
        verify_domain=lambda _request: pytest.fail(
            "a durable domain verification receipt must not be recomputed"
        ),
    )

    assert result.record["state"] == "waiting_to_publish"
    assert len(result.record["verification_receipts"]) == 1
    assert len(calls) == 1
