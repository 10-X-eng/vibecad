# SPDX-License-Identifier: LGPL-2.1-or-later
from __future__ import annotations

import pytest

from VibeCADAnalysisContracts import AnalysisContractError, CanonicalJson
from VibeCADAnalysisWorkflow import (
    WorkflowDefinition, WorkflowEdge, WorkflowNode, WorkflowRequirement,
    WorkflowRunError, WorkflowRunStore,
)


def _node(name: str, inputs=(), outputs=(), fan_out=1) -> WorkflowNode:
    return WorkflowNode(name, "fem", f"adapter.{name}", tuple(inputs), tuple(outputs),
                        condition=CanonicalJson.from_value({"all": []}), max_fan_out=fan_out,
                        retry_limit=1)


def _benchmark() -> WorkflowDefinition:
    names = ("geometry", "mesh", "solve", "postprocess", "verify")
    nodes = tuple(_node(name, () if i == 0 else (names[i-1],),
                        (name,), 1) for i, name in enumerate(names))
    edges = tuple(WorkflowEdge(names[i], names[i+1], names[i], names[i]) for i in range(4))
    return WorkflowDefinition("local-fem-benchmark", "1", nodes, edges)


def test_benchmark_order_ready_nodes_and_identity_are_deterministic() -> None:
    workflow = _benchmark()
    assert workflow.topological_order() == ("geometry", "mesh", "solve", "postprocess", "verify")
    assert workflow.ready_nodes({}) == ("geometry",)
    assert workflow.ready_nodes({"geometry": "succeeded"}) == ("mesh",)
    assert workflow.sha256() == _benchmark().sha256()


def test_cycle_missing_node_undeclared_output_and_fanout_are_refused() -> None:
    a, b = _node("a", ("b",), ("a",)), _node("b", ("a",), ("b",))
    with pytest.raises(AnalysisContractError, match="cycle"):
        WorkflowDefinition("cycle", "1", (a, b),
                           (WorkflowEdge("a", "b", "a", "a"), WorkflowEdge("b", "a", "b", "b")))
    with pytest.raises(AnalysisContractError, match="missing node"):
        WorkflowDefinition("missing", "1", (_node("a", outputs=("a",)),),
                           (WorkflowEdge("a", "b", "a", "a"),))
    with pytest.raises(AnalysisContractError, match="undeclared output"):
        WorkflowDefinition("output", "1", (_node("a"), _node("b", ("x",))),
                           (WorkflowEdge("a", "b", "x", "x"),))
    with pytest.raises(AnalysisContractError, match="fan-out"):
        WorkflowDefinition("fanout", "1",
                           (_node("a", outputs=("x",)), _node("b", ("x",)), _node("c", ("x",))),
                           (WorkflowEdge("a", "b", "x", "x"), WorkflowEdge("a", "c", "x", "x")))


def test_conditions_reject_live_objects_and_unbounded_retry_fanout() -> None:
    with pytest.raises(AnalysisContractError):
        WorkflowNode("bad", "fem", "adapter", (), (), condition=CanonicalJson.from_value({"live": object()}))
    with pytest.raises(AnalysisContractError, match="retry_limit"):
        WorkflowNode("bad", "fem", "adapter", (), (), retry_limit=17)
    with pytest.raises(AnalysisContractError, match="max_fan_out"):
        WorkflowNode("bad", "fem", "adapter", (), (), max_fan_out=33)


def test_durable_benchmark_recovers_and_retries_with_new_attempt(tmp_path) -> None:
    workflow = _benchmark()
    store = WorkflowRunStore(tmp_path)
    store.create(workflow, "run-1")
    assert store.eligible_ready_nodes(workflow, "run-1") == ("geometry",)
    store.start_node("run-1", "geometry", analysis_id="analysis-geometry-1")
    recovered = WorkflowRunStore(tmp_path).recover("run-1")
    assert recovered["nodes"]["geometry"]["state"] == "interrupted"
    retried = store.start_node("run-1", "geometry", analysis_id="analysis-geometry-2")
    assert [item["attempt"] for item in retried["nodes"]["geometry"]["attempts"]] == [1, 2]
    assert [item["analysis_id"] for item in retried["nodes"]["geometry"]["attempts"]] == [
        "analysis-geometry-1", "analysis-geometry-2"
    ]
    store.finish_node("run-1", "geometry", state="failed", outcome={"output": "geometry"})
    with pytest.raises(WorkflowRunError, match="retry limit"):
        store.start_node("run-1", "geometry", analysis_id="analysis-geometry-3")


