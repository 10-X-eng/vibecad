# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for read-only Sketch relationship queries."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchConstraintSelection import (
    prepare_constraint_selection,
    read_associated_constraints,
)
from VibeCADNativeSketchElementSelection import (
    prepare_element_selection,
    read_associated_elements,
)


_SELECT_CONSTRAINT_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_geometry_count",
        "selection",
    }
)
_SELECT_ELEMENT_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_geometry_count",
        "constraints",
    }
)


class NativeSketchInspectRuntime:
    """Execute exact Sketch reads in one frozen human-selected turn."""

    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def inspect(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        operation, values = strict_variant_arguments(
            arguments,
            {
                "select_constraints": _SELECT_CONSTRAINT_FIELDS,
                "select_elements": _SELECT_ELEMENT_FIELDS,
            },
        )
        if operation == "select_constraints":
            spec = prepare_constraint_selection(self._context.document_uid, values)
            return read_associated_constraints(self._context, spec)
        spec = prepare_element_selection(self._context.document_uid, values)
        return read_associated_elements(self._context, spec)
