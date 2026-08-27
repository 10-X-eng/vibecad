# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for grouped Drawing dimension series."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


DRAWING_DIMENSION_SERIES_CAPABILITY_NAME = "drawing.dimension_series"
DRAWING_DIMENSION_SERIES_OPERATIONS = (
    "create_horizontal_chain",
    "create_vertical_chain",
    "create_oblique_chain",
    "create_horizontal_coordinate",
    "create_vertical_coordinate",
    "create_oblique_coordinate",
)
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
_VERTEX = _closed(
    {
        "subelement": {
            "type": "string",
            "pattern": r"^Vertex(0|[1-9][0-9]*)$",
            "maxLength": 32,
        },
    },
    ("subelement",),
)


def _parameters(kind: str, direction: str) -> dict:
    ordering = (
        "The first two vertices establish the baseline direction and sign. "
        if direction == "oblique"
        else "The first two vertices establish coordinate sign. "
        if kind == "coordinate"
        else "The host orders vertices geometrically. "
    )
    distinct = (
        "Every vertex must have a distinct X coordinate. "
        if direction == "horizontal"
        else "Every vertex must have a distinct Y coordinate. "
        if direction == "vertical"
        else "Every vertex must have a distinct position along that baseline. "
    )
    return _closed(
        {
            "label": {
                "type": "string",
                "minLength": 1,
                "maxLength": 160,
                "description": "Preferred label for the single History operation group.",
            },
            "page": _PAGE,
            "view": _VIEW,
            "vertices": {
                "type": "array",
                "items": _VERTEX,
                "minItems": 3,
                "maxItems": 64,
                "description": (
                    f"Three to 64 unique exact projected vertices. {distinct}{ordering}"
                    f"Creates one {direction} {kind} series with N-1 dimensions."
                ),
            },
        },
        ("label", "page", "view", "vertices"),
    )


def drawing_dimension_series_capability_definition() -> NativeCapabilityDefinition:
    actions = {
        ("chain", "horizontal"): "TechDraw_ExtensionCreateHorizChainDimension",
        ("chain", "vertical"): "TechDraw_ExtensionCreateVertChainDimension",
        ("chain", "oblique"): "TechDraw_ExtensionCreateObliqueChainDimension",
        ("coordinate", "horizontal"): "TechDraw_ExtensionCreateHorizCoordDimension",
        ("coordinate", "vertical"): "TechDraw_ExtensionCreateVertCoordDimension",
        ("coordinate", "oblique"): "TechDraw_ExtensionCreateObliqueCoordDimension",
    }
    return NativeCapabilityDefinition(
        name=DRAWING_DIMENSION_SERIES_CAPABILITY_NAME,
        description=(
            "Create one exact chain or coordinate dimension series as a "
            "single History operation with exact owned dimensions."
        ),
        primary_classification="mutation",
        variants=tuple(
            NativeCapabilityVariant(
                operation=f"create_{direction}_{kind}",
                description=(
                    f"Create one {direction} {kind} series with N-1 exact dimensions."
                ),
                action_ids=frozenset({action_id}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type=(
                    f"ExactDrawing{direction.title()}{kind.title()}DimensionSeries"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters=_parameters(kind, direction),
            )
            for (kind, direction), action_id in actions.items()
        ),
    )


def register_drawing_dimension_series_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(drawing_dimension_series_capability_definition())
