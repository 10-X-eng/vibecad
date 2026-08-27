# SPDX-License-Identifier: LGPL-2.1-or-later

"""Independent, replay-safe publication authority for durable Analysis jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from tool_impl.analysis_persistence import AnalysisMetadataStore, AnalysisPersistenceError


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
