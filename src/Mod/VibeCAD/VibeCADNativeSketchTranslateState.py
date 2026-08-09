# SPDX-License-Identifier: LGPL-2.1-or-later

"""Translate-specific echoes over the shared exact Sketch transform state."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchDiagnosticState import ISSUE_FIELDS
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchTransformState import (
    FrozenSketchTransformState,
    SketchTransformPlan,
    SketchTransformSnapshot,
    capture_transform_snapshot,
    parse_transform_diagnostic,
    require_pure_transform_diagnostic,
    require_transform_snapshot_unchanged,
    vector_matches,
    verify_transform_state,
)
from VibeCADNativeSketchTranslateTarget import LABEL, SketchTranslateSpec


_FIELDS = frozenset(
    {
        "accepted",
        "degrees_of_freedom",
        "solver_status",
        *ISSUE_FIELDS,
        "geometry_count",
        "constraint_count",
        "geometry",
        "geometry_metadata",
        "constraints",
        "external_reference_count",
        "external_references",
        "external_geometry_count",
        "external_geometry",
        "external_geometry_metadata",
        "input_geometry_indices",
        "first_vector_mm",
        "copy_count",
        "second_vector_mm",
        "row_count",
        "equalize_dimensional_constraints",
        "deleted_originals",
        "geometry_tags",
        "constraint_tags",
        "expressions",
        "mutation_receipt",
    }
)

FrozenTranslateState = FrozenSketchTransformState
SketchTranslateSnapshot = SketchTransformSnapshot
SketchTranslatePlan = SketchTransformPlan


def capture_translate_snapshot(
    context: NativeRuntimeContext,
    spec: SketchTranslateSpec,
) -> SketchTranslateSnapshot:
    if not isinstance(spec, SketchTranslateSpec):
        raise TypeError("spec must be a SketchTranslateSpec")
    return capture_transform_snapshot(context, spec, label=LABEL)


def require_translate_snapshot_unchanged(
    document: Any,
    snapshot: SketchTranslateSnapshot,
) -> Any:
    return require_transform_snapshot_unchanged(document, snapshot)


def require_pure_translate_diagnostic(snapshot: SketchTranslateSnapshot) -> None:
    require_pure_transform_diagnostic(snapshot)


def parse_translate_diagnostic(
    result: Any,
    snapshot: SketchTranslateSnapshot,
) -> SketchTranslatePlan:
    if not isinstance(result, Mapping) or set(result) != _FIELDS:
        raise NativeSketchError(f"{LABEL} feasibility returned incomplete diagnostics.")
    spec = snapshot.spec
    if (
        tuple(result["input_geometry_indices"])
        if isinstance(result["input_geometry_indices"], (list, tuple))
        else ()
    ) != spec.geometry_indices or (
        not vector_matches(result["first_vector_mm"], spec.first_translation_mm)
        or result["copy_count"] != spec.copy_count
        or not vector_matches(result["second_vector_mm"], spec.second_translation_mm)
        or result["row_count"] != spec.row_count
        or result["equalize_dimensional_constraints"]
        is not spec.equalize_dimensional_constraints
        or result["deleted_originals"] is not (spec.copy_count == 0)
    ):
        raise NativeSketchError(f"{LABEL} feasibility analyzed a different operation.")
    return parse_transform_diagnostic(result, snapshot)


def verify_translate_state(
    document: Any,
    snapshot: SketchTranslateSnapshot,
    plan: SketchTranslatePlan,
    receipt: Any,
) -> tuple[Any, tuple[int, ...], tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
    return verify_transform_state(document, snapshot, plan, receipt)
