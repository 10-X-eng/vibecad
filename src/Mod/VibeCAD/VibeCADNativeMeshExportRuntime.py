# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound, background Mesh export runtime."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeBackground import NativeBackgroundError
from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshExport import (
    MESH_EXPORT_FORMAT_SUFFIXES,
    capture_mesh_export,
    mesh_export_request,
    mesh_export_source_still_exact,
    prepare_mesh_export,
    provider_mesh_export_format,
)
from VibeCADNativePointIO import (
    POINT_OUTPUT_SUFFIXES,
    point_output_request,
    publish_point_export,
)
from VibeCADNativePointTargets import point_target_still_exact, prepare_point_target
from VibeCADNativeOutput import NativeOutputError
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket
from VibeCADNativeTargets import NativeObjectRef, resolve_object


_FORMAT_SUFFIX = MESH_EXPORT_FORMAT_SUFFIXES


def _job_summary(snapshot: Any) -> dict[str, Any]:
    return {
        "job_id": str(snapshot.job_id),
        "capability": str(snapshot.capability_name),
        "phase": str(snapshot.phase),
        "progress_percent": int(snapshot.progress_percent),
        "progress_message": str(snapshot.progress_message),
        "terminal": bool(snapshot.terminal),
    }


class NativeMeshExportRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def export(
        self,
        arguments: Mapping[str, Any],
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(
            arguments,
            {
                "export_mesh": frozenset(
                    {"target", "format"}
                ),
                "export_point_cloud": frozenset(
                    {
                        "target",
                        "expected_state_sha256",
                        "expected_point_count",
                        "format",
                    }
                ),
            },
        )
        if not isinstance(ticket, NativeCallTicket):
            raise TypeError("ticket must be a NativeCallTicket")
        if operation == "export_point_cloud":
            return self._export_point_cloud(values)
        context = self._context
        context.guard()
        manager = context.background_manager
        dispatcher = context.document_thread_dispatch
        authorizer = context.authorize_output
        if manager is None or dispatcher is None or authorizer is None:
            raise NativeMeshError(
                "Background human-authorized Mesh export is unavailable in this session.",
                error_code="NATIVE_MESH_EXPORT_UNAVAILABLE",
            )
        target = values["target"]
        if not isinstance(target, Mapping) or set(target) != {
            "object_name",
            "expected_state_sha256",
        }:
            raise NativeMeshError(
                "target must contain one object_name and expected_state_sha256."
            )
        reference = NativeObjectRef(context.document_uid, str(target["object_name"]))
        obj = resolve_object(context.document, reference, expected_types=("Mesh::Feature",))
        format_value = provider_mesh_export_format(values["format"])
        captured = capture_mesh_export(
            context.document,
            obj,
            expected_state_sha256=str(target["expected_state_sha256"]),
            format_value=format_value,
        )
        request = mesh_export_request(captured.label, format_value)
        try:
            authorization = authorizer(request)
        except NativeOutputError as exc:
            raise NativeMeshError(str(exc), error_code=exc.code) from exc
        if authorization is None:
            raise NativeMeshError(
                "The human cancelled Mesh output authorization.",
                error_code="NATIVE_MESH_EXPORT_CANCELLED",
            )

        def validate_source() -> None:
            context.guard()
            if not mesh_export_source_still_exact(context.document, captured):
                raise NativeMeshError(
                    "The exact Mesh changed before output publication.",
                    error_code="NATIVE_MESH_STATE_STALE",
                )

        def prepare(cancelled: Any, progress: Any) -> Mapping[str, Any]:
            return prepare_mesh_export(
                captured,
                request,
                authorization,
                cancelled=cancelled,
                progress=progress,
                guard=lambda: dispatcher(validate_source),
            )

        try:
            snapshot = manager.submit(
                document_uid=context.document_uid,
                capability_name="mesh.export.export_mesh",
                prepare=prepare,
                validate_before_commit=lambda: None,
                commit=lambda prepared: prepared,
                dispatch_to_document_thread=dispatcher,
            )
        except NativeBackgroundError as exc:
            raise NativeMeshError(
                str(exc),
                error_code="NATIVE_MESH_EXPORT_QUEUE_FAILED",
            ) from exc
        return {
            "job": _job_summary(snapshot),
            "next": {
                "tool": "native.job",
                "operation": "status",
                "job_id": snapshot.job_id,
            },
        }

    def _export_point_cloud(self, values: Mapping[str, Any]) -> dict[str, Any]:
        context = self._context
        context.guard()
        manager = context.background_manager
        dispatcher = context.document_thread_dispatch
        authorizer = context.authorize_output
        if manager is None or dispatcher is None or authorizer is None:
            raise NativeMeshError(
                "Background human-authorized point-cloud export is unavailable in this session.",
                error_code="NATIVE_POINT_CLOUD_EXPORT_UNAVAILABLE",
            )
        target_value = values["target"]
        if not isinstance(target_value, Mapping) or set(target_value) != {"object_name"}:
            raise NativeMeshError("target must contain one exact object_name.")
        target = prepare_point_target(
            context.document,
            context.document_uid,
            {
                "object_name": target_value["object_name"],
                "expected_state_sha256": values["expected_state_sha256"],
                "expected_point_count": values["expected_point_count"],
            },
            require_label=False,
        )
        format_name = str(values["format"])
        if format_name not in POINT_OUTPUT_SUFFIXES:
            raise NativeMeshError("The requested point-cloud export format is unavailable.")
        request = point_output_request(str(target.source.Label), format_name)
        try:
            authorization = authorizer(request)
        except NativeOutputError as exc:
            raise NativeMeshError(str(exc), error_code=exc.code) from exc
        if authorization is None:
            raise NativeMeshError(
                "The human cancelled point-cloud output authorization.",
                error_code="NATIVE_POINT_CLOUD_EXPORT_CANCELLED",
            )

        def validate_source() -> None:
            context.guard()
            if not point_target_still_exact(context.document, target):
                raise NativeMeshError(
                    "The exact point cloud changed before output publication.",
                    error_code="NATIVE_POINT_CLOUD_STATE_STALE",
                )

        def prepare(cancelled: Any, progress: Any) -> Mapping[str, Any]:
            return publish_point_export(
                target,
                format_name,
                request,
                authorization,
                cancelled=cancelled,
                guard=lambda: dispatcher(validate_source),
                progress=progress,
            )

        try:
            snapshot = manager.submit(
                document_uid=context.document_uid,
                capability_name="mesh.export.export_point_cloud",
                prepare=prepare,
                validate_before_commit=lambda: None,
                commit=lambda prepared: prepared,
                dispatch_to_document_thread=dispatcher,
            )
        except NativeBackgroundError as exc:
            raise NativeMeshError(
                str(exc), error_code="NATIVE_POINT_CLOUD_EXPORT_QUEUE_FAILED"
            ) from exc
        return {
            "job": _job_summary(snapshot),
            "next": {
                "tool": "native.job",
                "operation": "status",
                "job_id": snapshot.job_id,
            },
        }
