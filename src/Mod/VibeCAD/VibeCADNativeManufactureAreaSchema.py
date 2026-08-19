# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed provider contract for the experimental CAM Area helpers."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


MANUFACTURE_AREA_CAPABILITY_NAME = "manufacture.area"

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
_LABEL = {
    "type": "string",
    "minLength": 1,
    "maxLength": 160,
    "pattern": r"^(?=.*\S)[^\x00-\x1F\x7F]+$",
}


def _closed(properties: dict, required: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_EXACT_MODEL = _closed(
    {
        "object_name": _IDENTIFIER,
        "expected_state_sha256": _SHA256,
    },
    ("object_name", "expected_state_sha256"),
)
_EXACT_AREA = _closed(
    {
        "object_name": _IDENTIFIER,
        "expected_state_sha256": _SHA256,
    },
    ("object_name", "expected_state_sha256"),
)
_WHOLE_SHAPE = _closed(
    {
        "kind": {"type": "string", "const": "whole_shape"},
        "model": _EXACT_MODEL,
    },
    ("kind", "model"),
)
_SUBELEMENT = _closed(
    {
        "kind": {"type": "string", "const": "subelement"},
        "model": _EXACT_MODEL,
        "name": {
            "type": "string",
            "pattern": r"^(Face|Edge)[1-9][0-9]*$",
            "maxLength": 32,
            "description": "Exact current FaceN or EdgeN element name.",
        },
    },
    ("kind", "model", "name"),
)
_GEOMETRY_TARGET = {
    "oneOf": [_WHOLE_SHAPE, _SUBELEMENT],
    "description": "One exact current model shape, FaceN, or EdgeN.",
}


def manufacture_area_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=MANUFACTURE_AREA_CAPABILITY_NAME,
        description=(
            "Create parametric CAM Areas and Area views, or assign one exact linked "
            "workplane, using the shipped experimental Area domain objects."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="create",
                description=(
                    "Create one source-preserving Area from one through 64 exact whole "
                    "shapes, faces, or edges; subelements remain parametric resources."
                ),
                action_ids=frozenset({"CAM_Area"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactCurrentPartGeometrySet",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "label": _LABEL,
                        "sources": {
                            "type": "array",
                            "items": _GEOMETRY_TARGET,
                            "minItems": 1,
                            "maxItems": 64,
                            "description": (
                                "Distinct exact source selections in deterministic order."
                            ),
                        },
                    },
                    ("label", "sources"),
                ),
            ),
            NativeCapabilityVariant(
                operation="create_view",
                description=(
                    "Create the shipped Area-view feature linked to one exact current Area."
                ),
                action_ids=frozenset({"CAM_Area"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactCurrentFeatureArea",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "label": _LABEL,
                        "area": _EXACT_AREA,
                    },
                    ("label", "area"),
                ),
            ),
            NativeCapabilityVariant(
                operation="set_workplane",
                description=(
                    "Assign one exact whole planar shape, face, or containing edge wire "
                    "as the authoritative linked workplane of one exact current Area."
                ),
                action_ids=frozenset({"CAM_Area_Workplane"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactCurrentFeatureAreaAndPartWorkplane",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "area": _EXACT_AREA,
                        "workplane": _GEOMETRY_TARGET,
                    },
                    ("area", "workplane"),
                ),
            ),
        ),
    )


def register_manufacture_area_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(manufacture_area_capability_definition())
