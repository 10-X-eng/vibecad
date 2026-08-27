# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json

import pytest

from tool_impl.analysis_contracts import AnalysisContractError
from tool_impl.governed_optimization import (
    DesignVariable,
    MetricConstraint,
    Objective,
    OptimizationBudget,
    OptimizationDefinition,
    OptimizationError,
    OptimizationRunStore,
    rank_candidates,
)


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def definition(*, max_candidates=6, max_workflow_runs=6, stale="exclude", failure="exclude"):
    return OptimizationDefinition(
        optimization_id="bracket-search",
        source_document_uid="document-1",
        source_revision="revision-7",
        source_sha256=DIGEST_A,
        workflow_definition_sha256=DIGEST_B,
        variables=(
            DesignVariable("width", "integer", "mm", "PartDesign", ("1", "2", "3")),
            DesignVariable("material", "discrete", "material-id", "Material", ("A", "B")),
        ),
        objectives=(Objective("mass", "minimize"), Objective("margin", "maximize")),
        constraints=(MetricConstraint("margin", ">=", "2"),),
        budget=OptimizationBudget(max_candidates, max_workflow_runs, 60, 100),
        stale_treatment=stale,
        failure_treatment=failure,
    )


def test_definition_enumerates_independently_checkable_deterministic_candidates():
    first = definition().candidates()
    second = definition().candidates()
    assert first == second
    assert len(first) == 6
    assert [item["values"] for item in first] == [
        {"width": "1", "material": "A"}, {"width": "1", "material": "B"},
        {"width": "2", "material": "A"}, {"width": "2", "material": "B"},
        {"width": "3", "material": "A"}, {"width": "3", "material": "B"},
    ]
    assert all(set(item) == {"candidate_id", "candidate_sha256", "values", "mutation_proposal"} for item in first)


def test_duplicate_normalized_values_and_candidate_budget_are_rejected():
    with pytest.raises(AnalysisContractError, match="unique after normalization"):
        DesignVariable("width", "continuous", "mm", "PartDesign", ("1", "1.0"))
    with pytest.raises(OptimizationError, match="search space"):
        definition(max_candidates=5, max_workflow_runs=5).candidates()


def test_definition_rejects_unbounded_or_authoritative_variants():
    with pytest.raises(AnalysisContractError, match="direct publication authority"):
        OptimizationDefinition(
            optimization_id="bad", source_document_uid="doc", source_revision="1",
            source_sha256=DIGEST_A, workflow_definition_sha256=DIGEST_B,
            variables=(DesignVariable("x", "integer", "mm", "owner", ("1",)),),
            objectives=(Objective("mass", "minimize"),), constraints=(),
            budget=OptimizationBudget(1, 1, 1, 1), publication_policy="automatic",
        )


def test_ranking_is_feasible_first_objective_ordered_and_id_tied():
    spec = definition(stale="rank_last", failure="rank_last")
    ids = [item["candidate_id"] for item in spec.candidates()]
    evaluations = {
        ids[0]: {"state": "succeeded", "currentness": "current", "metrics": {"mass": "3", "margin": "3"}},
        ids[1]: {"state": "succeeded", "currentness": "current", "metrics": {"mass": "1", "margin": "1"}},
        ids[2]: {"state": "succeeded", "currentness": "current", "metrics": {"mass": "2", "margin": "2"}},
        ids[3]: {"state": "succeeded", "currentness": "stale", "metrics": {"mass": "0", "margin": "9"}},
        ids[4]: {"state": "failed", "currentness": "indeterminate", "metrics": {}},
    }
    ranked = rank_candidates(spec, evaluations)
    assert [item["candidate_id"] for item in ranked[:3]] == [ids[2], ids[0], ids[1]]
    assert ranked[2]["constraint_failures"] == ("margin",)
    assert {item["candidate_id"] for item in ranked[-3:]} == {ids[3], ids[4], ids[5]}


def test_store_recovers_interrupted_work_and_enforces_workflow_budget(tmp_path):
    spec = definition(max_workflow_runs=1)
    store = OptimizationRunStore(tmp_path)
    record = store.create(spec, "run-1")
    first, second = tuple(record["candidates"])[:2]
    store.start_candidate("run-1", first, workflow_run_id="workflow-1")
    recovered = store.recover("run-1")
    assert recovered["candidates"][first]["state"] == "interrupted"
    with pytest.raises(OptimizationError, match="budget is exhausted"):
        store.start_candidate("run-1", second, workflow_run_id="workflow-2")


