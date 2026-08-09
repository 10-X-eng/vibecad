# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for Native Assembly joint operations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAssemblyJointRuntime import NativeAssemblyJointRuntime
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


ASSEMBLY_JOINT_CAPABILITY_NAME = "assembly.joint"


def _runtime(call: Any) -> NativeAssemblyJointRuntime:
    runtime = getattr(call, "runtime", None)
    if not isinstance(runtime, NativeAssemblyJointRuntime):
        raise TypeError("An Assembly joint call requires its exact runtime.")
    return runtime


def _arguments(call: Any) -> Mapping[str, Any]:
    arguments = getattr(call, "arguments", None)
    if not isinstance(arguments, Mapping):
        raise TypeError("An Assembly joint call requires argument data.")
    return arguments


def _joint(call: Any) -> Mapping[str, Any]:
    return _runtime(call).mutate_joint(
        _arguments(call),
        ticket=getattr(call, "ticket", None),
    )


def register_assembly_joint_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            ASSEMBLY_JOINT_CAPABILITY_NAME,
            _joint,
        )
    )


def assembly_joint_runtime_bindings(
    runtime: NativeAssemblyJointRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAssemblyJointRuntime):
        raise TypeError("runtime must be a NativeAssemblyJointRuntime")
    return {ASSEMBLY_JOINT_CAPABILITY_NAME: runtime}
