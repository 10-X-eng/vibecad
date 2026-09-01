# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for retained background CAM material simulation."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeBackground import NativeBackgroundError
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureSimulationResult import (
    create_native_simulation_result,
    verify_native_simulation_result,
)
from VibeCADNativeManufactureSimulationResultInput import (
    preflight_native_simulation,
    validate_native_simulation,
)
from VibeCADNativeManufactureSimulationResultWorker import (
    execute_native_simulation,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket, NativeRevisionConflict


def _job_summary(snapshot: Any) -> dict[str, Any]:
    return {
        "job_id": str(snapshot.job_id),
        "capability": str(snapshot.capability_name),
        "resource_scope": str(snapshot.resource_scope),
        "phase": str(snapshot.phase),
        "progress_percent": int(snapshot.progress_percent),
        "progress_message": str(snapshot.progress_message),
        "terminal": bool(snapshot.terminal),
    }


class NativeManufactureSimulationResultRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def simulate(
        self,
        arguments: Mapping[str, Any],
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(
            arguments,
            {"native": frozenset({"job", "operations", "quality"})},
        )
        if operation != "native" or not isinstance(ticket, NativeCallTicket):
            raise TypeError("Retained CAM simulation requires one exact Native call ticket")
        context = self._context
        context.guard()
        current_revision = context.state.current_revision(context.document_uid)
        if current_revision != ticket.expected_revision:
            raise NativeRevisionConflict(ticket.expected_revision, current_revision)
        manager = context.background_manager
        dispatcher = context.document_thread_dispatch
        if manager is None or dispatcher is None:
            raise NativeManufactureError(
                "Background retained CAM simulation is unavailable in this session.",
                error_code="NATIVE_MANUFACTURE_SIMULATION_UNAVAILABLE",
            )
        frozen = preflight_native_simulation(context.document, **values)

        def prepare(cancelled: Any, progress: Any) -> Any:
            return execute_native_simulation(
                frozen,
                cancelled=cancelled,
                progress=progress,
            )

        def validate() -> None:
            context.guard()
            revision = context.state.current_revision(context.document_uid)
            if revision != ticket.expected_revision:
                raise NativeRevisionConflict(ticket.expected_revision, revision)
            validate_native_simulation(context.document, frozen)

        def commit(prepared: Any) -> Mapping[str, Any]:
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Create CAM Simulation Result",
                mutate=lambda document: create_native_simulation_result(
                    document,
                    prepared,
                ),
                verify=verify_native_simulation_result,
            )

        try:
            snapshot = manager.submit(
                document_uid=context.document_uid,
                capability_name="manufacture.simulation_result.native",
                prepare=prepare,
                validate_before_commit=validate,
                commit=commit,
                dispatch_to_document_thread=dispatcher,
                finalize_message="Committing retained CAM material result",
                changes_document=True,
                resource_scope=f"manufacture:{frozen.job.Name}",
            )
        except NativeBackgroundError as exc:
            raise NativeManufactureError(
                str(exc),
                error_code="NATIVE_MANUFACTURE_SIMULATION_QUEUE_FAILED",
            ) from exc
        return {
            "job": _job_summary(snapshot),
            "next": {
                "tool": "native.job",
                "operation": "status",
                "job_id": str(snapshot.job_id),
            },
        }
