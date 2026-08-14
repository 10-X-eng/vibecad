# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for paired FEM connection mutations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeConnectionCreate import (
    create_connection,
    prepare_connection_create,
    verify_connection_create,
)
from VibeCADNativeAnalyzeConnectionEdit import (
    prepare_connection_update,
    update_connection,
    verify_connection_update,
)
from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_KINDS = ("contact", "tie")
_CREATE = {f"create_{kind}": kind for kind in _KINDS}
_UPDATE = {f"update_{kind}": kind for kind in _KINDS}


def _arguments(arguments: Mapping[str, Any]) -> tuple[str, str, dict[str, Any]]:
    if not isinstance(arguments, Mapping):
        raise NativeAnalyzeError("Analyze connection arguments must be one object.")
    values = dict(arguments)
    operation = str(values.pop("operation", "") or "")
    if operation in _CREATE:
        kind = _CREATE[operation]
        required = {"analysis", "label", "slave", "master", "connection"}
    elif operation in _UPDATE:
        kind = _UPDATE[operation]
        allowed = {"target", "label", "slave", "master", "connection"}
        provided = set(values)
        if "target" not in provided or len(provided) < 2 or not provided <= allowed:
            details = []
            if "target" not in provided:
                details.append("missing target")
            if len(provided - {"target"}) < 1:
                details.append("missing at least one editable field")
            extra = sorted(provided - allowed)
            if extra:
                details.append(f"unexpected {', '.join(extra)}")
            raise NativeAnalyzeError(
                "Analyze connection arguments do not match the selected operation"
                + (f": {'; '.join(details)}." if details else ".")
            )
        return operation, kind, values
    else:
        raise NativeAnalyzeError("The Analyze connection operation is unavailable.")
    if set(values) != required:
        missing = sorted(required - set(values))
        extra = sorted(set(values) - required)
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"unexpected {', '.join(extra)}")
        raise NativeAnalyzeError(
            "Analyze connection arguments do not match the selected operation"
            + (f": {'; '.join(details)}." if details else ".")
        )
    return operation, kind, values


class NativeAnalyzeConnectionRuntime:
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
            prepared = prepare_connection_create(
                context.document,
                context.document_uid,
                kind=kind,
                **values,
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name=f"Create FEM {kind.title()}",
                mutate=lambda document: create_connection(document, prepared),
                verify=verify_connection_create,
            )
        target = values.pop("target")
        prepared = prepare_connection_update(
            context.document,
            context.document_uid,
            kind=kind,
            target=target,
            changes=values,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name=f"Edit FEM {kind.title()}",
            mutate=lambda document: update_connection(document, prepared),
            verify=verify_connection_update,
        )
