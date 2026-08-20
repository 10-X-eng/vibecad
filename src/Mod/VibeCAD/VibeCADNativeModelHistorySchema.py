# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact provider contracts for Model History lifecycle control."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


_MODEL_SURFACE = frozenset({"model"})
_OBJECT_NAME = {
    "type": "string",
    "minLength": 1,
    "maxLength": 128,
    "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
}


def _parameters(
    properties: dict[str, Any],
    required: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": deepcopy(properties),
        "required": list(required),
        "additionalProperties": False,
    }


def _object_ref() -> dict[str, Any]:
    return _parameters({"object_name": _OBJECT_NAME}, ("object_name",))


def _targets() -> dict[str, Any]:
    return {
        "type": "array",
        "items": _object_ref(),
        "minItems": 1,
        "maxItems": 16,
        "uniqueItems": True,
    }


def _variant(
    operation: str,
    description: str,
    action_id: str,
    parameters: dict[str, Any],
    exact_target_type: str,
    *,
    transaction_behavior: str = "document",
) -> NativeCapabilityVariant:
    return NativeCapabilityVariant(
        operation=operation,
        description=description,
        action_ids=frozenset({action_id}),
        surface_ids=_MODEL_SURFACE,
        exact_target_type=exact_target_type,
        transaction_behavior=transaction_behavior,
        background_required=False,
        parameters=parameters,
    )


def model_history_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name="model.history",
        description="Delete or suppress features.",
        primary_classification="mutation",
        variants=(
            _variant(
                "delete_features",
                (
                    "Atomically delete exact History operations or standalone model "
                    "features in dependency-safe order."
                ),
                "VibeCAD_NativeDeleteModelFeatures",
                _parameters(
                    {
                        "targets": _targets(),
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
                    ("targets",),
                ),
                "ModelHistoryOperationOrStandaloneFeature[]",
            ),
            _variant(
                "set_suppressed",
                (
                    "Explicitly suppress or unsuppress one exact suppressible History "
                    "operation at the end of History."
                ),
                "VibeCAD_NativeSetModelFeatureSuppressed",
                _parameters(
                    {
                        "target": _object_ref(),
                        "suppressed": {"type": "boolean"},
                    },
                    ("target", "suppressed"),
                ),
                "SuppressibleModelHistoryOperation",
            ),
        ),
    )


def model_recompute_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name="model.recompute",
        description="Recompute Design History.",
        primary_classification="mutation",
        variants=(
            _variant(
                "recompute_validate",
                (
                    "Recompute exact Bodies or History operations and report their shape, "
                    "status, and BodyResult publication without creating an undo entry."
                ),
                "VibeCAD_NativeRecomputeValidateModel",
                _parameters({"targets": _targets()}, ("targets",)),
                "ModelBodyOrHistoryOperation[]",
                transaction_behavior="none",
            ),
        ),
    )


def register_model_history_capability_definitions(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_shared_definition(model_history_capability_definition())
    registry.register_shared_definition(model_recompute_capability_definition())
