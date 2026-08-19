# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for human-authorized CAM Job template export."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


MANUFACTURE_TEMPLATE_CAPABILITY_NAME = "manufacture.template"
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
_STOCK = {
    "oneOf": [
        {
            "type": "object",
            "properties": {"kind": {"type": "string", "const": "exclude"}},
            "required": ["kind"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "const": "include"},
                "extent": {"type": "boolean"},
                "placement": {"type": "boolean"},
            },
            "required": ["kind", "extent", "placement"],
            "additionalProperties": False,
        },
    ]
}
_SETUP_SHEET = {
    "type": "object",
    "properties": {
        "tool_rapids": {"type": "boolean"},
        "coolant": {"type": "boolean"},
        "operation_heights": {"type": "boolean"},
        "operation_depths": {"type": "boolean"},
        "operation_settings": {
            "type": "array",
            "items": {
                "type": "string",
                "pattern": r"^[A-Za-z][A-Za-z0-9_]{0,79}$",
                "maxLength": 80,
            },
            "maxItems": 64,
            "uniqueItems": True,
            "description": "Current SetupSheet operation-type names to include.",
        },
    },
    "required": [
        "tool_rapids",
        "coolant",
        "operation_heights",
        "operation_depths",
        "operation_settings",
    ],
    "additionalProperties": False,
}


def manufacture_template_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=MANUFACTURE_TEMPLATE_CAPABILITY_NAME,
        description="Export one exact CAM Job as a reusable template.",
        primary_classification="export",
        variants=(
            NativeCapabilityVariant(
                operation="export_template",
                description=(
                    "Export explicit Job, controller, stock, post, and SetupSheet "
                    "content without accepting a provider output path."
                ),
                action_ids=frozenset({"CAM_ExportTemplate"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type=(
                    "ExactCamJobTemplateContentAndHumanAuthorizedOutput"
                ),
                transaction_behavior="output",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "job": _EXACT_TARGET,
                        "description": {
                            "type": "string",
                            "maxLength": 4096,
                            "description": (
                                "Export-only description; an empty string omits it."
                            ),
                        },
                        "include_postprocessing": {"type": "boolean"},
                        "tool_controllers": {
                            "type": "array",
                            "items": _EXACT_TARGET,
                            "maxItems": 32,
                            "uniqueItems": True,
                            "description": (
                                "Exact Job-owned controllers to include, in desired "
                                "template order; an empty list excludes all tools."
                            ),
                        },
                        "stock": _STOCK,
                        "setup_sheet": _SETUP_SHEET,
                    },
                    "required": [
                        "job",
                        "description",
                        "include_postprocessing",
                        "tool_controllers",
                        "stock",
                        "setup_sheet",
                    ],
                    "additionalProperties": False,
                },
            ),
        ),
    )


def register_manufacture_template_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(manufacture_template_capability_definition())
