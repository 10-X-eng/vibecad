# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for Native Assembly BOM creation."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAssemblyBomRuntime import NativeAssemblyBomRuntime
from VibeCADNativeAssemblyBomSchema import ASSEMBLY_BOM_CAPABILITY_NAME
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


def _create(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAssemblyBomRuntime):
        raise TypeError("An Assembly BOM call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("An Assembly BOM call requires argument data.")
    return runtime.create(arguments, ticket=getattr(call, "ticket", None))


def register_assembly_bom_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(ASSEMBLY_BOM_CAPABILITY_NAME, _create)
    )


def assembly_bom_runtime_bindings(
    runtime: NativeAssemblyBomRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAssemblyBomRuntime):
        raise TypeError("runtime must be a NativeAssemblyBomRuntime")
    return {ASSEMBLY_BOM_CAPABILITY_NAME: runtime}
