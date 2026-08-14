# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for Drawing format customization."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeDrawingFormatRuntime import NativeDrawingFormatRuntime
from VibeCADNativeDrawingFormatSchema import DRAWING_FORMAT_CAPABILITY_NAME


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeDrawingFormatRuntime):
        raise TypeError("A Drawing format call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Drawing format call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_drawing_format_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(DRAWING_FORMAT_CAPABILITY_NAME, _execute)
    )


def drawing_format_runtime_bindings(
    runtime: NativeDrawingFormatRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeDrawingFormatRuntime):
        raise TypeError("runtime must be a NativeDrawingFormatRuntime")
    return {DRAWING_FORMAT_CAPABILITY_NAME: runtime}
