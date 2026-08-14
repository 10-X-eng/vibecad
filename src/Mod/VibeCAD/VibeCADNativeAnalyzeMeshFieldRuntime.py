# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for Gmsh refinement-field composition."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeMeshFieldCreate import (
    create_mesh_field,
    prepare_mesh_field_create,
    verify_mesh_field_create,
)
from VibeCADNativeAnalyzeMeshFieldEdit import (
    prepare_mesh_field_update,
    update_mesh_field,
    verify_mesh_field_update,
)
from VibeCADNativeAnalyzeMeshFieldValues import ADVANCED_KINDS, MANIPULATION_KINDS
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_KINDS = (*MANIPULATION_KINDS, *ADVANCED_KINDS)
_CREATE = {f"create_{kind}": kind for kind in _KINDS}
_UPDATE = {f"update_{kind}": kind for kind in _KINDS}
_GEOMETRY_KINDS = frozenset({"restrict", "attractor_aniso_curve", "distance"})


def _create_fields(kind: str) -> set[str]:
    fields = {"mesh", "label", "definition"}
    if kind in MANIPULATION_KINDS:
        fields.add("input_refinement")
    elif kind in {"math_eval", "math_eval_aniso"}:
        fields.add("input_refinements")
    if kind in _GEOMETRY_KINDS:
        fields.add("references")
    if kind == "result":
        fields.add("result")
    return fields


def _update_fields(kind: str) -> set[str]:
    fields = _create_fields(kind)
    fields.remove("mesh")
    return fields


def _arguments(arguments: Mapping[str, Any]) -> tuple[str, str, dict[str, Any]]:
    if not isinstance(arguments, Mapping):
        raise NativeAnalyzeError("Mesh-field arguments must be one object.")
    values = dict(arguments)
    operation = str(values.pop("operation", "") or "")
    if operation in _CREATE:
        kind = _CREATE[operation]
        required = _create_fields(kind)
    elif operation in _UPDATE:
        kind = _UPDATE[operation]
        allowed = {"target"} | _update_fields(kind)
        provided = set(values)
        if "target" not in provided or len(provided) < 2 or not provided <= allowed:
            missing = [] if "target" in provided else ["target"]
            extra = sorted(provided - allowed)
            details = []
            if missing:
                details.append("missing target")
            if len(provided - {"target"}) < 1:
                details.append("missing at least one editable field")
            if extra:
                details.append(f"unexpected {', '.join(extra)}")
            raise NativeAnalyzeError(
                "Mesh-field arguments do not match the selected operation"
                + (f": {'; '.join(details)}." if details else ".")
            )
        return operation, kind, values
    else:
        raise NativeAnalyzeError("The mesh-field operation is unavailable.")
    if set(values) != required:
        missing = sorted(required - set(values))
        extra = sorted(set(values) - required)
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"unexpected {', '.join(extra)}")
        raise NativeAnalyzeError(
            "Mesh-field arguments do not match the selected operation"
            + (f": {'; '.join(details)}." if details else ".")
        )
    return operation, kind, values


class NativeAnalyzeMeshFieldRuntime:
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
        operation, kind, values = _arguments(arguments)
        context = self._context
        context.guard()
        if operation in _CREATE:
            prepared = prepare_mesh_field_create(
                context.document,
                context.document_uid,
                kind=kind,
                **values,
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name=f"Create FEM Mesh {' '.join(part.title() for part in kind.split('_'))}",
                mutate=lambda document: create_mesh_field(document, prepared),
                verify=verify_mesh_field_create,
            )
        target = values.pop("target")
        prepared = prepare_mesh_field_update(
            context.document,
            context.document_uid,
            kind=kind,
            target=target,
            changes=values,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name=f"Edit FEM Mesh {' '.join(part.title() for part in kind.split('_'))}",
            mutate=lambda document: update_mesh_field(document, prepared),
            verify=verify_mesh_field_update,
        )
