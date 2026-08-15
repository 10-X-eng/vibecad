# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for contextual Sketch constraints."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeSketchConstraintRuntime import NativeSketchConstraintRuntime


SKETCH_CONSTRAINT_CAPABILITY_NAME = "sketch.constraint"


def _constraint(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeSketchConstraintRuntime):
        raise TypeError("A Sketch constraint call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Sketch constraint call requires argument data.")
    return runtime.mutate_constraint(arguments, ticket=getattr(call, "ticket", None))


def register_sketch_constraint_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(SKETCH_CONSTRAINT_CAPABILITY_NAME, _constraint)
    )


def sketch_constraint_runtime_bindings(
    runtime: NativeSketchConstraintRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeSketchConstraintRuntime):
        raise TypeError("runtime must be a NativeSketchConstraintRuntime")
    return {SKETCH_CONSTRAINT_CAPABILITY_NAME: runtime}