def test_workflow_discovery_retains_current_and_prior_analysis_links(tmp_path) -> None:
    workflow = _benchmark()
    store = WorkflowRunStore(tmp_path)
    store.create(workflow, "run-2")
    store.start_node("run-2", "geometry", analysis_id="analysis-first")
    store.recover("run-2")
    store.start_node("run-2", "geometry", analysis_id="analysis-retry")

    assert [record["run_id"] for record in store.list_records()] == ["run-2"]
    assert store.find_by_analysis_ids(["analysis-first"])[0]["run_id"] == "run-2"
    assert store.find_by_analysis_ids(["analysis-retry"])[0]["run_id"] == "run-2"
    assert store.find_by_analysis_ids(["unrelated"]) == ()
    resolved, missing = store.find_by_run_ids(["run-2", "run-missing"])
    assert [record["run_id"] for record in resolved] == ["run-2"]
    assert missing == ("run-missing",)
    with pytest.raises(WorkflowRunError, match="unique"):
        store.find_by_run_ids(["run-2", "run-2"])


def test_upstream_eligibility_rejects_stale_failed_and_unpublished(tmp_path) -> None:
    geometry = _node("geometry", outputs=("geometry",))
    mesh = WorkflowNode(
        "mesh", "fem", "adapter.mesh", ("geometry",), ("mesh",),
        requirements=(WorkflowRequirement("geometry"),),
    )
    workflow = WorkflowDefinition("eligibility", "1", (geometry, mesh),
                                  (WorkflowEdge("geometry", "mesh", "geometry", "geometry"),))
    for index, outcome in enumerate((
        {"output": "geometry", "execution_state": "failed", "currentness": "current", "publication_state": "published"},
        {"output": "geometry", "execution_state": "succeeded", "currentness": "stale", "publication_state": "published"},
        {"output": "geometry", "execution_state": "succeeded", "currentness": "current", "publication_state": "unpublished"},
    )):
        run = f"rejected-{index}"
        store = WorkflowRunStore(tmp_path / run)
        store.create(workflow, run)
        store.start_node(run, "geometry", analysis_id=f"analysis-{index}")
        store.finish_node(run, "geometry", state="succeeded", outcome=outcome)
        assert store.eligible_ready_nodes(workflow, run) == ()

    store = WorkflowRunStore(tmp_path / "accepted")
    store.create(workflow, "accepted")
    store.start_node("accepted", "geometry", analysis_id="analysis-ok")
    store.finish_node("accepted", "geometry", state="succeeded", outcome={
        "output": "geometry", "execution_state": "succeeded",
        "currentness": "current", "publication_state": "published",
    })
    assert store.eligible_ready_nodes(workflow, "accepted") == ("mesh",)


def test_workflow_cancel_is_distinct_and_late_completion_cannot_reopen(tmp_path) -> None:
    workflow = _benchmark()
    store = WorkflowRunStore(tmp_path)
    store.create(workflow, "cancel-run")
    store.start_node("cancel-run", "geometry", analysis_id="analysis-1")
    cancelled = store.cancel("cancel-run")
    assert cancelled["nodes"]["geometry"]["state"] == "cancel_requested"
    assert cancelled["nodes"]["mesh"]["state"] == "cancelled"
    late = store.finish_node("cancel-run", "geometry", state="succeeded", outcome={"output": "geometry"})
    assert late["nodes"]["geometry"]["state"] == "cancelled"
    assert store.eligible_ready_nodes(workflow, "cancel-run") == ()


