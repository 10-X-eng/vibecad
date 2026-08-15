# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Elmer equation creation."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeEquationCreate import (
    create_equation,
    prepare_equation_create,
    verify_equation_create,
)
from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_KINDS = (
    "elasticity",
    "deformation",
    "electrostatic",
    "electric_force",
    "magnetodynamic",
    "magnetodynamic_2d",
    "static_current",
    "flow",
    "flux",
    "heat",
)
_VARIANTS = {f"create_{kind}": frozenset({"solver", "label"}) for kind in _KINDS}


class NativeAnalyzeEquationRuntime:
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
        prepared = prepare_equation_create(
            context.document,
            context.document_uid,
            kind=kind,
            **values,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name=f"Create Elmer {kind.replace('_', ' ').title()} Equation",
            mutate=lambda document: create_equation(document, prepared),
            verify=verify_equation_create,
        )
