# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime bindings for Parameters spreadsheet capabilities."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeParametersRuntime import NativeParametersRuntime
from VibeCADNativeParametersSchema import (
    PARAMETERS_CELL_CAPABILITY_NAME,
    PARAMETERS_EXPORT_CAPABILITY_NAME,
    PARAMETERS_FORMAT_CAPABILITY_NAME,
    PARAMETERS_READ_CAPABILITY_NAME,
    PARAMETERS_SHEET_CAPABILITY_NAME,
)


def _handler(method_name: str, *, ticket: bool) -> Callable[[Any], Mapping[str, Any]]:
    def execute(call: Any) -> Mapping[str, Any]:
        runtime = getattr(call, "runtime", None)
        arguments = getattr(call, "arguments", None)
        if not isinstance(runtime, NativeParametersRuntime):
            raise TypeError("A Parameters call requires its exact runtime.")
        if not isinstance(arguments, Mapping):
            raise TypeError("A Parameters call requires argument data.")
        method = getattr(runtime, method_name)
        if ticket:
            return method(arguments, ticket=getattr(call, "ticket", None))
        return method(arguments)

    return execute


def register_parameters_capability_implementations(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    handlers = {
        PARAMETERS_SHEET_CAPABILITY_NAME: _handler("sheet", ticket=True),
        PARAMETERS_READ_CAPABILITY_NAME: _handler("read", ticket=False),
        PARAMETERS_CELL_CAPABILITY_NAME: _handler("cell", ticket=True),
        PARAMETERS_FORMAT_CAPABILITY_NAME: _handler("format", ticket=True),
        PARAMETERS_EXPORT_CAPABILITY_NAME: _handler("export", ticket=True),
    }
    for name, handler in handlers.items():
        registry.register_implementation(NativeCapabilityImplementation(name, handler))


def parameters_runtime_bindings(runtime: NativeParametersRuntime) -> dict[str, Any]:
    if not isinstance(runtime, NativeParametersRuntime):
        raise TypeError("runtime must be a NativeParametersRuntime")
    return {
        name: runtime
        for name in (
            PARAMETERS_SHEET_CAPABILITY_NAME,
            PARAMETERS_READ_CAPABILITY_NAME,
            PARAMETERS_CELL_CAPABILITY_NAME,
            PARAMETERS_FORMAT_CAPABILITY_NAME,
            PARAMETERS_EXPORT_CAPABILITY_NAME,
        )
    }
