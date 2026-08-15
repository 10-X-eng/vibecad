# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for persistent Drawing line attributes."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeDrawingLineAttributesRuntime import (
    NativeDrawingLineAttributesRuntime,
)
from VibeCADNativeDrawingLineAttributesSchema import (
    DRAWING_LINE_ATTRIBUTES_CAPABILITY_NAME,
)


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeDrawingLineAttributesRuntime):
        raise TypeError("A Drawing line-attributes call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Drawing line-attributes call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_drawing_line_attributes_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            DRAWING_LINE_ATTRIBUTES_CAPABILITY_NAME,
            _execute,
        )
    )


def drawing_line_attributes_runtime_bindings(
    runtime: NativeDrawingLineAttributesRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeDrawingLineAttributesRuntime):
        raise TypeError("runtime must be a NativeDrawingLineAttributesRuntime")
    return {DRAWING_LINE_ATTRIBUTES_CAPABILITY_NAME: runtime}
