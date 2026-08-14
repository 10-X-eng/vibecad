# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for exact CAM ToolBit output."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeManufactureToolOutputRuntime import (
    NativeManufactureToolOutputRuntime,
)
from VibeCADNativeManufactureToolOutputSchema import (
    MANUFACTURE_TOOL_OUTPUT_CAPABILITY_NAME,
)


def _export(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    ticket = getattr(call, "ticket", None)
    if not isinstance(runtime, NativeManufactureToolOutputRuntime):
        raise TypeError("A CAM ToolBit output call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A CAM ToolBit output call requires argument data.")
    return runtime.export(arguments, ticket)


def register_manufacture_tool_output_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            MANUFACTURE_TOOL_OUTPUT_CAPABILITY_NAME,
            _export,
        )
    )


def manufacture_tool_output_runtime_bindings(
    runtime: NativeManufactureToolOutputRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeManufactureToolOutputRuntime):
        raise TypeError("runtime must be a NativeManufactureToolOutputRuntime")
    return {MANUFACTURE_TOOL_OUTPUT_CAPABILITY_NAME: runtime}
