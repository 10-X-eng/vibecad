# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for current TechDraw line defaults."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeDrawingLineDefaults import read_drawing_line_defaults
from VibeCADNativeRuntimeContext import NativeRuntimeContext


class NativeDrawingLineDefaultsRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def read(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        operation, _values = strict_variant_arguments(
            arguments,
            {"read_current": frozenset()},
        )
        if operation != "read_current":
            raise ValueError("Unsupported Drawing line-defaults operation.")
        self._context.guard()
        return read_drawing_line_defaults(self._context.document)
