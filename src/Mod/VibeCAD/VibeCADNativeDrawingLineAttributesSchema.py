# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for persistent Drawing line attributes."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDrawingLineAttributeState import (
    MAX_DRAWING_LINE_ATTRIBUTE_PAGE_SIZE,
    MAX_DRAWING_LINE_ATTRIBUTES,
    MAX_DRAWING_LINE_ATTRIBUTE_TARGETS,
)


DRAWING_LINE_ATTRIBUTES_CAPABILITY_NAME = "drawing.line_attributes"
DRAWING_LINE_ATTRIBUTES_OPERATIONS = ("set", "read_view")
_ACTION_IDS = frozenset(
    {"TechDraw_ExtensionChangeLineAttributes", "TechDraw_DecorateLine"}
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
_TAG = {
    "type": "string",
    "pattern": (
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
        r"[0-9a-f]{4}-[0-9a-f]{12}$"
    ),
    "minLength": 36,
    "maxLength": 36,
}
_EDGE = {
    "type": "string",
    "pattern": r"^Edge(?:0|[1-9][0-9]*)$",
    "maxLength": 32,
}


def _closed(properties: dict, required: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _page_target() -> dict:
    return _closed(
        {"object_name": _OBJECT_NAME, "expected_state_sha256": _SHA256},
        ("object_name", "expected_state_sha256"),
    )


def _view_target() -> dict:
    return _closed(
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


def _common() -> dict:
    return {
        "page": _page_target(),
        "view": _view_target(),
        "expected_inventory_state_sha256": {
            **_SHA256,
            "description": (
                "Exact persistent line-inventory hash published in Drawing context."
            ),
        },
    }


def drawing_line_attributes_capability_definition() -> NativeCapabilityDefinition:
    common = _common()
    persistent_line_target = _closed(
        {
            "kind": {
                "type": "string",
                "enum": ["cosmetic_edge", "centerline"],
            },
            "tag": {
                **_TAG,
                "description": "Stable persistent line UUID, not a volatile EdgeN index.",
            },
            "expected_line_state_sha256": _SHA256,
        },
        ("kind", "tag", "expected_line_state_sha256"),
    )
    projected_line_target = _closed(
        {
            "kind": {"type": "string", "const": "projected_edge"},
            "subelement": {
                **_EDGE,
                "description": (
                    "Current projected EdgeN pinned by the view projection and "
                    "individual line-state hashes."
                ),
            },
            "expected_line_state_sha256": _SHA256,
        },
        ("kind", "subelement", "expected_line_state_sha256"),
    )
    line_target = {"oneOf": [persistent_line_target, projected_line_target]}
    color = _closed(
        {
            name: {"type": "number", "minimum": 0.0, "maximum": 1.0}
            for name in ("red", "green", "blue")
        },
        ("red", "green", "blue"),
    )
    attributes = _closed(
        {
            "expected_line_defaults_state_sha256": {
                **_SHA256,
                "description": (
                    "Pins the style catalog and exact thin/middle/thick widths."
                ),
            },
            "line_number": {
                "type": "integer",
                "minimum": 1,
                "maximum": 64,
                "description": "One line_number from drawing.line_defaults/read_current.",
            },
            "width_choice": {
                "type": "string",
                "enum": ["thin", "middle", "thick"],
            },
            "color_rgb": color,
            "visible": {"type": "boolean"},
        },
        (
            "expected_line_defaults_state_sha256",
            "line_number",
            "width_choice",
            "color_rgb",
            "visible",
        ),
    )
    return NativeCapabilityDefinition(
        name=DRAWING_LINE_ATTRIBUTES_CAPABILITY_NAME,
        description=(
            "Read or atomically replace the complete format of exact projected "
            "edges, cosmetic edges, and centerlines in one Drawing view."
        ),
        primary_classification="mutation",
        preserve_operation_branches=True,
        variants=(
            NativeCapabilityVariant(
                operation="set",
                description=(
                    "Apply one complete explicit format to 1 through 32 exact, "
                    "hash-pinned line targets without using hidden session defaults."
                ),
                action_ids=_ACTION_IDS,
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingLinesAndCompleteFormat",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        **common,
                        "targets": {
                            "type": "array",
                            "items": line_target,
                            "minItems": 1,
                            "maxItems": MAX_DRAWING_LINE_ATTRIBUTE_TARGETS,
                        },
                        "attributes": attributes,
                    },
                    (
                        "page",
                        "view",
                        "expected_inventory_state_sha256",
                        "targets",
                        "attributes",
                    ),
                ),
            ),
            NativeCapabilityVariant(
                operation="read_view",
                description=(
                    "Read 1 through 48 stable line targets from one exact, "
                    "hash-pinned view inventory."
                ),
                action_ids=_ACTION_IDS,
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingPersistentLineInventoryPage",
                transaction_behavior="none",
                background_required=False,
                parameters=_closed(
                    {
                        **common,
                        "offset": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": MAX_DRAWING_LINE_ATTRIBUTES,
                        },
                        "page_size": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": MAX_DRAWING_LINE_ATTRIBUTE_PAGE_SIZE,
                        },
                    },
                    (
                        "page",
                        "view",
                        "expected_inventory_state_sha256",
                        "offset",
                        "page_size",
                    ),
                ),
                provider_supplemental=True,
            ),
        ),
    )


def register_drawing_line_attributes_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(drawing_line_attributes_capability_definition())
