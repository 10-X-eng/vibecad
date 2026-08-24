# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact FEM solver creation."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeSolverCreate import (
    create_solver,
    prepare_solver_create,
    verify_solver_create,
)
from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_VARIANTS = {
    "create_calculix": frozenset({"analysis", "label"}),
    "create_elmer": frozenset({"analysis", "label"}),
    "create_openfoam": frozenset({"analysis", "label", "momentum_model"}),
    "create_mystran": frozenset({"analysis", "label"}),
    "create_z88": frozenset({"analysis", "label"}),
}
_KINDS = {operation: operation.removeprefix("create_") for operation in _VARIANTS}


class NativeAnalyzeSolverRuntime:
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
        request = dict(arguments)
        if request.get("operation") == "create_openfoam":
            request.setdefault("momentum_model", "laminar")
        operation, values = strict_variant_arguments(request, _VARIANTS)
        context = self._context
        context.guard()
        prepared = prepare_solver_create(
            context.document,
            context.document_uid,
            kind=_KINDS[operation],
            **values,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name=f"Create {prepared.kind.title()} FEM Solver",
            mutate=lambda document: create_solver(document, prepared),
            verify=verify_solver_create,
        )
