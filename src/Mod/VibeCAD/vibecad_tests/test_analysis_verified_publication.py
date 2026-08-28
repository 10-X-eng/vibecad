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
    AnalysisMetadataStore,
    AnalysisPersistenceError,
    new_job_record,
)
from VibeCADAnalysisPublication import (
    AnalysisPublicationError,
    CurrentnessReport,
    VerifiedAnalysisPublicationCoordinator,
    VerifiedPublicationAuthorization,
    VerifiedPublicationDescriptor,
)
from VibeCADEngineeringContracts import (
    ContentDescriptor,
    EngineeringIdentity,
    EngineeringResultEnvelope,
    ProvenanceGraph,
    canonical_payload,
)


def _artifact_descriptor(content: bytes = b"verified output") -> ArtifactDescriptor:
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


def _domain_result(request) -> EngineeringResultEnvelope:
    artifact = request.artifacts[0].descriptor
    source = EngineeringIdentity(
        "vibecad", "native", "document", request.source_document_uid, "1"
    )
    return EngineeringResultEnvelope(
        1,
        0,
        EngineeringIdentity("vibecad", "fem", "result", "result-1", "1"),
        EngineeringIdentity("vibecad", "fem", "activity", "verify-1", "1"),
        request.domain,
        request.adapter_id,
        request.provider_attempt_identity,
        "solved",
        "model-unqualified",
        "current",
        "unpublished",
        source,
        request.dependency_sha256,
        (
            ContentDescriptor(
                artifact.media_type,
                "sha256",
                artifact.sha256,
                artifact.byte_count,
                artifact.role,
                "vibecad-analysis-artifact-v1",
            ),
        ),
        canonical_payload({"fixture_count": 1}),
        (),
        ProvenanceGraph("verification-graph-1", (), ()),
        canonical_payload({"fixture_only": True}),
    )


def _verified_fixture(
    tmp_path: Path,
) -> tuple[
    AnalysisMetadataStore,
    ContentAddressedArtifactStore,
    ArtifactManifest,
    VerifiedPublicationDescriptor,
    VerifiedPublicationAuthorization,
]:
    content = b"verified output"
    artifact = _artifact_descriptor(content)
    manifest = ArtifactManifest(ARTIFACT_MANIFEST_VERSION, (artifact,))
    store = AnalysisMetadataStore(tmp_path / "metadata")
    store.create(
        new_job_record(
            analysis_id="analysis-1",
            domain="fem",
            adapter_id="vibecad.native.analyze.fem",
            source_document_uid="document-uid",
            prepared_analysis_sha256="a" * 64,
            dependency_sha256="b" * 64,
            input_manifest_sha256="c" * 64,
            execution_spec_sha256="d" * 64,
        )
    )
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
        updates={
            "provider_collection_receipts": [
                {
                    "collected_at": "2026-08-27T00:00:01Z",
                    "attempt": 1,
                    "provider_id": "remote-fixture",
                    "provider_job_id": "remote-job-7",
                    "output_manifest_sha256": manifest.sha256,
                }
            ]
        },
        expected_state="running_remote",
    )
    artifacts = ContentAddressedArtifactStore(tmp_path / "objects")
    source = tmp_path / "transport" / artifact.relative_path
    source.parent.mkdir(parents=True)
    source.write_bytes(content)
    artifacts.admit(source, artifact)
    store.record_artifact(
        "analysis-1",
        {name: getattr(artifact, name) for name in artifact.__dataclass_fields__},
        expected_state="collecting",
    )
    store.transition(
        "analysis-1",
        "verifying",
        reason="provider_outputs_admitted",
        expected_state="collecting",
    )
    verified = AnalysisDomainVerificationCoordinator(store, artifacts).verify(
        "analysis-1", manifest, verify_domain=_domain_result
    )
    receipt = verified.record["verification_receipts"][0]
    descriptor = VerifiedPublicationDescriptor(
        publication_id="publication-1",
        analysis_id="analysis-1",
        attempt=1,
        domain_id="fem",
        adapter_id="vibecad.native.analyze.fem",
        adapter_version="1",
        source_document_uid="document-uid",
        frozen_dependency_sha256="b" * 64,
        output_manifest_sha256=manifest.sha256,
        provider_attempt_identity=receipt["provider_attempt_identity"],
        result_identity=receipt["result_identity"],
        result_sha256=receipt["result_sha256"],
    )
    authorization = VerifiedPublicationAuthorization(
        publication_id=descriptor.publication_id,
        publication_descriptor_sha256=descriptor.sha256,
        authorization_id="authorization-1",
        authorized_at="2026-08-27T00:00:02Z",
    )
    return store, artifacts, manifest, descriptor, authorization


