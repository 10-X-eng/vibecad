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
            "Rebuild an editable millimetre B-rep from a printables reverse IR "
            "(schema_version 1). Sketches and features, not triangle wrapping. "
            "Does not call mesh.to_shape."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="from_printables_ir",
                description=(
                    "Consume reverse/<body>.ir.json, rebuild Part Design features "
                    "from the IR, export millimetre AP214 STEP and binary STL, "
                    "and classify parametric/analytic/failed."
                ),
                action_ids=frozenset(),
                surface_ids=frozenset({"mesh"}),
                exact_target_type="PrintablesReverseIR",
                transaction_behavior="immediate",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "ir_path": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 4096,
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
                        },
                        "stl_path": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 4096,
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
    registry.register_definition(mesh_reconstruct_parametric_capability_definition())
