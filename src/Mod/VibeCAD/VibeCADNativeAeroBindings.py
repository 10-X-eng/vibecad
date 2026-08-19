# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for Native aero.solve."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAeroRuntime import NativeAeroRuntime
from VibeCADNativeAeroSchema import AERO_SOLVE_CAPABILITY_NAME
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


def _solve(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAeroRuntime):
        raise TypeError("An Aero solve call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("An Aero solve call requires argument data.")
    return runtime.solve(arguments)


def register_aero_solve_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(AERO_SOLVE_CAPABILITY_NAME, _solve)
    )


def aero_solve_runtime_bindings(runtime: NativeAeroRuntime) -> dict[str, Any]:
    if not isinstance(runtime, NativeAeroRuntime):
        raise TypeError("runtime must be a NativeAeroRuntime")
    return {AERO_SOLVE_CAPABILITY_NAME: runtime}
