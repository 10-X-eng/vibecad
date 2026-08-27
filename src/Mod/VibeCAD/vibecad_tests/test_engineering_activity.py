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
    assert (
        projected["analyses"][0]["restart_disposition"]["action"]
        == "mark_interrupted"
    )
    assert projected["workflow_count"] == 1
    assert projected["workflows"][0]["run_id"] == "workflow-current"


def test_empty_store_is_an_explicit_empty_projection(tmp_path: Path) -> None:
    projected = discover_engineering_activity("document-current", root=tmp_path)
    assert projected["analysis_count"] == 0
    assert projected["analyses"] == []
    assert projected["workflow_count"] == 0
    assert projected["workflows"] == []
