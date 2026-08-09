# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for Model-ribbon standard fasteners."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeDesignResults import verify_design_operation
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeModelFastenerAttachment import (
    attach_model_fastener,
    prepare_model_fastener_attachment,
    verify_model_fastener_attachment,
)
from VibeCADNativeMatchingFastenerHole import (
    create_matching_fastener_hole,
    prepare_matching_fastener_hole,
)
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeModelFastener import (
    create_model_fastener,
    edit_model_fastener,
    preflight_model_fastener_edit,
    prepare_model_fastener,
    verify_model_fastener,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


class NativeModelFastenerRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def mutate_fastener(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(
            arguments,
            {
                "insert_standard_fastener": frozenset(
                    {"label", "definition"}
                ),
                "edit_standard_fastener": frozenset(
                    {"target", "label", "definition"}
                ),
                "create_matching_fastener_hole": frozenset(
                    {"label", "fastener", "profile", "purpose", "fit", "targets"}
                ),
                "attach_standard_fastener": frozenset({"fastener", "host"}),
            },
        )
        self._context.guard()
        if operation == "attach_standard_fastener":
            prepared_attachment = prepare_model_fastener_attachment(
                self._context.document,
                {name: values[name] for name in ("fastener", "host")},
            )
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Attach Native Standard Fastener",
                mutate=partial(
                    attach_model_fastener,
                    prepared=prepared_attachment,
                ),
                verify=verify_model_fastener_attachment,
            )
        label = str(values["label"] or "").strip()
        if not label or len(label) > 160:
            raise NativeModelError(
                "A visible fastener-operation label must contain 1 to 160 characters."
            )
        if operation == "create_matching_fastener_hole":
            prepared_hole = prepare_matching_fastener_hole(
                self._context.document,
                {
                    name: values[name]
                    for name in ("fastener", "profile", "purpose", "fit", "targets")
                },
            )
            transaction_name = "Create Native Matching Fastener Hole"
            mutate = partial(
                create_matching_fastener_hole,
                label=label,
                spec=prepared_hole,
            )
            verify = verify_design_operation
        else:
            prepared = prepare_model_fastener(values["definition"])
        if operation == "edit_standard_fastener":
            target = preflight_model_fastener_edit(
                self._context.document,
                values["target"],
                prepared,
            )
            transaction_name = "Edit Native Standard Fastener"
            mutate = partial(
                edit_model_fastener,
                target=target,
                label=label,
                prepared=prepared,
            )
            verify = verify_model_fastener
        elif operation == "insert_standard_fastener":
            transaction_name = "Insert Native Standard Fastener"
            mutate = partial(
                create_model_fastener,
                label=label,
                prepared=prepared,
            )
            verify = verify_model_fastener
        return run_immediate_mutation(
            self._context,
            ticket=ticket,
            transaction_name=transaction_name,
            mutate=mutate,
            verify=verify,
        )
