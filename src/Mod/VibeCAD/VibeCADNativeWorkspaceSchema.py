# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for changing the available kind of CAD work."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


NATIVE_SURFACE_BY_WORKSPACE = {
    "modeling": "model",
    "sketching": "sketch.setup",
    "assembly": "assemble",
    "mesh": "mesh",
    "analysis": "analyze",
    "manufacturing": "manufacture",
    "drawing": "drawing",
    "parameters": "parameters",
    "aerodynamics": "aero",
}
NATIVE_WORKSPACES = tuple(NATIVE_SURFACE_BY_WORKSPACE)
NATIVE_WORKSPACE_SURFACES = tuple(NATIVE_SURFACE_BY_WORKSPACE.values())
NATIVE_WORKSPACE_BY_SURFACE = {
    surface: workspace
    for workspace, surface in NATIVE_SURFACE_BY_WORKSPACE.items()
}
NATIVE_WORKSPACE_BY_SURFACE["sketch.edit"] = "sketching"


def workspace_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name="workspace.switch",
        description="Change CAD work.",
        primary_classification="view",
        variants=(
            NativeCapabilityVariant(
                operation="switch",
                description="Switch to the requested CAD work for the next turn.",
                action_ids=frozenset({"VibeCAD_NativeSwitchWorkspace"}),
                surface_ids=frozenset(NATIVE_WORKSPACE_SURFACES),
                exact_target_type=None,
                transaction_behavior="surface_control",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "workspace": {
                            "type": "string",
                            "enum": list(NATIVE_WORKSPACES),
                        }
                    },
                    "required": ["workspace"],
                    "additionalProperties": False,
                },
            ),
        ),
    )


def register_workspace_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_shared_definition(workspace_capability_definition())
