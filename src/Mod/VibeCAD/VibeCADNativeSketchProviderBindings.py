# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bindings for the compact Native Sketch provider surface."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeSketchProviderRuntime import NativeSketchProviderRuntime
from VibeCADNativeSketchProviderSchema import SKETCH_PROVIDER_CAPABILITY_NAMES


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    ticket = getattr(call, "ticket", None)
    if not isinstance(runtime, NativeSketchProviderRuntime):
        raise TypeError("A Sketch provider call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Sketch provider call requires argument data.")
    capability_name = str(getattr(ticket, "capability_name", "") or "")
    return runtime.execute(capability_name, arguments, ticket=ticket)


def register_sketch_provider_capability_implementations(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for name in sorted(SKETCH_PROVIDER_CAPABILITY_NAMES):
        registry.register_implementation(NativeCapabilityImplementation(name, _execute))


def sketch_provider_runtime_bindings(
    runtime: NativeSketchProviderRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeSketchProviderRuntime):
        raise TypeError("runtime must be a NativeSketchProviderRuntime")
    return {name: runtime for name in SKETCH_PROVIDER_CAPABILITY_NAMES}
