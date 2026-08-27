# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for component-interface publication."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import NativeArgumentError, strict_variant_arguments
from VibeCADNativeComponentInterface import (
    prepare_component_interface,
    publish_component_interface,
    read_component_interface_targets,
    verify_component_interface,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_FIELDS = frozenset(
    {"component", "lcs", "name", "kind", "allowed_joints", "compatibility"}
)
_FIELDS_WITH_FIT = _FIELDS | {"fit"}
_FIELDS_WITH_JOINT_PARAMETERS = _FIELDS | {"joint_parameters"}
_FIELDS_WITH_OPTIONALS = _FIELDS | {"fit", "joint_parameters"}


class NativeComponentInterfaceRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def interfaces(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, Mapping):
            raise NativeArgumentError("Native capability arguments must be an object.")
        values = dict(arguments)
        operation = values.pop("operation", "find")
        if operation != "find" or values:
            raise NativeArgumentError(
                "Component interface discovery takes no arguments."
            )
        return read_component_interface_targets(
            self._context.document,
            guard=self._context.guard,
        )

    def publish_interface(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        error = None
        for fields in (
            _FIELDS_WITH_OPTIONALS,
            _FIELDS_WITH_FIT,
            _FIELDS_WITH_JOINT_PARAMETERS,
            _FIELDS,
        ):
            try:
                _operation, values = strict_variant_arguments(
                    arguments, {"publish_interface": fields}
                )
                break
            except NativeArgumentError as exc:
                error = exc
        else:
            raise error or NativeArgumentError(
                "Native capability arguments do not match the selected operation."
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
