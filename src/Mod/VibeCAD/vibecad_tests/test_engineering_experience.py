# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from dataclasses import replace
import json

import pytest

from VibeCADAnalysisContracts import AnalysisContractError
from VibeCADAnalysisPersistence import AnalysisMetadataStore, new_job_record
from VibeCADAnalysisWorkflow import (
    WorkflowDefinition,
    WorkflowNode,
    WorkflowRunStore,
)
from VibeCADEngineeringContracts import (
    ContentDescriptor,
    EngineeringIdentity,
    EngineeringResultEnvelope,
    FindingEnvelope,
    ProvenanceEdge,
    ProvenanceGraph,
    ProvenanceNode,
    canonical_payload,
)
from VibeCADEngineeringExperience import (
    DomainPresentation,
    EngineeringFieldProjection,
    PresentationMetric,
    governance_role,
    project_analysis_activity,
    project_engineering_result,
    project_optimization_run,
    project_workflow_run,
)
from VibeCADGovernedOptimization import (
    DesignVariable,
    MetricConstraint,
    Objective,
    OptimizationBudget,
    OptimizationDefinition,
    OptimizationRunStore,
)


DOMAINS = ("native", "fem", "aero", "manufacture", "assembly", "robot")


def identity(owner, kind, value):
    return EngineeringIdentity("vibecad", owner, kind, value, "1")


def result(domain):
    source = identity("native", "document", f"doc-{domain}")
    activity = identity(domain, "activity", f"activity-{domain}")
    result_id = identity(domain, "result", f"result-{domain}")
    artifact = ContentDescriptor("application/json", "sha256", "a" * 64, 12,
                                 "result", "v1")
    finding = FindingEnvelope(
        f"finding-{domain}", "rule-1", "verifier", domain, "pass", "note",
        "bounded", "Domain-owned evidence", (source,), (artifact,), "",
        "current", "engineering-evidence-only",
    )
    provenance = ProvenanceGraph(
        f"graph-{domain}",
        (
            ProvenanceNode(source.canonical, "entity", canonical_payload({"role": "source"})),
            ProvenanceNode(activity.canonical, "activity", canonical_payload({"role": "solve"})),
            ProvenanceNode(result_id.canonical, "entity", canonical_payload({"role": "result"})),
        ),
        (
            ProvenanceEdge("used", "used", activity.canonical, source.canonical),
            ProvenanceEdge("generated", "generated", result_id.canonical, activity.canonical),
        ),
    )
    return EngineeringResultEnvelope(
        1, 0, result_id, activity, domain, f"adapter.{domain}", "attempt-1",
        "succeeded", "pass", "current", "published", source, "b" * 64,
        (artifact,), canonical_payload({"domain_metric": 12}), (finding,),
        provenance, canonical_payload({"owned": {"domain": domain, "exact": True}}),
    )


def presentation():
    return DomainPresentation(
        "Engineering result",
        (PresentationMetric("maximum", "Maximum", 347.8, "MPa", "max"),),
        (EngineeringFieldProjection(
            "vonMises", "Von Mises Stress", "stress.von_mises", "point", 1,
            "MPa", 12.4, 347.8, "scalar", "turbo",
        ),),
        canonical_payload({"page": "results"}),
    )


@pytest.mark.parametrize("domain", DOMAINS)
def test_projection_preserves_domain_payload_and_exact_common_identity(domain):
    envelope = result(domain)
    projected = project_engineering_result(envelope, presentation())

    assert projected["domain"] == domain
    assert projected["result_id"] == envelope.result_id.to_dict()
    assert projected["source_identity"] == envelope.source_identity.to_dict()
    assert projected["domain_payload"] == envelope.domain_payload.to_value()
    assert projected["domain_payload_sha256"] == envelope.domain_payload.sha256()
    assert projected["findings"] == [envelope.findings[0].to_dict()]
    assert projected["provenance"] == envelope.provenance.to_dict()
    assert projected["presentation_only"] is True
    assert set(projected["authority"].values()) == {False}


