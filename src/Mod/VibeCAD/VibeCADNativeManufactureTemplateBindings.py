# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for CAM Job template output."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeManufactureTemplateRuntime import (
    NativeManufactureTemplateRuntime,
)
from VibeCADNativeManufactureTemplateSchema import (
    MANUFACTURE_TEMPLATE_CAPABILITY_NAME,
)


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    ticket = getattr(call, "ticket", None)
    if not isinstance(runtime, NativeManufactureTemplateRuntime):
        raise TypeError("A CAM template call requires its exact document runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A CAM template call requires argument data.")
    return runtime.export(arguments, ticket)


def register_manufacture_template_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            MANUFACTURE_TEMPLATE_CAPABILITY_NAME,
            _execute,
        )
    )


def manufacture_template_runtime_bindings(
    runtime: NativeManufactureTemplateRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeManufactureTemplateRuntime):
        raise TypeError("runtime must be a NativeManufactureTemplateRuntime")
    return {MANUFACTURE_TEMPLATE_CAPABILITY_NAME: runtime}
