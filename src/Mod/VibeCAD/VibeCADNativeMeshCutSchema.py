# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for replayable model-space Mesh cuts and sections."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


MESH_CUT_CAPABILITY_NAME = "mesh.cut"
_OBJECT_NAME = {
    "type": "string",
    "minLength": 1,
    "maxLength": 128,
    "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
}
_STATE_SHA256 = {
    "type": "string",
    "minLength": 64,
    "maxLength": 64,
    "pattern": r"^[0-9a-f]{64}$",
}
_LABEL = {"type": "string", "minLength": 1, "maxLength": 160}
_EXACT_OBJECT = {
    "type": "object",
    "properties": {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _STATE_SHA256,
    },
    "required": ["object_name", "expected_state_sha256"],
    "additionalProperties": False,
}
_LABELED_MESH = {
    "type": "object",
    "properties": {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _STATE_SHA256,
        "label": _LABEL,
    },
    "required": ["object_name", "expected_state_sha256", "label"],
    "additionalProperties": False,
}
_COORDINATE = {"type": "number", "minimum": -1.0e100, "maximum": 1.0e100}
_POINT = {
    "type": "object",
    "properties": {
        "x_mm": _COORDINATE,
        "y_mm": _COORDINATE,
        "z_mm": _COORDINATE,
    },
    "required": ["x_mm", "y_mm", "z_mm"],
    "additionalProperties": False,
}
_POLYGON = {
    "type": "array",
    "description": (
        "Three to 256 ordered, coplanar vertices in document coordinates. "
        "Do not repeat the first vertex at the end."
    ),
    "items": _POINT,
    "minItems": 3,
    "maxItems": 256,
}
_POLYGON_RESULT = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "const": "remove_inside"},
                "result_label": _LABEL,
            },
            "required": ["mode", "result_label"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "const": "remove_outside"},
                "result_label": _LABEL,
            },
            "required": ["mode", "result_label"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "const": "split"},
                "inside_result_label": _LABEL,
                "outside_result_label": _LABEL,
            },
            "required": ["mode", "inside_result_label", "outside_result_label"],
            "additionalProperties": False,
        },
    ]
}
_PLANE_TRIM_RESULT = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "const": "keep_below"},
                "result_label": _LABEL,
            },
            "required": ["mode", "result_label"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "const": "keep_above"},
                "result_label": _LABEL,
            },
            "required": ["mode", "result_label"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "const": "split"},
                "below_result_label": _LABEL,
                "above_result_label": _LABEL,
            },
            "required": ["mode", "below_result_label", "above_result_label"],
            "additionalProperties": False,
        },
    ]
}
_NORMAL = {
    "type": "object",
    "description": "Finite nonzero normal in document coordinates.",
    "properties": {"x": _COORDINATE, "y": _COORDINATE, "z": _COORDINATE},
    "required": ["x", "y", "z"],
    "additionalProperties": False,
}


def _closed(properties: dict, required: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _variant(
    operation: str,
    description: str,
    action_id: str,
    parameters: dict,
) -> NativeCapabilityVariant:
    return NativeCapabilityVariant(
        operation=operation,
        description=description,
        action_ids=frozenset({action_id}),
        surface_ids=frozenset({"mesh"}),
        exact_target_type="ExactCurrentHistoryMeshAndModelSpaceCut",
        transaction_behavior="document",
        background_required=False,
        parameters=parameters,
    )


def mesh_cut_capability_definition() -> NativeCapabilityDefinition:
    polygon_parameters = lambda: _closed(
        {"target": _EXACT_OBJECT, "polygon": _POLYGON, "result": _POLYGON_RESULT},
        ("target", "polygon", "result"),
    )
    return NativeCapabilityDefinition(
        name=MESH_CUT_CAPABILITY_NAME,
        description=(
            "Create retained model-space Mesh polygon cuts, plane trims, and "
            "source-preserving sections without depending on the viewport camera."
        ),
        primary_classification="mutation",
        variants=(
            _variant(
                "poly_cut",
                "Remove whole facets projected inside/outside a polygon, or split both complements.",
                "Mesh_PolyCut",
                polygon_parameters(),
            ),
            _variant(
                "poly_trim",
                "Clip intersected facets at a polygon boundary and retain one or both complements.",
                "Mesh_PolyTrim",
                polygon_parameters(),
            ),
            _variant(
                "trim_by_plane",
                "Keep one side of an exact linked datum plane or create both split sides.",
                "Mesh_TrimByPlane",
                _closed(
                    {
                        "target": _EXACT_OBJECT,
                        "plane": _EXACT_OBJECT,
                        "result": _PLANE_TRIM_RESULT,
                    },
                    ("target", "plane", "result"),
                ),
            ),
            _variant(
                "section_by_plane",
                "Create retained Part wires where one exact Mesh meets one exact datum plane.",
                "Mesh_SectionByPlane",
                _closed(
                    {
                        "target": _EXACT_OBJECT,
                        "plane": _EXACT_OBJECT,
                        "result_label": _LABEL,
                        "settings": _closed(
                            {
                                "minimum_length_mm": {
                                    "type": "number",
                                    "minimum": 0.0,
                                    "maximum": 1.0e100,
                                },
                                "connect_edges": {"type": "boolean"},
                            },
                            ("minimum_length_mm", "connect_edges"),
                        ),
                    },
                    ("target", "plane", "result_label", "settings"),
                ),
            ),
            _variant(
                "cross_sections",
                "Create parallel retained section wires for 1 to 32 exact Meshes.",
                "Mesh_CrossSections",
                _closed(
                    {
                        "targets": {
                            "type": "array",
                            "items": _LABELED_MESH,
                            "minItems": 1,
                            "maxItems": 32,
                        },
                        "planes": _closed(
                            {
                                "normal": _NORMAL,
                                "positions_mm": {
                                    "type": "array",
                                    "description": (
                                        "Unique signed distances along the normalized normal."
                                    ),
                                    "items": _COORDINATE,
                                    "minItems": 1,
                                    "maxItems": 256,
                                    "uniqueItems": True,
                                },
                            },
                            ("normal", "positions_mm"),
                        ),
                        "settings": _closed(
                            {
                                "epsilon_mm": {
                                    "type": "number",
                                    "minimum": 0.0,
                                    "maximum": 1.0e6,
                                },
                                "connect_edges": {"type": "boolean"},
                            },
                            ("epsilon_mm", "connect_edges"),
                        ),
                    },
                    ("targets", "planes", "settings"),
                ),
            ),
        ),
    )


def register_mesh_cut_capability_definition(registry: NativeCapabilityRegistry) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(mesh_cut_capability_definition())
