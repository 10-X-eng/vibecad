# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for exact Drawing dimension-reference repair."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDrawingDimensionSchema import (
    _AXONOMETRIC_MEASUREMENT,
    _EDGE,
    _EXTENT_TARGET,
    _FACE,
    _LINEAR_REFERENCE,
    _OBJECT_NAME,
    _PAGE,
    _SHA256,
    _VERTEX,
    _VIEW,
    _closed,
)


DRAWING_DIMENSION_REPAIR_CAPABILITY_NAME = "drawing.dimension_repair"
DRAWING_DIMENSION_REPAIR_OPERATIONS = ("repair_references",)


def _replacement(kind: str, properties: dict, required: tuple[str, ...]) -> dict:
    return _closed(
        {"kind": {"type": "string", "const": kind}, **properties},
        ("kind", *required),
    )


def _references(items: dict, *, minimum: int, maximum: int) -> dict:
    return {
        "type": "array",
        "items": items,
        "minItems": minimum,
        "maxItems": maximum,
    }


_REPLACEMENT = {
    "oneOf": [
        _replacement(
            "length",
            {"references": _references(_LINEAR_REFERENCE, minimum=1, maximum=2)},
            ("references",),
        ),
        _replacement(
            "horizontal",
            {"references": _references(_LINEAR_REFERENCE, minimum=1, maximum=2)},
            ("references",),
        ),
        _replacement(
            "vertical",
            {"references": _references(_LINEAR_REFERENCE, minimum=1, maximum=2)},
            ("references",),
        ),
        _replacement(
            "radius",
            {"edge": _EDGE, "allow_approximate": {"type": "boolean"}},
            ("edge", "allow_approximate"),
        ),
        _replacement(
            "diameter",
            {"edge": _EDGE, "allow_approximate": {"type": "boolean"}},
            ("edge", "allow_approximate"),
        ),
        _replacement(
            "angle",
            {"first_edge": _EDGE, "second_edge": _EDGE},
            ("first_edge", "second_edge"),
        ),
        _replacement(
            "three_point_angle",
            {
                "first_arm_point": _VERTEX,
                "apex_point": _VERTEX,
                "second_arm_point": _VERTEX,
            },
            ("first_arm_point", "apex_point", "second_arm_point"),
        ),
        _replacement("area", {"face": _FACE}, ("face",)),
        _replacement(
            "horizontal_extent", {"extent": _EXTENT_TARGET}, ("extent",)
        ),
        _replacement(
            "vertical_extent", {"extent": _EXTENT_TARGET}, ("extent",)
        ),
        _replacement(
            "horizontal_chamfer",
            {"first_vertex": _VERTEX, "second_vertex": _VERTEX},
            ("first_vertex", "second_vertex"),
        ),
        _replacement(
            "vertical_chamfer",
            {"first_vertex": _VERTEX, "second_vertex": _VERTEX},
            ("first_vertex", "second_vertex"),
        ),
        _replacement("arc_length", {"arc_edge": _EDGE}, ("arc_edge",)),
        _replacement(
            "axonometric_length",
            {
                "measurement": _AXONOMETRIC_MEASUREMENT,
                "extension_direction_edge": _EDGE,
                "expected_value_mode": {
                    "type": "string",
                    "enum": [
                        "projected",
                        "x_axis_true_length",
                        "y_axis_true_length",
                        "z_axis_true_length",
                    ],
                },
            },
            (
                "measurement",
                "extension_direction_edge",
                "expected_value_mode",
            ),
        ),
    ],
    "description": (
        "Choose the branch matching the selected dimension's reported repair_kind. "
        "Repair never converts the dimension to another semantic kind."
    ),
}


def drawing_dimension_repair_capability_definition() -> NativeCapabilityDefinition:
    parameters = _closed(
        {
            "dimension": _closed(
                {
                    "object_name": _OBJECT_NAME,
                    "expected_repair_state_sha256": _SHA256,
                },
                ("object_name", "expected_repair_state_sha256"),
            ),
            "page": _PAGE,
            "view": _VIEW,
            "replacement": _REPLACEMENT,
        },
        ("dimension", "page", "view", "replacement"),
    )
    return NativeCapabilityDefinition(
        name=DRAWING_DIMENSION_REPAIR_CAPABILITY_NAME,
        description=(
            "Replace broken or incorrect projected references on one exact existing "
            "dimension while preserving its identity, page membership, placement, "
            "label, style, and semantic dimension kind."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="repair_references",
                description=(
                    "Replace one dimension's references with one exact, validated "
                    "replacement branch."
                ),
                action_ids=frozenset({"TechDraw_DimensionRepair"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingDimensionAndReplacementReferences",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters,
            ),
        ),
    )


def register_drawing_dimension_repair_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(drawing_dimension_repair_capability_definition())
