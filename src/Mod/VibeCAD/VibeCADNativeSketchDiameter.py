# SPDX-License-Identifier: LGPL-2.1-or-later

"""Deterministic Diameter constraint for an open Sketch."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchCircularSize import (
    DIAMETER_MODE,
    PreparedSketchCircularSize,
    SketchCircularSizeSpec,
    create_sketch_circular_size,
    preflight_sketch_circular_size,
    prepare_sketch_circular_size,
    verify_sketch_circular_size,
)


SketchDiameterSpec = SketchCircularSizeSpec
PreparedSketchDiameter = PreparedSketchCircularSize


def prepare_sketch_diameter(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchCircularSizeSpec:
    return prepare_sketch_circular_size(document_uid, value, mode=DIAMETER_MODE)


def preflight_sketch_diameter(
    context: NativeRuntimeContext,
    spec: SketchCircularSizeSpec,
) -> PreparedSketchCircularSize:
    return preflight_sketch_circular_size(context, spec)


def create_sketch_diameter(
    document: Any,
    prepared: PreparedSketchCircularSize,
) -> NativeMutationDraft:
    return create_sketch_circular_size(document, prepared)


def verify_sketch_diameter(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    return verify_sketch_circular_size(document, draft)
