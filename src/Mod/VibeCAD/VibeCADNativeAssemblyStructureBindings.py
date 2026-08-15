# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for Native Assembly structure operations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAssemblyStructureRuntime import NativeAssemblyStructureRuntime
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


ASSEMBLY_STRUCTURE_CAPABILITY_NAME = "assembly.structure"


def _runtime(call: Any) -> NativeAssemblyStructureRuntime:
    runtime = getattr(call, "runtime", None)
    if not isinstance(runtime, NativeAssemblyStructureRuntime):
        raise TypeError("An Assembly structure call requires its exact runtime.")
    return runtime


def _arguments(call: Any) -> Mapping[str, Any]:
    arguments = getattr(call, "arguments", None)
    if not isinstance(arguments, Mapping):
        raise TypeError("An Assembly structure call requires argument data.")
    return arguments


def _structure(call: Any) -> Mapping[str, Any]:
    return _runtime(call).mutate_structure(
        _arguments(call),
        ticket=getattr(call, "ticket", None),
    )


def register_assembly_structure_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            ASSEMBLY_STRUCTURE_CAPABILITY_NAME,
            _structure,
        )
    )


def assembly_structure_runtime_bindings(
    runtime: NativeAssemblyStructureRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAssemblyStructureRuntime):
        raise TypeError("runtime must be a NativeAssemblyStructureRuntime")
    return {ASSEMBLY_STRUCTURE_CAPABILITY_NAME: runtime}
