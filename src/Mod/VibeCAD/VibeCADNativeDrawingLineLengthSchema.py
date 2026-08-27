# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for symmetric Drawing line resizing."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDrawingLineLengthState import (
    MAX_DRAWING_LINE_DELTA_MM,
    MAX_DRAWING_LINE_LENGTH_PAGE_SIZE,
    MAX_DRAWING_LINE_LENGTHS,
    MIN_DRAWING_LINE_DELTA_MM,
)


DRAWING_LINE_LENGTH_CAPABILITY_NAME = "drawing.line_length"
DRAWING_LINE_LENGTH_OPERATIONS = ("extend", "shorten", "read_view")
_EXTEND_ACTIONS = frozenset({"TechDraw_ExtensionExtendLine"})
_SHORTEN_ACTIONS = frozenset({"TechDraw_ExtensionShortenLine"})
_ALL_ACTIONS = _EXTEND_ACTIONS | _SHORTEN_ACTIONS
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
                "Exact straight persistent-line inventory hash published in Drawing "
                "context or returned by read_view."
            ),
        },
    }


def _mutation_parameters() -> dict:
    return _closed(
        {
            **_common(),
            "target": _closed(
                {
                    "kind": {
                        "type": "string",
                        "enum": ["cosmetic_edge", "centerline"],
                    },
                    "tag": {
                        **_TAG,
                        "description": (
                            "Stable persistent line UUID, not a volatile EdgeN index."
                        ),
                    },
                    "expected_line_length_state_sha256": _SHA256,
                },
                ("kind", "tag", "expected_line_length_state_sha256"),
            ),
            "delta_distance_mm": {
                "type": "number",
                "minimum": MIN_DRAWING_LINE_DELTA_MM,
                "maximum": MAX_DRAWING_LINE_DELTA_MM,
                "description": (
                    "Positive millimetres added to or removed from each end; the "
                    "total line-length change is twice this value."
                ),
            },
        },
        (
            "page",
            "view",
            "expected_inventory_state_sha256",
            "target",
            "delta_distance_mm",
        ),
    )


def drawing_line_length_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=DRAWING_LINE_LENGTH_CAPABILITY_NAME,
        description=(
            "Read exact straight persistent-line geometry or symmetrically extend "
            "and shorten one cosmetic line or centerline by an explicit distance."
        ),
        primary_classification="mutation",
        preserve_operation_branches=True,
        variants=(
            NativeCapabilityVariant(
                operation="extend",
                description=(
                    "Extend both ends of one exact hash-pinned straight persistent "
                    "line by an explicit positive millimetre distance."
                ),
                action_ids=_EXTEND_ACTIONS,
                surface_ids=frozenset({"drawing"}),
                exact_target_type=(
                    "ExactDrawingStraightPersistentLineAndSymmetricDelta"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters=_mutation_parameters(),
            ),
            NativeCapabilityVariant(
                operation="shorten",
                description="Shorten both ends of one exact persistent straight line.",
                action_ids=_SHORTEN_ACTIONS,
                surface_ids=frozenset({"drawing"}),
                exact_target_type=(
                    "ExactDrawingStraightPersistentLineAndSymmetricDelta"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters=_mutation_parameters(),
            ),
            NativeCapabilityVariant(
                operation="read_view",
                description=(
                    "Read 1 through 48 stable straight-line targets from one exact, "
                    "hash-pinned view inventory."
                ),
                action_ids=_ALL_ACTIONS,
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingStraightPersistentLineInventoryPage",
                transaction_behavior="none",
                background_required=False,
                parameters=_closed(
                    {
                        **_common(),
                        "offset": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": MAX_DRAWING_LINE_LENGTHS,
                        },
                        "page_size": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": MAX_DRAWING_LINE_LENGTH_PAGE_SIZE,
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


def register_drawing_line_length_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(drawing_line_length_capability_definition())
