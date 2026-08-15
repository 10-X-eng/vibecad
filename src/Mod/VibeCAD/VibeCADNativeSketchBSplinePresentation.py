# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared exact result path for B-spline presentation preferences."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchExactState import canonical_sketch_records_sha256
from VibeCADNativeSketchPresentationPreference import (
    SketchPresentationPreference,
    SketchPresentationPreferenceSpec,
    apply_sketch_presentation_preference,
    prepare_sketch_presentation_preference,
)
from VibeCADNativeTargets import object_reference


def prepare_sketch_bspline_presentation(
    document_uid: str,
    values: Mapping[str, Any],
    preference: SketchPresentationPreference,
) -> SketchPresentationPreferenceSpec:
    return prepare_sketch_presentation_preference(document_uid, values, preference)


def set_sketch_bspline_presentation(
    context: NativeRuntimeContext,
    spec: SketchPresentationPreferenceSpec,
    preference: SketchPresentationPreference,
) -> dict[str, Any]:
    if (
        not isinstance(spec, SketchPresentationPreferenceSpec)
        or spec.preference != preference
    ):
        raise TypeError(f"spec must be the exact {preference.label} spec")
    applied = apply_sketch_presentation_preference(context, spec)
    prepared = applied.prepared
    state = prepared.state
    return {
        "operation": preference.operation,
        "sketch": object_reference(prepared.target.sketch),
        "previous_visible": prepared.previous_visible,
        "visible": prepared.spec.visible,
        "changed": applied.changed,
        "internal_b_spline_count": state.internal_b_spline_count,
        "external_b_spline_count": state.external_b_spline_count,
        "geometry_count": prepared.spec.target.expected_geometry_count,
        "constraint_count": prepared.spec.target.expected_constraint_count,
        "external_geometry_count": prepared.spec.expected_external_geometry_count,
        "geometry_state_sha256": canonical_sketch_records_sha256(
            state.geometry_records
        ),
        "constraint_state_sha256": canonical_sketch_records_sha256(
            state.constraint_records
        ),
        "external_geometry_state_sha256": canonical_sketch_records_sha256(
            state.external_geometry_records
        ),
    }
