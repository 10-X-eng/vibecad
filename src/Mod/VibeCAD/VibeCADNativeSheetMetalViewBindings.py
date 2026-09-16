# SPDX-License-Identifier: LGPL-2.1-or-later
"""Registry and exact document runtime bindings for folded/flat controls."""

from collections.abc import Mapping

from VibeCADNativeCapabilityRegistry import NativeCapabilityImplementation
from VibeCADNativeSheetMetalViewRuntime import NativeSheetMetalViewRuntime
from VibeCADNativeSheetMetalViewSchema import (
    SHEETMETAL_VIEW_CAPABILITY_NAME, sheetmetal_view_capability_definition,
)


def _view(call):
    runtime, arguments = getattr(call, "runtime", None), getattr(call, "arguments", None)
    if not isinstance(runtime, NativeSheetMetalViewRuntime):
        raise TypeError("A sheet presentation call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A sheet presentation call requires argument data.")
    return runtime.execute(arguments)


def register_sheetmetal_view(registry):
    registry.register_shared_definition(sheetmetal_view_capability_definition())
    registry.register_implementation(NativeCapabilityImplementation(SHEETMETAL_VIEW_CAPABILITY_NAME, _view))


def sheetmetal_view_runtime_bindings(runtime):
    if not isinstance(runtime, NativeSheetMetalViewRuntime):
        raise TypeError("runtime must be a NativeSheetMetalViewRuntime")
    return {SHEETMETAL_VIEW_CAPABILITY_NAME: runtime}
