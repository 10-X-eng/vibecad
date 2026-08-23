# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bind the focused face reader to the shared Analyze inspection runtime."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeFaceSchema import ANALYZE_FACE_CAPABILITY_NAME
from VibeCADNativeAnalyzeInspectRuntime import NativeAnalyzeInspectRuntime
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


def _read(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAnalyzeInspectRuntime):
        raise TypeError("An Analyze face call requires its exact inspection runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("An Analyze face call requires argument data.")
    values = dict(arguments)
    if values.pop("operation", None) != "read":
        raise ValueError("An Analyze face call requires the read operation.")
    values.setdefault("offset", 0)
    values.setdefault("page_size", 64)
    return runtime.inspect({"operation": "geometry_source", **values})


def register_analyze_face_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_FACE_CAPABILITY_NAME, _read)
    )


def analyze_face_runtime_bindings(
    runtime: NativeAnalyzeInspectRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAnalyzeInspectRuntime):
        raise TypeError("runtime must be a NativeAnalyzeInspectRuntime")
    return {ANALYZE_FACE_CAPABILITY_NAME: runtime}
