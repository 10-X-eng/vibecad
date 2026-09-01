# SPDX-License-Identifier: LGPL-2.1-or-later

"""Production assembly point for Native capability contracts and bindings."""

from __future__ import annotations

from VibeCADNativeAnalyzeInspectBindings import (
    register_analyze_inspect_capability_implementation,
)
from VibeCADNativeAnalyzeFaceBindings import (
    register_analyze_face_capability_implementation,
)
from VibeCADNativeAnalyzeFaceSchema import (
    register_analyze_face_capability_definition,
)
from VibeCADNativeAnalyzeFlowResultBindings import (
    register_analyze_flow_result_capability_implementation,
)
from VibeCADNativeAnalyzeFlowResultSchema import (
    register_analyze_flow_result_capability_definition,
)
from VibeCADNativeAnalyzeMechanicalResultBindings import (
    register_analyze_mechanical_result_capability_implementations,
)
from VibeCADNativeAnalyzeMechanicalResultSchema import (
    register_analyze_mechanical_result_capability_definitions,
)
from VibeCADNativeAnalyzeThermalResultBindings import (
    register_analyze_thermal_result_capability_implementations,
)
from VibeCADNativeAnalyzeThermalResultSchema import (
    register_analyze_thermal_result_capability_definitions,
)
from VibeCADNativeAnalyzeElectromagneticBindings import (
    register_analyze_electromagnetic_capability_implementation,
)
from VibeCADNativeAnalyzeElectromagneticSchema import (
    register_analyze_electromagnetic_capability_definition,
)
from VibeCADNativeAnalyzeFluidBindings import (
    register_analyze_fluid_capability_implementation,
)
from VibeCADNativeAnalyzeFluidSchema import (
    register_analyze_fluid_capability_definition,
)
from VibeCADNativeAnalyzeFluidCreateBindings import (
    register_analyze_fluid_create_capability_implementations,
)
from VibeCADNativeAnalyzeFluidCreateSchema import (
    register_analyze_fluid_create_capability_definitions,
)
from VibeCADNativeAnalyzeCfdLifecycleBindings import (
    register_analyze_cfd_lifecycle_capability_implementations,
)
from VibeCADNativeAnalyzeCfdLifecycleSchema import (
    register_analyze_cfd_lifecycle_capability_definitions,
)
from VibeCADNativeAnalyzeGeometricalBindings import (
    register_analyze_geometrical_capability_implementation,
)
from VibeCADNativeAnalyzeGeometricalSchema import (
    register_analyze_geometrical_capability_definition,
)
from VibeCADNativeAnalyzeSupportBindings import (
    register_analyze_support_capability_implementation,
)
from VibeCADNativeAnalyzeSupportSchema import (
    register_analyze_support_capability_definition,
)
from VibeCADNativeAnalyzeStructuralLifecycleBindings import (
    register_analyze_structural_lifecycle_capability_implementations,
)
from VibeCADNativeAnalyzeStructuralLifecycleSchema import (
    register_analyze_structural_lifecycle_capability_definitions,
)
from VibeCADNativeAnalyzeConnectionBindings import (
    register_analyze_connection_capability_implementation,
)
from VibeCADNativeAnalyzeConnectionSchema import (
    register_analyze_connection_capability_definition,
)
from VibeCADNativeAnalyzeLoadBindings import (
    register_analyze_load_capability_implementation,
)
from VibeCADNativeAnalyzeLoadSchema import (
    register_analyze_load_capability_definition,
)
from VibeCADNativeAnalyzeThermalBindings import (
    register_analyze_thermal_capability_implementation,
)
from VibeCADNativeAnalyzeThermalSchema import (
    register_analyze_thermal_capability_definition,
)
from VibeCADNativeAnalyzeMeshBindings import (
    register_analyze_mesh_capability_implementation,
)
from VibeCADNativeAnalyzeMeshSchema import (
    register_analyze_mesh_capability_definition,
)
from VibeCADNativeAnalyzeMeshLifecycleBindings import (
    register_analyze_mesh_lifecycle_capability_implementations,
)
from VibeCADNativeAnalyzeMeshLifecycleSchema import (
    register_analyze_mesh_lifecycle_capability_definitions,
)
from VibeCADNativeAnalyzeLocalMeshBindings import (
    register_analyze_local_mesh_capability_implementations,
)
from VibeCADNativeAnalyzeLocalMeshSchema import (
    register_analyze_local_mesh_capability_definitions,
)
from VibeCADNativeAnalyzeMeshFieldBindings import (
    register_analyze_mesh_field_capability_implementation,
)
from VibeCADNativeAnalyzeMeshFieldSchema import (
    register_analyze_mesh_field_capability_definition,
)
from VibeCADNativeAnalyzeMeshOutputBindings import (
    register_analyze_mesh_output_capability_implementation,
)
from VibeCADNativeAnalyzeMeshOutputSchema import (
    register_analyze_mesh_output_capability_definition,
)
from VibeCADNativeAnalyzeMeshRefinementBindings import (
    register_analyze_mesh_refinement_capability_implementation,
)
from VibeCADNativeAnalyzeMeshRefinementSchema import (
    register_analyze_mesh_refinement_capability_definition,
)
from VibeCADNativeAnalyzeStructuredMeshBindings import (
    register_analyze_structured_mesh_capability_implementation,
)
from VibeCADNativeAnalyzeStructuredMeshSchema import (
    register_analyze_structured_mesh_capability_definition,
)
from VibeCADNativeAnalyzeSolverBindings import (
    register_analyze_solver_capability_implementation,
)
from VibeCADNativeAnalyzeSolverSchema import (
    register_analyze_solver_capability_definition,
)
from VibeCADNativeAnalyzeSolverControlBindings import (
    register_analyze_solver_control_capability_implementation,
)
from VibeCADNativeAnalyzeSolverControlSchema import (
    register_analyze_solver_control_capability_definition,
)
from VibeCADNativeAnalyzeSolverExecutionBindings import (
    register_analyze_solver_execution_capability_implementation,
)
from VibeCADNativeAnalyzeSolverExecutionSchema import (
    register_analyze_solver_execution_capability_definition,
)
from VibeCADNativeAnalyzeRunBindings import (
    register_analyze_run_solver_capability_implementation,
)
from VibeCADNativeAnalyzeRunSchema import (
    register_analyze_run_solver_capability_definition,
)
from VibeCADNativeAnalyzeEquationBindings import (
    register_analyze_equation_capability_implementation,
)
from VibeCADNativeAnalyzeEquationSchema import (
    register_analyze_equation_capability_definition,
)
from VibeCADNativeAnalyzeResultsBindings import (
    register_analyze_results_capability_implementation,
)
from VibeCADNativeAnalyzeResultsSchema import (
    register_analyze_results_capability_definition,
)
from VibeCADNativeAnalyzePresentationBindings import (
    register_analyze_presentation_capability_implementation,
)
from VibeCADNativeAnalyzePresentationSchema import (
    register_analyze_presentation_capability_definition,
)
from VibeCADNativeAnalyzePostBindings import (
    register_analyze_post_capability_implementation,
)
from VibeCADNativeAnalyzePostSchema import (
    register_analyze_post_capability_definition,
)
from VibeCADNativeAnalyzePostFunctionBindings import (
    register_analyze_post_function_capability_implementation,
)
from VibeCADNativeAnalyzePostFunctionSchema import (
    register_analyze_post_function_capability_definition,
)
from VibeCADNativeAnalyzeVisualizationBindings import (
    register_analyze_visualization_capability_implementation,
)
from VibeCADNativeAnalyzeVisualizationSchema import (
    register_analyze_visualization_capability_definition,
)
from VibeCADNativeAnalyzeGeometryBindings import (
    register_analyze_geometry_capability_implementation,
)
from VibeCADNativeAnalyzeGeometrySchema import (
    register_analyze_geometry_capability_definition,
)
from VibeCADNativeAnalyzeInspectSchema import (
    register_analyze_inspect_capability_definition,
)
from VibeCADNativeAnalyzeAssignmentViewBindings import (
    register_analyze_assignment_view_capability_implementation,
)
from VibeCADNativeAnalyzeAssignmentViewSchema import (
    register_analyze_assignment_view_capability_definition,
)
from VibeCADNativeAnalyzeModelBindings import (
    register_analyze_model_capability_implementation,
)
from VibeCADNativeAnalyzeModelSchema import (
    register_analyze_model_capability_definition,
)
from VibeCADNativeAnalyzeSolidDomainBindings import (
    register_analyze_solid_domain_capability_implementation,
)
from VibeCADNativeAnalyzeSolidDomainSchema import (
    register_analyze_solid_domain_capability_definition,
)
from VibeCADNativeAssemblyDiagnosisBindings import (
    register_assembly_diagnosis_capability_implementation,
)
from VibeCADNativeAssemblyDiagnosisSchema import (
    register_assembly_diagnosis_capability_definition,
)
from VibeCADNativeAssemblyBomBindings import (
    register_assembly_bom_capability_implementation,
)
from VibeCADNativeAssemblyBomSchema import (
    register_assembly_bom_capability_definition,
)
from VibeCADNativeAssemblyFastenerBindings import (
    register_assembly_fastener_capability_implementation,
)
from VibeCADNativeAssemblyFastenerSchema import (
    register_assembly_fastener_capability_definition,
)
from VibeCADNativeAssemblyExportBindings import (
    register_assembly_export_capability_implementation,
)
from VibeCADNativeAssemblyExportSchema import (
    register_assembly_export_capability_definition,
)
from VibeCADNativeAssemblyInspectBindings import (
    register_assembly_connectors_capability_implementation,
    register_assembly_inspect_capability_implementation,
)
from VibeCADNativeAssemblyInspectSchema import (
    register_assembly_connectors_capability_definition,
    register_assembly_inspect_capability_definition,
)
from VibeCADNativeAssemblyJointBindings import (
    register_assembly_joint_capability_implementation,
)
from VibeCADNativeAssemblyJointSchema import (
    register_assembly_joint_capability_definition,
)
from VibeCADNativeAssemblyPlaybackBindings import (
    register_assembly_playback_capability_implementation,
)
from VibeCADNativeAssemblyPlaybackSchema import (
    register_assembly_playback_capability_definition,
)
from VibeCADNativeAssemblyStructureBindings import (
    register_assembly_structure_capability_implementation,
)
from VibeCADNativeAssemblyStructureSchema import (
    register_assembly_structure_capability_definition,
)
from VibeCADNativeCapabilityRegistry import NativeCapabilityRegistry
from VibeCADNativeParametersBindings import (
    register_parameters_capability_implementations,
)
from VibeCADNativeParametersSchema import (
    register_parameters_capability_definitions,
)
from VibeCADNativeMeshConvertBindings import (
    register_mesh_convert_capability_implementation,
)
from VibeCADNativeMeshConvertSchema import register_mesh_convert_capability_definition
from VibeCADNativeMeshBooleanBindings import (
    register_mesh_boolean_capability_implementation,
)
from VibeCADNativeMeshBooleanSchema import register_mesh_boolean_capability_definition
from VibeCADNativeMeshCutBindings import register_mesh_cut_capability_implementation
from VibeCADNativeMeshCutSchema import register_mesh_cut_capability_definition
from VibeCADNativeMeshCurvatureBindings import (
    register_mesh_curvature_capability_implementation,
)
from VibeCADNativeMeshCurvatureSchema import (
    register_mesh_curvature_capability_definition,
)
from VibeCADNativeMeshInspectBindings import (
    register_mesh_inspect_capability_implementation,
)
from VibeCADNativeMeshInspectSchema import register_mesh_inspect_capability_definition
from VibeCADNativeMeshSegmentBindings import (
    register_mesh_segment_capability_implementation,
)
from VibeCADNativeMeshSegmentSchema import register_mesh_segment_capability_definition
from VibeCADNativeMeshIOBindings import register_mesh_io_capability_implementation
from VibeCADNativeMeshIOSchema import register_mesh_io_capability_definition
from VibeCADNativeMeshModifyBindings import (
    register_mesh_modify_capability_implementation,
)
from VibeCADNativeMeshModifySchema import register_mesh_modify_capability_definition
from VibeCADNativeMeshPointsBindings import (
    register_mesh_points_capability_implementation,
)
from VibeCADNativeMeshPointsSchema import register_mesh_points_capability_definition
from VibeCADNativeMeshApproximateSchema import (
    register_mesh_approximate_capability_definition,
)
from VibeCADNativeMeshRebuildSchema import register_mesh_rebuild_capability_definition
from VibeCADNativeMeshReconstructParametricSchema import (
    register_mesh_reconstruct_parametric_capability_definition,
)
from VibeCADNativeReverseBindings import register_reverse_capability_implementations
from VibeCADNativeReconstructParametricBindings import (
    register_mesh_reconstruct_parametric_capability_implementation,
)
from VibeCADNativeMeshExportBindings import (
    register_mesh_export_capability_implementation,
)
from VibeCADNativeMeshExportSchema import register_mesh_export_capability_definition
from VibeCADNativeAeroBindings import (
    register_aero_solve_capability_implementation,
)
from VibeCADNativeAeroSchema import register_aero_solve_capability_definition
from VibeCADNativeBackgroundBindings import (
    register_native_background_capability_implementation,
)
from VibeCADNativeBackgroundSchema import (
    register_native_background_capability_definition,
)
from VibeCADNativeComponentInterfaceBindings import (
    register_component_interface_capability_implementation,
    register_component_interfaces_capability_implementation,
)
from VibeCADNativeComponentInterfaceSchema import (
    register_component_interface_capability_definition,
    register_component_interfaces_capability_definition,
)
from VibeCADNativeCommonBindings import register_common_capability_implementations
from VibeCADNativeCommonSchema import register_common_capability_definitions
from VibeCADNativeInspectionCompareBindings import (
    register_inspection_compare_capability_implementation,
)
from VibeCADNativeInspectionCompareSchema import (
    register_inspection_compare_capability_definition,
)
from VibeCADNativeWorkspaceBindings import (
    register_workspace_capability_implementation,
)
from VibeCADNativeWorkspaceSchema import register_workspace_capability_definition
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
from VibeCADNativeModelHistoryBindings import (
    register_model_history_capability_implementations,
)
from VibeCADNativeModelHistorySchema import (
    register_model_history_capability_definitions,
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
from VibeCADNativeSketchSetupBindings import (
    register_sketch_setup_capability_implementation,
)
from VibeCADNativeSketchSetupSchema import (
    register_sketch_setup_capability_definition,
)
from VibeCADNativeModelTransformBindings import (
    register_model_transform_capability_implementation,
)
from VibeCADNativeModelTransformSchema import (
    register_model_transform_capability_definition,
)
from VibeCADNativeManufactureInspectBindings import (
    register_manufacture_inspect_capability_implementation,
)
from VibeCADNativeManufactureInspectSchema import (
    register_manufacture_inspect_capability_definition,
)
from VibeCADNativeManufactureFocusedInspectBindings import (
    register_manufacture_focused_inspect_capability_implementations,
)
from VibeCADNativeManufactureFocusedInspectSchema import (
    register_manufacture_focused_inspect_capability_definitions,
)
from VibeCADNativeManufactureJobBindings import (
    register_manufacture_job_capability_implementation,
)
from VibeCADNativeManufactureJobSchema import (
    register_manufacture_job_capability_definition,
)
from VibeCADNativeManufactureAreaBindings import (
    register_manufacture_area_capability_implementation,
)
from VibeCADNativeManufactureAreaSchema import (
    register_manufacture_area_capability_definition,
)
from VibeCADNativeManufactureModifyBindings import (
    register_manufacture_modify_capability_implementation,
)
from VibeCADNativeManufactureModifySchema import (
    register_manufacture_modify_capability_definition,
)
from VibeCADNativeManufactureFocusedModifyBindings import (
    register_manufacture_focused_modify_capability_implementations,
)
from VibeCADNativeManufactureFocusedModifySchema import (
    register_manufacture_focused_modify_capability_definitions,
)
from VibeCADNativeManufactureProgramBindings import (
    register_manufacture_program_capability_implementation,
)
from VibeCADNativeManufactureProgramSchema import (
    register_manufacture_program_capability_definition,
)
from VibeCADNativeManufactureProbeBindings import (
    register_manufacture_probe_capability_implementation,
)
from VibeCADNativeManufactureProbeSchema import (
    register_manufacture_probe_capability_definition,
)
from VibeCADNativeManufacturePropertyBagBindings import (
    register_manufacture_property_bag_capability_implementation,
)
from VibeCADNativeManufacturePropertyBagSchema import (
    register_manufacture_property_bag_capability_definition,
)
from VibeCADNativeManufactureOperationBindings import (
    register_manufacture_operation_capability_implementation,
)
from VibeCADNativeManufactureOperationSchema import (
    register_manufacture_operation_capability_definition,
)
from VibeCADNativeManufactureFocusedOperationBindings import (
    register_manufacture_focused_operation_capability_implementations,
)
from VibeCADNativeManufactureFocusedOperationSchema import (
    register_manufacture_focused_operation_capability_definitions,
)
from VibeCADNativeManufactureCamoticsBindings import (
    register_manufacture_camotics_capability_implementation,
)
from VibeCADNativeManufactureCamoticsSchema import (
    register_manufacture_camotics_capability_definition,
)
from VibeCADNativeManufacturePostBindings import (
    register_manufacture_post_capability_implementation,
)
from VibeCADNativeManufacturePostSchema import (
    register_manufacture_post_capability_definition,
)
from VibeCADNativeManufactureFocusedPostBindings import (
    register_manufacture_focused_post_capability_implementations,
)
from VibeCADNativeManufactureFocusedPostSchema import (
    register_manufacture_focused_post_capability_definitions,
)
from VibeCADNativeManufactureTemplateBindings import (
    register_manufacture_template_capability_implementation,
)
from VibeCADNativeManufactureTemplateSchema import (
    register_manufacture_template_capability_definition,
)
from VibeCADNativeManufactureSimulationBindings import (
    register_manufacture_simulation_capability_implementation,
)
from VibeCADNativeManufactureSimulationSchema import (
    register_manufacture_simulation_capability_definition,
)
from VibeCADNativeManufactureSimulationControlBindings import (
    register_manufacture_simulation_control_capability_implementation,
)
from VibeCADNativeManufactureSimulationControlSchema import (
    register_manufacture_simulation_control_capability_definition,
)
from VibeCADNativeManufactureSimulationResultBindings import (
    register_manufacture_simulation_result_capability_implementation,
)
from VibeCADNativeManufactureSimulationResultSchema import (
    register_manufacture_simulation_result_capability_definition,
)
from VibeCADNativeManufactureFollowUpBindings import (
    register_manufacture_follow_up_capability_implementation,
)
from VibeCADNativeManufactureFollowUpSchema import (
    register_manufacture_follow_up_capability_definition,
)
from VibeCADNativeManufactureToolBindings import (
    register_manufacture_tool_capability_implementations,
)
from VibeCADNativeManufactureFocusedToolBindings import (
    register_manufacture_focused_tool_capability_implementations,
)
from VibeCADNativeManufactureFocusedToolSchema import (
    register_manufacture_focused_tool_capability_definitions,
)
from VibeCADNativeManufactureToolSchema import (
    register_manufacture_tool_capability_definitions,
)
from VibeCADNativeManufactureToolOutputBindings import (
    register_manufacture_tool_output_capability_implementation,
)
from VibeCADNativeManufactureToolOutputSchema import (
    register_manufacture_tool_output_capability_definition,
)
from VibeCADNativeDrawingPageBindings import (
    register_drawing_page_capability_implementation,
)
from VibeCADNativeDrawingPageSchema import (
    register_drawing_page_capability_definition,
)
from VibeCADNativeDrawingActiveViewBindings import (
    register_drawing_active_view_capability_implementation,
)
from VibeCADNativeDrawingActiveViewSchema import (
    register_drawing_active_view_capability_definition,
)
from VibeCADNativeDrawingViewBindings import (
    register_drawing_view_capability_implementation,
)
from VibeCADNativeDrawingViewSchema import (
    register_drawing_view_capability_definition,
)
from VibeCADNativeDrawingSectionBindings import (
    register_drawing_section_capability_implementation,
)
from VibeCADNativeDrawingSectionSchema import (
    register_drawing_section_capability_definition,
)
from VibeCADNativeDrawingComplexSectionBindings import (
    register_drawing_complex_section_capability_implementation,
)
from VibeCADNativeDrawingComplexSectionSchema import (
    register_drawing_complex_section_capability_definition,
)
from VibeCADNativeDrawingDetailBindings import (
    register_drawing_detail_capability_implementation,
)
from VibeCADNativeDrawingDetailSchema import (
    register_drawing_detail_capability_definition,
)
from VibeCADNativeDrawingDraftBindings import (
    register_drawing_draft_capability_implementation,
)
from VibeCADNativeDrawingDraftSchema import (
    register_drawing_draft_capability_definition,
)
from VibeCADNativeDrawingClipBindings import (
    register_drawing_clip_capability_implementation,
)
from VibeCADNativeDrawingClipSchema import (
    register_drawing_clip_capability_definition,
)
from VibeCADNativeDrawingStackBindings import (
    register_drawing_stack_capability_implementation,
)
from VibeCADNativeDrawingStackSchema import (
    register_drawing_stack_capability_definition,
)
from VibeCADNativeDrawingDimensionBindings import (
    register_drawing_dimension_capability_implementation,
)
from VibeCADNativeDrawingDimensionSchema import (
    register_drawing_dimension_capability_definition,
)
from VibeCADNativeDrawingDimensionInferenceBindings import (
    register_drawing_dimension_inference_capability_implementation,
)
from VibeCADNativeDrawingDimensionInferenceSchema import (
    register_drawing_dimension_inference_capability_definition,
)
from VibeCADNativeDrawingDimensionSeriesBindings import (
    register_drawing_dimension_series_capability_implementation,
)
from VibeCADNativeDrawingDimensionSeriesSchema import (
    register_drawing_dimension_series_capability_definition,
)
from VibeCADNativeDrawingDimensionRepairBindings import (
    register_drawing_dimension_repair_capability_implementation,
)
from VibeCADNativeDrawingDimensionRepairSchema import (
    register_drawing_dimension_repair_capability_definition,
)
from VibeCADNativeDrawingLineDefaultsBindings import (
    register_drawing_line_defaults_capability_implementation,
)
from VibeCADNativeDrawingLineDefaultsSchema import (
    register_drawing_line_defaults_capability_definition,
)
from VibeCADNativeDrawingLineAttributesBindings import (
    register_drawing_line_attributes_capability_implementation,
)
from VibeCADNativeDrawingLineAttributesSchema import (
    register_drawing_line_attributes_capability_definition,
)
from VibeCADNativeDrawingLineLengthBindings import (
    register_drawing_line_length_capability_implementation,
)
from VibeCADNativeDrawingLineLengthSchema import (
    register_drawing_line_length_capability_definition,
)
from VibeCADNativeDrawingViewLockBindings import (
    register_drawing_view_lock_capability_implementation,
)
from VibeCADNativeDrawingViewLockSchema import (
    register_drawing_view_lock_capability_definition,
)
from VibeCADNativeDrawingPlacementBindings import (
    register_drawing_placement_capability_implementations,
)
from VibeCADNativeDrawingPlacementSchema import (
    register_drawing_placement_capability_definitions,
)
from VibeCADNativeDrawingSectionPositionBindings import (
    register_drawing_section_position_capability_implementation,
)
from VibeCADNativeDrawingSectionPositionSchema import (
    register_drawing_section_position_capability_definition,
)
from VibeCADNativeDrawingFormatBindings import (
    register_drawing_format_capability_implementation,
)
from VibeCADNativeDrawingFormatSchema import (
    register_drawing_format_capability_definition,
)
from VibeCADNativeDrawingDimensionTextBindings import (
    register_drawing_dimension_text_capability_implementation,
)
from VibeCADNativeDrawingDimensionTextSchema import (
    register_drawing_dimension_text_capability_definition,
)
from VibeCADNativeDrawingPresentationBindings import (
    register_drawing_presentation_capability_implementation,
)
from VibeCADNativeDrawingPresentationSchema import (
    register_drawing_presentation_capability_definition,
)
from VibeCADNativeDrawingHatchBindings import (
    register_drawing_hatch_capability_implementation,
)
from VibeCADNativeDrawingHatchSchema import (
    register_drawing_hatch_capability_definition,
)
from VibeCADNativeDrawingRichAnnotationBindings import (
    register_drawing_rich_annotation_capability_implementation,
)
from VibeCADNativeDrawingRichAnnotationSchema import (
    register_drawing_rich_annotation_capability_definition,
)
from VibeCADNativeDrawingSymbolBindings import (
    register_drawing_symbol_capability_implementation,
)
from VibeCADNativeDrawingSymbolSchema import (
    register_drawing_symbol_capability_definition,
)
from VibeCADNativeDrawingExportBindings import (
    register_drawing_export_capability_implementation,
)
from VibeCADNativeDrawingExportSchema import (
    register_drawing_export_capability_definition,
)
from VibeCADNativeDrawingLeaderBindings import (
    register_drawing_leader_capability_implementation,
)
from VibeCADNativeDrawingLeaderSchema import (
    register_drawing_leader_capability_definition,
)
from VibeCADNativeDrawingCircleCenterLineBindings import (
    register_drawing_circle_center_line_capability_implementation,
)
from VibeCADNativeDrawingCircleCenterLineSchema import (
    register_drawing_circle_center_line_capability_definition,
)
from VibeCADNativeDrawingGeneralCenterLineBindings import (
    register_drawing_general_center_line_capability_implementation,
)
from VibeCADNativeDrawingGeneralCenterLineSchema import (
    register_drawing_general_center_line_capability_definition,
)
from VibeCADNativeDrawingBoltCircleCenterLineBindings import (
    register_drawing_bolt_circle_center_line_capability_implementation,
)
from VibeCADNativeDrawingBoltCircleCenterLineSchema import (
    register_drawing_bolt_circle_center_line_capability_definition,
)
from VibeCADNativeDrawingThreadRepresentationBindings import (
    register_drawing_thread_representation_capability_implementation,
)
from VibeCADNativeDrawingThreadRepresentationSchema import (
    register_drawing_thread_representation_capability_definition,
)
from VibeCADNativeDrawingCosmeticVertexBindings import (
    register_drawing_cosmetic_vertex_capability_implementation,
)
from VibeCADNativeDrawingCosmeticVertexSchema import (
    register_drawing_cosmetic_vertex_capability_definition,
)
from VibeCADNativeDrawingCosmeticCurveBindings import (
    register_drawing_cosmetic_curve_capability_implementation,
)
from VibeCADNativeDrawingCosmeticCurveSchema import (
    register_drawing_cosmetic_curve_capability_definition,
)
from VibeCADNativeDrawingCosmeticLineBindings import (
    register_drawing_cosmetic_line_capability_implementation,
)
from VibeCADNativeDrawingCosmeticLineSchema import (
    register_drawing_cosmetic_line_capability_definition,
)
from VibeCADNativeDrawingBalloonBindings import (
    register_drawing_balloon_capability_implementation,
)
from VibeCADNativeDrawingBalloonSchema import (
    register_drawing_balloon_capability_definition,
)
from VibeCADNativeRobotSetupBindings import (
    register_robot_setup_capability_implementation,
)
from VibeCADNativeRobotSetupSchema import (
    register_robot_setup_capability_definition,
)
from VibeCADNativeRobotMotionBindings import (
    register_robot_motion_capability_implementation,
)
from VibeCADNativeRobotMotionSchema import (
    register_robot_motion_capability_definition,
)
from VibeCADNativeRobotExportBindings import (
    register_robot_export_capability_implementation,
)
from VibeCADNativeRobotExportSchema import (
    register_robot_export_capability_definition,
)
from VibeCADNativeRobotTrajectoryBindings import (
    register_robot_path_feature_capability_implementations,
    register_robot_trajectory_capability_implementation,
)
from VibeCADNativeRobotTrajectorySchema import (
    register_robot_path_feature_capability_definitions,
    register_robot_trajectory_capability_definition,
)
from VibeCADNativeSketchProviderBindings import (
    register_sketch_provider_capability_implementations,
)
from VibeCADNativeSketchProviderSchema import (
    register_sketch_provider_capability_definitions,
)


def build_native_capability_registry() -> NativeCapabilityRegistry:
    """Build a fresh fail-closed registry without document or GUI state."""

    registry = NativeCapabilityRegistry()
    register_analyze_model_capability_definition(registry)
    register_analyze_model_capability_implementation(registry)
    register_analyze_solid_domain_capability_definition(registry)
    register_analyze_solid_domain_capability_implementation(registry)
    register_analyze_inspect_capability_definition(registry)
    register_analyze_inspect_capability_implementation(registry)
    register_analyze_face_capability_definition(registry)
    register_analyze_face_capability_implementation(registry)
    register_analyze_flow_result_capability_definition(registry)
    register_analyze_flow_result_capability_implementation(registry)
    register_analyze_mechanical_result_capability_definitions(registry)
    register_analyze_mechanical_result_capability_implementations(registry)
    register_analyze_thermal_result_capability_definitions(registry)
    register_analyze_thermal_result_capability_implementations(registry)
    register_analyze_assignment_view_capability_definition(registry)
    register_analyze_assignment_view_capability_implementation(registry)
    register_analyze_geometry_capability_definition(registry)
    register_analyze_geometry_capability_implementation(registry)
    register_analyze_electromagnetic_capability_definition(registry)
    register_analyze_electromagnetic_capability_implementation(registry)
    register_analyze_fluid_capability_definition(registry)
    register_analyze_fluid_capability_implementation(registry)
    register_analyze_fluid_create_capability_definitions(registry)
    register_analyze_fluid_create_capability_implementations(registry)
    register_analyze_cfd_lifecycle_capability_definitions(registry)
    register_analyze_cfd_lifecycle_capability_implementations(registry)
    register_analyze_geometrical_capability_definition(registry)
    register_analyze_geometrical_capability_implementation(registry)
    register_analyze_support_capability_definition(registry)
    register_analyze_support_capability_implementation(registry)
    register_analyze_structural_lifecycle_capability_definitions(registry)
    register_analyze_structural_lifecycle_capability_implementations(registry)
    register_analyze_connection_capability_definition(registry)
    register_analyze_connection_capability_implementation(registry)
    register_analyze_load_capability_definition(registry)
    register_analyze_load_capability_implementation(registry)
    register_analyze_thermal_capability_definition(registry)
    register_analyze_thermal_capability_implementation(registry)
    register_analyze_mesh_capability_definition(registry)
    register_analyze_mesh_capability_implementation(registry)
    register_analyze_mesh_lifecycle_capability_definitions(registry)
    register_analyze_mesh_lifecycle_capability_implementations(registry)
    register_analyze_local_mesh_capability_definitions(registry)
    register_analyze_local_mesh_capability_implementations(registry)
    register_analyze_mesh_field_capability_definition(registry)
    register_analyze_mesh_field_capability_implementation(registry)
    register_analyze_mesh_output_capability_definition(registry)
    register_analyze_mesh_output_capability_implementation(registry)
    register_analyze_mesh_refinement_capability_definition(registry)
    register_analyze_mesh_refinement_capability_implementation(registry)
    register_analyze_structured_mesh_capability_definition(registry)
    register_analyze_structured_mesh_capability_implementation(registry)
    register_analyze_solver_capability_definition(registry)
    register_analyze_solver_capability_implementation(registry)
    register_analyze_solver_control_capability_definition(registry)
    register_analyze_solver_control_capability_implementation(registry)
    register_analyze_solver_execution_capability_definition(registry)
    register_analyze_solver_execution_capability_implementation(registry)
    register_analyze_run_solver_capability_definition(registry)
    register_analyze_run_solver_capability_implementation(registry)
    register_analyze_equation_capability_definition(registry)
    register_analyze_equation_capability_implementation(registry)
    register_analyze_results_capability_definition(registry)
    register_analyze_results_capability_implementation(registry)
    register_analyze_presentation_capability_definition(registry)
    register_analyze_presentation_capability_implementation(registry)
    register_analyze_post_capability_definition(registry)
    register_analyze_post_capability_implementation(registry)
    register_analyze_post_function_capability_definition(registry)
    register_analyze_post_function_capability_implementation(registry)
    register_analyze_visualization_capability_definition(registry)
    register_analyze_visualization_capability_implementation(registry)
    register_native_background_capability_definition(registry)
    register_native_background_capability_implementation(registry)
    register_aero_solve_capability_definition(registry)
    register_aero_solve_capability_implementation(registry)
    register_mesh_convert_capability_definition(registry)
    register_mesh_convert_capability_implementation(registry)
    register_mesh_io_capability_definition(registry)
    register_mesh_io_capability_implementation(registry)
    register_mesh_export_capability_definition(registry)
    register_mesh_export_capability_implementation(registry)
    register_mesh_modify_capability_definition(registry)
    register_mesh_modify_capability_implementation(registry)
    register_mesh_boolean_capability_definition(registry)
    register_mesh_boolean_capability_implementation(registry)
    register_mesh_cut_capability_definition(registry)
    register_mesh_cut_capability_implementation(registry)
    register_mesh_inspect_capability_definition(registry)
    register_mesh_inspect_capability_implementation(registry)
    register_mesh_curvature_capability_definition(registry)
    register_mesh_curvature_capability_implementation(registry)
    register_mesh_segment_capability_definition(registry)
    register_mesh_segment_capability_implementation(registry)
    register_mesh_points_capability_definition(registry)
    register_mesh_points_capability_implementation(registry)
    register_mesh_rebuild_capability_definition(registry)
    register_mesh_approximate_capability_definition(registry)
    register_mesh_reconstruct_parametric_capability_definition(registry)
    register_reverse_capability_implementations(registry)
    register_mesh_reconstruct_parametric_capability_implementation(registry)
    register_common_capability_definitions(registry)
    register_common_capability_implementations(registry)
    register_inspection_compare_capability_definition(registry)
    register_inspection_compare_capability_implementation(registry)
    register_workspace_capability_definition(registry)
    register_workspace_capability_implementation(registry)
    register_parameters_capability_definitions(registry)
    register_parameters_capability_implementations(registry)
    register_assembly_diagnosis_capability_definition(registry)
    register_assembly_diagnosis_capability_implementation(registry)
    register_assembly_bom_capability_definition(registry)
    register_assembly_bom_capability_implementation(registry)
    register_assembly_fastener_capability_definition(registry)
    register_assembly_fastener_capability_implementation(registry)
    register_assembly_export_capability_definition(registry)
    register_assembly_export_capability_implementation(registry)
    register_assembly_connectors_capability_definition(registry)
    register_assembly_connectors_capability_implementation(registry)
    register_assembly_inspect_capability_definition(registry)
    register_assembly_inspect_capability_implementation(registry)
    register_assembly_joint_capability_definition(registry)
    register_assembly_joint_capability_implementation(registry)
    register_assembly_playback_capability_definition(registry)
    register_assembly_playback_capability_implementation(registry)
    register_assembly_structure_capability_definition(registry)
    register_assembly_structure_capability_implementation(registry)
    register_component_interface_capability_definition(registry)
    register_component_interface_capability_implementation(registry)
    register_component_interfaces_capability_definition(registry)
    register_component_interfaces_capability_implementation(registry)
    register_model_catalog_capability_definition(registry)
    register_model_catalog_capability_implementation(registry)
    register_model_structure_capability_definitions(registry)
    register_model_structure_capability_implementations(registry)
    register_model_history_capability_definitions(registry)
    register_model_history_capability_implementations(registry)
    register_sketch_setup_capability_definition(registry)
    register_sketch_setup_capability_implementation(registry)
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
    register_manufacture_inspect_capability_definition(registry)
    register_manufacture_inspect_capability_implementation(registry)
    register_manufacture_focused_inspect_capability_definitions(registry)
    register_manufacture_focused_inspect_capability_implementations(registry)
    register_manufacture_job_capability_definition(registry)
    register_manufacture_job_capability_implementation(registry)
    register_manufacture_area_capability_definition(registry)
    register_manufacture_area_capability_implementation(registry)
    register_manufacture_modify_capability_definition(registry)
    register_manufacture_modify_capability_implementation(registry)
    register_manufacture_focused_modify_capability_definitions(registry)
    register_manufacture_focused_modify_capability_implementations(registry)
    register_manufacture_program_capability_definition(registry)
    register_manufacture_program_capability_implementation(registry)
    register_manufacture_probe_capability_definition(registry)
    register_manufacture_probe_capability_implementation(registry)
    register_manufacture_property_bag_capability_definition(registry)
    register_manufacture_property_bag_capability_implementation(registry)
    register_manufacture_operation_capability_definition(registry)
    register_manufacture_operation_capability_implementation(registry)
    register_manufacture_focused_operation_capability_definitions(registry)
    register_manufacture_focused_operation_capability_implementations(registry)
    register_manufacture_camotics_capability_definition(registry)
    register_manufacture_camotics_capability_implementation(registry)
    register_manufacture_post_capability_definition(registry)
    register_manufacture_post_capability_implementation(registry)
    register_manufacture_focused_post_capability_definitions(registry)
    register_manufacture_focused_post_capability_implementations(registry)
    register_manufacture_template_capability_definition(registry)
    register_manufacture_template_capability_implementation(registry)
    register_manufacture_simulation_capability_definition(registry)
    register_manufacture_simulation_capability_implementation(registry)
    register_manufacture_simulation_control_capability_definition(registry)
    register_manufacture_simulation_control_capability_implementation(registry)
    register_manufacture_simulation_result_capability_definition(registry)
    register_manufacture_simulation_result_capability_implementation(registry)
    register_manufacture_follow_up_capability_definition(registry)
    register_manufacture_follow_up_capability_implementation(registry)
    register_manufacture_tool_capability_definitions(registry)
    register_manufacture_tool_capability_implementations(registry)
    register_manufacture_focused_tool_capability_definitions(registry)
    register_manufacture_focused_tool_capability_implementations(registry)
    register_manufacture_tool_output_capability_definition(registry)
    register_manufacture_tool_output_capability_implementation(registry)
    register_drawing_page_capability_definition(registry)
    register_drawing_page_capability_implementation(registry)
    register_drawing_active_view_capability_definition(registry)
    register_drawing_active_view_capability_implementation(registry)
    register_drawing_view_capability_definition(registry)
    register_drawing_view_capability_implementation(registry)
    register_drawing_section_capability_definition(registry)
    register_drawing_section_capability_implementation(registry)
    register_drawing_complex_section_capability_definition(registry)
    register_drawing_complex_section_capability_implementation(registry)
    register_drawing_detail_capability_definition(registry)
    register_drawing_detail_capability_implementation(registry)
    register_drawing_draft_capability_definition(registry)
    register_drawing_draft_capability_implementation(registry)
    register_drawing_clip_capability_definition(registry)
    register_drawing_clip_capability_implementation(registry)
    register_drawing_stack_capability_definition(registry)
    register_drawing_stack_capability_implementation(registry)
    register_drawing_dimension_capability_definition(registry)
    register_drawing_dimension_capability_implementation(registry)
    register_drawing_dimension_inference_capability_definition(registry)
    register_drawing_dimension_inference_capability_implementation(registry)
    register_drawing_dimension_series_capability_definition(registry)
    register_drawing_dimension_series_capability_implementation(registry)
    register_drawing_dimension_repair_capability_definition(registry)
    register_drawing_dimension_repair_capability_implementation(registry)
    register_drawing_line_defaults_capability_definition(registry)
    register_drawing_line_defaults_capability_implementation(registry)
    register_drawing_line_attributes_capability_definition(registry)
    register_drawing_line_attributes_capability_implementation(registry)
    register_drawing_line_length_capability_definition(registry)
    register_drawing_line_length_capability_implementation(registry)
    register_drawing_view_lock_capability_definition(registry)
    register_drawing_view_lock_capability_implementation(registry)
    register_drawing_placement_capability_definitions(registry)
    register_drawing_placement_capability_implementations(registry)
    register_drawing_section_position_capability_definition(registry)
    register_drawing_section_position_capability_implementation(registry)
    register_drawing_format_capability_definition(registry)
    register_drawing_format_capability_implementation(registry)
    register_drawing_dimension_text_capability_definition(registry)
    register_drawing_dimension_text_capability_implementation(registry)
    register_drawing_presentation_capability_definition(registry)
    register_drawing_presentation_capability_implementation(registry)
    register_drawing_hatch_capability_definition(registry)
    register_drawing_hatch_capability_implementation(registry)
    register_drawing_rich_annotation_capability_definition(registry)
    register_drawing_rich_annotation_capability_implementation(registry)
    register_drawing_symbol_capability_definition(registry)
    register_drawing_symbol_capability_implementation(registry)
    register_drawing_export_capability_definition(registry)
    register_drawing_export_capability_implementation(registry)
    register_drawing_leader_capability_definition(registry)
    register_drawing_leader_capability_implementation(registry)
    register_drawing_circle_center_line_capability_definition(registry)
    register_drawing_circle_center_line_capability_implementation(registry)
    register_drawing_general_center_line_capability_definition(registry)
    register_drawing_general_center_line_capability_implementation(registry)
    register_drawing_bolt_circle_center_line_capability_definition(registry)
    register_drawing_bolt_circle_center_line_capability_implementation(registry)
    register_drawing_thread_representation_capability_definition(registry)
    register_drawing_thread_representation_capability_implementation(registry)
    register_drawing_cosmetic_vertex_capability_definition(registry)
    register_drawing_cosmetic_vertex_capability_implementation(registry)
    register_drawing_cosmetic_curve_capability_definition(registry)
    register_drawing_cosmetic_curve_capability_implementation(registry)
    register_drawing_cosmetic_line_capability_definition(registry)
    register_drawing_cosmetic_line_capability_implementation(registry)
    register_drawing_balloon_capability_definition(registry)
    register_drawing_balloon_capability_implementation(registry)
    register_robot_setup_capability_definition(registry)
    register_robot_setup_capability_implementation(registry)
    register_robot_motion_capability_definition(registry)
    register_robot_motion_capability_implementation(registry)
    register_robot_export_capability_definition(registry)
    register_robot_export_capability_implementation(registry)
    register_robot_trajectory_capability_definition(registry)
    register_robot_trajectory_capability_implementation(registry)
    register_robot_path_feature_capability_definitions(registry)
    register_robot_path_feature_capability_implementations(registry)
    register_sketch_provider_capability_definitions(registry)
    register_sketch_provider_capability_implementations(registry)
    return registry
