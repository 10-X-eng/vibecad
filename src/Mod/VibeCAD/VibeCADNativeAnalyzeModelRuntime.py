# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for concise FEM analysis and material mutations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeAnalysis import (
    create_analysis,
    prepare_analysis_create,
    verify_analysis_create,
)
from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeMaterialCreate import (
    create_material,
    create_nonlinear_material,
    prepare_material_create,
    prepare_nonlinear_create,
    verify_material_create,
    verify_nonlinear_create,
)
from VibeCADNativeAnalyzeMaterialEdit import (
    prepare_material_update,
    update_material,
    verify_material_update,
)
from VibeCADNativeAnalyzeStudyEdit import (
    prepare_study_update,
    update_study_intent,
    verify_study_update,
)
from VibeCADNativeAnalyzeSolidDomain import (
    create_solid_domain,
    prepare_solid_domain,
    verify_solid_domain,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_MATERIAL_UPDATE_FIELDS = frozenset(
    {
        "label",
        "references",
        "material_uuid",
        "clear_material_uuid",
        "properties",
        "clear_properties",
        "reinforcement_uuid",
        "clear_reinforcement_uuid",
        "reinforcement_properties",
        "clear_reinforcement_properties",
        "model",
        "yield_points",
    }
)

_FIELDS = {
    "create_solid_domain": (
        frozenset({"sources", "interface_mode", "label"}),
        frozenset({"sources", "interface_mode", "label"}),
    ),
    "create_analysis": (
        frozenset({"label", "default_solver_policy"}),
        frozenset({"label", "default_solver_policy", "study"}),
    ),
    "update_study": (
        frozenset({"target", "study"}),
        frozenset({"target", "study"}),
    ),
    "create_solid_material": (
        frozenset({"analysis", "label", "references"}),
        frozenset({"analysis", "label", "references", "material_uuid", "properties"}),
    ),
    "create_fluid_material": (
        frozenset({"analysis", "label", "references"}),
        frozenset({"analysis", "label", "references", "material_uuid", "properties"}),
    ),
    "create_reinforced_material": (
        frozenset({"analysis", "label", "references"}),
        frozenset(
            {
                "analysis",
                "label",
                "references",
                "material_uuid",
                "properties",
                "reinforcement_uuid",
                "reinforcement_properties",
            }
        ),
    ),
    "create_nonlinear_material": (
        frozenset({"base_material", "label", "model", "yield_points"}),
        frozenset({"base_material", "label", "model", "yield_points"}),
    ),
    "update_material": (
        frozenset({"target"}),
        frozenset({"target"}) | _MATERIAL_UPDATE_FIELDS,
    ),
}
_KINDS = {
    "create_solid_material": "solid",
    "create_fluid_material": "fluid",
    "create_reinforced_material": "reinforced",
}


def _arguments(arguments: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    if not isinstance(arguments, Mapping):
        raise NativeAnalyzeError("Analyze model arguments must be one object.")
    values = dict(arguments)
    operation = str(values.pop("operation", "") or "")
    contract = _FIELDS.get(operation)
    if contract is None:
        raise NativeAnalyzeError("The Analyze model operation is unavailable.")
    required, allowed = contract
    if not required <= set(values) <= allowed:
        missing = sorted(required - set(values))
        extra = sorted(set(values) - allowed)
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"unexpected {', '.join(extra)}")
        raise NativeAnalyzeError(
            "Analyze model arguments do not match the selected operation"
            + (f": {'; '.join(details)}." if details else ".")
        )
    if operation == "update_material" and not (set(values) & _MATERIAL_UPDATE_FIELDS):
        raise NativeAnalyzeError(
            "Analyze model arguments do not match the selected operation: "
            "missing at least one editable material field."
        )
    return operation, values


class NativeAnalyzeModelRuntime:
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
        if operation == "create_solid_domain":
            prepared = prepare_solid_domain(
                context.document,
                context.document_uid,
                **values,
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Create Solid Analysis Domain",
                mutate=lambda document: create_solid_domain(document, prepared),
                verify=verify_solid_domain,
            )
        if operation == "create_analysis":
            prepared = prepare_analysis_create(context.document, **values)
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Create FEM Analysis",
                mutate=lambda document: create_analysis(document, prepared),
                verify=verify_analysis_create,
            )
        if operation == "update_study":
            prepared = prepare_study_update(
                context.document,
                context.document_uid,
                **values,
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Edit FEM Study",
                mutate=lambda document: update_study_intent(document, prepared),
                verify=verify_study_update,
            )
        if operation in _KINDS:
            prepared = prepare_material_create(
                context.document,
                context.document_uid,
                kind=_KINDS[operation],
                **values,
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name=f"Create {prepared.kind.title()} FEM Material",
                mutate=lambda document: create_material(document, prepared),
                verify=verify_material_create,
            )
        if operation == "create_nonlinear_material":
            prepared = prepare_nonlinear_create(
                context.document,
                context.document_uid,
                **values,
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Create Nonlinear FEM Material",
                mutate=lambda document: create_nonlinear_material(document, prepared),
                verify=verify_nonlinear_create,
            )
        target = values.pop("target")
        prepared = prepare_material_update(
            context.document,
            context.document_uid,
            target=target,
            changes=values,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name="Edit FEM Material",
            mutate=lambda document: update_material(document, prepared),
            verify=verify_material_update,
        )
