# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact periodic interpolated B-spline in the human-opened Sketch."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchInterpolatedBSpline import (
    PreparedSketchInterpolatedBSpline,
    SketchInterpolatedBSplineSpec,
    create_sketch_interpolated_bspline,
    preflight_sketch_interpolated_bspline,
    prepare_interpolated_bspline,
    verify_sketch_interpolated_bspline,
)


_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "interpolation_points_mm",
    }
)


def prepare_sketch_periodic_interpolated_bspline(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchInterpolatedBSplineSpec:
    return prepare_interpolated_bspline(
        document_uid,
        value,
        fields=_FIELDS,
        periodic=True,
        label="periodic interpolated B-spline",
    )


def preflight_sketch_periodic_interpolated_bspline(
    context: NativeRuntimeContext,
    spec: SketchInterpolatedBSplineSpec,
) -> PreparedSketchInterpolatedBSpline:
    return preflight_sketch_interpolated_bspline(context, spec)


def create_sketch_periodic_interpolated_bspline(
    document: Any,
    prepared: PreparedSketchInterpolatedBSpline,
) -> NativeMutationDraft:
    return create_sketch_interpolated_bspline(document, prepared)


def verify_sketch_periodic_interpolated_bspline(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    return verify_sketch_interpolated_bspline(document, draft)
