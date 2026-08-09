# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for shared Native Model catalog reads."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeModelCatalogRuntime import NativeModelCatalogRuntime


MODEL_CATALOG_CAPABILITY_NAME = "model.catalog"


def _catalog(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeModelCatalogRuntime):
        raise TypeError("A Model catalog call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Model catalog call requires argument data.")
    return runtime.read_catalog(arguments)


def register_model_catalog_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(MODEL_CATALOG_CAPABILITY_NAME, _catalog)
    )


def model_catalog_runtime_bindings(
    runtime: NativeModelCatalogRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeModelCatalogRuntime):
        raise TypeError("runtime must be a NativeModelCatalogRuntime")
    return {MODEL_CATALOG_CAPABILITY_NAME: runtime}
