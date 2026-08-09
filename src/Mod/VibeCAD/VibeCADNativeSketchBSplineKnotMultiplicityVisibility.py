# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, non-document-mutating control of B-spline knot labels."""

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
    BSPLINE_KNOT_MULTIPLICITY_PREFERENCE,
)


BSPLINE_KNOT_MULTIPLICITY_VISIBILITY = SketchPresentationPreference(
    operation="bspline_knot_multiplicity",
    key=BSPLINE_KNOT_MULTIPLICITY_PREFERENCE,
    default_visible=True,
    label="B-spline knot-multiplicity visibility",
)


def prepare_sketch_bspline_knot_multiplicity_visibility(
    document_uid: str,
    values: Mapping[str, Any],
) -> SketchPresentationPreferenceSpec:
    return prepare_sketch_bspline_presentation(
        document_uid,
        values,
        BSPLINE_KNOT_MULTIPLICITY_VISIBILITY,
    )


def set_sketch_bspline_knot_multiplicity_visibility(
    context: NativeRuntimeContext,
    spec: SketchPresentationPreferenceSpec,
) -> dict[str, Any]:
    return set_sketch_bspline_presentation(
        context,
        spec,
        BSPLINE_KNOT_MULTIPLICITY_VISIBILITY,
    )
