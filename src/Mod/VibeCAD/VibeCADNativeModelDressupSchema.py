# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contracts for current Model dress-up operations."""

from __future__ import annotations

from typing import Any

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDesignSchema import (
    LABEL_SCHEMA,
    OBJECT_NAME_SCHEMA,
    POSITIVE_MM_SCHEMA,
    design_link_schema,
    object_reference_schema,
    parameters_schema,
)


MODEL_SURFACE = frozenset({"model"})
_EXPLICIT_TARGET = design_link_schema(
    "subelements",
    r"^(Edge|Face)[1-9][0-9]*$",
    minimum=1,
    maximum=64,
)


def _kinded(kind: str, properties: dict[str, Any]) -> dict[str, Any]:
    fields = {"kind": {"type": "string", "const": kind}, **properties}
    return parameters_schema(fields, tuple(fields))


def _selection_schema() -> dict[str, Any]:
    return {
        "oneOf": [
            _kinded(
                "explicit",
                {
                    "targets": {
                        "type": "array",
                        "items": _EXPLICIT_TARGET,
                        "minItems": 1,
                        "maxItems": 16,
                        "uniqueItems": True,
                    }
                },
            ),
            _kinded(
                "all_edges",
                {
                    "targets": {
                        "type": "array",
                        "items": object_reference_schema(),
                        "minItems": 1,
                        "maxItems": 16,
                        "uniqueItems": True,
                    }
                },
            ),
        ]
    }


def _chamfer_definition_schema() -> dict[str, Any]:
    angle = {
        "type": "number",
        "exclusiveMinimum": 0.0,
        "exclusiveMaximum": 180.0,
    }
    return {
        "oneOf": [
            _kinded("equal_distance", {"size_mm": POSITIVE_MM_SCHEMA}),
            _kinded(
                "two_distances",
                {
                    "size_mm": POSITIVE_MM_SCHEMA,
                    "second_size_mm": POSITIVE_MM_SCHEMA,
                    "flip_direction": {"type": "boolean"},
                },
            ),
            _kinded(
                "distance_angle",
                {
                    "size_mm": POSITIVE_MM_SCHEMA,
                    "angle_degrees": angle,
                    "flip_direction": {"type": "boolean"},
                },
            ),
        ]
    }


def _reference_schema(pattern: str) -> dict[str, Any]:
    return {
        "oneOf": [
            _kinded("automatic", {}),
            _kinded("object", {"object_name": OBJECT_NAME_SCHEMA}),
            _kinded(
                "subelement",
                {
                    "object_name": OBJECT_NAME_SCHEMA,
                    "subelement": {
                        "type": "string",
                        "maxLength": 64,
                        "pattern": pattern,
                    },
                },
            ),
        ]
    }


def _face_selection_schema() -> dict[str, Any]:
    targets = {
        "type": "array",
        "items": design_link_schema(
            "subelements",
            r"^Face[1-9][0-9]*$",
            minimum=1,
            maximum=64,
        ),
        "minItems": 1,
        "maxItems": 16,
        "uniqueItems": True,
    }
    return _kinded("explicit", {"targets": targets})


def model_dressup_capability_definition() -> NativeCapabilityDefinition:
    fillet_parameters = parameters_schema(
        {
            "label": LABEL_SCHEMA,
            "selection": _selection_schema(),
            "radius_mm": POSITIVE_MM_SCHEMA,
            "stage": {
                "type": "string",
                "enum": ["propose", "apply"],
            },
            "preview_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 64,
            },
        },
        ("label", "selection", "radius_mm"),
    )
    chamfer_parameters = parameters_schema(
        {
            "label": LABEL_SCHEMA,
            "selection": _selection_schema(),
            "definition": _chamfer_definition_schema(),
            "stage": {
                "type": "string",
                "enum": ["propose", "apply"],
            },
            "preview_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 64,
            },
        },
        ("label", "selection", "definition"),
    )
    draft_parameters = parameters_schema(
        {
            "label": LABEL_SCHEMA,
            "selection": _face_selection_schema(),
            "angle_degrees": {
                "type": "number",
                "exclusiveMinimum": 0.0,
                "exclusiveMaximum": 90.0,
            },
            "neutral_plane": _reference_schema(r"^(Face|Edge)[1-9][0-9]*$"),
            "pull_direction": _reference_schema(r"^Edge[1-9][0-9]*$"),
            "reversed": {"type": "boolean"},
        },
        (
            "label",
            "selection",
            "angle_degrees",
            "neutral_plane",
            "pull_direction",
            "reversed",
        ),
    )
    thickness_parameters = parameters_schema(
        {
            "label": LABEL_SCHEMA,
            "selection": _face_selection_schema(),
            "thickness_mm": POSITIVE_MM_SCHEMA,
            "direction": {"type": "string", "enum": ["inward", "outward"]},
            "mode": {
                "type": "string",
                "enum": ["skin", "pipe", "recto_verso"],
            },
            "join": {"type": "string", "enum": ["arc", "intersection"]},
            "intersection_handling": {"type": "boolean"},
            "stage": {
                "type": "string",
                "enum": ["propose", "apply"],
            },
            "preview_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 64,
            },
        },
        (
            "label",
            "selection",
            "thickness_mm",
            "direction",
            "mode",
            "join",
            "intersection_handling",
        ),
    )
    return NativeCapabilityDefinition(
        name="model.dressup",
        description="Finish Body faces and edges.",
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="fillet",
                description="Fillet explicit edges/faces or every sharp edge on exact Bodies.",
                action_ids=frozenset({"PartDesign_Fillet"}),
                surface_ids=MODEL_SURFACE,
                exact_target_type="PartDesign::Body[] + (Edge|Face)[]",
                transaction_behavior="document",
                background_required=False,
                parameters=fillet_parameters,
            ),
            NativeCapabilityVariant(
                operation="chamfer",
                description="Chamfer explicit edges/faces or every edge on exact Bodies.",
                action_ids=frozenset({"PartDesign_Chamfer"}),
                surface_ids=MODEL_SURFACE,
                exact_target_type="PartDesign::Body[] + (Edge|Face)[]",
                transaction_behavior="document",
                background_required=False,
                parameters=chamfer_parameters,
            ),
            NativeCapabilityVariant(
                operation="draft",
                description="Draft exact Body faces around exact or inferred references.",
                action_ids=frozenset({"PartDesign_Draft"}),
                surface_ids=MODEL_SURFACE,
                exact_target_type="PartDesign::Body[] + Face[] + DraftReference?",
                transaction_behavior="document",
                background_required=False,
                parameters=draft_parameters,
            ),
            NativeCapabilityVariant(
                operation="thickness",
                description="Shell exact Body faces with current Thickness controls.",
                action_ids=frozenset({"PartDesign_Thickness"}),
                surface_ids=MODEL_SURFACE,
                exact_target_type="PartDesign::Body[] + Face[]",
                transaction_behavior="document",
                background_required=False,
                parameters=thickness_parameters,
            ),
        ),
    )


def register_model_dressup_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(model_dressup_capability_definition())
