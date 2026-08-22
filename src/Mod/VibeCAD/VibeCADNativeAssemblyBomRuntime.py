# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for creating an Assembly bill of materials."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeAssemblyBom import (
    AssemblyBomCreateSpec,
    DEFAULT_BOM_COLUMNS,
    NativeAssemblyBomError,
    create_assembly_bom,
    preflight_create_assembly_bom,
    verify_created_assembly_bom,
)
from VibeCADNativeAssemblyState import read_active_assembly
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket
from VibeCADNativeTargets import NativeObjectRef


def _bom_columns(value: Any) -> tuple[Any, ...]:
    if not isinstance(value, (list, tuple)):
        raise NativeAssemblyBomError("columns must be one ordered list.")
    return tuple(value)


class NativeAssemblyBomRuntime:
    """Create a BOM only for the exact human-active Assembly."""

    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def create(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        normalized = dict(arguments)
        normalized.setdefault("columns", list(DEFAULT_BOM_COLUMNS))
        normalized.setdefault("label", "Bill of Materials")
        normalized.setdefault("detail_subassemblies", True)
        normalized.setdefault("detail_parts", True)
        normalized.setdefault("only_parts", False)
        _operation, values = strict_variant_arguments(
            normalized,
            {
                "create": frozenset(
                    {
                        "columns",
                        "label",
                        "detail_subassemblies",
                        "detail_parts",
                        "only_parts",
                    }
                )
            },
        )
        self._context.guard()
        assembly = read_active_assembly(self._context.document)
        if assembly is None:
            raise NativeAssemblyBomError("No Assembly is active.")
        spec = AssemblyBomCreateSpec(
            assembly_ref=NativeObjectRef(
                self._context.document_uid,
                str(assembly.Name),
            ),
            columns=_bom_columns(values["columns"]),
            label=values["label"],
            detail_subassemblies=values["detail_subassemblies"],
            detail_parts=values["detail_parts"],
            only_parts=values["only_parts"],
        )
        preflight_create_assembly_bom(self._context.document, spec)
        return run_immediate_mutation(
            self._context,
            ticket=ticket,
            transaction_name="Create Native Assembly BOM",
            mutate=lambda document: create_assembly_bom(document, spec),
            verify=verify_created_assembly_bom,
        )
