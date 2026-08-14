# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for exact bounded CAM probing grids."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeManufactureProbeRuntime import NativeManufactureProbeRuntime
from VibeCADNativeManufactureProbeSchema import MANUFACTURE_PROBE_CAPABILITY_NAME


def _probe(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    ticket = getattr(call, "ticket", None)
    if not isinstance(runtime, NativeManufactureProbeRuntime):
        raise TypeError("A CAM Probe call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A CAM Probe call requires argument data.")
    return runtime.probe(arguments, ticket=ticket)


def register_manufacture_probe_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(MANUFACTURE_PROBE_CAPABILITY_NAME, _probe)
    )


def manufacture_probe_runtime_bindings(
    runtime: NativeManufactureProbeRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeManufactureProbeRuntime):
        raise TypeError("runtime must be a NativeManufactureProbeRuntime")
    return {MANUFACTURE_PROBE_CAPABILITY_NAME: runtime}
