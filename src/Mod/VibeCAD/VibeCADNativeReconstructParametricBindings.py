# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry bindings for mesh.reconstruct_parametric. Additive; old reverse tools stay."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeMeshReconstructParametricSchema import (
    MESH_RECONSTRUCT_PARAMETRIC_CAPABILITY_NAME,
)
from VibeCADNativeReconstructParametricRuntime import NativeReconstructParametricRuntime


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeReconstructParametricRuntime):
        raise TypeError(
            "A reconstruct_parametric call requires NativeReconstructParametricRuntime."
        )
    if not isinstance(arguments, Mapping):
        raise TypeError("A reconstruct_parametric call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_mesh_reconstruct_parametric_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            MESH_RECONSTRUCT_PARAMETRIC_CAPABILITY_NAME,
            _execute,
        )
    )


def reconstruct_parametric_runtime_bindings(
    runtime: NativeReconstructParametricRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeReconstructParametricRuntime):
        raise TypeError("runtime must be NativeReconstructParametricRuntime")
    return {MESH_RECONSTRUCT_PARAMETRIC_CAPABILITY_NAME: runtime}
