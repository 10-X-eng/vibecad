# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import runpy
from typing import Any, Callable

import pytest

from VibeCADAnalysisPublication import (
    CurrentnessReport,
    VerifiedPublicationAuthorization,
    VerifiedPublicationDescriptor,
    VerifiedPublicationRequest,
)
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADAnalysisNativePublication import (
    NATIVE_VERIFIED_PUBLICATION_EVIDENCE_VERSION,
    NativeAnalysisPublicationHostError,
    NativeVerifiedPublicationAdapter,
    NativeVerifiedPublicationHost,
)


def _descriptor() -> VerifiedPublicationDescriptor:
    return VerifiedPublicationDescriptor(
        publication_id="publication-1",
        analysis_id="analysis-1",
        attempt=1,
        domain_id="fixture-domain",
        adapter_id="fixture-publisher",
        adapter_version="1.0.0",
        source_document_uid="document-uid",
        frozen_dependency_sha256="a" * 64,
        output_manifest_sha256="b" * 64,
        provider_attempt_identity="c" * 64,
        result_identity="result-1",
        result_sha256="d" * 64,
    )


def _authorization(
    descriptor: VerifiedPublicationDescriptor,
) -> VerifiedPublicationAuthorization:
    return VerifiedPublicationAuthorization(
        descriptor.publication_id,
        descriptor.sha256,
        "authorization-1",
        "2026-08-27T12:00:00Z",
    )


@dataclass
class _Document:
    Uid: str = "document-uid"
    Name: str = "Fixture"
    value: int = 0
    HasPendingTransaction: bool = False

    def getBookedTransactionID(self) -> int:
        return 0


class _Transaction:
    def __init__(self, document: _Document, name: str) -> None:
        self.document = document
        self.name = name
        self.original = document.value
        self.committed = False
        self.aborted = False

    def commit(self) -> None:
        self.committed = True

    def abort(self) -> None:
        self.aborted = True
        self.document.value = self.original


class _Dispatcher:
    def __init__(self) -> None:
        self.active = False
        self.calls = 0

    def __call__(self, callback: Callable[[], Any]) -> Any:
        assert not self.active
        self.calls += 1
        self.active = True
        try:
            return callback()
        finally:
            self.active = False


class _Coordinator:
    def __init__(self, before_mutation: Callable[[], None] | None = None) -> None:
        self.before_mutation = before_mutation or (lambda: None)
        self.resolved: Any | None = None

    def publish(
        self,
        descriptor: VerifiedPublicationDescriptor,
        authorization: VerifiedPublicationAuthorization,
        *,
        resolve_document: Callable[[str], Any | None],
        evaluate_currentness: Callable[[Any, VerifiedPublicationDescriptor], Any],
        adapter_is_compatible: Callable[[VerifiedPublicationDescriptor], bool],
        mutate_document: Callable[[Any, VerifiedPublicationRequest], Any],
        verify_postconditions: Callable[[Any, Any], bool],
    ) -> dict[str, Any]:
        document = resolve_document(descriptor.source_document_uid)
        if document is None:
            raise RuntimeError("source unavailable")
        self.resolved = document
        report = evaluate_currentness(document, descriptor)
        assert report == CurrentnessReport(True, True)
        assert adapter_is_compatible(descriptor) is True
        assert authorization.publication_descriptor_sha256 == descriptor.sha256
        self.before_mutation()
        request = VerifiedPublicationRequest(descriptor, None, ())  # type: ignore[arg-type]
        result = mutate_document(document, request)
        assert verify_postconditions(document, result) is True
        return dict(result)


def _adapter(
    dispatcher: _Dispatcher,
    *,
    currentness: Callable[[int], CurrentnessReport] | None = None,
    verify_fails: bool = False,
    unsafe_result: bool = False,
    aborts: list[str] | None = None,
) -> tuple[NativeVerifiedPublicationAdapter, dict[str, int]]:
    calls = {"currentness": 0, "build": 0, "verify": 0}

    def evaluate(document: _Document, _descriptor: Any) -> CurrentnessReport:
        assert dispatcher.active
        calls["currentness"] += 1
        if currentness is not None:
            return currentness(calls["currentness"])
        return CurrentnessReport(True, True)

    def build(document: _Document, _request: Any) -> NativeMutationDraft:
        assert dispatcher.active
        calls["build"] += 1
        document.value += 1
        return NativeMutationDraft(value={"value": document.value})

    def verify(
        document: _Document,
        draft: NativeMutationDraft,
        _request: Any,
    ) -> dict[str, Any]:
        assert dispatcher.active
        calls["verify"] += 1
        if verify_fails:
            raise RuntimeError("fixture postcondition failed")
        assert draft.value == {"value": document.value}
        if unsafe_result:
            return {"working_path": "C:\\private\\solver-output.bin"}
        return {"object_name": "PublishedResult", "value": document.value}

    return (
        NativeVerifiedPublicationAdapter(
            domain_id="fixture-domain",
            adapter_id="fixture-publisher",
            adapter_version="1.0.0",
            transaction_name="Publish verified fixture result",
            evaluate_currentness=evaluate,
            build_draft=build,
            verify_draft=verify,
            after_abort=(
                (lambda _document: aborts.append("stabilized"))
                if aborts is not None
                else None
            ),
        ),
        calls,
    )


