# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact CAM Job creation."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeManufactureJob import (
    JobCreateSpec,
    JobModelInput,
    create_job,
    preflight_job_create,
    verify_created_job,
)
from VibeCADNativeManufactureSetupEdit import (
    prepare_setup_update,
    update_setup_configuration,
    verify_setup_update,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket, NativeRevisionConflict


_CREATE_FIELDS = frozenset(
    {
        "label",
        "models",
        "template",
        "expected_creation_state_sha256",
        "expected_job_count",
    }
)
_UPDATE_FIELDS = frozenset({"target", "changes"})


class NativeManufactureJobRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def mutate_job(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(
            arguments,
            {
                "create_job": _CREATE_FIELDS,
                "update_setup": _UPDATE_FIELDS,
            },
        )
        context = self._context
        context.guard()
        if not isinstance(ticket, NativeCallTicket):
            raise TypeError("ticket must be a NativeCallTicket")
        current = context.state.current_revision(context.document_uid)
        if current != ticket.expected_revision:
            raise NativeRevisionConflict(ticket.expected_revision, current)
        if operation == "update_setup":
            prepared = prepare_setup_update(context.document, **values)
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Edit Native CAM Setup",
                mutate=partial(update_setup_configuration, prepared=prepared),
                verify=verify_setup_update,
            )
        if operation != "create_job":
            raise RuntimeError("The requested CAM Job operation is unavailable.")
        raw_models = values["models"]
        prepared = preflight_job_create(
            context.document,
            JobCreateSpec(
                label=values["label"],
                models=tuple(
                    JobModelInput(
                        target=item["target"],
                        replace_in_history=item["replace_in_history"],
                    )
                    for item in raw_models
                ),
                template=values["template"],
                expected_creation_state_sha256=values[
                    "expected_creation_state_sha256"
                ],
                expected_job_count=values["expected_job_count"],
            ),
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name="Create Native CAM Job",
            mutate=partial(create_job, prepared=prepared),
            verify=verify_created_job,
        )
