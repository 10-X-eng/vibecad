# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for human-authorized Mesh input and regular solids."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDesignSchema import placement_schema


MESH_IO_CAPABILITY_NAME = "mesh.io"
_LABEL = {"type": "string", "minLength": 1, "maxLength": 160}
_LENGTH = {
    "type": "number",
    "exclusiveMinimum": 0.0,
    "maximum": 1_000_000_000.0,
}
_NONNEGATIVE_LENGTH = {
    "type": "number",
    "minimum": 0.0,
    "maximum": 1_000_000_000.0,
}
_SAMPLING = {"type": "integer", "minimum": 3, "maximum": 1000}


def _closed(properties: dict, required: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_SOLID = {
    "oneOf": [
        _closed(
            {
                "kind": {"type": "string", "const": "box"},
                "length_mm": _LENGTH,
                "width_mm": _LENGTH,
                "height_mm": _LENGTH,
            },
            ("kind", "length_mm", "width_mm", "height_mm"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "cylinder"},
                "radius_mm": _LENGTH,
                "length_mm": _LENGTH,
                "edge_length_mm": _NONNEGATIVE_LENGTH,
                "sampling": _SAMPLING,
                "closed": {"type": "boolean"},
            },
            (
                "kind",
                "radius_mm",
                "length_mm",
                "edge_length_mm",
                "sampling",
                "closed",
            ),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "cone"},
                "radius1_mm": _NONNEGATIVE_LENGTH,
                "radius2_mm": _NONNEGATIVE_LENGTH,
                "length_mm": _LENGTH,
                "edge_length_mm": _NONNEGATIVE_LENGTH,
                "sampling": _SAMPLING,
                "closed": {"type": "boolean"},
            },
            (
                "kind",
                "radius1_mm",
                "radius2_mm",
                "length_mm",
                "edge_length_mm",
                "sampling",
                "closed",
            ),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "sphere"},
                "radius_mm": _LENGTH,
                "sampling": _SAMPLING,
            },
            ("kind", "radius_mm", "sampling"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "ellipsoid"},
                "radius1_mm": _LENGTH,
                "radius2_mm": _LENGTH,
                "sampling": _SAMPLING,
            },
            ("kind", "radius1_mm", "radius2_mm", "sampling"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "torus"},
                "major_radius_mm": _LENGTH,
                "minor_radius_mm": _LENGTH,
                "sampling": _SAMPLING,
            },
            ("kind", "major_radius_mm", "minor_radius_mm", "sampling"),
        ),
    ]
}


def mesh_io_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=MESH_IO_CAPABILITY_NAME,
        description=(
            "Create parametric regular meshes or ask the human for one mesh file "
            "and load it away from the UI thread. AI never supplies a path."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="import_mesh",
                description=(
                    "Ask the human for one mesh file and return a cancellable job; "
                    "the verified result is committed as one History operation."
                ),
                action_ids=frozenset({"Mesh_Import"}),
                surface_ids=frozenset({"mesh"}),
                exact_target_type="HumanAuthorizedMeshInput",
                transaction_behavior="background",
                background_required=True,
                parameters={
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
            ),
            NativeCapabilityVariant(
                operation="regular_solid",
                description=(
                    "Create one editable Mesh regular-solid feature with exact "
                    "dimensions, tessellation settings, placement, and label."
                ),
                action_ids=frozenset({"Mesh_BuildRegularSolid"}),
                surface_ids=frozenset({"mesh"}),
                exact_target_type="NewParametricMeshSolid",
                transaction_behavior="document",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "label": _LABEL,
                        "placement": placement_schema(),
                        "solid": _SOLID,
                    },
                    "required": ["label", "placement", "solid"],
                    "additionalProperties": False,
                },
            ),
        ),
    )


def register_mesh_io_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(mesh_io_capability_definition())
