# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json

from VibeCADNativeAnalyzeInspectSchema import ANALYZE_INSPECT_CAPABILITY_NAME
from VibeCADNativeAnalyzeGeometrySchema import ANALYZE_GEOMETRY_CAPABILITY_NAME
from VibeCADNativeAnalyzeElectromagneticSchema import (
    ANALYZE_ELECTROMAGNETIC_CAPABILITY_NAME,
)
from VibeCADNativeAnalyzeFluidSchema import ANALYZE_FLUID_CAPABILITY_NAME
from VibeCADNativeAnalyzeGeometricalSchema import (
    ANALYZE_GEOMETRICAL_CAPABILITY_NAME,
)
from VibeCADNativeAnalyzeModelSchema import ANALYZE_MODEL_CAPABILITY_NAME
from VibeCADNativeAnalyzeSupportSchema import ANALYZE_SUPPORT_CAPABILITY_NAME
from VibeCADNativeAnalyzeConnectionSchema import ANALYZE_CONNECTION_CAPABILITY_NAME
from VibeCADNativeAnalyzeLoadSchema import ANALYZE_LOAD_CAPABILITY_NAME
from VibeCADNativeAnalyzeThermalSchema import ANALYZE_THERMAL_CAPABILITY_NAME
from VibeCADNativeAnalyzeMeshSchema import ANALYZE_MESH_CAPABILITY_NAME
from VibeCADNativeAnalyzeMeshFieldSchema import ANALYZE_MESH_FIELD_CAPABILITY_NAME
from VibeCADNativeAnalyzeMeshOutputSchema import ANALYZE_MESH_OUTPUT_CAPABILITY_NAME
from VibeCADNativeAnalyzeMeshRefinementSchema import (
    ANALYZE_MESH_REFINEMENT_CAPABILITY_NAME,
)
from VibeCADNativeAnalyzeResultsSchema import ANALYZE_RESULTS_CAPABILITY_NAME
from VibeCADNativeAnalyzePresentationSchema import (
    ANALYZE_PRESENTATION_CAPABILITY_NAME,
)
from VibeCADNativeAnalyzePostSchema import ANALYZE_POST_CAPABILITY_NAME
from VibeCADNativeAnalyzePostFunctionSchema import (
    ANALYZE_POST_FUNCTION_CAPABILITY_NAME,
)
from VibeCADNativeAnalyzeVisualizationSchema import (
    ANALYZE_VISUALIZATION_CAPABILITY_NAME,
)
from VibeCADNativeAnalyzeStructuredMeshSchema import (
    ANALYZE_STRUCTURED_MESH_CAPABILITY_NAME,
)
from VibeCADNativeAnalyzeSolverSchema import ANALYZE_SOLVER_CAPABILITY_NAME
from VibeCADNativeAnalyzeSolverControlSchema import (
    ANALYZE_SOLVER_CONTROL_CAPABILITY_NAME,
)
from VibeCADNativeAnalyzeSolverExecutionSchema import (
    ANALYZE_SOLVER_EXECUTION_CAPABILITY_NAME,
)
from VibeCADNativeAnalyzeEquationSchema import ANALYZE_EQUATION_CAPABILITY_NAME
from VibeCADNativeAssemblyDiagnosisBindings import (
    ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME,
)
from VibeCADNativeAssemblyExportSchema import ASSEMBLY_EXPORT_CAPABILITY_NAME
from VibeCADNativeAssemblyFastenerSchema import ASSEMBLY_FASTENER_CAPABILITY_NAME
from VibeCADNativeAssemblyInspectSchema import ASSEMBLY_INSPECT_CAPABILITY_NAME
from VibeCADNativeAssemblyJointBindings import ASSEMBLY_JOINT_CAPABILITY_NAME
from VibeCADNativeAssemblyPlaybackBindings import (
    ASSEMBLY_PLAYBACK_CAPABILITY_NAME,
)
from VibeCADNativeCommonBindings import COMMON_NATIVE_CAPABILITY_NAMES
from VibeCADNativeAssemblyStructureBindings import (
    ASSEMBLY_STRUCTURE_CAPABILITY_NAME,
)
from VibeCADNativeCapabilityRegistry import MAX_NATIVE_SCHEMAS_JSON_BYTES
from VibeCADNativeBackgroundSchema import NATIVE_BACKGROUND_CAPABILITY_NAME
from VibeCADNativeDrawingPageSchema import DRAWING_PAGE_CAPABILITY_NAME
from VibeCADNativeDrawingActiveViewSchema import (
    DRAWING_ACTIVE_VIEW_CAPABILITY_NAME,
)
from VibeCADNativeDrawingSectionSchema import DRAWING_SECTION_CAPABILITY_NAME
from VibeCADNativeDrawingComplexSectionSchema import (
    DRAWING_COMPLEX_SECTION_CAPABILITY_NAME,
)
from VibeCADNativeDrawingDetailSchema import DRAWING_DETAIL_CAPABILITY_NAME
from VibeCADNativeDrawingDimensionSchema import DRAWING_DIMENSION_CAPABILITY_NAME
from VibeCADNativeDrawingDimensionRepairSchema import (
    DRAWING_DIMENSION_REPAIR_CAPABILITY_NAME,
)
from VibeCADNativeDrawingLineDefaultsSchema import (
    DRAWING_LINE_DEFAULTS_CAPABILITY_NAME,
)
from VibeCADNativeDrawingLineAttributesSchema import (
    DRAWING_LINE_ATTRIBUTES_CAPABILITY_NAME,
)
from VibeCADNativeDrawingLineLengthSchema import (
    DRAWING_LINE_LENGTH_CAPABILITY_NAME,
)
from VibeCADNativeDrawingViewLockSchema import (
    DRAWING_VIEW_LOCK_CAPABILITY_NAME,
)
from VibeCADNativeDrawingSectionPositionSchema import (
    DRAWING_SECTION_POSITION_CAPABILITY_NAME,
)
from VibeCADNativeDrawingFormatSchema import DRAWING_FORMAT_CAPABILITY_NAME
from VibeCADNativeDrawingDimensionTextSchema import (
    DRAWING_DIMENSION_TEXT_CAPABILITY_NAME,
)
from VibeCADNativeDrawingPresentationSchema import (
    DRAWING_PRESENTATION_CAPABILITY_NAME,
)
from VibeCADNativeDrawingHatchSchema import DRAWING_HATCH_CAPABILITY_NAME
from VibeCADNativeDrawingRichAnnotationSchema import (
    DRAWING_RICH_ANNOTATION_CAPABILITY_NAME,
)
from VibeCADNativeDrawingLeaderSchema import (
    DRAWING_ANNOTATION_CAPABILITY_NAME,
)
from VibeCADNativeDrawingCircleCenterLineSchema import (
    DRAWING_CIRCLE_CENTER_LINE_CAPABILITY_NAME,
)
from VibeCADNativeDrawingGeneralCenterLineSchema import (
    DRAWING_GENERAL_CENTER_LINE_CAPABILITY_NAME,
)
from VibeCADNativeDrawingBoltCircleCenterLineSchema import (
    DRAWING_BOLT_CIRCLE_CENTER_LINE_CAPABILITY_NAME,
)
from VibeCADNativeDrawingThreadRepresentationSchema import (
    DRAWING_THREAD_REPRESENTATION_CAPABILITY_NAME,
)
from VibeCADNativeDrawingCosmeticVertexSchema import (
    DRAWING_COSMETIC_VERTEX_CAPABILITY_NAME,
)
from VibeCADNativeDrawingCosmeticCurveSchema import (
    DRAWING_COSMETIC_CURVE_CAPABILITY_NAME,
)
from VibeCADNativeDrawingCosmeticLineSchema import (
    DRAWING_COSMETIC_LINE_CAPABILITY_NAME,
)
from VibeCADNativeDrawingBalloonSchema import DRAWING_BALLOON_CAPABILITY_NAME
from VibeCADNativeDrawingDraftSchema import DRAWING_DRAFT_CAPABILITY_NAME
from VibeCADNativeDrawingClipSchema import DRAWING_CLIP_CAPABILITY_NAME
from VibeCADNativeDrawingStackSchema import DRAWING_STACK_CAPABILITY_NAME
from VibeCADNativeDrawingViewSchema import DRAWING_VIEW_CAPABILITY_NAME
from VibeCADNativeComponentInterfaceBindings import (
    COMPONENT_INTERFACE_CAPABILITY_NAME,
)
from VibeCADNativeModelDressupBindings import MODEL_DRESSUP_CAPABILITY_NAME
from VibeCADNativeModelCatalogBindings import MODEL_CATALOG_CAPABILITY_NAME
from VibeCADNativeModelBooleanBindings import MODEL_BOOLEAN_CAPABILITY_NAME
from VibeCADNativeModelFeatureBindings import MODEL_FEATURE_CAPABILITY_NAMES
from VibeCADNativeModelFastenerBindings import MODEL_FASTENER_CAPABILITY_NAME
from VibeCADNativeModelHoleBindings import MODEL_HOLE_CAPABILITY_NAME
from VibeCADNativeModelHistoryBindings import MODEL_HISTORY_CAPABILITY_NAMES
from VibeCADNativeModelJoinBindings import MODEL_JOIN_CAPABILITY_NAME
from VibeCADNativeModelPartBindings import MODEL_PART_CAPABILITY_NAME
from VibeCADNativeModelStructureBindings import MODEL_STRUCTURE_CAPABILITY_NAMES
from VibeCADNativeModelSurfaceBindings import MODEL_SURFACE_CAPABILITY_NAME
from VibeCADNativeModelTransformBindings import MODEL_TRANSFORM_CAPABILITY_NAME
from VibeCADNativeMeshConvertSchema import MESH_CONVERT_CAPABILITY_NAME
from VibeCADNativeMeshBooleanSchema import MESH_BOOLEAN_CAPABILITY_NAME
from VibeCADNativeMeshCutSchema import MESH_CUT_CAPABILITY_NAME
from VibeCADNativeMeshCurvatureSchema import MESH_CURVATURE_CAPABILITY_NAME
from VibeCADNativeMeshExportSchema import MESH_EXPORT_CAPABILITY_NAME
from VibeCADNativeMeshIOSchema import MESH_IO_CAPABILITY_NAME
from VibeCADNativeMeshInspectSchema import MESH_INSPECT_CAPABILITY_NAME
from VibeCADNativeMeshModifySchema import MESH_MODIFY_CAPABILITY_NAME
from VibeCADNativeMeshPointsSchema import MESH_POINTS_CAPABILITY_NAME
from VibeCADNativeMeshApproximateSchema import MESH_APPROXIMATE_CAPABILITY_NAME
from VibeCADNativeMeshRebuildSchema import MESH_REBUILD_CAPABILITY_NAME
from VibeCADNativeMeshSegmentSchema import MESH_SEGMENT_CAPABILITY_NAME
from VibeCADNativeManufactureInspectSchema import (
    MANUFACTURE_INSPECT_CAPABILITY_NAME,
)
from VibeCADNativeManufactureAreaSchema import MANUFACTURE_AREA_CAPABILITY_NAME
from VibeCADNativeManufactureJobSchema import MANUFACTURE_JOB_CAPABILITY_NAME
from VibeCADNativeManufactureModifySchema import (
    MANUFACTURE_MODIFY_CAPABILITY_NAME,
)
from VibeCADNativeManufactureProgramSchema import (
    MANUFACTURE_PROGRAM_CAPABILITY_NAME,
)
from VibeCADNativeManufactureProbeSchema import MANUFACTURE_PROBE_CAPABILITY_NAME
from VibeCADNativeManufacturePropertyBagSchema import (
    MANUFACTURE_PROPERTY_BAG_CAPABILITY_NAME,
)
from VibeCADNativeManufactureOperationSchema import (
    MANUFACTURE_OPERATION_CAPABILITY_NAME,
)
from VibeCADNativeManufactureCamoticsSchema import (
    MANUFACTURE_CAMOTICS_CAPABILITY_NAME,
)
from VibeCADNativeManufacturePostSchema import MANUFACTURE_POST_CAPABILITY_NAME
from VibeCADNativeManufactureTemplateSchema import (
    MANUFACTURE_TEMPLATE_CAPABILITY_NAME,
)
from VibeCADNativeManufactureSimulationSchema import (
    MANUFACTURE_SIMULATION_CAPABILITY_NAME,
)
from VibeCADNativeManufactureSimulationResultSchema import (
    MANUFACTURE_SIMULATION_RESULT_CAPABILITY_NAME,
)
from VibeCADNativeManufactureToolSchema import (
    MANUFACTURE_TOOL_CAPABILITY_NAME,
    MANUFACTURE_TOOL_CATALOG_CAPABILITY_NAME,
)
from VibeCADNativeManufactureToolOutputSchema import (
    MANUFACTURE_TOOL_OUTPUT_CAPABILITY_NAME,
)
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRobotExportSchema import ROBOT_EXPORT_CAPABILITY_NAME
from VibeCADNativeRobotMotionSchema import ROBOT_MOTION_CAPABILITY_NAME
from VibeCADNativeRobotSetupSchema import ROBOT_SETUP_CAPABILITY_NAME
from VibeCADNativeRobotTrajectorySchema import ROBOT_TRAJECTORY_CAPABILITY_NAME
from VibeCADNativeSketchBatchBindings import SKETCH_BATCH_CAPABILITY_NAME
from VibeCADNativeSketchProviderSchema import (
    SKETCH_PROVIDER_CAPABILITY_NAMES,
)


