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
        "VibeCAD_SectionView",
        "VibeCAD_AnalyzeStudySetup",
        "FEM_MaterialSolid",
        "FEM_MaterialFluid",
        "FEM_Analysis",
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
        "FEM_ConstraintFluidBoundary",
        "FEM_ConstraintInitialFlowVelocity",
        "FEM_ConstraintInitialPressure",
        "FEM_ConstraintFlowVelocity",
        "FEM_ConstraintTransform",
        "FEM_ConstraintPlaneRotation",
        "FEM_ConstraintSectionPrint",
        "FEM_ConstraintFixed",
        "FEM_ConstraintDisplacement",
        "FEM_ConstraintForce",
        "FEM_ConstraintPressure",
        "FEM_ConstraintRigidBody",
        "FEM_ConstraintContact",
        "FEM_ConstraintTie",
        "FEM_ConstraintSpring",
        "FEM_ConstraintCentrif",
        "FEM_ConstraintSelfWeight",
        "FEM_ConstraintTemperature",
        "FEM_ConstraintHeatflux",
        "FEM_ConstraintBodyHeatSource",
        "FEM_ConstraintInitialTemperature",
        "FEM_MeshGmshFromShape",
        "FEM_MeshRegion",
        "FEM_MeshGMSHRefinement",
        "FEM_MeshDistance",
        "FEM_MeshBoundaryLayer",
        "FEM_MeshShape",
        "FEM_MeshManipulate",
        "FEM_MeshAdvanced",
        "FEM_MeshTransfiniteCurve",
        "FEM_MeshTransfiniteSurface",
        "FEM_MeshTransfiniteVolume",
        "FEM_MeshNetgenFromShape",
        "FEM_MeshGroup",
        "FEM_CreateElementsSet",
        "FEM_FEMMesh2Mesh",
        "FEM_CompSolvers",
        "FEM_SolverCalculiX",
        "FEM_SolverElmer",
        "FEM_SolverMystran",
        "FEM_SolverZ88",
        "FEM_SolverOpenFOAM",
        "FEM_SolverControl",
        "FEM_SolverRun",
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
        "FEM_ResultShow",
        "FEM_PostPipelineFromResult",
        "FEM_PostFilterWarp",
        "FEM_PostFilterContours",
        "FEM_PostApplyChanges",
        "FEM_PostBranchFilter",
        "FEM_PostFilterClipScalar",
        "FEM_PostFilterCutFunction",
        "FEM_PostFilterClipRegion",
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
        "FEM_ResultsPurge",
        "FEM_Examples",
        "FEM_ClippingPlaneAdd",
        "FEM_ClippingPlaneRemoveAll",
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
        "VibeCAD_SectionView",
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
        "VibeCAD_SectionView",
        "TechDraw_PageDefault",
        "TechDraw_PageTemplate",
        "TechDraw_FillTemplateFields",
        "TechDraw_RedrawPage",
        "TechDraw_PrintAll",
        "TechDraw_View",
        "TechDraw_ProjectionGroup",
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
        "VibeCAD_SectionView",
        "CAM_Job",
        "CAM_FollowUpSetup",
        "CAM_PropertyBag",
        "CAM_Sanity",
        "CAM_PostTools",
        "CAM_Post",
        "CAM_PostSelected",
        "CAM_SimTools",
        "CAM_SimulatorGL",
        "CAM_Simulator",
        "CAM_RetainSimulationResult",
        "CAM_Inspect",
        "CAM_SelectLoop",
        "CAM_OpActiveToggle",
        "CAM_ToolBitDock",
        "CAM_Comment",
        "CAM_Stop",
        "CAM_Custom",
        "CAM_Probe",
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
        "VibeCAD_SectionView",
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
        "VibeCAD_SectionView",
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
        "Part_MakeFace",
        "Part_RuledSurface",
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
        "VibeCAD_SectionView",
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
        "VibeCAD_SectionView",
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
        "VibeCAD_SectionView",
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
    "aero": (
        "Std_ViewFitAll",
        "Std_ViewIsometric",
        "VibeCAD_ToggleGrid",
        "VibeCAD_SectionView",
        "VibeCADAero_Analyze",
        "VibeCADAero_Section",
        "VibeCADAero_VLM",
        "VibeCADAero_ExportJSBSim",
        "VibeCADAero_Report",
        "VibeCADAero_ProposeRepairs",
        "VibeCADAero_ApplyRepairs",
        "VibeCADAero_FlightCard",
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
    "model": (
        "VibeCADAero_Analyze",
        "VibeCADAero_Section",
        "VibeCADAero_VLM",
        "VibeCADAero_ExportJSBSim",
        "VibeCADAero_Report",
        "VibeCADAero_ProposeRepairs",
        "VibeCADAero_ApplyRepairs",
        "VibeCADAero_FlightCard",
    ),
    "parameters": (),
    "aero": (),
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
        "VibeCAD_AnalyzeStudySetup",
        "CAM_RetainSimulationResult",
        "FEM_Examples",
        "Sketcher_CancelSketch",
    }
)

_VIEW_COMMAND_IDS = frozenset(
    {
        "Std_ViewFitAll",
        "Std_ViewIsometric",
        "VibeCAD_ToggleGrid",
        "VibeCAD_SectionView",
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
        "FEM_PostApplyChanges",
        "CAM_Camotics",
        "CAM_SimulatorGL",
        "TechDraw_ToggleFrame",
        "TechDraw_ShowAll",
    }
)

_READ_COMMAND_IDS = frozenset(
    {
        "Std_Measure",
        "Std_MassProperties",
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
        "Mesh_CurvatureInfo",
        "Mesh_EvaluateSolid",
        "Mesh_BoundingBox",
        "FEM_PostFilterLinearizedStresses",
        "TechDraw_ExtensionSelectLineAttributes",
        "VibeCADAero_FlightCard",
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
        "VibeCADAero_ExportJSBSim",
    }
)

