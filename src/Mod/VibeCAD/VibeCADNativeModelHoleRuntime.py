# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for Design Hole and its live catalog."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeDesignHole import (
    create_design_hole,
    preflight_design_hole,
    prepare_design_hole,
)
from VibeCADNativeDesignResults import verify_design_operation
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import (
    NativeCallTicket,
    NativeRevisionConflict,
    NativeStateError,
)


_HOLE_FIELDS = frozenset(
    {
        "label",
        "profile",
        "base_profile",
        "hole_type",
        "head",
        "depth",
        "drill_point",
        "taper",
        "reversed",
        "targets",
    }
)
_HOLE_PREVIEW_FIELDS = ("stage", "preview_id")


class NativeModelHoleRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def _maybe_preview_hole(
        self, values: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        stage = str(values.get("stage") or "propose").strip()
        if stage == "apply":
            return None
        if stage != "propose":
            raise NativeModelError("model.hole stage must be propose or apply.")
        return self._context.state.propose_mutation_preview(
            self._context.document_uid,
            capability_name="model.hole",
            arguments={"operation": "hole", **dict(values)},
        )

    def _hole_apply_values(self, values: Mapping[str, Any]) -> dict[str, Any]:
        stage = str(values.get("stage") or "propose").strip()
        if stage != "apply":
            return {
                name: value
                for name, value in values.items()
                if name not in {"stage", "preview_id"}
            }
        preview_id = str(values.get("preview_id") or "").strip()
        if not preview_id:
            raise NativeModelError("model.hole apply needs preview_id.")
        try:
            stored = self._context.state.consume_mutation_preview(
                self._context.document_uid,
                preview_id,
                capability_name="model.hole",
            )
        except NativeRevisionConflict:
            raise
        except NativeStateError as exc:
            raise NativeModelError(str(exc)) from exc
        if str(stored.get("operation") or "hole") != "hole":
            raise NativeModelError("preview_id is not a hole preview.")
        return {
            name: value
            for name, value in stored.items()
            if name not in {"stage", "preview_id", "operation"}
        }

    def mutate_hole(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        raw = dict(arguments)
        preview_fields = {}
        for name in _HOLE_PREVIEW_FIELDS:
            if name in raw:
                preview_fields[name] = raw.pop(name)
        _operation, values = strict_variant_arguments(
            raw,
            {"hole": _HOLE_FIELDS},
        )
        if preview_fields:
            values = {**values, **preview_fields}
        label = str(values["label"] or "").strip()
        if not label or len(label) > 160:
            raise NativeModelError("A visible Hole label must contain 1 to 160 characters.")
        previewed = self._maybe_preview_hole(values)
        if previewed is not None:
            return previewed
        values = self._hole_apply_values(values)
        prepared = prepare_design_hole(
            self._context.document_uid,
            values,
        )
        self._context.guard()
        preflight_design_hole(self._context.document, prepared)

        return run_immediate_mutation(
            self._context,
            ticket=ticket,
            transaction_name="Create Native Design Hole",
            mutate=lambda document: create_design_hole(
                document,
                label=label,
                spec=prepared,
            ),
            verify=verify_design_operation,
        )
