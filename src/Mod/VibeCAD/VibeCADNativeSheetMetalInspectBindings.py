# SPDX-License-Identifier: LGPL-2.1-or-later
"""Native registry and document runtime bindings for sheet inspection."""

from collections.abc import Mapping

from VibeCADNativeCapabilityRegistry import NativeCapabilityImplementation
from VibeCADNativeSheetMetalInspectRuntime import NativeSheetMetalInspectRuntime
from VibeCADNativeSheetMetalInspectSchema import (
    SHEETMETAL_INSPECT_CAPABILITY_NAME, sheetmetal_inspect_capability_definition,
)


def _inspect(call):
    runtime, arguments = getattr(call, "runtime", None), getattr(call, "arguments", None)
    if not isinstance(runtime, NativeSheetMetalInspectRuntime):
        raise TypeError("A sheet inspection call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A sheet inspection call requires argument data.")
    return runtime.inspect(arguments)


def _inspect_async(call):
    runtime, arguments = getattr(call, "runtime", None), getattr(call, "arguments", None)
    if not isinstance(runtime, NativeSheetMetalInspectRuntime) or not isinstance(arguments, Mapping):
        raise TypeError("A sheet inspection call requires its exact runtime and argument data.")
    return runtime.inspect_async(arguments)


def register_sheetmetal_inspect(registry):
    registry.register_shared_definition(sheetmetal_inspect_capability_definition())
    registry.register_implementation(NativeCapabilityImplementation(
        SHEETMETAL_INSPECT_CAPABILITY_NAME, _inspect, async_handler=_inspect_async))


def sheetmetal_inspect_runtime_bindings(runtime):
    if not isinstance(runtime, NativeSheetMetalInspectRuntime):
        raise TypeError("runtime must be a NativeSheetMetalInspectRuntime")
    return {SHEETMETAL_INSPECT_CAPABILITY_NAME: runtime}
