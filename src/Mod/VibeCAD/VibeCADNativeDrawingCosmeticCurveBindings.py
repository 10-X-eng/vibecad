# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for Drawing cosmetic-curve creation."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeDrawingCosmeticCurveRuntime import (
    NativeDrawingCosmeticCurveRuntime,
)
from VibeCADNativeDrawingCosmeticCurveSchema import (
    DRAWING_COSMETIC_CURVE_CAPABILITY_NAME,
)


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeDrawingCosmeticCurveRuntime):
        raise TypeError("A cosmetic-curve call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A cosmetic-curve call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_drawing_cosmetic_curve_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            DRAWING_COSMETIC_CURVE_CAPABILITY_NAME,
            _execute,
        )
    )


def drawing_cosmetic_curve_runtime_bindings(
    runtime: NativeDrawingCosmeticCurveRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeDrawingCosmeticCurveRuntime):
        raise TypeError("runtime must be NativeDrawingCosmeticCurveRuntime")
    return {DRAWING_COSMETIC_CURVE_CAPABILITY_NAME: runtime}
