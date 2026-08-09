# SPDX-License-Identifier: LGPL-2.1-or-later

"""Explicit Vertical binding for the shared Native Sketch alignment domain."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchAlignment import (
    VERTICAL_ALIGNMENT,
    PreparedSketchAlignment,
    SketchAlignmentSpec,
    create_sketch_alignment,
    preflight_sketch_alignment,
    prepare_sketch_alignment,
    verify_sketch_alignment,
)


def prepare_sketch_vertical(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchAlignmentSpec:
    return prepare_sketch_alignment(document_uid, value, VERTICAL_ALIGNMENT)


def preflight_sketch_vertical(
    context: NativeRuntimeContext,
    spec: SketchAlignmentSpec,
) -> PreparedSketchAlignment:
    if not isinstance(spec, SketchAlignmentSpec) or spec.definition != VERTICAL_ALIGNMENT:
        raise TypeError("spec must be an explicit Vertical Sketch alignment")
    return preflight_sketch_alignment(context, spec)


def create_sketch_vertical(
    document: Any,
    prepared: PreparedSketchAlignment,
) -> NativeMutationDraft:
    if (
        not isinstance(prepared, PreparedSketchAlignment)
        or prepared.spec.definition != VERTICAL_ALIGNMENT
    ):
        raise TypeError("prepared must be an explicit Vertical Sketch alignment")
    return create_sketch_alignment(document, prepared)


def verify_sketch_vertical(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value.get("prepared") if isinstance(draft.value, dict) else None
    if (
        not isinstance(prepared, PreparedSketchAlignment)
        or prepared.spec.definition != VERTICAL_ALIGNMENT
    ):
        raise TypeError("draft must contain an explicit Vertical Sketch alignment")
    return verify_sketch_alignment(document, draft)
