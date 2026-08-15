# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for Native Assembly output."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAssemblyExportRuntime import NativeAssemblyExportRuntime
from VibeCADNativeAssemblyExportSchema import ASSEMBLY_EXPORT_CAPABILITY_NAME
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


def _export(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    ticket = getattr(call, "ticket", None)
    if not isinstance(runtime, NativeAssemblyExportRuntime):
        raise TypeError("An Assembly export call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("An Assembly export call requires argument data.")
    return runtime.export(arguments, ticket)


def register_assembly_export_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            ASSEMBLY_EXPORT_CAPABILITY_NAME,
            _export,
        )
    )


def assembly_export_runtime_bindings(
    runtime: NativeAssemblyExportRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAssemblyExportRuntime):
        raise TypeError("runtime must be a NativeAssemblyExportRuntime")
    return {ASSEMBLY_EXPORT_CAPABILITY_NAME: runtime}
