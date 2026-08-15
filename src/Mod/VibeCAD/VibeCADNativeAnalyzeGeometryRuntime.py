# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for FEM element-definition mutations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeGeometryCreate import (
    create_element_definition,
    prepare_element_create,
    verify_element_create,
)
from VibeCADNativeAnalyzeGeometryEdit import (
    prepare_element_update,
    update_element_definition,
    verify_element_update,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_CREATE = {
    "create_beam_section": ("beam_section", "section"),
    "create_beam_rotation": ("beam_rotation", "rotation_degrees"),
    "create_shell_thickness": ("shell_thickness", "thickness_mm"),
    "create_fluid_section": ("fluid_section", "section"),
}
_UPDATE = {
    "update_beam_section": ("beam_section", "section"),
    "update_beam_rotation": ("beam_rotation", "rotation_degrees"),
    "update_shell_thickness": ("shell_thickness", "thickness_mm"),
    "update_fluid_section": ("fluid_section", "section"),
}


def _arguments(arguments: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    if not isinstance(arguments, Mapping):
        raise NativeAnalyzeError("Analyze geometry arguments must be one object.")
    values = dict(arguments)
    operation = str(values.pop("operation", "") or "")
    if operation in _CREATE:
        _kind, field = _CREATE[operation]
        required = {"analysis", "label", "references", field}
        allowed = required
    elif operation in _UPDATE:
        _kind, field = _UPDATE[operation]
        required = {"target"}
        allowed = {"target", "label", "references", field}
    else:
        raise NativeAnalyzeError("The Analyze geometry operation is unavailable.")
    if not required <= set(values) or not set(values) <= allowed or len(values) < 2:
        missing = sorted(required - set(values))
        extra = sorted(set(values) - allowed)
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"unexpected {', '.join(extra)}")
        if operation in _UPDATE and len(values) < 2:
            details.append("no update fields")
        raise NativeAnalyzeError(
            "Analyze geometry arguments do not match the selected operation"
            + (f": {'; '.join(details)}." if details else ".")
        )
    return operation, values


class NativeAnalyzeGeometryRuntime:
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
        operation, values = _arguments(arguments)
        context = self._context
        context.guard()
        if operation in _CREATE:
            kind, field = _CREATE[operation]
            value = values.pop(field)
            prepared = prepare_element_create(
                context.document,
                context.document_uid,
                kind=kind,
                value=value,
                **values,
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name=f"Create FEM {kind.replace('_', ' ').title()}",
                mutate=lambda document: create_element_definition(document, prepared),
                verify=verify_element_create,
            )
        kind, field = _UPDATE[operation]
        target = values.pop("target")
        prepared = prepare_element_update(
            context.document,
            context.document_uid,
            kind=kind,
            value_field=field,
            target=target,
            changes=values,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name=f"Edit FEM {kind.replace('_', ' ').title()}",
            mutate=lambda document: update_element_definition(document, prepared),
            verify=verify_element_update,
        )
