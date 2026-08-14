# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for Native Drawing complex sections."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeDrawingComplexSectionRuntime import (
    NativeDrawingComplexSectionRuntime,
)
from VibeCADNativeDrawingComplexSectionSchema import (
    DRAWING_COMPLEX_SECTION_CAPABILITY_NAME,
)


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeDrawingComplexSectionRuntime):
        raise TypeError("A Drawing complex-section call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Drawing complex-section call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_drawing_complex_section_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            DRAWING_COMPLEX_SECTION_CAPABILITY_NAME,
            _execute,
        )
    )


def drawing_complex_section_runtime_bindings(
    runtime: NativeDrawingComplexSectionRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeDrawingComplexSectionRuntime):
        raise TypeError("runtime must be a NativeDrawingComplexSectionRuntime")
    return {DRAWING_COMPLEX_SECTION_CAPABILITY_NAME: runtime}
