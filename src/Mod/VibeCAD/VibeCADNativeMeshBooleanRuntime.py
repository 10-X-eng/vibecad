# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for retained Mesh booleans."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeMeshBoolean import (
    create_mesh_boolean,
    prepare_mesh_boolean,
    verify_mesh_boolean,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_VARIANTS = {
    "union": frozenset({"first", "second", "result_label"}),
    "intersection": frozenset({"first", "second", "result_label"}),
    "difference": frozenset({"first", "second", "result_label"}),
}


class NativeMeshBooleanRuntime:
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
        self._context.guard()
        prepared = prepare_mesh_boolean(
            self._context.document,
            self._context.document_uid,
            operation,
            values,
        )
        return run_immediate_mutation(
            self._context,
            ticket=ticket,
            transaction_name=f"Mesh {operation.title()}",
            mutate=lambda document: create_mesh_boolean(document, prepared),
            verify=verify_mesh_boolean,
        )