def test_production_registry_has_every_finished_contract_and_binding() -> None:
    registry = build_native_capability_registry()

    assert registry.shared_definition_names == (
        NATIVE_BACKGROUND_CAPABILITY_NAME,
        *COMMON_NATIVE_CAPABILITY_NAMES,
        "model.catalog",
        *MODEL_HISTORY_CAPABILITY_NAMES,
        SKETCH_BATCH_CAPABILITY_NAME,
    )
    expected = (
        *COMMON_NATIVE_CAPABILITY_NAMES,
        ANALYZE_CONNECTION_CAPABILITY_NAME,
        ANALYZE_ELECTROMAGNETIC_CAPABILITY_NAME,
        ANALYZE_FLUID_CAPABILITY_NAME,
        ANALYZE_GEOMETRICAL_CAPABILITY_NAME,
        ANALYZE_GEOMETRY_CAPABILITY_NAME,
        ANALYZE_INSPECT_CAPABILITY_NAME,
        ANALYZE_LOAD_CAPABILITY_NAME,
        ANALYZE_MODEL_CAPABILITY_NAME,
        ANALYZE_MESH_CAPABILITY_NAME,
        ANALYZE_MESH_FIELD_CAPABILITY_NAME,
        ANALYZE_MESH_OUTPUT_CAPABILITY_NAME,
        ANALYZE_MESH_REFINEMENT_CAPABILITY_NAME,
        ANALYZE_STRUCTURED_MESH_CAPABILITY_NAME,
        ANALYZE_PRESENTATION_CAPABILITY_NAME,
        ANALYZE_POST_CAPABILITY_NAME,
        ANALYZE_POST_FUNCTION_CAPABILITY_NAME,
        ANALYZE_VISUALIZATION_CAPABILITY_NAME,
        ANALYZE_RESULTS_CAPABILITY_NAME,
        ANALYZE_SOLVER_CAPABILITY_NAME,
        ANALYZE_SOLVER_CONTROL_CAPABILITY_NAME,
        ANALYZE_SOLVER_EXECUTION_CAPABILITY_NAME,
        ANALYZE_EQUATION_CAPABILITY_NAME,
        ANALYZE_SUPPORT_CAPABILITY_NAME,
        ANALYZE_THERMAL_CAPABILITY_NAME,
        ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME,
        ASSEMBLY_FASTENER_CAPABILITY_NAME,
        ASSEMBLY_EXPORT_CAPABILITY_NAME,
        ASSEMBLY_INSPECT_CAPABILITY_NAME,
        ASSEMBLY_JOINT_CAPABILITY_NAME,
        ASSEMBLY_PLAYBACK_CAPABILITY_NAME,
        ASSEMBLY_STRUCTURE_CAPABILITY_NAME,
            COMPONENT_INTERFACE_CAPABILITY_NAME,
            DRAWING_ACTIVE_VIEW_CAPABILITY_NAME,
            DRAWING_CLIP_CAPABILITY_NAME,
            DRAWING_COMPLEX_SECTION_CAPABILITY_NAME,
            DRAWING_DETAIL_CAPABILITY_NAME,
            DRAWING_DIMENSION_CAPABILITY_NAME,
            DRAWING_DIMENSION_REPAIR_CAPABILITY_NAME,
            DRAWING_LINE_DEFAULTS_CAPABILITY_NAME,
            DRAWING_LINE_ATTRIBUTES_CAPABILITY_NAME,
            DRAWING_LINE_LENGTH_CAPABILITY_NAME,
            DRAWING_VIEW_LOCK_CAPABILITY_NAME,
            DRAWING_SECTION_POSITION_CAPABILITY_NAME,
            DRAWING_FORMAT_CAPABILITY_NAME,
            DRAWING_DIMENSION_TEXT_CAPABILITY_NAME,
            DRAWING_PRESENTATION_CAPABILITY_NAME,
            DRAWING_HATCH_CAPABILITY_NAME,
            DRAWING_RICH_ANNOTATION_CAPABILITY_NAME,
            DRAWING_ANNOTATION_CAPABILITY_NAME,
            DRAWING_CIRCLE_CENTER_LINE_CAPABILITY_NAME,
            DRAWING_GENERAL_CENTER_LINE_CAPABILITY_NAME,
            DRAWING_BOLT_CIRCLE_CENTER_LINE_CAPABILITY_NAME,
            DRAWING_THREAD_REPRESENTATION_CAPABILITY_NAME,
            DRAWING_COSMETIC_VERTEX_CAPABILITY_NAME,
            DRAWING_COSMETIC_CURVE_CAPABILITY_NAME,
            DRAWING_COSMETIC_LINE_CAPABILITY_NAME,
            DRAWING_BALLOON_CAPABILITY_NAME,
            DRAWING_DRAFT_CAPABILITY_NAME,
            DRAWING_PAGE_CAPABILITY_NAME,
            DRAWING_SECTION_CAPABILITY_NAME,
            DRAWING_STACK_CAPABILITY_NAME,
        DRAWING_VIEW_CAPABILITY_NAME,
        NATIVE_BACKGROUND_CAPABILITY_NAME,
        MANUFACTURE_AREA_CAPABILITY_NAME,
        MANUFACTURE_INSPECT_CAPABILITY_NAME,
        MANUFACTURE_JOB_CAPABILITY_NAME,
        MANUFACTURE_MODIFY_CAPABILITY_NAME,
        MANUFACTURE_PROGRAM_CAPABILITY_NAME,
        MANUFACTURE_PROBE_CAPABILITY_NAME,
        MANUFACTURE_PROPERTY_BAG_CAPABILITY_NAME,
            MANUFACTURE_OPERATION_CAPABILITY_NAME,
            MANUFACTURE_CAMOTICS_CAPABILITY_NAME,
            MANUFACTURE_POST_CAPABILITY_NAME,
            MANUFACTURE_TEMPLATE_CAPABILITY_NAME,
            MANUFACTURE_SIMULATION_CAPABILITY_NAME,
        MANUFACTURE_SIMULATION_RESULT_CAPABILITY_NAME,
        MANUFACTURE_TOOL_CAPABILITY_NAME,
        MANUFACTURE_TOOL_CATALOG_CAPABILITY_NAME,
        MANUFACTURE_TOOL_OUTPUT_CAPABILITY_NAME,
        MESH_BOOLEAN_CAPABILITY_NAME,
        MESH_CONVERT_CAPABILITY_NAME,
        MESH_CUT_CAPABILITY_NAME,
        MESH_CURVATURE_CAPABILITY_NAME,
        MESH_EXPORT_CAPABILITY_NAME,
        MESH_IO_CAPABILITY_NAME,
        MESH_INSPECT_CAPABILITY_NAME,
        MESH_MODIFY_CAPABILITY_NAME,
        MESH_POINTS_CAPABILITY_NAME,
        MESH_APPROXIMATE_CAPABILITY_NAME,
        MESH_REBUILD_CAPABILITY_NAME,
        MESH_SEGMENT_CAPABILITY_NAME,
        MODEL_BOOLEAN_CAPABILITY_NAME,
        *MODEL_STRUCTURE_CAPABILITY_NAMES,
        *MODEL_FEATURE_CAPABILITY_NAMES,
        MODEL_FASTENER_CAPABILITY_NAME,
        MODEL_DRESSUP_CAPABILITY_NAME,
        MODEL_CATALOG_CAPABILITY_NAME,
        MODEL_HOLE_CAPABILITY_NAME,
        *MODEL_HISTORY_CAPABILITY_NAMES,
        MODEL_JOIN_CAPABILITY_NAME,
        MODEL_PART_CAPABILITY_NAME,
        MODEL_SURFACE_CAPABILITY_NAME,
        MODEL_TRANSFORM_CAPABILITY_NAME,
        ROBOT_EXPORT_CAPABILITY_NAME,
        ROBOT_MOTION_CAPABILITY_NAME,
        ROBOT_SETUP_CAPABILITY_NAME,
        ROBOT_TRAJECTORY_CAPABILITY_NAME,
        *SKETCH_PROVIDER_CAPABILITY_NAMES,
    )
    assert registry.definition_names == tuple(sorted(expected))
    assert registry.implementation_names == registry.definition_names


def test_production_registry_is_fresh_and_has_no_document_or_gui_state() -> None:
    first = build_native_capability_registry()
    second = build_native_capability_registry()

    assert first is not second
    assert first.definition_names == second.definition_names
    assert all(
        first.implementation(name) is not second.implementation(name)
        for name in first.implementation_names
    )


def test_current_registered_model_contracts_fit_the_hard_schema_budget() -> None:
    registry = build_native_capability_registry()
    schemas = []
    for name in registry.definition_names:
        definition = registry.definition(name)
        operations = tuple(
            variant.operation
            for variant in definition.variants
            if "model" in variant.surface_ids
        )
        if operations:
            schemas.append(definition.provider_schema(operations))

    encoded = json.dumps(
        schemas,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES
