# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for Native Assembly standard fasteners."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAssemblyFastenerRuntime import NativeAssemblyFastenerRuntime
from VibeCADNativeAssemblyFastenerSchema import (
    ASSEMBLY_FASTENER_CAPABILITY_NAME,
    ASSEMBLY_FASTENER_EDIT_CAPABILITY_NAME,
)
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


def _mutate(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    ticket = getattr(call, "ticket", None)
    if not isinstance(runtime, NativeAssemblyFastenerRuntime):
        raise TypeError("An Assembly fastener call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("An Assembly fastener call requires argument data.")
    return runtime.mutate_fastener(arguments, ticket=ticket)


def register_assembly_fastener_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for capability_name in (
        ASSEMBLY_FASTENER_CAPABILITY_NAME,
        ASSEMBLY_FASTENER_EDIT_CAPABILITY_NAME,
    ):
        registry.register_implementation(
            NativeCapabilityImplementation(
                capability_name,
                _mutate,
            )
        )


def assembly_fastener_runtime_bindings(
    runtime: NativeAssemblyFastenerRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAssemblyFastenerRuntime):
        raise TypeError("runtime must be a NativeAssemblyFastenerRuntime")
    return {
        ASSEMBLY_FASTENER_CAPABILITY_NAME: runtime,
        ASSEMBLY_FASTENER_EDIT_CAPABILITY_NAME: runtime,
    }
