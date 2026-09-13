# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for atomic CAM Job creation."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDesignSchema import LABEL_SCHEMA, placement_schema


MANUFACTURE_JOB_CAPABILITY_NAME = "manufacture.job"
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


_MODEL_TARGET = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _SHA256,
    },
    ("object_name", "expected_state_sha256"),
)
_JOB_TARGET = _MODEL_TARGET
_MODEL_INPUT = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _SHA256,
        "replace_in_history": {
            "type": "boolean",
            "description": "Turn-start Job replacement policy for this model.",
        },
    },
    ("object_name", "expected_state_sha256", "replace_in_history"),
)
_TEMPLATE = {
    "default": {"kind": "none"},
    "oneOf": [
        _closed({"kind": {"type": "string", "const": "none"}}, ("kind",)),
        _closed(
            {
                "kind": {"type": "string", "const": "catalog"},
                "template_id": {
                    "type": "string",
                    "pattern": r"^cam-job-template-v1:[0-9a-f]{64}$",
                    "maxLength": 84,
                },
                "expected_content_sha256": _SHA256,
            },
            ("kind", "template_id", "expected_content_sha256"),
        ),
    ]
}
_FIXTURES = (
    "G54",
    "G55",
    "G56",
    "G57",
    "G58",
    "G59",
    "G59.1",
    "G59.2",
    "G59.3",
    "G59.4",
    "G59.5",
    "G59.6",
    "G59.7",
    "G59.8",
    "G59.9",
)
_SETUP_CHANGES = {
    "type": "object",
    "properties": {
        "label": LABEL_SCHEMA,
        "description": {"type": "string", "maxLength": 4096},
        "machine": {"type": "string", "maxLength": 160},
        "postprocessor": {
            "type": "string",
            "pattern": r"^$|^[A-Za-z][A-Za-z0-9_]*$",
            "maxLength": 160,
        },
        "postprocessor_args": {"type": "string", "maxLength": 4096},
        "fixtures": {
            "type": "array",
            "items": {"type": "string", "enum": list(_FIXTURES)},
            "maxItems": len(_FIXTURES),
            "uniqueItems": True,
        },
        "split_output": {"type": "boolean"},
        "output_order": {
            "type": "string",
            "enum": ["Fixture", "Tool", "Operation"],
        },
        "geometry_tolerance_mm": {
            "type": "number",
            "exclusiveMinimum": 0.0,
        },
    },
    "minProperties": 1,
    "additionalProperties": False,
}
_SIGNED_MM = {
    "type": "number",
    "minimum": -1_000_000.0,
    "maximum": 1_000_000.0,
}
_POSITIVE_MM = {
    "type": "number",
    "exclusiveMinimum": 0.0,
    "maximum": 1_000_000.0,
}
_PLACEMENT = placement_schema()
_ALLOWANCE = _closed(
    {
        name: dict(_SIGNED_MM)
        for name in (
            "x_negative",
            "x_positive",
            "y_negative",
            "y_positive",
            "z_negative",
            "z_positive",
        )
    },
    (
        "x_negative",
        "x_positive",
        "y_negative",
        "y_positive",
        "z_negative",
        "z_positive",
    ),
)
_BOX_SIZE = _closed(
    {axis: dict(_POSITIVE_MM) for axis in ("x", "y", "z")},
    ("x", "y", "z"),
)
_STOCK = {
    "oneOf": [
        _closed(
            {
                "kind": {"type": "string", "const": "model_bounds"},
                "allowance_mm": _ALLOWANCE,
                "placement": _PLACEMENT,
            },
            ("kind", "allowance_mm"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "box"},
                "size_mm": _BOX_SIZE,
                "placement": _PLACEMENT,
            },
            ("kind", "size_mm"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "cylinder"},
                "radius_mm": dict(_POSITIVE_MM),
                "height_mm": dict(_POSITIVE_MM),
                "placement": _PLACEMENT,
            },
            ("kind", "radius_mm", "height_mm"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "existing_solid"},
                "source": _MODEL_TARGET,
                "placement": _PLACEMENT,
            },
            ("kind", "source"),
        ),
    ]
}
_VECTOR = _closed(
    {axis: {"type": "number"} for axis in ("x", "y", "z")},
    ("x", "y", "z"),
)
_WORKPIECE_FRAME = _closed(
    {
        "origin_mm": _VECTOR,
        "x_direction_hint": _VECTOR,
        "z_direction": _VECTOR,
    },
    ("origin_mm", "x_direction_hint", "z_direction"),
)


def manufacture_job_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=MANUFACTURE_JOB_CAPABILITY_NAME,
        description=(
            "Create a CAM setup from exact current models using installed defaults."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="create_job",
                description=(
                    "Create a machining setup with default stock, setup settings, "
                    "and initial tools."
                ),
                action_ids=frozenset({"CAM_Job"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactCurrentCamModelsAndCreationEnvironment",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "label": LABEL_SCHEMA,
                        "models": {
                            "type": "array",
                            "items": _MODEL_INPUT,
                            "minItems": 1,
                            "maxItems": 32,
                        },
                    },
                    ("label", "models"),
                ),
            ),
            NativeCapabilityVariant(
                operation="create_job_from_template",
                description="Create a machining setup from one exact catalog template.",
                action_ids=frozenset({"CAM_Job"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactCurrentCamModelsAndCatalogTemplate",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "label": LABEL_SCHEMA,
                        "models": {
                            "type": "array",
                            "items": _MODEL_INPUT,
                            "minItems": 1,
                            "maxItems": 32,
                        },
                        "template": _TEMPLATE,
                    },
                    ("label", "models", "template"),
                ),
                provider_supplemental=True,
            ),
            NativeCapabilityVariant(
                operation="update_setup",
                description=(
                    "Update authored machine, postprocessor, work offsets, output, "
                    "identity, or tolerance fields on one exact CAM setup."
                ),
                action_ids=frozenset({"CAM_Job"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactCamJobAndSetupChanges",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "target": _JOB_TARGET,
                        "changes": _SETUP_CHANGES,
                    },
                    ("target", "changes"),
                ),
                provider_supplemental=True,
            ),
            NativeCapabilityVariant(
                operation="configure_stock",
                description=(
                    "Configure model-bounds, box, cylinder, or exact existing-solid "
                    "stock on one CAM setup."
                ),
                action_ids=frozenset({"CAM_Job"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactCamJobAndStockConfiguration",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {"target": _JOB_TARGET, "stock": _STOCK},
                    ("target", "stock"),
                ),
                provider_supplemental=True,
            ),
            NativeCapabilityVariant(
                operation="orient_workpiece",
                description=(
                    "Map a workpiece frame in current model coordinates onto machine "
                    "XYZ for one CAM setup."
                ),
                action_ids=frozenset({"CAM_Job"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactCamJobAndWorkpieceFrame",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "target": _JOB_TARGET,
                        "frame": _WORKPIECE_FRAME,
                        "include_stock": {"type": "boolean"},
                    },
                    ("target", "frame", "include_stock"),
                ),
                provider_supplemental=True,
            ),
        ),
    )


def register_manufacture_job_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(manufacture_job_capability_definition())
