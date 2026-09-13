# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for bounded Manufacture reads."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeBackground import NativeBackgroundError
from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureGeometryRead import (
    cleanup_model_geometry_read,
    preflight_model_geometry_read,
    prepare_model_geometry_read,
    validate_model_geometry_read,
)
from VibeCADNativeManufactureInspect import (
    detect_loop,
    inspect_toolpath,
    list_remaining_stock,
    list_setups,
    read_job,
    search_setup_options,
    validate_job,
)
from VibeCADNativeManufactureThreadCatalog import read_thread_catalog
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket, NativeRevisionConflict


_VARIANTS = {
    "list_setups": frozenset({"query", "offset", "page_size"}),
    "list_remaining_stock": frozenset({"query", "offset", "page_size"}),
    "search_setup_options": frozenset({"category", "query", "offset", "page_size"}),
    "read_job": frozenset({"target", "operation_offset", "page_size"}),
    "validate_job": frozenset({"target"}),
    "inspect_toolpath": frozenset({"target", "offset", "page_size"}),
    "detect_loop": frozenset({"target", "selection"}),
    "read_model_geometry": frozenset({"target", "elements", "offset", "page_size"}),
    "read_thread_catalog": frozenset({"series", "query", "offset", "page_size"}),
}


class NativeManufactureInspectRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def inspect(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket | None = None,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(
            arguments,
            _VARIANTS,
            defaults={
                "list_setups": {"query": "", "offset": 0, "page_size": 32},
                "list_remaining_stock": {
                    "query": "",
                    "offset": 0,
                    "page_size": 32,
                },
                "search_setup_options": {
                    "query": "",
                    "offset": 0,
                    "page_size": 32,
                },
                "read_job": {"operation_offset": 0, "page_size": 32},
                "inspect_toolpath": {"offset": 0, "page_size": 32},
                "read_model_geometry": {"offset": 0, "page_size": 32},
                "read_thread_catalog": {
                    "query": "",
                    "offset": 0,
                    "page_size": 32,
                },
            },
        )
        context = self._context
        context.guard(allow_owned_cam_simulation=True)
        if operation == "read_model_geometry":
            if not isinstance(ticket, NativeCallTicket):
                raise TypeError("CAM geometry inspection requires one exact call ticket")
            current = context.state.current_revision(context.document_uid)
            if current != ticket.expected_revision:
                raise NativeRevisionConflict(ticket.expected_revision, current)
            manager = context.background_manager
            dispatcher = context.document_thread_dispatch
            if manager is None or dispatcher is None:
                raise NativeManufactureError(
                    "Background CAM geometry inspection is unavailable in this session.",
                    error_code="NATIVE_MANUFACTURE_GEOMETRY_UNAVAILABLE",
                )
            frozen = preflight_model_geometry_read(context.document, **values)

            def prepare(cancelled: Any, progress: Any) -> Any:
                return prepare_model_geometry_read(
                    frozen,
                    cancelled=cancelled,
                    progress=progress,
                )

            def validate() -> None:
                context.guard(allow_owned_cam_simulation=True)
                revision = context.state.current_revision(context.document_uid)
                if revision != ticket.expected_revision:
                    raise NativeRevisionConflict(ticket.expected_revision, revision)
                validate_model_geometry_read(context.document, frozen)

            try:
                snapshot = manager.submit(
                    document_uid=context.document_uid,
                    capability_name="manufacture.geometry.read",
                    prepare=prepare,
                    validate_before_commit=validate,
                    commit=lambda prepared: prepared,
                    dispatch_to_document_thread=dispatcher,
                    finalize_message="Validating exact CAM geometry",
                    cleanup=lambda _prepared: cleanup_model_geometry_read(frozen),
                    resource_scope=f"manufacture:model:{frozen.model.Name}",
                )
            except NativeBackgroundError as exc:
                cleanup_model_geometry_read(frozen)
                raise NativeManufactureError(
                    str(exc),
                    error_code="NATIVE_MANUFACTURE_GEOMETRY_UNAVAILABLE",
                ) from exc
            return {
                "job": {
                    "job_id": snapshot.job_id,
                    "capability": snapshot.capability_name,
                    "resource_scope": snapshot.resource_scope,
                    "phase": snapshot.phase,
                    "progress_percent": snapshot.progress_percent,
                    "progress_message": snapshot.progress_message,
                },
                "next": {
                    "tool": "native.job",
                    "operation": "status",
                    "job_id": snapshot.job_id,
                },
            }
        if operation == "list_setups":
            return list_setups(context.document, **values)
        if operation == "list_remaining_stock":
            return list_remaining_stock(context.document, **values)
        if operation == "read_job":
            return read_job(context.document, **values)
        if operation == "search_setup_options":
            return search_setup_options(**values)
        if operation == "validate_job":
            return validate_job(context.document, **values)
        if operation == "inspect_toolpath":
            return inspect_toolpath(context.document, **values)
        if operation == "detect_loop":
            return detect_loop(context.document, **values)
        return read_thread_catalog(**values)
