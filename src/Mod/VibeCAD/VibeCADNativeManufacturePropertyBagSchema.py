# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed provider contract for the shipped CAM Property Bag."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


MANUFACTURE_PROPERTY_BAG_CAPABILITY_NAME = "manufacture.property_bag"

_IDENTIFIER = {
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
_ONE_LINE = {
    "type": "string",
    "minLength": 1,
    "maxLength": 128,
    "pattern": r"^(?=.*\S)[^\x00-\x1F\x7F]+$",
}
_MULTILINE_1024 = {
    "type": "string",
    "maxLength": 1024,
    "pattern": r"^[^\x00-\x08\x0B\x0C\x0D\x0E-\x1F\x7F]*$",
}
_MULTILINE_4096 = {
    "type": "string",
    "maxLength": 4096,
    "pattern": r"^[^\x00-\x08\x0B\x0C\x0D\x0E-\x1F\x7F]*$",
}


def _closed(properties: dict, required: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _value_branch(kind: str, value_schema: dict) -> dict:
    return _closed(
        {
            "kind": {"type": "string", "const": kind},
            "value": value_schema,
        },
        ("kind", "value"),
    )


_PROPERTY = {
    "oneOf": [
        _value_branch(
            "angle_degrees",
            {"type": "number", "minimum": -360_000, "maximum": 360_000},
        ),
        _value_branch("boolean", {"type": "boolean"}),
        _value_branch(
            "distance_mm",
            {"type": "number", "minimum": -1_000_000, "maximum": 1_000_000},
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "enumeration"},
                "options": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 160,
                        "pattern": r"^(?=.*\S)[^\x00-\x1F\x7F]+$",
                    },
                    "minItems": 1,
                    "maxItems": 64,
                    "uniqueItems": True,
                    "description": "One through 64 unique printable choices.",
                },
                "selected": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 160,
                    "pattern": r"^(?=.*\S)[^\x00-\x1F\x7F]+$",
                    "description": "Exact selected value; it must occur in options.",
                },
            },
            ("kind", "options", "selected"),
        ),
        _value_branch(
            "number",
            {
                "type": "number",
                "minimum": -1_000_000_000_000,
                "maximum": 1_000_000_000_000,
            },
        ),
        _value_branch(
            "integer",
            {"type": "integer", "minimum": -(2**31), "maximum": 2**31 - 1},
        ),
        _value_branch(
            "length_mm",
            {"type": "number", "minimum": 0, "maximum": 1_000_000},
        ),
        _value_branch(
            "percent",
            {"type": "integer", "minimum": 0, "maximum": 100},
        ),
        _value_branch("string", _MULTILINE_4096),
    ]
}
_PROPERTY_ITEM = _closed(
    {
        "name": {
            **_IDENTIFIER,
            "description": (
                "Stable custom-property identifier, unique without case ambiguity. "
                "CustomPropertyGroups is reserved."
            ),
        },
        "group": {
            **_ONE_LINE,
            "description": "Printable custom group name; Base is reserved.",
        },
        "description": {
            **_MULTILINE_1024,
            "description": "Optional human-facing property documentation.",
        },
        "typed_value": _PROPERTY,
    },
    ("name", "group", "description", "typed_value"),
)
_DESTINATION = {
    "oneOf": [
        _closed(
            {
                "object_name": _IDENTIFIER,
                "expected_state_sha256": _SHA256,
            },
            ("object_name", "expected_state_sha256"),
        ),
        {"type": "null"},
    ],
    "description": (
        "Exact current Part Design Body from property_bag_destinations, or null "
        "to create the bag at document root."
    ),
}


def manufacture_property_bag_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=MANUFACTURE_PROPERTY_BAG_CAPABILITY_NAME,
        description=(
            "Create the shipped CAM Property Bag atomically with a closed set of "
            "typed initial properties and an optional exact Body destination. "
            "File properties remain available in the human editor; the provider "
            "cannot supply filesystem paths."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="create",
                description=(
                    "Create one source-preserving History operation containing zero "
                    "through 64 exact typed reference properties."
                ),
                action_ids=frozenset({"CAM_PropertyBag"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactOptionalPartDesignBodyAndTypedPropertySet",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "label": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160,
                            "pattern": r"^(?=.*\S)[^\x00-\x1F\x7F]+$",
                            "description": (
                                "Printable document label. Surrounding spaces are trimmed."
                            ),
                        },
                        "destination_body": _DESTINATION,
                        "properties": {
                            "type": "array",
                            "items": _PROPERTY_ITEM,
                            "minItems": 0,
                            "maxItems": 64,
                            "description": (
                                "Typed properties canonicalized by stable property name. "
                                "Names must be unique without case ambiguity."
                            ),
                        },
                    },
                    ("label", "destination_body", "properties"),
                ),
            ),
        ),
    )


def register_manufacture_property_bag_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(manufacture_property_bag_capability_definition())
