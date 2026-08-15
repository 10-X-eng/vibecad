# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for exact CAM Property Bag creation."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeManufacturePropertyBagRuntime import (
    NativeManufacturePropertyBagRuntime,
)
from VibeCADNativeManufacturePropertyBagSchema import (
    MANUFACTURE_PROPERTY_BAG_CAPABILITY_NAME,
)


def _property_bag(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    ticket = getattr(call, "ticket", None)
    if not isinstance(runtime, NativeManufacturePropertyBagRuntime):
        raise TypeError("A CAM Property Bag call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A CAM Property Bag call requires argument data.")
    return runtime.property_bag(arguments, ticket=ticket)


def register_manufacture_property_bag_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            MANUFACTURE_PROPERTY_BAG_CAPABILITY_NAME,
            _property_bag,
        )
    )


def manufacture_property_bag_runtime_bindings(
    runtime: NativeManufacturePropertyBagRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeManufacturePropertyBagRuntime):
        raise TypeError("runtime must be a NativeManufacturePropertyBagRuntime")
    return {MANUFACTURE_PROPERTY_BAG_CAPABILITY_NAME: runtime}
