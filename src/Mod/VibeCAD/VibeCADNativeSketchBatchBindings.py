# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for the atomic Native Sketch batch."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeSketchBatchRuntime import NativeSketchBatchRuntime


SKETCH_BATCH_CAPABILITY_NAME = "sketch.batch"


def _batch(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeSketchBatchRuntime):
        raise TypeError("A Sketch batch call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Sketch batch call requires argument data.")
    return runtime.create(arguments, ticket=getattr(call, "ticket", None))


def register_sketch_batch_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(SKETCH_BATCH_CAPABILITY_NAME, _batch)
    )


def sketch_batch_runtime_bindings(
    runtime: NativeSketchBatchRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeSketchBatchRuntime):
        raise TypeError("runtime must be a NativeSketchBatchRuntime")
    return {SKETCH_BATCH_CAPABILITY_NAME: runtime}
