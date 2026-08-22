# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Native Assembly inspection reads."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import NativeArgumentError, strict_variant_arguments
from VibeCADNativeAssemblyInspect import (
    NativeAssemblyInspectError,
    read_joint_connector_pairs,
    read_joint_connectors,
    read_selected_linked_assembly,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeTargets import NativeObjectRef


class NativeAssemblyInspectRuntime:
    """Inspect the exact human selection in one frozen Assemble turn."""

    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def inspect(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, Mapping):
            raise NativeArgumentError("Native capability arguments must be an object.")
        operation = str(arguments.get("operation") or "").strip()
        if operation == "linked_source":
            _operation, values = strict_variant_arguments(
                arguments,
                {"linked_source": frozenset({"link"})},
            )
            return read_selected_linked_assembly(
                self._context.document,
                self._reference(values["link"], "link"),
                guard=self._context.guard,
            )
        if operation != "joint_connectors":
            raise NativeArgumentError("Native capability operation is unavailable.")
        values = dict(arguments)
        values.pop("operation", None)
        if not {"component", "joint_type"} <= set(values) or not set(values) <= {
            "component",
            "joint_type",
            "offset",
            "page_size",
        }:
            raise NativeArgumentError(
                "Native capability arguments do not match the selected operation."
            )
        return self._connectors(values)

    def connectors(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, Mapping):
            raise NativeArgumentError("Native capability arguments must be an object.")
        values = dict(arguments)
        operation = values.pop("operation", "find")
        if operation != "find":
            raise NativeArgumentError("Native capability operation is unavailable.")
        if not {
            "first_component",
            "second_component",
            "joint_type",
        } <= set(values) or not set(values) <= {
            "first_component",
            "second_component",
            "joint_type",
            "limit",
        }:
            raise NativeArgumentError(
                "Native capability arguments do not match connector discovery."
            )
        return read_joint_connector_pairs(
            self._context.document,
            self._reference(values["first_component"], "first_component"),
            self._reference(values["second_component"], "second_component"),
            joint_type=values["joint_type"],
            limit=values.get("limit", 12),
            guard=self._context.guard,
        )

    def _connectors(self, values: Mapping[str, Any]) -> dict[str, Any]:
        return read_joint_connectors(
            self._context.document,
            self._reference(values["component"], "component"),
            joint_type=values["joint_type"],
            offset=values.get("offset", 0),
            page_size=values.get("page_size", 48),
            guard=self._context.guard,
        )

    def _reference(self, value: Any, field: str) -> NativeObjectRef:
        if not isinstance(value, Mapping) or set(value) != {"object_name"}:
            raise NativeAssemblyInspectError(
                f"{field} must contain one exact object_name."
            )
        try:
            return NativeObjectRef(
                self._context.document_uid,
                str(value["object_name"]),
            )
        except Exception as exc:
            raise NativeAssemblyInspectError(
                f"{field}.object_name must identify one exact Assembly object."
            ) from exc
