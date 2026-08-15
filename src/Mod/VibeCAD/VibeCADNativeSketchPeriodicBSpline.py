# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact periodic control-point B-spline in the human-opened Sketch."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchControlBSpline import (
    PreparedSketchControlBSpline,
    SketchControlBSplineSpec,
    create_control_bspline,
    preflight_control_bspline,
    prepare_control_bspline,
    verify_control_bspline,
)


_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "control_points_mm",
        "degree",
    }
)


def prepare_sketch_periodic_bspline(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchControlBSplineSpec:
    return prepare_control_bspline(
        document_uid,
        value,
        fields=_FIELDS,
        periodic=True,
        label="periodic B-spline",
    )


def preflight_sketch_periodic_bspline(
    context: NativeRuntimeContext,
    spec: SketchControlBSplineSpec,
) -> PreparedSketchControlBSpline:
    return preflight_control_bspline(context, spec)


def create_sketch_periodic_bspline(
    document: Any,
    prepared: PreparedSketchControlBSpline,
) -> NativeMutationDraft:
    return create_control_bspline(document, prepared)


def verify_sketch_periodic_bspline(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    return verify_control_bspline(document, draft)
