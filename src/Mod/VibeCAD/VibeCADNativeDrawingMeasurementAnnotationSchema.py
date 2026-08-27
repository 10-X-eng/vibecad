# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for host-measured Drawing annotations."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityVariant,
)


DRAWING_MEASUREMENT_ANNOTATION_OPERATIONS = (
    "create_area_annotation",
    "create_arc_length_annotation",
)
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


_PAGE = _closed(
    {"object_name": _OBJECT_NAME, "expected_state_sha256": _SHA256},
    ("object_name", "expected_state_sha256"),
)
_VIEW = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _SHA256,
        "expected_projection_state_sha256": _SHA256,
    },
    (
        "object_name",
        "expected_state_sha256",
        "expected_projection_state_sha256",
    ),
)


def _element(prefix: str) -> dict:
    return _closed(
        {
            "subelement": {
                "type": "string",
                "pattern": rf"^{prefix}(?:0|[1-9][0-9]*)$",
                "maxLength": 32,
            },
        },
        ("subelement",),
    )


def _parameters(prefix: str) -> dict:
    return _closed(
        {
            "page": _PAGE,
            "view": _VIEW,
            "elements": {
                "type": "array",
                "items": _element(prefix),
                "minItems": 1,
                "maxItems": 64,
            },
            "label": {
                "type": "string",
                "minLength": 1,
                "maxLength": 160,
                "pattern": r"^\S(?:.*\S)?$",
            },
        },
        ("page", "view", "elements", "label"),
    )


def drawing_measurement_annotation_variants() -> tuple[
    NativeCapabilityVariant,
    ...,
]:
    return (
        NativeCapabilityVariant(
            operation="create_area_annotation",
            description=(
                "Measure 1 to 64 exact projected faces and create one "
                "centroid-placed, unit-aware area annotation."
            ),
            action_ids=frozenset({"TechDraw_ExtensionAreaAnnotation"}),
            surface_ids=frozenset({"drawing"}),
            exact_target_type="ExactDrawingProjectedFacesAndAreaAnnotation",
            transaction_behavior="document",
            background_required=False,
            parameters=_parameters("Face"),
        ),
        NativeCapabilityVariant(
            operation="create_arc_length_annotation",
            description=(
                "Measure 1 to 64 exact projected edges in supplied order "
                "and create one host-formatted arc-length annotation."
            ),
            action_ids=frozenset({"TechDraw_ExtensionArcLengthAnnotation"}),
            surface_ids=frozenset({"drawing"}),
            exact_target_type=(
                "ExactDrawingOrderedProjectedEdgesAndArcLengthAnnotation"
            ),
            transaction_behavior="document",
            background_required=False,
            parameters=_parameters("Edge"),
        ),
    )
