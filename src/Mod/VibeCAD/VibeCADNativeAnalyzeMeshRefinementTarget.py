# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact targets for FEM mesh refinement resources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeMeshRefinementState import (
    mesh_refinement_mode,
    mesh_refinement_state,
    mesh_refinement_still_exact,
)
from VibeCADNativeTargets import NativeObjectRef, resolve_object


@dataclass(frozen=True, slots=True)
class PreparedMeshRefinementTarget:
    refinement: Any
    mode: str
    expected_state_sha256: str


def prepare_mesh_refinement_target(
    document: Any,
    document_uid: str,
    value: Any,
    *,
    expected_mode: str | None = None,
) -> PreparedMeshRefinementTarget:
    required = {"object_name", "expected_state_sha256"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise NativeAnalyzeError(
            "refinement target must contain only object_name and expected_state_sha256."
        )
    refinement = resolve_object(
        document,
        NativeObjectRef(document_uid, str(value["object_name"])),
    )
    mode = mesh_refinement_mode(refinement)
    if expected_mode is not None and mode != expected_mode:
        raise NativeAnalyzeError(
            f"The exact target is {mode}; this operation requires {expected_mode}.",
            error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
        )
    state = mesh_refinement_state(refinement)
    expected_sha = str(value["expected_state_sha256"])
    if state["state_sha256"] != expected_sha:
        raise NativeAnalyzeError(
            "The exact FEM mesh refinement changed after the provider read it.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
            repair={
                "mesh_refinement": {"object_name": str(refinement.Name)},
                "current_state_sha256": state["state_sha256"],
                "refinement_mode": mode,
            },
        )
    return PreparedMeshRefinementTarget(refinement, mode, expected_sha)


def mesh_refinement_target_still_exact(
    target: PreparedMeshRefinementTarget,
) -> bool:
    return mesh_refinement_still_exact(
        target.refinement,
        target.expected_state_sha256,
    )
