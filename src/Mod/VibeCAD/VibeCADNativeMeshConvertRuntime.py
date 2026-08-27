# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact retained Mesh conversions."""

from __future__ import annotations

import math
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeBackground import NativeBackgroundError
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeMeshConvert import (
    capture_shape_tessellation,
    capture_mesh_conversion,
    commit_mesh_conversion,
    commit_shape_tessellation,
    shape_tessellation_source_still_exact,
    verify_committed_mesh_conversion,
    verify_shape_tessellation,
)
from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeMeshTargets import mesh_target_still_exact
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket
from VibeCADNativeSurfaceCurveOnMesh import (
    create_surface_curve_on_mesh,
    preflight_surface_curve_on_mesh,
    prepare_surface_curve_on_mesh,
    verify_surface_curve_on_mesh,
)
from VibeCADNativeTargets import NativeObjectRef, resolve_object


_VARIANTS = {
    "shape_to_mesh": frozenset(
        {
            "source",
            "subelements",
            "label",
            "linear_deflection_mm",
            "angular_deflection_degrees",
            "relative",
            "segments",
        }
    ),
    "mesh_to_shape": frozenset(
        {
            "source",
            "label",
            "tolerance_mm",
            "sew_adjacent_faces",
        }
    ),
    "mesh_to_solid": frozenset(
        {
            "source",
            "label",
            "tolerance_mm",
        }
    ),
    "curve_on_mesh": frozenset(
        {
            "source",
            "anchors",
            "label",
            "closed",
            "approximate",
            "maximum_degree",
            "continuity",
            "tolerance_mm",
            "split_angle_degrees",
        }
    ),
}


