# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider schema for the dual-behavior Sketch virtual-space action."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import NativeCapabilityVariant
from VibeCADNativeDesignSchema import OBJECT_NAME_SCHEMA, parameters_schema
from VibeCADNativeSketchVirtualSpaceTarget import MAX_CONSTRAINT_TARGETS, OPERATION


def _constraint_target() -> dict:
    return parameters_schema(
        {
            "constraint_index": {
                "type": "integer",
                "minimum": 0,
                "maximum": 999_999,
            },
            "expected_virtual_space": {"type": "boolean"},
            "virtual_space": {"type": "boolean"},
        },
        ("constraint_index", "expected_virtual_space", "virtual_space"),
    )


def _target() -> dict:
    return {
        "oneOf": [
            parameters_schema(
                {
                    "kind": {"type": "string", "const": "view"},
                    "expected_shown_virtual_space": {"type": "boolean"},
                    "shown_virtual_space": {"type": "boolean"},
                },
                (
                    "kind",
                    "expected_shown_virtual_space",
                    "shown_virtual_space",
                ),
            ),
            parameters_schema(
                {
                    "kind": {"type": "string", "const": "constraints"},
                    "constraints": {
                        "type": "array",
                        "items": _constraint_target(),
                        "minItems": 1,
                        "maxItems": MAX_CONSTRAINT_TARGETS,
                    },
                },
                ("kind", "constraints"),
            ),
        ]
    }


def _parameters() -> dict:
    return parameters_schema(
        {
            "sketch": parameters_schema(
                {"object_name": OBJECT_NAME_SCHEMA},
                ("object_name",),
            ),
            "expected_geometry_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            "expected_constraint_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            "expected_external_geometry_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            "target": _target(),
        },
        (
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "target",
        ),
    )


def sketch_virtual_space_variant() -> NativeCapabilityVariant:
    return NativeCapabilityVariant(
        operation=OPERATION,
        description=(
            "Set the real/virtual constraint view explicitly, or move one through "
            "sixteen exact constraints to their other virtual space."
        ),
        action_ids=frozenset({"Sketcher_SwitchVirtualSpace"}),
        surface_ids=frozenset({"sketch.edit"}),
        exact_target_type="ActiveSketchExactVirtualSpaceViewOrConstraints",
        transaction_behavior="document",
        background_required=False,
        parameters=_parameters(),
    )
