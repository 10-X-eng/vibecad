# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for FEM mechanical support mutations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeSupportCreate import (
    create_support_condition,
    prepare_support_create,
    verify_support_create,
)
from VibeCADNativeAnalyzeSupportEdit import (
    prepare_support_update,
    update_support_condition,
    verify_support_update,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_KINDS = ("fixed", "rigid_body", "displacement", "spring")
_CREATE = {f"create_{kind}": kind for kind in _KINDS}
_UPDATE = {f"update_{kind}": kind for kind in _KINDS}


def _arguments(arguments: Mapping[str, Any]) -> tuple[str, str, dict[str, Any]]:
    if not isinstance(arguments, Mapping):
        raise NativeAnalyzeError("Analyze support arguments must be one object.")
    values = dict(arguments)
    operation = str(values.pop("operation", "") or "")
    if operation in _CREATE:
        kind = _CREATE[operation]
        required = {"analysis", "label", "references"}
        if kind != "fixed":
            required.add("condition")
        allowed = required
    elif operation in _UPDATE:
        kind = _UPDATE[operation]
        required = {"target"}
        allowed = {"target", "label", "references"}
        if kind != "fixed":
            allowed.add("condition")
    else:
        raise NativeAnalyzeError("The Analyze support operation is unavailable.")
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
            "Analyze support arguments do not match the selected operation"
            + (f": {'; '.join(details)}." if details else ".")
        )
    return operation, kind, values


class NativeAnalyzeSupportRuntime:
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
            prepared = prepare_support_create(
                context.document,
                context.document_uid,
                kind=kind,
                **values,
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name=f"Create FEM {kind.replace('_', ' ').title()}",
                mutate=lambda document: create_support_condition(document, prepared),
                verify=verify_support_create,
            )
        target = values.pop("target")
        prepared = prepare_support_update(
            context.document,
            context.document_uid,
            kind=kind,
            target=target,
            changes=values,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name=f"Edit FEM {kind.replace('_', ' ').title()}",
            mutate=lambda document: update_support_condition(document, prepared),
            verify=verify_support_update,
        )
