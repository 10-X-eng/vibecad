# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry bindings for Visual Inspection comparison."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeInspectionCompareRuntime import NativeInspectionCompareRuntime
from VibeCADNativeInspectionCompareSchema import INSPECTION_COMPARE_CAPABILITY_NAME


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    if not isinstance(runtime, NativeInspectionCompareRuntime):
        raise TypeError("An Inspection comparison call requires its native runtime.")
    arguments = getattr(call, "arguments", None)
    if not isinstance(arguments, Mapping):
        raise TypeError("An Inspection comparison call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_inspection_compare_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(INSPECTION_COMPARE_CAPABILITY_NAME, _execute)
    )


def inspection_compare_runtime_bindings(
    runtime: NativeInspectionCompareRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeInspectionCompareRuntime):
        raise TypeError("runtime must be a NativeInspectionCompareRuntime")
    return {INSPECTION_COMPARE_CAPABILITY_NAME: runtime}
