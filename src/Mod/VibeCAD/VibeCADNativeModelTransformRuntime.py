# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for Model transformation operations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeDesignMirror import (
    create_design_mirror,
    preflight_design_mirror,
    prepare_design_mirror,
    verify_design_mirror,
)
from VibeCADNativeDesignScale import (
    create_design_scale,
    preflight_design_scale,
    prepare_design_scale,
)
from VibeCADNativeDesignResults import verify_design_operation
from VibeCADNativeDesignLinearPattern import (
    create_design_linear_pattern,
    preflight_design_linear_pattern,
    prepare_design_linear_pattern,
    verify_design_linear_pattern,
)
from VibeCADNativeDesignCircularPattern import (
    create_design_circular_pattern,
    preflight_design_circular_pattern,
    prepare_design_circular_pattern,
    verify_design_circular_pattern,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import (
    NativeCallTicket,
    NativeRevisionConflict,
    NativeStateError,
)


_FIELDS = {
    "pattern": frozenset({"label", "source", "definition"}),
    "scale": frozenset({"label", "targets", "definition", "stage", "preview_id"}),
}


class NativeModelTransformRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def _maybe_preview_scale(
        self, values: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        stage = str(values.get("stage") or "propose").strip()
        if stage == "apply":
            return None
        if stage != "propose":
            raise NativeModelError("model.transform scale stage must be propose or apply.")
        return self._context.state.propose_mutation_preview(
            self._context.document_uid,
            capability_name="model.transform",
            arguments={"operation": "scale", **dict(values)},
        )

    def _scale_apply_values(self, values: Mapping[str, Any]) -> dict[str, Any]:
        stage = str(values.get("stage") or "propose").strip()
        if stage != "apply":
            return {
                name: value
                for name, value in values.items()
                if name not in {"stage", "preview_id"}
            }
        preview_id = str(values.get("preview_id") or "").strip()
        if not preview_id:
            raise NativeModelError("model.transform scale apply needs preview_id.")
        try:
            stored = self._context.state.consume_mutation_preview(
                self._context.document_uid,
                preview_id,
                capability_name="model.transform",
            )
        except NativeRevisionConflict:
            raise
        except NativeStateError as exc:
            raise NativeModelError(str(exc)) from exc
        if str(stored.get("operation") or "scale") != "scale":
            raise NativeModelError("preview_id is not a scale preview.")
        return {
            name: value
            for name, value in stored.items()
            if name not in {"stage", "preview_id", "operation"}
        }

    def mutate_transform(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(
            arguments,
            _FIELDS,
        )
        label = str(values["label"] or "").strip()
        if not label or len(label) > 160:
            raise NativeModelError(
                "A visible Design transform label must contain 1 to 160 characters."
            )
        if operation == "scale":
            previewed = self._maybe_preview_scale(values)
            if previewed is not None:
                return previewed
            values = self._scale_apply_values(values)
            spec = prepare_design_scale(self._context.document_uid, values)
            self._context.guard()
            prepared = preflight_design_scale(self._context.document, spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Design Scale",
                mutate=lambda document: create_design_scale(
                    document,
                    label=label,
                    prepared=prepared,
                ),
                verify=verify_design_operation,
            )
        definition = values.get("definition")
        kind = str(definition.get("kind") or "") if isinstance(definition, Mapping) else ""
        if kind == "mirror":
            prepared = prepare_design_mirror(self._context.document_uid, values)
            preflight = preflight_design_mirror
            create = create_design_mirror
            verify = verify_design_mirror
            transaction_name = "Create Native Design Mirror"
        elif kind == "linear":
            prepared = prepare_design_linear_pattern(self._context.document_uid, values)
            preflight = preflight_design_linear_pattern
            create = create_design_linear_pattern
            verify = verify_design_linear_pattern
            transaction_name = "Create Native Design Linear Pattern"
        elif kind == "circular":
            prepared = prepare_design_circular_pattern(
                self._context.document_uid,
                values,
            )
            preflight = preflight_design_circular_pattern
            create = create_design_circular_pattern
            verify = verify_design_circular_pattern
            transaction_name = "Create Native Design Circular Pattern"
        else:
            raise NativeModelError("A Design Pattern kind is unavailable.")
        self._context.guard()
        preflight(self._context.document, prepared)
        return run_immediate_mutation(
            self._context,
            ticket=ticket,
            transaction_name=transaction_name,
            mutate=lambda document: create(
                document,
                label=label,
                spec=prepared,
            ),
            verify=verify,
        )
