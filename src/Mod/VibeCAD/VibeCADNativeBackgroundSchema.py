# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared provider contract for exact Native background-job control."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


NATIVE_BACKGROUND_CAPABILITY_NAME = "native.job"
_ACTION_ID = "VibeCAD_NativeBackgroundJob"
_JOB_ID = {
    "type": "string",
    "minLength": 32,
    "maxLength": 32,
    "pattern": r"^[0-9a-f]{32}$",
}


def native_background_capability_definition() -> NativeCapabilityDefinition:
    variants = tuple(
        NativeCapabilityVariant(
            operation=operation,
            description=description,
            action_ids=frozenset({_ACTION_ID}),
            surface_ids=frozenset({"mesh", "analyze", "manufacture", "drawing"}),
            exact_target_type="NativeBackgroundJobId",
            transaction_behavior="none",
            background_required=False,
            parameters={
                "type": "object",
                "properties": {"job_id": _JOB_ID},
                "required": ["job_id"],
                "additionalProperties": False,
            },
        )
        for operation, description in (
            (
                "status",
                "Read bounded progress, liveness, polling guidance, or the verified "
                "terminal result. Unchanged percent alone is not evidence of a hang.",
            ),
            (
                "cancel",
                "Request cancellation before document commit begins only when the user "
                "asked to cancel or the job reported failure; never cancel merely because "
                "progress is slow or unchanged.",
            ),
        )
    )
    return NativeCapabilityDefinition(
        name=NATIVE_BACKGROUND_CAPABILITY_NAME,
        description=(
            "Inspect one exact long-running document job and follow its polling guidance; "
            "cancel only on explicit user intent or reported failure."
        ),
        primary_classification="read",
        variants=variants,
    )


def register_native_background_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_shared_definition(native_background_capability_definition())