def test_publication_receipt_is_idempotent_and_cannot_change(tmp_path) -> None:
    publish = WorkflowNode("geometry", "fem", "adapter", (), ("geometry",),
                           publication_policy="publish_once")
    workflow = WorkflowDefinition("publish", "1", (publish,), ())
    store = WorkflowRunStore(tmp_path)
    store.create(workflow, "publish-run")
    store.start_node("publish-run", "geometry", analysis_id="analysis-1")
    with pytest.raises(WorkflowRunError, match="requires its receipt"):
        store.finish_node("publish-run", "geometry", state="succeeded", outcome={"output": "geometry"})
    store.finish_node("publish-run", "geometry", state="succeeded", outcome={"output": "geometry"},
                      publication_receipt_id="receipt-1")
    with pytest.raises(WorkflowRunError):
        store.finish_node("publish-run", "geometry", state="succeeded", outcome={"output": "geometry"},
                          publication_receipt_id="receipt-2")


@pytest.mark.parametrize("fault_point", ("before_stage", "after_stage", "before_replace"))
def test_fault_before_atomic_replace_preserves_previous_run(fault_point, tmp_path) -> None:
    workflow = _benchmark()
    store = WorkflowRunStore(tmp_path)
    store.create(workflow, "fault-run")
    baseline = store.load("fault-run")
    failing = WorkflowRunStore(
        tmp_path,
        fault_injector=lambda point, _record: (_ for _ in ()).throw(RuntimeError(point))
        if point == fault_point else None,
    )
    with pytest.raises(RuntimeError, match=fault_point):
        failing.start_node("fault-run", "geometry", analysis_id="analysis-1")
    assert store.load("fault-run") == baseline


def test_deterministic_condition_skip_and_terminal_summary(tmp_path) -> None:
    node = WorkflowNode(
        "optional", "fem", "adapter", (), ("result",),
        condition=CanonicalJson.from_value({"all": [{"key": "enabled", "equals": True}]}),
    )
    workflow = WorkflowDefinition("condition", "1", (node,), ())
    assert workflow.condition_allows(node, {"enabled": True}) is True
    assert workflow.condition_allows(node, {"enabled": False}) is False
    store = WorkflowRunStore(tmp_path)
    store.create(workflow, "condition-run")
    finished = store.skip_node("condition-run", "optional", reason="condition_false")
    assert finished["state"] == "succeeded"
    assert store.summary("condition-run")["nodes"]["optional"] == {
        "state": "skipped", "analysis_id": None, "publication_receipt_id": None
    }


@pytest.mark.parametrize("failed_node", ("geometry", "mesh", "solve", "postprocess", "verify"))
def test_five_stage_benchmark_injected_failure_stops_downstream(failed_node, tmp_path) -> None:
    workflow = _benchmark()
    store = WorkflowRunStore(tmp_path / failed_node)
    store.create(workflow, failed_node)
    for node_id in workflow.topological_order():
        assert store.eligible_ready_nodes(workflow, failed_node) == (node_id,)
        store.start_node(failed_node, node_id, analysis_id=f"analysis-{node_id}")
        state = "failed" if node_id == failed_node else "succeeded"
        store.finish_node(failed_node, node_id, state=state, outcome={
            "output": node_id, "execution_state": state,
            "currentness": "current", "publication_state": "not_required",
        })
        if state == "failed":
            break
    assert store.eligible_ready_nodes(workflow, failed_node) == ()
    record = store.load(failed_node)
    order = workflow.topological_order()
    failed_index = order.index(failed_node)
    assert all(record["nodes"][name]["state"] == "pending" for name in order[failed_index + 1:])


def test_interprocess_writer_lock_refuses_competing_transition(tmp_path) -> None:
    workflow = _benchmark()
    first = WorkflowRunStore(tmp_path)
    second = WorkflowRunStore(tmp_path)
    first.create(workflow, "locked")
    with first._writer():
        with pytest.raises(WorkflowRunError, match="Another process"):
            second.start_node("locked", "geometry", analysis_id="analysis-1")
    assert first.load("locked")["nodes"]["geometry"]["state"] == "pending"