def _host(
    coordinator: _Coordinator,
    documents: list[_Document],
    dispatcher: _Dispatcher,
    transactions: list[_Transaction],
) -> NativeVerifiedPublicationHost:
    def transaction_factory(document: _Document, name: str) -> _Transaction:
        transaction = _Transaction(document, name)
        transactions.append(transaction)
        return transaction

    return NativeVerifiedPublicationHost(
        coordinator,
        open_documents=lambda: tuple(documents),
        dispatch_to_document_thread=dispatcher,
        transaction_factory=transaction_factory,
        document_is_live=lambda document: document in documents,
    )


def test_native_host_rebinds_and_commits_verified_domain_draft_on_document_thread(
) -> None:
    descriptor = _descriptor()
    document = _Document()
    documents = [document]
    dispatcher = _Dispatcher()
    transactions: list[_Transaction] = []
    adapter, calls = _adapter(dispatcher)
    host = _host(_Coordinator(), documents, dispatcher, transactions)

    result = host.publish(descriptor, _authorization(descriptor), adapter)

    assert result == {
        "schema_version": NATIVE_VERIFIED_PUBLICATION_EVIDENCE_VERSION,
        "publication_id": descriptor.publication_id,
        "publication_descriptor_sha256": descriptor.sha256,
        "analysis_id": descriptor.analysis_id,
        "attempt": descriptor.attempt,
        "domain_id": descriptor.domain_id,
        "adapter_id": descriptor.adapter_id,
        "adapter_version": descriptor.adapter_version,
        "source_document_uid": descriptor.source_document_uid,
        "domain_result": {"object_name": "PublishedResult", "value": 1},
    }
    assert document.value == 1
    assert calls == {"currentness": 2, "build": 1, "verify": 1}
    assert dispatcher.calls == 3
    assert len(transactions) == 1
    assert transactions[0].name == "Publish verified fixture result"
    assert transactions[0].committed is True
    assert transactions[0].aborted is False


def test_native_host_rebinds_same_uid_again_immediately_before_transaction() -> None:
    descriptor = _descriptor()
    original = _Document(Name="Original")
    reopened = _Document(Name="Reopened")
    documents = [original]
    dispatcher = _Dispatcher()
    transactions: list[_Transaction] = []
    adapter, _calls = _adapter(dispatcher)

    def replace_document() -> None:
        documents[:] = [reopened]

    host = _host(
        _Coordinator(before_mutation=replace_document),
        documents,
        dispatcher,
        transactions,
    )

    result = host.publish(descriptor, _authorization(descriptor), adapter)

    assert result["source_document_uid"] == descriptor.source_document_uid
    assert original.value == 0
    assert reopened.value == 1
    assert transactions[0].document is reopened


def test_native_host_rejects_ambiguous_document_uid_before_domain_access() -> None:
    descriptor = _descriptor()
    documents = [_Document(Name="First"), _Document(Name="Second")]
    dispatcher = _Dispatcher()
    transactions: list[_Transaction] = []
    adapter, calls = _adapter(dispatcher)
    host = _host(_Coordinator(), documents, dispatcher, transactions)

    with pytest.raises(NativeAnalysisPublicationHostError) as caught:
        host.publish(descriptor, _authorization(descriptor), adapter)

    assert caught.value.error_code == "NATIVE_ANALYSIS_DOCUMENT_AMBIGUOUS"
    assert calls == {"currentness": 0, "build": 0, "verify": 0}
    assert transactions == []


