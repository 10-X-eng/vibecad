# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for retained Part Join operations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativePartJoin import (
    create_part_join,
    preflight_part_join,
    prepare_part_join,
    verify_part_join,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_OUTER_FIELDS = {
    "connect": frozenset({"label", "definition"}),
    "embed": frozenset({"label", "definition"}),
    "cutout": frozenset({"label", "definition"}),
}


class NativeModelJoinRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def mutate_join(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(arguments, _OUTER_FIELDS)
        label = str(values["label"] or "").strip()
        if not label or len(label) > 160:
            raise NativeModelError("A visible Part Join label must contain 1 to 160 characters.")
        spec = prepare_part_join(
            self._context.document_uid,
            operation,
            values["definition"],
        )
        self._context.guard()
        prepared = preflight_part_join(self._context.document, spec)
        return run_immediate_mutation(
            self._context,
            ticket=ticket,
            transaction_name=f"Create Native Part Join {operation.title()}",
            mutate=lambda document: create_part_join(
                document,
                label=label,
                prepared=prepared,
            ),
            verify=verify_part_join,
        )
