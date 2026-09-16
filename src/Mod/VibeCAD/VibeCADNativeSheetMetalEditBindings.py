# SPDX-License-Identifier: LGPL-2.1-or-later
"""Native registry bindings preserve the document-thread Future completion path."""

from collections.abc import Mapping

from VibeCADNativeCapabilityRegistry import NativeCapabilityImplementation
from VibeCADNativeSheetMetalEditRuntime import NativeSheetMetalEditRuntime
from VibeCADNativeSheetMetalEditSchema import (
    SHEETMETAL_EDIT_CAPABILITY_NAME, sheetmetal_edit_capability_definition,
)


def _invoke(call, *, asynchronous):
    runtime, arguments = getattr(call, "runtime", None), getattr(call, "arguments", None)
    if not isinstance(runtime, NativeSheetMetalEditRuntime) or not isinstance(arguments, Mapping):
        raise TypeError("A sheet edit requires its exact runtime and argument data.")
    handler = runtime.execute_async if asynchronous else runtime.execute
    return handler(arguments, ticket=getattr(call, "ticket", None))


def _edit(call):
    return _invoke(call, asynchronous=False)


def _edit_async(call):
    return _invoke(call, asynchronous=True)


def register_sheetmetal_edit(registry):
    registry.register_shared_definition(sheetmetal_edit_capability_definition())
    registry.register_implementation(NativeCapabilityImplementation(
        SHEETMETAL_EDIT_CAPABILITY_NAME, _edit, async_handler=_edit_async))


def sheetmetal_edit_runtime_bindings(runtime):
    if not isinstance(runtime, NativeSheetMetalEditRuntime):
        raise TypeError("runtime must be a NativeSheetMetalEditRuntime")
    return {SHEETMETAL_EDIT_CAPABILITY_NAME: runtime}
