# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime bindings for Model structure capability families."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeModelStructureRuntime import NativeModelStructureRuntime


MODEL_STRUCTURE_CAPABILITY_NAMES = (
    "model.structure",
    "model.sketch",
    "model.revolution_sketch",
    "sketch.open",
    "sketch.validate",
)


def _runtime(call: Any) -> NativeModelStructureRuntime:
    runtime = getattr(call, "runtime", None)
    if not isinstance(runtime, NativeModelStructureRuntime):
        raise TypeError("A Model structure call requires its exact runtime.")
    return runtime


def _arguments(call: Any) -> Mapping[str, Any]:
    arguments = getattr(call, "arguments", None)
    if not isinstance(arguments, Mapping):
        raise TypeError("A Model structure call requires argument data.")
    return arguments


def _structure(call: Any) -> Mapping[str, Any]:
    return _runtime(call).mutate_structure(
        _arguments(call),
        ticket=getattr(call, "ticket", None),
    )


def _sketch(call: Any) -> Mapping[str, Any]:
    return _runtime(call).create_sketch(
        _arguments(call),
        ticket=getattr(call, "ticket", None),
    )


def _revolution_sketch(call: Any) -> Mapping[str, Any]:
    arguments = dict(_arguments(call))
    arguments["axis"] = str(arguments["axis"]["axis"])
    arguments["operation"] = "create_revolution"
    return _runtime(call).create_sketch(
        arguments,
        ticket=getattr(call, "ticket", None),
    )


def _open_sketch(call: Any) -> Mapping[str, Any]:
    return _runtime(call).open_sketch(
        _arguments(call),
        ticket=getattr(call, "ticket", None),
    )


def _validate(call: Any) -> Mapping[str, Any]:
    return _runtime(call).validate_sketch(_arguments(call))


_HANDLERS = {
    "model.structure": _structure,
    "model.sketch": _sketch,
    "model.revolution_sketch": _revolution_sketch,
    "sketch.open": _open_sketch,
    "sketch.validate": _validate,
}


def register_model_structure_capability_implementations(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for name in MODEL_STRUCTURE_CAPABILITY_NAMES:
        registry.register_implementation(
            NativeCapabilityImplementation(name, _HANDLERS[name])
        )


def model_structure_runtime_bindings(
    runtime: NativeModelStructureRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeModelStructureRuntime):
        raise TypeError("runtime must be a NativeModelStructureRuntime")
    return {name: runtime for name in MODEL_STRUCTURE_CAPABILITY_NAMES}
