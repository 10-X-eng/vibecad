# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for grouped Drawing dimension series."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeDrawingDimensionSeriesRuntime import (
    NativeDrawingDimensionSeriesRuntime,
)
from VibeCADNativeDrawingDimensionSeriesSchema import (
    DRAWING_DIMENSION_SERIES_CAPABILITY_NAME,
)


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeDrawingDimensionSeriesRuntime):
        raise TypeError("A Drawing dimension-series call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Drawing dimension-series call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_drawing_dimension_series_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            DRAWING_DIMENSION_SERIES_CAPABILITY_NAME,
            _execute,
        )
    )


def drawing_dimension_series_runtime_bindings(
    runtime: NativeDrawingDimensionSeriesRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeDrawingDimensionSeriesRuntime):
        raise TypeError("runtime must be a NativeDrawingDimensionSeriesRuntime")
    return {DRAWING_DIMENSION_SERIES_CAPABILITY_NAME: runtime}
