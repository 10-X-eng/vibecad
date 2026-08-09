# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, non-document-mutating control of B-spline pole weights."""

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
from VibeCADNativeSketchPresentationState import BSPLINE_POLE_WEIGHT_PREFERENCE


BSPLINE_POLE_WEIGHT_VISIBILITY = SketchPresentationPreference(
    operation="bspline_pole_weight",
    key=BSPLINE_POLE_WEIGHT_PREFERENCE,
    default_visible=True,
    label="B-spline pole-weight visibility",
)


def prepare_sketch_bspline_pole_weight_visibility(
    document_uid: str,
    values: Mapping[str, Any],
) -> SketchPresentationPreferenceSpec:
    return prepare_sketch_bspline_presentation(
        document_uid,
        values,
        BSPLINE_POLE_WEIGHT_VISIBILITY,
    )


def set_sketch_bspline_pole_weight_visibility(
    context: NativeRuntimeContext,
    spec: SketchPresentationPreferenceSpec,
) -> dict[str, Any]:
    return set_sketch_bspline_presentation(
        context,
        spec,
        BSPLINE_POLE_WEIGHT_VISIBILITY,
    )
