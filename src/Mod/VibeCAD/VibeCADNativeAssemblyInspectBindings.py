# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for Native Assembly inspection reads."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAssemblyInspectRuntime import NativeAssemblyInspectRuntime
from VibeCADNativeAssemblyInspectSchema import ASSEMBLY_INSPECT_CAPABILITY_NAME
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


def _inspect(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAssemblyInspectRuntime):
        raise TypeError("An Assembly inspection call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("An Assembly inspection call requires argument data.")
    return runtime.inspect(arguments)


def register_assembly_inspect_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            ASSEMBLY_INSPECT_CAPABILITY_NAME,
            _inspect,
        )
    )


def assembly_inspect_runtime_bindings(
    runtime: NativeAssemblyInspectRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAssemblyInspectRuntime):
        raise TypeError("runtime must be a NativeAssemblyInspectRuntime")
    return {ASSEMBLY_INSPECT_CAPABILITY_NAME: runtime}
