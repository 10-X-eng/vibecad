# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for exact, human-authorized CAM ToolBit output."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


MANUFACTURE_TOOL_OUTPUT_CAPABILITY_NAME = "manufacture.tool_output"
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
_TARGET = {
    "type": "object",
    "properties": {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _SHA256,
    },
    "required": ["object_name", "expected_state_sha256"],
    "additionalProperties": False,
}
_FORMAT = {"type": "string", "enum": ["fctb", "yaml"]}


def _parameters() -> dict:
    return {
        "type": "object",
        "properties": {
            "target": _TARGET,
            "format": _FORMAT,
        },
        "required": ["target", "format"],
        "additionalProperties": False,
    }


def manufacture_tool_output_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=MANUFACTURE_TOOL_OUTPUT_CAPABILITY_NAME,
        description="Export one exact CAM ToolBit to a human-authorized destination.",
        primary_classification="export",
        variants=(
            NativeCapabilityVariant(
                operation="save",
                description=(
                    "Run the shipped Save Tool intent for one unchanged ToolBit and "
                    "atomically publish the human-authorized output."
                ),
                action_ids=frozenset({"CAM_ToolBitSave"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactCamToolBitAndAuthorizedOutputPath",
                transaction_behavior="output",
                background_required=False,
                parameters=_parameters(),
            ),
            NativeCapabilityVariant(
                operation="save_as",
                description=(
                    "Run the shipped Save Tool As intent for one unchanged ToolBit and "
                    "atomically publish the human-authorized output."
                ),
                action_ids=frozenset({"CAM_ToolBitSaveAs"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactCamToolBitAndAuthorizedOutputPath",
                transaction_behavior="output",
                background_required=False,
                parameters=_parameters(),
            ),
        ),
    )


def register_manufacture_tool_output_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(manufacture_tool_output_capability_definition())
