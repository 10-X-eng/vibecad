# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for one atomic Native Sketch batch."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchBatch import (
    create_sketch_batch,
    preflight_sketch_batch,
    verify_sketch_batch,
)
from VibeCADNativeSketchBatchPlan import prepare_sketch_batch
from VibeCADNativeState import NativeCallTicket


_OUTER_FIELDS = {
    "create": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "geometry",
            "constraints",
        }
    )
}


class NativeSketchBatchRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def create(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        _operation, values = strict_variant_arguments(arguments, _OUTER_FIELDS)
        spec = prepare_sketch_batch(self._context.document_uid, values)
        prepared = preflight_sketch_batch(self._context, spec)
        return run_immediate_mutation(
            self._context,
            ticket=ticket,
            transaction_name="Create Native Sketch Batch",
            mutate=lambda document: create_sketch_batch(document, prepared),
            verify=verify_sketch_batch,
        )
