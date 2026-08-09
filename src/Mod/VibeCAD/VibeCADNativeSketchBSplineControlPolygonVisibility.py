# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, non-document-mutating control of B-spline control polygons."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchBSplinePresentation import (
    prepare_sketch_bspline_presentation,
    set_sketch_bspline_presentation,
)
from VibeCADNativeSketchPresentationPreference import (
    SketchPresentationPreference,
    SketchPresentationPreferenceSpec,
)
from VibeCADNativeSketchPresentationState import (
    BSPLINE_CONTROL_POLYGON_PREFERENCE,
)


BSPLINE_CONTROL_POLYGON_VISIBILITY = SketchPresentationPreference(
    operation="bspline_control_polygon",
    key=BSPLINE_CONTROL_POLYGON_PREFERENCE,
    default_visible=True,
    label="B-spline control-polygon visibility",
)


def prepare_sketch_bspline_control_polygon_visibility(
    document_uid: str,
    values: Mapping[str, Any],
) -> SketchPresentationPreferenceSpec:
    return prepare_sketch_bspline_presentation(
        document_uid,
        values,
        BSPLINE_CONTROL_POLYGON_VISIBILITY,
    )


def set_sketch_bspline_control_polygon_visibility(
    context: NativeRuntimeContext,
    spec: SketchPresentationPreferenceSpec,
) -> dict[str, Any]:
    return set_sketch_bspline_presentation(
        context,
        spec,
        BSPLINE_CONTROL_POLYGON_VISIBILITY,
    )
