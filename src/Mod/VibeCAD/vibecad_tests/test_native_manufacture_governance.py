# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADNativeManufactureCamoticsRuntime as camotics_runtime_module
import VibeCADNativeManufacturePostRuntime as runtime_module
import VibeCADNativeManufactureSimulationResultRuntime as retained_runtime_module
import VibeCADNativeManufactureSimulationRuntime as gl_runtime_module
from VibeCADEngineeringExperience import (
    project_manufacture_post_evidence,
    project_manufacture_simulation_evidence,
)
from VibeCADNativeManufactureGovernance import (
    create_manufacture_evidence_governance,
    create_manufacture_post_governance,
    manufacture_post_identity,
)
from VibeCADNativeManufacturePostRuntime import NativeManufacturePostRuntime
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket, NativeDocumentStateStore
from VibeCADNativeUndo import NativeAssistantUndoLedger


def _frozen():
    file_identity = lambda digest, size: SimpleNamespace(sha256=digest, size=size)
    return SimpleNamespace(
        operation_variant="complete_job",
        job_name="Job",
        job_before={"state_sha256": "a" * 64},
        selected_operation_names=(),
        selected_operation_state_sha256=(),
        snapshot_sha256="b" * 64,
        snapshot_size=4096,
        postprocessor_source=file_identity("c" * 64, 1200),
        machine_config_sha256="d" * 64,
        freecadcmd=file_identity("e" * 64, 2000),
        child_script=file_identity("f" * 64, 3000),
        postprocessor_name="grbl",
        machine_name="router",
        use_machine_flow=True,
    )


