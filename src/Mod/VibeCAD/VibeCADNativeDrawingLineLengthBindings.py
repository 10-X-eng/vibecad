# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for symmetric Drawing line resizing."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeDrawingLineLengthRuntime import (
    NativeDrawingLineLengthRuntime,
)
from VibeCADNativeDrawingLineLengthSchema import (
    DRAWING_LINE_LENGTH_CAPABILITY_NAME,
)


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeDrawingLineLengthRuntime):
        raise TypeError("A Drawing line-length call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Drawing line-length call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_drawing_line_length_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            DRAWING_LINE_LENGTH_CAPABILITY_NAME,
            _execute,
        )
    )


def drawing_line_length_runtime_bindings(
    runtime: NativeDrawingLineLengthRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeDrawingLineLengthRuntime):
        raise TypeError("runtime must be a NativeDrawingLineLengthRuntime")
    return {DRAWING_LINE_LENGTH_CAPABILITY_NAME: runtime}
