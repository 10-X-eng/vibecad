# SPDX-License-Identifier: LGPL-2.1-or-later

"""Receipt-bound domain verification of immutable Analysis outputs."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any, Callable, Mapping

from tool_impl.analysis_artifacts import (
    AnalysisArtifactError,
    ArtifactDescriptor,
    ArtifactManifest,
    ContentAddressedArtifactStore,
)
from tool_impl.analysis_persistence import (
    AnalysisMetadataStore,
    AnalysisPersistenceError,
    analysis_provider_attempt_identity,
)
from tool_impl.engineering_contracts import EngineeringResultEnvelope


class AnalysisDomainVerificationError(RuntimeError):
    """Domain verification cannot advance without guessing or losing evidence."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = str(reason or "").strip()
        super().__init__(str(message or "").strip())


class DomainVerifierUnavailable(RuntimeError):
    """A domain-owned verifier is temporarily unavailable and may be retried."""


@dataclass(frozen=True, slots=True)
class VerifiedAnalysisArtifact:
    descriptor: ArtifactDescriptor
    path: Path


@dataclass(frozen=True, slots=True)
class AnalysisDomainVerificationRequest:
    analysis_id: str
    domain: str
    adapter_id: str
    source_document_uid: str
    dependency_sha256: str
    attempt: int
    provider_attempt_identity: str
    output_manifest_sha256: str
    artifacts: tuple[VerifiedAnalysisArtifact, ...]


@dataclass(frozen=True, slots=True)
class AnalysisDomainVerificationResult:
    analysis_id: str
    attempt: int
    outcome: str
    reason: str
    record: dict[str, Any]
    result_envelope: EngineeringResultEnvelope | None
    publication_authorized: bool = field(default=False, init=False)


DomainVerifier = Callable[
    [AnalysisDomainVerificationRequest], EngineeringResultEnvelope
]


