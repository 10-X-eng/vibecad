# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry binding for exact bounded Native FEM reads."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeInspectRuntime import NativeAnalyzeInspectRuntime
from VibeCADNativeAnalyzeInspectSchema import (
    ANALYZE_INSPECT_CAPABILITY_NAME,
    ANALYZE_MATERIAL_CATALOG,
)
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


def _focused_material_catalog_result(result: Mapping[str, Any]) -> dict[str, Any]:
    materials = []
    for material in list(result.get("materials") or ()):
        if not isinstance(material, Mapping):
            raise TypeError("A material catalog result must contain material objects.")
        materials.append(
            {
                "material_name": str(material.get("name") or ""),
                "material_uuid": str(material.get("uuid") or ""),
                "category": str(material.get("category") or ""),
                "description": str(material.get("description") or ""),
                "properties": dict(material.get("properties") or {}),
            }
        )
    return {
        key: result[key]
        for key in ("query", "match_count", "returned_count", "truncated")
        if key in result
    } | {"materials": materials}


def _inspect(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAnalyzeInspectRuntime):
        raise TypeError("An Analyze inspection call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("An Analyze inspection call requires argument data.")
    return runtime.inspect(arguments)


def _material_catalog(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAnalyzeInspectRuntime):
        raise TypeError("A material catalog call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A material catalog call requires argument data.")
    values = dict(arguments)
    if values.pop("operation", None) != "search":
        raise ValueError("A material catalog call requires the search operation.")
    return _focused_material_catalog_result(
        runtime.inspect({"operation": "material_catalog", **values})
    )


def register_analyze_inspect_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_INSPECT_CAPABILITY_NAME, _inspect)
    )
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_MATERIAL_CATALOG, _material_catalog)
    )


def analyze_inspect_runtime_bindings(
    runtime: NativeAnalyzeInspectRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAnalyzeInspectRuntime):
        raise TypeError("runtime must be a NativeAnalyzeInspectRuntime")
    return {
        ANALYZE_INSPECT_CAPABILITY_NAME: runtime,
        ANALYZE_MATERIAL_CATALOG: runtime,
    }
