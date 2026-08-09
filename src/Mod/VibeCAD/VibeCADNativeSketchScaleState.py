# SPDX-License-Identifier: LGPL-2.1-or-later

"""Scale-specific echoes over the shared exact Sketch transform state."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchDiagnosticState import ISSUE_FIELDS
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchScaleTarget import LABEL, SketchScaleSpec
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
        "center_mm",
        "scale_factor",
        "keep_originals",
        "allow_origin_constraints",
        "deleted_originals",
        "geometry_tags",
        "constraint_tags",
        "expressions",
        "mutation_receipt",
    }
)

FrozenScaleState = FrozenSketchTransformState
SketchScaleSnapshot = SketchTransformSnapshot
SketchScalePlan = SketchTransformPlan


def capture_scale_snapshot(
    context: NativeRuntimeContext,
    spec: SketchScaleSpec,
) -> SketchScaleSnapshot:
    if not isinstance(spec, SketchScaleSpec):
        raise TypeError("spec must be a SketchScaleSpec")
    return capture_transform_snapshot(context, spec, label=LABEL)


def require_scale_snapshot_unchanged(
    document: Any,
    snapshot: SketchScaleSnapshot,
) -> Any:
    return require_transform_snapshot_unchanged(document, snapshot)


def require_pure_scale_diagnostic(snapshot: SketchScaleSnapshot) -> None:
    require_pure_transform_diagnostic(snapshot)


def parse_scale_diagnostic(
    result: Any,
    snapshot: SketchScaleSnapshot,
) -> SketchScalePlan:
    if not isinstance(result, Mapping) or set(result) != _FIELDS:
        raise NativeSketchError(f"{LABEL} feasibility returned incomplete diagnostics.")
    spec = snapshot.spec
    if (
        tuple(result["input_geometry_indices"])
        if isinstance(result["input_geometry_indices"], (list, tuple))
        else ()
    ) != spec.geometry_indices or (
        not vector_matches(result["center_mm"], spec.center_mm)
        or type(result["scale_factor"]) is not float
        or result["scale_factor"] != spec.scale_factor
        or result["keep_originals"] is not spec.keep_originals
        or result["allow_origin_constraints"] is not False
        or result["deleted_originals"] is not (not spec.keep_originals)
    ):
        raise NativeSketchError(f"{LABEL} feasibility analyzed a different operation.")
    return parse_transform_diagnostic(result, snapshot)


def verify_scale_state(
    document: Any,
    snapshot: SketchScaleSnapshot,
    plan: SketchScalePlan,
    receipt: Any,
) -> tuple[Any, tuple[int, ...], tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
    return verify_transform_state(document, snapshot, plan, receipt)
