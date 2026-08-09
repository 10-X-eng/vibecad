# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Sketch edit control."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchLeave import leave_sketch_edit, prepare_sketch_leave
from VibeCADNativeSketchTargets import prepare_active_sketch_target
from VibeCADNativeState import NativeCallTicket
from VibeCADNativeTargets import object_identity


_LEAVE_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
    }
)


class NativeSketchControlRuntime:
    """Finish one exact Sketch task in a frozen Sketch turn."""

    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def control(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        _operation, values = strict_variant_arguments(
            arguments,
            {"leave": _LEAVE_FIELDS},
        )
        if not isinstance(ticket, NativeCallTicket):
            raise TypeError("ticket must be a NativeCallTicket")
        spec = prepare_active_sketch_target(
            self._context.document_uid,
            sketch=values["sketch"],
            expected_geometry_count=values["expected_geometry_count"],
            expected_constraint_count=values["expected_constraint_count"],
        )
        prepared = prepare_sketch_leave(self._context, spec)
        authorization = self._context.state.authorize_mutation(ticket)
        if authorization.duplicate:
            return dict(authorization.prior_verified_result or {})

        self._context.state.begin_mutation_observation(ticket)
        try:
            result = leave_sketch_edit(self._context, prepared)
            revision_after = self._context.state.commit_mutation_observation(ticket)
            changed = (
                (object_identity(prepared.target.sketch),)
                if revision_after > ticket.expected_revision
                else ()
            )
            completion = self._context.state.prepare_mutation_completion(
                ticket,
                result,
                changed=changed,
            )
            receipt = self._context.state.complete_prepared_mutation(completion)
        except Exception:
            self._context.state.cancel_mutation(ticket)
            raise
        return {**result, "receipt": receipt.summary()}
