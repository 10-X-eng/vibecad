# SPDX-License-Identifier: LGPL-2.1-or-later

"""Independent, replay-safe publication authority for durable Analysis jobs."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from tool_impl.analysis_artifacts import (
    AnalysisArtifactError,
    ArtifactDescriptor,
    ContentAddressedArtifactStore,
)
from tool_impl.analysis_contracts import AnalysisContractError
from tool_impl.analysis_persistence import (
    AnalysisMetadataStore,
    AnalysisPersistenceError,
    VERIFIED_PUBLICATION_AUTHORIZATION_VERSION,
    VERIFIED_PUBLICATION_INTENT_VERSION,
    VERIFIED_PUBLICATION_RECEIPT_VERSION,
)
from tool_impl.engineering_contracts import (
    EngineeringResultEnvelope,
    canonical_payload,
)


class AnalysisPublicationError(RuntimeError):
    def __init__(self, reason: str, message: str) -> None:
        self.reason = str(reason)
        super().__init__(str(message))


@dataclass(frozen=True, slots=True)
class PublicationDescriptor:
    publication_id: str
    analysis_id: str
    domain_id: str
    adapter_id: str
    adapter_version: str
    source_document_uid: str
    frozen_dependency_sha256: str
    output_manifest_sha256: str
    result_identity: str

    def __post_init__(self) -> None:
        for field in (
            "publication_id", "analysis_id", "domain_id", "adapter_id",
            "adapter_version", "source_document_uid", "result_identity",
        ):
            if not str(getattr(self, field) or "").strip():
                raise ValueError(f"{field} must be non-empty")
        for field in ("frozen_dependency_sha256", "output_manifest_sha256"):
            value = str(getattr(self, field) or "").lower()
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise ValueError(f"{field} must be a SHA-256 digest")


@dataclass(frozen=True, slots=True)
class CurrentnessReport:
    current: bool
    source_resolved: bool
    changed_dependencies: tuple[str, ...] = ()
    ambiguous_dependencies: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PublicationAuthorization:
    publication_id: str
    result_identity: str
    authorization_id: str
    authorized_at: str


@dataclass(frozen=True, slots=True)
class VerifiedPublicationDescriptor:
    """Exact verified-result identity eligible for one publication decision."""

    publication_id: str
    analysis_id: str
    attempt: int
    domain_id: str
    adapter_id: str
    adapter_version: str
    source_document_uid: str
    frozen_dependency_sha256: str
    output_manifest_sha256: str
    provider_attempt_identity: str
    result_identity: str
    result_sha256: str

    def __post_init__(self) -> None:
        for field in (
            "publication_id",
            "analysis_id",
            "domain_id",
            "adapter_id",
            "adapter_version",
            "source_document_uid",
            "provider_attempt_identity",
            "result_identity",
        ):
            value = str(getattr(self, field) or "").strip()
            if not value:
                raise ValueError(f"{field} must be non-empty")
            object.__setattr__(self, field, value)
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("attempt must be a positive integer")
        for field in (
            "frozen_dependency_sha256",
            "output_manifest_sha256",
            "provider_attempt_identity",
            "result_sha256",
        ):
            value = str(getattr(self, field) or "").lower()
            if len(value) != 64 or any(
                character not in "0123456789abcdef" for character in value
            ):
                raise ValueError(f"{field} must be a SHA-256 digest")
            object.__setattr__(self, field, value)

    def to_dict(self) -> dict[str, Any]:
        return {
            name: getattr(self, name)
            for name in self.__dataclass_fields__
        }

    @property
    def sha256(self) -> str:
        canonical = json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class VerifiedPublicationAuthorization:
    """Fresh authorization bound to one exact publication descriptor hash."""

    publication_id: str
    publication_descriptor_sha256: str
    authorization_id: str
    authorized_at: str

    def __post_init__(self) -> None:
        for field in ("publication_id", "authorization_id", "authorized_at"):
            value = str(getattr(self, field) or "").strip()
            if not value:
                raise ValueError(f"{field} must be non-empty")
            object.__setattr__(self, field, value)
        digest = str(self.publication_descriptor_sha256 or "").lower()
        if len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise ValueError(
                "publication_descriptor_sha256 must be a SHA-256 digest"
            )
        object.__setattr__(self, "publication_descriptor_sha256", digest)


@dataclass(frozen=True, slots=True)
class VerifiedPublicationArtifact:
    descriptor: ArtifactDescriptor
    path: Path


@dataclass(frozen=True, slots=True)
class VerifiedPublicationRequest:
    """Ephemeral inputs supplied to the document-thread publication owner."""

    descriptor: VerifiedPublicationDescriptor
    result_envelope: EngineeringResultEnvelope
    artifacts: tuple[VerifiedPublicationArtifact, ...]


class AnalysisPublicationCoordinator:
    """Validate, acquire one owner, transact, verify, and persist one receipt."""

    def __init__(self, store: AnalysisMetadataStore) -> None:
        if not isinstance(store, AnalysisMetadataStore):
            raise TypeError("store must be AnalysisMetadataStore")
        self.store = store

    def publish(
        self,
        descriptor: PublicationDescriptor,
        authorization: PublicationAuthorization,
        *,
        resolve_document: Callable[[str], Any | None],
        validate_output_manifest: Callable[[PublicationDescriptor], bool],
        evaluate_currentness: Callable[[Any, PublicationDescriptor], CurrentnessReport],
        adapter_is_compatible: Callable[[PublicationDescriptor], bool],
        mutate_document: Callable[[Any, PublicationDescriptor], Mapping[str, Any]],
        verify_postconditions: Callable[[Any, Mapping[str, Any]], bool],
    ) -> dict[str, Any]:
        record = self.store.load(descriptor.analysis_id)
        publication = record.get("publication") or {}
        existing = publication.get("receipt")
        if isinstance(existing, Mapping):
            if existing.get("publication_id") != descriptor.publication_id:
                raise AnalysisPublicationError("receipt_conflict", "A different publication receipt already exists")
            return dict(existing)
        if record["state"] == "publishing":
            raise AnalysisPublicationError("outcome_unknown", "Publication ownership began without a durable receipt")
        if record["state"] != "waiting_to_publish":
            raise AnalysisPublicationError("not_ready", "Analysis is not waiting for publication")
        if descriptor.source_document_uid != record["source_document_uid"]:
            raise AnalysisPublicationError("source_mismatch", "Descriptor does not bind the persisted source document")
        if descriptor.frozen_dependency_sha256 != record["dependency_sha256"]:
            raise AnalysisPublicationError("dependency_mismatch", "Descriptor does not bind frozen dependencies")
        if not validate_output_manifest(descriptor):
            raise AnalysisPublicationError("invalid_outputs", "Output manifest validation failed")
        document = resolve_document(descriptor.source_document_uid)
        if document is None:
            raise AnalysisPublicationError("source_unavailable", "Exact source document UID is not open")
        report = evaluate_currentness(document, descriptor)
        if not isinstance(report, CurrentnessReport):
            raise TypeError("evaluate_currentness must return CurrentnessReport")
        if not report.source_resolved:
            raise AnalysisPublicationError("source_unresolved", "Domain targets are not unambiguously resolved")
        if report.ambiguous_dependencies:
            raise AnalysisPublicationError("ambiguous", "Dependency currentness is ambiguous")
        if not report.current or report.changed_dependencies:
            raise AnalysisPublicationError("stale", "Source dependencies changed after preparation")
        if not adapter_is_compatible(descriptor):
            raise AnalysisPublicationError("adapter_incompatible", "Publication adapter version is incompatible")
        if (
            authorization.publication_id != descriptor.publication_id
            or authorization.result_identity != descriptor.result_identity
            or not authorization.authorization_id.strip()
        ):
            raise AnalysisPublicationError("authorization_mismatch", "Fresh authorization does not bind this publication")

        intent = {
            "publication_id": descriptor.publication_id,
            "result_identity": descriptor.result_identity,
            "output_manifest_sha256": descriptor.output_manifest_sha256,
        }
        self.store.transition(
            descriptor.analysis_id,
            "publishing",
            reason="publication_owner_acquired",
            updates={"publication": {
                "intent": intent,
                "authorization": {
                    "authorization_id": authorization.authorization_id,
                    "authorized_at": authorization.authorized_at,
                    "publication_id": authorization.publication_id,
                    "result_identity": authorization.result_identity,
                },
                "receipt": None,
            }},
        )
        try:
            result = mutate_document(document, descriptor)
            if not isinstance(result, Mapping):
                raise AnalysisPublicationError("mutation_result", "Publication mutation did not return a receipt draft")
            if not verify_postconditions(document, result):
                raise AnalysisPublicationError("postcondition_failed", "Publication postconditions failed")
        except Exception:
            # The persisted publishing state is intentionally retained. The
            # document transaction owner must roll back; restart cannot infer
            # whether mutation happened and therefore cannot replay blindly.
            raise
        receipt = {
            "publication_id": descriptor.publication_id,
            "analysis_id": descriptor.analysis_id,
            "result_identity": descriptor.result_identity,
            "authorization_id": authorization.authorization_id,
            "published_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "result": dict(result),
        }
        try:
            self.store.transition(
                descriptor.analysis_id,
                "succeeded",
                reason="published",
                updates={"publication": {
                    "intent": intent,
                    "authorization": record.get("publication", {}).get("authorization") or {
                        "authorization_id": authorization.authorization_id,
                        "authorized_at": authorization.authorized_at,
                        "publication_id": authorization.publication_id,
                        "result_identity": authorization.result_identity,
                    },
                    "receipt": receipt,
                }},
            )
        except AnalysisPersistenceError as exc:
            raise AnalysisPublicationError("receipt_failed", "Publication receipt could not be persisted") from exc
        return receipt


class VerifiedAnalysisPublicationCoordinator:
    """Publish one receipt-bound verified result without replaying mutation."""

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

    def publish(
        self,
        descriptor: VerifiedPublicationDescriptor,
        authorization: VerifiedPublicationAuthorization,
        *,
        resolve_document: Callable[[str], Any | None],
        evaluate_currentness: Callable[
            [Any, VerifiedPublicationDescriptor], CurrentnessReport
        ],
        adapter_is_compatible: Callable[[VerifiedPublicationDescriptor], bool],
        mutate_document: Callable[
            [Any, VerifiedPublicationRequest], Mapping[str, Any]
        ],
        verify_postconditions: Callable[[Any, Mapping[str, Any]], bool],
    ) -> dict[str, Any]:
        if not isinstance(descriptor, VerifiedPublicationDescriptor):
            raise TypeError("descriptor must be VerifiedPublicationDescriptor")
        if not isinstance(authorization, VerifiedPublicationAuthorization):
            raise TypeError("authorization must be VerifiedPublicationAuthorization")

        record = self.store.load(descriptor.analysis_id)
        publication = record.get("publication") or {}
        existing = publication.get("receipt")
        if isinstance(existing, Mapping):
            self._validate_published_receipt(
                record, descriptor, authorization, existing
            )
            if record["state"] == "publishing":
                try:
                    self.store.transition(
                        descriptor.analysis_id,
                        "succeeded",
                        reason="verified_publication_receipt_reconciled",
                        expected_state="publishing",
                    )
                except AnalysisPersistenceError as exc:
                    raise AnalysisPublicationError(
                        "completion_failed",
                        "The durable publication receipt could not be finalized.",
                    ) from exc
            elif record["state"] != "succeeded":
                raise AnalysisPublicationError(
                    "receipt_state_invalid",
                    "A publication receipt exists in an invalid lifecycle state.",
                )
            return dict(existing)
        if record["state"] == "publishing":
            raise AnalysisPublicationError(
                "outcome_unknown",
                "Publication ownership began without a durable receipt.",
            )
        if record["state"] != "waiting_to_publish":
            raise AnalysisPublicationError(
                "not_ready", "Analysis is not waiting for publication."
            )

        envelope, verification_receipt = self._bind_verification_receipt(
            record, descriptor
        )
        artifacts = self._reverify_artifacts(record, verification_receipt)
        request = VerifiedPublicationRequest(descriptor, envelope, artifacts)

        document = resolve_document(descriptor.source_document_uid)
        if document is None:
            raise AnalysisPublicationError(
                "source_unavailable", "Exact source document UID is not open."
            )
        report = evaluate_currentness(document, descriptor)
        if not isinstance(report, CurrentnessReport):
            raise TypeError("evaluate_currentness must return CurrentnessReport")
        if (
            type(report.current) is not bool
            or type(report.source_resolved) is not bool
            or not isinstance(report.changed_dependencies, tuple)
            or not isinstance(report.ambiguous_dependencies, tuple)
            or any(
                not isinstance(value, str) or not value.strip()
                for value in (
                    *report.changed_dependencies,
                    *report.ambiguous_dependencies,
                )
            )
        ):
            raise TypeError("CurrentnessReport contains invalid evidence")
        if not report.source_resolved:
            raise AnalysisPublicationError(
                "source_unresolved",
                "Domain targets are not unambiguously resolved.",
            )
        if report.ambiguous_dependencies:
            raise AnalysisPublicationError(
                "ambiguous", "Dependency currentness is ambiguous."
            )
        if not report.current or report.changed_dependencies:
            raise AnalysisPublicationError(
                "stale", "Source dependencies changed after preparation."
            )
        if adapter_is_compatible(descriptor) is not True:
            raise AnalysisPublicationError(
                "adapter_incompatible",
                "Publication adapter version is incompatible.",
            )
        if (
            authorization.publication_id != descriptor.publication_id
            or authorization.publication_descriptor_sha256 != descriptor.sha256
        ):
            raise AnalysisPublicationError(
                "authorization_mismatch",
                "Fresh authorization does not bind this verified publication.",
            )

        verification_receipt_sha256 = self._canonical_sha256(
            verification_receipt
        )
        artifact_sha256 = list(verification_receipt["artifact_sha256"])
        intent = {
            "schema_version": VERIFIED_PUBLICATION_INTENT_VERSION,
            "publication_descriptor": descriptor.to_dict(),
            "publication_descriptor_sha256": descriptor.sha256,
            "verification_receipt_sha256": verification_receipt_sha256,
            "artifact_references": artifact_sha256,
            "currentness": {
                "current": True,
                "source_resolved": True,
                "changed_dependencies": [],
                "ambiguous_dependencies": [],
            },
        }
        authorization_evidence = {
            "schema_version": VERIFIED_PUBLICATION_AUTHORIZATION_VERSION,
            "publication_id": authorization.publication_id,
            "publication_descriptor_sha256": (
                authorization.publication_descriptor_sha256
            ),
            "authorization_id": authorization.authorization_id,
            "authorized_at": authorization.authorized_at,
        }
        try:
            self.store.transition(
                descriptor.analysis_id,
                "publishing",
                reason="verified_publication_owner_acquired",
                updates={
                    "publication": {
                        "intent": intent,
                        "authorization": authorization_evidence,
                        "receipt": None,
                    }
                },
                expected_state="waiting_to_publish",
            )
        except AnalysisPersistenceError as exc:
            raise AnalysisPublicationError(
                "owner_conflict", "Publication ownership could not be acquired."
            ) from exc

        result = mutate_document(document, request)
        if not isinstance(result, Mapping):
            raise AnalysisPublicationError(
                "mutation_result",
                "Publication mutation did not return a receipt draft.",
            )
        if verify_postconditions(document, result) is not True:
            raise AnalysisPublicationError(
                "postcondition_failed", "Publication postconditions failed."
            )
        try:
            clean_result = canonical_payload(
                dict(result), "publication mutation result"
            ).to_value()
        except AnalysisContractError as exc:
            raise AnalysisPublicationError(
                "mutation_result",
                "Publication mutation evidence is unsafe or not canonical.",
            ) from exc
        receipt = {
            "schema_version": VERIFIED_PUBLICATION_RECEIPT_VERSION,
            "publication_id": descriptor.publication_id,
            "publication_descriptor_sha256": descriptor.sha256,
            "analysis_id": descriptor.analysis_id,
            "attempt": descriptor.attempt,
            "provider_attempt_identity": descriptor.provider_attempt_identity,
            "output_manifest_sha256": descriptor.output_manifest_sha256,
            "result_identity": descriptor.result_identity,
            "result_sha256": descriptor.result_sha256,
            "artifact_sha256": artifact_sha256,
            "authorization_id": authorization.authorization_id,
            "published_at": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "mutation_result": clean_result,
        }
        try:
            self.store.record_publication_receipt(
                descriptor.analysis_id, receipt
            )
        except AnalysisPersistenceError as exc:
            raise AnalysisPublicationError(
                "receipt_failed", "Publication receipt could not be persisted."
            ) from exc
        try:
            self.store.transition(
                descriptor.analysis_id,
                "succeeded",
                reason="verified_result_published",
                expected_state="publishing",
            )
        except AnalysisPersistenceError as exc:
            raise AnalysisPublicationError(
                "completion_failed",
                "The durable publication receipt could not be finalized.",
            ) from exc
        return receipt

    @staticmethod
    def _canonical_sha256(value: Mapping[str, Any]) -> str:
        encoded = json.dumps(
            dict(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _bind_verification_receipt(
        record: Mapping[str, Any],
        descriptor: VerifiedPublicationDescriptor,
    ) -> tuple[EngineeringResultEnvelope, Mapping[str, Any]]:
        receipts = record.get("verification_receipts", [])
        if not isinstance(receipts, list):
            raise AnalysisPublicationError(
                "verification_receipt_invalid",
                "Domain verification evidence is malformed.",
            )
        attempts = record.get("attempts", [])
        if descriptor.attempt != len(attempts):
            raise AnalysisPublicationError(
                "verification_receipt_mismatch",
                "Publication does not bind the latest verified attempt.",
            )
        receipt = next(
            (
                item
                for item in receipts
                if isinstance(item, Mapping)
                and item.get("attempt") == descriptor.attempt
            ),
            None,
        )
        if receipt is None:
            raise AnalysisPublicationError(
                "verification_receipt_missing",
                "No durable domain verification receipt binds this publication.",
            )
        try:
            envelope = EngineeringResultEnvelope.from_dict(
                receipt["result_envelope"]
            )
            canonical = envelope.to_canonical_json()
            result_sha256 = hashlib.sha256(
                canonical.encode("utf-8")
            ).hexdigest()
        except Exception as exc:
            raise AnalysisPublicationError(
                "verification_receipt_invalid",
                "Domain verification evidence is malformed.",
            ) from exc
        valid = (
            descriptor.analysis_id == record.get("analysis_id")
            and descriptor.attempt == len(attempts)
            and descriptor.domain_id == record.get("domain")
            and descriptor.adapter_id == record.get("adapter_id")
            and descriptor.source_document_uid
            == record.get("source_document_uid")
            and descriptor.frozen_dependency_sha256
            == record.get("dependency_sha256")
            and descriptor.provider_attempt_identity
            == receipt.get("provider_attempt_identity")
            and descriptor.output_manifest_sha256
            == receipt.get("output_manifest_sha256")
            and descriptor.result_identity == receipt.get("result_identity")
            and descriptor.result_identity == envelope.result_id.canonical
            and descriptor.result_sha256 == receipt.get("result_sha256")
            and descriptor.result_sha256 == result_sha256
            and envelope.domain == descriptor.domain_id
            and envelope.adapter_id == descriptor.adapter_id
            and envelope.provider_attempt_id
            == descriptor.provider_attempt_identity
            and envelope.source_identity.kind == "document"
            and envelope.source_identity.value
            == descriptor.source_document_uid
            and envelope.dependency_digest
            == descriptor.frozen_dependency_sha256
            and envelope.currentness == "current"
            and envelope.publication_state == "unpublished"
            and receipt.get("artifact_sha256")
            == [item.digest for item in envelope.artifacts]
        )
        if not valid:
            raise AnalysisPublicationError(
                "verification_receipt_mismatch",
                "Publication does not bind the exact verified result.",
            )
        return envelope, receipt

    def _reverify_artifacts(
        self,
        record: Mapping[str, Any],
        verification_receipt: Mapping[str, Any],
    ) -> tuple[VerifiedPublicationArtifact, ...]:
        active = {
            item.get("sha256"): item
            for item in record.get("artifacts", [])
            if isinstance(item, Mapping) and not item.get("tombstoned_at")
        }
        fields = tuple(ArtifactDescriptor.__dataclass_fields__)
        verified: list[VerifiedPublicationArtifact] = []
        for digest in verification_receipt["artifact_sha256"]:
            persisted = active.get(digest)
            if persisted is None:
                return self._artifact_failure(
                    record, "publication_artifact_integrity_failed"
                )
            try:
                descriptor = ArtifactDescriptor(
                    **{name: persisted[name] for name in fields}
                )
                path = self.artifact_store.verify_admitted(descriptor)
            except (KeyError, TypeError, ValueError, AnalysisArtifactError) as exc:
                if (
                    isinstance(exc, AnalysisArtifactError)
                    and exc.reason == "read_failed"
                ):
                    raise AnalysisPublicationError(
                        "publication_storage_unavailable",
                        "Immutable verified output storage is unavailable.",
                    ) from exc
                return self._artifact_failure(
                    record, "publication_artifact_integrity_failed", cause=exc
                )
            verified.append(VerifiedPublicationArtifact(descriptor, path))
        return tuple(verified)

    def _artifact_failure(
        self,
        record: Mapping[str, Any],
        reason: str,
        *,
        cause: Exception | None = None,
    ):
        try:
            attempts = deepcopy(record.get("attempts", []))
            if attempts:
                attempts[-1]["terminal_reason"] = reason
            self.store.transition(
                record["analysis_id"],
                "failed",
                reason=reason,
                updates={"attempts": attempts},
                expected_state="waiting_to_publish",
            )
        except AnalysisPersistenceError as exc:
            raise AnalysisPublicationError(
                "owner_conflict",
                "Artifact-integrity failure could not be recorded.",
            ) from exc
        error = AnalysisPublicationError(
            reason,
            "Verified publication artifacts no longer match durable evidence.",
        )
        if cause is not None:
            raise error from cause
        raise error

    @staticmethod
    def _validate_published_receipt(
        record: Mapping[str, Any],
        descriptor: VerifiedPublicationDescriptor,
        authorization: VerifiedPublicationAuthorization,
        receipt: Mapping[str, Any],
    ) -> None:
        publication = record.get("publication") or {}
        intent = publication.get("intent") or {}
        persisted_authorization = publication.get("authorization") or {}
        valid = (
            receipt.get("schema_version")
            == VERIFIED_PUBLICATION_RECEIPT_VERSION
            and receipt.get("publication_id") == descriptor.publication_id
            and receipt.get("publication_descriptor_sha256")
            == descriptor.sha256
            and receipt.get("analysis_id") == descriptor.analysis_id
            and receipt.get("attempt") == descriptor.attempt
            and receipt.get("provider_attempt_identity")
            == descriptor.provider_attempt_identity
            and receipt.get("output_manifest_sha256")
            == descriptor.output_manifest_sha256
            and receipt.get("result_identity") == descriptor.result_identity
            and receipt.get("result_sha256") == descriptor.result_sha256
            and receipt.get("authorization_id")
            == authorization.authorization_id
            and authorization.publication_id == descriptor.publication_id
            and authorization.publication_descriptor_sha256
            == descriptor.sha256
            and intent.get("publication_descriptor_sha256")
            == descriptor.sha256
            and persisted_authorization.get("authorization_id")
            == authorization.authorization_id
        )
        if not valid:
            raise AnalysisPublicationError(
                "receipt_conflict",
                "A different durable publication receipt already exists.",
            )
