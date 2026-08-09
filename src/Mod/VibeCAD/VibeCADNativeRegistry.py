# SPDX-License-Identifier: LGPL-2.1-or-later

"""Production assembly point for Native capability contracts and bindings."""

from __future__ import annotations

from VibeCADNativeAssemblyJointBindings import (
    register_assembly_joint_capability_implementation,
)
from VibeCADNativeAssemblyJointSchema import (
    register_assembly_joint_capability_definition,
)
from VibeCADNativeAssemblyStructureBindings import (
    register_assembly_structure_capability_implementation,
)
from VibeCADNativeAssemblyStructureSchema import (
    register_assembly_structure_capability_definition,
)
from VibeCADNativeCapabilityRegistry import NativeCapabilityRegistry
from VibeCADNativeComponentInterfaceBindings import (
    register_component_interface_capability_implementation,
)
from VibeCADNativeComponentInterfaceSchema import (
    register_component_interface_capability_definition,
)
from VibeCADNativeCommonBindings import register_common_capability_implementations
from VibeCADNativeCommonSchema import register_common_capability_definitions
from VibeCADNativeModelCatalogBindings import (
    register_model_catalog_capability_implementation,
)
from VibeCADNativeModelCatalogSchema import (
    register_model_catalog_capability_definition,
)
from VibeCADNativeModelDressupBindings import (
    register_model_dressup_capability_implementation,
)
from VibeCADNativeModelDressupSchema import (
    register_model_dressup_capability_definition,
)
from VibeCADNativeModelBooleanBindings import (
    register_model_boolean_capability_implementation,
)
from VibeCADNativeModelBooleanSchema import (
    register_model_boolean_capability_definition,
)
from VibeCADNativeModelFeatureBindings import (
    register_model_feature_capability_implementation,
)
from VibeCADNativeModelFeatureSchema import (
    register_model_feature_capability_definition,
)
from VibeCADNativeModelFastenerBindings import (
    register_model_fastener_capability_implementation,
)
from VibeCADNativeModelFastenerSchema import (
    register_model_fastener_capability_definition,
)
from VibeCADNativeModelHoleBindings import (
    register_model_hole_capability_implementations,
)
from VibeCADNativeModelHoleSchema import (
    register_model_hole_capability_definitions,
)
from VibeCADNativeModelJoinBindings import (
    register_model_join_capability_implementation,
)
from VibeCADNativeModelJoinSchema import register_model_join_capability_definition
from VibeCADNativeModelPartBindings import (
    register_model_part_capability_implementation,
)
from VibeCADNativeModelPartSchema import register_model_part_capability_definition
from VibeCADNativeModelSurfaceBindings import (
    register_model_surface_capability_implementation,
)
from VibeCADNativeModelSurfaceSchema import (
    register_model_surface_capability_definition,
)
from VibeCADNativeModelStructureBindings import (
    register_model_structure_capability_implementations,
)
from VibeCADNativeModelStructureSchema import (
    register_model_structure_capability_definitions,
)
from VibeCADNativeModelTransformBindings import (
    register_model_transform_capability_implementation,
)
from VibeCADNativeModelTransformSchema import (
    register_model_transform_capability_definition,
)
from VibeCADNativeSketchBatchBindings import (
    register_sketch_batch_capability_implementation,
)
from VibeCADNativeSketchBatchSchema import (
    register_sketch_batch_capability_definition,
)
from VibeCADNativeSketchGeometryBindings import (
    register_sketch_geometry_capability_implementation,
)
from VibeCADNativeSketchGeometrySchema import (
    register_sketch_geometry_capability_definition,
)
from VibeCADNativeSketchInspectBindings import (
    register_sketch_inspect_capability_implementation,
)
from VibeCADNativeSketchInspectSchema import (
    register_sketch_inspect_capability_definition,
)
from VibeCADNativeSketchPresentationBindings import (
    register_sketch_presentation_capability_implementation,
)
from VibeCADNativeSketchPresentationSchema import (
    register_sketch_presentation_capability_definition,
)
from VibeCADNativeSketchConstraintBindings import (
    register_sketch_constraint_capability_implementation,
)
from VibeCADNativeSketchConstraintSchema import (
    register_sketch_constraint_capability_definition,
)
from VibeCADNativeSketchControlBindings import (
    register_sketch_control_capability_implementation,
)
from VibeCADNativeSketchControlSchema import (
    register_sketch_control_capability_definition,
)


def build_native_capability_registry() -> NativeCapabilityRegistry:
    """Build a fresh fail-closed registry without document or GUI state."""

    registry = NativeCapabilityRegistry()
    register_common_capability_definitions(registry)
    register_common_capability_implementations(registry)
    register_assembly_joint_capability_definition(registry)
    register_assembly_joint_capability_implementation(registry)
    register_assembly_structure_capability_definition(registry)
    register_assembly_structure_capability_implementation(registry)
    register_component_interface_capability_definition(registry)
    register_component_interface_capability_implementation(registry)
    register_model_catalog_capability_definition(registry)
    register_model_catalog_capability_implementation(registry)
    register_model_structure_capability_definitions(registry)
    register_model_structure_capability_implementations(registry)
    register_model_boolean_capability_definition(registry)
    register_model_boolean_capability_implementation(registry)
    register_model_feature_capability_definition(registry)
    register_model_feature_capability_implementation(registry)
    register_model_fastener_capability_definition(registry)
    register_model_fastener_capability_implementation(registry)
    register_model_dressup_capability_definition(registry)
    register_model_dressup_capability_implementation(registry)
    register_model_hole_capability_definitions(registry)
    register_model_hole_capability_implementations(registry)
    register_model_join_capability_definition(registry)
    register_model_join_capability_implementation(registry)
    register_model_part_capability_definition(registry)
    register_model_part_capability_implementation(registry)
    register_model_surface_capability_definition(registry)
    register_model_surface_capability_implementation(registry)
    register_model_transform_capability_definition(registry)
    register_model_transform_capability_implementation(registry)
    register_sketch_batch_capability_definition(registry)
    register_sketch_batch_capability_implementation(registry)
    register_sketch_geometry_capability_definition(registry)
    register_sketch_geometry_capability_implementation(registry)
    register_sketch_constraint_capability_definition(registry)
    register_sketch_constraint_capability_implementation(registry)
    register_sketch_control_capability_definition(registry)
    register_sketch_control_capability_implementation(registry)
    register_sketch_inspect_capability_definition(registry)
    register_sketch_inspect_capability_implementation(registry)
    register_sketch_presentation_capability_definition(registry)
    register_sketch_presentation_capability_implementation(registry)
    return registry
