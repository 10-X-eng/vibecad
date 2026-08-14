# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact FEM solver-control edits."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeSolverControlEdit import (
    prepare_solver_control_update,
    update_solver_control,
    verify_solver_control_update,
)
from VibeCADNativeAnalyzeSolverControlSchema import SOLVER_CONTROL_FIELDS_BY_BACKEND
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_KINDS = {
    "update_calculix": "calculix",
    "update_elmer": "elmer",
    "update_z88": "z88",
}


def _arguments(arguments: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    if not isinstance(arguments, Mapping):
        raise NativeAnalyzeError("Analyze solver-control arguments must be one object.")
    values = dict(arguments)
    operation = str(values.pop("operation", "") or "")
    kind = _KINDS.get(operation)
    if kind is None:
        raise NativeAnalyzeError("The Analyze solver-control operation is unavailable.")
    allowed = {"target", *SOLVER_CONTROL_FIELDS_BY_BACKEND[kind]}
    provided = set(values)
    if "target" not in provided or len(provided) < 2 or not provided <= allowed:
        details = []
        if "target" not in provided:
            details.append("missing target")
        if len(provided - {"target"}) < 1:
            details.append("missing at least one editable setting")
        extra = sorted(provided - allowed)
        if extra:
            details.append(f"unexpected {', '.join(extra)}")
        raise NativeAnalyzeError(
            "Analyze solver-control arguments do not match the selected operation"
            + (f": {'; '.join(details)}." if details else ".")
        )
    return operation, values


class NativeAnalyzeSolverControlRuntime:
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
        target = values.pop("target")
        prepared = prepare_solver_control_update(
            context.document,
            context.document_uid,
            kind=_KINDS[operation],
            target=target,
            changes=values,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name=f"Update {prepared.target.kind.title()} FEM Solver",
            mutate=lambda document: update_solver_control(document, prepared),
            verify=verify_solver_control_update,
        )
