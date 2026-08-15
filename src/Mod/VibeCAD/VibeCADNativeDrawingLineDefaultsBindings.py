# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for TechDraw line-default reads."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeDrawingLineDefaultsRuntime import (
    NativeDrawingLineDefaultsRuntime,
)
from VibeCADNativeDrawingLineDefaultsSchema import (
    DRAWING_LINE_DEFAULTS_CAPABILITY_NAME,
)


def _read(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeDrawingLineDefaultsRuntime):
        raise TypeError("A Drawing line-defaults call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Drawing line-defaults call requires argument data.")
    return runtime.read(arguments)


def register_drawing_line_defaults_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            DRAWING_LINE_DEFAULTS_CAPABILITY_NAME,
            _read,
        )
    )


def drawing_line_defaults_runtime_bindings(
    runtime: NativeDrawingLineDefaultsRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeDrawingLineDefaultsRuntime):
        raise TypeError("runtime must be a NativeDrawingLineDefaultsRuntime")
    return {DRAWING_LINE_DEFAULTS_CAPABILITY_NAME: runtime}
