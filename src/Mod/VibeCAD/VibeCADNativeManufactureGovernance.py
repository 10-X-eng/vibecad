# SPDX-License-Identifier: LGPL-2.1-or-later

"""Durable G2/G5 evidence bridge for the existing Native CAM post owner."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from VibeCADAnalysisPersistence import (
    AnalysisMetadataStore,
    DurableRuntimeLifecycle,
)
from VibeCADAnalysisWorkflow import (
    WorkflowDefinition,
    WorkflowNode,
    WorkflowRunStore,
)
from VibeCADProject import vibecad_data_dir


MANUFACTURE_POST_ADAPTER_ID = "native.manufacture.post"
MANUFACTURE_POST_WORKFLOW_ID = "manufacture-post"
MANUFACTURE_POST_WORKFLOW_VERSION = "1"
MANUFACTURE_POST_NODE_ID = "post"


def _sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def manufacture_post_workflow_definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        workflow_id=MANUFACTURE_POST_WORKFLOW_ID,
        version=MANUFACTURE_POST_WORKFLOW_VERSION,
        nodes=(
            WorkflowNode(
                node_id=MANUFACTURE_POST_NODE_ID,
                domain="manufacture",
                adapter_id=MANUFACTURE_POST_ADAPTER_ID,
                inputs=("exact_cam_job",),
                outputs=("human_authorized_cam_outputs",),
                publication_policy="publish_once",
                resource_class="local_process",
                concurrency_group="document_cam_post",
            ),
        ),
        edges=(),
    )


def manufacture_evidence_identity(
    frozen: Any,
    *,
    adapter_id: str,
    operation: str,
) -> dict[str, str]:
    """Derive path-free identities shared by Native Manufacture evidence tasks."""

    runs = tuple(getattr(frozen, "runs", ()) or ())
    operation_states = [str(run.expected_state_sha256) for run in runs]
    job_state = str(getattr(frozen, "expected_job_state_sha256"))
    prepared = {
        "operation": operation,
        "job_name": str(frozen.job.Name),
        "job_state_sha256": job_state,
        "operation_names": [str(run.operation_name) for run in runs],
        "operation_state_sha256": operation_states,
    }
    settings = {
        "quality": getattr(frozen, "quality", None),
        "resolution": getattr(frozen, "resolution", None),
        "request_kind": getattr(frozen, "request_kind", None),
        "command_count": getattr(
            frozen, "command_count", getattr(frozen, "source_command_count", None)
        ),
    }
    return {
        "prepared_analysis_sha256": _sha256(prepared),
        "dependency_sha256": _sha256({
            "job_state_sha256": job_state,
            "operation_state_sha256": operation_states,
        }),
        "input_manifest_sha256": _sha256({"prepared": prepared, "settings": settings}),
        "execution_spec_sha256": _sha256({
            "adapter_id": adapter_id,
            "operation": operation,
            "settings": settings,
        }),
    }


def manufacture_post_identity(frozen: Any) -> dict[str, str]:
    """Derive bounded durable identities from the already-frozen CAM input."""

    job_state = str(frozen.job_before["state_sha256"])
    operation_states = [
        str(value) for value in frozen.selected_operation_state_sha256
    ]
    prepared = {
        "operation": str(frozen.operation_variant),
        "job_name": str(frozen.job_name),
        "job_state_sha256": job_state,
        "selected_operation_names": list(frozen.selected_operation_names),
        "selected_operation_state_sha256": operation_states,
    }
    dependencies = {
        "snapshot_sha256": str(frozen.snapshot_sha256),
        "job_state_sha256": job_state,
        "postprocessor_sha256": str(frozen.postprocessor_source.sha256),
        "machine_config_sha256": frozen.machine_config_sha256,
        "freecadcmd_sha256": str(frozen.freecadcmd.sha256),
        "child_script_sha256": str(frozen.child_script.sha256),
        "selected_operation_state_sha256": operation_states,
    }
    manifest = {
        "snapshot": {
            "sha256": str(frozen.snapshot_sha256),
            "size_bytes": int(frozen.snapshot_size),
        },
        "postprocessor": {
            "sha256": str(frozen.postprocessor_source.sha256),
            "size_bytes": int(frozen.postprocessor_source.size),
        },
        "machine_config_sha256": frozen.machine_config_sha256,
    }
    execution = {
        "adapter_id": MANUFACTURE_POST_ADAPTER_ID,
        "operation": str(frozen.operation_variant),
        "postprocessor_name": str(frozen.postprocessor_name),
        "machine_name": str(frozen.machine_name),
        "use_machine_flow": bool(frozen.use_machine_flow),
    }
    return {
        "prepared_analysis_sha256": _sha256(prepared),
        "dependency_sha256": _sha256(dependencies),
        "input_manifest_sha256": _sha256(manifest),
        "execution_spec_sha256": _sha256(execution),
    }


class ManufacturePostGovernanceLifecycle:
    """Mirror one existing CAM post job into exact durable G2/G5 records."""

    def __init__(
        self,
        analysis_store: AnalysisMetadataStore,
        workflow_store: WorkflowRunStore,
        *,
        identity: Mapping[str, str],
        adapter_id: str = MANUFACTURE_POST_ADAPTER_ID,
        workflow_id: str = MANUFACTURE_POST_WORKFLOW_ID,
        node_id: str = MANUFACTURE_POST_NODE_ID,
        evidence_kind: str = "post",
    ) -> None:
        self.analysis_store = analysis_store
        self.workflow_store = workflow_store
        self.adapter_id = str(adapter_id)
        self.workflow_id = str(workflow_id)
        self.node_id = str(node_id)
        self.evidence_kind = str(evidence_kind)
        self.workflow_definition = WorkflowDefinition(
            workflow_id=self.workflow_id,
            version="1",
            nodes=(WorkflowNode(
                node_id=self.node_id,
                domain="manufacture",
                adapter_id=self.adapter_id,
                inputs=("exact_cam_job",),
                outputs=(f"{self.evidence_kind}_evidence",),
                publication_policy="publish_once",
                resource_class="local_process",
                concurrency_group=f"document_{self.workflow_id.replace('-', '_')}",
            ),),
            edges=(),
        )
        if self.evidence_kind == "post":
            self.workflow_definition = manufacture_post_workflow_definition()
        self.analysis_id = ""
        self.workflow_run_id = ""
        self.provider_attempt_id = "1"
        self._durable = DurableRuntimeLifecycle(
            analysis_store,
            domain="manufacture",
            adapter_id=self.adapter_id,
            provider_id="native-background",
            provider_kind="local",
            **dict(identity),
        )

    def submitted(self, job_id: str, document_uid: str, capability_name: str) -> None:
        self._durable.submitted(job_id, document_uid, capability_name)
        self.analysis_id = self._durable.analysis_id
        self.workflow_run_id = f"{self.workflow_id}-{self.analysis_id}"
        try:
            self.workflow_store.create(self.workflow_definition, self.workflow_run_id)
            self.workflow_store.start_node(
                self.workflow_run_id,
                self.node_id,
                analysis_id=self.analysis_id,
            )
        except Exception:
            self._durable.failed("workflow_record_initialization_failed")
            raise

    def started(self) -> None:
        self._durable.started()

    def prepared(self) -> None:
        self._durable.prepared()

    def publication_started(self) -> None:
        self._durable.publication_started()

    def record_result(self, result: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(result)
        descriptors = self._artifact_descriptors(value)
        for descriptor in descriptors:
            self.analysis_store.record_artifact(
                self.analysis_id,
                descriptor,
                pinned=True,
            )
        claim_ceiling = (
            "not_proven_toolpath" if self.evidence_kind == "post"
            else "simulation_evidence_only"
        )
        intent = {
            "kind": f"manufacture_{self.evidence_kind}_evidence",
            "artifact_count": len(descriptors),
            "claim_ceiling": claim_ceiling,
        }
        authorization = {
            "kind": "human_requested_native_manufacture_evidence",
            "destination_paths_persisted": False,
        }
        if self.evidence_kind == "post":
            intent = {
                "kind": "human_authorized_cam_output_bundle",
                "output_count": int(value.get("output_count", 0)),
                "total_size_bytes": int(value.get("total_size_bytes", 0)),
                "claim_ceiling": claim_ceiling,
            }
            authorization = {
                "kind": "human_selected_output_destinations",
                "authorized_output_count": int(value.get("output_count", 0)),
                "destination_paths_persisted": False,
            }
        self.analysis_store.record_publication_evidence(
            self.analysis_id,
            intent=intent,
            authorization=authorization,
        )
        value["governance"] = self.references()
        return value

    def _output_name(self) -> str:
        return (
            "human_authorized_cam_outputs"
            if self.evidence_kind == "post"
            else f"{self.evidence_kind}_evidence"
        )

    def _artifact_descriptors(self, value: Mapping[str, Any]) -> list[dict[str, Any]]:
        if self.evidence_kind == "post":
            return [
                {**dict(item), "role": "human_authorized_cam_output"}
                for item in value.get("outputs", [])
            ]
        if self.evidence_kind == "camotics":
            surface = dict(value.get("surface") or {})
            return [{
                "sha256": str(surface.get("sha256") or value.get("program_sha256")),
                "role": "camotics_simulation_evidence",
                "program_sha256": str(value.get("program_sha256") or ""),
            }]
        if self.evidence_kind == "gl_simulation":
            simulation = dict(value.get("simulation") or {})
            return [{
                "sha256": str(simulation.get("program_sha256") or ""),
                "role": "gl_simulation_evidence",
            }]
        if self.evidence_kind == "retained_simulation":
            simulation = dict(value.get("simulation_result") or {})
            state = dict(simulation.get("result") or {})
            return [{
                "sha256": str(state.get("state_sha256") or ""),
                "role": "retained_cam_simulation_result",
                "program_sha256": str(simulation.get("program_sha256") or ""),
            }]
        raise ValueError(f"Unsupported Manufacture evidence kind: {self.evidence_kind}")

    def succeeded(self, result_sha256: str) -> None:
        self._durable.succeeded(result_sha256)
        receipt = self.analysis_store.load(self.analysis_id)["publication"]["receipt"]
        self.workflow_store.finish_node(
            self.workflow_run_id,
            self.node_id,
            state="succeeded",
            publication_receipt_id=str(receipt["publication_id"]),
            outcome={
                "output": self._output_name(),
                "execution_state": "succeeded",
                "currentness": "current",
                "publication_state": "published",
                "claim_ceiling": (
                    "not_proven_toolpath" if self.evidence_kind == "post"
                    else "simulation_evidence_only"
                ),
                "result_sha256": str(result_sha256),
            },
        )

    def failed(self, reason: str) -> None:
        self._durable.failed(reason)
        analysis_state = self.analysis_store.load(self.analysis_id)["state"]
        if analysis_state == "publishing":
            self._finish_unsuccessful("interrupted", "publication_outcome_unknown")
        else:
            self._finish_unsuccessful("failed", reason)

    def cancelled(self) -> None:
        self._durable.cancelled()
        self._finish_unsuccessful("cancelled", "cancelled_before_publication")

    def _finish_unsuccessful(self, state: str, reason: str) -> None:
        record = self.workflow_store.load(self.workflow_run_id)
        node = record["nodes"][self.node_id]
        if node["state"] in {"running", "cancel_requested"}:
            self.workflow_store.finish_node(
                self.workflow_run_id,
                self.node_id,
                state=state,
                outcome={
                    "output": self._output_name(),
                    "execution_state": state,
                    "currentness": "indeterminate",
                    "publication_state": (
                        "outcome_unknown"
                        if reason == "publication_outcome_unknown"
                        else "not_published"
                    ),
                    "reason": str(reason),
                },
            )

    def references(self) -> dict[str, str]:
        return {
            "analysis_id": self.analysis_id,
            "workflow_run_id": self.workflow_run_id,
            "workflow_node_id": self.node_id,
            "provider_attempt_id": self.provider_attempt_id,
        }


def create_manufacture_post_governance(
    frozen: Any,
    *,
    root: str | Path | None = None,
) -> ManufacturePostGovernanceLifecycle:
    selected_root = Path(root) if root is not None else vibecad_data_dir() / "analysis"
    return ManufacturePostGovernanceLifecycle(
        AnalysisMetadataStore(selected_root / "metadata"),
        WorkflowRunStore(selected_root / "workflows"),
        identity=manufacture_post_identity(frozen),
    )


def create_manufacture_evidence_governance(
    frozen: Any,
    *,
    adapter_id: str,
    operation: str,
    evidence_kind: str,
    root: str | Path | None = None,
) -> ManufacturePostGovernanceLifecycle:
    selected_root = Path(root) if root is not None else vibecad_data_dir() / "analysis"
    workflow_id = f"manufacture-{evidence_kind.replace('_', '-')}"
    return ManufacturePostGovernanceLifecycle(
        AnalysisMetadataStore(selected_root / "metadata"),
        WorkflowRunStore(selected_root / "workflows"),
        identity=manufacture_evidence_identity(
            frozen, adapter_id=adapter_id, operation=operation
        ),
        adapter_id=adapter_id,
        workflow_id=workflow_id,
        node_id="evidence",
        evidence_kind=evidence_kind,
    )
