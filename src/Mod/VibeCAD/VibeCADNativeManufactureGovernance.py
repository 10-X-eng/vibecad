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
    ) -> None:
        self.analysis_store = analysis_store
        self.workflow_store = workflow_store
        self.workflow_definition = manufacture_post_workflow_definition()
        self.analysis_id = ""
        self.workflow_run_id = ""
        self.provider_attempt_id = "1"
        self._durable = DurableRuntimeLifecycle(
            analysis_store,
            domain="manufacture",
            adapter_id=MANUFACTURE_POST_ADAPTER_ID,
            provider_id="native-background",
            provider_kind="local",
            **dict(identity),
        )

    def submitted(self, job_id: str, document_uid: str, capability_name: str) -> None:
        self._durable.submitted(job_id, document_uid, capability_name)
        self.analysis_id = self._durable.analysis_id
        self.workflow_run_id = f"manufacture-post-{self.analysis_id}"
        try:
            self.workflow_store.create(self.workflow_definition, self.workflow_run_id)
            self.workflow_store.start_node(
                self.workflow_run_id,
                MANUFACTURE_POST_NODE_ID,
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
        for descriptor in value.get("outputs", []):
            self.analysis_store.record_artifact(
                self.analysis_id,
                {**dict(descriptor), "role": "human_authorized_cam_output"},
                pinned=True,
            )
        self.analysis_store.record_publication_evidence(
            self.analysis_id,
            intent={
                "kind": "human_authorized_cam_output_bundle",
                "output_count": int(value.get("output_count", 0)),
                "total_size_bytes": int(value.get("total_size_bytes", 0)),
                "claim_ceiling": "not_proven_toolpath",
            },
            authorization={
                "kind": "human_selected_output_destinations",
                "authorized_output_count": int(value.get("output_count", 0)),
                "destination_paths_persisted": False,
            },
        )
        value["governance"] = self.references()
        return value

    def succeeded(self, result_sha256: str) -> None:
        self._durable.succeeded(result_sha256)
        receipt = self.analysis_store.load(self.analysis_id)["publication"]["receipt"]
        self.workflow_store.finish_node(
            self.workflow_run_id,
            MANUFACTURE_POST_NODE_ID,
            state="succeeded",
            publication_receipt_id=str(receipt["publication_id"]),
            outcome={
                "output": "human_authorized_cam_outputs",
                "execution_state": "succeeded",
                "currentness": "current",
                "publication_state": "published",
                "claim_ceiling": "not_proven_toolpath",
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
        node = record["nodes"][MANUFACTURE_POST_NODE_ID]
        if node["state"] in {"running", "cancel_requested"}:
            self.workflow_store.finish_node(
                self.workflow_run_id,
                MANUFACTURE_POST_NODE_ID,
                state=state,
                outcome={
                    "output": "human_authorized_cam_outputs",
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
            "workflow_node_id": MANUFACTURE_POST_NODE_ID,
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
