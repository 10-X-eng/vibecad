# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact retained Mesh conversions."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeMeshConvert import (
    create_mesh_to_shape,
    create_shape_to_mesh,
    prepare_mesh_to_shape,
    prepare_shape_to_mesh,
    verify_mesh_to_shape,
    verify_shape_to_mesh,
)
from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshState import mesh_object_state
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
            "expected_state_sha256",
            "label",
            "tolerance_mm",
            "sew_adjacent_faces",
        }
    ),
    "curve_on_mesh": frozenset(
        {
            "source",
            "expected_state_sha256",
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
        operation, values = strict_variant_arguments(arguments, _VARIANTS)
        context = self._context
        context.guard()
        if operation == "shape_to_mesh":
            prepared = prepare_shape_to_mesh(
                context.document,
                context.document_uid,
                **values,
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Mesh From Shape",
                mutate=lambda document: create_shape_to_mesh(document, prepared),
                verify=verify_shape_to_mesh,
            )
        if operation == "mesh_to_shape":
            prepared = prepare_mesh_to_shape(
                context.document,
                context.document_uid,
                **values,
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Convert Mesh to Shape",
                mutate=lambda document: create_mesh_to_shape(document, prepared),
                verify=verify_mesh_to_shape,
            )
        return self._curve_on_mesh(values, ticket)

    def _curve_on_mesh(
        self,
        values: Mapping[str, Any],
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        context = self._context
        source_value = values["source"]
        if not isinstance(source_value, Mapping) or set(source_value) != {"object_name"}:
            raise NativeMeshError("source must contain one exact object_name.")
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
        expected_state = str(values["expected_state_sha256"])
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