def _publish(
    coordinator: VerifiedAnalysisPublicationCoordinator,
    descriptor: VerifiedPublicationDescriptor,
    authorization: VerifiedPublicationAuthorization,
    **overrides,
):
    document = {"uid": "document-uid", "objects": []}
    requests = []

    def mutate(target, request):
        requests.append(request)
        target["objects"].append("Result")
        return {"created_objects": ["Result"], "structural_revision": "2"}

    arguments = {
        "resolve_document": lambda uid: document if uid == document["uid"] else None,
        "evaluate_currentness": lambda _document, _descriptor: CurrentnessReport(
            True, True
        ),
        "adapter_is_compatible": lambda _descriptor: True,
        "mutate_document": mutate,
        "verify_postconditions": lambda target, result: (
            result["created_objects"] == ["Result"]
            and target["objects"] == ["Result"]
        ),
    }
    arguments.update(overrides)
    receipt = coordinator.publish(descriptor, authorization, **arguments)
    return receipt, document, requests


def test_verified_publication_rechecks_receipt_and_artifacts_then_mutates_once(
    tmp_path: Path,
) -> None:
    store, artifacts, manifest, descriptor, authorization = _verified_fixture(tmp_path)
    coordinator = VerifiedAnalysisPublicationCoordinator(store, artifacts)

    receipt, document, requests = _publish(
        coordinator, descriptor, authorization
    )

    assert document["objects"] == ["Result"]
    assert len(requests) == 1
    assert requests[0].descriptor == descriptor
    assert requests[0].result_envelope.result_id.canonical == descriptor.result_identity
    assert requests[0].artifacts[0].path == artifacts.path_for(
        manifest.artifacts[0].sha256
    )
    assert receipt["publication_descriptor_sha256"] == descriptor.sha256
    assert receipt["artifact_sha256"] == [manifest.artifacts[0].sha256]
    assert store.load("analysis-1")["state"] == "succeeded"

    replay, second_document, replay_requests = _publish(
        coordinator, descriptor, authorization
    )
    assert replay == receipt
    assert second_document["objects"] == []
    assert replay_requests == []


@pytest.mark.parametrize(
    ("field", "replacement", "reason"),
    (
        ("attempt", 2, "verification_receipt_mismatch"),
        ("domain_id", "aero", "verification_receipt_mismatch"),
        ("output_manifest_sha256", "9" * 64, "verification_receipt_mismatch"),
        (
            "result_identity",
            "vibecad:fem:result:1:other",
            "verification_receipt_mismatch",
        ),
        ("result_sha256", "8" * 64, "verification_receipt_mismatch"),
    ),
)
def test_verified_publication_refuses_descriptor_drift_before_document_access(
    tmp_path: Path, field: str, replacement, reason: str,
) -> None:
    store, artifacts, _manifest, descriptor, authorization = _verified_fixture(tmp_path)
    changed = replace(descriptor, **{field: replacement})
    calls = []

    with pytest.raises(AnalysisPublicationError) as caught:
        _publish(
            VerifiedAnalysisPublicationCoordinator(store, artifacts),
            changed,
            replace(
                authorization,
                publication_descriptor_sha256=changed.sha256,
            ),
            resolve_document=lambda _uid: calls.append("document"),
        )

    assert caught.value.reason == reason
    assert calls == []
    assert store.load("analysis-1")["state"] == "waiting_to_publish"


