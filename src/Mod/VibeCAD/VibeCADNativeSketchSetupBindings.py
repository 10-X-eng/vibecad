# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry binding for reusable Sketch setup."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeSketchSetupRuntime import NativeSketchSetupRuntime


CAPABILITY_NAME = "sketch.setup"


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeSketchSetupRuntime):
        raise TypeError("Sketch setup requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("Sketch setup requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_sketch_setup_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(CAPABILITY_NAME, _execute)
    )


def sketch_setup_runtime_bindings(
    runtime: NativeSketchSetupRuntime,
) -> dict[str, NativeSketchSetupRuntime]:
    if not isinstance(runtime, NativeSketchSetupRuntime):
        raise TypeError("runtime must be a NativeSketchSetupRuntime")
    return {CAPABILITY_NAME: runtime}
