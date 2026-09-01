# SPDX-License-Identifier: LGPL-2.1-or-later

"""Production assembly point for document-bound Native runtimes."""

from __future__ import annotations

from typing import Any

from VibeCADNativeAeroBindings import aero_solve_runtime_bindings
from VibeCADNativeAeroRuntime import NativeAeroRuntime
from VibeCADNativeAnalyzeInspectBindings import analyze_inspect_runtime_bindings
from VibeCADNativeAnalyzeFaceBindings import analyze_face_runtime_bindings
from VibeCADNativeAnalyzeFlowResultBindings import (
    analyze_flow_presentation_runtime_bindings,
    analyze_flow_result_runtime_bindings,
)
from VibeCADNativeAnalyzeMechanicalResultBindings import (
    analyze_mechanical_presentation_runtime_bindings,
    analyze_mechanical_result_runtime_bindings,
)
from VibeCADNativeAnalyzeThermalResultBindings import (
    analyze_thermal_presentation_runtime_bindings,
    analyze_thermal_result_runtime_bindings,
)
from VibeCADNativeAnalyzeInspectRuntime import NativeAnalyzeInspectRuntime
from VibeCADNativeAnalyzeAssignmentViewBindings import (
    analyze_assignment_view_runtime_bindings,
)
from VibeCADNativeAnalyzeAssignmentViewRuntime import (
    NativeAnalyzeAssignmentViewRuntime,
)
from VibeCADNativeAnalyzeGeometryBindings import analyze_geometry_runtime_bindings
from VibeCADNativeAnalyzeGeometryRuntime import NativeAnalyzeGeometryRuntime
from VibeCADNativeAnalyzeElectromagneticBindings import (
    analyze_electromagnetic_runtime_bindings,
)
from VibeCADNativeAnalyzeElectromagneticRuntime import (
    NativeAnalyzeElectromagneticRuntime,
)
from VibeCADNativeAnalyzeFluidBindings import analyze_fluid_runtime_bindings
from VibeCADNativeAnalyzeFluidCreateBindings import (
    analyze_fluid_create_runtime_bindings,
)
from VibeCADNativeAnalyzeCfdLifecycleBindings import (
    analyze_cfd_lifecycle_runtime_bindings,
)
from VibeCADNativeAnalyzeFluidRuntime import NativeAnalyzeFluidRuntime
from VibeCADNativeAnalyzeGeometricalBindings import (
    analyze_geometrical_runtime_bindings,
)
from VibeCADNativeAnalyzeGeometricalRuntime import NativeAnalyzeGeometricalRuntime
from VibeCADNativeAnalyzeSupportBindings import analyze_support_runtime_bindings
from VibeCADNativeAnalyzeStructuralLifecycleBindings import (
    analyze_structural_lifecycle_runtime_bindings,
)
from VibeCADNativeAnalyzeSupportRuntime import NativeAnalyzeSupportRuntime
from VibeCADNativeAnalyzeConnectionBindings import analyze_connection_runtime_bindings
from VibeCADNativeAnalyzeConnectionRuntime import NativeAnalyzeConnectionRuntime
from VibeCADNativeAnalyzeLoadBindings import analyze_load_runtime_bindings
from VibeCADNativeAnalyzeLoadRuntime import NativeAnalyzeLoadRuntime
from VibeCADNativeAnalyzeThermalBindings import analyze_thermal_runtime_bindings
from VibeCADNativeAnalyzeThermalRuntime import NativeAnalyzeThermalRuntime
from VibeCADNativeAnalyzeMeshBindings import analyze_mesh_runtime_bindings
from VibeCADNativeAnalyzeMeshLifecycleBindings import (
    analyze_mesh_lifecycle_runtime_bindings,
)
from VibeCADNativeAnalyzeMeshRuntime import NativeAnalyzeMeshRuntime
from VibeCADNativeAnalyzeMeshFieldBindings import analyze_mesh_field_runtime_bindings
from VibeCADNativeAnalyzeMeshFieldRuntime import NativeAnalyzeMeshFieldRuntime
from VibeCADNativeAnalyzeMeshOutputBindings import analyze_mesh_output_runtime_bindings
from VibeCADNativeAnalyzeMeshOutputRuntime import NativeAnalyzeMeshOutputRuntime
from VibeCADNativeAnalyzeMeshRefinementBindings import (
    analyze_mesh_refinement_runtime_bindings,
)
from VibeCADNativeAnalyzeLocalMeshBindings import (
    analyze_local_mesh_runtime_bindings,
)
from VibeCADNativeAnalyzeMeshRefinementRuntime import (
    NativeAnalyzeMeshRefinementRuntime,
)
from VibeCADNativeAnalyzeStructuredMeshBindings import (
    analyze_structured_mesh_runtime_bindings,
)
from VibeCADNativeAnalyzeStructuredMeshRuntime import (
    NativeAnalyzeStructuredMeshRuntime,
)
from VibeCADNativeAnalyzeSolverBindings import analyze_solver_runtime_bindings
from VibeCADNativeAnalyzeSolverRuntime import NativeAnalyzeSolverRuntime
from VibeCADNativeAnalyzeSolverControlBindings import (
    analyze_solver_control_runtime_bindings,
)
from VibeCADNativeAnalyzeSolverControlRuntime import (
    NativeAnalyzeSolverControlRuntime,
)
from VibeCADNativeAnalyzeSolverExecutionBindings import (
    analyze_solver_execution_runtime_bindings,
)
from VibeCADNativeAnalyzeSolverExecutionRuntime import (
    NativeAnalyzeSolverExecutionRuntime,
)
from VibeCADNativeAnalyzeRunBindings import analyze_run_solver_runtime_bindings
from VibeCADNativeAnalyzeEquationBindings import analyze_equation_runtime_bindings
from VibeCADNativeAnalyzeEquationRuntime import NativeAnalyzeEquationRuntime
from VibeCADNativeAnalyzeResultsBindings import analyze_results_runtime_bindings
from VibeCADNativeAnalyzeResultsRuntime import NativeAnalyzeResultsRuntime
from VibeCADNativeAnalyzePresentationBindings import (
    analyze_presentation_runtime_bindings,
)
from VibeCADNativeAnalyzePresentationRuntime import (
    NativeAnalyzePresentationRuntime,
)
from VibeCADNativeAnalyzePostBindings import analyze_post_runtime_bindings
from VibeCADNativeAnalyzePostRuntime import NativeAnalyzePostRuntime
from VibeCADNativeAnalyzePostFunctionBindings import (
    analyze_post_function_runtime_bindings,
)
from VibeCADNativeAnalyzePostFunctionRuntime import NativeAnalyzePostFunctionRuntime
from VibeCADNativeAnalyzeVisualizationBindings import (
    analyze_visualization_runtime_bindings,
)
from VibeCADNativeAnalyzeVisualizationRuntime import (
    NativeAnalyzeVisualizationRuntime,
)
from VibeCADNativeAnalyzeModelBindings import analyze_model_runtime_bindings
from VibeCADNativeAnalyzeModelRuntime import NativeAnalyzeModelRuntime
from VibeCADNativeAnalyzeSolidDomainBindings import (
    analyze_solid_domain_runtime_bindings,
)
from VibeCADNativeAssemblyDiagnosisBindings import (
    assembly_diagnosis_runtime_bindings,
)
from VibeCADNativeAssemblyDiagnosisRuntime import NativeAssemblyDiagnosisRuntime
from VibeCADNativeAssemblyBomBindings import assembly_bom_runtime_bindings
from VibeCADNativeAssemblyBomRuntime import NativeAssemblyBomRuntime
from VibeCADNativeAssemblyFastenerBindings import assembly_fastener_runtime_bindings
from VibeCADNativeAssemblyFastenerRuntime import NativeAssemblyFastenerRuntime
from VibeCADNativeAssemblyExportBindings import assembly_export_runtime_bindings
from VibeCADNativeAssemblyExportRuntime import NativeAssemblyExportRuntime
from VibeCADNativeAssemblyInspectBindings import assembly_inspect_runtime_bindings
from VibeCADNativeAssemblyInspectRuntime import NativeAssemblyInspectRuntime
from VibeCADNativeAssemblyJointBindings import assembly_joint_runtime_bindings
from VibeCADNativeAssemblyJointRuntime import NativeAssemblyJointRuntime
from VibeCADNativeAssemblyPlaybackBindings import (
    assembly_playback_runtime_bindings,
)
from VibeCADNativeAssemblyPlaybackRuntime import NativeAssemblyPlaybackRuntime
from VibeCADNativeAssemblyStructureBindings import (
    assembly_structure_runtime_bindings,
)
from VibeCADNativeAssemblyStructureRuntime import NativeAssemblyStructureRuntime
from VibeCADNativeCommonBindings import common_runtime_bindings
from VibeCADNativeInspectionCompareBindings import inspection_compare_runtime_bindings
from VibeCADNativeInspectionCompareRuntime import NativeInspectionCompareRuntime
from VibeCADNativeCommonRuntime import NativeCommonRuntime
from VibeCADNativeWorkspaceBindings import workspace_runtime_bindings
from VibeCADNativeWorkspaceRuntime import NativeWorkspaceRuntime
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
from VibeCADNativeModelHistoryBindings import model_history_runtime_bindings
from VibeCADNativeModelHistoryRuntime import NativeModelHistoryRuntime
from VibeCADNativeModelJoinBindings import model_join_runtime_bindings
from VibeCADNativeModelJoinRuntime import NativeModelJoinRuntime
from VibeCADNativeModelPartBindings import model_part_runtime_bindings
from VibeCADNativeModelPartRuntime import NativeModelPartRuntime
from VibeCADNativeModelSurfaceBindings import model_surface_runtime_bindings
from VibeCADNativeModelSurfaceRuntime import NativeModelSurfaceRuntime
from VibeCADNativeModelStructureBindings import model_structure_runtime_bindings
from VibeCADNativeModelStructureRuntime import NativeModelStructureRuntime
from VibeCADNativeSketchSetupBindings import sketch_setup_runtime_bindings
from VibeCADNativeSketchSetupRuntime import NativeSketchSetupRuntime
from VibeCADNativeModelTransformBindings import model_transform_runtime_bindings
from VibeCADNativeModelTransformRuntime import NativeModelTransformRuntime
from VibeCADNativeManufactureInspectBindings import (
    manufacture_inspect_runtime_bindings,
)
from VibeCADNativeManufactureFocusedInspectBindings import (
    manufacture_focused_inspect_runtime_bindings,
)
from VibeCADNativeManufactureInspectRuntime import NativeManufactureInspectRuntime
from VibeCADNativeManufactureJobBindings import manufacture_job_runtime_bindings
from VibeCADNativeManufactureJobRuntime import NativeManufactureJobRuntime
from VibeCADNativeManufactureJobSchema import MANUFACTURE_JOB_CAPABILITY_NAME
from VibeCADNativeManufactureAreaBindings import manufacture_area_runtime_bindings
from VibeCADNativeManufactureAreaRuntime import NativeManufactureAreaRuntime
from VibeCADNativeManufactureModifyBindings import (
    manufacture_modify_runtime_bindings,
)
from VibeCADNativeManufactureFocusedModifyBindings import (
    manufacture_focused_modify_runtime_bindings,
)
from VibeCADNativeManufactureModifyRuntime import NativeManufactureModifyRuntime
from VibeCADNativeManufactureProgramBindings import (
    manufacture_program_runtime_bindings,
)
from VibeCADNativeManufactureProgramRuntime import NativeManufactureProgramRuntime
from VibeCADNativeManufactureProbeBindings import manufacture_probe_runtime_bindings
from VibeCADNativeManufactureProbeRuntime import NativeManufactureProbeRuntime
from VibeCADNativeManufacturePropertyBagBindings import (
    manufacture_property_bag_runtime_bindings,
)
from VibeCADNativeManufacturePropertyBagRuntime import (
    NativeManufacturePropertyBagRuntime,
)
from VibeCADNativeManufactureOperationBindings import (
    manufacture_operation_runtime_bindings,
)
from VibeCADNativeManufactureFocusedOperationBindings import (
    manufacture_focused_operation_runtime_bindings,
)
from VibeCADNativeManufactureOperationRuntime import (
    NativeManufactureOperationRuntime,
)
from VibeCADNativeManufactureOperationGeneration import (
    start_background_operation_mutation,
)
from VibeCADNativeManufactureCamoticsBindings import (
    manufacture_camotics_runtime_bindings,
)
from VibeCADNativeManufactureCamoticsRuntime import (
    NativeManufactureCamoticsRuntime,
)
from VibeCADNativeManufacturePostBindings import manufacture_post_runtime_bindings
from VibeCADNativeManufactureFocusedPostBindings import (
    manufacture_focused_post_runtime_bindings,
)
from VibeCADNativeManufacturePostRuntime import NativeManufacturePostRuntime
from VibeCADNativeManufactureTemplateBindings import (
    manufacture_template_runtime_bindings,
)
from VibeCADNativeManufactureTemplateRuntime import (
    NativeManufactureTemplateRuntime,
)
from VibeCADNativeManufactureSimulationBindings import (
    manufacture_simulation_runtime_bindings,
)
from VibeCADNativeManufactureSimulationControlBindings import (
    manufacture_simulation_control_runtime_bindings,
)
from VibeCADNativeManufactureSimulationRuntime import (
    NativeManufactureSimulationRuntime,
)
from VibeCADNativeManufactureSimulationResultBindings import (
    manufacture_simulation_result_runtime_bindings,
)
from VibeCADNativeManufactureSimulationResultRuntime import (
    NativeManufactureSimulationResultRuntime,
)
from VibeCADNativeManufactureFollowUpBindings import (
    manufacture_follow_up_runtime_bindings,
)
from VibeCADNativeManufactureFollowUpRuntime import (
    NativeManufactureFollowUpRuntime,
)
from VibeCADNativeManufactureFollowUpSchema import (
    MANUFACTURE_FOLLOW_UP_CAPABILITY_NAME,
)
from VibeCADNativeManufactureToolBindings import manufacture_tool_runtime_bindings
from VibeCADNativeManufactureFocusedToolBindings import (
    manufacture_focused_tool_runtime_bindings,
)
from VibeCADNativeManufactureToolRuntime import (
    NativeManufactureToolCatalogRuntime,
    NativeManufactureToolRuntime,
)
from VibeCADNativeManufactureToolSchema import (
    MANUFACTURE_TOOL_CATALOG_CAPABILITY_NAME,
    MANUFACTURE_TOOL_CAPABILITY_NAME,
)
from VibeCADNativeManufactureToolOutputBindings import (
    manufacture_tool_output_runtime_bindings,
)
from VibeCADNativeManufactureToolOutputRuntime import (
    NativeManufactureToolOutputRuntime,
)
from VibeCADNativeDrawingPageBindings import drawing_page_runtime_bindings
from VibeCADNativeDrawingPageRuntime import NativeDrawingPageRuntime
from VibeCADNativeDrawingActiveViewBindings import (
    drawing_active_view_runtime_bindings,
)
from VibeCADNativeDrawingActiveViewRuntime import NativeDrawingActiveViewRuntime
from VibeCADNativeDrawingViewBindings import drawing_view_runtime_bindings
from VibeCADNativeDrawingViewRuntime import NativeDrawingViewRuntime
from VibeCADNativeDrawingSectionBindings import drawing_section_runtime_bindings
from VibeCADNativeDrawingSectionRuntime import NativeDrawingSectionRuntime
from VibeCADNativeDrawingComplexSectionBindings import (
    drawing_complex_section_runtime_bindings,
)
from VibeCADNativeDrawingComplexSectionRuntime import (
    NativeDrawingComplexSectionRuntime,
)
from VibeCADNativeDrawingDetailBindings import drawing_detail_runtime_bindings
from VibeCADNativeDrawingDetailRuntime import NativeDrawingDetailRuntime
from VibeCADNativeDrawingDraftBindings import drawing_draft_runtime_bindings
from VibeCADNativeDrawingDraftRuntime import NativeDrawingDraftRuntime
from VibeCADNativeDrawingClipBindings import drawing_clip_runtime_bindings
from VibeCADNativeDrawingClipRuntime import NativeDrawingClipRuntime
from VibeCADNativeDrawingStackBindings import drawing_stack_runtime_bindings
from VibeCADNativeDrawingStackRuntime import NativeDrawingStackRuntime
from VibeCADNativeDrawingDimensionBindings import (
    drawing_dimension_runtime_bindings,
)
from VibeCADNativeDrawingDimensionRuntime import NativeDrawingDimensionRuntime
from VibeCADNativeDrawingDimensionInferenceBindings import (
    drawing_dimension_inference_runtime_bindings,
)
from VibeCADNativeDrawingDimensionInferenceRuntime import (
    NativeDrawingDimensionInferenceRuntime,
)
from VibeCADNativeDrawingDimensionSeriesBindings import (
    drawing_dimension_series_runtime_bindings,
)
from VibeCADNativeDrawingDimensionSeriesRuntime import (
    NativeDrawingDimensionSeriesRuntime,
)
from VibeCADNativeParametersBindings import parameters_runtime_bindings
from VibeCADNativeParametersRuntime import NativeParametersRuntime
from VibeCADNativeDrawingDimensionRepairBindings import (
    drawing_dimension_repair_runtime_bindings,
)
from VibeCADNativeDrawingDimensionRepairRuntime import (
    NativeDrawingDimensionRepairRuntime,
)
from VibeCADNativeDrawingLineDefaultsBindings import (
    drawing_line_defaults_runtime_bindings,
)
from VibeCADNativeDrawingLineDefaultsRuntime import (
    NativeDrawingLineDefaultsRuntime,
)
from VibeCADNativeDrawingLineAttributesBindings import (
    drawing_line_attributes_runtime_bindings,
)
from VibeCADNativeDrawingLineAttributesRuntime import (
    NativeDrawingLineAttributesRuntime,
)
from VibeCADNativeDrawingLineLengthBindings import (
    drawing_line_length_runtime_bindings,
)
from VibeCADNativeDrawingLineLengthRuntime import (
    NativeDrawingLineLengthRuntime,
)
from VibeCADNativeDrawingViewLockBindings import (
    drawing_view_lock_runtime_bindings,
)
from VibeCADNativeDrawingViewLockRuntime import (
    NativeDrawingViewLockRuntime,
)
from VibeCADNativeDrawingPlacementBindings import (
    drawing_placement_runtime_bindings,
)
from VibeCADNativeDrawingPlacementRuntime import NativeDrawingPlacementRuntime
from VibeCADNativeDrawingSectionPositionBindings import (
    drawing_section_position_runtime_bindings,
)
from VibeCADNativeDrawingSectionPositionRuntime import (
    NativeDrawingSectionPositionRuntime,
)
from VibeCADNativeDrawingFormatBindings import (
    drawing_format_runtime_bindings,
)
from VibeCADNativeDrawingFormatRuntime import NativeDrawingFormatRuntime
from VibeCADNativeDrawingDimensionTextBindings import (
    drawing_dimension_text_runtime_bindings,
)
from VibeCADNativeDrawingDimensionTextRuntime import (
    NativeDrawingDimensionTextRuntime,
)
from VibeCADNativeDrawingPresentationBindings import (
    drawing_presentation_runtime_bindings,
)
from VibeCADNativeDrawingPresentationRuntime import (
    NativeDrawingPresentationRuntime,
)
from VibeCADNativeDrawingHatchBindings import drawing_hatch_runtime_bindings
from VibeCADNativeDrawingHatchRuntime import NativeDrawingHatchRuntime
from VibeCADNativeDrawingRichAnnotationBindings import (
    drawing_rich_annotation_runtime_bindings,
)
from VibeCADNativeDrawingRichAnnotationRuntime import (
    NativeDrawingRichAnnotationRuntime,
)
from VibeCADNativeDrawingSymbolBindings import drawing_symbol_runtime_bindings
from VibeCADNativeDrawingSymbolRuntime import NativeDrawingSymbolRuntime
from VibeCADNativeDrawingExportBindings import drawing_export_runtime_bindings
from VibeCADNativeDrawingExportRuntime import NativeDrawingExportRuntime
from VibeCADNativeDrawingLeaderBindings import drawing_leader_runtime_bindings
from VibeCADNativeDrawingLeaderRuntime import NativeDrawingLeaderRuntime
from VibeCADNativeDrawingCircleCenterLineBindings import (
    drawing_circle_center_line_runtime_bindings,
)
from VibeCADNativeDrawingCircleCenterLineRuntime import (
    NativeDrawingCircleCenterLineRuntime,
)
from VibeCADNativeDrawingGeneralCenterLineBindings import (
    drawing_general_center_line_runtime_bindings,
)
from VibeCADNativeDrawingGeneralCenterLineRuntime import (
    NativeDrawingGeneralCenterLineRuntime,
)
from VibeCADNativeDrawingBoltCircleCenterLineBindings import (
    drawing_bolt_circle_center_line_runtime_bindings,
)
from VibeCADNativeDrawingBoltCircleCenterLineRuntime import (
    NativeDrawingBoltCircleCenterLineRuntime,
)
from VibeCADNativeDrawingThreadRepresentationBindings import (
    drawing_thread_representation_runtime_bindings,
)
from VibeCADNativeDrawingThreadRepresentationRuntime import (
    NativeDrawingThreadRepresentationRuntime,
)
from VibeCADNativeDrawingCosmeticVertexBindings import (
    drawing_cosmetic_vertex_runtime_bindings,
)
from VibeCADNativeDrawingCosmeticVertexRuntime import (
    NativeDrawingCosmeticVertexRuntime,
)
from VibeCADNativeDrawingCosmeticCurveBindings import (
    drawing_cosmetic_curve_runtime_bindings,
)
from VibeCADNativeDrawingCosmeticCurveRuntime import (
    NativeDrawingCosmeticCurveRuntime,
)
from VibeCADNativeDrawingCosmeticLineBindings import (
    drawing_cosmetic_line_runtime_bindings,
)
from VibeCADNativeDrawingCosmeticLineRuntime import (
    NativeDrawingCosmeticLineRuntime,
)
from VibeCADNativeDrawingBalloonBindings import drawing_balloon_runtime_bindings
from VibeCADNativeDrawingBalloonRuntime import NativeDrawingBalloonRuntime
from VibeCADNativeRobotSetupBindings import robot_setup_runtime_bindings
from VibeCADNativeRobotSetupRuntime import NativeRobotSetupRuntime
from VibeCADNativeRobotMotionBindings import robot_motion_runtime_bindings
from VibeCADNativeRobotMotionRuntime import NativeRobotMotionRuntime
from VibeCADNativeRobotExportBindings import robot_export_runtime_bindings
from VibeCADNativeRobotExportRuntime import NativeRobotExportRuntime
from VibeCADNativeRobotTrajectoryBindings import robot_trajectory_runtime_bindings
from VibeCADNativeRobotTrajectoryRuntime import NativeRobotTrajectoryRuntime
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeMeshConvertBindings import mesh_convert_runtime_bindings
from VibeCADNativeMeshConvertRuntime import NativeMeshConvertRuntime
from VibeCADNativeMeshBooleanBindings import mesh_boolean_runtime_bindings
from VibeCADNativeMeshBooleanRuntime import NativeMeshBooleanRuntime
from VibeCADNativeMeshCutBindings import mesh_cut_runtime_bindings
from VibeCADNativeMeshCutRuntime import NativeMeshCutRuntime
from VibeCADNativeMeshCurvatureBindings import mesh_curvature_runtime_bindings
from VibeCADNativeMeshCurvatureRuntime import NativeMeshCurvatureRuntime
from VibeCADNativeMeshInspectBindings import mesh_inspect_runtime_bindings
from VibeCADNativeMeshInspectRuntime import NativeMeshInspectRuntime
from VibeCADNativeMeshSegmentBindings import mesh_segment_runtime_bindings
from VibeCADNativeMeshSegmentRuntime import NativeMeshSegmentRuntime
from VibeCADNativeMeshIOBindings import mesh_io_runtime_bindings
from VibeCADNativeMeshIORuntime import NativeMeshIORuntime
from VibeCADNativeMeshModifyBindings import mesh_modify_runtime_bindings
from VibeCADNativeMeshModifyRuntime import NativeMeshModifyRuntime
from VibeCADNativeMeshPointsBindings import mesh_points_runtime_bindings
from VibeCADNativeMeshPointsRuntime import NativeMeshPointsRuntime
from VibeCADNativeReverseBindings import reverse_runtime_bindings
from VibeCADNativeReverseRuntime import NativeReverseRuntime
from VibeCADNativeReconstructParametricBindings import (
    reconstruct_parametric_runtime_bindings,
)
from VibeCADNativeReconstructParametricRuntime import NativeReconstructParametricRuntime
from VibeCADNativeMeshExportBindings import mesh_export_runtime_bindings
from VibeCADNativeMeshExportRuntime import NativeMeshExportRuntime
from VibeCADNativeBackgroundBindings import native_background_runtime_bindings
from VibeCADNativeBackgroundRuntime import NativeBackgroundRuntime
from VibeCADNativeSketchProviderBindings import sketch_provider_runtime_bindings
from VibeCADNativeSketchProviderRuntime import NativeSketchProviderRuntime


