from reference_host_runtime.VibeCADAnalysisPublication import (
    CurrentnessReport,
    PublicationDescriptor,
    publication_disposition,
)


def _descriptor() -> PublicationDescriptor:
    return PublicationDescriptor(
        publication_id="pub-1",
        job_id="job-1",
        analysis_id="analysis-1",
        submission_id="sub-1",
        domain_id="aero",
        adapter_id="openfoam",
        adapter_version="1",
        source_document_uid="doc-uid",
        frozen_dependency_snapshot_id="deps-1",
        output_manifest_id="out-1",
        result_identity="result-1",
    )


def test_missing_source_waits_instead_of_guessing_document() -> None:
    report = CurrentnessReport(current=False, source_resolved=False)
    assert publication_disposition(
        _descriptor(), report, fresh_host_authorization=False
    ) == "AWAITING_SOURCE"


def test_current_result_without_fresh_host_authorization_waits() -> None:
    report = CurrentnessReport(current=True, source_resolved=True)
    assert publication_disposition(
        _descriptor(), report, fresh_host_authorization=False
    ) == "AWAITING_PUBLICATION"


def test_relevant_dependency_drift_is_stale_even_with_host_authorization() -> None:
    report = CurrentnessReport(
        current=False,
        source_resolved=True,
        changed_dependencies=("geometry_sha256",),
    )
    assert publication_disposition(
        _descriptor(), report, fresh_host_authorization=True
    ) == "STALE"


def test_existing_receipt_makes_replay_idempotently_published() -> None:
    report = CurrentnessReport(current=True, source_resolved=True)
    assert publication_disposition(
        _descriptor(),
        report,
        fresh_host_authorization=True,
        existing_receipt={"publication_id": "pub-1"},
    ) == "PUBLISHED"
