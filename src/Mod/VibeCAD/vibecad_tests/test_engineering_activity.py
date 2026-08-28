# SPDX-License-Identifier: LGPL-2.1-or-later

from pathlib import Path

from VibeCADAnalysisContracts import CanonicalJson
from VibeCADAnalysisPersistence import AnalysisMetadataStore, new_job_record
from VibeCADAnalysisWorkflow import WorkflowDefinition, WorkflowNode, WorkflowRunStore
from VibeCADEngineeringActivity import discover_engineering_activity


def _record(analysis_id: str, document_uid: str) -> dict:
    return new_job_record(
        analysis_id=analysis_id,
        domain="fem",
        adapter_id="native.fem",
        source_document_uid=document_uid,
        prepared_analysis_sha256="a" * 64,
        dependency_sha256="b" * 64,
        input_manifest_sha256="c" * 64,
        execution_spec_sha256="d" * 64,
    )


def test_discovers_only_exact_document_activity_and_linked_workflows(tmp_path: Path) -> None:
    analysis_store = AnalysisMetadataStore(tmp_path / "metadata")
    workflow_store = WorkflowRunStore(tmp_path / "workflows")
    analysis_store.create(_record("analysis-current", "document-current"))
    analysis_store.begin_attempt(
        "analysis-current",
        provider_id="local-process",
        provider_kind="local",
    )
    analysis_store.record_artifact(
        "analysis-current",
        {"sha256": "e" * 64, "role": "solver_output", "byte_count": 12},
        pinned=True,
    )
    analysis_store.create(_record("analysis-other", "document-other"))
    definition = WorkflowDefinition(
        "fem-run",
        "1",
        (
            WorkflowNode(
                "solve", "fem", "native.fem", (), ("result",),
                condition=CanonicalJson.from_value({"all": []}),
            ),
        ),
        (),
    )
    workflow_store.create(definition, "workflow-current")
    workflow_store.start_node(
        "workflow-current", "solve", analysis_id="analysis-current"
    )

    projected = discover_engineering_activity("document-current", root=tmp_path)
    assert projected["presentation_only"] is True
    assert set(projected["authority"].values()) == {False}
    assert projected["analysis_count"] == 1
    assert projected["analyses"][0]["analysis_id"] == "analysis-current"
    assert projected["analyses"][0]["attempts"] == [
        {
            "attempt": 1,
            "provider_id": "local-process",
            "provider_kind": "local",
            "provider_job_id": "",
            "provider_capability_snapshot": {
                "job_survives_client_exit": False,
                "reconnect_supported": False,
            },
            "started_at": projected["analyses"][0]["attempts"][0]["started_at"],
            "terminal_reason": None,
        }
    ]
    assert projected["analyses"][0]["artifacts"] == [
        {
            "sha256": "e" * 64,
            "role": "solver_output",
            "byte_count": 12,
            "pinned": True,
            "cleanup_eligible": False,
            "tombstoned_at": None,
        }
    ]
    assert projected["analyses"][0]["publication_axes"] == {
        "authorization_recorded": False,
        "intent_recorded": False,
        "receipt_recorded": False,
    }
    assert (
        projected["analyses"][0]["restart_disposition"]["action"]
        == "mark_interrupted"
    )
    assert projected["workflow_count"] == 1
    assert projected["workflows"][0]["run_id"] == "workflow-current"
    assert projected["workflows"][0]["nodes"] == [
        {
            "node_id": "solve",
            "state": "running",
            "analysis_id": "analysis-current",
            "attempt_count": 1,
            "attempts": projected["workflows"][0]["nodes"][0]["attempts"],
            "outcome": None,
            "publication_receipt_id": None,
        }
    ]


def test_empty_store_is_an_explicit_empty_projection(tmp_path: Path) -> None:
    projected = discover_engineering_activity("document-current", root=tmp_path)
    assert projected["analysis_count"] == 0
    assert projected["analyses"] == []
    assert projected["workflow_count"] == 0
    assert projected["workflows"] == []
