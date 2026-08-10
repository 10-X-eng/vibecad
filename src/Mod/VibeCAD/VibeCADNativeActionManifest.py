# SPDX-License-Identifier: LGPL-2.1-or-later

"""Classification inventory for actions on the human-selected Native ribbon.

The live C++ manifest remains the action-graph authority. This module supplies
an explicit allowlist and planning metadata for those live IDs; it never
dispatches a FreeCAD command. New live actions fail classification until they
are deliberately added here.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from VibeCADRibbonSurface import RibbonAction, RibbonSurface
from VibeCADNativeSurfaceVariants import (
    NativeSurfaceVariantError,
    validate_surface_variant,
)


class NativeActionManifestError(RuntimeError):
    """A live ribbon action is absent from or conflicts with the inventory."""


KNOWN_ACTIONS_BY_SURFACE: dict[str, tuple[str, ...]] = {
    "analyze": (
        "Std_ViewFitAll",
        "Std_ViewIsometric",
        "VibeCAD_ToggleGrid",
        "FEM_Analysis",
        "FEM_MaterialSolid",
        "FEM_MaterialFluid",
        "FEM_MaterialMechanicalNonlinear",
        "FEM_MaterialReinforced",
        "FEM_MaterialEditor",
        "FEM_ElementGeometry1D",
        "FEM_ElementRotation1D",
        "FEM_ElementGeometry2D",
        "FEM_ElementFluid1D",
        "FEM_CompEmConstraints",
        "FEM_ConstraintElectromagnetic",
        "FEM_ConstraintCurrentDensity",
        "FEM_ConstraintMagnetization",
        "FEM_ConstraintElectricChargeDensity",
        "FEM_ConstraintInitialFlowVelocity",
        "FEM_ConstraintInitialPressure",
        "FEM_ConstraintFlowVelocity",
        "FEM_ConstraintPlaneRotation",
        "FEM_ConstraintSectionPrint",
        "FEM_ConstraintTransform",
        "FEM_ConstraintFixed",
        "FEM_ConstraintRigidBody",
        "FEM_ConstraintDisplacement",
        "FEM_ConstraintContact",
        "FEM_ConstraintTie",
        "FEM_ConstraintSpring",
        "FEM_ConstraintForce",
        "FEM_ConstraintPressure",
        "FEM_ConstraintCentrif",
        "FEM_ConstraintSelfWeight",
        "FEM_ConstraintInitialTemperature",
        "FEM_ConstraintHeatflux",
        "FEM_ConstraintTemperature",
        "FEM_ConstraintBodyHeatSource",
        "FEM_MeshNetgenFromShape",
        "FEM_MeshGmshFromShape",
        "FEM_MeshRegion",
        "FEM_MeshGroup",
        "FEM_MeshGMSHRefinement",
        "FEM_MeshDistance",
        "FEM_MeshBoundaryLayer",
        "FEM_MeshShape",
        "FEM_MeshManipulate",
        "FEM_MeshAdvanced",
        "FEM_MeshTransfiniteCurve",
        "FEM_MeshTransfiniteSurface",
        "FEM_MeshTransfiniteVolume",
        "FEM_FEMMesh2Mesh",
        "FEM_CompSolvers",
        "FEM_SolverCalculiX",
        "FEM_SolverElmer",
        "FEM_SolverMystran",
        "FEM_SolverZ88",
        "FEM_CompMechEquations",
        "FEM_EquationElasticity",
        "FEM_EquationDeformation",
        "FEM_CompEmEquations",
        "FEM_EquationElectrostatic",
        "FEM_EquationElectricforce",
        "FEM_EquationMagnetodynamic",
        "FEM_EquationMagnetodynamic2D",
        "FEM_EquationStaticCurrent",
        "FEM_EquationFlow",
        "FEM_EquationFlux",
        "FEM_EquationHeat",
        "FEM_SolverControl",
        "FEM_SolverRun",
        "FEM_ResultsPurge",
        "FEM_ResultShow",
        "FEM_PostApplyChanges",
        "FEM_PostPipelineFromResult",
        "FEM_PostBranchFilter",
        "FEM_PostFilterWarp",
        "FEM_PostFilterClipScalar",
        "FEM_PostFilterCutFunction",
        "FEM_PostFilterClipRegion",
        "FEM_PostFilterContours",
        "FEM_PostFilterGlyph",
        "FEM_PostFilterDataAlongLine",
        "FEM_PostFilterLinearizedStresses",
        "FEM_PostFilterDataAtPoint",
        "FEM_PostFilterCalculator",
        "FEM_PostCreateFunctions",
        "FEM_PostCreateFunctionPlane",
        "FEM_PostCreateFunctionSphere",
        "FEM_PostCreateFunctionCylinder",
        "FEM_PostCreateFunctionBox",
        "FEM_PostVisualization",
        "FEM_PostVisualizationLineplot",
        "FEM_PostVisualizationHistogram",
        "FEM_PostVisualizationTable",
        "FEM_ClippingPlaneAdd",
        "FEM_ClippingPlaneRemoveAll",
        "FEM_Examples",
        "Std_Measure",
        "Std_MassProperties",
        "Inspection_VisualInspection",
        "Inspection_InspectElement",
        "Part_CheckGeometry",
    ),
    "assemble": (
        "Std_ViewFitAll",
        "Std_ViewIsometric",
        "VibeCAD_ToggleGrid",
        "Assembly_CreateAssembly",
        "Assembly_ActivateAssembly",
        "Assembly_Insert",
        "Assembly_InsertLink",
        "Assembly_InsertNewPart",
        "Assembly_SolveAssembly",
        "Assembly_CreateView",
        "Assembly_CreateSimulation",
        "Assembly_CreateBom",
        "Assembly_ToggleGrounded",
        "Assembly_CreateJointFixed",
        "Assembly_CreateJointRevolute",
        "Assembly_CreateJointCylindrical",
        "Assembly_CreateJointSlider",
        "Assembly_CreateJointBall",
        "Assembly_CreateJointDistance",
        "Assembly_CreateJointParallel",
        "Assembly_CreateJointPerpendicular",
        "Assembly_CreateJointAngle",
        "Assembly_CreateJointRackPinion",
        "Assembly_CreateJointScrew",
        "Assembly_CreateJointGearBelt",
        "Assembly_CreateJointGears",
        "Assembly_CreateJointBelt",
        "Assembly_SelectConflictingConstraints",
        "Assembly_SelectRedundantConstraints",
        "Assembly_SelectPartiallyRedundantConstraints",
        "Assembly_SelectMalformedConstraints",
        "Assembly_SelectJointsOfComponent",
        "Std_Measure",
        "Std_MassProperties",
        "Inspection_VisualInspection",
        "Inspection_InspectElement",
        "Part_CheckGeometry",
        "VibeCAD_InsertStandardFastener",
        "VibeCAD_EditStandardFastener",
        "Robot_Create",
        "Robot_AddToolShape",
        "Robot_SetDefaultOrientation",
        "Robot_SetDefaultValues",
        "Robot_CreateTrajectory",
        "Robot_InsertWaypoint",
        "Robot_InsertWaypointPreselect",
        "Robot_Edge2Trac",
        "Robot_TrajectoryDressUp",
        "Robot_TrajectoryCompound",
        "Robot_SetHomePos",
        "Robot_RestoreHomePos",
        "Robot_Simulate",
        "VibeCAD_PublishInterface",
    ),
    "drawing": (
        "Std_ViewFitAll",
        "Std_ViewIsometric",
        "VibeCAD_ToggleGrid",
        "TechDraw_PageDefault",
        "TechDraw_PageTemplate",
        "TechDraw_FillTemplateFields",
        "TechDraw_RedrawPage",
        "TechDraw_PrintAll",
        "TechDraw_View",
        "TechDraw_BrokenView",
        "TechDraw_ActiveView",
        "TechDraw_SectionGroup",
        "TechDraw_SectionView",
        "TechDraw_ComplexSection",
        "TechDraw_DetailView",
        "TechDraw_DraftView",
        "TechDraw_ClipGroup",
        "TechDraw_StackGroup",
        "TechDraw_StackTop",
        "TechDraw_StackBottom",
        "TechDraw_StackUp",
        "TechDraw_StackDown",
        "TechDraw_CompDimensionTools",
        "TechDraw_Dimension",
        "TechDraw_LengthDimension",
        "TechDraw_HorizontalDimension",
        "TechDraw_VerticalDimension",
        "TechDraw_RadiusDimension",
        "TechDraw_DiameterDimension",
        "TechDraw_AngleDimension",
        "TechDraw_3PtAngleDimension",
        "TechDraw_AreaDimension",
        "TechDraw_ExtensionCreateLengthArc",
        "TechDraw_HorizontalExtentDimension",
        "TechDraw_VerticalExtentDimension",
        "TechDraw_ExtensionCreateHorizChainDimension",
        "TechDraw_ExtensionCreateVertChainDimension",
        "TechDraw_ExtensionCreateObliqueChainDimension",
        "TechDraw_ExtensionCreateHorizCoordDimension",
        "TechDraw_ExtensionCreateVertCoordDimension",
        "TechDraw_ExtensionCreateObliqueCoordDimension",
        "TechDraw_ExtensionCreateHorizChamferDimension",
        "TechDraw_ExtensionCreateVertChamferDimension",
        "TechDraw_Balloon",
        "TechDraw_AxoLengthDimension",
        "TechDraw_DimensionRepair",
        "TechDraw_ExtensionSelectLineAttributes",
        "TechDraw_ExtensionChangeLineAttributes",
        "TechDraw_ExtensionExtendShortenLineGroup",
        "TechDraw_ExtensionExtendLine",
        "TechDraw_ExtensionShortenLine",
        "TechDraw_ExtensionLockUnlockView",
        "TechDraw_ExtensionPositionSectionView",
        "TechDraw_ExtensionCustomizeFormat",
        "TechDraw_ExtensionCircleCenterLinesGroup",
        "TechDraw_ExtensionCircleCenterLines",
        "TechDraw_ExtensionHoleCircle",
        "TechDraw_ExtensionThreadsGroup",
        "TechDraw_ExtensionThreadHoleSide",
        "TechDraw_ExtensionThreadHoleBottom",
        "TechDraw_ExtensionThreadBoltSide",
        "TechDraw_ExtensionThreadBoltBottom",
        "TechDraw_CommandVertexCreationGroup",
        "TechDraw_ExtensionVertexAtIntersection",
        "TechDraw_CommandAddOffsetVertex",
        "TechDraw_ExtensionDrawCirclesGroup",
        "TechDraw_CosmeticCircle",
        "TechDraw_ExtensionDrawCosmCircle",
        "TechDraw_ExtensionDrawCosmCircle3Points",
        "TechDraw_ExtensionDrawCosmArc",
        "TechDraw_ExtensionLinePPGroup",
        "TechDraw_ExtensionLineParallel",
        "TechDraw_ExtensionLinePerpendicular",
        "TechDraw_ExtensionInsertPrefixGroup",
        "TechDraw_ExtensionInsertDiameter",
        "TechDraw_ExtensionInsertSquare",
        "TechDraw_ExtensionInsertRepetition",
        "TechDraw_ExtensionRemovePrefixChar",
        "TechDraw_ExtensionIncreaseDecreaseGroup",
        "TechDraw_ExtensionIncreaseDecimal",
        "TechDraw_ExtensionDecreaseDecimal",
        "TechDraw_ExportPageSVG",
        "TechDraw_ExportPageDXF",
        "TechDraw_ToggleFrame",
        "TechDraw_Hatch",
        "TechDraw_GeometricHatch",
        "TechDraw_RichTextAnnotation",
        "TechDraw_LeaderLine",
        "TechDraw_CosmeticVertexGroup",
        "TechDraw_CosmeticVertex",
        "TechDraw_Midpoints",
        "TechDraw_Quadrants",
        "TechDraw_CenterLineGroup",
        "TechDraw_FaceCenterLine",
        "TechDraw_2LineCenterLine",
        "TechDraw_2PointCenterLine",
        "TechDraw_2PointCosmeticLine",
        "TechDraw_DecorateLine",
        "TechDraw_ShowAll",
        "TechDraw_WeldSymbol",
        "TechDraw_SurfaceFinishSymbols",
        "TechDraw_HoleShaftFit",
        "Std_Measure",
        "Std_MassProperties",
        "Inspection_VisualInspection",
        "Inspection_InspectElement",
        "Part_CheckGeometry",
    ),
    "manufacture": (
        "Std_ViewFitAll",
        "Std_ViewIsometric",
        "VibeCAD_ToggleGrid",
        "CAM_Job",
        "CAM_Sanity",
        "CAM_PostTools",
        "CAM_Post",
        "CAM_PostSelected",
        "CAM_SimTools",
        "CAM_SimulatorGL",
        "CAM_Simulator",
        "CAM_Inspect",
        "CAM_SelectLoop",
        "CAM_OpActiveToggle",
        "CAM_ToolBitDock",
        "CAM_Profile",
        "CAM_Pocket_Shape",
        "CAM_MillFacing",
        "CAM_Helix",
        "CAM_Adaptive",
        "CAM_Slot",
        "CAM_DrillingTools",
        "CAM_Drilling",
        "CAM_ThreadMilling",
        "CAM_EngraveTools",
        "CAM_Engrave",
        "CAM_Deburr",
        "CAM_Vcarve",
        "CAM_Pocket3D",
        "CAM_OperationCopy",
        "CAM_Array",
        "CAM_SimpleCopy",
        "CAM_DressupTools",
        "CAM_DressupArray",
        "CAM_DressupAxisMap",
        "CAM_DressupPathBoundary",
        "CAM_DressupDogbone",
        "CAM_DressupDragKnife",
        "CAM_DressupLeadInOut",
        "CAM_DressupMirror",
        "CAM_DressupRampEntry",
        "CAM_DressupTag",
        "CAM_DressupZCorrect",
        "Robot_Edge2Trac",
        "Robot_TrajectoryDressUp",
        "Robot_TrajectoryCompound",
        "Robot_Simulate",
        "Robot_ExportKukaCompact",
        "Robot_ExportKukaFull",
        "Std_Measure",
        "Std_MassProperties",
        "Inspection_VisualInspection",
        "Inspection_InspectElement",
        "Part_CheckGeometry",
    ),
    "mesh": (
        "Std_ViewFitAll",
        "Std_ViewIsometric",
        "VibeCAD_ToggleGrid",
        "Mesh_Import",
        "Mesh_Export",
        "Mesh_BuildRegularSolid",
        "Mesh_FromPartShape",
        "MeshPart_ShapeFromMesh",
        "MeshPart_CurveOnMesh",
        "Mesh_HarmonizeNormals",
        "Mesh_FlipNormals",
        "Mesh_FillupHoles",
        "Mesh_FillInteractiveHole",
        "Mesh_AddFacet",
        "Mesh_RemoveComponents",
        "Mesh_Smoothing",
        "Mesh_RemeshGmsh",
        "Mesh_Decimating",
        "Mesh_Scale",
        "Mesh_Union",
        "Mesh_Intersection",
        "Mesh_Difference",
        "Mesh_PolyCut",
        "Mesh_PolyTrim",
        "Mesh_TrimByPlane",
        "Mesh_SectionByPlane",
        "Mesh_CrossSections",
        "Mesh_Merge",
        "Mesh_SplitComponents",
        "Mesh_Segmentation",
        "Mesh_SegmentationBestFit",
        "Reen_Segmentation",
        "Reen_SegmentationManual",
        "Reen_SegmentationFromComponents",
        "Reen_MeshBoundary",
        "Mesh_Evaluation",
        "Mesh_EvaluateFacet",
        "Mesh_VertexCurvature",
        "Mesh_CurvatureInfo",
        "Mesh_EvaluateSolid",
        "Mesh_BoundingBox",
        "Points_Import",
        "Points_Export",
        "Points_Convert",
        "Points_Structure",
        "Points_Merge",
        "Points_PolyCut",
        "Reen_PoissonReconstruction",
        "Reen_ViewTriangulation",
        "Reen_ApproxPlane",
        "Reen_ApproxCylinder",
        "Reen_ApproxSphere",
        "Reen_ApproxPolynomial",
        "Reen_ApproxSurface",
        "Reen_ApproxCurve",
        "Std_Measure",
        "Std_MassProperties",
        "Inspection_VisualInspection",
        "Inspection_InspectElement",
        "Part_CheckGeometry",
    ),
    "model": (
        "Std_ViewFitAll",
        "Std_ViewIsometric",
        "VibeCAD_ToggleGrid",
        "PartDesign_NewComponent",
        "PartDesign_NewBody",
        "Sketcher_NewSketch",
        "Sketcher_EditSketch",
        "Sketcher_ValidateSketch",
        "PartDesign_SubShapeBinder",
        "PartDesign_Clone",
        "PartDesign_DesignExtrude",
        "PartDesign_DesignRevolve",
        "PartDesign_DesignLoft",
        "PartDesign_DesignSweep",
        "PartDesign_DesignHelix",
        "PartDesign_DesignPrimitive",
        "PartDesign::DesignBox",
        "PartDesign::DesignCylinder",
        "PartDesign::DesignSphere",
        "PartDesign::DesignCone",
        "PartDesign::DesignEllipsoid",
        "PartDesign::DesignTorus",
        "PartDesign::DesignPrism",
        "PartDesign::DesignWedge",
        "PartDesign::DesignTube",
        "PartDesign_Hole",
        "PartDesign_Fillet",
        "PartDesign_Chamfer",
        "PartDesign_Draft",
        "PartDesign_Thickness",
        "PartDesign_Scale",
        "PartDesign_DesignMirror",
        "PartDesign_DesignLinearPattern",
        "PartDesign_DesignCircularPattern",
        "Part_Primitives",
        "Part_Builder",
        "Part_Extrude",
        "Part_Revolve",
        "Part_Mirror",
        "Part_MakeFace",
        "Part_RuledSurface",
        "Part_Loft",
        "Part_Sweep",
        "Part_Section",
        "Part_CrossSections",
        "Part_CompOffset",
        "Part_Offset",
        "Part_Offset2D",
        "Part_ProjectionOnSurface",
        "Part_Compound",
        "PartDesign_Separate",
        "Part_CompoundFilter",
        "PartDesign_Combine",
        "Part_CompJoinFeatures",
        "Part_JoinConnect",
        "Part_JoinEmbed",
        "Part_JoinCutout",
        "PartDesign_Split",
        "Part_Defeaturing",
        "Std_Measure",
        "Std_MassProperties",
        "Inspection_VisualInspection",
        "Inspection_InspectElement",
        "Part_CheckGeometry",
        "VibeCAD_InsertStandardFastener",
        "VibeCAD_EditStandardFastener",
        "VibeCAD_CreateMatchingFastenerHole",
        "VibeCAD_AttachStandardFastener",
        "Surface_Filling",
        "Surface_GeomFillSurface",
        "Surface_Sections",
        "Surface_ExtendFace",
        "Surface_CurveOnMesh",
        "Surface_BlendCurve",
        "VibeCAD_PublishInterface",
    ),
    "parameters": (
        "Std_ViewFitAll",
        "Std_ViewIsometric",
        "VibeCAD_ToggleGrid",
        "Spreadsheet_CreateSheet",
        "Spreadsheet_Import",
        "Spreadsheet_Export",
        "Spreadsheet_MergeCells",
        "Spreadsheet_SplitCell",
        "Spreadsheet_CellProperties",
        "Spreadsheet_SetAlias",
        "Spreadsheet_AlignLeft",
        "Spreadsheet_AlignCenter",
        "Spreadsheet_AlignRight",
        "Spreadsheet_AlignTop",
        "Spreadsheet_AlignVCenter",
        "Spreadsheet_AlignBottom",
        "Spreadsheet_StyleBold",
        "Spreadsheet_StyleItalic",
        "Spreadsheet_StyleUnderline",
        "Std_Measure",
        "Std_MassProperties",
        "Inspection_VisualInspection",
        "Inspection_InspectElement",
        "Part_CheckGeometry",
    ),
    "sketch.edit": (
        "Std_ViewFitAll",
        "Std_ViewIsometric",
        "VibeCAD_ToggleGrid",
        "Sketcher_LeaveSketch",
        "Sketcher_CancelSketch",
        "Sketcher_ViewSketch",
        "Sketcher_ViewSection",
        "Sketcher_CreatePoint",
        "Sketcher_CompLine",
        "Sketcher_CreatePolyline",
        "Sketcher_CreateLine",
        "Sketcher_CompCreateArc",
        "Sketcher_CreateArc",
        "Sketcher_Create3PointArc",
        "Sketcher_CreateArcOfEllipse",
        "Sketcher_CreateArcOfHyperbola",
        "Sketcher_CreateArcOfParabola",
        "Sketcher_CompCreateConic",
        "Sketcher_CreateCircle",
        "Sketcher_Create3PointCircle",
        "Sketcher_CreateEllipseByCenter",
        "Sketcher_CreateEllipseBy3Points",
        "Sketcher_CompCreateRectangles",
        "Sketcher_CreateRectangle",
        "Sketcher_CreateRectangle_Center",
        "Sketcher_CreateOblong",
        "Sketcher_CompCreateRegularPolygon",
        "Sketcher_CreateTriangle",
        "Sketcher_CreateSquare",
        "Sketcher_CreatePentagon",
        "Sketcher_CreateHexagon",
        "Sketcher_CreateHeptagon",
        "Sketcher_CreateOctagon",
        "Sketcher_CreateRegularPolygon",
        "Sketcher_CompSlot",
        "Sketcher_CreateSlot",
        "Sketcher_CreateArcSlot",
        "Sketcher_CompCreateBSpline",
        "Sketcher_CreateBSpline",
        "Sketcher_CreatePeriodicBSpline",
        "Sketcher_CreateBSplineByInterpolation",
        "Sketcher_CreatePeriodicBSplineByInterpolation",
        "Sketcher_CreateText",
        "Sketcher_ToggleConstruction",
        "Sketcher_CompDimensionTools",
        "Sketcher_Dimension",
        "Sketcher_ConstrainDistanceX",
        "Sketcher_ConstrainDistanceY",
        "Sketcher_ConstrainDistance",
        "Sketcher_ConstrainRadiam",
        "Sketcher_ConstrainRadius",
        "Sketcher_ConstrainDiameter",
        "Sketcher_ConstrainAngle",
        "Sketcher_ConstrainLock",
        "Sketcher_ConstrainCoincidentUnified",
        "Sketcher_CompHorVer",
        "Sketcher_ConstrainHorVer",
        "Sketcher_ConstrainHorizontal",
        "Sketcher_ConstrainVertical",
        "Sketcher_ConstrainParallel",
        "Sketcher_ConstrainPerpendicular",
        "Sketcher_ConstrainTangent",
        "Sketcher_ConstrainEqual",
        "Sketcher_ConstrainSymmetric",
        "Sketcher_ConstrainBlock",
        "Sketcher_ConstrainGroup",
        "Sketcher_CompToggleConstraints",
        "Sketcher_ToggleDrivingConstraint",
        "Sketcher_ToggleActiveConstraint",
        "Sketcher_CompCreateFillets",
        "Sketcher_CreateFillet",
        "Sketcher_CreateChamfer",
        "Sketcher_CompCurveEdition",
        "Sketcher_Trimming",
        "Sketcher_Split",
        "Sketcher_Extend",
        "Sketcher_CompExternal",
        "Sketcher_Projection",
        "Sketcher_Intersection",
        "Sketcher_CarbonCopy",
        "Sketcher_Translate",
        "Sketcher_Rotate",
        "Sketcher_Scale",
        "Sketcher_Offset",
        "Sketcher_Symmetry",
        "Sketcher_RemoveAxesAlignment",
        "Sketcher_BSplineConvertToNURBS",
        "Sketcher_BSplineIncreaseDegree",
        "Sketcher_BSplineDecreaseDegree",
        "Sketcher_CompModifyKnotMultiplicity",
        "Sketcher_BSplineIncreaseKnotMultiplicity",
        "Sketcher_BSplineDecreaseKnotMultiplicity",
        "Sketcher_BSplineInsertKnot",
        "Sketcher_JoinCurves",
        "Sketcher_SelectConstraints",
        "Sketcher_SelectElementsAssociatedWithConstraints",
        "Sketcher_ArcOverlay",
        "Sketcher_CompBSplineShowHideGeometryInformation",
        "Sketcher_BSplineDegree",
        "Sketcher_BSplinePolygon",
        "Sketcher_BSplineComb",
        "Sketcher_BSplineKnotMultiplicity",
        "Sketcher_BSplinePoleWeight",
        "Sketcher_RestoreInternalAlignmentGeometry",
        "Sketcher_SwitchVirtualSpace",
    ),
    "sketch.setup": (
        "Std_ViewFitAll",
        "Std_ViewIsometric",
        "VibeCAD_ToggleGrid",
        "Sketcher_NewSketch",
        "Sketcher_EditSketch",
        "Sketcher_MapSketch",
        "Sketcher_ReorientSketch",
        "Sketcher_ValidateSketch",
        "Sketcher_MergeSketches",
        "Sketcher_MirrorSketch",
        "Std_Measure",
        "Std_MassProperties",
        "Inspection_VisualInspection",
        "Inspection_InspectElement",
        "Part_CheckGeometry",
    ),
    "unavailable": (),
}

# These commands are live only under supported preferences or optional runtime
# features. They stay separate from the proven default graph so tests can
# detect accidental default-surface drift while the classifier accepts every
# shipped variant deliberately.
OPTIONAL_ACTIONS_BY_SURFACE: dict[str, tuple[str, ...]] = {
    "analyze": (),
    "assemble": (),
    "drawing": (
        "TechDraw_ExtentGroup",
        "TechDraw_ExtensionAreaAnnotation",
        "TechDraw_ExtensionArcLengthAnnotation",
        "TechDraw_ExtensionCreateChainDimensionGroup",
        "TechDraw_ExtensionCreateCoordDimensionGroup",
        "TechDraw_ExtensionChamferDimensionGroup",
    ),
    "manufacture": (
        "CAM_Area",
        "CAM_Area_Workplane",
        "CAM_Camotics",
        "CAM_3dTools",
        "CAM_Surface",
        "CAM_Waterline",
        "CAM_RotarySurface",
    ),
    "mesh": (),
    "model": (),
    "parameters": (),
    "sketch.edit": (),
    "sketch.setup": (),
    "unavailable": (),
}

if set(OPTIONAL_ACTIONS_BY_SURFACE) != set(KNOWN_ACTIONS_BY_SURFACE):
    raise NativeActionManifestError(
        "Default and optional Native surface inventories must cover the same surfaces."
    )

ALLOWED_ACTION_IDS_BY_SURFACE = {
    surface_id: frozenset((*command_ids, *OPTIONAL_ACTIONS_BY_SURFACE[surface_id]))
    for surface_id, command_ids in KNOWN_ACTIONS_BY_SURFACE.items()
}

KNOWN_COMPOSITE_COMMAND_IDS = frozenset(
    {
        "Assembly_CreateJointGearBelt",
        "Assembly_Insert",
        "CAM_DressupTools",
        "CAM_DrillingTools",
        "CAM_EngraveTools",
        "CAM_3dTools",
        "CAM_PostTools",
        "CAM_SimTools",
        "FEM_CompEmConstraints",
        "FEM_CompEmEquations",
        "FEM_CompMechEquations",
        "FEM_CompSolvers",
        "FEM_MeshGMSHRefinement",
        "FEM_PostCreateFunctions",
        "FEM_PostVisualization",
        "PartDesign_DesignPrimitive",
        "Part_CompJoinFeatures",
        "Part_CompOffset",
        "Sketcher_CompBSplineShowHideGeometryInformation",
        "Sketcher_CompCreateArc",
        "Sketcher_CompCreateBSpline",
        "Sketcher_CompCreateConic",
        "Sketcher_CompCreateFillets",
        "Sketcher_CompCreateRectangles",
        "Sketcher_CompCreateRegularPolygon",
        "Sketcher_CompCurveEdition",
        "Sketcher_CompDimensionTools",
        "Sketcher_CompExternal",
        "Sketcher_CompHorVer",
        "Sketcher_CompLine",
        "Sketcher_CompModifyKnotMultiplicity",
        "Sketcher_CompSlot",
        "Sketcher_CompToggleConstraints",
        "TechDraw_CenterLineGroup",
        "TechDraw_CommandVertexCreationGroup",
        "TechDraw_CompDimensionTools",
        "TechDraw_CosmeticVertexGroup",
        "TechDraw_ExtentGroup",
        "TechDraw_ExtensionChamferDimensionGroup",
        "TechDraw_ExtensionCircleCenterLinesGroup",
        "TechDraw_ExtensionCreateChainDimensionGroup",
        "TechDraw_ExtensionCreateCoordDimensionGroup",
        "TechDraw_ExtensionDrawCirclesGroup",
        "TechDraw_ExtensionExtendShortenLineGroup",
        "TechDraw_ExtensionIncreaseDecreaseGroup",
        "TechDraw_ExtensionInsertPrefixGroup",
        "TechDraw_ExtensionLinePPGroup",
        "TechDraw_ExtensionThreadsGroup",
        "TechDraw_SectionGroup",
        "TechDraw_StackGroup",
    }
)

_EXACT_COMPOSITE_CHILDREN_BY_SURFACE = {
    "model": {
        "PartDesign_DesignPrimitive": (
            "PartDesign::DesignBox",
            "PartDesign::DesignCylinder",
            "PartDesign::DesignSphere",
            "PartDesign::DesignCone",
            "PartDesign::DesignEllipsoid",
            "PartDesign::DesignTorus",
            "PartDesign::DesignPrism",
            "PartDesign::DesignWedge",
            "PartDesign::DesignTube",
        ),
        "Part_CompOffset": (
            "Part_Offset",
            "Part_Offset2D",
        ),
        "Part_CompJoinFeatures": (
            "Part_JoinConnect",
            "Part_JoinEmbed",
            "Part_JoinCutout",
        ),
    },
}

DEFAULT_SURFACE_ACTION_COUNTS = {
    surface_id: len(command_ids)
    for surface_id, command_ids in KNOWN_ACTIONS_BY_SURFACE.items()
    if surface_id != "unavailable"
}
DEFAULT_UNIQUE_ACTION_COUNT = len(
    {
        command_id
        for command_ids in KNOWN_ACTIONS_BY_SURFACE.values()
        for command_id in command_ids
    }
)

_HUMAN_ONLY_COMMAND_IDS = frozenset(
    {
        "Assembly_ActivateAssembly",
        "CAM_ToolBitDock",
        "FEM_Examples",
        "Sketcher_EditSketch",
        "Sketcher_CancelSketch",
    }
)

_VIEW_COMMAND_IDS = frozenset(
    {
        "Std_ViewFitAll",
        "Std_ViewIsometric",
        "VibeCAD_ToggleGrid",
        "Sketcher_ViewSketch",
        "Sketcher_ViewSection",
        "Sketcher_ArcOverlay",
        "Sketcher_BSplineDegree",
        "Sketcher_BSplinePolygon",
        "Sketcher_BSplineComb",
        "Sketcher_BSplineKnotMultiplicity",
        "Sketcher_BSplinePoleWeight",
        "FEM_ClippingPlaneAdd",
        "FEM_ClippingPlaneRemoveAll",
        "FEM_ResultShow",
        "TechDraw_ToggleFrame",
        "TechDraw_ShowAll",
    }
)

_READ_COMMAND_IDS = frozenset(
    {
        "Std_Measure",
        "Std_MassProperties",
        "Inspection_VisualInspection",
        "Inspection_InspectElement",
        "Part_CheckGeometry",
        "Sketcher_ValidateSketch",
        "Sketcher_SelectConstraints",
        "Sketcher_SelectElementsAssociatedWithConstraints",
        "Assembly_SelectConflictingConstraints",
        "Assembly_SelectRedundantConstraints",
        "Assembly_SelectPartiallyRedundantConstraints",
        "Assembly_SelectMalformedConstraints",
        "Assembly_SelectJointsOfComponent",
        "CAM_Sanity",
        "CAM_Inspect",
        "CAM_SelectLoop",
        "Mesh_Evaluation",
        "Mesh_EvaluateFacet",
        "Mesh_VertexCurvature",
        "Mesh_CurvatureInfo",
        "Mesh_EvaluateSolid",
        "Mesh_BoundingBox",
        "TechDraw_ExtensionSelectLineAttributes",
    }
)

_EXPORT_COMMAND_IDS = frozenset(
    {
        "Mesh_Export",
        "Points_Export",
        "Spreadsheet_Export",
        "TechDraw_PrintAll",
        "TechDraw_ExportPageSVG",
        "TechDraw_ExportPageDXF",
        "CAM_Post",
        "CAM_PostSelected",
        "Robot_ExportKukaCompact",
        "Robot_ExportKukaFull",
    }
)

_BACKGROUND_COMMAND_IDS = frozenset(
    {
        "Mesh_RemeshGmsh",
        "Reen_PoissonReconstruction",
        "FEM_MeshNetgenFromShape",
        "FEM_MeshGmshFromShape",
        "FEM_SolverRun",
        "CAM_SimulatorGL",
        "CAM_Simulator",
        "CAM_Post",
        "CAM_PostSelected",
        "TechDraw_RedrawPage",
        "TechDraw_PrintAll",
        "TechDraw_ExportPageSVG",
        "TechDraw_ExportPageDXF",
    }
)

_SESSION_COMMAND_IDS = frozenset(
    {
        "Robot_SetDefaultOrientation",
        "Robot_SetDefaultValues",
        "Robot_Simulate",
    }
)

_INTERACTIVE_COMMAND_IDS = (
    frozenset(
        {
            "FEM_MaterialEditor",
            "CAM_ToolBitDock",
            "CAM_SimulatorGL",
            "CAM_Simulator",
        }
    )
    | _HUMAN_ONLY_COMMAND_IDS
)

_CAPABILITY_OVERRIDES = {
    "Std_ViewFitAll": "view.control",
    "Std_ViewIsometric": "view.control",
    "VibeCAD_ToggleGrid": "view.control",
    "Std_Measure": "inspect.query",
    "Std_MassProperties": "inspect.query",
    "Inspection_VisualInspection": "inspect.query",
    "Inspection_InspectElement": "inspect.query",
    "Part_CheckGeometry": "inspect.query",
    "PartDesign_Hole": "model.hole",
    "PartDesign_Scale": "model.transform",
    "Part_Primitives": "model.part",
    "Part_Builder": "model.part",
    "Part_Extrude": "model.part",
    "Part_Revolve": "model.part",
    "Part_Mirror": "model.part",
    "Part_MakeFace": "model.part",
    "Part_RuledSurface": "model.part",
    "Part_Loft": "model.part",
    "Part_Sweep": "model.part",
    "Part_Section": "model.boolean",
    "PartDesign_Combine": "model.boolean",
    "Part_CrossSections": "model.part",
    "Part_Offset": "model.part",
    "Part_Offset2D": "model.part",
    "Part_ProjectionOnSurface": "model.part",
    "Part_Compound": "model.part",
    "Part_CompoundFilter": "model.part",
    "Part_Defeaturing": "model.part",
    "Part_JoinConnect": "model.join",
    "Part_JoinEmbed": "model.join",
    "Part_JoinCutout": "model.join",
    "PartDesign_Split": "model.boolean",
    "PartDesign_Separate": "model.structure",
    "FEM_ResultShow": "analyze.presentation",
    "CAM_Sanity": "manufacture.inspect",
    "CAM_Inspect": "manufacture.inspect",
    "CAM_SelectLoop": "manufacture.inspect",
    "CAM_OpActiveToggle": "manufacture.modify",
    "CAM_SimulatorGL": "manufacture.simulation",
    "CAM_Simulator": "manufacture.simulation",
    "CAM_Post": "manufacture.post",
    "CAM_PostSelected": "manufacture.post",
    "Mesh_Export": "mesh.export",
    "Points_Export": "mesh.export",
    "Spreadsheet_Export": "parameters.export",
    "Sketcher_NewSketch": "model.sketch",
    "Sketcher_EditSketch": "sketch.control",
    "Sketcher_ValidateSketch": "sketch.validate",
    "Sketcher_LeaveSketch": "sketch.control",
    "Sketcher_CancelSketch": "sketch.control",
    "Sketcher_SelectConstraints": "sketch.inspect",
    "Sketcher_SelectElementsAssociatedWithConstraints": "sketch.inspect",
    "Sketcher_ViewSketch": "sketch.presentation",
    "Sketcher_ViewSection": "sketch.presentation",
    "Sketcher_RestoreInternalAlignmentGeometry": "sketch.geometry",
    "Sketcher_SwitchVirtualSpace": "sketch.constraint",
    "TechDraw_ExtensionSelectLineAttributes": "drawing.inspect",
    "TechDraw_PrintAll": "drawing.export",
    "TechDraw_ToggleFrame": "drawing.presentation",
    "TechDraw_Hatch": "drawing.hatch",
    "TechDraw_GeometricHatch": "drawing.hatch",
    "TechDraw_ShowAll": "drawing.presentation",
    "VibeCAD_PublishInterface": "component.interface",
    "Robot_Simulate": "robot.motion",
}

_OPERATION_VARIANT_OVERRIDES = {
    "Assembly_CreateJointBall": "create_ball",
    "Assembly_CreateJointBelt": "create_belt",
    "Assembly_CreateJointCylindrical": "create_cylindrical",
    "Assembly_CreateJointAngle": "create_angle",
    "Assembly_CreateJointDistance": "create_distance",
    "Assembly_CreateJointFixed": "create_fixed",
    "Assembly_CreateJointGears": "create_gears",
    "Assembly_CreateJointParallel": "create_parallel",
    "Assembly_CreateJointPerpendicular": "create_perpendicular",
    "Assembly_CreateJointRackPinion": "create_rack_pinion",
    "Assembly_CreateJointRevolute": "create_revolute",
    "Assembly_CreateJointScrew": "create_screw",
    "Assembly_CreateJointSlider": "create_slider",
    "Assembly_ToggleGrounded": "set_grounded",
    "PartDesign::DesignBox": "primitive",
    "PartDesign::DesignCylinder": "primitive",
    "PartDesign::DesignSphere": "primitive",
    "PartDesign::DesignCone": "primitive",
    "PartDesign::DesignEllipsoid": "primitive",
    "PartDesign::DesignTorus": "primitive",
    "PartDesign::DesignPrism": "primitive",
    "PartDesign::DesignWedge": "primitive",
    "PartDesign::DesignTube": "primitive",
    "PartDesign_DesignExtrude": "profile",
    "PartDesign_DesignRevolve": "profile",
    "PartDesign_DesignLoft": "profile",
    "PartDesign_DesignSweep": "profile",
    "PartDesign_DesignHelix": "profile",
    "PartDesign_DesignMirror": "pattern",
    "PartDesign_DesignLinearPattern": "pattern",
    "PartDesign_DesignCircularPattern": "pattern",
    "PartDesign_Scale": "scale",
    "Part_Primitives": "primitive",
    "Part_Builder": "builder",
    "Part_Extrude": "extrude",
    "Part_Revolve": "revolve",
    "Part_Mirror": "mirror",
    "Part_MakeFace": "make_face",
    "Part_RuledSurface": "ruled_surface",
    "Part_Loft": "loft",
    "Part_Sweep": "sweep",
    "Part_Section": "section",
    "PartDesign_Combine": "combine",
    "Part_CrossSections": "cross_sections",
    "Part_Offset": "offset_3d",
    "Part_Offset2D": "offset_2d",
    "Part_ProjectionOnSurface": "project_surface",
    "Part_Compound": "compound",
    "Part_CompoundFilter": "compound_filter",
    "Part_Defeaturing": "defeature",
    "Part_JoinConnect": "connect",
    "Part_JoinEmbed": "embed",
    "Part_JoinCutout": "cutout",
    "PartDesign_Split": "split",
    "PartDesign_Separate": "separate",
    "Std_ViewFitAll": "fit_all",
    "Std_ViewIsometric": "isometric",
    "VibeCAD_ToggleGrid": "set_grid",
    "Std_Measure": "distance",
    "Inspection_VisualInspection": "visual_result",
    "Inspection_InspectElement": "element",
    "Part_CheckGeometry": "validity",
    "CAM_Pocket3D": "pocket_3d",
    "CAM_SimulatorGL": "gl",
    "CAM_Simulator": "native",
    "Mesh_Export": "export_mesh",
    "Mesh_Segmentation": "mesh_segmentation",
    "Points_Export": "export_point_cloud",
    "Reen_Segmentation": "reverse_segmentation",
    "Sketcher_CreateBSpline": "create_b_spline",
    "Sketcher_CreateBSplineByInterpolation": "create_b_spline_by_interpolation",
    "Sketcher_CreatePeriodicBSpline": "create_periodic_b_spline",
    "Sketcher_CreatePeriodicBSplineByInterpolation": (
        "create_periodic_b_spline_by_interpolation"
    ),
    "Sketcher_SelectElementsAssociatedWithConstraints": "select_elements",
    "Sketcher_ViewSketch": "align_view_to_sketch",
    "Sketcher_ViewSection": "section_view",
    "Sketcher_Dimension": "infer_dimension",
    "Sketcher_ConstrainRadiam": "constrain_radius_diameter",
    "Sketcher_ConstrainCoincidentUnified": "constrain_coincident",
    "Sketcher_ConstrainHorVer": "constrain_horizontal_vertical",
    "Sketcher_ToggleDrivingConstraint": "toggle_driving_reference",
    "Sketcher_ToggleActiveConstraint": "toggle_active_inactive",
    "Sketcher_CreateEllipseByCenter": "create_ellipse",
    "Sketcher_CreateEllipseBy3Points": "create3_point_ellipse",
    "Sketcher_CreateRectangle_Center": "create_center_rectangle",
    "Sketcher_LeaveSketch": "leave",
    "Sketcher_Trimming": "trim",
    "Sketcher_Projection": "project_external_geometry",
    "Sketcher_Intersection": "intersect_external_geometry",
    "Sketcher_RemoveAxesAlignment": "remove_axis_alignment",
    "Sketcher_BSplineConvertToNURBS": "convert_to_nurbs",
    "Sketcher_BSplineIncreaseDegree": "increase_bspline_degree",
    "Sketcher_BSplineDecreaseDegree": "decrease_bspline_degree",
    "Sketcher_BSplineIncreaseKnotMultiplicity": (
        "increase_bspline_knot_multiplicity"
    ),
    "Sketcher_BSplineDecreaseKnotMultiplicity": (
        "decrease_bspline_knot_multiplicity"
    ),
    "Sketcher_BSplineInsertKnot": "insert_bspline_knot",
    "Sketcher_BSplineDegree": "bspline_degree",
    "Sketcher_BSplinePolygon": "bspline_control_polygon",
    "Sketcher_BSplineComb": "bspline_curvature_comb",
    "Sketcher_BSplineKnotMultiplicity": "bspline_knot_multiplicity",
    "Sketcher_BSplinePoleWeight": "bspline_pole_weight",
    "Sketcher_SwitchVirtualSpace": "set_virtual_space",
    "Robot_InsertWaypoint": "insert_robot_waypoint",
    "Robot_InsertWaypointPreselect": "insert_position_waypoint",
    "TechDraw_ExportPageDXF": "dxf",
    "TechDraw_ExportPageSVG": "svg",
}

_GROUP_CAPABILITY_FAMILIES = {
    ("model", "Structure"): "model.structure",
    ("model", "Solids"): "model.feature",
    ("model", "Finish"): "model.dressup",
    ("model", "Transform"): "model.transform",
    ("model", "Geometry"): "model.geometry",
    ("model", "Modify"): "model.modify",
    ("model", "Fasteners"): "model.fastener",
    ("model", "Surface"): "model.surface",
    ("model", "Connect"): "component.interface",
    ("sketch.setup", "Sketch"): "sketch.setup",
    ("sketch.edit", "Finish"): "sketch.control",
    ("sketch.edit", "Geometry"): "sketch.geometry",
    ("sketch.edit", "Constraints"): "sketch.constraint",
    ("sketch.edit", "Modify"): "sketch.geometry",
    ("sketch.edit", "B-Spline"): "sketch.geometry",
    ("sketch.edit", "Visual"): "sketch.presentation",
    ("assemble", "Assembly"): "assembly.structure",
    ("assemble", "Joints"): "assembly.joint",
    ("assemble", "Diagnose"): "assembly.diagnose",
    ("assemble", "Fasteners"): "assembly.fastener",
    ("assemble", "Robot"): "robot.setup",
    ("assemble", "Trajectory"): "robot.trajectory",
    ("assemble", "Motion"): "robot.motion",
    ("assemble", "Connect"): "component.interface",
    ("mesh", "Tools"): "mesh.io",
    ("mesh", "Convert"): "mesh.convert",
    ("mesh", "Modify"): "mesh.modify",
    ("mesh", "Boolean"): "mesh.boolean",
    ("mesh", "Cut"): "mesh.cut",
    ("mesh", "Segment"): "mesh.segment",
    ("mesh", "Analyze"): "mesh.inspect",
    ("mesh", "Points"): "mesh.points",
    ("mesh", "Rebuild"): "mesh.rebuild",
    ("mesh", "Approximate"): "mesh.approximate",
    ("analyze", "Model"): "analyze.model",
    ("analyze", "Electromagnetics"): "analyze.electromagnetic",
    ("analyze", "Fluids"): "analyze.fluid",
    ("analyze", "Geometry"): "analyze.geometry",
    ("analyze", "Mechanics"): "analyze.mechanics",
    ("analyze", "Thermal"): "analyze.thermal",
    ("analyze", "Mesh"): "analyze.mesh",
    ("analyze", "Solve"): "analyze.solve",
    ("analyze", "Results"): "analyze.results",
    ("analyze", "Utilities"): "analyze.utility",
    ("manufacture", "Setup"): "manufacture.setup",
    ("manufacture", "Tools"): "manufacture.tool",
    ("manufacture", "Operations"): "manufacture.operation",
    ("manufacture", "Modify"): "manufacture.modify",
    ("manufacture", "Area"): "manufacture.area",
    ("manufacture", "Robot"): "robot.trajectory",
    ("manufacture", "Export"): "robot.export",
    ("drawing", "Pages"): "drawing.page",
    ("drawing", "Views"): "drawing.view",
    ("drawing", "Stacking"): "drawing.stack",
    ("drawing", "Dimensions"): "drawing.dimension",
    ("drawing", "Attributes"): "drawing.attribute",
    ("drawing", "Centerlines"): "drawing.cosmetic",
    ("drawing", "Extend"): "drawing.format",
    ("drawing", "Files"): "drawing.export",
    ("drawing", "Decoration"): "drawing.presentation",
    ("drawing", "Annotation"): "drawing.annotation",
    ("parameters", "Sheet"): "parameters.sheet",
    ("parameters", "Cells"): "parameters.cell",
    ("parameters", "Align"): "parameters.format",
    ("parameters", "Style"): "parameters.format",
}


@dataclass(frozen=True, slots=True)
class NativeActionClassification:
    read: bool
    mutation: bool
    view: bool
    export: bool
    interactive: bool
    parent_only: bool
    human_only: bool

    def __post_init__(self) -> None:
        primary_count = sum(
            (
                self.read,
                self.mutation,
                self.view,
                self.export,
                self.parent_only,
                self.human_only,
            )
        )
        if primary_count != 1:
            raise NativeActionManifestError(
                "Every action must have exactly one primary classification."
            )


@dataclass(frozen=True, slots=True)
class NativeActionPlan:
    command_id: str
    surface_id: str
    group_label: str
    parent_command_id: str | None
    classification: NativeActionClassification
    capability_family: str
    operation_variant: str | None
    prerequisites: tuple[str, ...]
    exact_target_type: str | None
    transaction_behavior: str
    postcondition_checker: str | None
    background_required: bool
    implementation_status: str

    def summary(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "surface_id": self.surface_id,
            "group": self.group_label,
            "parent_command_id": self.parent_command_id,
            "classification": {
                "read": self.classification.read,
                "mutation": self.classification.mutation,
                "view": self.classification.view,
                "export": self.classification.export,
                "interactive": self.classification.interactive,
                "parent_only": self.classification.parent_only,
                "human_only": self.classification.human_only,
            },
            "capability_family": self.capability_family,
            "operation_variant": self.operation_variant,
            "prerequisites": list(self.prerequisites),
            "exact_target_type": self.exact_target_type,
            "transaction_behavior": self.transaction_behavior,
            "postcondition_checker": self.postcondition_checker,
            "background_required": self.background_required,
            "implementation_status": self.implementation_status,
        }


@dataclass(frozen=True, slots=True)
class NativeSurfaceActionInventory:
    """One validated live graph and the actions required by its environment."""

    required_action_ids: tuple[str, ...]
    plans: tuple[NativeActionPlan, ...]


def _operation_variant(command_id: str) -> str:
    override = _OPERATION_VARIANT_OVERRIDES.get(command_id)
    if override:
        return override
    value = re.sub(r"^(?:PartDesign::|[A-Za-z]+_)", "", command_id)
    value = re.sub(r"(?<!^)(?=[A-Z])", "_", value).replace("::", "_")
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _classification(action: RibbonAction) -> NativeActionClassification:
    command_id = action.command_id
    parent_only = action.kind == "composite"
    if parent_only != (command_id in KNOWN_COMPOSITE_COMMAND_IDS):
        raise NativeActionManifestError(
            f"Ribbon action {command_id!r} changed composite/leaf role."
        )
    human_only = command_id in _HUMAN_ONLY_COMMAND_IDS
    view = command_id in _VIEW_COMMAND_IDS
    read = command_id in _READ_COMMAND_IDS
    export = command_id in _EXPORT_COMMAND_IDS
    mutation = not any((parent_only, human_only, view, read, export))
    return NativeActionClassification(
        read=read,
        mutation=mutation,
        view=view,
        export=export,
        interactive=command_id in _INTERACTIVE_COMMAND_IDS,
        parent_only=parent_only,
        human_only=human_only,
    )


def _capability_family(
    surface_id: str,
    group_label: str,
    command_id: str,
) -> str:
    override = _CAPABILITY_OVERRIDES.get(command_id)
    if override:
        return override
    if group_label == "View":
        return "view.presentation"
    if group_label == "Inspect":
        return "inspect.query"
    family = _GROUP_CAPABILITY_FAMILIES.get((surface_id, group_label))
    if family is None:
        raise NativeActionManifestError(
            f"Ribbon group {group_label!r} on {surface_id!r} is unclassified."
        )
    return family


def _plan(
    surface_id: str,
    group_label: str,
    action: RibbonAction,
) -> NativeActionPlan:
    classification = _classification(action)
    if classification.parent_only:
        transaction_behavior = "none"
        status = "parent_only"
    elif classification.human_only:
        transaction_behavior = "human"
        status = "human_only"
    elif action.command_id == "Sketcher_LeaveSketch":
        transaction_behavior = "edit_control"
        status = "planned"
    elif action.command_id in _SESSION_COMMAND_IDS:
        transaction_behavior = "session"
        status = "planned"
    elif classification.view:
        transaction_behavior = "presentation"
        status = "planned"
    elif classification.read:
        transaction_behavior = "none"
        status = "planned"
    elif classification.export:
        transaction_behavior = (
            "background_output"
            if action.command_id in _BACKGROUND_COMMAND_IDS
            else "output"
        )
        status = "planned"
    else:
        transaction_behavior = (
            "background" if action.command_id in _BACKGROUND_COMMAND_IDS else "document"
        )
        status = "planned"
    return NativeActionPlan(
        command_id=action.command_id,
        surface_id=surface_id,
        group_label=group_label,
        parent_command_id=action.parent_command_id,
        classification=classification,
        capability_family=_capability_family(
            surface_id,
            group_label,
            action.command_id,
        ),
        operation_variant=(
            None
            if classification.parent_only or classification.human_only
            else _operation_variant(action.command_id)
        ),
        prerequisites=(),
        exact_target_type=None,
        transaction_behavior=transaction_behavior,
        postcondition_checker=None,
        background_required=action.command_id in _BACKGROUND_COMMAND_IDS,
        implementation_status=status,
    )


def resolve_native_action_inventory(
    surface: RibbonSurface,
) -> NativeSurfaceActionInventory:
    """Validate and classify the sole provider inventory for one live surface."""

    if not isinstance(surface, RibbonSurface):
        raise TypeError("surface must be a RibbonSurface")
    expected = ALLOWED_ACTION_IDS_BY_SURFACE.get(surface.surface_id)
    if expected is None:
        raise NativeActionManifestError(
            f"Unknown Native ribbon surface {surface.surface_id!r}."
        )
    observed = set(surface.command_ids)
    unknown = sorted(observed - expected)
    if unknown:
        raise NativeActionManifestError(
            f"Native ribbon surface {surface.surface_id!r} has unclassified "
            f"actions: {unknown}."
        )
    try:
        variant = validate_surface_variant(
            surface,
            KNOWN_ACTIONS_BY_SURFACE[surface.surface_id],
        )
    except NativeSurfaceVariantError as exc:
        raise NativeActionManifestError(str(exc)) from exc

    plans: list[NativeActionPlan] = []
    for group in surface.groups:
        for action in group.actions:
            plans.append(_plan(surface.surface_id, group.label, action))
            plans.extend(
                _plan(surface.surface_id, group.label, child)
                for child in action.children
            )
    if tuple(plan.command_id for plan in plans) != surface.command_ids:
        raise NativeActionManifestError(
            "Native action classification changed the live ribbon order."
        )

    exact_composites = _EXACT_COMPOSITE_CHILDREN_BY_SURFACE.get(
        surface.surface_id,
        {},
    )
    for group in surface.groups:
        for action in group.actions:
            expected_children = exact_composites.get(action.command_id)
            if expected_children is None:
                continue
            observed_children = tuple(child.command_id for child in action.children)
            if observed_children != expected_children:
                raise NativeActionManifestError(
                    f"Ribbon composite {action.command_id!r} exposes children "
                    f"{observed_children!r}; expected {expected_children!r}."
                )
    return NativeSurfaceActionInventory(
        required_action_ids=(
            variant.command_ids
            if variant is not None
            else KNOWN_ACTIONS_BY_SURFACE[surface.surface_id]
        ),
        plans=tuple(plans),
    )


def classify_native_surface(surface: RibbonSurface) -> tuple[NativeActionPlan, ...]:
    """Classify every live action or reject the entire Native surface."""

    return resolve_native_action_inventory(surface).plans


def planned_provider_capability_families(
    plans: tuple[NativeActionPlan, ...],
) -> tuple[str, ...]:
    """Return deduplicated non-human, non-parent capability families in order."""

    return tuple(
        dict.fromkeys(
            plan.capability_family
            for plan in plans
            if not plan.classification.human_only
            and not plan.classification.parent_only
        )
    )
