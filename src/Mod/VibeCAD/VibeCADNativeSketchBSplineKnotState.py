# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared frozen root and helper state for exact B-spline knot mutations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchBSplineHelperState import (
    HelperAlignment,
    alignment_values,
    indexed_records,
    require_safe_existing_helpers,
)
from VibeCADNativeSketchBSplineKnotMultiplicityProof import (
    KnotMultiplicityCurveProof,
    knot_multiplicity_curve_proof,
)
from VibeCADNativeSketchDiagnosticState import require_healthy_external_records
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchMutationState import grouped_geometry_members
from VibeCADNativeSketchTargets import preflight_active_sketch
from VibeCADNativeSketchTransformState import (
    SketchTransformSnapshot,
    frozen_transform_state,
)


@dataclass(frozen=True, slots=True)
class SketchBSplineKnotSnapshot:
    transform: SketchTransformSnapshot
    proof: KnotMultiplicityCurveProof
    helpers: tuple[HelperAlignment, ...]


def capture_bspline_knot_snapshot(
    context: NativeRuntimeContext,
    spec: Any,
    *,
    label: str,
) -> SketchBSplineKnotSnapshot:
    target = preflight_active_sketch(context, spec.target)
    state = frozen_transform_state(
        target.sketch,
        spec.target.expected_geometry_count,
        spec.target.expected_constraint_count,
        label=label,
    )
    if (
        len(state.external_reference_records) != spec.expected_external_reference_count
        or len(state.external_geometry_records) != spec.expected_external_geometry_count
    ):
        raise NativeSketchError(f"{label} external state changed; read it and retry.")
    if any(state.solver_issues):
        raise NativeSketchError(f"{label} requires a Sketch without solver issues.")
    require_healthy_external_records(state.external_geometry_records, label=label)
    geometry = indexed_records(state.geometry_records, "geometry")
    record = geometry.get(spec.geometry_index)
    if (
        record is None
        or spec.geometry_index in grouped_geometry_members(target.sketch, label=label)
        or record.get("kind") != "b_spline"
        or record.get("internal_type")
        or type(record.get("degree")) is not int
    ):
        raise NativeSketchError(f"{label} requires one ungrouped internal B-spline.")
    proof = knot_multiplicity_curve_proof(
        tuple(target.sketch.Geometry)[spec.geometry_index], label=label
    )
    helpers = alignment_values(
        tuple(target.sketch.Constraints),
        geometry,
        state.geometry_tags,
        state.constraint_tags,
        spec.geometry_index,
    )
    if any(
        item.alignment_index
        >= (
            len(proof.control_positions)
            if item.internal_type == "BSplineControlPoint"
            else len(proof.knot_positions)
        )
        for item in helpers
    ):
        raise NativeSketchError(f"{label} found out-of-range helper alignment.")
    require_safe_existing_helpers(state, helpers)
    return SketchBSplineKnotSnapshot(
        SketchTransformSnapshot(target, spec, state, label), proof, helpers
    )
