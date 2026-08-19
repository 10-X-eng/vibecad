# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for standalone Part operations on Model."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativePartPrimitives import (
    create_part_primitive,
    part_placement_from_mapping,
    prepare_part_primitive,
    verify_part_primitive,
)
from VibeCADNativePartBuilder import (
    create_part_builder_shape,
    preflight_part_builder,
    prepare_part_builder,
    verify_part_builder_shape,
)
from VibeCADNativePartMakeFace import (
    create_part_make_face,
    preflight_part_make_face,
    prepare_part_make_face,
    verify_part_make_face,
)
from VibeCADNativePartRuledSurface import (
    create_part_ruled_surface,
    preflight_part_ruled_surface,
    prepare_part_ruled_surface,
    verify_part_ruled_surface,
)
from VibeCADNativePartCrossSections import (
    create_part_cross_sections,
    preflight_part_cross_sections,
    prepare_part_cross_sections,
    verify_part_cross_sections,
)
from VibeCADNativePartOffset import (
    create_part_offset,
    create_part_offset_2d,
    preflight_part_offset,
    prepare_part_offset,
    prepare_part_offset_2d,
    verify_part_offset,
    verify_part_offset_2d,
)
from VibeCADNativePartProjection import (
    create_part_projection,
    preflight_part_projection,
    prepare_part_projection,
    verify_part_projection,
)
from VibeCADNativePartCompound import (
    create_part_compound,
    preflight_part_compound,
    prepare_part_compound,
    verify_part_compound,
)
from VibeCADNativePartCompoundFilter import (
    create_part_compound_filter,
    preflight_part_compound_filter,
    prepare_part_compound_filter,
    verify_part_compound_filter,
)
from VibeCADNativePartDefeature import (
    create_part_defeature,
    preflight_part_defeature,
    prepare_part_defeature,
    verify_part_defeature,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_OUTER_FIELDS = {
    "primitive": frozenset({"label", "placement", "definition"}),
    "builder": frozenset({"label", "definition"}),
    "make_face": frozenset({"label", "definition"}),
    "ruled_surface": frozenset({"label", "definition"}),
    "cross_sections": frozenset({"label", "definition"}),
    "offset_3d": frozenset({"label", "definition"}),
    "offset_2d": frozenset({"label", "definition"}),
    "project_surface": frozenset({"label", "definition"}),
    "compound": frozenset({"label", "definition"}),
    "compound_filter": frozenset({"label", "definition"}),
    "defeature": frozenset({"label", "definition"}),
}


class NativeModelPartRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def mutate_part(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(arguments, _OUTER_FIELDS)
        label = str(values["label"] or "").strip()
        if not label or len(label) > 160:
            raise NativeModelError("A visible Part label must contain 1 to 160 characters.")
        if operation == "primitive":
            spec = prepare_part_primitive(values["definition"])
            placement = part_placement_from_mapping(values["placement"])
            self._context.guard()
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Part Primitive",
                mutate=lambda document: create_part_primitive(
                    document,
                    label=label,
                    placement=placement,
                    spec=spec,
                ),
                verify=verify_part_primitive,
            )
        if operation == "builder":
            spec = prepare_part_builder(
                self._context.document_uid,
                values["definition"],
            )
            self._context.guard()
            prepared = preflight_part_builder(self._context.document, spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Part Builder Shape",
                mutate=lambda document: create_part_builder_shape(
                    document,
                    label=label,
                    prepared=prepared,
                ),
                verify=verify_part_builder_shape,
            )
        if operation == "make_face":
            spec = prepare_part_make_face(
                self._context.document_uid,
                values["definition"],
            )
            self._context.guard()
            prepared = preflight_part_make_face(self._context.document, spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Face From Wires",
                mutate=lambda document: create_part_make_face(
                    document,
                    label=label,
                    prepared=prepared,
                ),
                verify=verify_part_make_face,
            )
        if operation == "ruled_surface":
            spec = prepare_part_ruled_surface(
                self._context.document_uid,
                values["definition"],
            )
            self._context.guard()
            prepared = preflight_part_ruled_surface(self._context.document, spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Ruled Surface",
                mutate=lambda document: create_part_ruled_surface(
                    document,
                    label=label,
                    prepared=prepared,
                ),
                verify=verify_part_ruled_surface,
            )
        if operation == "cross_sections":
            spec = prepare_part_cross_sections(
                self._context.document_uid,
                values["definition"],
            )
            self._context.guard()
            prepared = preflight_part_cross_sections(self._context.document, spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Part Cross Sections",
                mutate=lambda document: create_part_cross_sections(
                    document,
                    label=label,
                    prepared=prepared,
                ),
                verify=verify_part_cross_sections,
            )
        if operation == "offset_3d":
            spec = prepare_part_offset(
                self._context.document_uid,
                values["definition"],
            )
            self._context.guard()
            prepared = preflight_part_offset(self._context.document, spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Part 3D Offset",
                mutate=lambda document: create_part_offset(
                    document,
                    label=label,
                    prepared=prepared,
                ),
                verify=verify_part_offset,
            )
        if operation == "offset_2d":
            spec = prepare_part_offset_2d(
                self._context.document_uid,
                values["definition"],
            )
            self._context.guard()
            prepared = preflight_part_offset(self._context.document, spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Part 2D Offset",
                mutate=lambda document: create_part_offset_2d(
                    document,
                    label=label,
                    prepared=prepared,
                ),
                verify=verify_part_offset_2d,
            )
        if operation == "project_surface":
            spec = prepare_part_projection(
                self._context.document_uid,
                values["definition"],
            )
            self._context.guard()
            prepared = preflight_part_projection(self._context.document, spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Projection on Surface",
                mutate=lambda document: create_part_projection(
                    document,
                    label=label,
                    prepared=prepared,
                ),
                verify=verify_part_projection,
            )
        if operation == "compound":
            spec = prepare_part_compound(
                self._context.document_uid,
                values["definition"],
            )
            self._context.guard()
            prepared = preflight_part_compound(self._context.document, spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Part Compound",
                mutate=lambda document: create_part_compound(
                    document,
                    label=label,
                    prepared=prepared,
                ),
                verify=verify_part_compound,
            )
        if operation == "compound_filter":
            spec = prepare_part_compound_filter(
                self._context.document_uid,
                values["definition"],
            )
            self._context.guard()
            prepared = preflight_part_compound_filter(self._context.document, spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Part Compound Filter",
                mutate=lambda document: create_part_compound_filter(
                    document,
                    label=label,
                    prepared=prepared,
                ),
                verify=verify_part_compound_filter,
            )
        if operation == "defeature":
            spec = prepare_part_defeature(
                self._context.document_uid,
                values["definition"],
            )
            self._context.guard()
            prepared = preflight_part_defeature(self._context.document, spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Part Defeaturing",
                mutate=lambda document: create_part_defeature(
                    document,
                    label=label,
                    prepared=prepared,
                ),
                verify=verify_part_defeature,
            )
        raise NativeModelError("That standalone Part operation is unavailable.")
