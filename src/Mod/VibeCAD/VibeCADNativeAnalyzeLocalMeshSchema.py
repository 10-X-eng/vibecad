# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused provider contracts for local FEM mesh sizing."""

from __future__ import annotations

from VibeCADNativeAnalyzeModelSchema import _OBJECT_NAME
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


ANALYZE_LOCAL_MESH_SIZE = "analyze.local_mesh_size"
ANALYZE_EDIT_LOCAL_MESH_SIZE = "analyze.edit_local_mesh_size"


def _closed(properties: dict, required: tuple[str, ...], *, minimum: int = 0) -> dict:
    result = {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }
    if minimum:
        result["minProperties"] = minimum
    return result


_SOURCE_NAME = {**_OBJECT_NAME, "description": "Geometry source_name."}
_SUBELEMENT_NAMES = {
    "type": "array",
    "items": {
        "type": "string",
        "pattern": r"^(?:[A-Za-z_][A-Za-z0-9_]*\.)?(?:Solid|Face|Edge|Vertex)[1-9][0-9]*$",
        "maxLength": 32,
    },
    "minItems": 1,
    "maxItems": 256,
    "uniqueItems": True,
    "description": "SolidN, FaceN, EdgeN, or VertexN receiving this element size.",
}
_ELEMENT_SIZE = {
    "type": "number",
    "exclusiveMinimum": 0.0,
    "maximum": 1.0e12,
    "description": "Local element size in millimetres.",
}
_APPLIED_TO = _closed(
    {
        "source_name": _SOURCE_NAME,
        "subelement_names": _SUBELEMENT_NAMES,
    },
    ("source_name", "subelement_names"),
)


def _definition(
    name: str,
    description: str,
    action_id: str,
    operation: str,
    exact_target_type: str,
    parameters: dict,
) -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=name,
        description=description,
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation=operation,
                description=description,
                action_ids=frozenset({action_id}),
                surface_ids=frozenset({"analyze"}),
                exact_target_type=exact_target_type,
                transaction_behavior="document",
                background_required=False,
                parameters=parameters,
                provider_supplemental=True,
            ),
        ),
    )


def analyze_local_mesh_capability_definitions(
) -> tuple[NativeCapabilityDefinition, ...]:
    return (
        _definition(
            ANALYZE_LOCAL_MESH_SIZE,
            "Set a local element size on selected geometry.",
            "VibeCAD_AnalyzeCreateLocalMeshSizeFocused",
            "create",
            "CurrentNamedFemMeshAndGeometry",
            _closed(
                {
                    "mesh_name": {**_OBJECT_NAME, "description": "Mesh mesh_name."},
                    "source_name": _SOURCE_NAME,
                    "subelement_names": _SUBELEMENT_NAMES,
                    "element_size_mm": _ELEMENT_SIZE,
                },
                (
                    "mesh_name",
                    "source_name",
                    "subelement_names",
                    "element_size_mm",
                ),
            ),
        ),
        _definition(
            ANALYZE_EDIT_LOCAL_MESH_SIZE,
            "Edit one local element-size assignment.",
            "VibeCAD_AnalyzeEditLocalMeshSizeFocused",
            "edit",
            "CurrentNamedFemMeshRegion",
            _closed(
                {
                    "refinement_name": {
                        **_OBJECT_NAME,
                        "description": "Local mesh-size refinement_name.",
                    },
                    "changes": {
                        "type": "object",
                        "properties": {
                            "element_size_mm": _ELEMENT_SIZE,
                            "applied_to": _APPLIED_TO,
                        },
                        "minProperties": 1,
                        "additionalProperties": False,
                    },
                },
                ("refinement_name", "changes"),
            ),
        ),
    )


def register_analyze_local_mesh_capability_definitions(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for definition in analyze_local_mesh_capability_definitions():
        registry.register_definition(definition)
