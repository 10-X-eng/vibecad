# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for current Model transformation operations."""

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
    global_axis_schema,
    object_reference_schema,
    parameters_schema,
    vector_schema,
)


MODEL_SURFACE = frozenset({"model"})


def _kinded(kind: str, properties: dict[str, Any]) -> dict[str, Any]:
    fields = {"kind": {"type": "string", "const": kind}, **properties}
    return parameters_schema(fields, tuple(fields))


def _source_schema() -> dict[str, Any]:
    return {
        "oneOf": [
            _kinded("body", {"body": object_reference_schema()}),
            _kinded(
                "feature",
                {
                    "operation": object_reference_schema(),
                    "targets": {
                        "type": "array",
                        "items": object_reference_schema(),
                        "minItems": 1,
                        "maxItems": 16,
                        "uniqueItems": True,
                    },
                },
            ),
        ]
    }


def _mirror_plane_schema() -> dict[str, Any]:
    return {
        "oneOf": [
            _kinded(
                "explicit",
                {
                    "origin_mm": vector_schema(
                        minimum=-1_000_000.0,
                        maximum=1_000_000.0,
                    ),
                    "normal": vector_schema(minimum=-1.0, maximum=1.0),
                },
            ),
            _kinded("object", {"object_name": OBJECT_NAME_SCHEMA}),
            _kinded(
                "subelement",
                {
                    "object_name": OBJECT_NAME_SCHEMA,
                    "subelement": {
                        "type": "string",
                        "maxLength": 64,
                        "pattern": r"^(?:Face[1-9][0-9]*|N_Axis)$",
                    },
                },
            ),
        ]
    }


def _linear_direction_schema() -> dict[str, Any]:
    return {
        "oneOf": [
            _kinded(
                "explicit",
                {"vector": vector_schema(minimum=-1.0, maximum=1.0)},
            ),
            _kinded("object", {"object_name": OBJECT_NAME_SCHEMA}),
            _kinded(
                "subelement",
                {
                    "object_name": OBJECT_NAME_SCHEMA,
                    "subelement": {
                        "type": "string",
                        "maxLength": 64,
                        "pattern": (
                            r"^(?:H_Axis|V_Axis|N_Axis|Axis[0-9]+|"
                            r"Edge[1-9][0-9]*)$"
                        ),
                    },
                },
            ),
        ]
    }


def _circular_axis_schema() -> dict[str, Any]:
    return {
        "oneOf": [
            global_axis_schema(),
            _kinded(
                "explicit",
                {
                    "origin_mm": vector_schema(
                        minimum=-1_000_000.0,
                        maximum=1_000_000.0,
                    ),
                    "direction": vector_schema(minimum=-1.0, maximum=1.0),
                },
            ),
            _kinded("object", {"object_name": OBJECT_NAME_SCHEMA}),
            _kinded(
                "subelement",
                {
                    "object_name": OBJECT_NAME_SCHEMA,
                    "subelement": {
                        "type": "string",
                        "maxLength": 64,
                        "pattern": (
                            r"^(?:H_Axis|V_Axis|N_Axis|Axis[0-9]+|"
                            r"Edge[1-9][0-9]*)$"
                        ),
                    },
                },
            ),
        ]
    }


def _scale_definition_schema() -> dict[str, Any]:
    factor = {
        "type": "number",
        "minimum": 1.0e-6,
        "maximum": 1.0e6,
    }
    center = vector_schema(minimum=-1.0e9, maximum=1.0e9)
    return {
        "oneOf": [
            _kinded("uniform", {"factor": factor, "center_mm": center}),
            _kinded(
                "non_uniform",
                {
                    "x_factor": factor,
                    "y_factor": factor,
                    "z_factor": factor,
                    "center_mm": center,
                },
            ),
        ]
    }


def model_transform_capability_definition() -> NativeCapabilityDefinition:
    pattern_parameters = parameters_schema(
        {
            "label": LABEL_SCHEMA,
            "source": _source_schema(),
            "definition": {
                "oneOf": [
                    _kinded("mirror", {"plane": _mirror_plane_schema()}),
                    _kinded(
                        "linear",
                        {
                            "direction": _linear_direction_schema(),
                            "spacing_mm": {
                                "type": "number",
                                "exclusiveMinimum": 0.0,
                                "maximum": 1.0e9,
                            },
                            "occurrences": {
                                "type": "integer",
                                "minimum": 2,
                                "maximum": 10000,
                            },
                            "centered": {"type": "boolean"},
                        },
                    ),
                    _kinded(
                        "circular",
                        {
                            "axis": _circular_axis_schema(),
                            "angle_degrees": {
                                "type": "number",
                                "exclusiveMinimum": 0.0,
                                "maximum": 360.0,
                            },
                            "occurrences": {
                                "type": "integer",
                                "minimum": 2,
                                "maximum": 10000,
                            },
                            "reversed": {"type": "boolean"},
                        },
                    ),
                ]
            },
        },
        ("label", "source", "definition"),
    )
    scale_parameters = parameters_schema(
        {
            "label": LABEL_SCHEMA,
            "targets": {
                "type": "array",
                "items": object_reference_schema(),
                "minItems": 1,
                "maxItems": 16,
                "uniqueItems": True,
            },
            "definition": _scale_definition_schema(),
        },
        ("label", "targets", "definition"),
    )
    return NativeCapabilityDefinition(
        name="model.transform",
        description="Pattern or scale Bodies.",
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="pattern",
                description="Pattern one exact Body or an earlier additive/subtractive Design feature.",
                action_ids=frozenset(
                    {
                        "PartDesign_DesignMirror",
                        "PartDesign_DesignLinearPattern",
                        "PartDesign_DesignCircularPattern",
                    }
                ),
                surface_ids=MODEL_SURFACE,
                exact_target_type="Body | FeatureAddSub + Body[] + PatternDefinition",
                transaction_behavior="document",
                background_required=False,
                parameters=pattern_parameters,
            ),
            NativeCapabilityVariant(
                operation="scale",
                description=(
                    "Scale 1 to 16 exact current Bodies uniformly or per Design axis "
                    "around one fixed Design-space center."
                ),
                action_ids=frozenset({"PartDesign_Scale"}),
                surface_ids=MODEL_SURFACE,
                exact_target_type="Body[] + ScaleDefinition",
                transaction_behavior="document",
                background_required=False,
                parameters=scale_parameters,
            ),
        ),
    )


def register_model_transform_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(model_transform_capability_definition())