def test_native_host_maps_document_enumerator_failure_to_bounded_host_error() -> None:
    descriptor = _descriptor()
    dispatcher = _Dispatcher()
    adapter, calls = _adapter(dispatcher)

    def fail_enumeration() -> tuple[_Document, ...]:
        raise RuntimeError("private host detail")

    host = NativeVerifiedPublicationHost(
        _Coordinator(),
        open_documents=fail_enumeration,
        dispatch_to_document_thread=dispatcher,
    )

    with pytest.raises(NativeAnalysisPublicationHostError) as caught:
        host.publish(descriptor, _authorization(descriptor), adapter)

    assert caught.value.failure() == {
        "error_code": "NATIVE_ANALYSIS_DOCUMENT_ENUMERATION_FAILED",
        "message": "Open Native documents could not be enumerated.",
    }
    assert calls == {"currentness": 0, "build": 0, "verify": 0}


def test_native_host_rejects_malformed_currentness_report_fail_closed() -> None:
    descriptor = _descriptor()
    document = _Document()
    dispatcher = _Dispatcher()
    transactions: list[_Transaction] = []
    adapter, calls = _adapter(
        dispatcher,
        currentness=lambda _call: CurrentnessReport(1, True),  # type: ignore[arg-type]
    )
    host = _host(_Coordinator(), [document], dispatcher, transactions)

    with pytest.raises(TypeError, match="invalid evidence"):
        host.publish(descriptor, _authorization(descriptor), adapter)

    assert document.value == 0
    assert calls == {"currentness": 1, "build": 0, "verify": 0}
    assert transactions == []


def test_native_host_aborts_transaction_when_domain_postcondition_fails() -> None:
    descriptor = _descriptor()
    document = _Document()
    documents = [document]
    dispatcher = _Dispatcher()
    transactions: list[_Transaction] = []
    aborts: list[str] = []
    adapter, calls = _adapter(dispatcher, verify_fails=True, aborts=aborts)
    host = _host(_Coordinator(), documents, dispatcher, transactions)

    with pytest.raises(NativeMutationError) as caught:
        host.publish(descriptor, _authorization(descriptor), adapter)

    assert caught.value.error_code == "NATIVE_POSTCONDITION_FAILED"
    assert document.value == 0
    assert calls == {"currentness": 2, "build": 1, "verify": 1}
    assert aborts == ["stabilized"]
    assert transactions[0].committed is False
    assert transactions[0].aborted is True


def test_native_host_aborts_when_domain_evidence_is_not_safe_to_persist() -> None:
    descriptor = _descriptor()
    document = _Document()
    documents = [document]
    dispatcher = _Dispatcher()
    transactions: list[_Transaction] = []
    adapter, calls = _adapter(dispatcher, unsafe_result=True)
    host = _host(_Coordinator(), documents, dispatcher, transactions)

    with pytest.raises(NativeMutationError) as caught:
        host.publish(descriptor, _authorization(descriptor), adapter)

    assert caught.value.error_code == "NATIVE_POSTCONDITION_FAILED"
    assert document.value == 0
    assert calls == {"currentness": 2, "build": 1, "verify": 1}
    assert transactions[0].committed is False
    assert transactions[0].aborted is True


def test_native_host_rechecks_currentness_inside_owned_transaction() -> None:
    descriptor = _descriptor()
    document = _Document()
    documents = [document]
    dispatcher = _Dispatcher()
    transactions: list[_Transaction] = []
    adapter, calls = _adapter(
        dispatcher,
        currentness=lambda call: (
            CurrentnessReport(True, True)
            if call == 1
            else CurrentnessReport(False, True, ("Body",), ())
        ),
    )
    host = _host(_Coordinator(), documents, dispatcher, transactions)

    with pytest.raises(NativeMutationError) as caught:
        host.publish(descriptor, _authorization(descriptor), adapter)

    assert caught.value.error_code == "NATIVE_EXECUTION_FAILED"
    assert document.value == 0
    assert calls == {"currentness": 2, "build": 0, "verify": 0}
    assert transactions[0].aborted is True


def test_installed_native_publication_runner_parses_bounded_report() -> None:
    runner = runpy.run_path(
        str(
            Path(__file__).resolve().parents[4]
            / "tools"
            / "run_analysis_native_publication.py"
        ),
        run_name="analysis_native_publication_runner_contract",
    )

    report = runner["parse_report"](
        "host output\n"
        "VIBECAD_ANALYSIS_NATIVE_PUBLICATION_OK "
        '{"runtime":"installed-freecadcmd","committed":true,"rollback":true}'
        "\ntrailing output\n"
    )

    assert report == {
        "runtime": "installed-freecadcmd",
        "committed": True,
        "rollback": True,
    }
