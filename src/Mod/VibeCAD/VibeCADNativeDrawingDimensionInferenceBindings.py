# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for Drawing dimension inference."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeDrawingDimensionInferenceRuntime import (
    NativeDrawingDimensionInferenceRuntime,
)
from VibeCADNativeDrawingDimensionInferenceSchema import (
    DRAWING_DIMENSION_INFERENCE_CAPABILITY_NAME,
)


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeDrawingDimensionInferenceRuntime):
        raise TypeError("A Drawing dimension-inference call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Drawing dimension-inference call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_drawing_dimension_inference_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            DRAWING_DIMENSION_INFERENCE_CAPABILITY_NAME,
            _execute,
        )
    )


def drawing_dimension_inference_runtime_bindings(
    runtime: NativeDrawingDimensionInferenceRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeDrawingDimensionInferenceRuntime):
        raise TypeError("runtime must be a NativeDrawingDimensionInferenceRuntime")
    return {DRAWING_DIMENSION_INFERENCE_CAPABILITY_NAME: runtime}
