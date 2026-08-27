# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for fail-closed Drawing dimension inference."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeDrawingDimension import mutate_drawing_dimension, verify_drawing_dimension
from VibeCADNativeDrawingDimensionInference import prepare_drawing_dimension_inference
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_FIELDS = {
    "infer": frozenset(
        {"label", "page", "view", "label_position_on_page_mm", "elements"}
    )
}


class NativeDrawingDimensionInferenceRuntime:
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
        operation, values = strict_variant_arguments(arguments, _FIELDS)
        if operation != "infer":
            raise ValueError("operation is not Drawing dimension inference")
        context = self._context
        context.guard()
        prepared = prepare_drawing_dimension_inference(
            context.document,
            values=values,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name="Create Inferred Native Drawing Dimension",
            mutate=partial(mutate_drawing_dimension, prepared=prepared),
            verify=verify_drawing_dimension,
        )
