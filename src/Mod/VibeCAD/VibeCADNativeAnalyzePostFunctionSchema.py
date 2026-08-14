# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp Native contracts for reusable FEM post functions."""

from __future__ import annotations

from VibeCADNativeAnalyzeResultState import RESULT_TARGET
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


ANALYZE_POST_FUNCTION_CAPABILITY_NAME = "analyze.post_function"

_COORDINATE = {
    "type": "number",
    "minimum": -1000000000.0,
    "maximum": 1000000000.0,
}
_VECTOR = {
    "type": "object",
    "properties": {"x": _COORDINATE, "y": _COORDINATE, "z": _COORDINATE},
    "required": ["x", "y", "z"],
    "additionalProperties": False,
}
_POSITIVE_LENGTH = {
    "type": "number",
    "exclusiveMinimum": 0.0,
    "maximum": 1000000000.0,
}
_LABEL = {"type": "string", "minLength": 1, "maxLength": 160}


def _variant(operation, description, action_ids, properties, required):
    return NativeCapabilityVariant(
        operation=operation,
        description=description,
        action_ids=frozenset(action_ids),
        surface_ids=frozenset({"analyze"}),
        exact_target_type="ExactPostPipelineAndHistory",
        transaction_behavior="document",
        background_required=False,
        parameters={
            "type": "object",
            "properties": {
                "pipeline": RESULT_TARGET,
                "label": _LABEL,
                **properties,
            },
            "required": ["pipeline", "label", *required],
            "additionalProperties": False,
        },
    )


def analyze_post_function_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ANALYZE_POST_FUNCTION_CAPABILITY_NAME,
        description=(
            "Create exact reusable implicit geometry for FEM post-processing filters."
        ),
        primary_classification="mutation",
        variants=(
            _variant(
                "create_plane",
                "Create a plane from an origin in millimetres and a nonzero normal.",
                {"FEM_PostCreateFunctions", "FEM_PostCreateFunctionPlane"},
                {"origin_mm": _VECTOR, "normal": _VECTOR},
                ["origin_mm", "normal"],
            ),
            _variant(
                "create_sphere",
                "Create a sphere from a center and positive radius in millimetres.",
                {"FEM_PostCreateFunctionSphere"},
                {"center_mm": _VECTOR, "radius_mm": _POSITIVE_LENGTH},
                ["center_mm", "radius_mm"],
            ),
            _variant(
                "create_cylinder",
                "Create a cylinder from a center, nonzero axis, and positive radius.",
                {"FEM_PostCreateFunctionCylinder"},
                {
                    "center_mm": _VECTOR,
                    "axis": _VECTOR,
                    "radius_mm": _POSITIVE_LENGTH,
                },
                ["center_mm", "axis", "radius_mm"],
            ),
            _variant(
                "create_box",
                "Create an axis-aligned box from a center and positive dimensions.",
                {"FEM_PostCreateFunctionBox"},
                {
                    "center_mm": _VECTOR,
                    "length_mm": _POSITIVE_LENGTH,
                    "width_mm": _POSITIVE_LENGTH,
                    "height_mm": _POSITIVE_LENGTH,
                },
                ["center_mm", "length_mm", "width_mm", "height_mm"],
            ),
        ),
    )


def register_analyze_post_function_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(analyze_post_function_capability_definition())
