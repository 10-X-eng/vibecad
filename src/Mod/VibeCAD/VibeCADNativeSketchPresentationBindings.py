# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for exact Sketch presentation operations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeSketchPresentationRuntime import NativeSketchPresentationRuntime


SKETCH_PRESENTATION_CAPABILITY_NAME = "sketch.presentation"


def _present(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeSketchPresentationRuntime):
        raise TypeError("A Sketch presentation call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Sketch presentation call requires argument data.")
    return runtime.present(arguments)


def register_sketch_presentation_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(SKETCH_PRESENTATION_CAPABILITY_NAME, _present)
    )


def sketch_presentation_runtime_bindings(
    runtime: NativeSketchPresentationRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeSketchPresentationRuntime):
        raise TypeError("runtime must be a NativeSketchPresentationRuntime")
    return {SKETCH_PRESENTATION_CAPABILITY_NAME: runtime}
