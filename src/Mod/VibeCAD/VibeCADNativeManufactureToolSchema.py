# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contracts for CAM ToolBit catalog and controller work."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDesignSchema import LABEL_SCHEMA


MANUFACTURE_TOOL_CATALOG_CAPABILITY_NAME = "manufacture.tool_catalog"
MANUFACTURE_TOOL_CAPABILITY_NAME = "manufacture.tool"
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


_TARGET = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _SHA256,
    },
    ("object_name", "expected_state_sha256"),
)
_JOB_TARGET = {**_TARGET, "description": "Exact setup from read_setup job."}
_CONTROLLER_TARGET = {
    **_TARGET,
    "description": "Exact controller from read_setup tools[].",
}
_TOOL_BIT_TARGET = {
    **_TARGET,
    "description": "Exact ToolBit from read_setup tools[].tool.",
}
_CATALOG_TARGET = _closed(
    {
        "catalog_id": {
            "type": "string",
            "pattern": r"^cam-toolbit-v1:[0-9a-f]{64}$",
            "minLength": 79,
            "maxLength": 79,
        },
        "expected_content_sha256": _SHA256,
    },
    ("catalog_id", "expected_content_sha256"),
)
_NONNEGATIVE = {"type": "number", "minimum": 0, "maximum": 1_000_000_000}
_TOOL_NUMBER = {
    "oneOf": [
        _closed({"kind": {"type": "string", "const": "next_available"}}, ("kind",)),
        _closed(
            {
                "kind": {"type": "string", "const": "explicit"},
                "value": {"type": "integer", "minimum": 1, "maximum": 10_000},
            },
            ("kind", "value"),
        ),
    ]
}
_TYPED_TOOL_VALUE = {
    "oneOf": [
        _closed(
            {
                "kind": {"type": "string", "const": kind},
                "value": value_schema,
            },
            ("kind", "value"),
        )
        for kind, value_schema in (
            ("length_mm", {"type": "number", "minimum": 0, "maximum": 1_000_000}),
            (
                "angle_degrees",
                {"type": "number", "minimum": -360_000, "maximum": 360_000},
            ),
            ("integer", {"type": "integer", "minimum": -1_000_000_000, "maximum": 1_000_000_000}),
            ("number", {"type": "number", "minimum": -1e100, "maximum": 1e100}),
            ("boolean", {"type": "boolean"}),
            ("choice", {"type": "string", "maxLength": 320}),
            ("string", {"type": "string", "maxLength": 320}),
        )
    ]
}
_TOOL_PROPERTY_CHANGE = _closed(
    {
        "property_name": {
            "type": "string",
            "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
            "maxLength": 128,
        },
        "value": _TYPED_TOOL_VALUE,
    },
    ("property_name", "value"),
)
_TOOL_PROPERTY_CHANGES = {
    "type": "array",
    "items": _TOOL_PROPERTY_CHANGE,
    "minItems": 0,
    "maxItems": 64,
    "default": [],
}
_CONTROLLER = _closed(
    {
        "label": LABEL_SCHEMA,
        "tool_number": _TOOL_NUMBER,
        "tool_length_offset": {"type": "integer", "minimum": 0, "maximum": 10_000},
        "spindle_speed_rpm": {
            "type": "number",
            "minimum": 0,
            "maximum": 10_000_000,
        },
        "spindle_direction": {
            "type": "string",
            "enum": ["Forward", "Reverse", "None"],
        },
        "horizontal_feed_mm_per_minute": _NONNEGATIVE,
        "vertical_feed_mm_per_minute": _NONNEGATIVE,
        "ramp_feed_mm_per_minute": _NONNEGATIVE,
        "lead_in_feed_mm_per_minute": _NONNEGATIVE,
        "lead_out_feed_mm_per_minute": _NONNEGATIVE,
        "horizontal_rapid_mm_per_minute": _NONNEGATIVE,
        "vertical_rapid_mm_per_minute": _NONNEGATIVE,
    },
    (
        "label",
        "tool_number",
        "tool_length_offset",
        "spindle_speed_rpm",
        "spindle_direction",
        "horizontal_feed_mm_per_minute",
        "vertical_feed_mm_per_minute",
        "ramp_feed_mm_per_minute",
        "lead_in_feed_mm_per_minute",
        "lead_out_feed_mm_per_minute",
        "horizontal_rapid_mm_per_minute",
        "vertical_rapid_mm_per_minute",
    ),
)


