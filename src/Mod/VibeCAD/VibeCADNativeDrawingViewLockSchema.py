# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for explicit Drawing view position locks."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDrawingViewLockState import (
    MAX_DRAWING_VIEW_LOCK_PAGE_SIZE,
    MAX_DRAWING_VIEW_LOCKS,
)


DRAWING_VIEW_LOCK_CAPABILITY_NAME = "drawing.view_lock"
DRAWING_VIEW_LOCK_OPERATIONS = ("set", "read_page")
_ACTIONS = frozenset({"TechDraw_ExtensionLockUnlockView"})
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
_COMMON = {
    "page": _PAGE,
    "expected_inventory_state_sha256": {
        **_SHA256,
        "description": (
            "Exact view-lock inventory hash published in Drawing context or "
            "returned by read_page."
        ),
    },
}
_CHANGE = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_view_lock_state_sha256": _SHA256,
        "locked": {
            "type": "boolean",
            "description": "Explicit final lock state.",
        },
    },
    ("object_name", "expected_view_lock_state_sha256", "locked"),
)


def drawing_view_lock_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=DRAWING_VIEW_LOCK_CAPABILITY_NAME,
        description=(
            "Read exact Drawing position-lock state or set 1 through 32 "
            "hash-pinned views to explicit final lock states."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="set",
                description=(
                    "Set explicit final lock states for exact views on one page. "
                    "Mixed lock and unlock requests are applied atomically."
                ),
                action_ids=_ACTIONS,
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingPageAndExplicitViewLockStates",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        **_COMMON,
                        "views": {
                            "type": "array",
                            "items": _CHANGE,
                            "minItems": 1,
                            "maxItems": 32,
                        },
                    },
                    ("page", "expected_inventory_state_sha256", "views"),
                ),
            ),
            NativeCapabilityVariant(
                operation="read_page",
                description=(
                    "Read 1 through 48 exact position-lock targets from one "
                    "hash-pinned Drawing page inventory."
                ),
                action_ids=_ACTIONS,
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingViewLockInventoryPage",
                transaction_behavior="none",
                background_required=False,
                parameters=_closed(
                    {
                        **_COMMON,
                        "offset": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": MAX_DRAWING_VIEW_LOCKS,
                        },
                        "page_size": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": MAX_DRAWING_VIEW_LOCK_PAGE_SIZE,
                        },
                    },
                    (
                        "page",
                        "expected_inventory_state_sha256",
                        "offset",
                        "page_size",
                    ),
                ),
                provider_supplemental=True,
            ),
        ),
    )


def register_drawing_view_lock_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(drawing_view_lock_capability_definition())
