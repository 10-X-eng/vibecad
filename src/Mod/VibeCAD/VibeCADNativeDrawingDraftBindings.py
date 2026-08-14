# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for Native Drawing Draft views."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeDrawingDraftRuntime import NativeDrawingDraftRuntime
from VibeCADNativeDrawingDraftSchema import DRAWING_DRAFT_CAPABILITY_NAME


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeDrawingDraftRuntime):
        raise TypeError("A Drawing Draft call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Drawing Draft call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_drawing_draft_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(DRAWING_DRAFT_CAPABILITY_NAME, _execute)
    )


def drawing_draft_runtime_bindings(
    runtime: NativeDrawingDraftRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeDrawingDraftRuntime):
        raise TypeError("runtime must be a NativeDrawingDraftRuntime")
    return {DRAWING_DRAFT_CAPABILITY_NAME: runtime}
