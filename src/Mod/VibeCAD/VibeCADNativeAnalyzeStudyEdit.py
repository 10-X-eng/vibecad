# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact study-intent edits for one FEM analysis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeState import analysis_state
from VibeCADNativeAnalyzeStudy import configure_study_intent, normalize_study_intent
from VibeCADNativeAnalyzeTargets import (
    PreparedAnalysisTarget,
    analysis_target_still_exact,
    prepare_analysis_target,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


@dataclass(frozen=True, slots=True)
class PreparedStudyUpdate:
    target: PreparedAnalysisTarget
    physics: tuple[str, ...]
    regime: str


def prepare_study_update(
    document: Any,
    document_uid: str,
    *,
    target: Any,
    study: Any,
) -> PreparedStudyUpdate:
    prepared_target = prepare_analysis_target(document, document_uid, target)
    physics, regime = normalize_study_intent(study)
    return PreparedStudyUpdate(prepared_target, physics, regime)


def update_study_intent(
    document: Any,
    prepared: PreparedStudyUpdate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedStudyUpdate):
        raise TypeError("prepared must be a PreparedStudyUpdate")
    if not analysis_target_still_exact(prepared.target):
        raise NativeAnalyzeError(
            "The exact FEM analysis changed after study preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    analysis = prepared.target.analysis
    configure_study_intent(
        analysis,
        {"physics": list(prepared.physics), "regime": prepared.regime},
    )
    return NativeMutationDraft(
        value={"analysis": analysis, "prepared": prepared},
        recompute_targets=(analysis,),
        changed=(object_identity(analysis),),
    )


def verify_study_update(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    analysis = draft.value["analysis"]
    prepared = draft.value["prepared"]
    state = analysis_state(analysis)
    intent = state["study"]
    if (
        tuple(intent.get("physics") or ()) != prepared.physics
        or intent.get("regime") != prepared.regime
    ):
        raise NativeAnalyzeError("The FEM study intent failed its exact postcondition.")
    return {
        "analysis": state,
        "analysis_target": {
            "object_name": state["object_name"],
            "expected_state_sha256": state["state_sha256"],
            "expected_member_count": state["member_count"],
        },
    }
