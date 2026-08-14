# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for Native Mesh input and regular solids."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeBackground import NativeBackgroundError
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeInput import NativeInputError
from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshImport import (
    commit_mesh_import,
    mesh_import_input_request,
    prepare_mesh_import,
    verify_mesh_import,
)
from VibeCADNativeMeshRegularSolid import create_regular_solid, verify_regular_solid
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


def _label(value: Any) -> str:
    result = str(value or "").strip()
    if not result or len(result) > 160:
        raise NativeMeshError("label must contain 1 to 160 visible characters.")
    return result


def _job_summary(snapshot: Any) -> dict[str, Any]:
    return {
        "job_id": str(snapshot.job_id),
        "capability": str(snapshot.capability_name),
        "phase": str(snapshot.phase),
        "progress_percent": int(snapshot.progress_percent),
        "progress_message": str(snapshot.progress_message),
        "terminal": bool(snapshot.terminal),
    }


class NativeMeshIORuntime:
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
        operation, values = strict_variant_arguments(
            arguments,
            {
                "import_mesh": frozenset(),
                "regular_solid": frozenset({"label", "placement", "solid"}),
            },
        )
        if operation == "regular_solid":
            solid = values["solid"]
            placement = values["placement"]
            if not isinstance(solid, Mapping) or not isinstance(placement, Mapping):
                raise NativeMeshError(
                    "regular_solid requires exact solid and placement definitions."
                )
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Mesh Solid",
                mutate=lambda document: create_regular_solid(
                    document,
                    label=_label(values["label"]),
                    placement=placement,
                    solid=solid,
                ),
                verify=verify_regular_solid,
            )
        return self._start_import(ticket)

    def _start_import(self, ticket: NativeCallTicket) -> dict[str, Any]:
        self._context.guard()
        authorizer = self._context.authorize_input
        if authorizer is None:
            raise NativeMeshError(
                "Human Mesh input authorization is unavailable in this session.",
                error_code="NATIVE_MESH_IMPORT_UNAVAILABLE",
            )
        manager = self._context.background_manager
        dispatcher = self._context.document_thread_dispatch
        if manager is None or dispatcher is None:
            raise NativeMeshError(
                "Background Mesh import is unavailable in this session.",
                error_code="NATIVE_MESH_IMPORT_UNAVAILABLE",
            )
        request = mesh_import_input_request()
        try:
            authorization = authorizer(request)
        except NativeInputError as exc:
            raise NativeMeshError(str(exc), error_code=exc.code) from exc
        if authorization is None:
            raise NativeMeshError(
                "The human cancelled Mesh input authorization.",
                error_code="NATIVE_MESH_IMPORT_CANCELLED",
            )

        def prepare(cancelled: Any, progress: Any) -> Any:
            return prepare_mesh_import(
                authorization,
                request,
                cancelled=cancelled,
                progress=progress,
            )

        def commit(prepared: Any) -> Mapping[str, Any]:
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Import Native Mesh",
                mutate=lambda document: commit_mesh_import(document, prepared),
                verify=verify_mesh_import,
            )

        try:
            snapshot = manager.submit(
                document_uid=self._context.document_uid,
                capability_name="mesh.io.import_mesh",
                prepare=prepare,
                validate_before_commit=self._context.guard,
                commit=commit,
                dispatch_to_document_thread=dispatcher,
            )
        except NativeBackgroundError as exc:
            raise NativeMeshError(
                str(exc),
                error_code="NATIVE_MESH_IMPORT_QUEUE_FAILED",
            ) from exc
        return {
            "job": _job_summary(snapshot),
            "next": {
                "tool": "native.job",
                "operation": "status",
                "job_id": snapshot.job_id,
            },
        }
