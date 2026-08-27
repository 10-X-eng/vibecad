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
from VibeCADNativeManufactureJobState import capture_job_creation_environment
from VibeCADNativeManufactureState import is_job
from VibeCADNativeManufactureSetupEdit import (
    prepare_setup_update,
    update_setup_configuration,
    verify_setup_update,
)
from VibeCADNativeManufactureStockEdit import (
    configure_stock,
    prepare_stock_configuration,
    verify_stock_configuration,
)
from VibeCADNativeManufactureWorkCoordinateEdit import (
    orient_workpiece,
    prepare_workpiece_orientation,
    verify_workpiece_orientation,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket, NativeRevisionConflict


_CREATE_FIELDS = frozenset({"label", "models"})
_CREATE_TEMPLATE_FIELDS = frozenset({"label", "models", "template"})
_UPDATE_FIELDS = frozenset({"target", "changes"})
_STOCK_FIELDS = frozenset({"target", "stock"})
_WORKPIECE_FIELDS = frozenset({"target", "frame", "include_stock"})


class NativeManufactureJobRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context
        self._creation_state_sha256 = capture_job_creation_environment().state_sha256

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
                "create_job_from_template": _CREATE_TEMPLATE_FIELDS,
                "configure_stock": _STOCK_FIELDS,
                "orient_workpiece": _WORKPIECE_FIELDS,
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
        if operation == "configure_stock":
            prepared = prepare_stock_configuration(context.document, **values)
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Configure Native CAM Stock",
                mutate=partial(configure_stock, prepared=prepared),
                verify=verify_stock_configuration,
            )
        if operation == "orient_workpiece":
            prepared = prepare_workpiece_orientation(context.document, **values)
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Orient Native CAM Workpiece",
                mutate=partial(orient_workpiece, prepared=prepared),
                verify=verify_workpiece_orientation,
            )
        if operation not in {"create_job", "create_job_from_template"}:
            raise RuntimeError("The requested CAM Job operation is unavailable.")
        raw_models = values["models"]
        prepared = preflight_job_create(
            context.document,
            JobCreateSpec(
                label=values["label"],
                models=tuple(
                    JobModelInput(
                        target={
                            "object_name": item["object_name"],
                            "expected_state_sha256": item[
                                "expected_state_sha256"
                            ],
                        },
                        replace_in_history=item["replace_in_history"],
                    )
                    for item in raw_models
                ),
                template=(
                    values["template"]
                    if operation == "create_job_from_template"
                    else {"kind": "none"}
                ),
                expected_creation_state_sha256=self._creation_state_sha256,
                expected_job_count=sum(
                    1
                    for obj in tuple(context.document.Objects)
                    if is_job(obj)
                ),
            ),
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name="Create Native CAM Job",
            mutate=partial(create_job, prepared=prepared),
            verify=verify_created_job,
        )
