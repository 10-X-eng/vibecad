# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for parametric reconstruction from a printables reverse IR.

Additive. Does not replace mesh.rebuild, mesh.approximate, or mesh.to_shape.
mesh.to_shape remains a faceted OCC snapshot and is not design intent.
"""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)

MESH_RECONSTRUCT_PARAMETRIC_CAPABILITY_NAME = "mesh.reconstruct_parametric"


def mesh_reconstruct_parametric_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=MESH_RECONSTRUCT_PARAMETRIC_CAPABILITY_NAME,
        description=(
            "Rebuild one retained solid B-rep from a human-selected printables "
            "reverse IR (schema_version 1), without triangle-wrapped conversion."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="from_printables_ir",
                description=(
                    "Select and validate reverse/<body>.ir.json off-thread, rebuild "
                    "one solid, and optionally authorize STEP or STL output."
                ),
                action_ids=frozenset({"Reen_PoissonReconstruction"}),
                surface_ids=frozenset({"mesh"}),
                exact_target_type="HumanAuthorizedPrintablesReverseIR",
                transaction_behavior="background",
                background_required=True,
                parameters={
                    "type": "object",
                    "properties": {
                        "ir_path": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 4096,
                            "description": (
                                "File-name hint for the required human input chooser; "
                                "the provider cannot authorize or open this path."
                            ),
                        },
                        "result_label": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160,
                        },
                        "step_path": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 4096,
                            "description": (
                                "Optional STEP filename hint; the human chooses and "
                                "authorizes the actual destination."
                            ),
                        },
                        "stl_path": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 4096,
                            "description": (
                                "Optional STL filename hint; the human chooses and "
                                "authorizes the actual destination."
                            ),
                        },
                    },
                    "required": ["ir_path", "result_label"],
                    "additionalProperties": False,
                },
            ),
        ),
    )


def register_mesh_reconstruct_parametric_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_shared_definition(mesh_reconstruct_parametric_capability_definition())
