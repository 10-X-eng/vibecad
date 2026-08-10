# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Assembly simulation playback controls."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeAssemblyPlayback import (
    AssemblyPlaybackControlSpec,
    AssemblyPlaybackOpenSpec,
    NativeAssemblyPlaybackError,
    control_native_assembly_playback,
    open_native_assembly_playback,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeTargets import NativeObjectRef


def _object_ref(document_uid: str, value: Any, field: str) -> NativeObjectRef:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise NativeAssemblyPlaybackError(
            f"{field} must be one exact object reference."
        )
    try:
        return NativeObjectRef(document_uid, str(value["object_name"]))
    except Exception as exc:
        raise NativeAssemblyPlaybackError(str(exc)) from exc


class NativeAssemblyPlaybackRuntime:
    """Control only the exact simulation player owned by this Native document."""

    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def control(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        operation, values = strict_variant_arguments(
            arguments,
            {
                "open": frozenset(
                    {
                        "simulation",
                        "expected_simulation_state_sha256",
                        "time_seconds",
                        "mode",
                    }
                ),
                "seek": frozenset({"simulation", "playback_id", "time_seconds"}),
                "step": frozenset({"simulation", "playback_id", "direction"}),
                "play": frozenset({"simulation", "playback_id", "direction"}),
                "pause": frozenset({"simulation", "playback_id"}),
                "close": frozenset({"simulation", "playback_id"}),
            },
        )
        simulation_ref = _object_ref(
            self._context.document_uid,
            values["simulation"],
            "simulation",
        )
        if operation == "open":
            return open_native_assembly_playback(
                self._context,
                AssemblyPlaybackOpenSpec(
                    simulation_ref=simulation_ref,
                    expected_simulation_state_sha256=str(
                        values["expected_simulation_state_sha256"]
                    ),
                    time_seconds=values["time_seconds"],
                    mode=str(values["mode"]),
                ),
            )
        return control_native_assembly_playback(
            self._context,
            operation,
            AssemblyPlaybackControlSpec(
                simulation_ref=simulation_ref,
                playback_id=str(values["playback_id"]),
                time_seconds=values.get("time_seconds"),
                direction=(
                    None
                    if values.get("direction") is None
                    else str(values["direction"])
                ),
            ),
        )
