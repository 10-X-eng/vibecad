# SPDX-License-Identifier: LGPL-2.1-or-later

"""Canonical mirror tool for Body features and standalone shapes."""

from typing import Any

from . import domain_runtime
from . import part_mirror
from . import partdesign_mirror
from . import partdesign_transform_feature


TOOL_SPEC = {
    "name": "model.mirror",
    "description": (
        "Mirror modeled geometry. Use body_features to repeat one or more features inside their "
        "Body, or standalone_shape to create an independent parametric mirrored result."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "PartDesignWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "result_mode": {
                "type": "string",
                "enum": ["body_features", "standalone_shape"],
                "description": "Repeat Body features or create a standalone mirrored shape.",
            },
            "feature_names": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "description": "Body feature names; required for body_features.",
            },
            "source_object_name": {
                "type": "string",
                "description": "Source shape name; required for standalone_shape.",
            },
            "body_plane": partdesign_transform_feature.PLANE_REFERENCE_SCHEMA,
            "plane_point": domain_runtime.vector_schema(
                "Global point on the plane for standalone_shape."
            ),
            "plane_normal": domain_runtime.vector_schema(
                "Global plane normal for standalone_shape.", units=None
            ),
            "transform_mode": partdesign_transform_feature.TRANSFORM_MODE_SCHEMA,
            "refine": {
                "type": "boolean",
                "description": "Remove redundant edges from a Body result.",
            },
            "label": {"type": "string", "description": "Visible result label."},
        },
        "required": ["result_mode", "label"],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    result_mode: str,
    label: str,
    feature_names: list[str] | None = None,
    source_object_name: str | None = None,
    body_plane: dict[str, Any] | None = None,
    plane_point: dict[str, Any] | None = None,
    plane_normal: dict[str, Any] | None = None,
    transform_mode: str | None = None,
    refine: bool | None = None,
) -> dict[str, Any]:
    if result_mode == "body_features":
        if not feature_names or body_plane is None or transform_mode is None:
            return _invalid(
                "body_features requires feature_names, body_plane, and transform_mode."
            )
        result = partdesign_mirror.run(
            service,
            feature_names=feature_names,
            label=label,
            plane=body_plane,
            transform_mode=transform_mode,
            refine=True if refine is None else refine,
        )
    elif result_mode == "standalone_shape":
        if not source_object_name or plane_point is None or plane_normal is None:
            return _invalid(
                "standalone_shape requires source_object_name, plane_point, and plane_normal."
            )
        if refine:
            return _invalid(
                "Standalone mirror does not have a native refine option; set refine=false or "
                "omit it."
            )
        result = part_mirror.run(
            service,
            source_object_name=source_object_name,
            plane_point=plane_point,
            plane_normal=plane_normal,
            label=label,
        )
    else:
        return _invalid("result_mode must be body_features or standalone_shape.")
    if isinstance(result, dict):
        result["operation"] = "mirror"
        result["result_mode"] = result_mode
    return result


def _invalid(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False}
