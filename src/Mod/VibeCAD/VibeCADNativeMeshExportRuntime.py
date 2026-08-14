# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound, background Mesh export runtime."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeBackground import NativeBackgroundError
from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativePointIO import (
    POINT_OUTPUT_SUFFIXES,
    point_output_request,
    publish_point_export,
)
from VibeCADNativePointTargets import point_target_still_exact, prepare_point_target
from VibeCADNativeOutput import (
    NativeOutputError,
    NativeOutputRequest,
    publish_authorized_output,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket
from VibeCADNativeTargets import NativeObjectRef, resolve_object


MAX_MESH_EXPORT_BYTES = 16 * 1024 * 1024 * 1024
_FORMAT_SUFFIX = {
    "binary_stl": ".stl",
    "ascii_stl": ".ast",
    "binary_mesh": ".bms",
    "obj": ".obj",
    "off": ".off",
    "ply": ".ply",
    "nastran": ".bdf",
}


def _target_ref(document_uid: str, value: Any) -> NativeObjectRef:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise NativeMeshError("target must contain one exact object_name.")
    try:
        return NativeObjectRef(document_uid, str(value["object_name"]))
    except Exception as exc:
        raise NativeMeshError("target.object_name must identify one exact Mesh.") from exc


def _count(value: Any, field: str) -> int:
    if type(value) is not int or not 0 <= value <= 2_147_483_647:
        raise NativeMeshError(f"{field} must be one non-negative integer.")
    return value


def _suggested_name(label: str, suffix: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", label.strip()).strip("._-")
    return f"{(stem or 'mesh')[:120]}{suffix}"


def _request(label: str, format_name: str) -> NativeOutputRequest:
    suffix = _FORMAT_SUFFIX[format_name]
    return NativeOutputRequest(
        purpose="export_mesh",
        title="Export Mesh",
        suggested_file_name=_suggested_name(label, suffix),
        allowed_suffixes=(suffix,),
        name_filter=f"{format_name.replace('_', ' ').title()} (*{suffix})",
        maximum_bytes=MAX_MESH_EXPORT_BYTES,
    )


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
                    {
                        "target",
                        "expected_state_sha256",
                        "expected_point_count",
                        "expected_facet_count",
                        "format",
                    }
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
        reference = _target_ref(context.document_uid, values["target"])
        expected_state = str(values["expected_state_sha256"])
        expected_points = _count(values["expected_point_count"], "expected_point_count")
        expected_facets = _count(values["expected_facet_count"], "expected_facet_count")
        format_name = str(values["format"])
        if format_name not in _FORMAT_SUFFIX:
            raise NativeMeshError("The requested Mesh export format is unavailable.")

        obj = resolve_object(context.document, reference, expected_types=("Mesh::Feature",))
        import MeshGui

        if not bool(MeshGui.isNativeMeshInputActive(obj)):
            raise NativeMeshError(
                "The exact Mesh is not active at the current History position.",
                error_code="NATIVE_MESH_HISTORY_TARGET_INACTIVE",
            )
        state = mesh_object_state(obj)
        topology = dict(state.get("topology") or {})
        if (
            state.get("state_sha256") != expected_state
            or topology.get("points") != expected_points
            or topology.get("facets") != expected_facets
        ):
            raise NativeMeshError(
                "The exact Mesh changed after the provider read its state.",
                error_code="NATIVE_MESH_STATE_STALE",
                repair={
                    "target": {"object_name": reference.object_name},
                    "current_state_sha256": state.get("state_sha256"),
                    "current_topology": topology,
                },
            )
        if expected_facets < 1:
            raise NativeMeshError("The exact Mesh has no facets to export.")
        detached = obj.Mesh.copy()
        label = str(getattr(obj, "Label", "") or reference.object_name)
        request = _request(label, format_name)
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
            current = resolve_object(
                context.document,
                reference,
                expected_types=("Mesh::Feature",),
            )
            if (
                not bool(MeshGui.isNativeMeshInputActive(current))
                or mesh_object_state(current).get("state_sha256") != expected_state
            ):
                raise NativeMeshError(
                    "The exact Mesh changed before output publication.",
                    error_code="NATIVE_MESH_STATE_STALE",
                )

        def prepare(cancelled: Any, progress: Any) -> Mapping[str, Any]:
            if cancelled():
                from VibeCADNativeBackground import NativeBackgroundCancelled

                raise NativeBackgroundCancelled()
            progress(10, "Writing detached mesh data")

            def writer(path: str) -> None:
                detached.write(Filename=path)

            def validator(path: Path) -> None:
                if cancelled():
                    from VibeCADNativeBackground import NativeBackgroundCancelled

                    raise NativeBackgroundCancelled()
                import Mesh

                check = Mesh.read(str(path))
                if int(getattr(check, "CountFacets", 0) or 0) < 1:
                    raise NativeMeshError("The generated Mesh output has no facets.")

            artifact = publish_authorized_output(
                request,
                authorization,
                writer=writer,
                guard=lambda: dispatcher(validate_source),
                validator=validator,
                temporary_suffix=_FORMAT_SUFFIX[format_name],
            )
            progress(90, "Mesh output verified and published")
            return {
                "output": artifact.summary(),
                "format": format_name,
                "source": {
                    "object_name": reference.object_name,
                    "state_sha256": expected_state,
                    "points": expected_points,
                    "facets": expected_facets,
                },
            }

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
