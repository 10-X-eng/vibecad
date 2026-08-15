# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime bindings for exact CAM tool work."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeManufactureToolRuntime import (
    NativeManufactureToolCatalogRuntime,
    NativeManufactureToolRuntime,
)
from VibeCADNativeManufactureToolSchema import (
    MANUFACTURE_TOOL_CAPABILITY_NAME,
    MANUFACTURE_TOOL_CATALOG_CAPABILITY_NAME,
)


def _inspect(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeManufactureToolCatalogRuntime):
        raise TypeError("A CAM tool catalog call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A CAM tool catalog call requires argument data.")
    return runtime.inspect(arguments)


def _mutate(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    ticket = getattr(call, "ticket", None)
    if not isinstance(runtime, NativeManufactureToolRuntime):
        raise TypeError("A CAM tool mutation requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A CAM tool mutation requires argument data.")
    return runtime.mutate(arguments, ticket=ticket)


def register_manufacture_tool_capability_implementations(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            MANUFACTURE_TOOL_CATALOG_CAPABILITY_NAME,
            _inspect,
        )
    )
    registry.register_implementation(
        NativeCapabilityImplementation(MANUFACTURE_TOOL_CAPABILITY_NAME, _mutate)
    )


def manufacture_tool_runtime_bindings(
    catalog_runtime: NativeManufactureToolCatalogRuntime,
    mutation_runtime: NativeManufactureToolRuntime,
) -> dict[str, Any]:
    if not isinstance(catalog_runtime, NativeManufactureToolCatalogRuntime):
        raise TypeError("catalog_runtime must be NativeManufactureToolCatalogRuntime")
    if not isinstance(mutation_runtime, NativeManufactureToolRuntime):
        raise TypeError("mutation_runtime must be NativeManufactureToolRuntime")
    return {
        MANUFACTURE_TOOL_CATALOG_CAPABILITY_NAME: catalog_runtime,
        MANUFACTURE_TOOL_CAPABILITY_NAME: mutation_runtime,
    }