def test_verified_publication_requires_exact_fresh_authorization(
    tmp_path: Path,
) -> None:
    store, artifacts, _manifest, descriptor, authorization = _verified_fixture(tmp_path)
    bad = replace(authorization, publication_descriptor_sha256="f" * 64)

    with pytest.raises(AnalysisPublicationError) as caught:
        _publish(
            VerifiedAnalysisPublicationCoordinator(store, artifacts),
            descriptor,
            bad,
        )

    assert caught.value.reason == "authorization_mismatch"
    assert store.load("analysis-1")["state"] == "waiting_to_publish"


def test_verified_publication_requires_durable_domain_verification(
    tmp_path: Path,
) -> None:
    store = AnalysisMetadataStore(tmp_path / "metadata")
    store.create(
        new_job_record(
            analysis_id="analysis-1",
            domain="fem",
            adapter_id="vibecad.native.analyze.fem",
            source_document_uid="document-uid",
            prepared_analysis_sha256="a" * 64,
            dependency_sha256="b" * 64,
            input_manifest_sha256="c" * 64,
            execution_spec_sha256="d" * 64,
        )
    )
    store.begin_attempt(
        "analysis-1",
        provider_id="local-fixture",
        provider_kind="local",
        provider_job_id="local-1",
    )
    for state in ("collecting", "verifying", "waiting_to_publish"):
        store.transition("analysis-1", state, reason="legacy-fixture")
    descriptor = VerifiedPublicationDescriptor(
        "publication-1",
        "analysis-1",
        1,
        "fem",
        "vibecad.native.analyze.fem",
        "1",
        "document-uid",
        "b" * 64,
        "e" * 64,
        "f" * 64,
        "vibecad:fem:result:1:result-1",
        "9" * 64,
    )
    authorization = VerifiedPublicationAuthorization(
        "publication-1", descriptor.sha256, "authorization-1", "now"
    )

    with pytest.raises(AnalysisPublicationError) as caught:
        _publish(
            VerifiedAnalysisPublicationCoordinator(
                store, ContentAddressedArtifactStore(tmp_path / "objects")
            ),
            descriptor,
            authorization,
        )

    assert caught.value.reason == "verification_receipt_missing"
    assert store.load("analysis-1")["state"] == "waiting_to_publish"


def test_verified_publication_rechecks_currentness_before_owner_acquisition(
    tmp_path: Path,
) -> None:
    store, artifacts, _manifest, descriptor, authorization = _verified_fixture(tmp_path)

    with pytest.raises(AnalysisPublicationError) as caught:
        _publish(
            VerifiedAnalysisPublicationCoordinator(store, artifacts),
            descriptor,
            authorization,
            evaluate_currentness=lambda _document, _descriptor: CurrentnessReport(
                False, True, ("geometry",)
            ),
        )

    assert caught.value.reason == "stale"
    assert store.load("analysis-1")["state"] == "waiting_to_publish"


def test_verified_publication_rejects_artifact_drift_without_document_access(
    tmp_path: Path,
) -> None:
    store, artifacts, manifest, descriptor, authorization = _verified_fixture(tmp_path)
    artifacts.path_for(manifest.artifacts[0].sha256).write_bytes(b"tampered output")
    calls = []

    with pytest.raises(AnalysisPublicationError) as caught:
        _publish(
            VerifiedAnalysisPublicationCoordinator(store, artifacts),
            descriptor,
            authorization,
            resolve_document=lambda _uid: calls.append("document"),
        )

    assert caught.value.reason == "publication_artifact_integrity_failed"
    assert calls == []
    assert store.load("analysis-1")["state"] == "failed"


