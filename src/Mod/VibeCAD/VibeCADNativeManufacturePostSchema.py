# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for human-authorized CAM postprocessing."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


MANUFACTURE_POST_CAPABILITY_NAME = "manufacture.post"
_EXACT_TARGET = {
    "type": "object",
    "properties": {
        "object_name": {
            "type": "string",
            "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
            "maxLength": 128,
        },
        "expected_state_sha256": {
            "type": "string",
            "pattern": r"^[0-9a-f]{64}$",
            "minLength": 64,
            "maxLength": 64,
        },
    },
    "required": ["object_name", "expected_state_sha256"],
    "additionalProperties": False,
}


def manufacture_post_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=MANUFACTURE_POST_CAPABILITY_NAME,
        description=(
            "Generate the configured machine program for one exact CAM Job in an "
            "isolated process, then publish every output only to paths chosen by the human."
        ),
        primary_classification="export",
        variants=(
            NativeCapabilityVariant(
                operation="complete_job",
                description=(
                    "Post every current active operation in the exact Job using its "
                    "host-configured modern postprocessor; the provider cannot choose "
                    "a processor, executable, option, or output path."
                ),
                action_ids=frozenset({"CAM_Post"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactCamJobAndHumanAuthorizedPostOutputs",
                transaction_behavior="background_output",
                background_required=True,
                parameters={
                    "type": "object",
                    "properties": {"job": _EXACT_TARGET},
                    "required": ["job"],
                    "additionalProperties": False,
                },
            ),
            NativeCapabilityVariant(
                operation="selected_operations",
                description=(
                    "Post one exact ordered subset of current active Job operations "
                    "using the host-configured modern postprocessor; the provider "
                    "cannot choose a processor, executable, option, or output path."
                ),
                action_ids=frozenset({"CAM_PostSelected"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type=(
                    "ExactCamJobOrderedOperationsAndHumanAuthorizedPostOutputs"
                ),
                transaction_behavior="background_output",
                background_required=True,
                parameters={
                    "type": "object",
                    "properties": {
                        "job": _EXACT_TARGET,
                        "operations": {
                            "type": "array",
                            "items": _EXACT_TARGET,
                            "minItems": 1,
                            "maxItems": 64,
                            "uniqueItems": True,
                            "description": (
                                "Distinct exact active direct Job operations in their "
                                "current Job order."
                            ),
                        },
                    },
                    "required": ["job", "operations"],
                    "additionalProperties": False,
                },
            ),
        ),
    )


def register_manufacture_post_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(manufacture_post_capability_definition())
