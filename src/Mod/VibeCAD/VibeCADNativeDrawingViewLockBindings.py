# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for exact Drawing view position locks."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeDrawingViewLockRuntime import NativeDrawingViewLockRuntime
from VibeCADNativeDrawingViewLockSchema import DRAWING_VIEW_LOCK_CAPABILITY_NAME


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeDrawingViewLockRuntime):
        raise TypeError("A Drawing view-lock call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Drawing view-lock call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_drawing_view_lock_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(DRAWING_VIEW_LOCK_CAPABILITY_NAME, _execute)
    )


def drawing_view_lock_runtime_bindings(
    runtime: NativeDrawingViewLockRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeDrawingViewLockRuntime):
        raise TypeError("runtime must be a NativeDrawingViewLockRuntime")
    return {DRAWING_VIEW_LOCK_CAPABILITY_NAME: runtime}
