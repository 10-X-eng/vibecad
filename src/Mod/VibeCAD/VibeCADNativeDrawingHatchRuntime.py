# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Drawing hatch operations."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingHatch import (
    drawing_geometric_hatch_input_request,
    drawing_hatch_defaults_state,
    drawing_image_hatch_input_request,
    mutate_drawing_hatch,
    prepare_drawing_hatch,
    verify_drawing_hatch,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeInput import NativeInputError
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_COMMON_FIELDS = frozenset({"page", "view", "faces", "label", "style"})
_FIELDS = {
    "create_image_default": _COMMON_FIELDS,
    "create_image_file": _COMMON_FIELDS,
    "create_geometric_default": _COMMON_FIELDS | {"pattern_name"},
    "create_geometric_file": _COMMON_FIELDS | {"pattern_name"},
    "read_defaults": frozenset(),
}


class NativeDrawingHatchRuntime:
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
        context = self._context
        context.guard()
        if operation == "read_defaults":
            return drawing_hatch_defaults_state()
        authorization = None
        input_request = None
        if operation.endswith("_file"):
            authorizer = context.authorize_input
            if authorizer is None:
                raise NativeDrawingError(
                    "Human Drawing hatch pattern authorization is unavailable in this session.",
                    error_code="NATIVE_DRAWING_HATCH_INPUT_UNAVAILABLE",
                )
            input_request = (
                drawing_image_hatch_input_request()
                if operation.startswith("create_image")
                else drawing_geometric_hatch_input_request()
            )
            try:
                authorization = authorizer(input_request)
            except NativeInputError as exc:
                raise NativeDrawingError(str(exc), error_code=exc.code) from exc
            if authorization is None:
                raise NativeDrawingError(
                    "The human cancelled Drawing hatch pattern selection.",
                    error_code="NATIVE_DRAWING_HATCH_INPUT_CANCELLED",
                )
        prepared = prepare_drawing_hatch(
            context.document,
            operation=operation,
            values=values,
            authorization=authorization,
            input_request=input_request,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name=(
                "Create Native Drawing Image Hatch"
                if operation.startswith("create_image")
                else "Create Native Drawing Geometric Hatch"
            ),
            mutate=partial(mutate_drawing_hatch, prepared=prepared),
            verify=verify_drawing_hatch,
        )
