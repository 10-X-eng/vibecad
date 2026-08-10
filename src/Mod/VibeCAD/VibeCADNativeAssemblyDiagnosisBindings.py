# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for Native Assembly diagnosis reads."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAssemblyDiagnosisRuntime import NativeAssemblyDiagnosisRuntime
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME = "assembly.diagnose"


def _diagnose(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAssemblyDiagnosisRuntime):
        raise TypeError("An Assembly diagnosis call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("An Assembly diagnosis call requires argument data.")
    return runtime.diagnose(arguments)


def register_assembly_diagnosis_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME,
            _diagnose,
        )
    )


def assembly_diagnosis_runtime_bindings(
    runtime: NativeAssemblyDiagnosisRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAssemblyDiagnosisRuntime):
        raise TypeError("runtime must be a NativeAssemblyDiagnosisRuntime")
    return {ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME: runtime}
