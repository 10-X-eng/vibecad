# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from pathlib import Path

import pytest

from VibeCADAnalysisPersistence import AnalysisMetadataStore, new_job_record
from VibeCADAnalysisPublication import (
    AnalysisPublicationCoordinator,
    AnalysisPublicationError,
    CurrentnessReport,
    PublicationAuthorization,
    PublicationDescriptor,
)


def _ready_store(tmp_path: Path) -> AnalysisMetadataStore:
    store = AnalysisMetadataStore(tmp_path)
    store.create(new_job_record(
        analysis_id="analysis-1", domain="fem", adapter_id="adapter",
        source_document_uid="doc-1", prepared_analysis_sha256="a" * 64,
        dependency_sha256="b" * 64, input_manifest_sha256="c" * 64,
        execution_spec_sha256="d" * 64,
    ))
    for state in ("running_local", "collecting", "verifying", "waiting_to_publish"):
        store.transition("analysis-1", state, reason="fixture")
    return store


def _descriptor() -> PublicationDescriptor:
    return PublicationDescriptor(
        publication_id="publication-1", analysis_id="analysis-1", domain_id="fem",
        adapter_id="adapter", adapter_version="1", source_document_uid="doc-1",
        frozen_dependency_sha256="b" * 64, output_manifest_sha256="e" * 64,
        result_identity="result-1",
    )


def _authorization() -> PublicationAuthorization:
    return PublicationAuthorization(
        publication_id="publication-1", result_identity="result-1",
        authorization_id="authorization-1", authorized_at="2026-08-27T00:00:00Z",
    )


def _publish(coordinator, **overrides):
    document = {"uid": "doc-1", "objects": []}
    arguments = {
        "resolve_document": lambda uid: document if uid == "doc-1" else None,
        "validate_output_manifest": lambda _descriptor: True,
        "evaluate_currentness": lambda _document, _descriptor: CurrentnessReport(True, True),
        "adapter_is_compatible": lambda _descriptor: True,
        "mutate_document": lambda target, _descriptor: target["objects"].append("Result") or {"object": "Result"},
        "verify_postconditions": lambda target, result: result["object"] in target["objects"],
    }
    arguments.update(overrides)
    return coordinator.publish(_descriptor(), _authorization(), **arguments), document


def test_publication_validates_mutates_once_and_replays_receipt(tmp_path: Path) -> None:
    store = _ready_store(tmp_path)
    coordinator = AnalysisPublicationCoordinator(store)
    receipt, document = _publish(coordinator)

    assert receipt["publication_id"] == "publication-1"
    assert document["objects"] == ["Result"]
    assert store.load("analysis-1")["state"] == "succeeded"

    replay, second_document = _publish(coordinator)
    assert replay == receipt
    assert second_document["objects"] == []


@pytest.mark.parametrize(
    ("override", "reason"),
    (
        ({"validate_output_manifest": lambda _descriptor: False}, "invalid_outputs"),
        ({"resolve_document": lambda _uid: None}, "source_unavailable"),
        ({"evaluate_currentness": lambda _doc, _desc: CurrentnessReport(False, True, ("geometry",))}, "stale"),
        ({"evaluate_currentness": lambda _doc, _desc: CurrentnessReport(False, True, (), ("body",))}, "ambiguous"),
        ({"adapter_is_compatible": lambda _descriptor: False}, "adapter_incompatible"),
    ),
)
def test_precondition_refusal_never_acquires_publication_owner(
    tmp_path: Path, override: dict, reason: str,
) -> None:
    store = _ready_store(tmp_path)
    with pytest.raises(AnalysisPublicationError) as caught:
        _publish(AnalysisPublicationCoordinator(store), **override)
    assert caught.value.reason == reason
    assert store.load("analysis-1")["state"] == "waiting_to_publish"


def test_authorization_must_bind_exact_publication_and_result(tmp_path: Path) -> None:
    store = _ready_store(tmp_path)
    coordinator = AnalysisPublicationCoordinator(store)
    bad = PublicationAuthorization("publication-other", "result-1", "auth", "now")
    with pytest.raises(AnalysisPublicationError) as caught:
        coordinator.publish(
            _descriptor(), bad,
            resolve_document=lambda _uid: object(),
            validate_output_manifest=lambda _descriptor: True,
            evaluate_currentness=lambda _doc, _descriptor: CurrentnessReport(True, True),
            adapter_is_compatible=lambda _descriptor: True,
            mutate_document=lambda _doc, _descriptor: {},
            verify_postconditions=lambda _doc, _result: True,
        )
    assert caught.value.reason == "authorization_mismatch"
    assert store.load("analysis-1")["state"] == "waiting_to_publish"


def test_failed_postcondition_leaves_outcome_unknown_and_refuses_replay(tmp_path: Path) -> None:
    store = _ready_store(tmp_path)
    coordinator = AnalysisPublicationCoordinator(store)
    with pytest.raises(AnalysisPublicationError) as caught:
        _publish(coordinator, verify_postconditions=lambda _document, _result: False)
    assert caught.value.reason == "postcondition_failed"
    assert store.restart_disposition("analysis-1")["action"] == "publication_outcome_unknown"
    with pytest.raises(AnalysisPublicationError) as replay:
        _publish(coordinator)
    assert replay.value.reason == "outcome_unknown"
