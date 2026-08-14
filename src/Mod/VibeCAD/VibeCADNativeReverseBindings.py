# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry bindings for Reverse Engineering capabilities."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeMeshApproximateSchema import MESH_APPROXIMATE_CAPABILITY_NAME
from VibeCADNativeMeshRebuildSchema import MESH_REBUILD_CAPABILITY_NAME
from VibeCADNativeReverseRuntime import NativeReverseRuntime


def _execute(expected_name: str, call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeReverseRuntime) or runtime.capability_name != expected_name:
        raise TypeError("A Reverse Engineering call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Reverse Engineering call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_reverse_capability_implementations(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for name in (MESH_REBUILD_CAPABILITY_NAME, MESH_APPROXIMATE_CAPABILITY_NAME):
        registry.register_implementation(
            NativeCapabilityImplementation(
                name,
                lambda call, expected=name: _execute(expected, call),
            )
        )


def reverse_runtime_bindings(
    rebuild: NativeReverseRuntime,
    approximate: NativeReverseRuntime,
) -> dict[str, Any]:
    if not isinstance(rebuild, NativeReverseRuntime) or not isinstance(
        approximate, NativeReverseRuntime
    ):
        raise TypeError("Reverse Engineering runtimes must be NativeReverseRuntime instances")
    return {
        MESH_REBUILD_CAPABILITY_NAME: rebuild,
        MESH_APPROXIMATE_CAPABILITY_NAME: approximate,
    }
