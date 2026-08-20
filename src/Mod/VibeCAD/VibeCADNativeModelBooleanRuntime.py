# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for standalone Model boolean operations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeDesignCombine import (
    create_design_combine,
    preflight_design_combine,
    prepare_design_combine,
    verify_design_combine,
)
from VibeCADNativeDesignSplit import (
    create_design_split,
    preflight_design_split,
    prepare_design_split,
    verify_design_split,
)
from VibeCADNativePartSection import (
    create_part_section,
    preflight_part_section,
    prepare_part_section,
    verify_part_section,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import (
    NativeCallTicket,
    NativeRevisionConflict,
    NativeStateError,
)


_OUTER_FIELDS = {
    "section": frozenset({"label", "definition"}),
    "combine": frozenset({"label", "definition", "stage", "preview_id"}),
    "split": frozenset({"label", "definition"}),
}


class NativeModelBooleanRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def _maybe_preview_boolean(
        self, values: Mapping[str, Any], *, mode: str
    ) -> dict[str, Any] | None:
        stage = str(values.get("stage") or "propose").strip()
        if stage == "apply":
            return None
        if stage != "propose":
            raise NativeModelError(
                f"model.boolean {mode} stage must be propose or apply."
            )
        return self._context.state.propose_mutation_preview(
            self._context.document_uid,
            capability_name="model.boolean",
            arguments={"operation": "combine", **dict(values)},
        )

    def _boolean_apply_values(
        self, values: Mapping[str, Any], *, mode: str
    ) -> dict[str, Any]:
        stage = str(values.get("stage") or "propose").strip()
        if stage != "apply":
            return {
                name: value
                for name, value in values.items()
                if name not in {"stage", "preview_id"}
            }
        preview_id = str(values.get("preview_id") or "").strip()
        if not preview_id:
            raise NativeModelError(f"model.boolean {mode} apply needs preview_id.")
        try:
            stored = self._context.state.consume_mutation_preview(
                self._context.document_uid,
                preview_id,
                capability_name="model.boolean",
            )
        except NativeRevisionConflict:
            raise
        except NativeStateError as exc:
            raise NativeModelError(str(exc)) from exc
        definition = stored.get("definition")
        stored_mode = ""
        if isinstance(definition, Mapping):
            stored_mode = str(definition.get("mode") or "")
        if stored_mode != mode:
            raise NativeModelError(f"preview_id is not a boolean {mode} preview.")
        return {
            name: value
            for name, value in stored.items()
            if name not in {"stage", "preview_id", "operation"}
        }

    def mutate_boolean(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(arguments, _OUTER_FIELDS)
        label = str(values["label"] or "").strip()
        if not label or len(label) > 160:
            raise NativeModelError("A visible Boolean label must contain 1 to 160 characters.")
        if operation == "section":
            spec = prepare_part_section(
                self._context.document_uid,
                values["definition"],
            )
            self._context.guard()
            prepared = preflight_part_section(self._context.document, spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Part Section",
                mutate=lambda document: create_part_section(
                    document,
                    label=label,
                    prepared=prepared,
                ),
                verify=verify_part_section,
            )
        if operation == "combine":
            definition = values["definition"]
            mode = ""
            if isinstance(definition, Mapping):
                mode = str(definition.get("mode") or "")
            if mode in {"cut", "join"}:
                previewed = self._maybe_preview_boolean(values, mode=mode)
                if previewed is not None:
                    return previewed
                values = self._boolean_apply_values(values, mode=mode)
                definition = values["definition"]
            spec = prepare_design_combine(
                self._context.document_uid,
                definition,
            )
            self._context.guard()
            prepared = preflight_design_combine(self._context.document, spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Design Combine",
                mutate=lambda document: create_design_combine(
                    document,
                    label=label,
                    prepared=prepared,
                ),
                verify=verify_design_combine,
            )
        if operation == "split":
            spec = prepare_design_split(
                self._context.document_uid,
                values["definition"],
            )
            self._context.guard()
            prepared = preflight_design_split(self._context.document, spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Design Split",
                mutate=lambda document: create_design_split(
                    document,
                    label=label,
                    prepared=prepared,
                ),
                verify=verify_design_split,
            )
        raise NativeModelError("That standalone Boolean operation is unavailable.")
