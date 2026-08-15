# SPDX-License-Identifier: LGPL-2.1-or-later

"""Offset-specific echoes over the shared exact Sketch transform state."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchDiagnosticState import ISSUE_FIELDS
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchOffsetTarget import LABEL, SketchOffsetSpec
from VibeCADNativeSketchTransformState import (
    FrozenSketchTransformState,
    SketchTransformPlan,
    SketchTransformSnapshot,
    capture_transform_snapshot,
    parse_transform_diagnostic,
    require_pure_transform_diagnostic,
    require_transform_snapshot_unchanged,
    verify_transform_state,
)


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
        "offset_length_mm",
        "join_type",
        "source_mode",
        "deleted_originals",
        "constrained_offset",
        "geometry_tags",
        "constraint_tags",
        "expressions",
        "mutation_receipt",
    }
)

FrozenOffsetState = FrozenSketchTransformState
SketchOffsetSnapshot = SketchTransformSnapshot
SketchOffsetPlan = SketchTransformPlan


def capture_offset_snapshot(
    context: NativeRuntimeContext,
    spec: SketchOffsetSpec,
) -> SketchOffsetSnapshot:
    if not isinstance(spec, SketchOffsetSpec):
        raise TypeError("spec must be a SketchOffsetSpec")
    return capture_transform_snapshot(context, spec, label=LABEL)


def require_offset_snapshot_unchanged(
    document: Any,
    snapshot: SketchOffsetSnapshot,
) -> Any:
    return require_transform_snapshot_unchanged(document, snapshot)


def require_pure_offset_diagnostic(snapshot: SketchOffsetSnapshot) -> None:
    require_pure_transform_diagnostic(snapshot)


def parse_offset_diagnostic(
    result: Any,
    snapshot: SketchOffsetSnapshot,
) -> SketchOffsetPlan:
    if not isinstance(result, Mapping) or set(result) != _FIELDS:
        raise NativeSketchError(f"{LABEL} feasibility returned incomplete diagnostics.")
    spec = snapshot.spec
    indices = (
        tuple(result["input_geometry_indices"])
        if isinstance(result["input_geometry_indices"], (list, tuple))
        else ()
    )
    if (
        indices != spec.geometry_indices
        or type(result["offset_length_mm"]) is not float
        or result["offset_length_mm"] != spec.offset_length_mm
        or result["join_type"] != spec.join_type
        or result["source_mode"] != spec.source_mode
        or type(result["deleted_originals"]) is not bool
        or result["deleted_originals"] is not (spec.source_mode == "delete")
        or type(result["constrained_offset"]) is not bool
        or result["constrained_offset"] is not (spec.source_mode == "constrain")
    ):
        raise NativeSketchError(f"{LABEL} feasibility analyzed a different operation.")
    return parse_transform_diagnostic(result, snapshot)


def verify_offset_state(
    document: Any,
    snapshot: SketchOffsetSnapshot,
    plan: SketchOffsetPlan,
    receipt: Any,
) -> tuple[Any, tuple[int, ...], tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
    return verify_transform_state(document, snapshot, plan, receipt)
