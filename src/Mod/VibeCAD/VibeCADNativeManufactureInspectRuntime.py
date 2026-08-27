# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for bounded Manufacture reads."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeManufactureInspect import (
    detect_loop,
    inspect_toolpath,
    list_setups,
    read_job,
    search_setup_options,
    validate_job,
)
from VibeCADNativeManufactureThreadCatalog import read_thread_catalog
from VibeCADNativeRuntimeContext import NativeRuntimeContext


_VARIANTS = {
    "list_setups": frozenset({"query", "offset", "page_size"}),
    "search_setup_options": frozenset({"category", "query", "offset", "page_size"}),
    "read_job": frozenset({"target", "operation_offset", "page_size"}),
    "validate_job": frozenset({"target"}),
    "inspect_toolpath": frozenset({"target", "offset", "page_size"}),
    "detect_loop": frozenset({"target", "selection"}),
    "read_thread_catalog": frozenset({"series", "query", "offset", "page_size"}),
}


class NativeManufactureInspectRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def inspect(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        operation, values = strict_variant_arguments(arguments, _VARIANTS)
        context = self._context
        context.guard()
        if operation == "list_setups":
            return list_setups(context.document, **values)
        if operation == "read_job":
            return read_job(context.document, **values)
        if operation == "search_setup_options":
            return search_setup_options(**values)
        if operation == "validate_job":
            return validate_job(context.document, **values)
        if operation == "inspect_toolpath":
            return inspect_toolpath(context.document, **values)
        if operation == "detect_loop":
            return detect_loop(context.document, **values)
        return read_thread_catalog(**values)
