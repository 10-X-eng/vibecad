# SPDX-License-Identifier: LGPL-2.1-or-later
"""Provider bindings for native asynchronous sheet creation."""

from collections.abc import Mapping

from VibeCADNativeCapabilityRegistry import NativeCapabilityImplementation
from VibeCADNativeSheetMetalCreateRuntime import NativeSheetMetalCreateRuntime
from VibeCADNativeSheetMetalCreateSchema import (
    SHEETMETAL_CREATE_CAPABILITY_NAME, sheetmetal_create_capability_definition,
)


def _invoke(call, *, asynchronous):
    runtime, arguments = getattr(call, "runtime", None), getattr(call, "arguments", None)
    if not isinstance(runtime, NativeSheetMetalCreateRuntime) or not isinstance(arguments, Mapping):
        raise TypeError("Sheet creation requires its exact runtime and argument data.")
    handler = runtime.execute_async if asynchronous else runtime.execute
    return handler(arguments, ticket=getattr(call, "ticket", None))


def _create(call):
    return _invoke(call, asynchronous=False)


def _create_async(call):
    return _invoke(call, asynchronous=True)


def register_sheetmetal_create(registry):
    registry.register_shared_definition(sheetmetal_create_capability_definition())
    registry.register_implementation(NativeCapabilityImplementation(
        SHEETMETAL_CREATE_CAPABILITY_NAME, _create, async_handler=_create_async))


def sheetmetal_create_runtime_bindings(runtime):
    if not isinstance(runtime, NativeSheetMetalCreateRuntime):
        raise TypeError("runtime must be a NativeSheetMetalCreateRuntime")
    return {SHEETMETAL_CREATE_CAPABILITY_NAME: runtime}
