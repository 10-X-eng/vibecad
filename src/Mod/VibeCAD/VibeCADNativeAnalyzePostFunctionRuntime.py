# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact FEM post functions."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzePostFunctions import (
    create_post_function,
    prepare_post_function,
    verify_post_function,
)
from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_VARIANTS = {
    "create_plane": frozenset({"pipeline", "label", "origin_mm", "normal"}),
    "create_sphere": frozenset({"pipeline", "label", "center_mm", "radius_mm"}),
    "create_cylinder": frozenset(
        {"pipeline", "label", "center_mm", "axis", "radius_mm"}
    ),
    "create_box": frozenset(
        {
            "pipeline",
            "label",
            "center_mm",
            "length_mm",
            "width_mm",
            "height_mm",
        }
    ),
}


class NativeAnalyzePostFunctionRuntime:
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
        operation, values = strict_variant_arguments(arguments, _VARIANTS)
        context = self._context
        context.guard()
        kind = operation.removeprefix("create_")
        prepared = prepare_post_function(
            context.document,
            context.document_uid,
            kind=kind,
            **values,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name=f"Create FEM Post {kind.title()} Function",
            mutate=lambda document: create_post_function(document, prepared),
            verify=verify_post_function,
        )
