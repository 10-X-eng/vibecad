# SPDX-License-Identifier: LGPL-2.1-or-later

"""Read-only discovery of exact G6 optimization runs for one CAD document."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .analysis_contracts import AnalysisContractError, CanonicalJson
from .analysis_workflow import WorkflowRunStore
from .engineering_experience import project_optimization_run, project_workflow_run
from .governed_optimization import (
    OptimizationRunStore,
    optimization_definition_from_value,
)


def discover_engineering_optimization(
    document_uid: str,
    *,
    root: str | Path | None = None,
) -> dict[str, Any]:
    """Project owner-ranked optimization runs without ranking in the view."""

    identity = str(document_uid or "").strip()
    if not identity:
        raise AnalysisContractError("document_uid must be non-empty.")
    if root is None:
        from VibeCADProject import vibecad_data_dir

        selected_root = vibecad_data_dir() / "analysis"
    else:
        selected_root = Path(root)
    store = OptimizationRunStore(selected_root / "optimization")
    workflow_store = WorkflowRunStore(selected_root / "workflows")
    records = store.find_by_document_uid(identity)
    referenced_workflow_ids = sorted({
        workflow_run_id
        for record in records
        for candidate in record["candidates"].values()
        for workflow_run_id in candidate.get("workflow_run_ids") or []
    })
    resolved_workflows, _missing_workflows = workflow_store.find_by_run_ids(
        referenced_workflow_ids
    )
    workflow_records = {record["run_id"]: record for record in resolved_workflows}
    runs = []
    for record in records:
        definition = optimization_definition_from_value(record["definition"])
        ranking = store.ranking(definition, record["run_id"])
        projected = project_optimization_run(record, ranking)
        for candidate in projected["candidates"]:
            active_id = candidate.get("workflow_run_id")
            provenance = []
            for workflow_run_id in candidate["workflow_run_ids"]:
                workflow_record = workflow_records.get(workflow_run_id)
                if workflow_record is None:
                    provenance.append({
                        "run_id": workflow_run_id,
                        "active": workflow_run_id == active_id,
                        "resolved": False,
                        "workflow": None,
                    })
                else:
                    provenance.append({
                        "run_id": workflow_run_id,
                        "active": workflow_run_id == active_id,
                        "resolved": True,
                        "workflow": project_workflow_run(workflow_record),
                    })
            candidate["workflow_provenance"] = provenance
            candidate["unresolved_workflow_run_ids"] = [
                item["run_id"] for item in provenance if not item["resolved"]
            ]
        runs.append(projected)
    value = {
        "schema_version": 1,
        "projection_kind": "document_engineering_optimization",
        "presentation_only": True,
        "authority": {
            "may_rank": False,
            "may_select": False,
            "may_mutate": False,
            "may_execute": False,
            "may_publish": False,
            "may_export": False,
        },
        "source_document_uid": identity,
        "run_count": len(runs),
        "runs": runs,
    }
    return CanonicalJson.from_value(value).to_value()


__all__ = ["discover_engineering_optimization"]
