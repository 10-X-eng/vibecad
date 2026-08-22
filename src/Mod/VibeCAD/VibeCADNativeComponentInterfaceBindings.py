# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for component-interface publication."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeComponentInterfaceRuntime import NativeComponentInterfaceRuntime
from VibeCADNativeComponentInterfaceSchema import (
    COMPONENT_INTERFACE_CAPABILITY_NAME,
    COMPONENT_INTERFACES_CAPABILITY_NAME,
)



def _publish(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeComponentInterfaceRuntime):
        raise TypeError("A component-interface call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A component-interface call requires argument data.")
    return runtime.publish_interface(
        arguments,
        ticket=getattr(call, "ticket", None),
    )


def _interfaces(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeComponentInterfaceRuntime):
        raise TypeError("A component-interface call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A component-interface call requires argument data.")
    return runtime.interfaces(arguments)


def register_component_interface_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(COMPONENT_INTERFACE_CAPABILITY_NAME, _publish)
    )


def register_component_interfaces_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(COMPONENT_INTERFACES_CAPABILITY_NAME, _interfaces)
    )


def component_interface_runtime_bindings(
    runtime: NativeComponentInterfaceRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeComponentInterfaceRuntime):
        raise TypeError("runtime must be a NativeComponentInterfaceRuntime")
    return {
        COMPONENT_INTERFACE_CAPABILITY_NAME: runtime,
        COMPONENT_INTERFACES_CAPABILITY_NAME: runtime,
    }
