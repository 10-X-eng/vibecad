# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for Model dress-up operations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeDesignChamfer import (
    create_design_chamfer,
    preflight_design_chamfer,
    prepare_design_chamfer,
)
from VibeCADNativeDesignDraft import (
    create_design_draft,
    preflight_design_draft,
    prepare_design_draft,
)
from VibeCADNativeDesignFillet import (
    create_design_fillet,
    preflight_design_fillet,
    prepare_design_fillet,
)
from VibeCADNativeDesignThickness import (
    create_design_thickness,
    preflight_design_thickness,
    prepare_design_thickness,
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


_FILLET_FIELDS = frozenset({"label", "selection", "radius_mm", "stage", "preview_id"})
_CHAMFER_FIELDS = frozenset({"label", "selection", "definition"})
_DRAFT_FIELDS = frozenset(
    {
        "label",
        "selection",
        "angle_degrees",
        "neutral_plane",
        "pull_direction",
        "reversed",
    }
)
_THICKNESS_FIELDS = frozenset(
    {
        "label",
        "selection",
        "thickness_mm",
        "direction",
        "mode",
        "join",
        "intersection_handling",
    }
)


class NativeModelDressupRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def _maybe_preview_fillet(
        self, values: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        stage = str(values.get("stage") or "propose").strip()
        if stage == "apply":
            return None
        if stage != "propose":
            raise NativeModelError("model.dressup fillet stage must be propose or apply.")
        return self._context.state.propose_mutation_preview(
            self._context.document_uid,
            capability_name="model.dressup",
            arguments={"operation": "fillet", **dict(values)},
        )

    def _fillet_apply_values(self, values: Mapping[str, Any]) -> dict[str, Any]:
        stage = str(values.get("stage") or "propose").strip()
        if stage != "apply":
            return {
                name: value
                for name, value in values.items()
                if name not in {"stage", "preview_id"}
            }
        preview_id = str(values.get("preview_id") or "").strip()
        if not preview_id:
            raise NativeModelError("model.dressup fillet apply needs preview_id.")
        try:
            stored = self._context.state.consume_mutation_preview(
                self._context.document_uid,
                preview_id,
                capability_name="model.dressup",
            )
        except NativeRevisionConflict:
            raise
        except NativeStateError as exc:
            raise NativeModelError(str(exc)) from exc
        return {
            name: value
            for name, value in stored.items()
            if name not in {"stage", "preview_id", "operation"}
        }

    def mutate_dressup(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(
            arguments,
            {
                "fillet": _FILLET_FIELDS,
                "chamfer": _CHAMFER_FIELDS,
                "draft": _DRAFT_FIELDS,
                "thickness": _THICKNESS_FIELDS,
            },
        )
        label = str(values["label"] or "").strip()
        if not label or len(label) > 160:
            raise NativeModelError(
                f"A visible {operation.title()} label must contain 1 to 160 characters."
            )
        if operation == "fillet":
            previewed = self._maybe_preview_fillet(values)
            if previewed is not None:
                return previewed
            values = self._fillet_apply_values(values)
            prepared = prepare_design_fillet(self._context.document_uid, values)
            preflight = preflight_design_fillet
            create = create_design_fillet
        elif operation == "chamfer":
            prepared = prepare_design_chamfer(self._context.document_uid, values)
            preflight = preflight_design_chamfer
            create = create_design_chamfer
        elif operation == "draft":
            prepared = prepare_design_draft(self._context.document_uid, values)
            preflight = preflight_design_draft
            create = create_design_draft
        else:
            prepared = prepare_design_thickness(self._context.document_uid, values)
            preflight = preflight_design_thickness
            create = create_design_thickness
        self._context.guard()
        preflight(self._context.document, prepared)

        return run_immediate_mutation(
            self._context,
            ticket=ticket,
            transaction_name=f"Create Native Design {operation.title()}",
            mutate=lambda document: create(
                document,
                label=label,
                spec=prepared,
            ),
            verify=verify_design_operation,
        )
