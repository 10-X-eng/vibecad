# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact arbitrary regular Polygon creation in the human-opened Sketch."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchRegularPolygon import (
    REGULAR_POLYGON_FIELDS,
    PreparedSketchRegularPolygon,
    SketchRegularPolygonSpec,
    create_sketch_regular_polygon,
    preflight_sketch_regular_polygon,
    prepare_sketch_regular_polygon,
    verify_sketch_regular_polygon,
)


_FIELDS = REGULAR_POLYGON_FIELDS | {"side_count"}


def prepare_sketch_arbitrary_regular_polygon(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchRegularPolygonSpec:
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise NativeSketchError(
            "A Sketch Regular Polygon definition has incorrect fields."
        )
    side_count = value["side_count"]
    if type(side_count) is not int or not 3 <= side_count <= 9_999:
        raise NativeSketchError(
            "Sketch Regular Polygon side_count must be an integer from 3 through 9999."
        )
    return prepare_sketch_regular_polygon(
        document_uid,
        {field: value[field] for field in REGULAR_POLYGON_FIELDS},
        side_count=side_count,
        label="Regular Polygon",
    )


def preflight_sketch_arbitrary_regular_polygon(
    context: NativeRuntimeContext,
    spec: SketchRegularPolygonSpec,
) -> PreparedSketchRegularPolygon:
    return preflight_sketch_regular_polygon(context, spec)


def create_sketch_arbitrary_regular_polygon(
    document: Any,
    prepared: PreparedSketchRegularPolygon,
) -> NativeMutationDraft:
    return create_sketch_regular_polygon(document, prepared)


def verify_sketch_arbitrary_regular_polygon(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    return verify_sketch_regular_polygon(document, draft)