def test_retries_consume_budget_and_require_new_workflow_identity(tmp_path):
    spec = definition(max_workflow_runs=2)
    store = OptimizationRunStore(tmp_path)
    record = store.create(spec, "run-1")
    first = next(iter(record["candidates"]))
    store.start_candidate("run-1", first, workflow_run_id="workflow-1")
    store.finish_candidate("run-1", first, state="failed", currentness="indeterminate")
    with pytest.raises(OptimizationError, match="cannot be reused"):
        store.start_candidate("run-1", first, workflow_run_id="workflow-1")
    store.start_candidate("run-1", first, workflow_run_id="workflow-2")
    store.finish_candidate("run-1", first, state="failed", currentness="indeterminate")
    with pytest.raises(OptimizationError, match="budget is exhausted"):
        store.start_candidate("run-1", first, workflow_run_id="workflow-3")


def test_atomic_failure_preserves_last_durable_record(tmp_path):
    spec = definition()
    stable = OptimizationRunStore(tmp_path)
    record = stable.create(spec, "run-1")
    first = next(iter(record["candidates"]))

    def fail(point, _record):
        if point == "after_stage":
            raise RuntimeError("injected")

    broken = OptimizationRunStore(tmp_path, fault_injector=fail)
    with pytest.raises(RuntimeError, match="injected"):
        broken.start_candidate("run-1", first, workflow_run_id="workflow-1")
    assert stable.load("run-1")["candidates"][first]["state"] == "pending"


def test_human_selection_is_exact_current_and_publish_once(tmp_path):
    spec = definition()
    store = OptimizationRunStore(tmp_path)
    record = store.create(spec, "run-1")
    first = next(iter(record["candidates"]))
    store.start_candidate("run-1", first, workflow_run_id="workflow-1")
    store.finish_candidate("run-1", first, state="succeeded", currentness="current",
                           metrics={"mass": "1.25", "margin": "3"},
                           findings=({"code": "verified"},))
    with pytest.raises(OptimizationError, match="Source design changed"):
        store.authorize_selection(spec, "run-1", candidate_id=first,
                                  human_authorization_id="approval-1",
                                  observed_source_revision="revision-8", observed_source_sha256=DIGEST_A)
    selected = store.authorize_selection(spec, "run-1", candidate_id=first,
                                         human_authorization_id="approval-1",
                                         observed_source_revision="revision-7", observed_source_sha256=DIGEST_A)
    assert selected["selection"]["candidate_id"] == first
    published = store.publish_once("run-1", candidate_id=first,
                                   human_authorization_id="approval-1", publication_receipt_id="receipt-1")
    replay = store.publish_once("run-1", candidate_id=first,
                                human_authorization_id="approval-1", publication_receipt_id="receipt-1")
    assert replay["publication"] == published["publication"]
    with pytest.raises(OptimizationError, match="cannot publish twice"):
        store.publish_once("run-1", candidate_id=first,
                           human_authorization_id="approval-1", publication_receipt_id="receipt-2")
    serialized = json.dumps(replay)
    assert "accepted_geometry" not in serialized
    assert "document_object" not in serialized


def test_stale_failed_cancelled_and_indeterminate_treatments_are_explicit():
    spec = definition()
    ids = [item["candidate_id"] for item in spec.candidates()]
    evaluations = {
        ids[0]: {"state": "failed", "currentness": "indeterminate", "metrics": {}},
        ids[1]: {"state": "cancelled", "currentness": "indeterminate", "metrics": {}},
        ids[2]: {"state": "succeeded", "currentness": "stale", "metrics": {"mass": "1", "margin": "3"}},
        ids[3]: {"state": "succeeded", "currentness": "indeterminate", "metrics": {"mass": "1", "margin": "3"}},
        ids[4]: {"state": "succeeded", "currentness": "current", "metrics": {"mass": "2", "margin": "3"}},
    }
    assert [item["candidate_id"] for item in rank_candidates(spec, evaluations)] == [ids[4]]
