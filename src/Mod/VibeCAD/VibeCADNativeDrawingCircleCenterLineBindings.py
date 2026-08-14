# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for Drawing circle centerlines."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeDrawingCircleCenterLineRuntime import (
    NativeDrawingCircleCenterLineRuntime,
)
from VibeCADNativeDrawingCircleCenterLineSchema import (
    DRAWING_CIRCLE_CENTER_LINE_CAPABILITY_NAME,
)


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeDrawingCircleCenterLineRuntime):
        raise TypeError("A circle centerline call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A circle centerline call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_drawing_circle_center_line_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            DRAWING_CIRCLE_CENTER_LINE_CAPABILITY_NAME,
            _execute,
        )
    )


def drawing_circle_center_line_runtime_bindings(
    runtime: NativeDrawingCircleCenterLineRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeDrawingCircleCenterLineRuntime):
        raise TypeError("runtime must be NativeDrawingCircleCenterLineRuntime")
    return {DRAWING_CIRCLE_CENTER_LINE_CAPABILITY_NAME: runtime}
