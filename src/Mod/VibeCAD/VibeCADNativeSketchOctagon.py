# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact regular Octagon creation in the human-opened Sketch."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchRegularPolygon import (
    PreparedSketchRegularPolygon,
    SketchRegularPolygonSpec,
    create_sketch_regular_polygon,
    preflight_sketch_regular_polygon,
    prepare_sketch_regular_polygon,
    verify_sketch_regular_polygon,
)


def prepare_sketch_octagon(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchRegularPolygonSpec:
    return prepare_sketch_regular_polygon(
        document_uid,
        value,
        side_count=8,
        label="Octagon",
    )


def preflight_sketch_octagon(
    context: NativeRuntimeContext,
    spec: SketchRegularPolygonSpec,
) -> PreparedSketchRegularPolygon:
    return preflight_sketch_regular_polygon(context, spec)


def create_sketch_octagon(
    document: Any,
    prepared: PreparedSketchRegularPolygon,
) -> NativeMutationDraft:
    return create_sketch_regular_polygon(document, prepared)


def verify_sketch_octagon(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    return verify_sketch_regular_polygon(document, draft)
