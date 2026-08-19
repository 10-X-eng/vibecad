# SPDX-License-Identifier: LGPL-2.1-or-later

"""Assembly point for the split Model feature capability contract."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
)
from VibeCADNativeModelPrimitiveSchema import (
    model_primitive_capability_definition,
)
from VibeCADNativeModelProfileSchema import (
    focused_model_profile_variant,
    model_profile_variants,
)


_FOCUSED_PROFILE_FEATURES = (
    (
        "extrude",
        "PartDesign_DesignExtrude",
        "Extrude a Sketch profile along its normal, an axis, or a vector.",
    ),
    (
        "revolve",
        "PartDesign_DesignRevolve",
        "Revolve a Sketch profile around an axis into a Body.",
    ),
    (
        "loft",
        "PartDesign_DesignLoft",
        "Loft Sketch profiles into a Body.",
    ),
    (
        "sweep",
        "PartDesign_DesignSweep",
        "Sweep a Sketch profile along a path into a Body.",
    ),
    (
        "helix",
        "PartDesign_DesignHelix",
        "Sweep a Sketch profile along a helix into a Body.",
    ),
)


def model_feature_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name="model.feature",
        description="Create a Sketch-profile Body feature.",
        primary_classification="mutation",
        variants=model_profile_variants(),
    )


def focused_model_feature_capability_definitions() -> tuple[
    NativeCapabilityDefinition,
    ...,
]:
    return tuple(
        NativeCapabilityDefinition(
            name=f"model.{kind}",
            description=description,
            primary_classification="mutation",
            variants=(focused_model_profile_variant(kind, action_id),),
        )
        for kind, action_id, description in _FOCUSED_PROFILE_FEATURES
    )


def register_model_feature_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(model_feature_capability_definition())
    for definition in focused_model_feature_capability_definitions():
        registry.register_definition(definition)
    registry.register_definition(model_primitive_capability_definition())