def test_four_governance_axes_are_independent():
    original = result("fem")
    cases = (
        replace(original, execution_status="failed"),
        replace(original, verification_verdict="indeterminate"),
        replace(original, currentness="stale"),
        replace(original, publication_state="historical"),
    )
    expected = (
        ("failed", "pass", "current", "published"),
        ("succeeded", "indeterminate", "current", "published"),
        ("succeeded", "pass", "stale", "published"),
        ("succeeded", "pass", "current", "historical"),
    )
    for envelope, values in zip(cases, expected):
        axes = project_engineering_result(envelope, presentation())["axes"]
        assert tuple(axes[name]["value"] for name in
                     ("execution", "verification", "currentness", "publication")) == values


def test_scientific_color_map_is_not_derived_from_governance_role():
    projected = project_engineering_result(
        replace(result("fem"), verification_verdict="failed"), presentation()
    )
    assert projected["fields"][0]["default_color_map"] == "turbo"
    assert projected["axes"]["verification"]["role"] == "negative"
    assert "turbo" not in {axis["role"] for axis in projected["axes"].values()}


@pytest.mark.parametrize("axis,value,role", (
    ("execution", "running", "active"),
    ("execution", "cancelled", "historical"),
    ("verification", "indeterminate", "caution"),
    ("currentness", "stale", "caution"),
    ("publication", "authorized", "active"),
))
def test_governance_roles_are_semantic_not_literal_colors(axis, value, role):
    assert governance_role(axis, value) == role


def test_projection_is_bounded_json_without_live_authority_objects():
    projected = project_engineering_result(result("aero"), presentation())
    encoded = json.dumps(projected, sort_keys=True, allow_nan=False)
    for forbidden in ("callback", "document_object", "publish_function", "credential"):
        assert forbidden not in encoded


def test_field_contract_rejects_invalid_ranges_colormaps_and_nonfinite_values():
    with pytest.raises(AnalysisContractError, match="minimum"):
        EngineeringFieldProjection("f", "Field", "scalar", "point", 1, "Pa",
                                   2, 1, "scalar", "turbo")
    with pytest.raises(AnalysisContractError, match="color map"):
        EngineeringFieldProjection("f", "Field", "scalar", "point", 1, "Pa",
                                   1, 2, "scalar", "decorative-rainbow")
    with pytest.raises(AnalysisContractError, match="finite"):
        PresentationMetric("m", "Metric", float("nan"), "Pa")


def test_duplicate_and_unbounded_projection_items_are_refused():
    metric = PresentationMetric("m", "Metric", 1, "Pa")
    with pytest.raises(AnalysisContractError, match="unique"):
        DomainPresentation("Duplicate", (metric, metric))
    with pytest.raises(AnalysisContractError, match="bounded"):
        DomainPresentation("Too many", tuple(
            PresentationMetric(f"m-{index}", "Metric", index, "Pa")
            for index in range(129)
        ))


def test_unknown_status_axis_is_refused():
    with pytest.raises(AnalysisContractError, match="Unknown governance"):
        governance_role("scientific", "red")


