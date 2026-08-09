# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for Surface operations on the Model ribbon."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket
from VibeCADNativeSurfaceFilling import (
    create_surface_filling,
    preflight_surface_filling,
    prepare_surface_filling,
    verify_surface_filling,
)
from VibeCADNativeSurfaceGeomFill import (
    create_surface_geometric_fill,
    preflight_surface_geometric_fill,
    prepare_surface_geometric_fill,
    verify_surface_geometric_fill,
)
from VibeCADNativeSurfaceSections import (
    create_surface_sections,
    preflight_surface_sections,
    prepare_surface_sections,
    verify_surface_sections,
)
from VibeCADNativeSurfaceExtend import (
    create_surface_extend,
    preflight_surface_extend,
    prepare_surface_extend,
    verify_surface_extend,
)
from VibeCADNativeSurfaceCurveOnMesh import (
    create_surface_curve_on_mesh,
    preflight_surface_curve_on_mesh,
    prepare_surface_curve_on_mesh,
    verify_surface_curve_on_mesh,
)
from VibeCADNativeSurfaceBlendCurve import (
    create_surface_blend_curve,
    preflight_surface_blend_curve,
    prepare_surface_blend_curve,
    verify_surface_blend_curve,
)


_OUTER_FIELDS = {
    "filling": frozenset({"label", "definition"}),
    "geom_fill_surface": frozenset({"label", "definition"}),
    "sections": frozenset({"label", "definition"}),
    "extend_face": frozenset({"label", "definition"}),
    "curve_on_mesh": frozenset({"label", "definition"}),
    "blend_curve": frozenset({"label", "definition"}),
}


class NativeModelSurfaceRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def mutate_surface(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(arguments, _OUTER_FIELDS)
        label = str(values["label"] or "").strip()
        if not label or len(label) > 160:
            raise NativeModelError(
                "A visible Surface label must contain 1 to 160 characters."
            )
        if operation == "filling":
            spec = prepare_surface_filling(
                self._context.document_uid,
                values["definition"],
            )
            self._context.guard()
            prepared = preflight_surface_filling(self._context.document, spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Surface Filling",
                mutate=lambda document: create_surface_filling(
                    document,
                    label=label,
                    prepared=prepared,
                ),
                verify=verify_surface_filling,
            )
        if operation == "geom_fill_surface":
            spec = prepare_surface_geometric_fill(
                self._context.document_uid,
                values["definition"],
            )
            self._context.guard()
            prepared = preflight_surface_geometric_fill(
                self._context.document,
                spec,
            )
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Geometric Fill Surface",
                mutate=lambda document: create_surface_geometric_fill(
                    document,
                    label=label,
                    prepared=prepared,
                ),
                verify=verify_surface_geometric_fill,
            )
        if operation == "sections":
            spec = prepare_surface_sections(
                self._context.document_uid,
                values["definition"],
            )
            self._context.guard()
            prepared = preflight_surface_sections(self._context.document, spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Surface Sections",
                mutate=lambda document: create_surface_sections(
                    document,
                    label=label,
                    prepared=prepared,
                ),
                verify=verify_surface_sections,
            )
        if operation == "extend_face":
            spec = prepare_surface_extend(
                self._context.document_uid,
                values["definition"],
            )
            self._context.guard()
            prepared = preflight_surface_extend(self._context.document, spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Surface Extend Face",
                mutate=lambda document: create_surface_extend(
                    document,
                    label=label,
                    prepared=prepared,
                ),
                verify=verify_surface_extend,
            )
        if operation == "curve_on_mesh":
            spec = prepare_surface_curve_on_mesh(
                self._context.document_uid,
                values["definition"],
            )
            self._context.guard()
            prepared = preflight_surface_curve_on_mesh(self._context.document, spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Curve on Mesh",
                mutate=lambda document: create_surface_curve_on_mesh(
                    document,
                    label=label,
                    prepared=prepared,
                ),
                verify=verify_surface_curve_on_mesh,
            )
        if operation == "blend_curve":
            spec = prepare_surface_blend_curve(
                self._context.document_uid,
                values["definition"],
            )
            self._context.guard()
            prepared = preflight_surface_blend_curve(self._context.document, spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Blend Curve",
                mutate=lambda document: create_surface_blend_curve(
                    document,
                    label=label,
                    prepared=prepared,
                ),
                verify=verify_surface_blend_curve,
            )
        raise NativeModelError("That Surface operation is unavailable.")
