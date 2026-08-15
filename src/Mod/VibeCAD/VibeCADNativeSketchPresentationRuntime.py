# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Sketch presentation operations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchArcOverlay import (
    prepare_sketch_arc_overlay,
    set_sketch_arc_overlay,
)
from VibeCADNativeSketchBSplineDegreeVisibility import (
    prepare_sketch_bspline_degree_visibility,
    set_sketch_bspline_degree_visibility,
)
from VibeCADNativeSketchBSplineControlPolygonVisibility import (
    prepare_sketch_bspline_control_polygon_visibility,
    set_sketch_bspline_control_polygon_visibility,
)
from VibeCADNativeSketchBSplineCurvatureCombVisibility import (
    prepare_sketch_bspline_curvature_comb_visibility,
    set_sketch_bspline_curvature_comb_visibility,
)
from VibeCADNativeSketchBSplineKnotMultiplicityVisibility import (
    prepare_sketch_bspline_knot_multiplicity_visibility,
    set_sketch_bspline_knot_multiplicity_visibility,
)
from VibeCADNativeSketchBSplinePoleWeightVisibility import (
    prepare_sketch_bspline_pole_weight_visibility,
    set_sketch_bspline_pole_weight_visibility,
)
from VibeCADNativeSketchSectionView import (
    prepare_sketch_section_view,
    set_sketch_section_view,
)
from VibeCADNativeSketchViewAlignment import (
    align_view_to_sketch,
    prepare_sketch_view_alignment,
)


_PRESENTATION_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_geometry_count",
        "expected_visible",
        "visible",
    }
)
_ALIGNMENT_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_geometry_count",
    }
)


class NativeSketchPresentationRuntime:
    """Execute presentation changes in one frozen human-selected Sketch turn."""

    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def present(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        operation, values = strict_variant_arguments(
            arguments,
            {
                "align_view_to_sketch": _ALIGNMENT_FIELDS,
                "section_view": _PRESENTATION_FIELDS,
                "arc_overlay": _PRESENTATION_FIELDS,
                "bspline_degree": _PRESENTATION_FIELDS,
                "bspline_control_polygon": _PRESENTATION_FIELDS,
                "bspline_curvature_comb": _PRESENTATION_FIELDS,
                "bspline_knot_multiplicity": _PRESENTATION_FIELDS,
                "bspline_pole_weight": _PRESENTATION_FIELDS,
            },
        )
        if operation == "align_view_to_sketch":
            spec = prepare_sketch_view_alignment(
                self._context.document_uid,
                values,
            )
            return align_view_to_sketch(self._context, spec)
        if operation == "section_view":
            spec = prepare_sketch_section_view(
                self._context.document_uid,
                values,
            )
            return set_sketch_section_view(self._context, spec)
        if operation == "arc_overlay":
            spec = prepare_sketch_arc_overlay(self._context.document_uid, values)
            return set_sketch_arc_overlay(self._context, spec)
        if operation == "bspline_degree":
            spec = prepare_sketch_bspline_degree_visibility(
                self._context.document_uid,
                values,
            )
            return set_sketch_bspline_degree_visibility(self._context, spec)
        if operation == "bspline_control_polygon":
            spec = prepare_sketch_bspline_control_polygon_visibility(
                self._context.document_uid,
                values,
            )
            return set_sketch_bspline_control_polygon_visibility(
                self._context,
                spec,
            )
        if operation == "bspline_curvature_comb":
            spec = prepare_sketch_bspline_curvature_comb_visibility(
                self._context.document_uid,
                values,
            )
            return set_sketch_bspline_curvature_comb_visibility(
                self._context,
                spec,
            )
        if operation == "bspline_knot_multiplicity":
            spec = prepare_sketch_bspline_knot_multiplicity_visibility(
                self._context.document_uid,
                values,
            )
            return set_sketch_bspline_knot_multiplicity_visibility(
                self._context,
                spec,
            )
        if operation == "bspline_pole_weight":
            spec = prepare_sketch_bspline_pole_weight_visibility(
                self._context.document_uid,
                values,
            )
            return set_sketch_bspline_pole_weight_visibility(
                self._context,
                spec,
            )
        raise RuntimeError(
            f"Sketch presentation operation is unavailable: {operation}."
        )
