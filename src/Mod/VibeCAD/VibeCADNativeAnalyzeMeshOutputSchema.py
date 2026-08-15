# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp Native contract for FEM mesh filtering and surface conversion."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeAnalyzeResultState import RESULT_TARGET


ANALYZE_MESH_OUTPUT_CAPABILITY_NAME = "analyze.mesh_output"

FEM_MESH_OBJECT_TARGET = {
    "type": "object",
    "properties": {
        "object_name": {"type": "string", "minLength": 1, "maxLength": 160},
        "expected_state_sha256": {
            "type": "string",
            "pattern": "^[0-9a-f]{64}$",
            "maxLength": 64,
        },
    },
    "required": ["object_name", "expected_state_sha256"],
    "additionalProperties": False,
}


def _variant(
    operation: str,
    description: str,
    action_id: str,
    parameters: dict,
    *,
    exact_target_type: str = "ExactActiveFemMeshContentAndHistory",
) -> NativeCapabilityVariant:
    ribbon_action = {
        "erase_elements": "FEM_CreateElementsSet",
        "convert_surface": "FEM_FEMMesh2Mesh",
    }.get(operation)
    return NativeCapabilityVariant(
        operation=operation,
        description=description,
        action_ids=frozenset(
            {action_id} if ribbon_action is None else {action_id, ribbon_action}
        ),
        surface_ids=frozenset({"analyze"}),
        exact_target_type=exact_target_type,
        transaction_behavior="document",
        background_required=False,
        parameters=parameters,
    )


def analyze_mesh_output_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ANALYZE_MESH_OUTPUT_CAPABILITY_NAME,
        description=(
            "Filter explicit primary FEM element IDs into a durable result, or convert one "
            "exact undeformed or mechanical-result-deformed FEM exterior into a standard "
            "Mesh feature."
        ),
        primary_classification="mutation",
        variants=(
            _variant(
                "erase_elements",
                "Erase explicit primary element IDs while leaving at least one element.",
                "VibeCAD_AnalyzeEraseMeshElements",
                {
                    "type": "object",
                    "properties": {
                        "target": FEM_MESH_OBJECT_TARGET,
                        "label": {"type": "string", "minLength": 1, "maxLength": 160},
                        "element_ids": {
                            "type": "array",
                            "items": {"type": "integer", "minimum": 1},
                            "minItems": 1,
                            "maxItems": 256,
                            "uniqueItems": True,
                        },
                    },
                    "required": ["target", "label", "element_ids"],
                    "additionalProperties": False,
                },
            ),
            _variant(
                "erase_element_ranges",
                "Erase sorted inclusive primary-element ID ranges without a noisy ID list.",
                "VibeCAD_AnalyzeEraseMeshElementRanges",
                {
                    "type": "object",
                    "properties": {
                        "target": FEM_MESH_OBJECT_TARGET,
                        "label": {"type": "string", "minLength": 1, "maxLength": 160},
                        "element_id_ranges": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "first_id": {"type": "integer", "minimum": 1},
                                    "last_id": {"type": "integer", "minimum": 1},
                                },
                                "required": ["first_id", "last_id"],
                                "additionalProperties": False,
                            },
                            "minItems": 1,
                            "maxItems": 256,
                        },
                    },
                    "required": ["target", "label", "element_id_ranges"],
                    "additionalProperties": False,
                },
            ),
            _variant(
                "convert_surface",
                "Convert the undeformed exterior of one FEM volume or face mesh to Mesh.",
                "VibeCAD_AnalyzeConvertFemMeshSurface",
                {
                    "type": "object",
                    "properties": {
                        "target": FEM_MESH_OBJECT_TARGET,
                        "label": {"type": "string", "minLength": 1, "maxLength": 160},
                    },
                    "required": ["target", "label"],
                    "additionalProperties": False,
                },
            ),
            _variant(
                "convert_deformed_surface",
                (
                    "Convert one FEM exterior using exact displacement data from a "
                    "mechanical result linked to that mesh."
                ),
                "VibeCAD_AnalyzeConvertDeformedFemMeshSurface",
                {
                    "type": "object",
                    "properties": {
                        "target": FEM_MESH_OBJECT_TARGET,
                        "result": RESULT_TARGET,
                        "label": {"type": "string", "minLength": 1, "maxLength": 160},
                    },
                    "required": ["target", "result", "label"],
                    "additionalProperties": False,
                },
                exact_target_type=(
                    "ExactActiveFemMeshMechanicalDisplacementAndHistory"
                ),
            ),
        ),
    )


def register_analyze_mesh_output_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(analyze_mesh_output_capability_definition())
