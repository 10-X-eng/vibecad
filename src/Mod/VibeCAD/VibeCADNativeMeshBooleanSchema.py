# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for retained two-solid Mesh booleans."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


MESH_BOOLEAN_CAPABILITY_NAME = "mesh.boolean"
_OBJECT_NAME = {
    "type": "string",
    "minLength": 1,
    "maxLength": 128,
    "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
}
_STATE_SHA256 = {
    "type": "string",
    "minLength": 64,
    "maxLength": 64,
    "pattern": r"^[0-9a-f]{64}$",
}
_SOURCE = {
    "type": "object",
    "properties": {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _STATE_SHA256,
    },
    "required": ["object_name", "expected_state_sha256"],
    "additionalProperties": False,
}
_LABEL = {"type": "string", "minLength": 1, "maxLength": 160}


def _parameters() -> dict:
    return {
        "type": "object",
        "properties": {
            "first": _SOURCE,
            "second": _SOURCE,
            "result_label": _LABEL,
        },
        "required": ["first", "second", "result_label"],
        "additionalProperties": False,
    }


def _variant(operation: str, description: str, action_id: str) -> NativeCapabilityVariant:
    return NativeCapabilityVariant(
        operation=operation,
        description=description,
        action_ids=frozenset({action_id}),
        surface_ids=frozenset({"mesh"}),
        exact_target_type="TwoExactCurrentHistoryClosedMeshes",
        transaction_behavior="document",
        background_required=False,
        parameters=_parameters(),
    )


def mesh_boolean_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=MESH_BOOLEAN_CAPABILITY_NAME,
        description=(
            "Create one retained source-linked solid boolean from two exact, "
            "closed current-History Meshes. Source order is significant for difference."
        ),
        primary_classification="mutation",
        variants=(
            _variant(
                "union",
                "Unify first and second into one closed Mesh solid.",
                "Mesh_Union",
            ),
            _variant(
                "intersection",
                "Keep the closed volume shared by first and second.",
                "Mesh_Intersection",
            ),
            _variant(
                "difference",
                "Keep first minus second; reversing the sources changes the result.",
                "Mesh_Difference",
            ),
        ),
    )


def register_mesh_boolean_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(mesh_boolean_capability_definition())
