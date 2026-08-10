# SPDX-License-Identifier: LGPL-2.1-or-later

"""Production assembly point for document-bound Native runtimes."""

from __future__ import annotations

from typing import Any

from VibeCADNativeAssemblyDiagnosisBindings import (
    assembly_diagnosis_runtime_bindings,
)
from VibeCADNativeAssemblyDiagnosisRuntime import NativeAssemblyDiagnosisRuntime
from VibeCADNativeAssemblyJointBindings import assembly_joint_runtime_bindings
from VibeCADNativeAssemblyJointRuntime import NativeAssemblyJointRuntime
from VibeCADNativeAssemblyStructureBindings import (
    assembly_structure_runtime_bindings,
)
from VibeCADNativeAssemblyStructureRuntime import NativeAssemblyStructureRuntime
from VibeCADNativeCommonBindings import common_runtime_bindings
from VibeCADNativeCommonRuntime import NativeCommonRuntime
from VibeCADNativeComponentInterfaceBindings import (
    component_interface_runtime_bindings,
)
from VibeCADNativeComponentInterfaceRuntime import NativeComponentInterfaceRuntime
from VibeCADNativeModelCatalogBindings import model_catalog_runtime_bindings
from VibeCADNativeModelCatalogRuntime import NativeModelCatalogRuntime
from VibeCADNativeModelBooleanBindings import model_boolean_runtime_bindings
from VibeCADNativeModelBooleanRuntime import NativeModelBooleanRuntime
from VibeCADNativeModelFeatureBindings import model_feature_runtime_bindings
from VibeCADNativeModelFeatureRuntime import NativeModelFeatureRuntime
from VibeCADNativeModelFastenerBindings import model_fastener_runtime_bindings
from VibeCADNativeModelFastenerRuntime import NativeModelFastenerRuntime
from VibeCADNativeModelDressupBindings import model_dressup_runtime_bindings
from VibeCADNativeModelDressupRuntime import NativeModelDressupRuntime
from VibeCADNativeModelHoleBindings import model_hole_runtime_bindings
from VibeCADNativeModelHoleRuntime import NativeModelHoleRuntime
from VibeCADNativeModelJoinBindings import model_join_runtime_bindings
from VibeCADNativeModelJoinRuntime import NativeModelJoinRuntime
from VibeCADNativeModelPartBindings import model_part_runtime_bindings
from VibeCADNativeModelPartRuntime import NativeModelPartRuntime
from VibeCADNativeModelSurfaceBindings import model_surface_runtime_bindings
from VibeCADNativeModelSurfaceRuntime import NativeModelSurfaceRuntime
from VibeCADNativeModelStructureBindings import model_structure_runtime_bindings
from VibeCADNativeModelStructureRuntime import NativeModelStructureRuntime
from VibeCADNativeModelTransformBindings import model_transform_runtime_bindings
from VibeCADNativeModelTransformRuntime import NativeModelTransformRuntime
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchBatchBindings import sketch_batch_runtime_bindings
from VibeCADNativeSketchBatchRuntime import NativeSketchBatchRuntime
from VibeCADNativeSketchConstraintBindings import sketch_constraint_runtime_bindings
from VibeCADNativeSketchConstraintRuntime import NativeSketchConstraintRuntime
from VibeCADNativeSketchControlBindings import sketch_control_runtime_bindings
from VibeCADNativeSketchControlRuntime import NativeSketchControlRuntime
from VibeCADNativeSketchGeometryBindings import sketch_geometry_runtime_bindings
from VibeCADNativeSketchGeometryRuntime import NativeSketchGeometryRuntime
from VibeCADNativeSketchInspectBindings import sketch_inspect_runtime_bindings
from VibeCADNativeSketchInspectRuntime import NativeSketchInspectRuntime
from VibeCADNativeSketchPresentationBindings import (
    sketch_presentation_runtime_bindings,
)
from VibeCADNativeSketchPresentationRuntime import NativeSketchPresentationRuntime


def build_native_runtime_bindings(
    context: NativeRuntimeContext,
    tool_names: tuple[str, ...],
) -> dict[str, Any]:
    """Return fresh exact runtime bindings for one Native assistant turn."""

    if not isinstance(context, NativeRuntimeContext):
        raise TypeError("context must be a NativeRuntimeContext")
    common = NativeCommonRuntime(context=context)
    assembly_diagnosis = NativeAssemblyDiagnosisRuntime(context)
    assembly_joint = NativeAssemblyJointRuntime(context)
    assembly_structure = NativeAssemblyStructureRuntime(context)
    component_interface = NativeComponentInterfaceRuntime(context)
    model_catalog = NativeModelCatalogRuntime(context)
    model_boolean = NativeModelBooleanRuntime(context)
    model_feature = NativeModelFeatureRuntime(context)
    model_fastener = NativeModelFastenerRuntime(context)
    model_dressup = NativeModelDressupRuntime(context)
    model_hole = NativeModelHoleRuntime(context)
    model_join = NativeModelJoinRuntime(context)
    model_part = NativeModelPartRuntime(context)
    model_surface = NativeModelSurfaceRuntime(context)
    model_structure = NativeModelStructureRuntime(context)
    model_transform = NativeModelTransformRuntime(context)
    sketch_batch = NativeSketchBatchRuntime(context)
    sketch_constraint = NativeSketchConstraintRuntime(context)
    sketch_control = NativeSketchControlRuntime(context)
    sketch_geometry = NativeSketchGeometryRuntime(context)
    sketch_inspect = NativeSketchInspectRuntime(context)
    sketch_presentation = NativeSketchPresentationRuntime(context)
    available = {
        **common_runtime_bindings(common),
        **assembly_diagnosis_runtime_bindings(assembly_diagnosis),
        **assembly_joint_runtime_bindings(assembly_joint),
        **assembly_structure_runtime_bindings(assembly_structure),
        **component_interface_runtime_bindings(component_interface),
        **model_catalog_runtime_bindings(model_catalog),
        **model_boolean_runtime_bindings(model_boolean),
        **model_feature_runtime_bindings(model_feature),
        **model_fastener_runtime_bindings(model_fastener),
        **model_dressup_runtime_bindings(model_dressup),
        **model_hole_runtime_bindings(model_hole),
        **model_join_runtime_bindings(model_join),
        **model_part_runtime_bindings(model_part),
        **model_surface_runtime_bindings(model_surface),
        **model_structure_runtime_bindings(model_structure),
        **model_transform_runtime_bindings(model_transform),
        **sketch_batch_runtime_bindings(sketch_batch),
        **sketch_constraint_runtime_bindings(sketch_constraint),
        **sketch_control_runtime_bindings(sketch_control),
        **sketch_geometry_runtime_bindings(sketch_geometry),
        **sketch_inspect_runtime_bindings(sketch_inspect),
        **sketch_presentation_runtime_bindings(sketch_presentation),
    }
    missing = sorted(set(tool_names) - set(available))
    if missing:
        raise RuntimeError(f"Native runtime bindings are missing: {missing}.")
    return {name: available[name] for name in tool_names}