def _result():
    return {
        "operation": "complete_job",
        "job": {
            "object_name": "Job",
            "state_sha256": "a" * 64,
            "posted_operation_count": 2,
            "command_count": 48,
            "active_operation_count": 2,
        },
        "postprocessor": {
            "name": "grbl",
            "source_sha256": "c" * 64,
            "machine_configured": True,
            "machine_config_sha256": "d" * 64,
        },
        "outputs": [{
            "file_name": "bracket.ngc",
            "size_bytes": 128,
            "sha256": "1" * 64,
            "replaced_existing": False,
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


def _simulation_frozen():
    return SimpleNamespace(
        job=SimpleNamespace(Name="Job"),
        expected_job_state_sha256="a" * 64,
        runs=(SimpleNamespace(
            operation_name="Profile",
            expected_state_sha256="b" * 64,
        ),),
        quality=5,
        resolution="medium",
        request_kind="read_result",
        command_count=48,
    )


def test_identity_is_deterministic_and_contains_no_private_paths() -> None:
    first = manufacture_post_identity(_frozen())
    second = manufacture_post_identity(_frozen())

    assert first == second
    assert set(first) == {
        "prepared_analysis_sha256",
        "dependency_sha256",
        "input_manifest_sha256",
        "execution_spec_sha256",
    }
    assert all(len(value) == 64 for value in first.values())


def test_success_creates_linked_analysis_workflow_artifact_and_projection(tmp_path) -> None:
    lifecycle = create_manufacture_post_governance(_frozen(), root=tmp_path)
    lifecycle.submitted("analysis-post-1", "document-1", "manufacture.post.complete_job")
    lifecycle.started()
    lifecycle.prepared()
    lifecycle.publication_started()
    result = lifecycle.record_result(_result())
    lifecycle.succeeded("2" * 64)

    analysis = lifecycle.analysis_store.load("analysis-post-1")
    workflow = lifecycle.workflow_store.load(lifecycle.workflow_run_id)
    projected = project_manufacture_post_evidence(
        result,
        analysis_record=analysis,
        workflow_record=workflow,
        provider_attempt_id=lifecycle.provider_attempt_id,
    )

    assert analysis["state"] == "succeeded"
    assert analysis["attempts"][0]["provider_id"] == "native-background"
    assert analysis["artifacts"][0]["sha256"] == "1" * 64
    assert analysis["artifacts"][0]["pinned"] is True
    assert analysis["publication"]["intent"]["claim_ceiling"] == "not_proven_toolpath"
    assert analysis["publication"]["authorization"] == {
        "kind": "human_selected_output_destinations",
        "authorized_output_count": 1,
        "destination_paths_persisted": False,
    }
    assert analysis["publication"]["receipt"]["analysis_id"] == "analysis-post-1"
    assert workflow["state"] == "succeeded"
    assert workflow["nodes"]["post"]["analysis_id"] == "analysis-post-1"
    assert projected["analysis_id"] == "analysis-post-1"
    assert projected["workflow_run_id"] == lifecycle.workflow_run_id
    assert projected["claim_ceiling"] == "not_proven_toolpath"
    assert projected["proven_toolpath"] is projected["manufacturable"] is False


def test_failure_finishes_both_records_without_publication(tmp_path) -> None:
    lifecycle = create_manufacture_post_governance(_frozen(), root=tmp_path)
    lifecycle.submitted("analysis-post-failed", "document-1", "manufacture.post.complete_job")
    lifecycle.started()
    lifecycle.failed("postprocessor_failed")

    analysis = lifecycle.analysis_store.load("analysis-post-failed")
    workflow = lifecycle.workflow_store.load(lifecycle.workflow_run_id)

    assert analysis["state"] == "failed"
    assert analysis["publication"]["receipt"] is None
    assert workflow["state"] == "failed"
    assert workflow["nodes"]["post"]["state"] == "failed"
    assert workflow["nodes"]["post"]["outcome"]["publication_state"] == "not_published"


def test_projection_rejects_mismatched_runtime_governance_references(tmp_path) -> None:
    lifecycle = create_manufacture_post_governance(_frozen(), root=tmp_path)
    lifecycle.submitted("analysis-post-2", "document-1", "manufacture.post.complete_job")
    lifecycle.started()
    lifecycle.prepared()
    lifecycle.publication_started()
    result = lifecycle.record_result(_result())
    lifecycle.succeeded("3" * 64)
    result["governance"]["analysis_id"] = "another-analysis"

    import pytest
    from VibeCADAnalysisContracts import AnalysisContractError

    with pytest.raises(AnalysisContractError, match="do not match"):
        project_manufacture_post_evidence(
            result,
            analysis_record=lifecycle.analysis_store.load("analysis-post-2"),
            workflow_record=lifecycle.workflow_store.load(lifecycle.workflow_run_id),
            provider_attempt_id="1",
        )


def test_failure_after_publication_gate_remains_outcome_unknown(tmp_path) -> None:
    lifecycle = create_manufacture_post_governance(_frozen(), root=tmp_path)
    lifecycle.submitted("analysis-post-unknown", "document-1", "manufacture.post.complete_job")
    lifecycle.started()
    lifecycle.prepared()
    lifecycle.publication_started()

    lifecycle.failed("MetadataWriteError")

    analysis = lifecycle.analysis_store.load("analysis-post-unknown")
    workflow = lifecycle.workflow_store.load(lifecycle.workflow_run_id)
    assert analysis["state"] == "publishing"
    assert analysis["terminal_reason"] is None
    assert analysis["publication"]["receipt"] is None
    assert workflow["state"] == "failed"
    assert workflow["nodes"]["post"]["state"] == "interrupted"
    assert workflow["nodes"]["post"]["outcome"]["publication_state"] == "outcome_unknown"


def test_camotics_evidence_is_durable_bounded_and_directly_projectable(tmp_path) -> None:
    lifecycle = create_manufacture_evidence_governance(
        _simulation_frozen(),
        adapter_id="native.manufacture.camotics",
        operation="camotics.read_result",
        evidence_kind="camotics",
        root=tmp_path,
    )
    lifecycle.submitted("camotics-1", "document-1", "manufacture.camotics.read_result")
    lifecycle.started()
    lifecycle.prepared()
    lifecycle.publication_started()
    result = lifecycle.record_result({
        "request": "read_result",
        "job": "Job",
        "operation_count": 1,
        "command_count": 48,
        "resolution": "medium",
        "path_step_count": 99,
        "duration_seconds": 4.25,
        "surface": {"sha256": "c" * 64, "facet_count": 24},
        "program_sha256": "d" * 64,
    })
    lifecycle.succeeded("e" * 64)

    projected = project_manufacture_simulation_evidence(
        "camotics",
        result,
        analysis_record=lifecycle.analysis_store.load("camotics-1"),
        workflow_record=lifecycle.workflow_store.load(lifecycle.workflow_run_id),
        provider_attempt_id="1",
    )

    assert projected["kind"] == "camotics"
    assert projected["evidence_sha256"] == "c" * 64
    assert projected["claim_ceiling"] == "simulation_evidence_only"
    assert projected["proven_toolpath"] is projected["manufacturable"] is False
    assert lifecycle.analysis_store.load("camotics-1")["artifacts"][0]["role"] == (
        "camotics_simulation_evidence"
    )


def test_gl_and_retained_profiles_admit_only_their_exact_owner_digest(tmp_path) -> None:
    cases = (
        (
            "gl_simulation",
            {"simulation": {"program_sha256": "f" * 64, "document_changed": False}},
            "f" * 64,
        ),
        (
            "retained_simulation",
            {"simulation_result": {
                "result": {"state_sha256": "1" * 64},
                "program_sha256": "2" * 64,
            }},
            "1" * 64,
        ),
    )
    for index, (kind, source, digest) in enumerate(cases, 1):
        lifecycle = create_manufacture_evidence_governance(
            _simulation_frozen(),
            adapter_id=f"native.manufacture.{kind}",
            operation=kind,
            evidence_kind=kind,
            root=tmp_path / kind,
        )
        analysis_id = f"simulation-{index}"
        lifecycle.submitted(analysis_id, "document-1", f"manufacture.{kind}")
        lifecycle.started()
        lifecycle.prepared()
        lifecycle.publication_started()
        result = lifecycle.record_result(source)
        lifecycle.succeeded("3" * 64)
        projected = project_manufacture_simulation_evidence(
            kind,
            result,
            analysis_record=lifecycle.analysis_store.load(analysis_id),
            workflow_record=lifecycle.workflow_store.load(lifecycle.workflow_run_id),
            provider_attempt_id="1",
        )
        assert projected["evidence_sha256"] == digest


def test_runtime_submits_the_governance_lifecycle_and_returns_exact_references(
    monkeypatch,
) -> None:
    document = SimpleNamespace(Uid="document-1")
    submitted = {}

    class Governance:
        def submitted(self, job_id, _document_uid, _capability):
            submitted["job_id"] = job_id

        def references(self):
            return {
                "analysis_id": submitted.get("job_id", ""),
                "workflow_run_id": "manufacture-post-job-1",
                "workflow_node_id": "post",
                "provider_attempt_id": "1",
            }

        def record_result(self, result):
            return result

        started = prepared = publication_started = lambda self: None
        succeeded = failed = lambda self, _value: None
        cancelled = lambda self: None

    governance = Governance()

    class Manager:
        def submit(self, **kwargs):
            submitted.update(kwargs)
            kwargs["durable_lifecycle"].submitted(
                "job-1", kwargs["document_uid"], kwargs["capability_name"]
            )
            return SimpleNamespace(
                job_id="job-1",
                capability_name=kwargs["capability_name"],
                phase="preparing",
                progress_percent=1,
                progress_message="Preparing detached data",
            )

    context = NativeRuntimeContext(
        service=object(),
        document=document,
        state=NativeDocumentStateStore(),
        undo_ledger=NativeAssistantUndoLedger(),
        reauthorize_turn=lambda: None,
        active_document=lambda: document,
        active_surface_id=lambda: "manufacture",
        edit_or_task_active=lambda: False,
        authorize_output=lambda _request: None,
        background_manager=Manager(),
        document_thread_dispatch=lambda operation: operation(),
    )
    monkeypatch.setattr(runtime_module, "preflight_post", lambda *_args, **_kwargs: _frozen())
    monkeypatch.setattr(
        runtime_module,
        "create_manufacture_post_governance",
        lambda _frozen_value: governance,
    )

    result = NativeManufacturePostRuntime(context).execute(
        {
            "operation": "complete_job",
            "job": {"object_name": "Job", "expected_state_sha256": "a" * 64},
        },
        NativeCallTicket("document-1", "manufacture.post", 0, "token-1"),
    )

    assert submitted["durable_lifecycle"] is governance
    assert submitted["capability_name"] == "manufacture.post.complete_job"
    assert result["governance"] == governance.references()
    assert result["claim_ceiling"] == "not_proven_toolpath"


@pytest.mark.parametrize(
    ("module", "runtime_type", "method", "arguments", "capability"),
    (
        (
            camotics_runtime_module,
            camotics_runtime_module.NativeManufactureCamoticsRuntime,
            "execute",
            {
                "operation": "camotics",
                "job": {},
                "operations": [],
                "request": {},
            },
            "manufacture.camotics.read_result",
        ),
        (
            gl_runtime_module,
            gl_runtime_module.NativeManufactureSimulationRuntime,
            "simulate",
            {"operation": "gl", "job": {}, "operations": [], "quality": 5},
            "manufacture.simulation.gl",
        ),
        (
            retained_runtime_module,
            retained_runtime_module.NativeManufactureSimulationResultRuntime,
            "simulate",
            {"operation": "native", "job": {}, "operations": [], "quality": 5},
            "manufacture.simulation_result.native",
        ),
    ),
)
def test_each_simulation_runtime_submits_its_governance_lifecycle(
    monkeypatch, module, runtime_type, method, arguments, capability
) -> None:
    document = SimpleNamespace(Uid="document-1")
    captured = {}

    class Governance:
        def submitted(self, job_id, _document_uid, _capability):
            captured["job_id"] = job_id

        def references(self):
            return {
                "analysis_id": captured.get("job_id", ""),
                "workflow_run_id": "workflow-1",
                "workflow_node_id": "evidence",
                "provider_attempt_id": "1",
            }

    governance = Governance()

    class Manager:
        def submit(self, **kwargs):
            captured.update(kwargs)
            kwargs["durable_lifecycle"].submitted(
                "job-1", kwargs["document_uid"], kwargs["capability_name"]
            )
            return SimpleNamespace(
                job_id="job-1",
                capability_name=kwargs["capability_name"],
                phase="preparing",
                progress_percent=1,
                progress_message="Preparing",
            )

    context = NativeRuntimeContext(
        service=object(),
        document=document,
        state=NativeDocumentStateStore(),
        undo_ledger=NativeAssistantUndoLedger(),
        reauthorize_turn=lambda: None,
        active_document=lambda: document,
        active_surface_id=lambda: "manufacture",
        edit_or_task_active=lambda: False,
        authorize_output=lambda _request: None,
        background_manager=Manager(),
        document_thread_dispatch=lambda operation: operation(),
    )
    frozen = _simulation_frozen()
    preflight_name = {
        camotics_runtime_module: "preflight_camotics",
        gl_runtime_module: "preflight_gl_simulation",
        retained_runtime_module: "preflight_native_simulation",
    }[module]
    monkeypatch.setattr(module, preflight_name, lambda *_args, **_kwargs: frozen)
    monkeypatch.setattr(
        module,
        "create_manufacture_evidence_governance",
        lambda *_args, **_kwargs: governance,
    )

    result = getattr(runtime_type(context), method)(
        arguments,
        NativeCallTicket("document-1", capability.split(".")[0], 0, "token-1"),
    )

    assert captured["durable_lifecycle"] is governance
    assert captured["capability_name"] == capability
    assert result["governance"] == governance.references()
