# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for FEM mechanical-load mutations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeLoadCreate import (
    create_load,
    prepare_load_create,
    verify_load_create,
)
from VibeCADNativeAnalyzeLoadEdit import prepare_load_update, update_load, verify_load_update
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_KINDS = ("force", "pressure", "centrifugal", "gravity")
_CREATE = {f"create_{kind}": kind for kind in _KINDS}
_UPDATE = {f"update_{kind}": kind for kind in _KINDS}


def _create_fields(kind: str) -> set[str]:
    fields = {"analysis", "label"}
    if kind == "force":
        fields.update({"references", "force_n", "direction"})
    elif kind == "pressure":
        fields.update({"references", "pressure_pa", "reversed"})
    elif kind == "centrifugal":
        fields.update({"rotation_frequency_hz", "axis", "scope"})
    else:
        fields.update({"acceleration_m_s2", "direction"})
    return fields


def _arguments(arguments: Mapping[str, Any]) -> tuple[str, str, dict[str, Any]]:
    if not isinstance(arguments, Mapping):
        raise NativeAnalyzeError("Analyze load arguments must be one object.")
    values = dict(arguments)
    operation = str(values.pop("operation", "") or "")
    if operation in _CREATE:
        kind = _CREATE[operation]
        required = _create_fields(kind)
    elif operation in _UPDATE:
        kind = _UPDATE[operation]
        allowed = {"target"} | set(_create_fields(kind)) - {"analysis"}
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
                "Analyze load arguments do not match the selected operation"
                + (f": {'; '.join(details)}." if details else ".")
            )
        return operation, kind, values
    else:
        raise NativeAnalyzeError("The Analyze load operation is unavailable.")
    if set(values) != required:
        missing = sorted(required - set(values))
        extra = sorted(set(values) - required)
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"unexpected {', '.join(extra)}")
        raise NativeAnalyzeError(
            "Analyze load arguments do not match the selected operation"
            + (f": {'; '.join(details)}." if details else ".")
        )
    return operation, kind, values


def _create_values(kind: str, values: dict[str, Any]) -> dict[str, Any]:
    prepared = dict(values)
    if kind == "force":
        direction = dict(prepared["direction"])
        prepared["load"] = {
            "force_n": prepared.pop("force_n"),
            "reversed": direction.pop("reversed"),
        }
        prepared["direction"] = direction
    elif kind == "pressure":
        prepared["load"] = {
            "pressure_pa": prepared.pop("pressure_pa"),
            "reversed": prepared.pop("reversed"),
        }
    elif kind == "centrifugal":
        prepared["load"] = {
            "rotation_frequency_hz": prepared.pop("rotation_frequency_hz")
        }
    else:
        prepared["load"] = {
            "acceleration_m_s2": prepared.pop("acceleration_m_s2"),
            "direction": prepared.pop("direction"),
        }
    return prepared


class NativeAnalyzeLoadRuntime:
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
            prepared = prepare_load_create(
                context.document,
                context.document_uid,
                kind=kind,
                **_create_values(kind, values),
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name=f"Create FEM {kind.title()} Load",
                mutate=lambda document: create_load(document, prepared),
                verify=verify_load_create,
            )
        target = values.pop("target")
        prepared = prepare_load_update(
            context.document,
            context.document_uid,
            kind=kind,
            target=target,
            changes=values,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name=f"Edit FEM {kind.title()} Load",
            mutate=lambda document: update_load(document, prepared),
            verify=verify_load_update,
        )