_BACKGROUND_COMMAND_IDS = frozenset(
    {
        "Inspection_VisualInspection",
        "Spreadsheet_Import",
        "Spreadsheet_Export",
        "Mesh_Import",
        "Mesh_Export",
        "Mesh_FromPartShape",
        "MeshPart_ShapeFromMesh",
        "Mesh_Union",
        "Mesh_Intersection",
        "Mesh_Difference",
        "Mesh_HarmonizeNormals",
        "Mesh_FlipNormals",
        "Mesh_FillupHoles",
        "Mesh_FillInteractiveHole",
        "Mesh_AddFacet",
        "Mesh_RemoveComponents",
        "Mesh_Smoothing",
        "CAM_FollowUpSetup",
        "CAM_Profile",
        "CAM_Pocket_Shape",
        "CAM_Pocket3D",
        "CAM_Surface",
        "CAM_Waterline",
        "CAM_RotarySurface",
        "CAM_MillFacing",
        "CAM_Helix",
        "CAM_Adaptive",
        "CAM_Slot",
        "CAM_Drilling",
        "CAM_ThreadMilling",
        "CAM_Engrave",
        "CAM_Deburr",
        "CAM_Vcarve",
        "Mesh_RemeshGmsh",
        "Mesh_Decimating",
        "Mesh_Scale",
        "Mesh_VertexCurvature",
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
        "FEM_SolverRun",
        "CAM_Camotics",
        "CAM_SimulatorGL",
        "CAM_Simulator",
        "CAM_RetainSimulationResult",
        "CAM_DressupZCorrect",
        "CAM_Post",
        "CAM_PostSelected",
        "TechDraw_RedrawPage",
        "TechDraw_View",
        "TechDraw_ProjectionGroup",
        "TechDraw_BrokenView",
        "TechDraw_SectionView",
        "TechDraw_ComplexSection",
        "TechDraw_DetailView",
        "TechDraw_DraftView",
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
            "CAM_Camotics",
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
    "VibeCAD_SectionView": "view.control",
    "Std_Measure": "inspect.query",
    "Std_MassProperties": "inspect.query",
    "Inspection_VisualInspection": "inspect.compare",
    "Inspection_InspectElement": "inspect.query",
    "Part_CheckGeometry": "inspect.query",
    "PartDesign_Hole": "model.hole",
    "PartDesign_DesignExtrude": "model.extrude",
    "PartDesign_DesignRevolve": "model.revolve",
    "PartDesign_DesignLoft": "model.loft",
    "PartDesign_DesignSweep": "model.sweep",
    "PartDesign_DesignHelix": "model.helix",
    "PartDesign_Scale": "model.transform",
    "Part_Primitives": "model.part",
    "Part_Builder": "model.part",
    "Part_MakeFace": "model.part",
    "Part_RuledSurface": "model.part",
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
    "Assembly_CreateAssembly": "assembly.create",
    "Assembly_InsertLink": "assembly.insert",
    "Assembly_InsertNewPart": "assembly.new_part",
    "Assembly_SolveAssembly": "assembly.solve",
    "Assembly_CreateView": "assembly.exploded_view",
    "Assembly_CreateSimulation": "assembly.motion_study",
    "AssemblyContextMakeFlexible": "assembly.rigidity",
    "AssemblyContextMakeRigid": "assembly.rigidity",
    "Assembly_ToggleGrounded": "assembly.ground",
    "Assembly_CreateBom": "assembly.bom",
    "Assembly_CreateJointDistance": "assembly.relation",
    "Assembly_CreateJointParallel": "assembly.relation",
    "Assembly_CreateJointPerpendicular": "assembly.relation",
    "Assembly_CreateJointAngle": "assembly.relation",
    "Assembly_CreateJointRackPinion": "assembly.rack_pinion",
    "Assembly_CreateJointScrew": "assembly.screw",
    "Assembly_CreateJointBelt": "assembly.belt",
    "Assembly_CreateJointGears": "assembly.gears",
    "Assembly_SelectJointsOfComponent": "assembly.component_joints",
    "FEM_Analysis": "analyze.model",
    "FEM_MaterialSolid": "analyze.model",
    "FEM_MaterialFluid": "analyze.model",
    "FEM_MaterialMechanicalNonlinear": "analyze.model",
    "FEM_MaterialReinforced": "analyze.model",
    "FEM_MaterialEditor": "analyze.model",
    "FEM_ElementGeometry1D": "analyze.geometry",
    "FEM_ElementRotation1D": "analyze.geometry",
    "FEM_ElementGeometry2D": "analyze.geometry",
    "FEM_ElementFluid1D": "analyze.geometry",
    "FEM_ConstraintElectromagnetic": "analyze.electromagnetic",
    "FEM_ConstraintCurrentDensity": "analyze.electromagnetic",
    "FEM_ConstraintMagnetization": "analyze.electromagnetic",
    "FEM_ConstraintElectricChargeDensity": "analyze.electromagnetic",
    "FEM_ConstraintInitialFlowVelocity": "analyze.fluid",
    "FEM_ConstraintInitialPressure": "analyze.fluid",
    "FEM_ConstraintFlowVelocity": "analyze.fluid",
    "FEM_ConstraintFluidBoundary": "analyze.fluid",
    "FEM_ConstraintPlaneRotation": "analyze.geometrical",
    "FEM_ConstraintSectionPrint": "analyze.geometrical",
    "FEM_ConstraintTransform": "analyze.geometrical",
    "FEM_ConstraintFixed": "analyze.support",
    "FEM_ConstraintRigidBody": "analyze.support",
    "FEM_ConstraintDisplacement": "analyze.support",
    "FEM_ConstraintSpring": "analyze.support",
    "FEM_ConstraintContact": "analyze.connection",
    "FEM_ConstraintTie": "analyze.connection",
    "FEM_ConstraintForce": "analyze.load",
    "FEM_ConstraintPressure": "analyze.load",
    "FEM_ConstraintCentrif": "analyze.load",
    "FEM_ConstraintSelfWeight": "analyze.load",
    "FEM_ConstraintInitialTemperature": "analyze.thermal",
    "FEM_ConstraintHeatflux": "analyze.thermal",
    "FEM_ConstraintTemperature": "analyze.thermal",
    "FEM_ConstraintBodyHeatSource": "analyze.thermal",
    "FEM_MeshNetgenFromShape": "analyze.mesh",
    "FEM_MeshGmshFromShape": "analyze.mesh",
    "FEM_MeshRegion": "analyze.mesh_refinement",
    "FEM_MeshGroup": "analyze.mesh_refinement",
    "FEM_MeshDistance": "analyze.mesh_refinement",
    "FEM_MeshBoundaryLayer": "analyze.mesh_refinement",
    "FEM_MeshShape": "analyze.mesh_refinement",
    "FEM_MeshManipulate": "analyze.mesh_field",
    "FEM_MeshAdvanced": "analyze.mesh_field",
    "FEM_CreateElementsSet": "analyze.mesh_output",
    "FEM_FEMMesh2Mesh": "analyze.mesh_output",
    "FEM_MeshTransfiniteCurve": "analyze.structured_mesh",
    "FEM_MeshTransfiniteSurface": "analyze.structured_mesh",
    "FEM_MeshTransfiniteVolume": "analyze.structured_mesh",
    "FEM_SolverCalculiX": "analyze.solver",
    "FEM_SolverElmer": "analyze.solver",
    "FEM_SolverMystran": "analyze.solver",
    "FEM_SolverZ88": "analyze.solver",
    "FEM_SolverOpenFOAM": "analyze.solver",
    "FEM_SolverControl": "analyze.solver_control",
    "FEM_SolverRun": "analyze.solver_execution",
    "FEM_EquationElasticity": "analyze.equation",
    "FEM_EquationDeformation": "analyze.equation",
    "FEM_EquationElectrostatic": "analyze.equation",
    "FEM_EquationElectricforce": "analyze.equation",
    "FEM_EquationMagnetodynamic": "analyze.equation",
    "FEM_EquationMagnetodynamic2D": "analyze.equation",
    "FEM_EquationStaticCurrent": "analyze.equation",
    "FEM_EquationFlow": "analyze.equation",
    "FEM_EquationFlux": "analyze.equation",
    "FEM_EquationHeat": "analyze.equation",
    "FEM_ResultShow": "analyze.presentation",
    "FEM_PostApplyChanges": "analyze.presentation",
    "FEM_ClippingPlaneAdd": "analyze.presentation",
    "FEM_ClippingPlaneRemoveAll": "analyze.presentation",
    "FEM_PostPipelineFromResult": "analyze.post",
    "FEM_PostBranchFilter": "analyze.post",
    "FEM_PostFilterWarp": "analyze.post",
    "FEM_PostFilterClipScalar": "analyze.post",
    "FEM_PostFilterCutFunction": "analyze.post",
    "FEM_PostFilterClipRegion": "analyze.post",
    "FEM_PostFilterContours": "analyze.post",
    "FEM_PostFilterGlyph": "analyze.post",
    "FEM_PostFilterDataAlongLine": "analyze.post",
    "FEM_PostFilterLinearizedStresses": "analyze.inspect",
    "FEM_PostFilterDataAtPoint": "analyze.post",
    "FEM_PostFilterCalculator": "analyze.post",
    "FEM_PostCreateFunctions": "analyze.post_function",
    "FEM_PostCreateFunctionPlane": "analyze.post_function",
    "FEM_PostCreateFunctionSphere": "analyze.post_function",
    "FEM_PostCreateFunctionCylinder": "analyze.post_function",
    "FEM_PostCreateFunctionBox": "analyze.post_function",
    "FEM_PostVisualizationLineplot": "analyze.visualization",
    "FEM_PostVisualizationHistogram": "analyze.visualization",
    "FEM_PostVisualizationTable": "analyze.visualization",
    "CAM_Sanity": "manufacture.validate",
    "CAM_Inspect": "manufacture.toolpath",
    "CAM_SelectLoop": "manufacture.loop",
    "CAM_Area": "manufacture.area",
    "CAM_Area_Workplane": "manufacture.area",
    "CAM_Job": "manufacture.job",
    "CAM_FollowUpSetup": "manufacture.follow_up_setup",
    "CAM_PropertyBag": "manufacture.property_bag",
    "CAM_ToolBitDock": "manufacture.add_tool",
    "CAM_Comment": "manufacture.program",
    "CAM_Stop": "manufacture.program",
    "CAM_Custom": "manufacture.program",
    "CAM_Probe": "manufacture.probe",
    "CAM_Profile": "manufacture.profile",
    "CAM_Pocket_Shape": "manufacture.pocket",
    "CAM_MillFacing": "manufacture.face",
    "CAM_Drilling": "manufacture.drill",
    "CAM_Pocket3D": "manufacture.pocket_3d",
    "CAM_Surface": "manufacture.surface",
    "CAM_Waterline": "manufacture.waterline",
    "CAM_RotarySurface": "manufacture.rotary_surface",
    "CAM_Helix": "manufacture.helix",
    "CAM_Adaptive": "manufacture.adaptive",
    "CAM_Slot": "manufacture.slot",
    "CAM_ThreadMilling": "manufacture.thread_mill",
    "CAM_Engrave": "manufacture.engrave",
    "CAM_Deburr": "manufacture.deburr",
    "CAM_Vcarve": "manufacture.v_carve",
    "CAM_OpActiveToggle": "manufacture.operations",
    "CAM_OperationCopy": "manufacture.operations",
    "CAM_Array": "manufacture.array",
    "CAM_SimpleCopy": "manufacture.copy_path",
    "CAM_DressupArray": "manufacture.dressup",
    "CAM_DressupAxisMap": "manufacture.dressup",
    "CAM_DressupPathBoundary": "manufacture.dressup",
    "CAM_DressupDogbone": "manufacture.dressup",
    "CAM_DressupDragKnife": "manufacture.dressup",
    "CAM_DressupLeadInOut": "manufacture.dressup",
    "CAM_DressupMirror": "manufacture.dressup",
    "CAM_DressupRampEntry": "manufacture.dressup",
    "CAM_DressupTag": "manufacture.dressup",
    "CAM_DressupZCorrect": "manufacture.dressup",
    "CAM_Camotics": "manufacture.camotics",
    "CAM_SimulatorGL": "manufacture.simulation",
    "CAM_Simulator": "manufacture.simulation_result",
    "CAM_Post": "manufacture.post_job",
    "CAM_PostSelected": "manufacture.post_selected",
    "Mesh_Export": "mesh.export",
    "Points_Export": "mesh.export",
    "Mesh_FromPartShape": "mesh.from_shape",
    "MeshPart_ShapeFromMesh": "mesh.to_shape",
    "MeshPart_CurveOnMesh": "mesh.curve_on_mesh",
    "Mesh_HarmonizeNormals": "mesh.repair",
    "Mesh_FlipNormals": "mesh.modify",
    "Mesh_FillupHoles": "mesh.fill_holes",
    "Mesh_FillInteractiveHole": "mesh.modify",
    "Mesh_AddFacet": "mesh.modify",
    "Mesh_RemoveComponents": "mesh.modify",
    "Mesh_Smoothing": "mesh.smooth",
    "Mesh_RemeshGmsh": "mesh.remesh",
    "Mesh_Decimating": "mesh.decimate",
    "Mesh_Scale": "mesh.scale",
    "Mesh_Merge": "mesh.combine",
    "Mesh_SplitComponents": "mesh.separate",
    "Mesh_VertexCurvature": "mesh.curvature",
    "Spreadsheet_Export": "parameters.export",
    "Spreadsheet_CreateSheet": "parameters.sheet",
    "Spreadsheet_Import": "parameters.sheet",
    "Spreadsheet_MergeCells": "parameters.cell",
    "Spreadsheet_SplitCell": "parameters.cell",
    "Spreadsheet_CellProperties": "parameters.cell",
    "Spreadsheet_SetAlias": "parameters.cell",
    "Spreadsheet_AlignLeft": "parameters.format",
    "Spreadsheet_AlignCenter": "parameters.format",
    "Spreadsheet_AlignRight": "parameters.format",
    "Spreadsheet_AlignTop": "parameters.format",
    "Spreadsheet_AlignVCenter": "parameters.format",
    "Spreadsheet_AlignBottom": "parameters.format",
    "Spreadsheet_StyleBold": "parameters.format",
    "Spreadsheet_StyleItalic": "parameters.format",
    "Spreadsheet_StyleUnderline": "parameters.format",
    "Sketcher_NewSketch": "model.sketch",
    "Sketcher_EditSketch": "sketch.open",
    "Sketcher_ValidateSketch": "sketch.validate",
    "Sketcher_LeaveSketch": "sketch.control",
    "Sketcher_CancelSketch": "sketch.control",
    "Sketcher_SelectConstraints": "sketch.inspect",
    "Sketcher_SelectElementsAssociatedWithConstraints": "sketch.inspect",
    "Sketcher_ViewSketch": "sketch.presentation",
    "Sketcher_ViewSection": "sketch.presentation",
    "Sketcher_RestoreInternalAlignmentGeometry": "sketch.edit",
    "Sketcher_Trimming": "sketch.trim",
    "Sketcher_Split": "sketch.split",
    "Sketcher_Extend": "sketch.extend",
    "Sketcher_SwitchVirtualSpace": "sketch.edit",
    "TechDraw_ExtensionSelectLineAttributes": "drawing.line_defaults",
    "TechDraw_ExtensionChangeLineAttributes": "drawing.line_attributes",
    "TechDraw_DecorateLine": "drawing.line_attributes",
    "TechDraw_ExtensionExtendLine": "drawing.line_length",
    "TechDraw_ExtensionShortenLine": "drawing.line_length",
    "TechDraw_ExtensionLockUnlockView": "drawing.set_view_locks",
    "TechDraw_ExtensionPositionSectionView": "drawing.section_position",
    "TechDraw_Dimension": "drawing.dimension_infer",
    "TechDraw_LengthDimension": "drawing.linear_dimension",
    "TechDraw_HorizontalDimension": "drawing.linear_dimension",
    "TechDraw_VerticalDimension": "drawing.linear_dimension",
    "TechDraw_RadiusDimension": "drawing.radial_dimension",
    "TechDraw_DiameterDimension": "drawing.radial_dimension",
    "TechDraw_AngleDimension": "drawing.angle_dimension",
    "TechDraw_3PtAngleDimension": "drawing.three_point_angle",
    "TechDraw_AreaDimension": "drawing.area_dimension",
    "TechDraw_HorizontalExtentDimension": "drawing.view_extent_dimension",
    "TechDraw_VerticalExtentDimension": "drawing.view_extent_dimension",
    "TechDraw_AxoLengthDimension": "drawing.axonometric_dimension",
    "TechDraw_ExtensionCreateHorizChainDimension": "drawing.dimension_series",
    "TechDraw_ExtensionCreateVertChainDimension": "drawing.dimension_series",
    "TechDraw_ExtensionCreateObliqueChainDimension": "drawing.dimension_series",
    "TechDraw_ExtensionCreateHorizCoordDimension": "drawing.dimension_series",
    "TechDraw_ExtensionCreateVertCoordDimension": "drawing.dimension_series",
    "TechDraw_ExtensionCreateObliqueCoordDimension": "drawing.dimension_series",
    "TechDraw_ExtensionAreaAnnotation": "drawing.area_annotation",
    "TechDraw_ExtensionArcLengthAnnotation": "drawing.arc_length_annotation",
    "TechDraw_ExtensionCustomizeFormat": "drawing.format",
    "TechDraw_HoleShaftFit": "drawing.format",
    "TechDraw_WeldSymbol": "drawing.symbol",
    "TechDraw_SurfaceFinishSymbols": "drawing.symbol",
    "TechDraw_ExtensionInsertDiameter": "drawing.dimension_text",
    "TechDraw_ExtensionInsertSquare": "drawing.dimension_text",
    "TechDraw_ExtensionInsertRepetition": "drawing.dimension_text",
    "TechDraw_ExtensionRemovePrefixChar": "drawing.dimension_text",
    "TechDraw_ExtensionIncreaseDecimal": "drawing.dimension_text",
    "TechDraw_ExtensionDecreaseDecimal": "drawing.dimension_text",
    "TechDraw_ExtensionCircleCenterLines": "drawing.circle_center_lines",
    "TechDraw_ExtensionHoleCircle": "drawing.bolt_circle_center_lines",
    "TechDraw_ExtensionThreadHoleSide": "drawing.thread_representation",
    "TechDraw_ExtensionThreadHoleBottom": "drawing.thread_representation",
    "TechDraw_ExtensionThreadBoltSide": "drawing.thread_representation",
    "TechDraw_ExtensionThreadBoltBottom": "drawing.thread_representation",
    "TechDraw_ExtensionVertexAtIntersection": "drawing.cosmetic_vertex",
    "TechDraw_CommandAddOffsetVertex": "drawing.cosmetic_vertex",
    "TechDraw_CosmeticVertex": "drawing.cosmetic_vertex",
    "TechDraw_Midpoints": "drawing.cosmetic_vertex",
    "TechDraw_Quadrants": "drawing.cosmetic_vertex",
    "TechDraw_FaceCenterLine": "drawing.centerline",
    "TechDraw_2LineCenterLine": "drawing.centerline",
    "TechDraw_2PointCenterLine": "drawing.centerline",
    "TechDraw_2PointCosmeticLine": "drawing.cosmetic_line",
    "TechDraw_CosmeticCircle": "drawing.cosmetic_curve",
    "TechDraw_ExtensionDrawCosmCircle": "drawing.cosmetic_curve",
    "TechDraw_ExtensionDrawCosmCircle3Points": "drawing.cosmetic_curve",
    "TechDraw_ExtensionDrawCosmArc": "drawing.cosmetic_curve",
    "TechDraw_ExtensionLineParallel": "drawing.cosmetic_line",
    "TechDraw_ExtensionLinePerpendicular": "drawing.cosmetic_line",
    "TechDraw_PageDefault": "drawing.create_page",
    "TechDraw_PageTemplate": "drawing.choose_page_template",
    "TechDraw_FillTemplateFields": "drawing.template_fields",
    "TechDraw_RedrawPage": "drawing.redraw_page",
    "TechDraw_View": "drawing.standard_view",
    "TechDraw_ProjectionGroup": "drawing.projection_group",
    "TechDraw_BrokenView": "drawing.broken_view",
    "TechDraw_SectionView": "drawing.section_view",
    "TechDraw_ComplexSection": "drawing.complex_section",
    "TechDraw_DetailView": "drawing.detail_view",
    "TechDraw_DraftView": "drawing.draft_source_view",
    "TechDraw_ClipGroup": "drawing.clip_group",
    "TechDraw_StackTop": "drawing.stack",
    "TechDraw_StackBottom": "drawing.stack",
    "TechDraw_StackUp": "drawing.stack",
    "TechDraw_StackDown": "drawing.stack",
    "TechDraw_ActiveView": "drawing.active_view",
    "TechDraw_ExtensionCreateHorizChamferDimension": "drawing.chamfer_dimension",
    "TechDraw_ExtensionCreateVertChamferDimension": "drawing.chamfer_dimension",
    "TechDraw_ExtensionCreateLengthArc": "drawing.arc_length_dimension",
    "TechDraw_Balloon": "drawing.balloon",
    "TechDraw_PrintAll": "drawing.export",
    "TechDraw_ToggleFrame": "drawing.page_frames",
    "TechDraw_Hatch": "drawing.hatch",
    "TechDraw_GeometricHatch": "drawing.hatch",
    "TechDraw_RichTextAnnotation": "drawing.note",
    "TechDraw_LeaderLine": "drawing.leader_line",
    "TechDraw_ShowAll": "drawing.hidden_edges",
    "VibeCAD_PublishInterface": "component.interface",
    "Robot_Simulate": "robot.motion",
    "Robot_Edge2Trac": "robot.edge_path",
    "Robot_TrajectoryDressUp": "robot.set_path_motion",
    "Robot_TrajectoryCompound": "robot.path_sequence",
}

_CAPABILITY_OVERRIDES.update(
    {
        command_id: "sketch.draw_line"
        for command_id in (
            "Sketcher_CreatePolyline",
            "Sketcher_CreateLine",
        )
    }
)
_CAPABILITY_OVERRIDES["Sketcher_CreatePoint"] = "sketch.draw_point"
_CAPABILITY_OVERRIDES["TechDraw_DimensionRepair"] = "drawing.dimension_repair"
_CAPABILITY_OVERRIDES["VibeCADAero_ExportJSBSim"] = "aero.export"
_CAPABILITY_OVERRIDES["VibeCADAero_FlightCard"] = "aero.inspect"
_CAPABILITY_OVERRIDES.update(
    {
        command_id: "sketch.draw_arc"
        for command_id in ("Sketcher_CreateArc",)
    }
)
_CAPABILITY_OVERRIDES.update(
    {
        command_id: "sketch.draw_conic_arc"
        for command_id in (
            "Sketcher_CreateArcOfEllipse",
            "Sketcher_CreateArcOfHyperbola",
            "Sketcher_CreateArcOfParabola",
        )
    }
)
_CAPABILITY_OVERRIDES["Sketcher_Create3PointArc"] = "sketch.draw_three_point_arc"
_CAPABILITY_OVERRIDES.update(
    {
        command_id: "model.primitive"
        for command_id in (
            "PartDesign::DesignBox",
            "PartDesign::DesignCylinder",
            "PartDesign::DesignSphere",
            "PartDesign::DesignCone",
            "PartDesign::DesignEllipsoid",
            "PartDesign::DesignTorus",
            "PartDesign::DesignPrism",
            "PartDesign::DesignWedge",
            "PartDesign::DesignTube",
        )
    }
)
_CAPABILITY_OVERRIDES.update(
    {
        command_id: "sketch.draw_circle"
        for command_id in (
            "Sketcher_CreateCircle",
            "Sketcher_Create3PointCircle",
        )
    }
)
_CAPABILITY_OVERRIDES.update(
    {
        "Sketcher_CreateEllipseByCenter": "sketch.draw_ellipse",
        "Sketcher_CreateEllipseBy3Points": "sketch.draw_three_point_ellipse",
    }
)
_CAPABILITY_OVERRIDES.update(
    {
        "Sketcher_CreateRectangle": "sketch.draw_rectangle",
        "Sketcher_CreateRectangle_Center": "sketch.draw_center_rectangle",
        "Sketcher_CreateOblong": "sketch.draw_rounded_rectangle",
    }
)
_CAPABILITY_OVERRIDES.update(
    {
        command_id: "sketch.draw_polygon"
        for command_id in (
            "Sketcher_CreateTriangle",
            "Sketcher_CreateSquare",
            "Sketcher_CreatePentagon",
            "Sketcher_CreateHexagon",
            "Sketcher_CreateHeptagon",
            "Sketcher_CreateOctagon",
            "Sketcher_CreateRegularPolygon",
        )
    }
)
_CAPABILITY_OVERRIDES.update(
    {
        command_id: "sketch.draw_slot"
        for command_id in (
            "Sketcher_CreateSlot",
            "Sketcher_CreateArcSlot",
        )
    }
)
_CAPABILITY_OVERRIDES.update(
    {
        command_id: "sketch.draw_spline"
        for command_id in (
            "Sketcher_CreateBSpline",
            "Sketcher_CreatePeriodicBSpline",
            "Sketcher_CreateBSplineByInterpolation",
            "Sketcher_CreatePeriodicBSplineByInterpolation",
        )
    }
)
_CAPABILITY_OVERRIDES["Sketcher_CreateText"] = "sketch.draw_text"
_CAPABILITY_OVERRIDES.update(
    {
        command_id: "sketch.dimension"
        for command_id in (
            "Sketcher_Dimension",
            "Sketcher_ConstrainDistanceX",
            "Sketcher_ConstrainDistanceY",
            "Sketcher_ConstrainDistance",
            "Sketcher_ConstrainRadiam",
            "Sketcher_ConstrainRadius",
            "Sketcher_ConstrainDiameter",
            "Sketcher_ConstrainAngle",
            "Sketcher_ConstrainLock",
        )
    }
)
_CAPABILITY_OVERRIDES.update(
    {
        command_id: "sketch.constrain"
        for command_id in (
            "Sketcher_ConstrainHorVer",
            "Sketcher_ConstrainHorizontal",
            "Sketcher_ConstrainVertical",
            "Sketcher_ConstrainParallel",
            "Sketcher_ConstrainEqual",
            "Sketcher_ConstrainBlock",
            "Sketcher_ConstrainGroup",
        )
    }
)
_CAPABILITY_OVERRIDES.update(
    {
        "Sketcher_ConstrainCoincidentUnified": "sketch.coincident",
        "Sketcher_ConstrainPerpendicular": "sketch.perpendicular",
        "Sketcher_ConstrainTangent": "sketch.tangent",
        "Sketcher_ConstrainSymmetric": "sketch.symmetric",
    }
)
_CAPABILITY_OVERRIDES.update(
    {
        command_id: "sketch.transform"
        for command_id in (
            "Sketcher_Translate",
            "Sketcher_Rotate",
            "Sketcher_Scale",
            "Sketcher_Offset",
            "Sketcher_Symmetry",
        )
    }
)
_CAPABILITY_OVERRIDES.update(
    {
        command_id: "sketch.edit"
        for command_id in (
            "Sketcher_ToggleConstruction",
            "Sketcher_ToggleDrivingConstraint",
            "Sketcher_ToggleActiveConstraint",
            "Sketcher_RemoveAxesAlignment",
            "Sketcher_BSplineConvertToNURBS",
            "Sketcher_BSplineIncreaseDegree",
            "Sketcher_BSplineDecreaseDegree",
            "Sketcher_BSplineIncreaseKnotMultiplicity",
            "Sketcher_BSplineDecreaseKnotMultiplicity",
            "Sketcher_BSplineInsertKnot",
            "Sketcher_JoinCurves",
        )
    }
)
_CAPABILITY_OVERRIDES.update(
    {
        "Sketcher_CreateFillet": "sketch.fillet",
        "Sketcher_CreateChamfer": "sketch.chamfer",
        "Sketcher_Projection": "sketch.external",
        "Sketcher_Intersection": "sketch.external",
        "Sketcher_CarbonCopy": "sketch.external",
    }
)

_OPERATION_VARIANT_OVERRIDES = {
    "Mesh_HarmonizeNormals": "repair",
    "VibeCADAero_VLM": "vlm",
    "VibeCADAero_ExportJSBSim": "export_jsbsim",
    "VibeCADAero_ProposeRepairs": "propose_repairs",
    "VibeCADAero_ApplyRepairs": "apply_repairs",
    "VibeCADAero_FlightCard": "flight_card",
    "Sketcher_CreateOblong": "create_rounded_rectangle",
    "Sketcher_NewSketch": "create_on_base_plane",
    "Spreadsheet_CreateSheet": "create",
    "Spreadsheet_Import": "import_csv",
    "Spreadsheet_Export": "export_csv",
    "Spreadsheet_MergeCells": "merge",
    "Spreadsheet_SplitCell": "split",
    "Spreadsheet_CellProperties": "set_properties",
    "Spreadsheet_SetAlias": "set_alias",
    "Spreadsheet_AlignLeft": "align_left",
    "Spreadsheet_AlignCenter": "align_center",
    "Spreadsheet_AlignRight": "align_right",
    "Spreadsheet_AlignTop": "align_top",
    "Spreadsheet_AlignVCenter": "align_vertical_center",
    "Spreadsheet_AlignBottom": "align_bottom",
    "Spreadsheet_StyleBold": "set_bold",
    "Spreadsheet_StyleItalic": "set_italic",
    "Spreadsheet_StyleUnderline": "set_underline",
    "FEM_ResultsPurge": "purge",
    "FEM_ResultShow": "show_result",
    "FEM_PostApplyChanges": "set_post_auto_recompute",
    "FEM_CreateElementsSet": "erase_elements",
    "FEM_FEMMesh2Mesh": "convert_surface",
    "FEM_ClippingPlaneAdd": "add_clipping_plane",
    "FEM_ClippingPlaneRemoveAll": "remove_all_clipping_planes",
    "FEM_PostPipelineFromResult": "create_pipeline",
    "FEM_PostBranchFilter": "create_branch",
    "FEM_PostFilterWarp": "create_warp",
    "FEM_PostFilterClipScalar": "create_scalar_clip",
    "FEM_PostFilterCutFunction": "create_cut",
    "FEM_PostFilterClipRegion": "create_region_clip",
    "FEM_PostFilterContours": "create_contours",
    "FEM_PostFilterGlyph": "create_glyphs",
    "FEM_PostFilterDataAlongLine": "create_line_sample",
    "FEM_PostFilterLinearizedStresses": "linearized_stress",
    "FEM_PostFilterDataAtPoint": "create_point_sample",
    "FEM_PostFilterCalculator": "create_calculated_field",
    "FEM_PostCreateFunctions": "create_plane",
    "FEM_PostCreateFunctionPlane": "create_plane",
    "FEM_PostCreateFunctionSphere": "create_sphere",
    "FEM_PostCreateFunctionCylinder": "create_cylinder",
    "FEM_PostCreateFunctionBox": "create_box",
    "FEM_PostVisualizationLineplot": "create_line_plot",
    "FEM_PostVisualizationHistogram": "create_histogram",
    "FEM_PostVisualizationTable": "create_table",
    "CAM_Sanity": "validate_job",
    "CAM_Inspect": "inspect_toolpath",
    "CAM_SelectLoop": "detect_loop",
    "CAM_Area": "create",
    "CAM_Area_Workplane": "set_workplane",
    "CAM_Job": "create_job",
    "CAM_FollowUpSetup": "create",
    "CAM_PropertyBag": "create",
    "CAM_Comment": "comment",
    "CAM_Stop": "stop",
    "CAM_Custom": "custom",
    "CAM_Probe": "create_grid",
    "CAM_ToolBitDock": "create_controller",
    "Assembly_InsertLink": "insert_component",
    "Assembly_InsertNewPart": "create_part",
    "Assembly_CreateBom": "create",
    "Assembly_CreateJointBall": "create",
    "Assembly_CreateJointBelt": "belt",
    "Assembly_CreateJointCylindrical": "create",
    "Assembly_CreateJointAngle": "create",
    "Assembly_CreateJointDistance": "create",
    "Assembly_CreateJointFixed": "create",
    "Assembly_CreateJointGears": "gears",
    "Assembly_CreateJointParallel": "create",
    "Assembly_CreateJointPerpendicular": "create",
    "Assembly_CreateJointRackPinion": "rack_pinion",
    "Assembly_CreateJointRevolute": "create",
    "Assembly_CreateJointScrew": "screw",
    "Assembly_CreateJointSlider": "create",
    "Assembly_SelectJointsOfComponent": "read",
    "Assembly_ToggleGrounded": "set_grounded",
    "FEM_Analysis": "create_analysis",
    "FEM_MaterialSolid": "create_solid_material",
    "FEM_MaterialFluid": "create_fluid_material",
    "FEM_MaterialMechanicalNonlinear": "create_nonlinear_material",
    "FEM_MaterialReinforced": "create_reinforced_material",
    "FEM_MaterialEditor": "update_material",
    "FEM_ElementGeometry1D": "create_beam_section",
    "FEM_ElementRotation1D": "create_beam_rotation",
    "FEM_ElementGeometry2D": "create_shell_thickness",
    "FEM_ElementFluid1D": "create_fluid_section",
    "FEM_ConstraintElectromagnetic": "constraint_electromagnetic",
    "FEM_ConstraintCurrentDensity": "constraint_current_density",
    "FEM_ConstraintMagnetization": "constraint_magnetization",
    "FEM_ConstraintElectricChargeDensity": "constraint_electric_charge_density",
    "FEM_ConstraintInitialFlowVelocity": "create_initial_flow_velocity",
    "FEM_ConstraintInitialPressure": "create_initial_pressure",
    "FEM_ConstraintFlowVelocity": "create_flow_velocity",
    "FEM_ConstraintFluidBoundary": "create_fluid_boundary",
    "FEM_ConstraintPlaneRotation": "create_plane_rotation",
    "FEM_ConstraintSectionPrint": "create_section_print",
    "FEM_ConstraintTransform": "create_transform",
    "FEM_ConstraintFixed": "create_fixed",
    "FEM_ConstraintRigidBody": "create_rigid_body",
    "FEM_ConstraintDisplacement": "create_displacement",
    "FEM_ConstraintSpring": "create_spring",
    "FEM_ConstraintContact": "create_contact",
    "FEM_ConstraintTie": "create_tie",
    "FEM_ConstraintForce": "create_force",
    "FEM_ConstraintPressure": "create_pressure",
    "FEM_ConstraintCentrif": "create_centrifugal",
    "FEM_ConstraintSelfWeight": "create_gravity",
    "FEM_ConstraintInitialTemperature": "create_initial_temperature",
    "FEM_ConstraintHeatflux": "create_surface_heat_flux",
    "FEM_ConstraintTemperature": "create_boundary_temperature",
    "FEM_ConstraintBodyHeatSource": "create_mass_heat_generation",
    "FEM_MeshNetgenFromShape": "create_netgen",
    "FEM_MeshGmshFromShape": "create_gmsh",
    "FEM_MeshRegion": "create_region",
    "FEM_MeshGroup": "create_group",
    "FEM_MeshDistance": "create_distance",
    "FEM_MeshBoundaryLayer": "create_boundary_layer",
    "FEM_MeshShape": "create_shape",
    "FEM_SolverCalculiX": "create_calculix",
    "FEM_SolverElmer": "create_elmer",
    "FEM_SolverMystran": "create_mystran",
    "FEM_SolverZ88": "create_z88",
    "FEM_SolverOpenFOAM": "create_openfoam",
    "FEM_SolverControl": "update_calculix",
    "FEM_SolverRun": "run",
    "FEM_EquationElasticity": "create_elasticity",
    "FEM_EquationDeformation": "create_deformation",
    "FEM_EquationElectrostatic": "create_electrostatic",
    "FEM_EquationElectricforce": "create_electric_force",
    "FEM_EquationMagnetodynamic": "create_magnetodynamic",
    "FEM_EquationMagnetodynamic2D": "create_magnetodynamic_2d",
    "FEM_EquationStaticCurrent": "create_static_current",
    "FEM_EquationFlow": "create_flow",
    "FEM_EquationFlux": "create_flux",
    "FEM_EquationHeat": "create_heat",
    "FEM_MeshManipulate": "create_restrict",
    "FEM_MeshAdvanced": "create_attractor_aniso_curve",
    "FEM_MeshTransfiniteCurve": "create_transfinite_curve",
    "FEM_MeshTransfiniteSurface": "create_transfinite_surface",
    "FEM_MeshTransfiniteVolume": "create_transfinite_volume",
    "PartDesign::DesignBox": "box",
    "PartDesign::DesignCylinder": "cylinder",
    "PartDesign::DesignSphere": "sphere",
    "PartDesign::DesignCone": "cone",
    "PartDesign::DesignEllipsoid": "ellipsoid",
    "PartDesign::DesignTorus": "torus",
    "PartDesign::DesignPrism": "prism",
    "PartDesign::DesignWedge": "wedge",
    "PartDesign::DesignTube": "tube",
    "PartDesign_DesignExtrude": "create",
    "PartDesign_DesignRevolve": "create",
    "PartDesign_DesignLoft": "create",
    "PartDesign_DesignSweep": "create",
    "PartDesign_DesignHelix": "create",
    "PartDesign_DesignMirror": "pattern",
    "PartDesign_DesignLinearPattern": "pattern",
    "PartDesign_DesignCircularPattern": "pattern",
    "PartDesign_Scale": "scale",
    "Part_Primitives": "primitive",
    "Part_Builder": "builder",
    "Part_MakeFace": "make_face",
    "Part_RuledSurface": "ruled_surface",
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
    "VibeCAD_SectionView": "set_section_view",
    "Std_Measure": "distance",
    "Inspection_VisualInspection": "compare",
    "Inspection_InspectElement": "element",
    "Part_CheckGeometry": "validity",
    "CAM_Pocket3D": "pocket_3d",
    "CAM_Surface": "surface",
    "CAM_Waterline": "waterline",
    "CAM_RotarySurface": "rotary_surface",
    "CAM_OpActiveToggle": "set_active",
    "CAM_OperationCopy": "copy_operations",
    "CAM_Array": "array",
    "CAM_SimpleCopy": "simple_copy",
    "CAM_DressupArray": "array_dressup",
    "CAM_DressupAxisMap": "axis_map_dressup",
    "CAM_DressupPathBoundary": "path_boundary_dressup",
    "CAM_DressupDogbone": "dogbone_dressup",
    "CAM_DressupDragKnife": "drag_knife_dressup",
    "CAM_DressupLeadInOut": "lead_in_out_dressup",
    "CAM_DressupMirror": "mirror_dressup",
    "CAM_DressupRampEntry": "ramp_entry_dressup",
    "CAM_DressupTag": "tag_dressup",
    "CAM_DressupZCorrect": "z_correct_dressup",
    "CAM_Pocket_Shape": "pocket_shape",
    "CAM_MillFacing": "mill_facing",
    "CAM_Helix": "helix",
    "CAM_Adaptive": "adaptive",
    "CAM_Slot": "slot",
    "CAM_Drilling": "drilling",
    "CAM_ThreadMilling": "thread_milling",
    "CAM_Engrave": "engrave",
    "CAM_Deburr": "deburr",
    "CAM_Vcarve": "v_carve",
    "CAM_Camotics": "camotics",
    "CAM_SimulatorGL": "gl",
    "CAM_Simulator": "native",
    "CAM_Post": "complete_job",
    "CAM_PostSelected": "selected_operations",
    "Mesh_Export": "export_mesh",
    "Mesh_Import": "import_mesh",
    "Mesh_BuildRegularSolid": "regular_solid",
    "Mesh_FromPartShape": "tessellate",
    "MeshPart_ShapeFromMesh": "shell",
    "MeshPart_CurveOnMesh": "create",
    "Mesh_FillupHoles": "fill_holes",
    "Mesh_FillInteractiveHole": "fill_boundary",
    "Mesh_AddFacet": "add_triangle",
    "Mesh_Smoothing": "smooth",
    "Mesh_RemeshGmsh": "gmsh_remesh",
    "Mesh_Decimating": "decimate",
    "Mesh_Segmentation": "mesh_segmentation",
    "Points_Export": "export_point_cloud",
    "Points_Import": "import_point_cloud",
    "Points_Convert": "convert_to_points",
    "Points_PolyCut": "polygon_cut",
    "Reen_Segmentation": "reverse_segmentation",
    "Sketcher_CreateBSpline": "create_b_spline",
    "Sketcher_CreateBSplineByInterpolation": "create_b_spline_by_interpolation",
    "Sketcher_CreatePeriodicBSpline": "create_periodic_b_spline",
    "Sketcher_CreatePeriodicBSplineByInterpolation": (
        "create_periodic_b_spline_by_interpolation"
    ),
    "Sketcher_SelectElementsAssociatedWithConstraints": "select_elements",
    "Sketcher_EditSketch": "open",
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
    "TechDraw_View": "create_standard_view",
    "TechDraw_ProjectionGroup": "create_projection_group",
    "TechDraw_BrokenView": "create_broken_view",
    "TechDraw_SectionView": "create_section_view",
    "TechDraw_ComplexSection": "create_complex_section_view",
    "TechDraw_DetailView": "create_detail_view",
    "TechDraw_DraftView": "create_draft_source_view",
    "TechDraw_ClipGroup": "create_clip_group",
    "TechDraw_StackTop": "stack_top",
    "TechDraw_StackBottom": "stack_bottom",
    "TechDraw_StackUp": "stack_up",
    "TechDraw_StackDown": "stack_down",
    "TechDraw_ActiveView": "create_active_view",
    "TechDraw_Dimension": "infer",
    "TechDraw_LengthDimension": "create_linear",
    "TechDraw_HorizontalDimension": "create_linear",
    "TechDraw_VerticalDimension": "create_linear",
    "TechDraw_RadiusDimension": "create_radial",
    "TechDraw_DiameterDimension": "create_radial",
    "TechDraw_AngleDimension": "create_angle",
    "TechDraw_3PtAngleDimension": "create_three_point_angle",
    "TechDraw_AreaDimension": "create_area",
    "TechDraw_HorizontalExtentDimension": "create_view_extent",
    "TechDraw_VerticalExtentDimension": "create_view_extent",
    "TechDraw_AxoLengthDimension": "create_axonometric_length",
    "TechDraw_ExtensionCreateHorizChainDimension": "create_horizontal_chain",
    "TechDraw_ExtensionCreateVertChainDimension": "create_vertical_chain",
    "TechDraw_ExtensionCreateObliqueChainDimension": "create_oblique_chain",
    "TechDraw_ExtensionCreateHorizCoordDimension": "create_horizontal_coordinate",
    "TechDraw_ExtensionCreateVertCoordDimension": "create_vertical_coordinate",
    "TechDraw_ExtensionCreateObliqueCoordDimension": "create_oblique_coordinate",
    "TechDraw_DimensionRepair": "repair_references",
    "TechDraw_ExtensionSelectLineAttributes": "read_current",
    "TechDraw_ExtensionChangeLineAttributes": "set",
    "TechDraw_ExtensionExtendLine": "extend",
    "TechDraw_ExtensionShortenLine": "shorten",
    "TechDraw_ExtensionLockUnlockView": "set",
    "TechDraw_ExtensionPositionSectionView": "align_axis",
    "TechDraw_ExtensionAreaAnnotation": "create_area_annotation",
    "TechDraw_ExtensionArcLengthAnnotation": "create_arc_length_annotation",
    "TechDraw_ExtensionCustomizeFormat": "set_dimension_format",
    "TechDraw_ExtensionInsertDiameter": "insert_diameter_prefix",
    "TechDraw_ExtensionInsertSquare": "insert_square_prefix",
    "TechDraw_ExtensionInsertRepetition": "insert_repetition_prefix",
    "TechDraw_ExtensionRemovePrefixChar": "remove_prefix",
    "TechDraw_ExtensionIncreaseDecimal": "increase_decimals",
    "TechDraw_ExtensionDecreaseDecimal": "decrease_decimals",
    "TechDraw_ToggleFrame": "set_visibility",
    "TechDraw_ShowAll": "set_visibility",
    "TechDraw_Hatch": "create_image_default",
    "TechDraw_GeometricHatch": "create_geometric_default",
    "TechDraw_RichTextAnnotation": "create",
    "TechDraw_LeaderLine": "create",
    "TechDraw_ExtensionCircleCenterLines": "create",
    "TechDraw_ExtensionHoleCircle": "create",
    "TechDraw_ExtensionThreadHoleSide": "create_hole_side",
    "TechDraw_ExtensionThreadHoleBottom": "create_hole_bottom",
    "TechDraw_ExtensionThreadBoltSide": "create_bolt_side",
    "TechDraw_ExtensionThreadBoltBottom": "create_bolt_bottom",
    "TechDraw_ExtensionVertexAtIntersection": "create_intersections",
    "TechDraw_CommandAddOffsetVertex": "create_offset",
    "TechDraw_CosmeticVertex": "create_point",
    "TechDraw_Midpoints": "create_midpoints",
    "TechDraw_Quadrants": "create_quadrants",
    "TechDraw_FaceCenterLine": "create_face",
    "TechDraw_2LineCenterLine": "create_between_edges",
    "TechDraw_2PointCenterLine": "create_between_vertices",
    "TechDraw_2PointCosmeticLine": "create_between_vertices",
    "TechDraw_CosmeticCircle": "create_one_point_circle",
    "TechDraw_ExtensionDrawCosmCircle": "create_two_point_circle",
    "TechDraw_ExtensionDrawCosmCircle3Points": "create_three_point_circle",
    "TechDraw_ExtensionDrawCosmArc": "create_center_start_end_arc",
    "TechDraw_ExtensionLineParallel": "create_parallel",
    "TechDraw_ExtensionLinePerpendicular": "create_perpendicular",
    "TechDraw_ExtensionCreateHorizChamferDimension": "create_chamfer",
    "TechDraw_ExtensionCreateVertChamferDimension": "create_chamfer",
    "TechDraw_ExtensionCreateLengthArc": "create_arc_length_dimension",
    "TechDraw_Balloon": "create",
    "TechDraw_DecorateLine": "set",
    "TechDraw_HoleShaftFit": "apply_iso_286_fit",
    "TechDraw_WeldSymbol": "create_weld",
    "TechDraw_SurfaceFinishSymbols": "create_iso_surface_finish",
    "TechDraw_RedrawPage": "redraw_page",
    "Robot_InsertWaypoint": "insert_robot_waypoint",
    "Robot_InsertWaypointPreselect": "insert_position_waypoint",
    "Robot_Edge2Trac": "create_path",
    "Robot_TrajectoryDressUp": "set_motion",
    "Robot_TrajectoryCompound": "create_sequence",
    "TechDraw_ExportPageDXF": "dxf",
    "TechDraw_ExportPageSVG": "svg",
}

_EXACT_TARGET_TYPE_OVERRIDES = {
    "Spreadsheet_CreateSheet": "NewParametersSheet",
    "Spreadsheet_Import": "HumanAuthorizedParametersCsv",
    "Spreadsheet_Export": "ExactParametersSheetAndHumanAuthorizedOutput",
    "Spreadsheet_MergeCells": "ExactParametersRange",
    "Spreadsheet_SplitCell": "ExactParametersMergedCell",
    "Spreadsheet_CellProperties": "ExactParametersRange",
    "Spreadsheet_SetAlias": "ExactParametersCell",
    "Spreadsheet_AlignLeft": "ExactParametersRange",
    "Spreadsheet_AlignCenter": "ExactParametersRange",
    "Spreadsheet_AlignRight": "ExactParametersRange",
    "Spreadsheet_AlignTop": "ExactParametersRange",
    "Spreadsheet_AlignVCenter": "ExactParametersRange",
    "Spreadsheet_AlignBottom": "ExactParametersRange",
    "Spreadsheet_StyleBold": "ExactParametersRange",
    "Spreadsheet_StyleItalic": "ExactParametersRange",
    "Spreadsheet_StyleUnderline": "ExactParametersRange",
    "CAM_Job": "ExactCurrentCamModelsAndCreationEnvironment",
    "CAM_FollowUpSetup": "ExactCurrentRetainedStockResult",
    "CAM_Profile": "ExactCamJobProfileGeometryAndController",
    "CAM_Pocket_Shape": "ExactCamJobPocketGeometryAndController",
    "CAM_MillFacing": "ExactCamJobStockAndController",
    "CAM_Helix": "ExactCamJobHoleFeaturesControllerAndHelixParameters",
    "CAM_Adaptive": "ExactCamJobAdaptiveRegionsAndController",
    "CAM_Slot": "ExactCamJobSlotPathControllerAndParameters",
    "CAM_Drilling": "ExactCamJobDrillableGeometryAndController",
    "CAM_ThreadMilling": "ExactCamJobHoleFeaturesControllerAndThreadDefinition",
    "CAM_Engrave": "ExactCamJobEngraveGeometryControllerAndParameters",
    "CAM_Deburr": "ExactCamJobDeburrFeaturesControllerAndParameters",
    "CAM_Vcarve": "ExactCamJobVCarveFacesControllerAndParameters",
    "CAM_Pocket3D": "ExactCamJobPocket3DFeaturesControllerAndParameters",
    "CAM_Surface": "ExactCamJobSurfaceFacesControllerAndParameters",
    "CAM_Waterline": (
        "ExactCamJobWaterlineFacesControllerAlgorithmAndParameters"
    ),
    "CAM_RotarySurface": (
        "ExactCamJobMachineCylinderRotaryFacesControllerAndParameters"
    ),
    "CAM_OpActiveToggle": "ExactCamJobAndOperationActiveStates",
    "CAM_OperationCopy": "ExactCamOperationCopySet",
    "CAM_Array": "ExactCamJobBaseToolpathsArrayPatternAndPointSources",
    "CAM_SimpleCopy": "ExactCamJobPlacedToolpathFlatteningSet",
    "CAM_DressupArray": "ExactCamJobOperationAndArrayDressupPattern",
    "CAM_DressupAxisMap": "ExactCamJobOperationAndAxisMapParameters",
    "CAM_DressupPathBoundary": (
        "ExactCamJobOperationAndPathBoundaryDefinition"
    ),
    "CAM_DressupDogbone": (
        "ExactCamJobOperationAndDogboneReliefDefinition"
    ),
    "CAM_DressupDragKnife": (
        "ExactCamJobOperationAndDragKnifeCompensation"
    ),
    "CAM_DressupLeadInOut": (
        "ExactCamJobOperationAndLeadInOutMotionDefinition"
    ),
    "CAM_DressupMirror": (
        "ExactCamJobOperationAndMirrorPlacementDefinition"
    ),
    "CAM_DressupRampEntry": (
        "ExactCamJobOperationAndRampEntryDefinition"
    ),
    "CAM_DressupTag": (
        "ExactCamJobOperationAndHoldingTagDefinition"
    ),
    "CAM_DressupZCorrect": (
        "ExactCamJobOperationAndHumanAuthorizedProbeMap"
    ),
    "CAM_Comment": "ExactCamJobAndProgramComment",
    "CAM_Stop": "ExactCamJobAndProgramStop",
    "CAM_Custom": "ExactCamJobControllerAndStructuredCustomProgram",
    "CAM_Probe": "ExactCamJobProbeControllerAndBoundedStockGrid",
    "CAM_PropertyBag": "ExactOptionalPartDesignBodyAndTypedPropertySet",
    "CAM_ToolBitDock": "ExactCamJobAndCatalogTool",
    "CAM_Sanity": "ExactCamJobGraphAndState",
    "CAM_Inspect": "ExactCamOperationToolpathAndState",
    "CAM_SelectLoop": "ExactCurrentCamModelShapeAndLoopSeed",
    "CAM_Area": "ExactCurrentPartGeometrySet",
    "CAM_Area_Workplane": "ExactCurrentFeatureAreaAndPartWorkplane",
    "CAM_Camotics": "ExactCamJobOrderedActiveOperationsCamoticsRequest",
    "CAM_SimulatorGL": "ExactCamJobOrderedActiveOperationsAndGlQuality",
    "CAM_Simulator": "ExactCamJobOrderedActiveOperationsAndNativeQuality",
    "CAM_Post": "ExactCamJobAndHumanAuthorizedPostOutputs",
    "CAM_PostSelected": (
        "ExactCamJobOrderedOperationsAndHumanAuthorizedPostOutputs"
    ),
    "TechDraw_PageDefault": "NewDrawingPageWithConfiguredTemplate",
    "TechDraw_PageTemplate": "HumanAuthorizedSvgTemplateForNewDrawingPage",
    "TechDraw_FillTemplateFields": (
        "ExactDrawingPageAndEditableTemplateFields"
    ),
    "TechDraw_View": "ExactDrawingPageSourcesAndProjectionSettings",
    "TechDraw_ProjectionGroup": (
        "ExactDrawingPageSourcesProjectionSetAndConvention"
    ),
    "TechDraw_BrokenView": (
        "ExactDrawingPageSourcesBreakDefinitionsAndProjectionSettings"
    ),
    "TechDraw_SectionView": "ExactDrawingPageBaseViewSectionPlaneAndScale",
    "TechDraw_ComplexSection": (
        "ExactDrawingPageBaseViewWholeProfileStrategyAndScale"
    ),
    "TechDraw_DetailView": (
        "ExactDrawingPageBaseViewAnchorRadiusPlacementAndScale"
    ),
    "TechDraw_DraftView": (
        "ExactDrawingPageDraftSourceOrientationPlacementScaleAndStyle"
    ),
    "TechDraw_ClipGroup": "ExactDrawingPageClipFrameMembersAndPlacements",
    "TechDraw_StackTop": "ExactDrawingPageAndOrderedGraphicalStackViews",
    "TechDraw_StackBottom": "ExactDrawingPageAndOrderedGraphicalStackViews",
    "TechDraw_StackUp": "ExactDrawingPageAndOrderedGraphicalStackViews",
    "TechDraw_StackDown": "ExactDrawingPageAndOrderedGraphicalStackViews",
    "TechDraw_ActiveView": (
        "ExactDrawingPageActive3DViewportAndCaptureSettings"
    ),
    "TechDraw_Dimension": "ExactDrawingElementsWithUnambiguousDimensionSemantics",
    "TechDraw_LengthDimension": "ExactDrawingLinearDimensionReferencesAndDirection",
    "TechDraw_HorizontalDimension": "ExactDrawingLinearDimensionReferencesAndDirection",
    "TechDraw_VerticalDimension": "ExactDrawingLinearDimensionReferencesAndDirection",
    "TechDraw_RadiusDimension": "ExactDrawingRadialEdgeAndKind",
    "TechDraw_DiameterDimension": "ExactDrawingRadialEdgeAndKind",
    "TechDraw_AngleDimension": "ExactDrawingTwoEdgeAngle",
    "TechDraw_3PtAngleDimension": "ExactDrawingOrderedThreePointAngle",
    "TechDraw_AreaDimension": "ExactDrawingProjectedFace",
    "TechDraw_HorizontalExtentDimension": "ExactDrawingViewExtentAndDirection",
    "TechDraw_VerticalExtentDimension": "ExactDrawingViewExtentAndDirection",
    "TechDraw_AxoLengthDimension": (
        "ExactDrawingAxonometricMeasurementDirectionsAndValueMode"
    ),
    "TechDraw_ExtensionCreateHorizChainDimension": (
        "ExactDrawingHorizontalChainDimensionSeries"
    ),
    "TechDraw_ExtensionCreateVertChainDimension": (
        "ExactDrawingVerticalChainDimensionSeries"
    ),
    "TechDraw_ExtensionCreateObliqueChainDimension": (
        "ExactDrawingObliqueChainDimensionSeries"
    ),
    "TechDraw_ExtensionCreateHorizCoordDimension": (
        "ExactDrawingHorizontalCoordinateDimensionSeries"
    ),
    "TechDraw_ExtensionCreateVertCoordDimension": (
        "ExactDrawingVerticalCoordinateDimensionSeries"
    ),
    "TechDraw_ExtensionCreateObliqueCoordDimension": (
        "ExactDrawingObliqueCoordinateDimensionSeries"
    ),
    "TechDraw_DimensionRepair": "ExactDrawingDimensionAndReplacementReferences",
    "TechDraw_ExtensionSelectLineAttributes": (
        "CurrentTechDrawLineAndPlacementDefaults"
    ),
    "TechDraw_ExtensionChangeLineAttributes": (
        "ExactDrawingLinesAndCompleteFormat"
    ),
    "TechDraw_DecorateLine": "ExactDrawingLinesAndCompleteFormat",
    "TechDraw_ExtensionExtendLine": (
        "ExactDrawingStraightPersistentLineAndSymmetricDelta"
    ),
    "TechDraw_ExtensionShortenLine": (
        "ExactDrawingStraightPersistentLineAndSymmetricDelta"
    ),
    "TechDraw_ExtensionLockUnlockView": (
        "ExactDrawingPageAndExplicitViewLockStates"
    ),
    "TechDraw_ExtensionPositionSectionView": (
        "ExactDrawingSectionViewAndExplicitBaseAxis"
    ),
    "TechDraw_ExtensionAreaAnnotation": (
        "ExactDrawingProjectedFacesAndAreaAnnotation"
    ),
    "TechDraw_ExtensionArcLengthAnnotation": (
        "ExactDrawingOrderedProjectedEdgesAndArcLengthAnnotation"
    ),
    "TechDraw_ExtensionCustomizeFormat": (
        "ExactDrawingDimensionAndCompleteFormat"
    ),
    "TechDraw_HoleShaftFit": (
        "ExactDrawingDimensionAndIso286ToleranceClass"
    ),
    "TechDraw_WeldSymbol": (
        "ExactDrawingLeaderAndCompleteWeldSymbolSpec"
    ),
    "TechDraw_SurfaceFinishSymbols": (
        "ExactDrawingPageOwnerIsoSurfaceFinishSpec"
    ),
    "TechDraw_ExtensionInsertDiameter": (
        "ExactDrawingDimensionsAndDiameterPrefix"
    ),
    "TechDraw_ExtensionInsertSquare": (
        "ExactDrawingDimensionsAndSquarePrefix"
    ),
    "TechDraw_ExtensionInsertRepetition": (
        "ExactDrawingDimensionsAndRepetitionCount"
    ),
    "TechDraw_ExtensionRemovePrefixChar": (
        "ExactDrawingDimensionsAndPrefixRemoval"
    ),
    "TechDraw_ExtensionIncreaseDecimal": (
        "ExactDrawingDimensionsAndPrecisionIncrease"
    ),
    "TechDraw_ExtensionDecreaseDecimal": (
        "ExactDrawingDimensionsAndPrecisionDecrease"
    ),
    "TechDraw_ToggleFrame": (
        "HumanActiveDrawingPageAndExactFrameVisibilityState"
    ),
    "TechDraw_ShowAll": (
        "HumanActiveDrawingViewAndExactHiddenEdgeVisibilityState"
    ),
    "TechDraw_Hatch": "ExactDrawingProjectedFacesAndImageHatchStyle",
    "TechDraw_GeometricHatch": (
        "ExactDrawingProjectedFacesAndGeometricHatchStyle"
    ),
    "TechDraw_RichTextAnnotation": (
        "ExactDrawingPageOwnerPlainTextPlacementWidthAndFrame"
    ),
    "TechDraw_LeaderLine": (
        "ExactDrawingPageOwnerPointsSymbolsBehaviorAndLineStyle"
    ),
    "TechDraw_ExtensionCircleCenterLines": (
        "ExactDrawingCircularEdgesAndPersistentCrossCenterlines"
    ),
    "TechDraw_ExtensionHoleCircle": (
        "ExactOrderedDrawingHoleCirclesAndDerivedBoltCircle"
    ),
    "TechDraw_ExtensionThreadHoleSide": (
        "ExactDrawingParallelHoleBoundariesAndSideThreadLines"
    ),
    "TechDraw_ExtensionThreadHoleBottom": (
        "ExactDrawingFullHoleCirclesAndBottomThreadArcs"
    ),
    "TechDraw_ExtensionThreadBoltSide": (
        "ExactDrawingParallelBoltBoundariesAndSideThreadLines"
    ),
    "TechDraw_ExtensionThreadBoltBottom": (
        "ExactDrawingFullBoltCirclesAndBottomThreadArcs"
    ),
    "TechDraw_ExtensionVertexAtIntersection": (
        "ExactDrawingIntersectingEdgesAndDerivedCosmeticVertices"
    ),
    "TechDraw_CommandAddOffsetVertex": (
        "ExactDrawingProjectedVertexAndExplicitOffset"
    ),
    "TechDraw_CosmeticVertex": (
        "ExactDrawingViewAndExplicitCosmeticVertexPoint"
    ),
    "TechDraw_Midpoints": "ExactDrawingEdgesAndDerivedMidpointVertices",
    "TechDraw_Quadrants": "ExactDrawingEdgesAndDerivedQuadrantVertices",
    "TechDraw_FaceCenterLine": "ExactDrawingFacesAndDerivedCenterLine",
    "TechDraw_2LineCenterLine": "ExactDrawingEdgePairAndDerivedCenterLine",
    "TechDraw_2PointCenterLine": "ExactDrawingVertexPairAndDerivedCenterLine",
    "TechDraw_2PointCosmeticLine": "ExactDrawingVertexPairAndCosmeticLine",
    "TechDraw_CosmeticCircle": "ExactDrawingCenterVertexAndExplicitRadius",
    "TechDraw_ExtensionDrawCosmCircle": (
        "ExactDrawingCenterAndRadiusVertices"
    ),
    "TechDraw_ExtensionDrawCosmCircle3Points": (
        "ExactDrawingThreePerimeterVertices"
    ),
    "TechDraw_ExtensionDrawCosmArc": (
        "ExactDrawingCenterStartAndEndAngleVertices"
    ),
    "TechDraw_ExtensionLineParallel": (
        "ExactDrawingStraightEdgeAndThroughVertexParallelLine"
    ),
    "TechDraw_ExtensionLinePerpendicular": (
        "ExactDrawingStraightEdgeAndThroughVertexPerpendicularLine"
    ),
    "TechDraw_ExtensionCreateHorizChamferDimension": (
        "ExactDrawingChamferVerticesAndDirection"
    ),
    "TechDraw_ExtensionCreateVertChamferDimension": (
        "ExactDrawingChamferVerticesAndDirection"
    ),
    "TechDraw_ExtensionCreateLengthArc": "ExactDrawingCircularArcLength",
    "TechDraw_Balloon": "ExactDrawingProjectedBalloonAnchorAndPlacement",
    "TechDraw_RedrawPage": "ExactDrawingPageAndActiveViewGraph",
    "Robot_ExportKukaCompact": (
        "ExactRobotAndNonEmptyTrajectoryWithHumanAuthorizedOutput"
    ),
    "Robot_ExportKukaFull": (
        "ExactRobotAndNonEmptyTrajectoryWithHumanAuthorizedOutputs"
    ),
    "Sketcher_MapSketch": "ExactReusableSketchAndSupport",
    "Sketcher_ReorientSketch": "ExactReusableSketchAndBasePlane",
    "Sketcher_MergeSketches": "ExactReusableSketchSet",
    "Sketcher_MirrorSketch": "ExactReusableSketchSetAndMirrorReference",
}

_SURFACE_CAPABILITY_OVERRIDES = {
    ("model", "VibeCAD_InsertStandardFastener"): "model.fastener",
    ("model", "VibeCAD_EditStandardFastener"): "model.fastener",
    ("assemble", "VibeCAD_InsertStandardFastener"): "assembly.fastener",
    ("assemble", "VibeCAD_EditStandardFastener"): "assembly.fastener_edit",
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
    ("model", "Aero"): "aero.solve",
    ("sketch.setup", "Sketch"): "sketch.setup",
    ("sketch.edit", "Finish"): "sketch.control",
    ("sketch.edit", "Geometry"): "sketch.draw_line",
    ("sketch.edit", "Constraints"): "sketch.constrain",
    ("sketch.edit", "Modify"): "sketch.edit",
    ("sketch.edit", "B-Spline"): "sketch.edit",
    ("sketch.edit", "Visual"): "sketch.presentation",
    ("assemble", "Assembly"): "assembly.create",
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
    ("analyze", "Geometry"): "analyze.geometrical",
    ("analyze", "Mechanics"): "analyze.mechanics",
    ("analyze", "Thermal"): "analyze.thermal",
    ("analyze", "Mesh"): "analyze.mesh",
    ("analyze", "Solve"): "analyze.solve",
    ("analyze", "Results"): "analyze.results",
    ("analyze", "Utilities"): "analyze.utility",
    ("manufacture", "Setup"): "manufacture.setup",
    ("manufacture", "Tools"): "manufacture.tool",
    ("manufacture", "Program"): "manufacture.program",
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
    ("drawing", "Decoration"): "drawing.page_frames",
    ("drawing", "Annotation"): "drawing.note",
    ("parameters", "Sheet"): "parameters.sheet",
    ("parameters", "Cells"): "parameters.cell",
    ("parameters", "Align"): "parameters.format",
    ("parameters", "Style"): "parameters.format",
    ("aero", "Actions"): "aero.solve",
    ("aero", "Aero"): "aero.solve",
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
    surface_override = _SURFACE_CAPABILITY_OVERRIDES.get((surface_id, command_id))
    if surface_override:
        return surface_override
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
    elif action.command_id in {"Sketcher_EditSketch", "Sketcher_LeaveSketch"}:
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
        exact_target_type=_EXACT_TARGET_TYPE_OVERRIDES.get(action.command_id),
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
