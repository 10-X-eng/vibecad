# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for Drawing output."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeDrawingExportRuntime import NativeDrawingExportRuntime
from VibeCADNativeDrawingExportSchema import DRAWING_EXPORT_CAPABILITY_NAME


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeDrawingExportRuntime):
        raise TypeError("A Drawing output call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Drawing output call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_drawing_export_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(DRAWING_EXPORT_CAPABILITY_NAME, _execute)
    )


def drawing_export_runtime_bindings(
    runtime: NativeDrawingExportRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeDrawingExportRuntime):
        raise TypeError("runtime must be a NativeDrawingExportRuntime")
    return {DRAWING_EXPORT_CAPABILITY_NAME: runtime}
