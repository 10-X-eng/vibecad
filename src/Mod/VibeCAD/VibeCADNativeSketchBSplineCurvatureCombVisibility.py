# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, non-document-mutating control of B-spline curvature combs."""

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
    BSPLINE_CURVATURE_COMB_PREFERENCE,
)


BSPLINE_CURVATURE_COMB_VISIBILITY = SketchPresentationPreference(
    operation="bspline_curvature_comb",
    key=BSPLINE_CURVATURE_COMB_PREFERENCE,
    default_visible=True,
    label="B-spline curvature-comb visibility",
)


def prepare_sketch_bspline_curvature_comb_visibility(
    document_uid: str,
    values: Mapping[str, Any],
) -> SketchPresentationPreferenceSpec:
    return prepare_sketch_bspline_presentation(
        document_uid,
        values,
        BSPLINE_CURVATURE_COMB_VISIBILITY,
    )


def set_sketch_bspline_curvature_comb_visibility(
    context: NativeRuntimeContext,
    spec: SketchPresentationPreferenceSpec,
) -> dict[str, Any]:
    return set_sketch_bspline_presentation(
        context,
        spec,
        BSPLINE_CURVATURE_COMB_VISIBILITY,
    )
