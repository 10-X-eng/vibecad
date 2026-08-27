# SPDX-License-Identifier: LGPL-2.1-or-later

"""Read-only discovery and projection of durable G2/G5 engineering activity."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .analysis_contracts import AnalysisContractError, CanonicalJson
from .analysis_persistence import (
    AnalysisMetadataStore,
    restart_disposition_for_record,
)
from .analysis_workflow import WorkflowRunStore
from .engineering_experience import project_analysis_activity, project_workflow_run


def discover_engineering_activity(
    document_uid: str,
    *,
    root: str | Path | None = None,
) -> dict[str, Any]:
    """Project durable records for one exact document without lifecycle actions."""

    identity = str(document_uid or "").strip()
    if not identity:
        raise AnalysisContractError("document_uid must be non-empty.")
    if root is None:
        from VibeCADProject import vibecad_data_dir

        selected_root = vibecad_data_dir() / "analysis"
    else:
        selected_root = Path(root)
    analysis_store = AnalysisMetadataStore(selected_root / "metadata")
    workflow_store = WorkflowRunStore(selected_root / "workflows")
    records = analysis_store.find_by_document_uid(identity)
    analyses = tuple(
        project_analysis_activity(
            record,
            restart_disposition=restart_disposition_for_record(record),
        )
        for record in records
    )
    workflows = tuple(
        project_workflow_run(record)
        for record in workflow_store.find_by_analysis_ids(
            [record["analysis_id"] for record in records]
        )
    )
    value = {
        "schema_version": 1,
        "projection_kind": "document_engineering_activity",
        "presentation_only": True,
        "authority": {
            "may_mutate": False,
            "may_execute": False,
            "may_recover": False,
            "may_schedule": False,
            "may_retry": False,
            "may_publish": False,
            "may_export": False,
        },
        "source_document_uid": identity,
        "analysis_count": len(analyses),
        "analyses": analyses,
        "workflow_count": len(workflows),
        "workflows": workflows,
    }
    return CanonicalJson.from_value(value).to_value()


__all__ = ["discover_engineering_activity"]
