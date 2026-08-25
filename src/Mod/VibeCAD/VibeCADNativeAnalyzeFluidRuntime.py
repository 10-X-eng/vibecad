# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for FEM fluid constraint mutations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeFluidCreate import (
    create_fluid_constraint,
    prepare_fluid_create,
    verify_fluid_create,
)
from VibeCADNativeAnalyzeFluidEdit import (
    prepare_fluid_update,
    update_fluid_constraint,
    verify_fluid_update,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_KINDS = (
    "initial_flow_velocity",
    "initial_pressure",
    "flow_velocity",
    "fluid_boundary",
)
_CREATE = {f"create_{kind}": kind for kind in _KINDS}
_UPDATE = {f"update_{kind}": kind for kind in _KINDS}


def _arguments(arguments: Mapping[str, Any]) -> tuple[str, str, dict[str, Any]]:
    if not isinstance(arguments, Mapping):
        raise NativeAnalyzeError("Analyze fluid arguments must be one object.")
    values = dict(arguments)
    operation = str(values.pop("operation", "") or "")
    if operation in _CREATE:
        required = {"analysis", "label", "references", "constraint"}
        kind = _CREATE[operation]
    elif operation in _UPDATE:
        kind = _UPDATE[operation]
        allowed = {"target", "label", "references", "constraint"}
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
                "Analyze fluid arguments do not match the selected operation"
                + (f": {'; '.join(details)}." if details else ".")
            )
        return operation, kind, values
    else:
        raise NativeAnalyzeError("The Analyze fluid operation is unavailable.")
    if set(values) != required:
        missing = sorted(required - set(values))
        extra = sorted(set(values) - required)
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"unexpected {', '.join(extra)}")
        raise NativeAnalyzeError(
            "Analyze fluid arguments do not match the selected operation"
            + (f": {'; '.join(details)}." if details else ".")
        )
    return operation, kind, values


class NativeAnalyzeFluidRuntime:
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
            prepared = prepare_fluid_create(
                context.document,
                context.document_uid,
                kind=kind,
                **values,
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name=f"Create FEM {kind.replace('_', ' ').title()}",
                mutate=lambda document: create_fluid_constraint(document, prepared),
                verify=verify_fluid_create,
            )
        target = values.pop("target")
        prepared = prepare_fluid_update(
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
            mutate=lambda document: update_fluid_constraint(document, prepared),
            verify=verify_fluid_update,
        )
