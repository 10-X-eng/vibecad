# SPDX-License-Identifier: LGPL-2.1-or-later

from VibeCADNativeCapabilityRegistry import NativeCapabilityImplementation
from VibeCADNativeSheetMetalConnectionRuntime import NativeSheetMetalConnectionRuntime
from VibeCADNativeSheetMetalConnectionSchema import NAME, sheetmetal_connection_capability_definition


def _execute(call, asynchronous=False):
    if not isinstance(call.runtime, NativeSheetMetalConnectionRuntime):
        raise TypeError("The connection call requires its native runtime")
    return call.runtime.execute(call.arguments, asynchronous=asynchronous)


def _execute_async(call):
    return _execute(call, asynchronous=True)


def register_sheetmetal_connection(registry):
    registry.register_shared_definition(sheetmetal_connection_capability_definition())
    registry.register_implementation(NativeCapabilityImplementation(NAME, _execute, async_handler=_execute_async))


def sheetmetal_connection_runtime_bindings(runtime):
    if not isinstance(runtime, NativeSheetMetalConnectionRuntime):
        raise TypeError("runtime must be a NativeSheetMetalConnectionRuntime")
    return {NAME: runtime}
