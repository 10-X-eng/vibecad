# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Native Drawing clip groups."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeDrawingClip import (
    mutate_clip_group,
    prepare_clip_mutation,
    restore_clip_selection,
    verify_clip_mutation,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


class NativeDrawingClipRuntime:
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
        operation, values = strict_variant_arguments(
            arguments,
            {
                "create_clip_group": frozenset(
                    {"page", "label", "position_on_page_mm", "frame", "members"}
                ),
                "add_views": frozenset({"page", "clip_group", "members"}),
                "remove_views": frozenset({"page", "clip_group", "members"}),
                "configure_clip_group": frozenset(
                    {"page", "clip_group", "label", "position_on_page_mm", "frame"}
                ),
            },
        )
        context = self._context
        context.guard()
        prepared = prepare_clip_mutation(
            context.document,
            operation=operation,
            values=values,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name={
                "create_clip_group": "Create Native Drawing Clip Group",
                "add_views": "Add Native Drawing Clip Views",
                "remove_views": "Remove Native Drawing Clip Views",
                "configure_clip_group": "Configure Native Drawing Clip Group",
            }[operation],
            mutate=partial(mutate_clip_group, prepared=prepared),
            verify=verify_clip_mutation,
            after_abort=lambda document: restore_clip_selection(
                document,
                prepared.selection_before,
            ),
        )
