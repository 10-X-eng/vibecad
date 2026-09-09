# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry binding for Native Assembly simulation playback."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAssemblyPlaybackRuntime import NativeAssemblyPlaybackRuntime
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


ASSEMBLY_PLAYBACK_CAPABILITY_NAME = "assembly.playback"


def _playback(call: Any) -> Mapping[str, Any]:
    return _invoke_playback(call, asynchronous=False)


def _playback_async(call: Any):
    return _invoke_playback(call, asynchronous=True)


def _invoke_playback(call: Any, *, asynchronous):
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAssemblyPlaybackRuntime):
        raise TypeError("An Assembly playback call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("An Assembly playback call requires argument data.")
    handler = runtime.control_async if asynchronous else runtime.control
    return handler(arguments, ticket=getattr(call, "ticket", None))


def register_assembly_playback_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            ASSEMBLY_PLAYBACK_CAPABILITY_NAME,
            _playback,
            async_handler=_playback_async,
        )
    )


def assembly_playback_runtime_bindings(
    runtime: NativeAssemblyPlaybackRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAssemblyPlaybackRuntime):
        raise TypeError("runtime must be a NativeAssemblyPlaybackRuntime")
    return {ASSEMBLY_PLAYBACK_CAPABILITY_NAME: runtime}
