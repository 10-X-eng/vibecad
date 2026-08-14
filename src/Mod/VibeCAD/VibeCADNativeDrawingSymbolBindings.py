# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for Drawing engineering symbols."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeDrawingSymbolRuntime import NativeDrawingSymbolRuntime
from VibeCADNativeDrawingSymbolSchema import DRAWING_SYMBOL_CAPABILITY_NAME


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeDrawingSymbolRuntime):
        raise TypeError("A Drawing symbol call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Drawing symbol call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_drawing_symbol_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(DRAWING_SYMBOL_CAPABILITY_NAME, _execute)
    )


def drawing_symbol_runtime_bindings(
    runtime: NativeDrawingSymbolRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeDrawingSymbolRuntime):
        raise TypeError("runtime must be a NativeDrawingSymbolRuntime")
    return {DRAWING_SYMBOL_CAPABILITY_NAME: runtime}
