# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for standalone Model boolean operations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeDesignCombine import (
    create_design_combine,
    preflight_design_combine,
    prepare_design_combine,
    verify_design_combine,
)
from VibeCADNativeDesignSplit import (
    create_design_split,
    preflight_design_split,
    prepare_design_split,
    verify_design_split,
)
from VibeCADNativePartSection import (
    create_part_section,
    preflight_part_section,
    prepare_part_section,
    verify_part_section,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_OUTER_FIELDS = {
    "section": frozenset({"label", "definition"}),
    "combine": frozenset({"label", "definition"}),
    "split": frozenset({"label", "definition"}),
}


class NativeModelBooleanRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def mutate_boolean(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(arguments, _OUTER_FIELDS)
        label = str(values["label"] or "").strip()
        if not label or len(label) > 160:
            raise NativeModelError("A visible Boolean label must contain 1 to 160 characters.")
        if operation == "section":
            spec = prepare_part_section(
                self._context.document_uid,
                values["definition"],
            )
            self._context.guard()
            prepared = preflight_part_section(self._context.document, spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Part Section",
                mutate=lambda document: create_part_section(
                    document,
                    label=label,
                    prepared=prepared,
                ),
                verify=verify_part_section,
            )
        if operation == "combine":
            spec = prepare_design_combine(
                self._context.document_uid,
                values["definition"],
            )
            self._context.guard()
            prepared = preflight_design_combine(self._context.document, spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Design Combine",
                mutate=lambda document: create_design_combine(
                    document,
                    label=label,
                    prepared=prepared,
                ),
                verify=verify_design_combine,
            )
        if operation == "split":
            spec = prepare_design_split(
                self._context.document_uid,
                values["definition"],
            )
            self._context.guard()
            prepared = preflight_design_split(self._context.document, spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Design Split",
                mutate=lambda document: create_design_split(
                    document,
                    label=label,
                    prepared=prepared,
                ),
                verify=verify_design_split,
            )
        raise NativeModelError("That standalone Boolean operation is unavailable.")