def manufacture_tool_catalog_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=MANUFACTURE_TOOL_CATALOG_CAPABILITY_NAME,
        description=(
            "Page and inspect the host CAM ToolBit catalog using opaque identities, "
            "exact content fingerprints, and normalized editable properties."
        ),
        primary_classification="read",
        variants=(
            NativeCapabilityVariant(
                operation="list_tools",
                description="Return one ordered page of catalog tools without filesystem paths.",
                action_ids=frozenset({"VibeCAD_ManufactureListTools"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactCamToolCatalogState",
                transaction_behavior="none",
                background_required=False,
                parameters=_closed(
                    {
                        "offset": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 100_000_000,
                            "default": 0,
                        },
                        "query": {
                            "type": "string",
                            "maxLength": 80,
                            "default": "",
                            "description": "Case-insensitive tool label or type; spaces and punctuation are ignored.",
                        },
                        "page_size": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 128,
                            "default": 32,
                        },
                    },
                    (),
                ),
            ),
            NativeCapabilityVariant(
                operation="read_tool",
                description=(
                    "Read one exact catalog tool's complete editable property contract."
                ),
                action_ids=frozenset({"VibeCAD_ManufactureReadTool"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactCamCatalogToolDefinition",
                transaction_behavior="none",
                background_required=False,
                parameters=_closed({"catalog_tool": _CATALOG_TARGET}, ("catalog_tool",)),
            ),
        ),
    )


def manufacture_tool_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=MANUFACTURE_TOOL_CAPABILITY_NAME,
        description=(
            "Create Job-owned ToolBit/controller graphs and update controller or "
            "ToolBit properties as exact one-step document mutations."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="create_controller",
                description=(
                    "Add one exact catalog tool to one Job. Omitted labels, tool number, "
                    "and controller values use the same defaults as the human command."
                ),
                action_ids=frozenset({"CAM_ToolBitDock"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactCamJobAndCatalogTool",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "job_target": _JOB_TARGET,
                        "catalog_tool": _CATALOG_TARGET,
                        "tool_label": LABEL_SCHEMA,
                        "tool_property_changes": _TOOL_PROPERTY_CHANGES,
                        "controller": _CONTROLLER,
                    },
                    (
                        "job_target",
                        "catalog_tool",
                    ),
                ),
            ),
            NativeCapabilityVariant(
                operation="update_controller",
                description="Replace the complete machining settings of one exact controller.",
                action_ids=frozenset({"VibeCAD_ManufactureUpdateController"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactCamToolControllerState",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {"target": _CONTROLLER_TARGET, "controller": _CONTROLLER},
                    ("target", "controller"),
                ),
            ),
            NativeCapabilityVariant(
                operation="update_tool_bit",
                description=(
                    "Update one attached ToolBit label and typed shape/attribute properties, "
                    "including exact replacement of its Job-owned display resources."
                ),
                action_ids=frozenset({"VibeCAD_ManufactureUpdateToolBit"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactCamToolBitState",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "target": _TOOL_BIT_TARGET,
                        "label": LABEL_SCHEMA,
                        "property_changes": {
                            **_TOOL_PROPERTY_CHANGES,
                            "minItems": 1,
                        },
                    },
                    ("target", "label", "property_changes"),
                ),
            ),
        ),
    )


def register_manufacture_tool_capability_definitions(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(manufacture_tool_catalog_capability_definition())
    registry.register_definition(manufacture_tool_capability_definition())
