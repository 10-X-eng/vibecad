# SPDX-License-Identifier: LGPL-2.1-or-later

"""Installed FreeCADCmd gate for verified Native publication authority."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import traceback
from typing import Any, Callable


VIBECAD_MODULE = Path(__file__).resolve().parents[1]
BUNDLED_SITE_PACKAGES = Path(sys.executable).resolve().parent / "Lib" / "site-packages"
for path in (BUNDLED_SITE_PACKAGES, VIBECAD_MODULE):
    text = str(path)
    if text in sys.path:
        sys.path.remove(text)
    sys.path.insert(0, text)

import FreeCAD as App

from VibeCADAnalysisNativePublication import (
    NativeVerifiedPublicationAdapter,
    NativeVerifiedPublicationHost,
)
from VibeCADAnalysisPublication import (
    CurrentnessReport,
    VerifiedPublicationAuthorization,
    VerifiedPublicationDescriptor,
    VerifiedPublicationRequest,
)
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeTargets import object_identity


class _Coordinator:
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
            raise RuntimeError("The exact installed document was not rebound.")
        if evaluate_currentness(document, descriptor) != CurrentnessReport(True, True):
            raise RuntimeError("The installed document was not current.")
        if adapter_is_compatible(descriptor) is not True:
            raise RuntimeError("The installed adapter was incompatible.")
        if authorization.publication_descriptor_sha256 != descriptor.sha256:
            raise RuntimeError("The installed authorization did not bind the descriptor.")
        request = VerifiedPublicationRequest(descriptor, None, ())  # type: ignore[arg-type]
        result = mutate_document(document, request)
        if verify_postconditions(document, result) is not True:
            raise RuntimeError("Installed Native publication evidence did not bind.")
        return dict(result)


def _descriptor(document_uid: str) -> VerifiedPublicationDescriptor:
    return VerifiedPublicationDescriptor(
        publication_id="installed-native-publication",
        analysis_id="installed-native-analysis",
        attempt=1,
        domain_id="installed-fixture-domain",
        adapter_id="installed-fixture-publisher",
        adapter_version="1.0.0",
        source_document_uid=document_uid,
        frozen_dependency_sha256="a" * 64,
        output_manifest_sha256="b" * 64,
        provider_attempt_identity="c" * 64,
        result_identity="installed-result",
        result_sha256="d" * 64,
    )


def _authorization(
    descriptor: VerifiedPublicationDescriptor,
) -> VerifiedPublicationAuthorization:
    return VerifiedPublicationAuthorization(
        descriptor.publication_id,
        descriptor.sha256,
        "installed-human-authorization",
        "2026-08-27T12:00:00Z",
    )


def _adapter(*, fail_postcondition: bool = False) -> NativeVerifiedPublicationAdapter:
    object_name = (
        "RolledBackVerifiedPublication"
        if fail_postcondition
        else "VerifiedPublicationResult"
    )

    def currentness(document: Any, descriptor: Any) -> CurrentnessReport:
        return CurrentnessReport(
            str(document.Uid) == descriptor.source_document_uid,
            True,
        )

    def build(document: Any, request: Any) -> NativeMutationDraft:
        result = document.addObject("App::FeaturePython", object_name)
        result.addProperty(
            "App::PropertyString",
            "PublicationDescriptorSha256",
            "VibeCAD Analysis",
        )
        result.addProperty(
            "App::PropertyString",
            "ResultIdentity",
            "VibeCAD Analysis",
        )
        result.PublicationDescriptorSha256 = request.descriptor.sha256
        result.ResultIdentity = request.descriptor.result_identity
        return NativeMutationDraft(
            value=result,
            recompute_targets=(result,),
            created=(object_identity(result),),
        )

    def verify(document: Any, draft: NativeMutationDraft, request: Any) -> dict:
        result = draft.value
        if fail_postcondition:
            raise RuntimeError("Deliberate installed postcondition failure")
        if (
            document.getObject(str(result.Name)) is not result
            or str(result.PublicationDescriptorSha256) != request.descriptor.sha256
            or str(result.ResultIdentity) != request.descriptor.result_identity
            or not bool(result.isValid())
        ):
            raise RuntimeError("Installed result object failed exact postconditions")
        return {
            "object_name": str(result.Name),
            "object_id": int(result.ID),
            "type_id": str(result.TypeId),
            "result_identity": str(result.ResultIdentity),
        }

    return NativeVerifiedPublicationAdapter(
        domain_id="installed-fixture-domain",
        adapter_id="installed-fixture-publisher",
        adapter_version="1.0.0",
        transaction_name="Publish verified Analysis result",
        evaluate_currentness=currentness,
        build_draft=build,
        verify_draft=verify,
        after_abort=lambda document: document.recompute(),
    )


def run() -> dict[str, Any]:
    document = None
    with tempfile.TemporaryDirectory(
        prefix="vibecad-native-publication-"
    ) as temporary:
        path = Path(temporary) / "native-publication.FCStd"
        try:
            document = App.newDocument("NativeVerifiedPublication")
            document.UndoMode = 1
            document.saveAs(str(path))
            document_uid = str(document.Uid)
            document_name = str(document.Name)
            App.closeDocument(document_name)
            document = App.openDocument(str(path))
            if str(document.Uid) != document_uid:
                raise RuntimeError("Document.Uid did not survive exact reopen.")

            descriptor = _descriptor(document_uid)
            host = NativeVerifiedPublicationHost(
                _Coordinator(),
                dispatch_to_document_thread=lambda callback: callback(),
            )
            evidence = host.publish(
                descriptor,
                _authorization(descriptor),
                _adapter(),
            )
            committed = bool(
                document.getObject("VerifiedPublicationResult") is not None
                and evidence["publication_descriptor_sha256"] == descriptor.sha256
                and int(document.getBookedTransactionID()) == 0
                and not bool(document.HasPendingTransaction)
            )

            rollback_descriptor = replace(
                descriptor,
                publication_id="installed-native-publication-rollback",
                result_identity="installed-result-rollback",
            )
            try:
                host.publish(
                    rollback_descriptor,
                    _authorization(rollback_descriptor),
                    _adapter(fail_postcondition=True),
                )
            except NativeMutationError:
                pass
            else:
                raise RuntimeError("The failing publication did not abort.")
            rollback = bool(
                document.getObject("RolledBackVerifiedPublication") is None
                and int(document.getBookedTransactionID()) == 0
                and not bool(document.HasPendingTransaction)
            )

            document.recompute()
            document.save()
            App.closeDocument(document.Name)
            document = App.openDocument(str(path))
            reopened = bool(
                str(document.Uid) == document_uid
                and document.getObject("VerifiedPublicationResult") is not None
                and document.getObject("RolledBackVerifiedPublication") is None
            )
            if not committed or not rollback or not reopened:
                raise RuntimeError("Installed Native publication evidence is incomplete.")
            return {
                "runtime": "installed-freecadcmd",
                "committed": committed,
                "rollback": rollback,
                "exact_document_uid_reopen": reopened,
                "fixture_domain": True,
                "production_fem_publisher": False,
                "physical_solver_validation": False,
            }
        finally:
            if document is not None and document.Name in App.listDocuments():
                App.closeDocument(document.Name)


if __name__ == "__main__":
    try:
        report = run()
        print(
            "VIBECAD_ANALYSIS_NATIVE_PUBLICATION_OK "
            + json.dumps(report, sort_keys=True, separators=(",", ":")),
            flush=True,
        )
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
        raise
