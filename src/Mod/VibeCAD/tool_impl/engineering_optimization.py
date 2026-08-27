# SPDX-License-Identifier: LGPL-2.1-or-later

"""Read-only discovery of exact G6 optimization runs for one CAD document."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .analysis_contracts import AnalysisContractError, CanonicalJson
from .engineering_experience import project_optimization_run
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
    runs = []
    for record in store.find_by_document_uid(identity):
        definition = optimization_definition_from_value(record["definition"])
        ranking = store.ranking(definition, record["run_id"])
        runs.append(project_optimization_run(record, ranking))
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
