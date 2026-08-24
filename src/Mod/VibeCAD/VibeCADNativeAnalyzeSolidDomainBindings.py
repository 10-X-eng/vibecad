# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bind multipart solid-domain creation to the shared Analyze runtime."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeCurrentTargets import current_target
from VibeCADNativeAnalyzeModelRuntime import NativeAnalyzeModelRuntime
from VibeCADNativeAnalyzeSolidDomainSchema import ANALYZE_SOLID_DOMAIN
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAnalyzeModelRuntime):
        raise TypeError("A solid-domain call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A solid-domain call requires argument data.")
    values = dict(arguments)
    if values.pop("operation", None) != "create":
        raise ValueError("A solid-domain call requires the create operation.")
    source_names = values.pop("source_names")
    request = {
        "operation": "create_solid_domain",
        "sources": [
            current_target(runtime, name, mesh_object_state)
            for name in source_names
        ],
        "interface_mode": values.pop("interface_mode"),
        "label": values.pop("label", "Solid analysis domain"),
    }
    if values:
        raise ValueError("A solid-domain call contains unsupported arguments.")
    return runtime.execute(request, ticket=getattr(call, "ticket", None))


def register_analyze_solid_domain_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_SOLID_DOMAIN, _execute)
    )


def analyze_solid_domain_runtime_bindings(
    runtime: NativeAnalyzeModelRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAnalyzeModelRuntime):
        raise TypeError("runtime must be a NativeAnalyzeModelRuntime")
    return {ANALYZE_SOLID_DOMAIN: runtime}