def build_native_runtime_bindings(
    context: NativeRuntimeContext,
    tool_names: tuple[str, ...],
) -> dict[str, Any]:
    """Return fresh exact runtime bindings for one Native assistant turn."""

    if not isinstance(context, NativeRuntimeContext):
        raise TypeError("context must be a NativeRuntimeContext")
    analyze_model = NativeAnalyzeModelRuntime(context)
    analyze_inspect = NativeAnalyzeInspectRuntime(context)
    analyze_assignment_view = NativeAnalyzeAssignmentViewRuntime(context)
    analyze_geometry = NativeAnalyzeGeometryRuntime(context)
    analyze_electromagnetic = NativeAnalyzeElectromagneticRuntime(context)
    analyze_fluid = NativeAnalyzeFluidRuntime(context)
    analyze_geometrical = NativeAnalyzeGeometricalRuntime(context)
    analyze_support = NativeAnalyzeSupportRuntime(context)
    analyze_connection = NativeAnalyzeConnectionRuntime(context)
    analyze_load = NativeAnalyzeLoadRuntime(context)
    analyze_thermal = NativeAnalyzeThermalRuntime(context)
    analyze_mesh = NativeAnalyzeMeshRuntime(context)
    analyze_mesh_field = NativeAnalyzeMeshFieldRuntime(context)
    analyze_mesh_output = NativeAnalyzeMeshOutputRuntime(context)
    analyze_mesh_refinement = NativeAnalyzeMeshRefinementRuntime(context)
    analyze_structured_mesh = NativeAnalyzeStructuredMeshRuntime(context)
    analyze_solver = NativeAnalyzeSolverRuntime(context)
    analyze_solver_control = NativeAnalyzeSolverControlRuntime(context)
    analyze_solver_execution = NativeAnalyzeSolverExecutionRuntime(context)
    analyze_equation = NativeAnalyzeEquationRuntime(context)
    analyze_results = NativeAnalyzeResultsRuntime(context)
    analyze_presentation = NativeAnalyzePresentationRuntime(context)
    analyze_post = NativeAnalyzePostRuntime(context)
    analyze_post_function = NativeAnalyzePostFunctionRuntime(context)
    analyze_visualization = NativeAnalyzeVisualizationRuntime(context)
    common = NativeCommonRuntime(context=context)
    inspection_compare = NativeInspectionCompareRuntime(context)
    workspace = NativeWorkspaceRuntime(context)
    background = NativeBackgroundRuntime(context)
    mesh_convert = NativeMeshConvertRuntime(context)
    mesh_io = NativeMeshIORuntime(context)
    mesh_export = NativeMeshExportRuntime(context)
    mesh_modify = NativeMeshModifyRuntime(context)
    mesh_boolean = NativeMeshBooleanRuntime(context)
    mesh_cut = NativeMeshCutRuntime(context)
    mesh_inspect = NativeMeshInspectRuntime(context)
    mesh_curvature = NativeMeshCurvatureRuntime(context)
    mesh_segment = NativeMeshSegmentRuntime(context)
    mesh_points = NativeMeshPointsRuntime(context)
    mesh_rebuild = NativeReverseRuntime(context, "mesh.rebuild")
    mesh_approximate = NativeReverseRuntime(context, "mesh.approximate")
    mesh_reconstruct_parametric = NativeReconstructParametricRuntime(context)
    assembly_diagnosis = NativeAssemblyDiagnosisRuntime(context)
    assembly_bom = NativeAssemblyBomRuntime(context)
    assembly_fastener = NativeAssemblyFastenerRuntime(context)
    assembly_export = NativeAssemblyExportRuntime(context)
    assembly_inspect = NativeAssemblyInspectRuntime(context)
    assembly_joint = NativeAssemblyJointRuntime(context)
    assembly_playback = NativeAssemblyPlaybackRuntime(context)
    assembly_structure = NativeAssemblyStructureRuntime(context)
    component_interface = NativeComponentInterfaceRuntime(context)
    model_catalog = NativeModelCatalogRuntime(context)
    model_boolean = NativeModelBooleanRuntime(context)
    model_feature = NativeModelFeatureRuntime(context)
    model_fastener = NativeModelFastenerRuntime(context)
    model_dressup = NativeModelDressupRuntime(context)
    model_hole = NativeModelHoleRuntime(context)
    model_history = NativeModelHistoryRuntime(context)
    model_join = NativeModelJoinRuntime(context)
    model_part = NativeModelPartRuntime(context)
    model_surface = NativeModelSurfaceRuntime(context)
    model_structure = NativeModelStructureRuntime(context)
    sketch_setup = NativeSketchSetupRuntime(context)
    model_transform = NativeModelTransformRuntime(context)
    manufacture_inspect = NativeManufactureInspectRuntime(context)
    manufacture_job = (
        NativeManufactureJobRuntime(context)
        if MANUFACTURE_JOB_CAPABILITY_NAME in tool_names
        else None
    )
    manufacture_area = NativeManufactureAreaRuntime(context)
    manufacture_modify = NativeManufactureModifyRuntime(context)
    manufacture_program = NativeManufactureProgramRuntime(context)
    manufacture_probe = NativeManufactureProbeRuntime(context)
    manufacture_property_bag = NativeManufacturePropertyBagRuntime(context)
    manufacture_operation = NativeManufactureOperationRuntime(
        context,
        mutation_executor=start_background_operation_mutation,
    )
    manufacture_camotics = NativeManufactureCamoticsRuntime(context)
    manufacture_post = NativeManufacturePostRuntime(context)
    manufacture_template = NativeManufactureTemplateRuntime(context)
    manufacture_simulation = NativeManufactureSimulationRuntime(context)
    manufacture_simulation_result = NativeManufactureSimulationResultRuntime(context)
    manufacture_follow_up = (
        NativeManufactureFollowUpRuntime(context)
        if MANUFACTURE_FOLLOW_UP_CAPABILITY_NAME in tool_names
        else None
    )
    manufacture_tool_catalog = (
        NativeManufactureToolCatalogRuntime(context)
        if MANUFACTURE_TOOL_CATALOG_CAPABILITY_NAME in tool_names
        else None
    )
    manufacture_tool = NativeManufactureToolRuntime(context)
    manufacture_tool_output = NativeManufactureToolOutputRuntime(context)
    drawing_page = NativeDrawingPageRuntime(context)
    drawing_active_view = NativeDrawingActiveViewRuntime(context)
    drawing_view = NativeDrawingViewRuntime(context)
    drawing_section = NativeDrawingSectionRuntime(context)
    drawing_complex_section = NativeDrawingComplexSectionRuntime(context)
    drawing_detail = NativeDrawingDetailRuntime(context)
    drawing_draft = NativeDrawingDraftRuntime(context)
    drawing_clip = NativeDrawingClipRuntime(context)
    drawing_stack = NativeDrawingStackRuntime(context)
    drawing_dimension = NativeDrawingDimensionRuntime(context)
    drawing_dimension_inference = NativeDrawingDimensionInferenceRuntime(context)
    drawing_dimension_series = NativeDrawingDimensionSeriesRuntime(context)
    parameters = NativeParametersRuntime(context)
    drawing_dimension_repair = NativeDrawingDimensionRepairRuntime(context)
    drawing_line_defaults = NativeDrawingLineDefaultsRuntime(context)
    drawing_line_attributes = NativeDrawingLineAttributesRuntime(context)
    drawing_line_length = NativeDrawingLineLengthRuntime(context)
    drawing_view_lock = NativeDrawingViewLockRuntime(context)
    drawing_placement = NativeDrawingPlacementRuntime(context)
    drawing_section_position = NativeDrawingSectionPositionRuntime(context)
    drawing_format = NativeDrawingFormatRuntime(context)
    drawing_dimension_text = NativeDrawingDimensionTextRuntime(context)
    drawing_presentation = NativeDrawingPresentationRuntime(context)
    drawing_hatch = NativeDrawingHatchRuntime(context)
    drawing_rich_annotation = NativeDrawingRichAnnotationRuntime(context)
    drawing_symbol = NativeDrawingSymbolRuntime(context)
    drawing_export = NativeDrawingExportRuntime(context)
    drawing_leader = NativeDrawingLeaderRuntime(context)
    drawing_circle_center_line = NativeDrawingCircleCenterLineRuntime(context)
    drawing_general_center_line = NativeDrawingGeneralCenterLineRuntime(context)
    drawing_bolt_circle_center_line = NativeDrawingBoltCircleCenterLineRuntime(
        context
    )
    drawing_thread_representation = NativeDrawingThreadRepresentationRuntime(
        context
    )
    drawing_cosmetic_vertex = NativeDrawingCosmeticVertexRuntime(context)
    drawing_cosmetic_curve = NativeDrawingCosmeticCurveRuntime(context)
    drawing_cosmetic_line = NativeDrawingCosmeticLineRuntime(context)
    drawing_balloon = NativeDrawingBalloonRuntime(context)
    robot_setup = NativeRobotSetupRuntime(context)
    robot_motion = NativeRobotMotionRuntime(context)
    robot_export = NativeRobotExportRuntime(context)
    robot_trajectory = NativeRobotTrajectoryRuntime(context)
    sketch_provider = NativeSketchProviderRuntime(context)
    aero_solve = NativeAeroRuntime(context)
    available = {
        **analyze_model_runtime_bindings(analyze_model),
        **analyze_solid_domain_runtime_bindings(analyze_model),
        **analyze_inspect_runtime_bindings(analyze_inspect),
        **analyze_face_runtime_bindings(analyze_inspect),
        **analyze_flow_result_runtime_bindings(analyze_inspect),
        **analyze_mechanical_result_runtime_bindings(analyze_inspect),
        **analyze_thermal_result_runtime_bindings(analyze_inspect),
        **analyze_assignment_view_runtime_bindings(analyze_assignment_view),
        **analyze_geometry_runtime_bindings(analyze_geometry),
        **analyze_electromagnetic_runtime_bindings(analyze_electromagnetic),
        **analyze_fluid_runtime_bindings(analyze_fluid),
        **analyze_fluid_create_runtime_bindings(analyze_fluid),
        **analyze_cfd_lifecycle_runtime_bindings(analyze_model, analyze_solver),
        **analyze_geometrical_runtime_bindings(analyze_geometrical),
        **analyze_support_runtime_bindings(analyze_support),
        **analyze_structural_lifecycle_runtime_bindings(
            analyze_model,
            analyze_support,
            analyze_load,
        ),
        **analyze_connection_runtime_bindings(analyze_connection),
        **analyze_load_runtime_bindings(analyze_load),
        **analyze_thermal_runtime_bindings(analyze_thermal),
        **analyze_mesh_runtime_bindings(analyze_mesh),
        **analyze_mesh_lifecycle_runtime_bindings(analyze_mesh),
        **analyze_mesh_field_runtime_bindings(analyze_mesh_field),
        **analyze_mesh_output_runtime_bindings(analyze_mesh_output),
        **analyze_mesh_refinement_runtime_bindings(analyze_mesh_refinement),
        **analyze_local_mesh_runtime_bindings(analyze_mesh_refinement),
        **analyze_structured_mesh_runtime_bindings(analyze_structured_mesh),
        **analyze_solver_runtime_bindings(analyze_solver),
        **analyze_solver_control_runtime_bindings(analyze_solver_control),
        **analyze_solver_execution_runtime_bindings(analyze_solver_execution),
        **analyze_run_solver_runtime_bindings(analyze_solver_execution),
        **analyze_equation_runtime_bindings(analyze_equation),
        **analyze_results_runtime_bindings(analyze_results),
        **analyze_presentation_runtime_bindings(analyze_presentation),
        **analyze_flow_presentation_runtime_bindings(analyze_presentation),
        **analyze_mechanical_presentation_runtime_bindings(analyze_presentation),
        **analyze_thermal_presentation_runtime_bindings(analyze_presentation),
        **analyze_post_runtime_bindings(analyze_post),
        **analyze_post_function_runtime_bindings(analyze_post_function),
        **analyze_visualization_runtime_bindings(analyze_visualization),
        **aero_solve_runtime_bindings(aero_solve),
        **common_runtime_bindings(common),
        **inspection_compare_runtime_bindings(inspection_compare),
        **workspace_runtime_bindings(workspace),
        **native_background_runtime_bindings(background),
        **mesh_convert_runtime_bindings(mesh_convert),
        **mesh_io_runtime_bindings(mesh_io),
        **mesh_export_runtime_bindings(mesh_export),
        **mesh_modify_runtime_bindings(mesh_modify),
        **mesh_boolean_runtime_bindings(mesh_boolean),
        **mesh_cut_runtime_bindings(mesh_cut),
        **mesh_inspect_runtime_bindings(mesh_inspect),
        **mesh_curvature_runtime_bindings(mesh_curvature),
        **mesh_segment_runtime_bindings(mesh_segment),
        **mesh_points_runtime_bindings(mesh_points),
        **reverse_runtime_bindings(mesh_rebuild, mesh_approximate),
        **reconstruct_parametric_runtime_bindings(mesh_reconstruct_parametric),
        **assembly_diagnosis_runtime_bindings(assembly_diagnosis),
        **assembly_bom_runtime_bindings(assembly_bom),
        **assembly_fastener_runtime_bindings(assembly_fastener),
        **assembly_export_runtime_bindings(assembly_export),
        **assembly_inspect_runtime_bindings(assembly_inspect),
        **assembly_joint_runtime_bindings(assembly_joint),
        **assembly_playback_runtime_bindings(assembly_playback),
        **assembly_structure_runtime_bindings(assembly_structure),
        **component_interface_runtime_bindings(component_interface),
        **model_catalog_runtime_bindings(model_catalog),
        **model_boolean_runtime_bindings(model_boolean),
        **model_feature_runtime_bindings(model_feature),
        **model_fastener_runtime_bindings(model_fastener),
        **model_dressup_runtime_bindings(model_dressup),
        **model_hole_runtime_bindings(model_hole),
        **model_history_runtime_bindings(model_history),
        **model_join_runtime_bindings(model_join),
        **model_part_runtime_bindings(model_part),
        **model_surface_runtime_bindings(model_surface),
        **model_structure_runtime_bindings(model_structure),
        **sketch_setup_runtime_bindings(sketch_setup),
        **model_transform_runtime_bindings(model_transform),
        **manufacture_inspect_runtime_bindings(manufacture_inspect),
        **manufacture_focused_inspect_runtime_bindings(manufacture_inspect),
        **(
            manufacture_job_runtime_bindings(manufacture_job)
            if manufacture_job is not None
            else {}
        ),
        **manufacture_area_runtime_bindings(manufacture_area),
        **manufacture_modify_runtime_bindings(manufacture_modify),
        **manufacture_focused_modify_runtime_bindings(manufacture_modify),
        **manufacture_program_runtime_bindings(manufacture_program),
        **manufacture_probe_runtime_bindings(manufacture_probe),
        **manufacture_property_bag_runtime_bindings(manufacture_property_bag),
        **manufacture_operation_runtime_bindings(manufacture_operation),
        **manufacture_focused_operation_runtime_bindings(manufacture_operation),
        **manufacture_camotics_runtime_bindings(manufacture_camotics),
        **manufacture_post_runtime_bindings(manufacture_post),
        **manufacture_focused_post_runtime_bindings(manufacture_post),
        **manufacture_template_runtime_bindings(manufacture_template),
        **manufacture_simulation_runtime_bindings(manufacture_simulation),
        **manufacture_simulation_control_runtime_bindings(manufacture_simulation),
        **manufacture_simulation_result_runtime_bindings(
            manufacture_simulation_result
        ),
        **(
            manufacture_follow_up_runtime_bindings(manufacture_follow_up)
            if manufacture_follow_up is not None
            else {}
        ),
        **(
            manufacture_tool_runtime_bindings(
                manufacture_tool_catalog,
                manufacture_tool,
            )
            if manufacture_tool_catalog is not None
            else {MANUFACTURE_TOOL_CAPABILITY_NAME: manufacture_tool}
        ),
        **manufacture_focused_tool_runtime_bindings(manufacture_tool),
        **manufacture_tool_output_runtime_bindings(manufacture_tool_output),
        **drawing_page_runtime_bindings(drawing_page),
        **drawing_active_view_runtime_bindings(drawing_active_view),
        **drawing_view_runtime_bindings(drawing_view),
        **drawing_section_runtime_bindings(drawing_section),
        **drawing_complex_section_runtime_bindings(drawing_complex_section),
        **drawing_detail_runtime_bindings(drawing_detail),
        **drawing_draft_runtime_bindings(drawing_draft),
        **drawing_clip_runtime_bindings(drawing_clip),
        **drawing_stack_runtime_bindings(drawing_stack),
        **drawing_dimension_runtime_bindings(drawing_dimension),
        **drawing_dimension_inference_runtime_bindings(drawing_dimension_inference),
        **drawing_dimension_series_runtime_bindings(drawing_dimension_series),
        **parameters_runtime_bindings(parameters),
        **drawing_dimension_repair_runtime_bindings(drawing_dimension_repair),
        **drawing_line_defaults_runtime_bindings(drawing_line_defaults),
        **drawing_line_attributes_runtime_bindings(drawing_line_attributes),
        **drawing_line_length_runtime_bindings(drawing_line_length),
        **drawing_view_lock_runtime_bindings(drawing_view_lock),
        **drawing_placement_runtime_bindings(drawing_placement),
        **drawing_section_position_runtime_bindings(drawing_section_position),
        **drawing_format_runtime_bindings(drawing_format),
        **drawing_dimension_text_runtime_bindings(drawing_dimension_text),
        **drawing_presentation_runtime_bindings(drawing_presentation),
        **drawing_hatch_runtime_bindings(drawing_hatch),
        **drawing_rich_annotation_runtime_bindings(drawing_rich_annotation),
        **drawing_symbol_runtime_bindings(drawing_symbol),
        **drawing_export_runtime_bindings(drawing_export),
        **drawing_leader_runtime_bindings(drawing_leader),
        **drawing_circle_center_line_runtime_bindings(
            drawing_circle_center_line
        ),
        **drawing_general_center_line_runtime_bindings(
            drawing_general_center_line
        ),
        **drawing_bolt_circle_center_line_runtime_bindings(
            drawing_bolt_circle_center_line
        ),
        **drawing_thread_representation_runtime_bindings(
            drawing_thread_representation
        ),
        **drawing_cosmetic_vertex_runtime_bindings(drawing_cosmetic_vertex),
        **drawing_cosmetic_curve_runtime_bindings(drawing_cosmetic_curve),
        **drawing_cosmetic_line_runtime_bindings(drawing_cosmetic_line),
        **drawing_balloon_runtime_bindings(drawing_balloon),
        **robot_setup_runtime_bindings(robot_setup),
        **robot_motion_runtime_bindings(robot_motion),
        **robot_export_runtime_bindings(robot_export),
        **robot_trajectory_runtime_bindings(robot_trajectory),
        **sketch_provider_runtime_bindings(sketch_provider),
    }
    missing = sorted(set(tool_names) - set(available))
    if missing:
        raise RuntimeError(f"Native runtime bindings are missing: {missing}.")
    return {name: available[name] for name in tool_names}
