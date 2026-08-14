# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for Drawing cosmetic-line creation."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeDrawingCosmeticLineRuntime import (
    NativeDrawingCosmeticLineRuntime,
)
from VibeCADNativeDrawingCosmeticLineSchema import (
    DRAWING_COSMETIC_LINE_CAPABILITY_NAME,
)


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeDrawingCosmeticLineRuntime):
        raise TypeError("A cosmetic-line call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A cosmetic-line call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_drawing_cosmetic_line_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            DRAWING_COSMETIC_LINE_CAPABILITY_NAME,
            _execute,
        )
    )


def drawing_cosmetic_line_runtime_bindings(
    runtime: NativeDrawingCosmeticLineRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeDrawingCosmeticLineRuntime):
        raise TypeError("runtime must be NativeDrawingCosmeticLineRuntime")
    return {DRAWING_COSMETIC_LINE_CAPABILITY_NAME: runtime}
