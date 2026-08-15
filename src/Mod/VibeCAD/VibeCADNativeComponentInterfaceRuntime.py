# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for component-interface publication."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeComponentInterface import (
    prepare_component_interface,
    publish_component_interface,
    verify_component_interface,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_FIELDS = frozenset(
    {"component", "lcs", "name", "kind", "allowed_joints", "compatibility"}
)


class NativeComponentInterfaceRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def publish_interface(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        _operation, values = strict_variant_arguments(
            arguments,
            {"publish_interface": _FIELDS},
        )
        self._context.guard()
        prepared = prepare_component_interface(self._context.document, values)
        return run_immediate_mutation(
            self._context,
            ticket=ticket,
            transaction_name="Publish Native Component Interface",
            mutate=partial(publish_component_interface, prepared=prepared),
            verify=verify_component_interface,
        )
