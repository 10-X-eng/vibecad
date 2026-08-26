# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for explicit Drawing view position locks."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDrawingViewLockState import MAX_DRAWING_VIEW_LOCKS


DRAWING_VIEW_LOCK_CAPABILITY_NAMES = (
    "drawing.view_locks",
    "drawing.set_view_locks",
)
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
_SET_COMMON = {
    "page": _PAGE,
    "expected_inventory_state_sha256": {
        **_SHA256,
        "description": (
            "View-lock inventory hash from Drawing context or drawing.view_locks."
        ),
    },
}
_READ_PAGE = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_inventory_state_sha256": {
            "default": "",
            "anyOf": [
                {"type": "string", "const": ""},
                _SHA256,
            ],
        },
    },
    ("object_name",),
)
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


def drawing_view_lock_capability_definitions() -> tuple[
    NativeCapabilityDefinition, ...
]:
    return (
        NativeCapabilityDefinition(
            name="drawing.view_locks",
            description="Read Drawing view position locks.",
            primary_classification="read",
            variants=(
            NativeCapabilityVariant(
                operation="read",
                description="Read Drawing view position locks.",
                action_ids=_ACTIONS,
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingViewLockInventoryPage",
                transaction_behavior="none",
                background_required=False,
                parameters=_closed(
                    {
                        "page": _READ_PAGE,
                        "offset": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": MAX_DRAWING_VIEW_LOCKS,
                            "default": 0,
                        },
                    },
                    ("page",),
                ),
                provider_supplemental=True,
            ),
            ),
        ),
        NativeCapabilityDefinition(
            name="drawing.set_view_locks",
            description="Set Drawing view position locks.",
            primary_classification="mutation",
            variants=(
            NativeCapabilityVariant(
                operation="set",
                description="Set Drawing view position locks.",
                action_ids=_ACTIONS,
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingPageAndExplicitViewLockStates",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        **_SET_COMMON,
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
            ),
        ),
    )


def register_drawing_view_lock_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for definition in drawing_view_lock_capability_definitions():
        registry.register_definition(definition)
