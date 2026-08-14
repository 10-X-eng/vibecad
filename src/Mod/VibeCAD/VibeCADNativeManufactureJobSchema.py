# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for atomic CAM Job creation."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDesignSchema import LABEL_SCHEMA


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
_MODEL_INPUT = _closed(
    {
        "target": _MODEL_TARGET,
        "replace_in_history": {
            "type": "boolean",
            "description": (
                "Use the job_create_replaces_in_history value published for "
                "this model at turn start. A mismatch is rejected as stale."
            ),
        },
    },
    ("target", "replace_in_history"),
)
_TEMPLATE = {
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


def manufacture_job_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=MANUFACTURE_JOB_CAPABILITY_NAME,
        description=(
            "Create one complete CAM Job resource graph from exact current models, "
            "with explicit human-equivalent History replacement semantics."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="create_job",
                description=(
                    "Create stock, model clones, setup sheet, tools, and Job as one "
                    "undoable History block; no task panel or filesystem path is exposed."
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
                        "template": _TEMPLATE,
                        "expected_creation_state_sha256": _SHA256,
                        "expected_job_count": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 128,
                        },
                    },
                    (
                        "label",
                        "models",
                        "template",
                        "expected_creation_state_sha256",
                        "expected_job_count",
                    ),
                ),
            ),
        ),
    )


def register_manufacture_job_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(manufacture_job_capability_definition())