class AnalysisDomainVerificationCoordinator:
    """Reverify admitted bytes and persist one domain-owned result receipt."""

    def __init__(
        self,
        store: AnalysisMetadataStore,
        artifact_store: ContentAddressedArtifactStore,
    ) -> None:
        if not isinstance(store, AnalysisMetadataStore):
            raise TypeError("store must be AnalysisMetadataStore")
        if not isinstance(artifact_store, ContentAddressedArtifactStore):
            raise TypeError("artifact_store must be ContentAddressedArtifactStore")
        self.store = store
        self.artifact_store = artifact_store

    def verify(
        self,
        analysis_id: str,
        manifest: ArtifactManifest,
        *,
        verify_domain: DomainVerifier,
    ) -> AnalysisDomainVerificationResult:
        if not isinstance(manifest, ArtifactManifest):
            raise AnalysisDomainVerificationError(
                "verification_manifest_invalid",
                "Domain verification requires the immutable output manifest.",
            )
        if not callable(verify_domain):
            raise TypeError("verify_domain must be callable")
        record = self.store.load(analysis_id)
        _collection_receipt, attempt, attempt_identity = self._authorize_manifest(
            record, manifest
        )
        existing = self._receipt_for_attempt(record, attempt)
        if record["state"] not in {"verifying", "waiting_to_publish"}:
            raise AnalysisDomainVerificationError(
                "not_verifying",
                "Durable lifecycle state does not authorize domain verification.",
            )
        if record["state"] == "waiting_to_publish" and existing is None:
            raise AnalysisDomainVerificationError(
                "verification_receipt_invalid",
                "Publication readiness has no durable domain verification receipt.",
            )

        verified_artifacts = self._reverify_artifacts(record, manifest, attempt)
        if isinstance(verified_artifacts, AnalysisDomainVerificationResult):
            return verified_artifacts
        if record["state"] == "waiting_to_publish":
            envelope = self._validate_existing_receipt(
                record, manifest, existing, attempt_identity
            )
            return self._result(record, attempt, envelope)

        if existing is None:
            request = AnalysisDomainVerificationRequest(
                analysis_id=record["analysis_id"],
                domain=record["domain"],
                adapter_id=record["adapter_id"],
                source_document_uid=record["source_document_uid"],
                dependency_sha256=record["dependency_sha256"],
                attempt=attempt,
                provider_attempt_identity=attempt_identity,
                output_manifest_sha256=manifest.sha256,
                artifacts=verified_artifacts,
            )
            try:
                envelope = verify_domain(request)
            except DomainVerifierUnavailable as exc:
                raise AnalysisDomainVerificationError(
                    "domain_verifier_unavailable",
                    "The domain verifier is unavailable; verification remains "
                    "retryable.",
                ) from exc
            if not self._envelope_matches(
                record, manifest, envelope, attempt_identity
            ):
                return self._terminal_failure(
                    record, attempt, "domain_verification_invalid"
                )
            canonical = envelope.to_canonical_json()
            receipt = {
                "verified_at": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "analysis_id": record["analysis_id"],
                "attempt": attempt,
                "provider_attempt_identity": attempt_identity,
                "output_manifest_sha256": manifest.sha256,
                "artifact_sha256": [
                    descriptor.sha256 for descriptor in manifest.artifacts
                ],
                "result_identity": envelope.result_id.canonical,
                "result_sha256": hashlib.sha256(
                    canonical.encode("utf-8")
                ).hexdigest(),
                "result_envelope": envelope.to_dict(),
            }
            try:
                record = self.store.record_verification_receipt(
                    record["analysis_id"], receipt
                )
            except AnalysisPersistenceError as exc:
                raise AnalysisDomainVerificationError(
                    "verification_metadata_unavailable",
                    "Domain verification evidence could not be durably recorded.",
                ) from exc
        else:
            envelope = self._validate_existing_receipt(
                record, manifest, existing, attempt_identity
            )

        updated = self.store.transition(
            record["analysis_id"],
            "waiting_to_publish",
            reason="domain_outputs_verified",
            expected_state="verifying",
        )
        return self._result(updated, attempt, envelope)

    @staticmethod
    def _authorize_manifest(
        record: Mapping[str, Any],
        manifest: ArtifactManifest,
    ) -> tuple[Mapping[str, Any], int, str]:
        receipts = record.get("provider_collection_receipts")
        attempts = record.get("attempts")
        if not isinstance(receipts, list) or not receipts:
            raise AnalysisDomainVerificationError(
                "verification_manifest_mismatch",
                "Verification has no durable provider collection receipt.",
            )
        if not isinstance(attempts, list) or not attempts:
            raise AnalysisDomainVerificationError(
                "verification_manifest_mismatch",
                "Verification has no durable provider attempt.",
            )
        receipt = receipts[-1]
        attempt = attempts[-1]
        attempt_number = attempt.get("attempt")
        if (
            not isinstance(receipt, Mapping)
            or receipt.get("attempt") != attempt_number
            or receipt.get("provider_id") != attempt.get("provider_id")
            or receipt.get("provider_job_id") != attempt.get("provider_job_id")
            or receipt.get("output_manifest_sha256") != manifest.sha256
        ):
            raise AnalysisDomainVerificationError(
                "verification_manifest_mismatch",
                "Output manifest does not bind the latest collected provider attempt.",
            )
        try:
            identity = analysis_provider_attempt_identity(
                analysis_id=record["analysis_id"],
                attempt=int(attempt_number),
                provider_id=attempt["provider_id"],
                provider_job_id=attempt["provider_job_id"],
                output_manifest_sha256=manifest.sha256,
            )
        except (KeyError, TypeError, ValueError, AnalysisPersistenceError) as exc:
            raise AnalysisDomainVerificationError(
                "verification_manifest_mismatch",
                "Provider attempt identity is not durable or complete.",
            ) from exc
        return receipt, int(attempt_number), identity

    def _reverify_artifacts(
        self,
        record: Mapping[str, Any],
        manifest: ArtifactManifest,
        attempt: int,
    ) -> tuple[VerifiedAnalysisArtifact, ...] | AnalysisDomainVerificationResult:
        active = {
            item["sha256"]: item
            for item in record.get("artifacts", [])
            if isinstance(item, Mapping) and not item.get("tombstoned_at")
        }
        verified: list[VerifiedAnalysisArtifact] = []
        descriptor_fields = tuple(ArtifactDescriptor.__dataclass_fields__)
        for descriptor in manifest.artifacts:
            persisted = active.get(descriptor.sha256)
            if persisted is None or any(
                persisted.get(name) != getattr(descriptor, name)
                for name in descriptor_fields
            ):
                raise AnalysisDomainVerificationError(
                    "verification_artifact_mismatch",
                    "Output artifact metadata does not match immutable admission.",
                )
            try:
                path = self.artifact_store.verify_admitted(descriptor)
            except AnalysisArtifactError as exc:
                if exc.reason == "read_failed":
                    raise AnalysisDomainVerificationError(
                        "verification_storage_unavailable",
                        "Immutable output storage is not durably readable.",
                    ) from exc
                return self._terminal_failure(
                    record, attempt, "verification_artifact_integrity_failed"
                )
            verified.append(VerifiedAnalysisArtifact(descriptor, path))
        return tuple(verified)

    @staticmethod
    def _receipt_for_attempt(
        record: Mapping[str, Any], attempt: int
    ) -> Mapping[str, Any] | None:
        receipts = record.get("verification_receipts", [])
        if not isinstance(receipts, list):
            return None
        return next(
            (
                item
                for item in receipts
                if isinstance(item, Mapping) and item.get("attempt") == attempt
            ),
            None,
        )

    def _validate_existing_receipt(
        self,
        record: Mapping[str, Any],
        manifest: ArtifactManifest,
        receipt: Mapping[str, Any],
        attempt_identity: str,
    ) -> EngineeringResultEnvelope:
        try:
            envelope = EngineeringResultEnvelope.from_dict(
                receipt["result_envelope"]
            )
            canonical = envelope.to_canonical_json()
            valid = (
                receipt.get("analysis_id") == record["analysis_id"]
                and receipt.get("provider_attempt_identity") == attempt_identity
                and receipt.get("output_manifest_sha256") == manifest.sha256
                and receipt.get("artifact_sha256")
                == [descriptor.sha256 for descriptor in manifest.artifacts]
                and receipt.get("result_identity") == envelope.result_id.canonical
                and receipt.get("result_sha256")
                == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
                and self._envelope_matches(
                    record, manifest, envelope, attempt_identity
                )
            )
        except Exception as exc:
            raise AnalysisDomainVerificationError(
                "verification_receipt_invalid",
                "Durable domain verification evidence is malformed.",
            ) from exc
        if not valid:
            raise AnalysisDomainVerificationError(
                "verification_receipt_mismatch",
                "Durable domain verification evidence does not bind this output set.",
            )
        return envelope

    @staticmethod
    def _envelope_matches(
        record: Mapping[str, Any],
        manifest: ArtifactManifest,
        envelope: Any,
        attempt_identity: str,
    ) -> bool:
        if not isinstance(envelope, EngineeringResultEnvelope):
            return False
        artifact_by_digest = {
            descriptor.sha256: descriptor for descriptor in manifest.artifacts
        }
        envelope_digests = [item.digest for item in envelope.artifacts]
        if envelope_digests != [item.sha256 for item in manifest.artifacts]:
            return False
        if any(
            item.byte_size != artifact_by_digest[item.digest].byte_count
            for item in envelope.artifacts
        ):
            return False
        return (
            envelope.domain == record.get("domain")
            and envelope.adapter_id == record.get("adapter_id")
            and envelope.provider_attempt_id == attempt_identity
            and envelope.source_identity.kind == "document"
            and envelope.source_identity.value == record.get("source_document_uid")
            and envelope.dependency_digest == record.get("dependency_sha256")
            and envelope.currentness == "current"
            and envelope.publication_state == "unpublished"
            and all(
                finding.domain == record.get("domain")
                and all(
                    evidence.digest in artifact_by_digest
                    for evidence in finding.evidence
                )
                for finding in envelope.findings
            )
        )

    def _terminal_failure(
        self,
        record: Mapping[str, Any],
        attempt: int,
        reason: str,
    ) -> AnalysisDomainVerificationResult:
        attempts = deepcopy(record["attempts"])
        attempts[attempt - 1]["terminal_reason"] = reason
        updated = self.store.transition(
            record["analysis_id"],
            "failed",
            reason=reason,
            updates={"attempts": attempts},
            expected_state=record["state"],
        )
        return AnalysisDomainVerificationResult(
            analysis_id=record["analysis_id"],
            attempt=attempt,
            outcome="failed",
            reason=reason,
            record=deepcopy(updated),
            result_envelope=None,
        )

    @staticmethod
    def _result(
        record: Mapping[str, Any],
        attempt: int,
        envelope: EngineeringResultEnvelope,
    ) -> AnalysisDomainVerificationResult:
        return AnalysisDomainVerificationResult(
            analysis_id=record["analysis_id"],
            attempt=attempt,
            outcome="waiting_to_publish",
            reason="domain_outputs_verified",
            record=deepcopy(dict(record)),
            result_envelope=envelope,
        )