def test_verified_publication_missing_storage_is_retryable(
    tmp_path: Path,
) -> None:
    store, artifacts, manifest, descriptor, authorization = _verified_fixture(tmp_path)
    artifacts.path_for(manifest.artifacts[0].sha256).unlink()

    with pytest.raises(AnalysisPublicationError) as caught:
        _publish(
            VerifiedAnalysisPublicationCoordinator(store, artifacts),
            descriptor,
            authorization,
        )

    assert caught.value.reason == "publication_storage_unavailable"
    assert store.load("analysis-1")["state"] == "waiting_to_publish"


def test_verified_publication_unknown_mutation_outcome_is_never_replayed(
    tmp_path: Path,
) -> None:
    store, artifacts, _manifest, descriptor, authorization = _verified_fixture(tmp_path)
    coordinator = VerifiedAnalysisPublicationCoordinator(store, artifacts)

    def outcome_unknown(document, _request):
        document["objects"].append("Result")
        raise RuntimeError("host exited after document mutation")

    with pytest.raises(RuntimeError):
        _publish(
            coordinator,
            descriptor,
            authorization,
            mutate_document=outcome_unknown,
        )
    assert store.load("analysis-1")["state"] == "publishing"

    with pytest.raises(AnalysisPublicationError) as replay:
        _publish(coordinator, descriptor, authorization)
    assert replay.value.reason == "outcome_unknown"


@pytest.mark.parametrize(
    "unsafe_result",
    (
        {"access_token": "must-not-persist"},
        {"artifact_path": "C:/private/publication/result.dat"},
    ),
)
def test_verified_publication_rejects_secret_or_path_mutation_evidence(
    tmp_path: Path, unsafe_result: dict,
) -> None:
    store, artifacts, _manifest, descriptor, authorization = _verified_fixture(tmp_path)

    with pytest.raises(AnalysisPublicationError) as caught:
        _publish(
            VerifiedAnalysisPublicationCoordinator(store, artifacts),
            descriptor,
            authorization,
            mutate_document=lambda _document, _request: unsafe_result,
            verify_postconditions=lambda _document, _result: True,
        )

    assert caught.value.reason == "mutation_result"
    record = store.load("analysis-1")
    assert record["state"] == "publishing"
    assert record["publication"]["receipt"] is None


def test_durable_receipt_finalizes_after_transition_crash_without_remutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, artifacts, _manifest, descriptor, authorization = _verified_fixture(tmp_path)
    coordinator = VerifiedAnalysisPublicationCoordinator(store, artifacts)
    original_transition = store.transition
    failed_once = False

    def fail_after_receipt(analysis_id, state, **kwargs):
        nonlocal failed_once
        if state == "succeeded" and not failed_once:
            failed_once = True
            raise AnalysisPersistenceError("simulated final transition crash")
        return original_transition(analysis_id, state, **kwargs)

    monkeypatch.setattr(store, "transition", fail_after_receipt)
    with pytest.raises(AnalysisPublicationError) as caught:
        _publish(coordinator, descriptor, authorization)
    assert caught.value.reason == "completion_failed"
    after_crash = store.load("analysis-1")
    assert after_crash["state"] == "publishing"
    assert after_crash["publication"]["receipt"] is not None
    assert store.restart_disposition("analysis-1")["action"] == (
        "finalize_publication_receipt"
    )

    receipt, document, requests = _publish(coordinator, descriptor, authorization)
    assert receipt == store.load("analysis-1")["publication"]["receipt"]
    assert store.load("analysis-1")["state"] == "succeeded"
    assert document["objects"] == []
    assert requests == []


def test_verified_publication_receipt_is_write_once(tmp_path: Path) -> None:
    store, artifacts, _manifest, descriptor, authorization = _verified_fixture(tmp_path)
    coordinator = VerifiedAnalysisPublicationCoordinator(store, artifacts)
    receipt, _document, _requests = _publish(coordinator, descriptor, authorization)

    with pytest.raises(AnalysisPersistenceError):
        store.record_publication_receipt(
            "analysis-1", {**receipt, "authorization_id": "rewritten"}
        )
