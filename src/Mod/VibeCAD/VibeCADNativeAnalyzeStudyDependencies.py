# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact edits for directed FEM study dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeState import analysis_state
from VibeCADNativeAnalyzeStudy import configure_study_dependencies
from VibeCADNativeAnalyzeTargets import (
    PreparedAnalysisTarget,
    analysis_target_still_exact,
    prepare_analysis_target,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


@dataclass(frozen=True, slots=True)
class PreparedStudyDependencyUpdate:
    target: PreparedAnalysisTarget
    dependencies: tuple[PreparedAnalysisTarget, ...]


def prepare_study_dependency_update(
    document: Any,
    document_uid: str,
    *,
    target: Any,
    depends_on: Any,
) -> PreparedStudyDependencyUpdate:
    if isinstance(depends_on, (str, bytes, bytearray)) or not isinstance(
        depends_on, (list, tuple)
    ):
        raise NativeAnalyzeError("depends_on must be an array of exact study targets.")
    if len(depends_on) > 64:
        raise NativeAnalyzeError("depends_on may contain at most 64 study targets.")
    prepared_target = prepare_analysis_target(document, document_uid, target)
    dependencies = tuple(
        prepare_analysis_target(document, document_uid, value)
        for value in depends_on
    )
    if len({id(value.analysis) for value in dependencies}) != len(dependencies):
        raise NativeAnalyzeError("depends_on study targets must be unique.")
    if any(value.analysis is prepared_target.analysis for value in dependencies):
        raise NativeAnalyzeError("A FEM study cannot depend on itself.")
    return PreparedStudyDependencyUpdate(prepared_target, dependencies)


def update_study_dependencies(
    document: Any,
    prepared: PreparedStudyDependencyUpdate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedStudyDependencyUpdate):
        raise TypeError("prepared must be a PreparedStudyDependencyUpdate")
    if not analysis_target_still_exact(prepared.target) or any(
        not analysis_target_still_exact(value) for value in prepared.dependencies
    ):
        raise NativeAnalyzeError(
            "A FEM study changed after dependency preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    analysis = prepared.target.analysis
    configure_study_dependencies(
        analysis,
        [value.analysis for value in prepared.dependencies],
    )
    return NativeMutationDraft(
        value={"analysis": analysis, "prepared": prepared},
        recompute_targets=(analysis,),
        changed=(object_identity(analysis),),
    )


def verify_study_dependency_update(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    analysis = draft.value["analysis"]
    prepared = draft.value["prepared"]
    state = analysis_state(analysis)
    expected = [value.analysis.Name for value in prepared.dependencies]
    if state["dependencies"]["depends_on"] != expected:
        raise NativeAnalyzeError(
            "The FEM study dependencies failed their exact postcondition."
        )
    return {
        "analysis": state,
        "analysis_target": {
            "object_name": state["object_name"],
            "expected_state_sha256": state["state_sha256"],
            "expected_member_count": state["member_count"],
        },
    }
