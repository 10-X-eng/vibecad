# SPDX-License-Identifier: LGPL-2.1-or-later

from VibeCADNativeCapabilityRegistry import NativeCapabilityImplementation
from VibeCADNativeSheetMetalManufacturingRuntime import NativeSheetMetalManufacturingRuntime
from VibeCADNativeSheetMetalManufacturingSchema import NAME, sheetmetal_manufacturing_capability_definition


def _execute(call, asynchronous=False):
    if not isinstance(call.runtime, NativeSheetMetalManufacturingRuntime):
        raise TypeError("The manufacturing call requires its native runtime")
    return call.runtime.execute(call.arguments, asynchronous=asynchronous)


def _execute_async(call):
    return _execute(call, asynchronous=True)


def register_sheetmetal_manufacturing(registry):
    registry.register_shared_definition(sheetmetal_manufacturing_capability_definition())
    registry.register_implementation(NativeCapabilityImplementation(NAME, _execute, async_handler=_execute_async))


def sheetmetal_manufacturing_runtime_bindings(runtime):
    if not isinstance(runtime, NativeSheetMetalManufacturingRuntime):
        raise TypeError("runtime must be a NativeSheetMetalManufacturingRuntime")
    return {NAME: runtime}
