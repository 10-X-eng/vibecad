# SPDX-License-Identifier: LGPL-2.1-or-later

"""Vertical Distance binding for the shared Native Sketch axis domain."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchDistanceAxis import (
    VERTICAL_DISTANCE,
    PreparedSketchAxisDistance,
    SketchAxisDistanceSpec,
    create_sketch_axis_distance,
    preflight_sketch_axis_distance,
    prepare_sketch_axis_distance,
    verify_sketch_axis_distance,
)


def prepare_sketch_vertical_distance(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchAxisDistanceSpec:
    return prepare_sketch_axis_distance(document_uid, value, VERTICAL_DISTANCE)


def preflight_sketch_vertical_distance(
    context: NativeRuntimeContext,
    spec: SketchAxisDistanceSpec,
) -> PreparedSketchAxisDistance:
    if (
        not isinstance(spec, SketchAxisDistanceSpec)
        or spec.definition != VERTICAL_DISTANCE
    ):
        raise TypeError("spec must be a vertical Sketch axis distance")
    return preflight_sketch_axis_distance(context, spec)


def create_sketch_vertical_distance(
    document: Any,
    prepared: PreparedSketchAxisDistance,
) -> NativeMutationDraft:
    if (
        not isinstance(prepared, PreparedSketchAxisDistance)
        or prepared.spec.definition != VERTICAL_DISTANCE
    ):
        raise TypeError("prepared must be a vertical Sketch axis distance")
    return create_sketch_axis_distance(document, prepared)


def verify_sketch_vertical_distance(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value.get("prepared") if isinstance(draft.value, dict) else None
    if (
        not isinstance(prepared, PreparedSketchAxisDistance)
        or prepared.spec.definition != VERTICAL_DISTANCE
    ):
        raise TypeError("draft must contain a vertical Sketch axis distance")
    return verify_sketch_axis_distance(document, draft)