def _focused_convert_arguments(
    capability_name: str,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    values = dict(arguments)
    if capability_name == "mesh.curve_on_mesh":
        operation = str(values.get("operation") or "create")
        source = values.get("source")
        source_name = (
            str(source.get("object_name") or "")
            if isinstance(source, Mapping)
            else "Mesh"
        )
        values["operation"] = (
            "curve_on_mesh" if operation == "create" else operation
        )
        values["label"] = str(
            values.pop("result_label", "") or f"{source_name} Curve"
        )
        values.setdefault("closed", False)
        values.setdefault("approximate", True)
        values.setdefault("maximum_degree", 5)
        values.setdefault("continuity", "C2")
        values.setdefault("tolerance_mm", 0.2)
        values.setdefault("split_angle_degrees", 45.0)
        return values
    if capability_name == "mesh.from_shape":
        operation = str(values.get("operation") or "tessellate")
        source = values.get("source")
        source_name = (
            str(source.get("object_name") or "")
            if isinstance(source, Mapping)
            else "Shape"
        )
        values["operation"] = (
            "shape_to_mesh" if operation == "tessellate" else operation
        )
        values["subelements"] = values.pop("faces", [])
        values["label"] = str(
            values.pop("result_label", "") or f"{source_name} Mesh"
        )
        values["linear_deflection_mm"] = values.pop(
            "surface_deviation_mm",
            0.1,
        )
        values["angular_deflection_degrees"] = values.pop(
            "angular_deviation_degrees",
            30.0,
        )
        values["relative"] = values.pop("relative_surface_deviation", False)
        values["segments"] = values.pop("preserve_face_colors", False)
        return values
    if capability_name != "mesh.to_shape":
        return values
    operation = str(values.get("operation") or "")
    source = values.get("source")
    source_name = (
        str(source.get("object_name") or "")
        if isinstance(source, Mapping)
        else "Mesh"
    )
    values["operation"] = {
        "shell": "mesh_to_shape",
        "solid": "mesh_to_solid",
    }.get(operation, operation)
    values["label"] = str(
        values.pop("result_label", "")
        or f"{source_name} {'Solid' if operation == 'solid' else 'Shell'}"
    )
    values.setdefault("tolerance_mm", 0.000001)
    if operation == "shell":
        values.setdefault("sew_adjacent_faces", True)
    return values


def _model_error(exc: NativeModelError) -> NativeMeshError:
    return NativeMeshError(str(exc), error_code="NATIVE_MESH_CURVE_INVALID")


def _label(value: Any) -> str:
    result = str(value or "").strip()
    if not result or len(result) > 160:
        raise NativeMeshError("label must contain 1 to 160 visible characters.")
    return result


class NativeMeshConvertRuntime:
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
        normalized_arguments = _focused_convert_arguments(
            str(getattr(ticket, "capability_name", "") or ""),
            arguments,
        )
        operation, values = strict_variant_arguments(normalized_arguments, _VARIANTS)
        context = self._context
        context.guard()
        if operation == "shape_to_mesh":
            captured = capture_shape_tessellation(
                context.document,
                context.document_uid,
                source=values["source"],
                subelements=values["subelements"],
                label=values["label"],
                settings={
                    "method": "standard",
                    "linear_deflection_mm": values["linear_deflection_mm"],
                    "angular_deflection_radians": math.radians(
                        values["angular_deflection_degrees"]
                    ),
                    "relative": values["relative"],
                    "segments": values["segments"],
                },
            )
            return self._start_shape_tessellation(captured, ticket)
        if operation in {"mesh_to_shape", "mesh_to_solid"}:
            conversion_values = dict(values)
            source_value = conversion_values.pop("source")
            if not isinstance(source_value, Mapping) or set(source_value) != {
                "object_name",
                "expected_state_sha256",
            }:
                raise NativeMeshError(
                    "source must contain object_name and expected_state_sha256."
                )
            conversion_values["source"] = {
                "object_name": str(source_value["object_name"]),
            }
            conversion_values["expected_state_sha256"] = str(
                source_value["expected_state_sha256"]
            )
            conversion_values["sew_adjacent_faces"] = (
                True
                if operation == "mesh_to_solid"
                else values["sew_adjacent_faces"]
            )
            conversion_values["make_solid"] = operation == "mesh_to_solid"
            captured = capture_mesh_conversion(
                context.document,
                context.document_uid,
                **conversion_values,
            )
            return self._start_mesh_conversion(operation, captured, ticket)
        return self._curve_on_mesh(values, ticket)

    def _start_shape_tessellation(
        self,
        captured: Any,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        context = self._context
        manager = context.background_manager
        dispatcher = context.document_thread_dispatch
        if manager is None or dispatcher is None:
            raise NativeMeshError(
                "Background shape tessellation is unavailable in this session.",
                error_code="NATIVE_MESH_TESSELLATION_UNAVAILABLE",
            )
        from VibeCADMeshTessellationJob import run_shape_tessellation

        def validate() -> None:
            context.guard()
            if not shape_tessellation_source_still_exact(context.document, captured):
                raise NativeMeshError(
                    "The exact shape changed while tessellation was running; no stale Mesh was applied.",
                    error_code="NATIVE_MESH_STATE_STALE",
                )

        def commit(prepared: Any) -> Mapping[str, Any]:
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Mesh From Shape",
                mutate=lambda document: commit_shape_tessellation(document, prepared),
                verify=verify_shape_tessellation,
            )

        def cleanup(_prepared: Any) -> None:
            context.state.cancel_mutation(ticket)

        try:
            snapshot = manager.submit(
                document_uid=context.document_uid,
                capability_name="mesh.convert.shape_to_mesh",
                prepare=lambda cancelled, progress: run_shape_tessellation(
                    captured,
                    cancelled=cancelled,
                    progress=progress,
                ),
                validate_before_commit=validate,
                commit=commit,
                dispatch_to_document_thread=dispatcher,
                finalize_message="Publishing verified shape tessellation",
                cleanup=cleanup,
                changes_document=True,
            )
        except NativeBackgroundError as exc:
            raise NativeMeshError(
                str(exc),
                error_code="NATIVE_MESH_TESSELLATION_QUEUE_FAILED",
            ) from exc
        try:
            def watch_status() -> None:
                import FreeCAD as App

                if not bool(getattr(App, "GuiUp", False)):
                    return
                from VibeCADMeshTessellationGui import watch_shape_tessellation_job

                watch_shape_tessellation_job(manager, str(snapshot.job_id))

            dispatcher(watch_status)
        except Exception:
            pass
        return {
            "job": {
                "job_id": str(snapshot.job_id),
                "capability": str(snapshot.capability_name),
                "phase": str(snapshot.phase),
                "progress_percent": int(snapshot.progress_percent),
                "progress_message": str(snapshot.progress_message),
                "terminal": bool(snapshot.terminal),
            },
            "next": {
                "tool": "native.job",
                "operation": "status",
                "job_id": snapshot.job_id,
            },
        }

    def _start_mesh_conversion(
        self,
        operation: str,
        captured: Any,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        context = self._context
        manager = context.background_manager
        dispatcher = context.document_thread_dispatch
        if manager is None or dispatcher is None:
            raise NativeMeshError(
                "Background Mesh conversion is unavailable in this session.",
                error_code="NATIVE_MESH_CONVERSION_UNAVAILABLE",
            )
        from VibeCADMeshConversionJob import run_mesh_conversion

        def prepare(cancelled: Any, progress: Any) -> Any:
            return run_mesh_conversion(
                captured,
                cancelled=cancelled,
                progress=progress,
            )

        def validate() -> None:
            context.guard()
            if not mesh_target_still_exact(context.document, captured.target):
                raise NativeMeshError(
                    "The exact Mesh changed while its BREP was being prepared; the stale result was not applied.",
                    error_code="NATIVE_MESH_STATE_STALE",
                )

        def commit(prepared: Any) -> Mapping[str, Any]:
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name=(
                    "Convert Mesh to Solid"
                    if operation == "mesh_to_solid"
                    else "Convert Mesh to Shape"
                ),
                mutate=lambda document: commit_mesh_conversion(document, prepared),
                verify=verify_committed_mesh_conversion,
            )

        def cleanup(_prepared: Any) -> None:
            context.state.cancel_mutation(ticket)

        try:
            snapshot = manager.submit(
                document_uid=context.document_uid,
                capability_name=f"mesh.convert.{operation}",
                prepare=prepare,
                validate_before_commit=validate,
                commit=commit,
                dispatch_to_document_thread=dispatcher,
                finalize_message="Publishing verified Mesh conversion",
                cleanup=cleanup,
                changes_document=True,
            )
        except NativeBackgroundError as exc:
            raise NativeMeshError(
                str(exc),
                error_code="NATIVE_MESH_CONVERSION_QUEUE_FAILED",
            ) from exc
        try:
            def watch_status() -> None:
                import FreeCAD as App

                if not bool(getattr(App, "GuiUp", False)):
                    return
                from VibeCADMeshConversionGui import watch_mesh_conversion_job

                watch_mesh_conversion_job(manager, str(snapshot.job_id))

            dispatcher(watch_status)
        except Exception:
            pass
        return {
            "job": {
                "job_id": str(snapshot.job_id),
                "capability": str(snapshot.capability_name),
                "phase": str(snapshot.phase),
                "progress_percent": int(snapshot.progress_percent),
                "progress_message": str(snapshot.progress_message),
                "terminal": bool(snapshot.terminal),
            },
            "next": {
                "tool": "native.job",
                "operation": "status",
                "job_id": snapshot.job_id,
            },
        }

    def _curve_on_mesh(
        self,
        values: Mapping[str, Any],
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        context = self._context
        source_value = values["source"]
        if not isinstance(source_value, Mapping) or set(source_value) != {
            "object_name",
            "expected_state_sha256",
        }:
            raise NativeMeshError(
                "source must contain object_name and expected_state_sha256."
            )
        reference = NativeObjectRef(context.document_uid, str(source_value["object_name"]))
        source = resolve_object(
            context.document,
            reference,
            expected_types=("Mesh::Feature",),
        )
        import MeshGui

        if not bool(MeshGui.isNativeMeshInputActive(source)):
            raise NativeMeshError(
                "The exact Mesh is not active at the current History position.",
                error_code="NATIVE_MESH_HISTORY_TARGET_INACTIVE",
            )
        current_state = mesh_object_state(source)
        expected_state = str(source_value["expected_state_sha256"])
        if current_state.get("state_sha256") != expected_state:
            raise NativeMeshError(
                "The exact Mesh changed after the provider read its state.",
                error_code="NATIVE_MESH_STATE_STALE",
                repair={
                    "source": {"object_name": reference.object_name},
                    "current_state_sha256": current_state.get("state_sha256"),
                    "current_topology": current_state.get("topology"),
                },
            )
        definition = {
            "object_name": reference.object_name,
            "anchors": values["anchors"],
            "closed": values["closed"],
            "approximate": values["approximate"],
            "maximum_degree": values["maximum_degree"],
            "continuity": values["continuity"],
            "tolerance": values["tolerance_mm"],
            "split_angle_degrees": values["split_angle_degrees"],
        }
        try:
            spec = prepare_surface_curve_on_mesh(context.document_uid, definition)
            prepared = preflight_surface_curve_on_mesh(context.document, spec)
        except NativeModelError as exc:
            raise _model_error(exc) from exc

        def mutate(document: Any):
            try:
                draft = create_surface_curve_on_mesh(
                    document,
                    label=_label(values["label"]),
                    prepared=prepared,
                )
                MeshGui.publishSourcePreservingOutputs(
                    str(document.Name),
                    [prepared.source],
                    [draft.value["result"]],
                    "CurvesOnMesh",
                    "Curves on Mesh",
                    "Create curves on mesh",
                )
                return draft
            except NativeModelError as exc:
                raise _model_error(exc) from exc

        def verify(document: Any, draft: Any) -> Mapping[str, Any]:
            try:
                result = dict(verify_surface_curve_on_mesh(document, draft))
            except NativeModelError as exc:
                raise _model_error(exc) from exc
            result["source_state_sha256"] = expected_state
            return result

        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name="Curve on Mesh",
            mutate=mutate,
            verify=verify,
        )
