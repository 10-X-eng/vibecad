# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for Native Assembly structure operations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAssemblyStructureRuntime import NativeAssemblyStructureRuntime
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


ASSEMBLY_CREATE_CAPABILITY_NAME = "assembly.create"
ASSEMBLY_INSERT_CAPABILITY_NAME = "assembly.insert"
ASSEMBLY_STRUCTURE_CAPABILITY_NAME = "assembly.structure"
ASSEMBLY_NEW_PART_CAPABILITY_NAME = "assembly.new_part"
ASSEMBLY_RIGIDITY_CAPABILITY_NAME = "assembly.rigidity"
ASSEMBLY_SOLVE_CAPABILITY_NAME = "assembly.solve"
ASSEMBLY_EXPLODED_VIEW_CAPABILITY_NAME = "assembly.exploded_view"
ASSEMBLY_MOTION_STUDY_CAPABILITY_NAME = "assembly.motion_study"
ASSEMBLY_STRUCTURE_CAPABILITY_NAMES = (
    ASSEMBLY_CREATE_CAPABILITY_NAME,
    ASSEMBLY_INSERT_CAPABILITY_NAME,
    ASSEMBLY_NEW_PART_CAPABILITY_NAME,
    ASSEMBLY_RIGIDITY_CAPABILITY_NAME,
    ASSEMBLY_SOLVE_CAPABILITY_NAME,
    ASSEMBLY_EXPLODED_VIEW_CAPABILITY_NAME,
    ASSEMBLY_MOTION_STUDY_CAPABILITY_NAME,
)


def _runtime(call: Any) -> NativeAssemblyStructureRuntime:
    runtime = getattr(call, "runtime", None)
    if not isinstance(runtime, NativeAssemblyStructureRuntime):
        raise TypeError("An Assembly structure call requires its exact runtime.")
    return runtime


def _arguments(call: Any) -> Mapping[str, Any]:
    arguments = getattr(call, "arguments", None)
    if not isinstance(arguments, Mapping):
        raise TypeError("An Assembly structure call requires argument data.")
    return arguments


def _structure(call: Any) -> Mapping[str, Any]:
    return _runtime(call).mutate_structure(
        _arguments(call),
        ticket=getattr(call, "ticket", None),
    )


def register_assembly_structure_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for name in ASSEMBLY_STRUCTURE_CAPABILITY_NAMES:
        registry.register_implementation(
            NativeCapabilityImplementation(
                name,
                _structure,
            )
        )


def assembly_structure_runtime_bindings(
    runtime: NativeAssemblyStructureRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAssemblyStructureRuntime):
        raise TypeError("runtime must be a NativeAssemblyStructureRuntime")
    return {name: runtime for name in ASSEMBLY_STRUCTURE_CAPABILITY_NAMES}
