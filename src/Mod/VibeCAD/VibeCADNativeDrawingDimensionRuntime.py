# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for explicit projected Drawing dimensions."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeDrawingDimension import (
    mutate_drawing_dimension,
    prepare_drawing_dimension,
    verify_drawing_dimension,
)
from VibeCADNativeDrawingDimensionEdit import (
    mutate_drawing_dimension_edit,
    prepare_drawing_dimension_edit,
    verify_drawing_dimension_edit,
)
from VibeCADNativeDrawingMeasurementAnnotation import (
    mutate_drawing_measurement_annotation,
    prepare_drawing_measurement_annotation,
    verify_drawing_measurement_annotation,
)
from VibeCADNativeDrawingSpecialDimension import (
    mutate_drawing_arc_length,
    mutate_drawing_chamfer,
    prepare_drawing_arc_length,
    prepare_drawing_chamfer,
    verify_drawing_arc_length,
    verify_drawing_chamfer,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_COMMON = frozenset({"label", "page", "view", "label_position_on_page_mm"})
_FIELDS = {
    "create_linear": _COMMON | {"references", "direction"},
    "create_radial": _COMMON | {"edge", "allow_approximate", "kind"},
    "create_angle": _COMMON | {"first_edge", "second_edge"},
    "create_three_point_angle": _COMMON
    | {"first_arm_point", "apex_point", "second_arm_point"},
    "create_area": _COMMON | {"face"},
    "create_view_extent": _COMMON | {"direction"},
    "create_edge_extent": _COMMON | {"edges", "direction"},
    "create_axonometric_length": _COMMON
    | {"measurement", "extension_direction_edge", "expected_value_mode"},
    "create_chamfer": _COMMON | {"first_vertex", "second_vertex", "direction"},
    "create_arc_length_dimension": _COMMON | {"arc_edge"},
    "create_area_annotation": frozenset({"page", "view", "elements", "label"}),
    "create_arc_length_annotation": frozenset({"page", "view", "elements", "label"}),
    "edit": frozenset({"dimension", "display", "tolerance", "layout", "appearance"}),
}
_TRANSACTION_NAMES = {
    "create_length": "Create Native Drawing Length",
    "create_horizontal": "Create Native Drawing Horizontal Dimension",
    "create_vertical": "Create Native Drawing Vertical Dimension",
    "create_radius": "Create Native Drawing Radius",
    "create_diameter": "Create Native Drawing Diameter",
    "create_angle": "Create Native Drawing Angle",
    "create_three_point_angle": "Create Native Drawing Three Point Angle",
    "create_area": "Create Native Drawing Area",
    "create_horizontal_extent": "Create Native Drawing Horizontal Extent",
    "create_vertical_extent": "Create Native Drawing Vertical Extent",
    "create_axonometric_length": "Create Native Drawing Axonometric Length",
    "create_horizontal_chamfer": "Create Native Drawing Horizontal Chamfer",
    "create_vertical_chamfer": "Create Native Drawing Vertical Chamfer",
    "create_arc_length_dimension": "Create Native Drawing Arc Length Dimension",
    "create_area_annotation": "Create Native Drawing Area Annotation",
    "create_arc_length_annotation": "Create Native Drawing Arc Length Annotation",
    "edit": "Edit Native Drawing Dimension",
}
_SPECIAL_OPERATIONS = frozenset(
    {
        "create_horizontal_chamfer",
        "create_vertical_chamfer",
        "create_arc_length_dimension",
    }
)
_MEASUREMENT_ANNOTATION_OPERATIONS = frozenset(
    {"create_area_annotation", "create_arc_length_annotation"}
)
_LINEAR_OPERATION = {
    "aligned": "create_length",
    "horizontal": "create_horizontal",
    "vertical": "create_vertical",
}
_RADIAL_OPERATION = {
    "radius": "create_radius",
    "diameter": "create_diameter",
}


def _normalized_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(arguments)
    if normalized.get("operation") == "create_radial":
        normalized.setdefault("allow_approximate", False)
    return normalized


def _specific_operation(
    operation: str,
    values: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    exact = dict(values)
    if operation == "create_linear":
        return _LINEAR_OPERATION[str(exact.pop("direction"))], exact
    if operation == "create_radial":
        exact.setdefault("allow_approximate", False)
        return _RADIAL_OPERATION[str(exact.pop("kind"))], exact
    if operation == "create_view_extent":
        direction = exact.pop("direction")
        exact["extent"] = {"scope": "whole_view"}
        return f"create_{direction}_extent", exact
    if operation == "create_edge_extent":
        direction = exact.pop("direction")
        exact["extent"] = {"scope": "edges", "edges": exact.pop("edges")}
        return f"create_{direction}_extent", exact
    if operation == "create_chamfer":
        return f"create_{exact.pop('direction')}_chamfer", exact
    return operation, exact


class NativeDrawingDimensionRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def execute(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(
            _normalized_arguments(arguments),
            _FIELDS,
        )
        operation, values = _specific_operation(operation, values)
        context = self._context
        context.guard()
        if operation == "edit":
            prepared_edit = prepare_drawing_dimension_edit(
                context.document,
                values=values,
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name=_TRANSACTION_NAMES[operation],
                mutate=partial(
                    mutate_drawing_dimension_edit,
                    prepared=prepared_edit,
                ),
                verify=verify_drawing_dimension_edit,
            )
        if operation in _MEASUREMENT_ANNOTATION_OPERATIONS:
            prepared = prepare_drawing_measurement_annotation(
                context.document,
                operation=operation,
                values=values,
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name=_TRANSACTION_NAMES[operation],
                mutate=partial(
                    mutate_drawing_measurement_annotation,
                    prepared=prepared,
                ),
                verify=verify_drawing_measurement_annotation,
            )
        if operation in _SPECIAL_OPERATIONS:
            arc_length = operation == "create_arc_length_dimension"
            prepared = (
                prepare_drawing_arc_length(
                    context.document,
                    operation=operation,
                    values=values,
                )
                if arc_length
                else prepare_drawing_chamfer(
                    context.document,
                    operation=operation,
                    values=values,
                )
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name=_TRANSACTION_NAMES[operation],
                mutate=partial(
                    mutate_drawing_arc_length if arc_length else mutate_drawing_chamfer,
                    prepared=prepared,
                ),
                verify=(
                    verify_drawing_arc_length if arc_length else verify_drawing_chamfer
                ),
            )
        prepared = prepare_drawing_dimension(
            context.document,
            operation=operation,
            values=values,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name=_TRANSACTION_NAMES[operation],
            mutate=partial(mutate_drawing_dimension, prepared=prepared),
            verify=verify_drawing_dimension,
        )