def test_analysis_activity_projection_uses_exact_durable_record_without_mutation(tmp_path):
    store = AnalysisMetadataStore(tmp_path / "analysis")
    digest = "a" * 64
    store.create(new_job_record(
        analysis_id="analysis-1", domain="fem", adapter_id="adapter.fem",
        source_document_uid="document-1", prepared_analysis_sha256=digest,
        dependency_sha256="b" * 64, input_manifest_sha256="c" * 64,
        execution_spec_sha256="d" * 64,
    ))
    store.begin_attempt("analysis-1", provider_id="local", provider_kind="local")
    store.record_artifact("analysis-1", {
        "sha256": "e" * 64, "media_type": "application/json", "role": "result",
    }, pinned=True)
    before = store.load("analysis-1")
    disposition = store.restart_disposition("analysis-1")

    projected = project_analysis_activity(before, restart_disposition=disposition)

    assert projected["analysis_id"] == "analysis-1"
    assert projected["attempts"] == before["attempts"]
    assert projected["artifacts"] == before["artifacts"]
    assert projected["restart_disposition"] == disposition
    assert projected["attempt_count"] == projected["artifact_count"] == 1
    assert set(projected["authority"].values()) == {False}
    assert store.load("analysis-1") == before
    with pytest.raises(AnalysisContractError, match="another Analysis"):
        project_analysis_activity(before, restart_disposition={
            **disposition, "analysis_id": "analysis-2",
        })


def test_workflow_projection_follows_store_and_never_schedules(tmp_path):
    definition = WorkflowDefinition("fem-flow", "1", (
        WorkflowNode("geometry", "fem", "adapter.geometry", (), ("geometry",)),
        WorkflowNode("mesh", "fem", "adapter.mesh", ("geometry",), ("mesh",)),
    ), ())
    store = WorkflowRunStore(tmp_path / "workflow")
    store.create(definition, "workflow-run-1")
    store.start_node("workflow-run-1", "geometry", analysis_id="analysis-geometry")
    before = store.load("workflow-run-1")

    projected = project_workflow_run(before)

    assert projected["run_id"] == "workflow-run-1"
    assert projected["definition_sha256"] == definition.sha256()
    assert projected["counts"] == {"pending": 1, "running": 1}
    assert projected["nodes"][0]["attempts"] == before["nodes"]["geometry"]["attempts"]
    assert set(projected["authority"].values()) == {False}
    assert store.load("workflow-run-1") == before


def _optimization_definition():
    return OptimizationDefinition(
        optimization_id="bracket", source_document_uid="document-1",
        source_revision="revision-1", source_sha256="a" * 64,
        workflow_definition_sha256="b" * 64,
        variables=(DesignVariable("width", "integer", "mm", "PartDesign", ("1", "2")),),
        objectives=(Objective("mass", "minimize"),),
        constraints=(MetricConstraint("margin", ">=", "2"),),
        budget=OptimizationBudget(2, 2, 60, 100),
    )


def test_optimization_projection_consumes_store_ranking_without_selecting(tmp_path):
    definition = _optimization_definition()
    store = OptimizationRunStore(tmp_path / "optimization")
    created = store.create(definition, "optimization-run-1")
    candidate_id = next(iter(created["candidates"]))
    store.start_candidate("optimization-run-1", candidate_id, workflow_run_id="workflow-1")
    store.finish_candidate(
        "optimization-run-1", candidate_id, state="succeeded", currentness="current",
        metrics={"mass": "1.25", "margin": "3"}, findings=({"code": "checked"},),
    )
    before = store.load("optimization-run-1")
    ranking = store.ranking(definition, "optimization-run-1")

    projected = project_optimization_run(before, ranking)

    selected = next(item for item in projected["candidates"] if item["candidate_id"] == candidate_id)
    assert selected["rank"] == 1
    assert selected["mutation_proposal"] == before["candidates"][candidate_id]["mutation_proposal"]
    assert projected["selection"] is None
    assert projected["publication"] is None
    assert set(projected["authority"].values()) == {False}
    assert store.load("optimization-run-1") == before
    with pytest.raises(AnalysisContractError, match="unknown candidate"):
        project_optimization_run(before, ({"candidate_id": "missing", "rank": 1},))


@pytest.mark.parametrize("projector,record,match", (
    (project_analysis_activity, {"schema_version": 999}, "incomplete"),
    (project_workflow_run, {"schema_version": 999}, "incomplete"),
))
def test_durable_projections_reject_incomplete_records(projector, record, match):
    with pytest.raises(AnalysisContractError, match=match):
        projector(record)
