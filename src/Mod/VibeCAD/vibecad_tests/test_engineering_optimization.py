# SPDX-License-Identifier: LGPL-2.1-or-later

import json
from pathlib import Path

import pytest

from VibeCADEngineeringOptimization import discover_engineering_optimization
from VibeCADGovernedOptimization import (
    DesignVariable,
    Objective,
    OptimizationBudget,
    OptimizationDefinition,
    OptimizationError,
    OptimizationRunStore,
)
from VibeCADAnalysisContracts import CanonicalJson
from VibeCADAnalysisWorkflow import WorkflowDefinition, WorkflowNode, WorkflowRunStore


def _definition(document_uid: str) -> OptimizationDefinition:
    return OptimizationDefinition(
        optimization_id="bracket",
        source_document_uid=document_uid,
        source_revision="revision-1",
        source_sha256="a" * 64,
        workflow_definition_sha256="b" * 64,
        variables=(
            DesignVariable("width", "integer", "mm", "PartDesign", ("1", "2")),
        ),
        objectives=(Objective("mass", "minimize"),),
        constraints=(),
        budget=OptimizationBudget(2, 2, 60, 100),
    )


def test_discovers_exact_document_runs_with_owner_ranking(tmp_path: Path) -> None:
    store = OptimizationRunStore(tmp_path / "optimization")
    definition = _definition("document-current")
    created = store.create(definition, "run-current")
    candidate = next(iter(created["candidates"]))
    store.start_candidate("run-current", candidate, workflow_run_id="workflow-1")
    store.finish_candidate(
        "run-current",
        candidate,
        state="succeeded",
        currentness="current",
        metrics={"mass": "1.25"},
    )
    store.create(_definition("document-other"), "run-other")
    workflow_store = WorkflowRunStore(tmp_path / "workflows")
    workflow_store.create(
        WorkflowDefinition(
            "candidate-evaluation",
            "1",
            (
                WorkflowNode(
                    "solve", "fem", "adapter.solve", (), ("result",),
                    condition=CanonicalJson.from_value({"all": []}),
                ),
            ),
            (),
        ),
        "workflow-1",
    )

    projected = discover_engineering_optimization(
        "document-current", root=tmp_path
    )

    assert projected["run_count"] == 1
    assert projected["runs"][0]["run_id"] == "run-current"
    ranked = [
        item for item in projected["runs"][0]["candidates"] if item["rank"] is not None
    ]
    assert ranked[0]["candidate_id"] == candidate
    assert ranked[0]["rank"] == 1
    assert ranked[0]["workflow_run_ids"] == ["workflow-1"]
    assert ranked[0]["workflow_provenance"][0]["resolved"] is True
    assert ranked[0]["workflow_provenance"][0]["active"] is True
    assert ranked[0]["workflow_provenance"][0]["workflow"]["workflow_id"] == (
        "candidate-evaluation"
    )
    assert ranked[0]["unresolved_workflow_run_ids"] == []
    assert set(projected["authority"].values()) == {False}


def test_discovery_marks_missing_workflow_provenance_unresolved(tmp_path: Path) -> None:
    store = OptimizationRunStore(tmp_path / "optimization")
    created = store.create(_definition("document-current"), "run-current")
    candidate = next(iter(created["candidates"]))
    store.start_candidate("run-current", candidate, workflow_run_id="missing-workflow")

    projected = discover_engineering_optimization("document-current", root=tmp_path)
    selected = next(
        item for item in projected["runs"][0]["candidates"]
        if item["candidate_id"] == candidate
    )

    assert selected["workflow_provenance"] == [{
        "run_id": "missing-workflow",
        "active": True,
        "resolved": False,
        "workflow": None,
    }]
    assert selected["unresolved_workflow_run_ids"] == ["missing-workflow"]


def test_discovery_fails_closed_on_filename_or_definition_drift(
    tmp_path: Path,
) -> None:
    store = OptimizationRunStore(tmp_path / "optimization")
    definition = _definition("document-current")
    store.create(definition, "run-current")
    path = store.records / "run-current.json"
    renamed = path.with_name("wrong-name.json")
    path.rename(renamed)
    with pytest.raises(OptimizationError, match="filename"):
        discover_engineering_optimization("document-current", root=tmp_path)

    renamed.rename(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["definition"]["source_revision"] = "revision-drifted"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(OptimizationError, match="definition identity"):
        discover_engineering_optimization("document-current", root=tmp_path)
