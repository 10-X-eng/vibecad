# SPDX-License-Identifier: LGPL-2.1-or-later

"""Assembly point for the split Model feature capability contract."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
)
from VibeCADNativeModelExtrudeSchema import model_extrude_capability_definition
from VibeCADNativeModelPrimitiveSchema import (
    model_box_capability_definition,
    model_cylinder_capability_definition,
    model_primitive_capability_definition,
    model_primitive_variants,
)
from VibeCADNativeModelProfileSchema import model_profile_variants


def model_feature_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name="model.feature",
        description="Revolve, loft, sweep, helix, or advanced-extrude Sketches.",
        primary_classification="mutation",
        variants=(*model_profile_variants(), *model_primitive_variants()),
    )


def register_model_feature_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(model_feature_capability_definition())
    registry.register_definition(model_extrude_capability_definition())
    registry.register_definition(model_box_capability_definition())
    registry.register_definition(model_cylinder_capability_definition())
    registry.register_definition(model_primitive_capability_definition())
