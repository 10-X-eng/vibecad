# SPDX-License-Identifier: LGPL-2.1-or-later

"""Binding for explicit Native ribbon transitions."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeWorkspaceRuntime import NativeWorkspaceRuntime


WORKSPACE_CAPABILITY_NAME = "workspace.switch"


def _switch(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeWorkspaceRuntime):
        raise TypeError("A workspace call requires NativeWorkspaceRuntime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A workspace call requires argument data.")
    return runtime.switch(arguments)


def register_workspace_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(WORKSPACE_CAPABILITY_NAME, _switch)
    )


def workspace_runtime_bindings(runtime: NativeWorkspaceRuntime) -> dict[str, Any]:
    if not isinstance(runtime, NativeWorkspaceRuntime):
        raise TypeError("runtime must be a NativeWorkspaceRuntime")
    return {WORKSPACE_CAPABILITY_NAME: runtime}

