# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for exact Drawing dimension-text changes."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDrawingDimensionTextState import (
    MAX_DRAWING_DIMENSION_TEXT_TARGETS,
    MAX_DRAWING_REPETITION_COUNT,
)


DRAWING_DIMENSION_TEXT_CAPABILITY_NAME = "drawing.dimension_text"
DRAWING_DIMENSION_TEXT_OPERATIONS = (
    "insert_diameter_prefix",
    "insert_square_prefix",
    "insert_repetition_prefix",
    "remove_prefix",
    "increase_decimals",
    "decrease_decimals",
)
_ACTIONS = {
    "insert_diameter_prefix": frozenset({"TechDraw_ExtensionInsertDiameter"}),
    "insert_square_prefix": frozenset({"TechDraw_ExtensionInsertSquare"}),
    "insert_repetition_prefix": frozenset(
        {"TechDraw_ExtensionInsertRepetition"}
    ),
    "remove_prefix": frozenset({"TechDraw_ExtensionRemovePrefixChar"}),
    "increase_decimals": frozenset({"TechDraw_ExtensionIncreaseDecimal"}),
    "decrease_decimals": frozenset({"TechDraw_ExtensionDecreaseDecimal"}),
}
_OBJECT_NAME = {
    "type": "string",
    "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
    "maxLength": 128,
}
_SHA256 = {
    "type": "string",
    "pattern": r"^[0-9a-f]{64}$",
    "minLength": 64,
    "maxLength": 64,
}


def _closed(properties: dict, required: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_PAGE = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _SHA256,
    },
    ("object_name", "expected_state_sha256"),
)
_DIMENSION = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_format_state_sha256": _SHA256,
    },
    ("object_name", "expected_format_state_sha256"),
)
_DIMENSIONS = {
    "type": "array",
    "minItems": 1,
    "maxItems": MAX_DRAWING_DIMENSION_TEXT_TARGETS,
    "uniqueItems": True,
    "items": _DIMENSION,
    "description": "Ordered applicable Drawing dimensions on one exact page.",
}


def _parameters(operation: str) -> dict:
    properties = {
        "page": _PAGE,
        "dimensions": _DIMENSIONS,
    }
    required = ["page", "dimensions"]
    if operation == "insert_repetition_prefix":
        properties["repeat_count"] = {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_DRAWING_REPETITION_COUNT,
            "description": "Integer repetition count rendered as 'n× '.",
        }
        required.append("repeat_count")
    return _closed(properties, tuple(required))


_DESCRIPTIONS = {
    "insert_diameter_prefix": (
        "Prepend the diameter symbol to each exact dimension FormatSpec."
    ),
    "insert_square_prefix": (
        "Prepend the square symbol to each exact dimension FormatSpec."
    ),
    "insert_repetition_prefix": (
        "Prepend an exact integer repetition count and multiplication sign to "
        "each dimension FormatSpec."
    ),
    "remove_prefix": (
        "Remove all text before the first precision marker from each exact "
        "dimension FormatSpec."
    ),
    "increase_decimals": (
        "Increase the single-digit decimal precision marker of every exact "
        "dimension by one."
    ),
    "decrease_decimals": (
        "Decrease the single-digit decimal precision marker of every exact "
        "dimension by one."
    ),
}
_TARGET_TYPES = {
    "insert_diameter_prefix": "ExactDrawingDimensionsAndDiameterPrefix",
    "insert_square_prefix": "ExactDrawingDimensionsAndSquarePrefix",
    "insert_repetition_prefix": "ExactDrawingDimensionsAndRepetitionCount",
    "remove_prefix": "ExactDrawingDimensionsAndPrefixRemoval",
    "increase_decimals": "ExactDrawingDimensionsAndPrecisionIncrease",
    "decrease_decimals": "ExactDrawingDimensionsAndPrecisionDecrease",
}


def drawing_dimension_text_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=DRAWING_DIMENSION_TEXT_CAPABILITY_NAME,
        description=(
            "Apply one exact, host-validated prefix or decimal-precision change "
            "atomically to one or more hash-pinned Drawing dimensions."
        ),
        primary_classification="mutation",
        preserve_operation_branches=True,
        variants=tuple(
            NativeCapabilityVariant(
                operation=operation,
                description=_DESCRIPTIONS[operation],
                action_ids=_ACTIONS[operation],
                surface_ids=frozenset({"drawing"}),
                exact_target_type=_TARGET_TYPES[operation],
                transaction_behavior="document",
                background_required=False,
                parameters=_parameters(operation),
            )
            for operation in DRAWING_DIMENSION_TEXT_OPERATIONS
        ),
    )


def register_drawing_dimension_text_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(drawing_dimension_text_capability_definition())
