# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for Drawing section-view positioning."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeDrawingSectionPositionRuntime import (
    NativeDrawingSectionPositionRuntime,
)
from VibeCADNativeDrawingSectionPositionSchema import (
    DRAWING_SECTION_POSITION_CAPABILITY_NAME,
)


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeDrawingSectionPositionRuntime):
        raise TypeError("A Drawing section-position call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Drawing section-position call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_drawing_section_position_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            DRAWING_SECTION_POSITION_CAPABILITY_NAME,
            _execute,
        )
    )


def drawing_section_position_runtime_bindings(
    runtime: NativeDrawingSectionPositionRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeDrawingSectionPositionRuntime):
        raise TypeError("runtime must be a NativeDrawingSectionPositionRuntime")
    return {DRAWING_SECTION_POSITION_CAPABILITY_NAME: runtime}
