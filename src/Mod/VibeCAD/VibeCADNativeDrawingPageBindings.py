# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for Native Drawing page operations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeDrawingPageRuntime import NativeDrawingPageRuntime
from VibeCADNativeDrawingPageSchema import DRAWING_PAGE_CAPABILITY_NAME


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeDrawingPageRuntime):
        raise TypeError("A Drawing page call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Drawing page call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_drawing_page_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(DRAWING_PAGE_CAPABILITY_NAME, _execute)
    )


def drawing_page_runtime_bindings(
    runtime: NativeDrawingPageRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeDrawingPageRuntime):
        raise TypeError("runtime must be a NativeDrawingPageRuntime")
    return {DRAWING_PAGE_CAPABILITY_NAME: runtime}
