# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native document-thread authority for receipt-bound Analysis publication."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

from VibeCADAnalysisPublication import (
    CurrentnessReport,
    VerifiedAnalysisPublicationCoordinator,
    VerifiedPublicationAuthorization,
    VerifiedPublicationDescriptor,
    VerifiedPublicationRequest,
)
from VibeCADNativeMutation import NativeMutationDraft, run_human_mutation
from tool_impl.analysis_contracts import AnalysisContractError
from tool_impl.engineering_contracts import canonical_payload


NATIVE_VERIFIED_PUBLICATION_EVIDENCE_VERSION = (
    "vibecad-native-verified-publication-evidence-v1"
)


class NativeAnalysisPublicationHostError(RuntimeError):
    """Fail-closed Native publication boundary error."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(str(message).strip())
        self.error_code = str(error_code or "NATIVE_ANALYSIS_PUBLICATION_FAILED")

    def failure(self) -> dict[str, str]:
        return {"error_code": self.error_code, "message": str(self)}


CurrentnessEvaluator = Callable[
    [Any, VerifiedPublicationDescriptor], CurrentnessReport
]
DraftBuilder = Callable[
    [Any, VerifiedPublicationRequest], NativeMutationDraft
]
DraftVerifier = Callable[
    [Any, NativeMutationDraft, VerifiedPublicationRequest], Mapping[str, Any]
]
AbortStabilizer = Callable[[Any], None]


@dataclass(frozen=True, slots=True)
class NativeVerifiedPublicationAdapter:
    """One domain-owned draft and postcondition contract for Native publication."""

    domain_id: str
    adapter_id: str
    adapter_version: str
    transaction_name: str
    evaluate_currentness: CurrentnessEvaluator = field(repr=False, compare=False)
    build_draft: DraftBuilder = field(repr=False, compare=False)
    verify_draft: DraftVerifier = field(repr=False, compare=False)
    after_abort: AbortStabilizer | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        for name in ("domain_id", "adapter_id", "adapter_version"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"{name} must be non-empty")
            object.__setattr__(self, name, value)
        transaction_name = str(self.transaction_name or "").strip()
        if not 1 <= len(transaction_name) <= 80:
            raise ValueError("transaction_name must contain 1 to 80 characters")
        object.__setattr__(self, "transaction_name", transaction_name)
        if not all(
            callable(callback)
            for callback in (
                self.evaluate_currentness,
                self.build_draft,
                self.verify_draft,
            )
        ):
            raise TypeError("Native publication adapter callbacks must be callable")
        if self.after_abort is not None and not callable(self.after_abort):
            raise TypeError("after_abort must be callable or None")

    def matches(self, descriptor: VerifiedPublicationDescriptor) -> bool:
        return bool(
            isinstance(descriptor, VerifiedPublicationDescriptor)
            and descriptor.domain_id == self.domain_id
            and descriptor.adapter_id == self.adapter_id
            and descriptor.adapter_version == self.adapter_version
        )


OpenDocuments = Callable[[], Iterable[Any] | Mapping[str, Any]]
DocumentThreadDispatcher = Callable[[Callable[[], Any]], Any]
TransactionFactory = Callable[[Any, str], Any]
DocumentLiveness = Callable[[Any], bool]


def _default_open_documents() -> tuple[Any, ...]:
    import FreeCAD as App

    return tuple(App.listDocuments().values())


def _default_dispatch_to_document_thread(callback: Callable[[], Any]) -> Any:
    import VibeCADGui

    VibeCADGui._ensure_document_thread_invoker()
    return VibeCADGui._dispatch_to_document_thread(callback)


class NativeVerifiedPublicationHost:
    """Bind the generic durable gate to one exact Native document transaction."""

    def __init__(
        self,
        coordinator: VerifiedAnalysisPublicationCoordinator,
        *,
        open_documents: OpenDocuments = _default_open_documents,
        dispatch_to_document_thread: DocumentThreadDispatcher = (
            _default_dispatch_to_document_thread
        ),
        transaction_factory: TransactionFactory | None = None,
        document_is_live: DocumentLiveness | None = None,
    ) -> None:
        if not callable(getattr(coordinator, "publish", None)):
            raise TypeError("coordinator must provide publish")
        if not callable(open_documents) or not callable(dispatch_to_document_thread):
            raise TypeError("Native publication host callbacks must be callable")
        if transaction_factory is not None and not callable(transaction_factory):
            raise TypeError("transaction_factory must be callable or None")
        if document_is_live is not None and not callable(document_is_live):
            raise TypeError("document_is_live must be callable or None")
        self.coordinator = coordinator
        self._open_documents = open_documents
        self._dispatch = dispatch_to_document_thread
        self._transaction_factory = transaction_factory
        self._document_is_live = document_is_live

    def publish(
        self,
        descriptor: VerifiedPublicationDescriptor,
        authorization: VerifiedPublicationAuthorization,
        adapter: NativeVerifiedPublicationAdapter,
    ) -> dict[str, Any]:
        if not isinstance(descriptor, VerifiedPublicationDescriptor):
            raise TypeError("descriptor must be VerifiedPublicationDescriptor")
        if not isinstance(authorization, VerifiedPublicationAuthorization):
            raise TypeError("authorization must be VerifiedPublicationAuthorization")
        if not isinstance(adapter, NativeVerifiedPublicationAdapter):
            raise TypeError("adapter must be NativeVerifiedPublicationAdapter")

        def resolve_document(source_document_uid: str) -> Any | None:
            return self._dispatch(
                lambda: self._resolve_exact_document(
                    source_document_uid,
                    required=False,
                )
            )

        def evaluate_currentness(
            document: Any,
            actual_descriptor: VerifiedPublicationDescriptor,
        ) -> CurrentnessReport:
            return self._dispatch(
                lambda: self._evaluate_currentness(
                    adapter,
                    document,
                    actual_descriptor,
                )
            )

        def mutate_document(
            _document: Any,
            request: VerifiedPublicationRequest,
        ) -> Mapping[str, Any]:
            return self._dispatch(
                lambda: self._commit_on_document_thread(adapter, request)
            )

        return self.coordinator.publish(
            descriptor,
            authorization,
            resolve_document=resolve_document,
            evaluate_currentness=evaluate_currentness,
            adapter_is_compatible=adapter.matches,
            mutate_document=mutate_document,
            verify_postconditions=lambda _document, result: (
                self._evidence_matches(result, descriptor)
            ),
        )

    def _documents(self) -> tuple[Any, ...]:
        try:
            documents = self._open_documents()
            if isinstance(documents, Mapping):
                documents = documents.values()
            return tuple(documents)
        except Exception as exc:
            raise NativeAnalysisPublicationHostError(
                "NATIVE_ANALYSIS_DOCUMENT_ENUMERATION_FAILED",
                "Open Native documents could not be enumerated.",
            ) from exc

    def _resolve_exact_document(
        self,
        source_document_uid: str,
        *,
        required: bool,
    ) -> Any | None:
        identity = str(source_document_uid or "").strip()
        if not identity:
            raise NativeAnalysisPublicationHostError(
                "NATIVE_ANALYSIS_DOCUMENT_UNAVAILABLE",
                "Verified publication requires one exact source document UID.",
            )
        matches = tuple(
            document
            for document in self._documents()
            if str(getattr(document, "Uid", "") or "") == identity
        )
        if len(matches) > 1:
            raise NativeAnalysisPublicationHostError(
                "NATIVE_ANALYSIS_DOCUMENT_AMBIGUOUS",
                "More than one open Native document has the verified source UID.",
            )
        if not matches:
            if not required:
                return None
            raise NativeAnalysisPublicationHostError(
                "NATIVE_ANALYSIS_DOCUMENT_UNAVAILABLE",
                "The exact verified source document is no longer open.",
            )
        return matches[0]

    @staticmethod
    def _require_current(report: CurrentnessReport) -> None:
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
            raise NativeAnalysisPublicationHostError(
                "NATIVE_ANALYSIS_SOURCE_UNRESOLVED",
                "Verified publication targets are no longer unambiguous.",
            )
        if report.ambiguous_dependencies:
            raise NativeAnalysisPublicationHostError(
                "NATIVE_ANALYSIS_SOURCE_AMBIGUOUS",
                "Verified publication dependencies are ambiguous.",
            )
        if not report.current or report.changed_dependencies:
            raise NativeAnalysisPublicationHostError(
                "NATIVE_ANALYSIS_SOURCE_STALE",
                "The verified source changed before Native publication.",
            )

    def _evaluate_currentness(
        self,
        adapter: NativeVerifiedPublicationAdapter,
        document: Any,
        descriptor: VerifiedPublicationDescriptor,
    ) -> CurrentnessReport:
        if str(getattr(document, "Uid", "") or "") != (
            descriptor.source_document_uid
        ):
            raise NativeAnalysisPublicationHostError(
                "NATIVE_ANALYSIS_DOCUMENT_UNAVAILABLE",
                "Currentness was requested for a different Native document.",
            )
        report = adapter.evaluate_currentness(document, descriptor)
        self._require_current(report)
        return report

    def _commit_on_document_thread(
        self,
        adapter: NativeVerifiedPublicationAdapter,
        request: VerifiedPublicationRequest,
    ) -> dict[str, Any]:
        if not isinstance(request, VerifiedPublicationRequest):
            raise TypeError("request must be VerifiedPublicationRequest")
        descriptor = request.descriptor
        if not adapter.matches(descriptor):
            raise NativeAnalysisPublicationHostError(
                "NATIVE_ANALYSIS_ADAPTER_INCOMPATIBLE",
                "The Native publication adapter does not match verified evidence.",
            )
        document = self._resolve_exact_document(
            descriptor.source_document_uid,
            required=True,
        )

        def mutate(active_document: Any) -> NativeMutationDraft:
            self._require_current(
                adapter.evaluate_currentness(active_document, descriptor)
            )
            draft = adapter.build_draft(active_document, request)
            if not isinstance(draft, NativeMutationDraft):
                raise TypeError("Native publication adapter returned an invalid draft")
            return draft

        def verify(
            active_document: Any,
            draft: NativeMutationDraft,
        ) -> Mapping[str, Any]:
            domain_result = adapter.verify_draft(
                active_document,
                draft,
                request,
            )
            if not isinstance(domain_result, Mapping):
                raise TypeError("Native publication postcondition must return an object")
            try:
                clean_result = canonical_payload(
                    dict(domain_result),
                    "native publication domain result",
                ).to_value()
            except AnalysisContractError as exc:
                raise NativeAnalysisPublicationHostError(
                    "NATIVE_ANALYSIS_POSTCONDITION_UNSAFE",
                    "Native publication postcondition evidence is unsafe.",
                ) from exc
            return self._evidence(descriptor, clean_result)

        options: dict[str, Any] = {}
        if self._transaction_factory is not None:
            options["transaction_factory"] = self._transaction_factory
        if self._document_is_live is not None:
            options["document_is_live"] = self._document_is_live
        return run_human_mutation(
            document=document,
            transaction_name=adapter.transaction_name,
            mutate=mutate,
            verify=verify,
            after_abort=adapter.after_abort,
            **options,
        )

    @staticmethod
    def _evidence(
        descriptor: VerifiedPublicationDescriptor,
        domain_result: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "schema_version": NATIVE_VERIFIED_PUBLICATION_EVIDENCE_VERSION,
            "publication_id": descriptor.publication_id,
            "publication_descriptor_sha256": descriptor.sha256,
            "analysis_id": descriptor.analysis_id,
            "attempt": descriptor.attempt,
            "domain_id": descriptor.domain_id,
            "adapter_id": descriptor.adapter_id,
            "adapter_version": descriptor.adapter_version,
            "source_document_uid": descriptor.source_document_uid,
            "domain_result": dict(domain_result),
        }

    @staticmethod
    def _evidence_matches(
        result: Mapping[str, Any],
        descriptor: VerifiedPublicationDescriptor,
    ) -> bool:
        if not isinstance(result, Mapping) or set(result) != {
            "schema_version",
            "publication_id",
            "publication_descriptor_sha256",
            "analysis_id",
            "attempt",
            "domain_id",
            "adapter_id",
            "adapter_version",
            "source_document_uid",
            "domain_result",
        }:
            return False
        expected = NativeVerifiedPublicationHost._evidence(descriptor, {})
        expected.pop("domain_result")
        observed = dict(result)
        domain_result = observed.pop("domain_result", None)
        return observed == expected and isinstance(domain_result, Mapping)


__all__ = (
    "NATIVE_VERIFIED_PUBLICATION_EVIDENCE_VERSION",
    "NativeAnalysisPublicationHostError",
    "NativeVerifiedPublicationAdapter",
    "NativeVerifiedPublicationHost",
)
