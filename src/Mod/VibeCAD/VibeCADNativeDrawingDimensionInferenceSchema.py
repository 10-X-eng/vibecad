# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed provider contract for fail-closed Drawing dimension inference."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


DRAWING_DIMENSION_INFERENCE_CAPABILITY_NAME = "drawing.dimension_infer"
DRAWING_DIMENSION_INFERENCE_OPERATIONS = ("infer",)
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
    {"object_name": _OBJECT_NAME, "expected_state_sha256": _SHA256},
    ("object_name", "expected_state_sha256"),
)
_VIEW = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _SHA256,
        "expected_projection_state_sha256": _SHA256,
    },
    (
        "object_name",
        "expected_state_sha256",
        "expected_projection_state_sha256",
    ),
)


def _element(kind: str) -> dict:
    return _closed(
        {
            "subelement": {
                "type": "string",
                "pattern": rf"^{kind}(0|[1-9][0-9]*)$",
                "maxLength": 32,
            },
        },
        ("subelement",),
    )


def drawing_dimension_inference_capability_definition() -> NativeCapabilityDefinition:
    parameters = _closed(
        {
            "label": {"type": "string", "minLength": 1, "maxLength": 160},
            "page": _PAGE,
            "view": _VIEW,
            "label_position_on_page_mm": {
                **_closed(
                    {
                        "x_mm": {
                            "type": "number",
                            "minimum": -10_000.0,
                            "maximum": 10_000.0,
                        },
                        "y_mm": {
                            "type": "number",
                            "minimum": -10_000.0,
                            "maximum": 10_000.0,
                        },
                    },
                    ("x_mm", "y_mm"),
                ),
                "description": "Dimension-label center in page coordinates, in mm.",
            },
            "elements": {
                "type": "array",
                "items": {
                    "oneOf": [_element("Edge"), _element("Vertex"), _element("Face")]
                },
                "minItems": 1,
                "maxItems": 64,
                "description": "Ordered projected elements with unambiguous dimension semantics.",
            },
        },
        ("label", "page", "view", "label_position_on_page_mm", "elements"),
    )
    return NativeCapabilityDefinition(
        name=DRAWING_DIMENSION_INFERENCE_CAPABILITY_NAME,
        description=(
            "Infer one exact projected dimension without guessing between valid "
            "dimension types, orderings, or series semantics."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="infer",
                description="Create the dimension implied by exact projected semantics.",
                action_ids=frozenset({"TechDraw_Dimension"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type=(
                    "ExactDrawingElementsWithUnambiguousDimensionSemantics"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters=parameters,
            ),
        ),
    )


def register_drawing_dimension_inference_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(drawing_dimension_inference_capability_definition())
