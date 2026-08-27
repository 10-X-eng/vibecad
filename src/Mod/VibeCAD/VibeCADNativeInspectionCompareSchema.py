# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for native actual-to-nominal geometry comparison."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADRibbonSurface import SURFACE_IDS


INSPECTION_COMPARE_CAPABILITY_NAME = "inspect.compare"
_OBJECT = {
    "type": "object",
    "properties": {
        "object_name": {
            "type": "string",
            "minLength": 1,
            "maxLength": 128,
            "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
        },
    },
    "required": ["object_name"],
    "additionalProperties": False,
}


def inspection_compare_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=INSPECTION_COMPARE_CAPABILITY_NAME,
        description="Compare actual Part, Mesh, or Points geometry with nominal geometry.",
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="compare",
                description="Create a color-mapped signed-deviation result.",
                action_ids=frozenset({"Inspection_VisualInspection"}),
                surface_ids=frozenset(SURFACE_IDS - {"unavailable"}),
                exact_target_type="ActualAndNominalGeometry",
                transaction_behavior="background",
                background_required=True,
                parameters={
                    "type": "object",
                    "properties": {
                        "actual": _OBJECT,
                        "nominals": {
                            "type": "array",
                            "items": _OBJECT,
                            "minItems": 1,
                            "maxItems": 16,
                            "uniqueItems": True,
                        },
                        "search_radius_mm": {
                            "type": "number",
                            "exclusiveMinimum": 0.0,
                            "maximum": 1_000_000.0,
                        },
                        "tolerance_mm": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1_000_000.0,
                        },
                        "require_complete": {"type": "boolean"},
                        "result_label": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160,
                        },
                    },
                    "required": [
                        "actual",
                        "nominals",
                        "search_radius_mm",
                        "tolerance_mm",
                    ],
                    "additionalProperties": False,
                },
            ),
        ),
    )


def register_inspection_compare_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_shared_definition(inspection_compare_capability_definition())
