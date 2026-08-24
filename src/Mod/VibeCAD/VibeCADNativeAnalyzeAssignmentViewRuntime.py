# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for FEM assignment presentation."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeAssignmentView import (
    highlight_assignment,
    isolate_assignment,
    restore_assignment_view,
)
from VibeCADNativeAnalyzeAssignments import prepare_assignment_target
from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeRuntimeContext import NativeRuntimeContext


_VARIANTS = {
    "highlight": frozenset({"analysis", "assignment"}),
    "isolate": frozenset({"analysis", "assignment"}),
    "restore": frozenset({"restore_token"}),
}


class NativeAnalyzeAssignmentViewRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def execute(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        operation, values = strict_variant_arguments(arguments, _VARIANTS)
        context = self._context
        context.guard()
        if operation == "restore":
            result = restore_assignment_view(context.document, values["restore_token"])
        else:
            target = prepare_assignment_target(
                context.document,
                context.document_uid,
                analysis=values["analysis"],
                assignment=values["assignment"],
            )
            result = (
                highlight_assignment(target)
                if operation == "highlight"
                else isolate_assignment(target)
            )
        context.guard()
        return result
