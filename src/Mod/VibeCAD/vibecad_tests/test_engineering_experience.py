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
    EngineeringFieldViewState,
    PresentationMetric,
    governance_role,
    project_analysis_activity,
    project_assembly_state,
    project_engineering_result,
    project_manufacture_post_evidence,
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


def test_field_contract_preserves_explicitly_unavailable_metadata():
    field = EngineeringFieldProjection(
        "unknown", "Unknown", "domain.unknown", "cell", 4,
        None, None, None, "vector", "viridis",
    )
    assert field.to_dict()["unit"] is None
    assert field.to_dict()["minimum"] is None
    assert field.to_dict()["maximum"] is None
    with pytest.raises(AnalysisContractError, match="both be known"):
        EngineeringFieldProjection(
            "partial", "Partial", "domain.partial", "point", 1,
            None, 0, None, "scalar", "viridis",
        )


def test_field_view_state_separates_validated_ui_state_from_engineering_data():
    state = EngineeringFieldViewState(
        "vonMises", "turbo", "manual", 12.4, 347.8, 2.5, True, True, True
    )

    assert state.to_dict() == {
        "selected_field_id": "vonMises",
        "color_map": "turbo",
        "range_mode": "manual",
        "range_minimum": 12.4,
        "range_maximum": 347.8,
        "deformation_scale": 2.5,
        "show_mesh_edges": True,
        "show_legend": True,
        "show_undeformed": True,
    }


@pytest.mark.parametrize(
    "arguments,message",
    (
        (("field", "rainbow"), "color map"),
        (("field", "turbo", "manual"), "require both"),
        (("field", "turbo", "auto", 0.0, 1.0), "cannot carry"),
        (("field", "turbo", "manual", 2.0, 1.0), "cannot exceed"),
        (("field", "turbo", "auto", None, None, -1.0), "between"),
        (("field", "turbo", "auto", None, None, 1.0, 1), "boolean"),
    ),
)
def test_field_view_state_rejects_ambiguous_or_invalid_controls(arguments, message):
    with pytest.raises(AnalysisContractError, match=message):
        EngineeringFieldViewState(*arguments)


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


def _manufacture_post_result():
    return {
        "operation": "complete_job",
        "job": {
            "object_name": "Job", "state_sha256": "a" * 64,
            "posted_operation_count": 2, "command_count": 48,
            "active_operation_count": 2,
        },
        "postprocessor": {
            "name": "grbl", "source_sha256": "b" * 64,
            "machine_configured": True, "machine_config_sha256": "c" * 64,
        },
        "outputs": [{
            "file_name": "bracket.ngc", "size_bytes": 128,
            "sha256": "d" * 64, "replaced_existing": False,
        }],
        "output_count": 1,
        "total_size_bytes": 128,
        "document_unchanged": True,
        "history_unchanged": True,
        "selection_unchanged": True,
        "visibility_unchanged": True,
        "claim_ceiling": "not_proven_toolpath",
        "proven_toolpath": False,
        "manufacturable": False,
    }


def _manufacture_governance_records():
    analysis = {
        "schema_version": 1, "analysis_id": "analysis-post-1",
        "domain": "manufacture", "adapter_id": "native.manufacture.post",
        "source_document_uid": "document-1", "state": "succeeded",
        "created_at": "2026-08-26T00:00:00Z", "updated_at": "2026-08-26T00:01:00Z",
        "terminal_reason": "completed",
        "attempts": [{"attempt": 1, "provider_id": "native-background"}],
        "artifacts": [], "currentness_evaluations": [],
        "publication": {"intent": {}, "authorization": {}, "receipt": {}},
        "events": [{"sequence": 1, "state": "succeeded"}],
    }
    workflow = {
        "schema_version": 1, "run_id": "workflow-post-1",
        "workflow_id": "manufacture-post", "workflow_version": "1",
        "definition_sha256": "f" * 64, "state": "succeeded",
        "cancel_requested": False, "nodes": {
            "post": {"state": "succeeded", "analysis_id": "analysis-post-1",
                     "attempts": [{"attempt": 1, "analysis_id": "analysis-post-1"}],
                     "outcome": {}, "publication_receipt_id": "receipt-1"},
        },
    }
    return analysis, workflow


