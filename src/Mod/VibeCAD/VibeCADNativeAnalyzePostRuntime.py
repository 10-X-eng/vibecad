# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact FEM post-processing graphs."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzePost import (
    create_post_branch,
    create_post_pipeline,
    create_post_warp,
    prepare_post_branch,
    prepare_post_pipeline,
    prepare_post_warp,
    verify_post_branch,
    verify_post_pipeline,
    verify_post_warp,
)
from VibeCADNativeAnalyzePostCalculator import (
    create_post_calculator,
    prepare_post_calculator,
    verify_post_calculator,
)
from VibeCADNativeAnalyzePostFilters import (
    create_post_contours,
    create_post_implicit_filter,
    create_post_scalar_clip,
    prepare_post_contours,
    prepare_post_implicit_filter,
    prepare_post_scalar_clip,
    verify_post_contours,
    verify_post_implicit_filter,
    verify_post_scalar_clip,
)
from VibeCADNativeAnalyzePostGlyph import (
    create_post_glyph,
    prepare_post_glyph,
    verify_post_glyph,
)
from VibeCADNativeAnalyzePostSampling import (
    create_post_line_sample,
    create_post_point_sample,
    prepare_post_line_sample,
    prepare_post_point_sample,
    verify_post_line_sample,
    verify_post_point_sample,
)
from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_VARIANTS = {
    "create_pipeline": frozenset({"analysis", "result", "label"}),
    "create_branch": frozenset({"source", "label", "mode", "output"}),
    "create_warp": frozenset({"source", "label", "vector_field", "factor"}),
    "create_scalar_clip": frozenset(
        {"source", "label", "scalar_field", "threshold", "inside_out"}
    ),
    "create_cut": frozenset({"source", "function", "label"}),
    "create_region_clip": frozenset(
        {"source", "function", "label", "inside_out", "cut_cells"}
    ),
    "create_contours": frozenset(
        {
            "source",
            "label",
            "field",
            "component",
            "count",
            "color_by_field",
            "smoothing",
            "relaxation",
        }
    ),
    "create_line_sample": frozenset(
        {"source", "label", "field", "component", "start_mm", "end_mm", "resolution"}
    ),
    "create_point_sample": frozenset({"source", "label", "field", "point_mm"}),
    "create_calculated_field": frozenset(
        {
            "source",
            "label",
            "result_field",
            "result_unit",
            "expression",
            "invalid_values",
        }
    ),
    "create_glyphs": frozenset(
        {"source", "label", "glyph", "orientation", "scaling", "sampling"}
    ),
}


class NativeAnalyzePostRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def execute(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        _operation, values = strict_variant_arguments(arguments, _VARIANTS)
        context = self._context
        context.guard()
        if _operation == "create_pipeline":
            prepared = prepare_post_pipeline(
                context.document,
                context.document_uid,
                **values,
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Create FEM Post Pipeline",
                mutate=lambda document: create_post_pipeline(document, prepared),
                verify=verify_post_pipeline,
            )
        elif _operation == "create_branch":
            prepared = prepare_post_branch(
                context.document,
                context.document_uid,
                **values,
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Create FEM Post Branch",
                mutate=lambda document: create_post_branch(document, prepared),
                verify=verify_post_branch,
            )
        elif _operation == "create_warp":
            prepared = prepare_post_warp(
                context.document,
                context.document_uid,
                **values,
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Create FEM Warp Filter",
                mutate=lambda document: create_post_warp(document, prepared),
                verify=verify_post_warp,
            )
        elif _operation == "create_scalar_clip":
            prepared = prepare_post_scalar_clip(
                context.document,
                context.document_uid,
                **values,
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Create FEM Scalar Clip",
                mutate=lambda document: create_post_scalar_clip(document, prepared),
                verify=verify_post_scalar_clip,
            )
        elif _operation in {"create_cut", "create_region_clip"}:
            kind = "cut" if _operation == "create_cut" else "region_clip"
            prepared = prepare_post_implicit_filter(
                context.document,
                context.document_uid,
                kind=kind,
                **values,
            )
            transaction_label = "Cut" if kind == "cut" else "Region Clip"
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name=f"Create FEM {transaction_label} Filter",
                mutate=lambda document: create_post_implicit_filter(document, prepared),
                verify=verify_post_implicit_filter,
            )
        elif _operation == "create_contours":
            prepared = prepare_post_contours(
                context.document,
                context.document_uid,
                **values,
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Create FEM Contours Filter",
                mutate=lambda document: create_post_contours(document, prepared),
                verify=verify_post_contours,
            )
        elif _operation == "create_line_sample":
            prepared = prepare_post_line_sample(
                context.document,
                context.document_uid,
                **values,
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Create FEM Line Sample",
                mutate=lambda document: create_post_line_sample(document, prepared),
                verify=verify_post_line_sample,
            )
        elif _operation == "create_point_sample":
            prepared = prepare_post_point_sample(
                context.document,
                context.document_uid,
                **values,
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Create FEM Point Sample",
                mutate=lambda document: create_post_point_sample(document, prepared),
                verify=verify_post_point_sample,
            )
        elif _operation == "create_calculated_field":
            prepared = prepare_post_calculator(
                context.document,
                context.document_uid,
                **values,
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Create FEM Calculated Field",
                mutate=lambda document: create_post_calculator(document, prepared),
                verify=verify_post_calculator,
            )
        elif _operation == "create_glyphs":
            prepared = prepare_post_glyph(
                context.document,
                context.document_uid,
                **values,
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Create FEM Glyph Filter",
                mutate=lambda document: create_post_glyph(document, prepared),
                verify=verify_post_glyph,
            )
        raise AssertionError(f"Unhandled Analyze post operation: {_operation}")
