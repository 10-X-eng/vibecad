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
from VibeCADNativeState import NativeCallTicket
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

    def control(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        normalized = dict(arguments)
        if normalized.get("operation") == "show":
            normalized.setdefault("time_seconds", None)
            normalized.setdefault("mode", "hold")
        operation, values = strict_variant_arguments(
            normalized,
            {
                "show": frozenset(
                    {
                        "simulation",
                        "time_seconds",
                        "mode",
                    }
                ),
                "seek": frozenset({"playback_id", "time_seconds"}),
                "step": frozenset({"playback_id", "direction"}),
                "play": frozenset({"playback_id", "direction"}),
                "pause": frozenset({"playback_id"}),
                "close": frozenset({"playback_id"}),
            },
        )
        authorization = self._context.state.authorize_mutation(ticket)
        if authorization.duplicate:
            return dict(authorization.prior_verified_result or {})
        self._context.state.begin_mutation_observation(ticket)
        try:
            if operation == "show":
                result = open_native_assembly_playback(
                    self._context,
                    AssemblyPlaybackOpenSpec(
                        simulation_ref=_object_ref(
                            self._context.document_uid,
                            values["simulation"],
                            "simulation",
                        ),
                        time_seconds=values["time_seconds"],
                        mode=str(values["mode"]),
                    ),
                )
            else:
                result = control_native_assembly_playback(
                    self._context,
                    operation,
                    AssemblyPlaybackControlSpec(
                        playback_id=str(values["playback_id"]),
                        time_seconds=values.get("time_seconds"),
                        direction=(
                            None
                            if values.get("direction") is None
                            else str(values["direction"])
                        ),
                    ),
                )
            return result
        finally:
            self._context.state.cancel_mutation(ticket)
