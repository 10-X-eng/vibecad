# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused contract for creating a related setup from retained stock."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDesignSchema import LABEL_SCHEMA


MANUFACTURE_FOLLOW_UP_CAPABILITY_NAME = "manufacture.follow_up_setup"
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


_RESULT_TARGET = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _SHA256,
    },
    ("object_name", "expected_state_sha256"),
)


def manufacture_follow_up_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=MANUFACTURE_FOLLOW_UP_CAPABILITY_NAME,
        description="Create a later setup from an exact retained-stock result.",
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="create",
                description=(
                    "Create a new setup for the same workpiece using the retained "
                    "material from an earlier setup."
                ),
                action_ids=frozenset({"CAM_FollowUpSetup"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactCurrentRetainedStockResult",
                transaction_behavior="background",
                background_required=True,
                parameters=_closed(
                    {
                        "remaining_stock": _RESULT_TARGET,
                        "label": LABEL_SCHEMA,
                    },
                    ("remaining_stock", "label"),
                ),
            ),
        ),
    )


def register_manufacture_follow_up_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(manufacture_follow_up_capability_definition())
