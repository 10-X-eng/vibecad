# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Native Assembly inspection reads."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeAssemblyInspect import (
    NativeAssemblyInspectError,
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
        operation, values = strict_variant_arguments(
            arguments,
            {"linked_source": frozenset({"link"})},
        )
        if operation != "linked_source":
            raise NativeAssemblyInspectError(
                "The Assembly inspection operation is not implemented."
            )
        link = values["link"]
        if not isinstance(link, Mapping) or set(link) != {"object_name"}:
            raise NativeAssemblyInspectError("link must contain one exact object_name.")
        try:
            reference = NativeObjectRef(
                self._context.document_uid,
                str(link["object_name"]),
            )
        except Exception as exc:
            raise NativeAssemblyInspectError(
                "link.object_name must identify one exact Assembly link."
            ) from exc
        return read_selected_linked_assembly(
            self._context.document,
            reference,
            guard=self._context.guard,
        )