def test_manufacture_post_projection_preserves_owner_evidence_and_claim_ceiling():
    source = _manufacture_post_result()
    analysis, workflow = _manufacture_governance_records()
    projected = project_manufacture_post_evidence(
        source, analysis_record=analysis, workflow_record=workflow,
        provider_attempt_id="1",
    )

    assert projected["job"] == source["job"]
    assert projected["postprocessor"] == source["postprocessor"]
    assert projected["outputs"] == source["outputs"]
    assert projected["analysis_id"] == "analysis-post-1"
    assert projected["workflow_run_id"] == "workflow-post-1"
    assert projected["claim_ceiling"] == "not_proven_toolpath"
    assert projected["proven_toolpath"] is projected["manufacturable"] is False
    assert set(projected["authority"].values()) == {False}


@pytest.mark.parametrize("change,match", (
    ({"claim_ceiling": "machine_verified", "proven_toolpath": True}, "claim ceiling"),
    ({"output_count": 2}, "count"),
    ({"total_size_bytes": 127}, "byte total"),
    ({"document_unchanged": False}, "unrelated domain state"),
))
def test_manufacture_post_projection_rejects_overclaim_and_inconsistent_evidence(change, match):
    source = {**_manufacture_post_result(), **change}
    analysis, workflow = _manufacture_governance_records()
    with pytest.raises(AnalysisContractError, match=match):
        project_manufacture_post_evidence(
            source, analysis_record=analysis, workflow_record=workflow,
            provider_attempt_id="1",
        )


def test_manufacture_post_projection_requires_exact_analysis_workflow_linkage():
    analysis, workflow = _manufacture_governance_records()
    with pytest.raises(AnalysisContractError, match="not bound"):
        project_manufacture_post_evidence(
            _manufacture_post_result(), analysis_record=analysis,
            workflow_record={**workflow, "nodes": {
                "post": {**workflow["nodes"]["post"], "analysis_id": "another-analysis"},
            }}, provider_attempt_id="1",
        )
    with pytest.raises(AnalysisContractError, match="attempt"):
        project_manufacture_post_evidence(
            _manufacture_post_result(), analysis_record=analysis,
            workflow_record=workflow, provider_attempt_id="2",
        )


def test_assembly_projection_preserves_graph_identity_and_refuses_inference_authority():
    state = {
        "available": True, "state_sha256": "a" * 64,
        "component_count": 3, "grounded_count": 1, "joint_count": 2,
        "eligible_joint_count": 1, "simulation_count": 1, "motion_count": 1,
        "eligible_joints": [{"object_name": "Hinge", "joint_type": "Revolute",
                             "supported_motion_types": ["angular"]}],
        "simulations": [{"object_name": "Cycle", "motion_count": 1,
                         "time_start_seconds": 0.0, "time_end_seconds": 2.0,
                         "output_time_step_seconds": 0.05}],
    }
    diagnostics = {
        "solver_status": 0, "remaining_degrees_of_freedom": 1,
        "has_conflicts": False, "has_redundancies": False,
        "has_partial_redundancies": False, "has_malformed_constraints": False,
        "conflicting_joints": [], "redundant_joints": [],
        "partially_redundant_joints": [], "malformed_joints": [],
        "grounded_components": [], "residual_tolerance": 1.0e-6,
    }

    projected = project_assembly_state(state, diagnostics)

    assert projected["graph_state_sha256"] == "a" * 64
    assert projected["solver_diagnostics"] == diagnostics
    assert projected["continuous_motion_certified"] is False
    assert projected["joint_proposals"] == projected["sequence_proposals"] == []
    assert projected["service_proposals"] == []
    assert set(projected["authority"].values()) == {False}
