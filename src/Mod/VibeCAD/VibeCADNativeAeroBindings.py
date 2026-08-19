# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for Native aero.* families."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAeroRuntime import NativeAeroRuntime
from VibeCADNativeAeroSchema import (
    AERO_EXPORT_CAPABILITY_NAME,
    AERO_INSPECT_CAPABILITY_NAME,
    AERO_SOLVE_CAPABILITY_NAME,
)
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


def _solve(call: Any) -> Mapping[str, Any]:
    return _require_runtime(call).solve(
        _require_arguments(call),
        ticket=call.ticket,
    )


def _export(call: Any) -> Mapping[str, Any]:
    return _require_runtime(call).export(
        _require_arguments(call),
        ticket=call.ticket,
    )


def _inspect(call: Any) -> Mapping[str, Any]:
    return _require_runtime(call).inspect(
        _require_arguments(call),
        ticket=call.ticket,
    )


def _require_runtime(call: Any) -> NativeAeroRuntime:
    runtime = getattr(call, "runtime", None)
    if not isinstance(runtime, NativeAeroRuntime):
        raise TypeError("An Aero call requires NativeAeroRuntime.")
    return runtime


def _require_arguments(call: Any) -> Mapping[str, Any]:
    arguments = getattr(call, "arguments", None)
    if not isinstance(arguments, Mapping):
        raise TypeError("An Aero call requires argument data.")
    return arguments


def register_aero_solve_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(AERO_SOLVE_CAPABILITY_NAME, _solve)
    )
    registry.register_implementation(
        NativeCapabilityImplementation(AERO_EXPORT_CAPABILITY_NAME, _export)
    )
    registry.register_implementation(
        NativeCapabilityImplementation(AERO_INSPECT_CAPABILITY_NAME, _inspect)
    )


def aero_solve_runtime_bindings(runtime: NativeAeroRuntime) -> dict[str, Any]:
    if not isinstance(runtime, NativeAeroRuntime):
        raise TypeError("runtime must be a NativeAeroRuntime")
    return {
        AERO_SOLVE_CAPABILITY_NAME: runtime,
        AERO_EXPORT_CAPABILITY_NAME: runtime,
        AERO_INSPECT_CAPABILITY_NAME: runtime,
    }
