# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for structured transfinite mesh resources."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeMeshRefinementCreate import (
    create_mesh_refinement,
    prepare_mesh_refinement_create,
    verify_mesh_refinement_create,
)
from VibeCADNativeAnalyzeMeshRefinementEdit import (
    prepare_mesh_refinement_update,
    update_mesh_refinement,
    verify_mesh_refinement_update,
)
from VibeCADNativeAnalyzeMeshRefinementValues import STRUCTURED_MODES
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_CREATE = {f"create_{mode}": mode for mode in STRUCTURED_MODES}
_UPDATE = {f"update_{mode}": mode for mode in STRUCTURED_MODES}


def _arguments(arguments: Mapping[str, Any]) -> tuple[str, str, dict[str, Any]]:
    if not isinstance(arguments, Mapping):
        raise NativeAnalyzeError("Structured mesh arguments must be one object.")
    values = dict(arguments)
    operation = str(values.pop("operation", "") or "")
    if operation in _CREATE:
        mode = _CREATE[operation]
        required = {"mesh", "label", "references", "definition"}
    elif operation in _UPDATE:
        mode = _UPDATE[operation]
        allowed = {"target", "label", "references", "definition"}
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
                "Structured mesh arguments do not match the selected operation"
                + (f": {'; '.join(details)}." if details else ".")
            )
        return operation, mode, values
    else:
        raise NativeAnalyzeError("The structured mesh operation is unavailable.")
    if set(values) != required:
        missing = sorted(required - set(values))
        extra = sorted(set(values) - required)
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"unexpected {', '.join(extra)}")
        raise NativeAnalyzeError(
            "Structured mesh arguments do not match the selected operation"
            + (f": {'; '.join(details)}." if details else ".")
        )
    return operation, mode, values


class NativeAnalyzeStructuredMeshRuntime:
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
        operation, mode, values = _arguments(arguments)
        context = self._context
        context.guard()
        if operation in _CREATE:
            prepared = prepare_mesh_refinement_create(
                context.document,
                context.document_uid,
                mode=mode,
                **values,
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name=f"Create FEM {mode.replace('_', ' ').title()}",
                mutate=lambda document: create_mesh_refinement(document, prepared),
                verify=verify_mesh_refinement_create,
            )
        target = values.pop("target")
        prepared = prepare_mesh_refinement_update(
            context.document,
            context.document_uid,
            mode=mode,
            target=target,
            changes=values,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name=f"Edit FEM {mode.replace('_', ' ').title()}",
            mutate=lambda document: update_mesh_refinement(document, prepared),
            verify=verify_mesh_refinement_update,
        )
