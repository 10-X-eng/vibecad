# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact provider contract for a finite new-Body Sketch extrusion."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityVariant,
)
from VibeCADNativeDesignSchema import (
    LABEL_SCHEMA,
    OBJECT_NAME_SCHEMA,
    POSITIVE_MM_SCHEMA,
    object_reference_schema,
    parameters_schema,
)


def model_extrude_capability_definition() -> NativeCapabilityDefinition:
    profile = parameters_schema(
        {"object_name": OBJECT_NAME_SCHEMA},
        ("object_name",),
    )
    return NativeCapabilityDefinition(
        name="model.extrude",
        description="Extrude a closed Sketch into a new Body.",
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="extrude",
                description="Use profile.object_name and length_mm.",
                action_ids=frozenset({"PartDesign_DesignExtrude"}),
                surface_ids=frozenset({"model"}),
                exact_target_type="DesignResult",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {
                        "label": LABEL_SCHEMA,
                        "profile": profile,
                        "length_mm": POSITIVE_MM_SCHEMA,
                        "destination_component": object_reference_schema(),
                    },
                    ("label", "profile", "length_mm"),
                ),
            ),
        ),
    )
