# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for FEM geometrical analysis feature mutations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeGeometricalCreate import (
    create_geometrical_feature,
    prepare_geometrical_create,
    verify_geometrical_create,
)
from VibeCADNativeAnalyzeGeometricalEdit import (
    prepare_geometrical_update,
    update_geometrical_feature,
    verify_geometrical_update,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_KINDS = ("plane_rotation", "section_print", "transform")
_CREATE = {f"create_{kind}": kind for kind in _KINDS}
_UPDATE = {f"update_{kind}": kind for kind in _KINDS}


def _create_fields(kind: str) -> set[str]:
    fields = {"analysis", "label", "face"}
    if kind == "section_print":
        fields.add("variable")
    elif kind == "transform":
        fields.add("coordinate_system")
    return fields


def _arguments(arguments: Mapping[str, Any]) -> tuple[str, str, dict[str, Any]]:
    if not isinstance(arguments, Mapping):
        raise NativeAnalyzeError(
            "Analyze geometrical-feature arguments must be one object."
        )
    values = dict(arguments)
    operation = str(values.pop("operation", "") or "")
    if operation in _CREATE:
        kind = _CREATE[operation]
        required = _create_fields(kind)
    elif operation in _UPDATE:
        kind = _UPDATE[operation]
        allowed = {"target"} | (_create_fields(kind) - {"analysis"})
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
                "Analyze geometrical-feature arguments do not match the selected operation"
                + (f": {'; '.join(details)}." if details else ".")
            )
        return operation, kind, values
    else:
        raise NativeAnalyzeError(
            "The Analyze geometrical-feature operation is unavailable."
        )
    if set(values) != required:
        missing = sorted(required - set(values))
        extra = sorted(set(values) - required)
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"unexpected {', '.join(extra)}")
        raise NativeAnalyzeError(
            "Analyze geometrical-feature arguments do not match the selected operation"
            + (f": {'; '.join(details)}." if details else ".")
        )
    return operation, kind, values


def _settings(kind: str, values: dict[str, Any]) -> Any:
    if kind == "section_print":
        return {"variable": values.pop("variable")}
    if kind == "transform":
        return {"coordinate_system": values.pop("coordinate_system")}
    return None


class NativeAnalyzeGeometricalRuntime:
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
            settings = _settings(kind, values)
            prepared = prepare_geometrical_create(
                context.document,
                context.document_uid,
                kind=kind,
                settings=settings,
                **values,
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name=(
                    f"Create FEM {kind.replace('_', ' ').title()}"
                ),
                mutate=lambda document: create_geometrical_feature(
                    document,
                    prepared,
                ),
                verify=verify_geometrical_create,
            )
        target = values.pop("target")
        prepared = prepare_geometrical_update(
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
            mutate=lambda document: update_geometrical_feature(document, prepared),
            verify=verify_geometrical_update,
        )
