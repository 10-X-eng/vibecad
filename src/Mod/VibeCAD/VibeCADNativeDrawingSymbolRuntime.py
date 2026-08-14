# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Drawing engineering symbols."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeDrawingSymbol import (
    drawing_weld_catalog_state,
    mutate_drawing_symbol,
    prepare_drawing_symbol,
    verify_drawing_symbol,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_SURFACE_COMMON = frozenset(
    {
        "page", "owner", "placement_on_page_mm", "symbol_type", "method",
        "machining_allowance", "lay", "rotation_degrees", "label",
    }
)
_WELD_COMMON = frozenset(
    {
        "expected_catalog_sha256", "all_around", "field_weld",
        "alternating_weld", "tail_text", "arrow_side", "other_side", "label",
    }
)
_FIELDS = {
    "create_iso_surface_finish": _SURFACE_COMMON | {"roughness"},
    "create_asme_surface_finish": _SURFACE_COMMON
    | {"sampling_length", "minimum_roughness_grade", "maximum_roughness_grade"},
    "create_weld": _WELD_COMMON | {"leader"},
    "edit_weld": _WELD_COMMON | {"symbol"},
    "read_weld_catalog": frozenset(),
}


class NativeDrawingSymbolRuntime:
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
        if operation == "read_weld_catalog":
            return drawing_weld_catalog_state()
        prepared = prepare_drawing_symbol(
            context.document, operation=operation, values=values
        )
        transaction = {
            "create_iso_surface_finish": "Create Native ISO Surface Finish Symbol",
            "create_asme_surface_finish": "Create Native ASME Surface Finish Symbol",
            "create_weld": "Create Native Drawing Weld Symbol",
            "edit_weld": "Edit Native Drawing Weld Symbol",
        }[operation]
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name=transaction,
            mutate=partial(mutate_drawing_symbol, prepared=prepared),
            verify=verify_drawing_symbol,
        )
