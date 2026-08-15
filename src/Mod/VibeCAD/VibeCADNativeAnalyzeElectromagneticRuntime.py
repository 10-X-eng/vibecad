# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for FEM electromagnetic constraint mutations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeConstraintCreate import (
    create_constraint,
    prepare_constraint_create,
    verify_constraint_create,
)
from VibeCADNativeAnalyzeConstraintEdit import (
    prepare_constraint_update,
    update_constraint,
    verify_constraint_update,
)
from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_KINDS = (
    "electromagnetic",
    "current_density",
    "magnetization",
    "electric_charge_density",
)
_CREATE = {f"constraint_{kind}": kind for kind in _KINDS}
_UPDATE = {f"update_{kind}": kind for kind in _KINDS}


def _arguments(arguments: Mapping[str, Any]) -> tuple[str, str, dict[str, Any]]:
    if not isinstance(arguments, Mapping):
        raise NativeAnalyzeError(
            "Analyze electromagnetic arguments must be one object."
        )
    values = dict(arguments)
    operation = str(values.pop("operation", "") or "")
    if operation in _CREATE:
        required = {"analysis", "label", "references", "constraint"}
        allowed = required
        kind = _CREATE[operation]
    elif operation in _UPDATE:
        kind = _UPDATE[operation]
        required = {"target"}
        allowed = {"target", "label", "references", "constraint"}
    else:
        raise NativeAnalyzeError(
            "The Analyze electromagnetic operation is unavailable."
        )
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
            "Analyze electromagnetic arguments do not match the selected operation"
            + (f": {'; '.join(details)}." if details else ".")
        )
    return operation, kind, values


class NativeAnalyzeElectromagneticRuntime:
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
            prepared = prepare_constraint_create(
                context.document,
                context.document_uid,
                kind=kind,
                **values,
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name=f"Create FEM {kind.replace('_', ' ').title()}",
                mutate=lambda document: create_constraint(document, prepared),
                verify=verify_constraint_create,
            )
        target = values.pop("target")
        prepared = prepare_constraint_update(
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
            mutate=lambda document: update_constraint(document, prepared),
            verify=verify_constraint_update,
        )
