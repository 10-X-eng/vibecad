# SPDX-License-Identifier: LGPL-2.1-or-later

"""Execution bindings for the five shared Native capability families."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeCommonRuntime import NativeCommonRuntime


COMMON_NATIVE_CAPABILITY_NAMES = (
    "state.read",
    "view.control",
    "inspect.query",
    "drawing.sources",
    "drawing.projected_geometry",
    "document.save",
    "document.undo",
)


def _runtime(call: Any) -> NativeCommonRuntime:
    value = getattr(call, "runtime", None)
    if not isinstance(value, NativeCommonRuntime):
        raise TypeError("A common Native call requires NativeCommonRuntime.")
    return value


def _arguments(call: Any) -> Mapping[str, Any]:
    value = getattr(call, "arguments", None)
    if not isinstance(value, Mapping):
        raise TypeError("A Native capability call requires argument data.")
    return value


def _read_state(call: Any) -> Mapping[str, Any]:
    return _runtime(call).read_state(_arguments(call))


def _control_view(call: Any) -> Mapping[str, Any]:
    return _runtime(call).control_view(_arguments(call))


def _inspect(call: Any) -> Mapping[str, Any]:
    return _runtime(call).inspect(_arguments(call))


def _drawing_sources(call: Any) -> Mapping[str, Any]:
    return _runtime(call).read_drawing_sources(_arguments(call))


def _projected_geometry(call: Any) -> Mapping[str, Any]:
    return _runtime(call).read_projected_geometry(_arguments(call))


def _save(call: Any) -> Mapping[str, Any]:
    return _runtime(call).save_document(_arguments(call))


def _undo(call: Any) -> Mapping[str, Any]:
    return _runtime(call).undo_document(
        _arguments(call),
        ticket=getattr(call, "ticket", None),
    )


_COMMON_HANDLERS = {
    "state.read": _read_state,
    "view.control": _control_view,
    "inspect.query": _inspect,
    "drawing.sources": _drawing_sources,
    "drawing.projected_geometry": _projected_geometry,
    "document.save": _save,
    "document.undo": _undo,
}


def register_common_capability_implementations(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for name in COMMON_NATIVE_CAPABILITY_NAMES:
        registry.register_implementation(
            NativeCapabilityImplementation(name, _COMMON_HANDLERS[name])
        )


def common_runtime_bindings(runtime: NativeCommonRuntime) -> dict[str, Any]:
    if not isinstance(runtime, NativeCommonRuntime):
        raise TypeError("runtime must be a NativeCommonRuntime")
    return {name: runtime for name in COMMON_NATIVE_CAPABILITY_NAMES}
