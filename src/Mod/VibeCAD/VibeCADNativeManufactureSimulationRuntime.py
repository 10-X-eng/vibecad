# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact background GL CAM simulation."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeBackground import NativeBackgroundError
from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureSimulation import (
    close_gl_simulation,
    preflight_gl_simulation,
    prepare_gl_simulation,
    present_gl_simulation,
    validate_gl_simulation,
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
    }


class NativeManufactureSimulationRuntime:
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
            {"gl": frozenset({"job", "operations", "quality"})},
        )
        if operation != "gl" or not isinstance(ticket, NativeCallTicket):
            raise TypeError("GL simulation requires one exact Native call ticket")
        context = self._context
        context.guard()
        current_revision = context.state.current_revision(context.document_uid)
        if current_revision != ticket.expected_revision:
            raise NativeRevisionConflict(ticket.expected_revision, current_revision)
        manager = context.background_manager
        dispatcher = context.document_thread_dispatch
        if manager is None or dispatcher is None:
            raise NativeManufactureError(
                "Background GL simulation is unavailable in this session.",
                error_code="NATIVE_MANUFACTURE_GL_SIMULATION_UNAVAILABLE",
            )
        frozen = preflight_gl_simulation(context.document, **values)

        def prepare(cancelled: Any, progress: Any) -> Any:
            return prepare_gl_simulation(
                frozen,
                cancelled=cancelled,
                progress=progress,
            )

        def validate() -> None:
            context.guard()
            revision = context.state.current_revision(context.document_uid)
            if revision != ticket.expected_revision:
                raise NativeRevisionConflict(ticket.expected_revision, revision)
            validate_gl_simulation(context.document, frozen)

        def present(prepared: Any) -> Mapping[str, Any]:
            revision_before = context.state.current_revision(context.document_uid)
            result = present_gl_simulation(context.document, prepared)
            revision_after = context.state.current_revision(context.document_uid)
            if revision_after != revision_before:
                raise NativeRevisionConflict(revision_before, revision_after)
            return result

        try:
            snapshot = manager.submit(
                document_uid=context.document_uid,
                capability_name="manufacture.simulation.gl",
                prepare=prepare,
                validate_before_commit=validate,
                commit=present,
                dispatch_to_document_thread=dispatcher,
                finalize_message="Opening exact GL simulation",
                resource_scope=f"manufacture:{frozen.job.Name}",
            )
        except NativeBackgroundError as exc:
            raise NativeManufactureError(
                str(exc),
                error_code="NATIVE_MANUFACTURE_GL_SIMULATION_QUEUE_FAILED",
            ) from exc
        return {
            "job": _job_summary(snapshot),
            "next": {
                "tool": "native.job",
                "operation": "status",
                "job_id": str(snapshot.job_id),
            },
        }

    def close(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(
            arguments,
            {"close": frozenset({"simulation_id"})},
        )
        if operation != "close" or not isinstance(ticket, NativeCallTicket):
            raise TypeError("CAM simulation close requires one exact Native call ticket")
        context = self._context
        context.guard(allow_owned_cam_simulation=True)
        authorization = context.state.authorize_mutation(ticket)
        if authorization.duplicate:
            return dict(authorization.prior_verified_result or {})
        context.state.begin_mutation_observation(ticket)
        try:
            revision_before = context.state.current_revision(context.document_uid)
            result = close_gl_simulation(
                context.document,
                str(values["simulation_id"]),
            )
            revision_after = context.state.current_revision(context.document_uid)
            if revision_after != revision_before:
                raise NativeRevisionConflict(revision_before, revision_after)
            return result
        finally:
            context.state.cancel_mutation(ticket)
