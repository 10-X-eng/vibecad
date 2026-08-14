# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for FEM thermal-condition mutations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeThermalCreate import (
    create_thermal_condition,
    prepare_thermal_create,
    verify_thermal_create,
)
from VibeCADNativeAnalyzeThermalEdit import (
    prepare_thermal_update,
    update_thermal_condition,
    verify_thermal_update,
)
from VibeCADNativeAnalyzeThermalValues import MODES, thermal_value_fields
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_CREATE = {f"create_{mode}": mode for mode in MODES}
_UPDATE = {f"update_{mode}": mode for mode in MODES}


def _create_fields(mode: str) -> set[str]:
    fields = {"analysis", "label", *thermal_value_fields(mode)}
    if mode != "initial_temperature":
        fields.add("references")
    return fields


def _arguments(arguments: Mapping[str, Any]) -> tuple[str, str, dict[str, Any]]:
    if not isinstance(arguments, Mapping):
        raise NativeAnalyzeError("Analyze thermal arguments must be one object.")
    values = dict(arguments)
    operation = str(values.pop("operation", "") or "")
    if operation in _CREATE:
        mode = _CREATE[operation]
        required = _create_fields(mode)
    elif operation in _UPDATE:
        mode = _UPDATE[operation]
        allowed = {"target"} | (_create_fields(mode) - {"analysis"})
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
                "Analyze thermal arguments do not match the selected operation"
                + (f": {'; '.join(details)}." if details else ".")
            )
        return operation, mode, values
    else:
        raise NativeAnalyzeError("The Analyze thermal operation is unavailable.")
    if set(values) != required:
        missing = sorted(required - set(values))
        extra = sorted(set(values) - required)
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"unexpected {', '.join(extra)}")
        raise NativeAnalyzeError(
            "Analyze thermal arguments do not match the selected operation"
            + (f": {'; '.join(details)}." if details else ".")
        )
    return operation, mode, values


def _create_values(mode: str, values: dict[str, Any]) -> dict[str, Any]:
    fields = thermal_value_fields(mode)
    prepared = dict(values)
    prepared["values"] = {field: prepared.pop(field) for field in fields}
    return prepared


class NativeAnalyzeThermalRuntime:
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
            prepared = prepare_thermal_create(
                context.document,
                context.document_uid,
                mode=mode,
                **_create_values(mode, values),
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name=f"Create FEM {mode.replace('_', ' ').title()}",
                mutate=lambda document: create_thermal_condition(document, prepared),
                verify=verify_thermal_create,
            )
        target = values.pop("target")
        prepared = prepare_thermal_update(
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
            mutate=lambda document: update_thermal_condition(document, prepared),
            verify=verify_thermal_update,
        )
