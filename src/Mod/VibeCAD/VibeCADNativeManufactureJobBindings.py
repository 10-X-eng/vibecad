# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for atomic CAM Job creation."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeManufactureJobRuntime import NativeManufactureJobRuntime
from VibeCADNativeManufactureJobSchema import MANUFACTURE_JOB_CAPABILITY_NAME


def _mutate(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    ticket = getattr(call, "ticket", None)
    if not isinstance(runtime, NativeManufactureJobRuntime):
        raise TypeError("A CAM Job call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A CAM Job call requires argument data.")
    return runtime.mutate_job(arguments, ticket=ticket)


def register_manufacture_job_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(MANUFACTURE_JOB_CAPABILITY_NAME, _mutate)
    )


def manufacture_job_runtime_bindings(
    runtime: NativeManufactureJobRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeManufactureJobRuntime):
        raise TypeError("runtime must be a NativeManufactureJobRuntime")
    return {MANUFACTURE_JOB_CAPABILITY_NAME: runtime}
