# SPDX-License-Identifier: LGPL-2.1-or-later

"""Explicit Native inventory for shipped context actions outside the ribbon."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from VibeCADNativeActionManifest import NativeActionClassification
from VibeCADRibbonSurface import SURFACE_IDS


class NativeContextManifestError(RuntimeError):
    """A context-action record is incomplete or internally inconsistent."""


_CONTEXT_SOURCES = frozenset(
    {
        "tree_context",
        "workbench_context",
        "drawing_canvas_context",
        "inspection_view_context",
        "task_panel",
        "menu",
    }
)
_TRANSACTION_BEHAVIORS = frozenset(
    {
        "none",
        "document",
        "background",
        "presentation",
        "output",
        "background_output",
        "human",
    }
)
_INSPECTION_SURFACES = (
    "model",
    "assemble",
    "mesh",
    "analyze",
    "manufacture",
    "drawing",
    "parameters",
    "aero",
    "sketch.setup",
)


def _classification(
    primary: str,
    *,
    interactive: bool = False,
) -> NativeActionClassification:
    if primary not in {"read", "mutation", "view", "export", "human_only"}:
        raise NativeContextManifestError(
            f"Unsupported context-action classification {primary!r}."
        )
    return NativeActionClassification(
        read=primary == "read",
        mutation=primary == "mutation",
        view=primary == "view",
        export=primary == "export",
        interactive=interactive,
        parent_only=False,
        human_only=primary == "human_only",
    )


@dataclass(frozen=True, slots=True)
class NativeContextActionPlan:
    action_id: str
    surface_ids: tuple[str, ...]
    sources: tuple[str, ...]
    source_command_id: str | None
    classification: NativeActionClassification
    capability_family: str
    operation_variant: str | None
    exact_target_type: str
    transaction_behavior: str
    background_required: bool = False
    implementation_status: str = "planned"

    def __post_init__(self) -> None:
        if not self.action_id.strip():
            raise NativeContextManifestError("Context action ID cannot be empty.")
        if not self.surface_ids or any(
            surface_id not in SURFACE_IDS or surface_id == "unavailable"
            for surface_id in self.surface_ids
        ):
            raise NativeContextManifestError(
                f"Context action {self.action_id!r} has invalid surfaces."
            )
        if len(self.surface_ids) != len(set(self.surface_ids)):
            raise NativeContextManifestError(
                f"Context action {self.action_id!r} repeats a surface."
            )
        if not self.sources or any(source not in _CONTEXT_SOURCES for source in self.sources):
            raise NativeContextManifestError(
                f"Context action {self.action_id!r} has invalid sources."
            )
        if not self.capability_family or "." not in self.capability_family:
            raise NativeContextManifestError(
                f"Context action {self.action_id!r} has no domain capability family."
            )
        if not self.exact_target_type:
            raise NativeContextManifestError(
                f"Context action {self.action_id!r} has no exact target type."
            )
        if self.transaction_behavior not in _TRANSACTION_BEHAVIORS:
            raise NativeContextManifestError(
                f"Context action {self.action_id!r} has invalid transaction behavior."
            )
        if self.classification.human_only:
            if self.operation_variant is not None or self.transaction_behavior != "human":
                raise NativeContextManifestError(
                    f"Human-only context action {self.action_id!r} cannot advertise an operation."
                )
            if self.implementation_status != "human_only":
                raise NativeContextManifestError(
                    f"Human-only context action {self.action_id!r} has invalid status."
                )
        elif not self.operation_variant or self.implementation_status != "planned":
            raise NativeContextManifestError(
                f"Provider context action {self.action_id!r} lacks a planned operation."
            )

    def summary(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "surface_ids": list(self.surface_ids),
            "sources": list(self.sources),
            "source_command_id": self.source_command_id,
            "classification": {
                "read": self.classification.read,
                "mutation": self.classification.mutation,
                "view": self.classification.view,
                "export": self.classification.export,
                "interactive": self.classification.interactive,
                "human_only": self.classification.human_only,
            },
            "capability_family": self.capability_family,
            "operation_variant": self.operation_variant,
            "exact_target_type": self.exact_target_type,
            "transaction_behavior": self.transaction_behavior,
            "background_required": self.background_required,
            "implementation_status": self.implementation_status,
        }


def _action(
    action_id: str,
    surface_ids: tuple[str, ...],
    sources: tuple[str, ...],
    primary: str,
    capability_family: str,
    operation_variant: str | None,
    exact_target_type: str,
    transaction_behavior: str,
    *,
    source_command_id: str | None = None,
    interactive: bool = False,
    background_required: bool = False,
) -> NativeContextActionPlan:
    human_only = primary == "human_only"
    return NativeContextActionPlan(
        action_id=action_id,
        surface_ids=surface_ids,
        sources=sources,
        source_command_id=source_command_id,
        classification=_classification(primary, interactive=interactive),
        capability_family=capability_family,
        operation_variant=operation_variant,
        exact_target_type=exact_target_type,
        transaction_behavior=transaction_behavior,
        background_required=background_required,
        implementation_status="human_only" if human_only else "planned",
    )


NATIVE_CONTEXT_ACTIONS = (
    _action(
        "VibeCAD_NativeSketchState", ("sketch.edit",), ("task_panel",),
        "read", "sketch.inspect", "read_state",
        "HumanOpenedSketch", "none",
    ),
    _action(
        "SketchEditDeleteGeometry", ("sketch.edit",), ("task_panel",),
        "mutation", "sketch.delete", "delete_geometry",
        "ActiveSketchExactGeometryDeletionAndExpectedState", "document",
    ),
    _action(
        "VibeCAD_AnalyzeReadAnalysis", ("analyze",), ("task_panel",),
        "read", "analyze.inspect", "analysis",
        "ExactFemAnalysisState", "none",
        source_command_id="FEM_Analysis",
    ),
    _action(
        "VibeCAD_AnalyzeReadGeometrySource", ("analyze",), ("task_panel",),
        "read", "analyze.faces", "read",
        "BoundedExactGeometryFacePage", "none",
        source_command_id="VibeCAD_AnalyzeStudySetup",
    ),
    _action(
        "VibeCAD_AnalyzeCreateSolidDomain", ("analyze",), ("task_panel",),
        "mutation", "analyze.solid_domain", "create",
        "CurrentSolidGeometrySources", "document",
        source_command_id="VibeCAD_AnalyzeStudySetup",
    ),
    _action(
        "VibeCAD_AnalyzeReadAssignments", ("analyze",), ("task_panel",),
        "read", "analyze.inspect", "assignments",
        "BoundedExactFemAssignmentPage", "none",
        source_command_id="VibeCAD_AnalyzeStudySetup",
    ),
    _action(
        "VibeCAD_AnalyzeValidateAssignments", ("analyze",), ("task_panel",),
        "read", "analyze.inspect", "validate_assignments",
        "ExactFemAssignmentValidation", "none",
        source_command_id="VibeCAD_AnalyzeStudySetup",
    ),
    _action(
        "VibeCAD_AnalyzeHighlightAssignment", ("analyze",), ("task_panel",),
        "view", "analyze.assignment_view", "highlight",
        "ExactFemAssignmentAndViewportPresentation", "presentation",
        source_command_id="VibeCAD_AnalyzeStudySetup",
    ),
    _action(
        "VibeCAD_AnalyzeIsolateAssignment", ("analyze",), ("task_panel",),
        "view", "analyze.assignment_view", "isolate",
        "ExactFemAssignmentAndViewportPresentation", "presentation",
        source_command_id="VibeCAD_AnalyzeStudySetup",
    ),
    _action(
        "VibeCAD_AnalyzeRestoreAssignmentView", ("analyze",), ("task_panel",),
        "view", "analyze.assignment_view", "restore",
        "ExactFemAssignmentAndViewportPresentation", "presentation",
        source_command_id="VibeCAD_AnalyzeStudySetup",
    ),
    _action(
        "VibeCAD_AnalyzeReadMaterial", ("analyze",), ("task_panel",),
        "read", "analyze.inspect", "material",
        "ExactFemMaterialState", "none",
        source_command_id="FEM_MaterialEditor",
    ),
    _action(
        "VibeCAD_AnalyzeSearchMaterialCatalog", ("analyze",), ("task_panel",),
        "read", "analyze.inspect", "material_catalog",
        "BoundedMaterialCatalogQuery", "none",
        source_command_id="FEM_MaterialEditor",
    ),
    _action(
        "VibeCAD_AnalyzeSearchMaterialCatalogFocused", ("analyze",), ("task_panel",),
        "read", "analyze.material_catalog", "search",
        "BoundedMaterialCatalogQuery", "none",
        source_command_id="FEM_MaterialEditor",
    ),
    _action(
        "VibeCAD_AnalyzeReadElementDefinition", ("analyze",), ("task_panel",),
        "read", "analyze.inspect", "element_definition",
        "ExactFemElementDefinitionState", "none",
        source_command_id="FEM_ElementGeometry1D",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateBeamSection", ("analyze",), ("task_panel",),
        "mutation", "analyze.geometry", "update_beam_section",
        "ExactFemBeamSectionAndGeometry", "document",
        source_command_id="FEM_ElementGeometry1D",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateBeamRotation", ("analyze",), ("task_panel",),
        "mutation", "analyze.geometry", "update_beam_rotation",
        "ExactFemBeamRotationAndGeometry", "document",
        source_command_id="FEM_ElementRotation1D",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateShellThickness", ("analyze",), ("task_panel",),
        "mutation", "analyze.geometry", "update_shell_thickness",
        "ExactFemShellThicknessAndGeometry", "document",
        source_command_id="FEM_ElementGeometry2D",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateFluidSection", ("analyze",), ("task_panel",),
        "mutation", "analyze.geometry", "update_fluid_section",
        "ExactFemFluidSectionAndGeometry", "document",
        source_command_id="FEM_ElementFluid1D",
    ),
    _action(
        "VibeCAD_AnalyzeReadElectromagneticConstraint", ("analyze",), ("task_panel",),
        "read", "analyze.inspect", "electromagnetic_constraint",
        "ExactFemElectromagneticConstraintState", "none",
        source_command_id="FEM_ConstraintElectromagnetic",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateElectromagnetic", ("analyze",), ("task_panel",),
        "mutation", "analyze.electromagnetic", "update_electromagnetic",
        "ExactFemElectromagneticConstraintAndGeometry", "document",
        source_command_id="FEM_ConstraintElectromagnetic",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateCurrentDensity", ("analyze",), ("task_panel",),
        "mutation", "analyze.electromagnetic", "update_current_density",
        "ExactFemCurrentDensityConstraintAndGeometry", "document",
        source_command_id="FEM_ConstraintCurrentDensity",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateMagnetization", ("analyze",), ("task_panel",),
        "mutation", "analyze.electromagnetic", "update_magnetization",
        "ExactFemMagnetizationConstraintAndGeometry", "document",
        source_command_id="FEM_ConstraintMagnetization",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateElectricChargeDensity", ("analyze",), ("task_panel",),
        "mutation", "analyze.electromagnetic", "update_electric_charge_density",
        "ExactFemElectricChargeDensityConstraintAndGeometry", "document",
        source_command_id="FEM_ConstraintElectricChargeDensity",
    ),
    _action(
        "VibeCAD_AnalyzeReadFluidConstraint", ("analyze",), ("task_panel",),
        "read", "analyze.inspect", "fluid_constraint",
        "ExactFemFluidConstraintState", "none",
        source_command_id="FEM_ConstraintFlowVelocity",
    ),
    _action(
        "VibeCAD_AnalyzeCreateInitialVelocity", ("analyze",), ("task_panel",),
        "mutation", "analyze.initial_velocity", "create",
        "ExactFemAnalysisFluidConstraintAndGeometry", "document",
        source_command_id="FEM_ConstraintInitialFlowVelocity",
    ),
    _action(
        "VibeCAD_AnalyzeCreateInitialPressure", ("analyze",), ("task_panel",),
        "mutation", "analyze.initial_pressure", "create",
        "ExactFemAnalysisFluidConstraintAndGeometry", "document",
        source_command_id="FEM_ConstraintInitialPressure",
    ),
    _action(
        "VibeCAD_AnalyzeCreateBoundaryVelocity", ("analyze",), ("task_panel",),
        "mutation", "analyze.boundary_velocity", "create",
        "ExactFemAnalysisFluidConstraintAndGeometry", "document",
        source_command_id="FEM_ConstraintFlowVelocity",
    ),
    _action(
        "VibeCAD_AnalyzeCreateFluidBoundary", ("analyze",), ("task_panel",),
        "mutation", "analyze.fluid_boundary", "create",
        "ExactFemAnalysisFluidConstraintAndGeometry", "document",
        source_command_id="FEM_ConstraintFluidBoundary",
    ),
    _action(
        "VibeCAD_AnalyzeCreateFluidMaterial", ("analyze",), ("task_panel",),
        "mutation", "analyze.fluid_material", "create",
        "CurrentNamedFemAnalysis", "document",
        source_command_id="FEM_MaterialFluid",
    ),
    _action(
        "VibeCAD_AnalyzeCreateSolidMaterial", ("analyze",), ("task_panel",),
        "mutation", "analyze.solid_material", "create",
        "CurrentNamedStructuralStudyAndGeometry", "document",
        source_command_id="FEM_MaterialSolid",
    ),
    _action(
        "VibeCAD_AnalyzeCreateSolidRegionMaterial", ("analyze",), ("task_panel",),
        "mutation", "analyze.solid_region_material", "create",
        "CurrentNamedStructuralStudyAndGeometry", "document",
        source_command_id="FEM_MaterialSolid",
    ),
    _action(
        "VibeCAD_AnalyzeCreateCatalogMaterial", ("analyze",), ("task_panel",),
        "mutation", "analyze.catalog_material", "create",
        "CurrentNamedStructuralStudyAndGeometry", "document",
        source_command_id="FEM_MaterialSolid",
    ),
    _action(
        "VibeCAD_AnalyzeCreateCustomMaterial", ("analyze",), ("task_panel",),
        "mutation", "analyze.custom_material", "create",
        "CurrentNamedStructuralStudyAndGeometry", "document",
        source_command_id="FEM_MaterialSolid",
    ),
    _action(
        "VibeCAD_AnalyzeCreateFixedSupport", ("analyze",), ("task_panel",),
        "mutation", "analyze.fixed_support", "create",
        "CurrentNamedStructuralStudyAndGeometry", "document",
        source_command_id="FEM_ConstraintFixed",
    ),
    _action(
        "VibeCAD_AnalyzeEditFixedSupportFocused", ("analyze",), ("task_panel",),
        "mutation", "analyze.edit_fixed_support", "edit",
        "CurrentNamedFemFixedSupport", "document",
        source_command_id="FEM_ConstraintFixed",
    ),
    _action(
        "VibeCAD_AnalyzeCreateRigidCouplingFocused", ("analyze",), ("task_panel",),
        "mutation", "analyze.rigid_coupling", "create",
        "CurrentNamedStructuralStudyAndGeometry", "document",
        source_command_id="FEM_ConstraintRigidBody",
    ),
    _action(
        "VibeCAD_AnalyzeEditRigidCouplingFocused", ("analyze",), ("task_panel",),
        "mutation", "analyze.edit_rigid_coupling", "edit",
        "CurrentNamedFemRigidCoupling", "document",
        source_command_id="FEM_ConstraintRigidBody",
    ),
    _action(
        "VibeCAD_AnalyzeCreateDisplacementSupportFocused", ("analyze",), ("task_panel",),
        "mutation", "analyze.displacement_support", "create",
        "CurrentNamedStructuralStudyAndGeometry", "document",
        source_command_id="FEM_ConstraintDisplacement",
    ),
    _action(
        "VibeCAD_AnalyzeEditDisplacementSupportFocused", ("analyze",), ("task_panel",),
        "mutation", "analyze.edit_displacement_support", "edit",
        "CurrentNamedFemDisplacementSupport", "document",
        source_command_id="FEM_ConstraintDisplacement",
    ),
    _action(
        "VibeCAD_AnalyzeCreateSpringSupportFocused", ("analyze",), ("task_panel",),
        "mutation", "analyze.spring_support", "create",
        "CurrentNamedStructuralStudyAndGeometry", "document",
        source_command_id="FEM_ConstraintSpring",
    ),
    _action(
        "VibeCAD_AnalyzeEditSpringSupportFocused", ("analyze",), ("task_panel",),
        "mutation", "analyze.edit_spring_support", "edit",
        "CurrentNamedFemSpringSupport", "document",
        source_command_id="FEM_ConstraintSpring",
    ),
    _action(
        "VibeCAD_AnalyzeCreateForce", ("analyze",), ("task_panel",),
        "mutation", "analyze.force", "create",
        "CurrentNamedStructuralStudyAndGeometry", "document",
        source_command_id="FEM_ConstraintForce",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateForceFocused", ("analyze",), ("task_panel",),
        "mutation", "analyze.edit_force", "edit",
        "CurrentNamedFemForce", "document",
        source_command_id="FEM_ConstraintForce",
    ),
    _action(
        "VibeCAD_AnalyzeCreatePressureFocused", ("analyze",), ("task_panel",),
        "mutation", "analyze.pressure", "create",
        "CurrentNamedStructuralStudyAndGeometry", "document",
        source_command_id="FEM_ConstraintPressure",
    ),
    _action(
        "VibeCAD_AnalyzeUpdatePressureFocused", ("analyze",), ("task_panel",),
        "mutation", "analyze.edit_pressure", "edit",
        "CurrentNamedFemPressure", "document",
        source_command_id="FEM_ConstraintPressure",
    ),
    _action(
        "VibeCAD_AnalyzeCreateGravityFocused", ("analyze",), ("task_panel",),
        "mutation", "analyze.gravity", "create",
        "CurrentNamedStructuralStudyAndGeometry", "document",
        source_command_id="FEM_ConstraintSelfWeight",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateGravityFocused", ("analyze",), ("task_panel",),
        "mutation", "analyze.edit_gravity", "edit",
        "CurrentNamedFemGravity", "document",
        source_command_id="FEM_ConstraintSelfWeight",
    ),
    _action(
        "VibeCAD_AnalyzeCreateCentrifugalFocused", ("analyze",), ("task_panel",),
        "mutation", "analyze.centrifugal_load", "create",
        "CurrentNamedStructuralStudyAndGeometry", "document",
        source_command_id="FEM_ConstraintCentrif",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateCentrifugalFocused", ("analyze",), ("task_panel",),
        "mutation", "analyze.edit_centrifugal_load", "edit",
        "CurrentNamedFemCentrifugalLoad", "document",
        source_command_id="FEM_ConstraintCentrif",
    ),
    _action(
        "VibeCAD_AnalyzeCreateOpenFOAMSolver", ("analyze",), ("task_panel",),
        "mutation", "analyze.openfoam_solver", "create",
        "CurrentNamedFemAnalysis", "document",
        source_command_id="FEM_SolverOpenFOAM",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateInitialFlowVelocity", ("analyze",), ("task_panel",),
        "mutation", "analyze.fluid", "update_initial_flow_velocity",
        "ExactFemInitialFlowVelocityAndGeometry", "document",
        source_command_id="FEM_ConstraintInitialFlowVelocity",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateInitialPressure", ("analyze",), ("task_panel",),
        "mutation", "analyze.fluid", "update_initial_pressure",
        "ExactFemInitialPressureAndGeometry", "document",
        source_command_id="FEM_ConstraintInitialPressure",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateFlowVelocity", ("analyze",), ("task_panel",),
        "mutation", "analyze.fluid", "update_flow_velocity",
        "ExactFemFlowVelocityAndGeometry", "document",
        source_command_id="FEM_ConstraintFlowVelocity",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateFluidBoundary", ("analyze",), ("task_panel",),
        "mutation", "analyze.edit_fluid_boundary", "edit",
        "CurrentNamedFemFluidBoundary", "document",
        source_command_id="FEM_ConstraintFluidBoundary",
    ),
    _action(
        "VibeCAD_AnalyzeReadGeometricalFeature", ("analyze",), ("task_panel",),
        "read", "analyze.inspect", "geometrical_feature",
        "ExactFemGeometricalFeatureState", "none",
        source_command_id="FEM_ConstraintPlaneRotation",
    ),
    _action(
        "VibeCAD_AnalyzeUpdatePlaneRotation", ("analyze",), ("task_panel",),
        "mutation", "analyze.geometrical", "update_plane_rotation",
        "ExactFemPlaneRotationAndFace", "document",
        source_command_id="FEM_ConstraintPlaneRotation",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateSectionPrint", ("analyze",), ("task_panel",),
        "mutation", "analyze.geometrical", "update_section_print",
        "ExactFemSectionPrintAndFace", "document",
        source_command_id="FEM_ConstraintSectionPrint",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateTransform", ("analyze",), ("task_panel",),
        "mutation", "analyze.geometrical", "update_transform",
        "ExactFemTransformAndEligibleFace", "document",
        source_command_id="FEM_ConstraintTransform",
    ),
    _action(
        "VibeCAD_AnalyzeReadSupportCondition", ("analyze",), ("task_panel",),
        "read", "analyze.inspect", "support_condition",
        "ExactFemSupportConditionState", "none",
        source_command_id="FEM_ConstraintFixed",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateFixed", ("analyze",), ("task_panel",),
        "mutation", "analyze.support", "update_fixed",
        "ExactFemFixedConditionAndGeometry", "document",
        source_command_id="FEM_ConstraintFixed",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateRigidBody", ("analyze",), ("task_panel",),
        "mutation", "analyze.support", "update_rigid_body",
        "ExactFemRigidBodyConditionAndGeometry", "document",
        source_command_id="FEM_ConstraintRigidBody",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateDisplacement", ("analyze",), ("task_panel",),
        "mutation", "analyze.support", "update_displacement",
        "ExactFemDisplacementConditionAndGeometry", "document",
        source_command_id="FEM_ConstraintDisplacement",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateSpring", ("analyze",), ("task_panel",),
        "mutation", "analyze.support", "update_spring",
        "ExactFemSpringConditionAndFace", "document",
        source_command_id="FEM_ConstraintSpring",
    ),
    _action(
        "VibeCAD_AnalyzeReadConnection", ("analyze",), ("task_panel",),
        "read", "analyze.inspect", "connection",
        "ExactFemConnectionState", "none",
        source_command_id="FEM_ConstraintContact",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateContact", ("analyze",), ("task_panel",),
        "mutation", "analyze.connection", "update_contact",
        "ExactFemContactAndSlaveMasterGeometry", "document",
        source_command_id="FEM_ConstraintContact",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateTie", ("analyze",), ("task_panel",),
        "mutation", "analyze.connection", "update_tie",
        "ExactFemTieAndSlaveMasterGeometry", "document",
        source_command_id="FEM_ConstraintTie",
    ),
    _action(
        "VibeCAD_AnalyzeReadLoad", ("analyze",), ("task_panel",),
        "read", "analyze.inspect", "load",
        "ExactFemMechanicalLoadState", "none",
        source_command_id="FEM_ConstraintForce",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateForce", ("analyze",), ("task_panel",),
        "mutation", "analyze.load", "update_force",
        "ExactFemForceLoadAndGeometry", "document",
        source_command_id="FEM_ConstraintForce",
    ),
    _action(
        "VibeCAD_AnalyzeUpdatePressure", ("analyze",), ("task_panel",),
        "mutation", "analyze.load", "update_pressure",
        "ExactFemPressureLoadAndGeometry", "document",
        source_command_id="FEM_ConstraintPressure",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateCentrifugal", ("analyze",), ("task_panel",),
        "mutation", "analyze.load", "update_centrifugal",
        "ExactFemCentrifugalLoadAxisAndScope", "document",
        source_command_id="FEM_ConstraintCentrif",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateGravity", ("analyze",), ("task_panel",),
        "mutation", "analyze.load", "update_gravity",
        "ExactFemGlobalGravityLoad", "document",
        source_command_id="FEM_ConstraintSelfWeight",
    ),
    _action(
        "VibeCAD_AnalyzeReadThermalCondition", ("analyze",), ("task_panel",),
        "read", "analyze.inspect", "thermal_condition",
        "ExactFemThermalConditionState", "none",
        source_command_id="FEM_ConstraintInitialTemperature",
    ),
    _action(
        "VibeCAD_AnalyzeCreateConvection", ("analyze",), ("task_panel",),
        "mutation", "analyze.thermal", "create_convection",
        "ExactFemSurfaceConvectionAndGeometry", "document",
        source_command_id="FEM_ConstraintHeatflux",
    ),
    _action(
        "VibeCAD_AnalyzeCreateRadiation", ("analyze",), ("task_panel",),
        "mutation", "analyze.thermal", "create_radiation",
        "ExactFemSurfaceRadiationAndGeometry", "document",
        source_command_id="FEM_ConstraintHeatflux",
    ),
    _action(
        "VibeCAD_AnalyzeCreateConcentratedHeatInput", ("analyze",), ("task_panel",),
        "mutation", "analyze.thermal", "create_concentrated_heat_input",
        "ExactFemConcentratedHeatInputAndGeometry", "document",
        source_command_id="FEM_ConstraintTemperature",
    ),
    _action(
        "VibeCAD_AnalyzeCreateTotalBodyPower", ("analyze",), ("task_panel",),
        "mutation", "analyze.thermal", "create_total_body_power",
        "ExactFemTotalBodyPowerAndGeometry", "document",
        source_command_id="FEM_ConstraintBodyHeatSource",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateInitialTemperature", ("analyze",), ("task_panel",),
        "mutation", "analyze.thermal", "update_initial_temperature",
        "ExactFemInitialTemperature", "document",
        source_command_id="FEM_ConstraintInitialTemperature",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateSurfaceHeatFlux", ("analyze",), ("task_panel",),
        "mutation", "analyze.thermal", "update_surface_heat_flux",
        "ExactFemSurfaceHeatFluxAndGeometry", "document",
        source_command_id="FEM_ConstraintHeatflux",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateConvection", ("analyze",), ("task_panel",),
        "mutation", "analyze.thermal", "update_convection",
        "ExactFemSurfaceConvectionAndGeometry", "document",
        source_command_id="FEM_ConstraintHeatflux",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateRadiation", ("analyze",), ("task_panel",),
        "mutation", "analyze.thermal", "update_radiation",
        "ExactFemSurfaceRadiationAndGeometry", "document",
        source_command_id="FEM_ConstraintHeatflux",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateBoundaryTemperature", ("analyze",), ("task_panel",),
        "mutation", "analyze.thermal", "update_boundary_temperature",
        "ExactFemBoundaryTemperatureAndGeometry", "document",
        source_command_id="FEM_ConstraintTemperature",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateConcentratedHeatInput", ("analyze",), ("task_panel",),
        "mutation", "analyze.thermal", "update_concentrated_heat_input",
        "ExactFemConcentratedHeatInputAndGeometry", "document",
        source_command_id="FEM_ConstraintTemperature",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateMassHeatGeneration", ("analyze",), ("task_panel",),
        "mutation", "analyze.thermal", "update_mass_heat_generation",
        "ExactFemMassHeatGenerationAndGeometry", "document",
        source_command_id="FEM_ConstraintBodyHeatSource",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateTotalBodyPower", ("analyze",), ("task_panel",),
        "mutation", "analyze.thermal", "update_total_body_power",
        "ExactFemTotalBodyPowerAndGeometry", "document",
        source_command_id="FEM_ConstraintBodyHeatSource",
    ),
    _action(
        "VibeCAD_AnalyzeReadMeshDefinition", ("analyze",), ("task_panel",),
        "read", "analyze.inspect", "fem_mesh_definition",
        "ExactFemMeshDefinitionState", "none",
        source_command_id="FEM_MeshGmshFromShape",
    ),
    _action(
        "VibeCAD_AnalyzeCreateGmshMesh", ("analyze",), ("task_panel",),
        "mutation", "analyze.gmsh_mesh", "create",
        "ExactFemAnalysisAndActiveShape", "document",
        source_command_id="FEM_MeshGmshFromShape",
    ),
    _action(
        "VibeCAD_AnalyzeCreateSolidMeshFocused", ("analyze",), ("task_panel",),
        "mutation", "analyze.solid_mesh", "create",
        "ExactFemAnalysisAndActiveShape", "document",
        source_command_id="FEM_MeshGmshFromShape",
    ),
    _action(
        "VibeCAD_AnalyzeCreateFlowMeshFocused", ("analyze",), ("task_panel",),
        "mutation", "analyze.flow_mesh", "create",
        "ExactFemAnalysisAndActiveShape", "document",
        source_command_id="FEM_MeshGmshFromShape",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateGmshMeshFocused", ("analyze",), ("task_panel",),
        "mutation", "analyze.edit_gmsh_mesh", "edit",
        "CurrentNamedFemGmshDefinition", "document",
        source_command_id="FEM_MeshGmshFromShape",
    ),
    _action(
        "VibeCAD_AnalyzeGenerateCurrentGmshMesh", ("analyze",), ("task_panel",),
        "mutation", "analyze.generate_gmsh", "generate",
        "ExactFemGmshDefinition", "background",
        source_command_id="FEM_MeshGmshFromShape", background_required=True,
    ),
    _action(
        "VibeCAD_AnalyzeUpdateGmshMesh", ("analyze",), ("task_panel",),
        "mutation", "analyze.mesh", "update_gmsh",
        "ExactFemGmshDefinitionAndActiveShape", "document",
        source_command_id="FEM_MeshGmshFromShape",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateNetgenMesh", ("analyze",), ("task_panel",),
        "mutation", "analyze.mesh", "update_netgen",
        "ExactFemNetgenDefinitionAndActiveShape", "document",
        source_command_id="FEM_MeshNetgenFromShape",
    ),
    _action(
        "VibeCAD_AnalyzeGenerateGmshMesh", ("analyze",), ("task_panel",),
        "mutation", "analyze.mesh", "generate_gmsh",
        "ExactFemMeshDefinitionRefinementGraphAndBackendArtifact", "background",
        source_command_id="FEM_MeshGmshFromShape", background_required=True,
    ),
    _action(
        "VibeCAD_AnalyzeGenerateNetgenMesh", ("analyze",), ("task_panel",),
        "mutation", "analyze.mesh", "generate_netgen",
        "ExactFemMeshDefinitionRefinementGraphAndBackendArtifact", "background",
        source_command_id="FEM_MeshNetgenFromShape", background_required=True,
    ),
    _action(
        "VibeCAD_AnalyzeReadMeshRefinement", ("analyze",), ("task_panel",),
        "read", "analyze.inspect", "mesh_refinement",
        "ExactFemMeshRefinementState", "none",
        source_command_id="FEM_MeshRegion",
    ),
    _action(
        "VibeCAD_AnalyzeCreateLocalMeshSizeFocused", ("analyze",), ("task_panel",),
        "mutation", "analyze.local_mesh_size", "create",
        "CurrentNamedFemMeshAndGeometry", "document",
        source_command_id="FEM_MeshRegion",
    ),
    _action(
        "VibeCAD_AnalyzeEditLocalMeshSizeFocused", ("analyze",), ("task_panel",),
        "mutation", "analyze.edit_local_mesh_size", "edit",
        "CurrentNamedFemMeshRegion", "document",
        source_command_id="FEM_MeshRegion",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateMeshRegion", ("analyze",), ("task_panel",),
        "mutation", "analyze.mesh_refinement", "update_region",
        "ExactFemMeshRegionAndGeometry", "document",
        source_command_id="FEM_MeshRegion",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateMeshGroup", ("analyze",), ("task_panel",),
        "mutation", "analyze.mesh_refinement", "update_group",
        "ExactFemMeshGroupAndGeometry", "document",
        source_command_id="FEM_MeshGroup",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateMeshDistance", ("analyze",), ("task_panel",),
        "mutation", "analyze.mesh_refinement", "update_distance",
        "ExactFemMeshDistanceAndGeometry", "document",
        source_command_id="FEM_MeshDistance",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateMeshBoundaryLayer", ("analyze",), ("task_panel",),
        "mutation", "analyze.mesh_refinement", "update_boundary_layer",
        "ExactFemMeshBoundaryLayerAndEdges", "document",
        source_command_id="FEM_MeshBoundaryLayer",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateMeshShape", ("analyze",), ("task_panel",),
        "mutation", "analyze.mesh_refinement", "update_shape",
        "ExactFemMeshShapeRefinement", "document",
        source_command_id="FEM_MeshShape",
    ),
    _action(
        "VibeCAD_AnalyzeCreateMeshThreshold", ("analyze",), ("task_panel",),
        "mutation", "analyze.mesh_field", "create_threshold",
        "ExactAcyclicGmshRefinementFieldGraphAndGeometry", "document",
        source_command_id="FEM_MeshManipulate",
    ),
    _action(
        "VibeCAD_AnalyzeCreateMeshMean", ("analyze",), ("task_panel",),
        "mutation", "analyze.mesh_field", "create_mean",
        "ExactAcyclicGmshRefinementFieldGraphAndGeometry", "document",
        source_command_id="FEM_MeshManipulate",
    ),
    _action(
        "VibeCAD_AnalyzeCreateMeshGradient", ("analyze",), ("task_panel",),
        "mutation", "analyze.mesh_field", "create_gradient",
        "ExactAcyclicGmshRefinementFieldGraphAndGeometry", "document",
        source_command_id="FEM_MeshManipulate",
    ),
    _action(
        "VibeCAD_AnalyzeCreateMeshCurvature", ("analyze",), ("task_panel",),
        "mutation", "analyze.mesh_field", "create_curvature",
        "ExactAcyclicGmshRefinementFieldGraphAndGeometry", "document",
        source_command_id="FEM_MeshManipulate",
    ),
    _action(
        "VibeCAD_AnalyzeCreateMeshLaplacian", ("analyze",), ("task_panel",),
        "mutation", "analyze.mesh_field", "create_laplacian",
        "ExactAcyclicGmshRefinementFieldGraphAndGeometry", "document",
        source_command_id="FEM_MeshManipulate",
    ),
    _action(
        "VibeCAD_AnalyzeCreateMeshMathEval", ("analyze",), ("task_panel",),
        "mutation", "analyze.mesh_field", "create_math_eval",
        "ExactAcyclicGmshRefinementFieldGraphAndGeometry", "document",
        source_command_id="FEM_MeshAdvanced",
    ),
    _action(
        "VibeCAD_AnalyzeCreateMeshMathEvalAniso", ("analyze",), ("task_panel",),
        "mutation", "analyze.mesh_field", "create_math_eval_aniso",
        "ExactAcyclicGmshRefinementFieldGraphAndGeometry", "document",
        source_command_id="FEM_MeshAdvanced",
    ),
    _action(
        "VibeCAD_AnalyzeCreateMeshFieldDistance", ("analyze",), ("task_panel",),
        "mutation", "analyze.mesh_field", "create_distance",
        "ExactAcyclicGmshRefinementFieldGraphAndGeometry", "document",
        source_command_id="FEM_MeshAdvanced",
    ),
    _action(
        "VibeCAD_AnalyzeCreateMeshResult", ("analyze",), ("task_panel",),
        "mutation", "analyze.mesh_field", "create_result",
        "ExactAcyclicGmshRefinementFieldGraphAndGeometry", "document",
        source_command_id="FEM_MeshAdvanced",
    ),
    *(
        _action(
            action_id, ("analyze",), ("task_panel",),
            "mutation", "analyze.mesh_field", operation,
            "ExactAcyclicGmshRefinementFieldGraphAndGeometry", "document",
            source_command_id=source,
        )
        for action_id, operation, source in (
            ("VibeCAD_AnalyzeUpdateMeshRestrict", "update_restrict", "FEM_MeshManipulate"),
            ("VibeCAD_AnalyzeUpdateMeshThreshold", "update_threshold", "FEM_MeshManipulate"),
            ("VibeCAD_AnalyzeUpdateMeshMean", "update_mean", "FEM_MeshManipulate"),
            ("VibeCAD_AnalyzeUpdateMeshGradient", "update_gradient", "FEM_MeshManipulate"),
            ("VibeCAD_AnalyzeUpdateMeshCurvature", "update_curvature", "FEM_MeshManipulate"),
            ("VibeCAD_AnalyzeUpdateMeshLaplacian", "update_laplacian", "FEM_MeshManipulate"),
            ("VibeCAD_AnalyzeUpdateMeshAttractorAnisoCurve", "update_attractor_aniso_curve", "FEM_MeshAdvanced"),
            ("VibeCAD_AnalyzeUpdateMeshMathEval", "update_math_eval", "FEM_MeshAdvanced"),
            ("VibeCAD_AnalyzeUpdateMeshMathEvalAniso", "update_math_eval_aniso", "FEM_MeshAdvanced"),
            ("VibeCAD_AnalyzeUpdateMeshFieldDistance", "update_distance", "FEM_MeshAdvanced"),
            ("VibeCAD_AnalyzeUpdateMeshResult", "update_result", "FEM_MeshAdvanced"),
        )
    ),
    _action(
        "VibeCAD_AnalyzeUpdateTransfiniteCurve", ("analyze",), ("task_panel",),
        "mutation", "analyze.structured_mesh", "update_transfinite_curve",
        "ExactGmshTransfiniteCurveAndEdges", "document",
        source_command_id="FEM_MeshTransfiniteCurve",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateTransfiniteSurface", ("analyze",), ("task_panel",),
        "mutation", "analyze.structured_mesh", "update_transfinite_surface",
        "ExactGmshTransfiniteSurfaceAndGeometry", "document",
        source_command_id="FEM_MeshTransfiniteSurface",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateTransfiniteVolume", ("analyze",), ("task_panel",),
        "mutation", "analyze.structured_mesh", "update_transfinite_volume",
        "ExactGmshTransfiniteVolumeAndSolids", "document",
        source_command_id="FEM_MeshTransfiniteVolume",
    ),
    _action(
        "VibeCAD_AnalyzeReadFemMeshElements", ("analyze",), ("task_panel",),
        "read", "analyze.inspect", "fem_mesh_elements",
        "ExactActiveFemMeshContentAndHistory", "none",
        source_command_id="FEM_CreateElementsSet",
    ),
    _action(
        "VibeCAD_AnalyzeEraseMeshElements", ("analyze",), ("task_panel",),
        "mutation", "analyze.mesh_output", "erase_elements",
        "ExactActiveFemMeshContentAndHistory", "document",
        source_command_id="FEM_CreateElementsSet",
    ),
    _action(
        "VibeCAD_AnalyzeEraseMeshElementRanges", ("analyze",), ("task_panel",),
        "mutation", "analyze.mesh_output", "erase_element_ranges",
        "ExactActiveFemMeshContentAndHistory", "document",
        source_command_id="FEM_CreateElementsSet",
    ),
    _action(
        "VibeCAD_AnalyzeConvertFemMeshSurface", ("analyze",), ("task_panel",),
        "mutation", "analyze.mesh_output", "convert_surface",
        "ExactActiveFemMeshContentAndHistory", "document",
        source_command_id="FEM_FEMMesh2Mesh",
    ),
    _action(
        "VibeCAD_AnalyzeConvertDeformedFemMeshSurface", ("analyze",), ("task_panel",),
        "mutation", "analyze.mesh_output", "convert_deformed_surface",
        "ExactActiveFemMeshMechanicalDisplacementAndHistory", "document",
        source_command_id="FEM_FEMMesh2Mesh",
    ),
    _action(
        "VibeCAD_AnalyzeReadSolver", ("analyze",), ("task_panel",),
        "read", "analyze.inspect", "solver",
        "ExactFemSolverState", "none",
        source_command_id="FEM_SolverControl",
    ),
    _action(
        "VibeCAD_AnalyzeRunCurrentSolver", ("analyze",), ("task_panel",),
        "mutation", "analyze.run_solver", "run",
        "CurrentNamedFemSolver", "background",
        source_command_id="FEM_SolverRun", background_required=True,
    ),
    _action(
        "VibeCAD_AnalyzeUpdateCalculiXSolver", ("analyze",), ("task_panel",),
        "mutation", "analyze.solver_control", "update_calculix",
        "ExactFemSolverSettingsAndHistory", "document",
        source_command_id="FEM_SolverControl",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateElmerSolver", ("analyze",), ("task_panel",),
        "mutation", "analyze.solver_control", "update_elmer",
        "ExactFemSolverSettingsAndHistory", "document",
        source_command_id="FEM_SolverControl",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateOpenFOAMSolver", ("analyze",), ("task_panel",),
        "mutation", "analyze.solver_control", "update_openfoam",
        "ExactFemSolverSettingsAndHistory", "document",
        source_command_id="FEM_SolverControl",
    ),
    _action(
        "VibeCAD_AnalyzeUpdateZ88Solver", ("analyze",), ("task_panel",),
        "mutation", "analyze.solver_control", "update_z88",
        "ExactFemSolverSettingsAndHistory", "document",
        source_command_id="FEM_SolverControl",
    ),
    _action(
        "VibeCAD_AnalyzeReadEquation", ("analyze",), ("task_panel",),
        "read", "analyze.inspect", "equation",
        "ExactElmerEquationState", "none",
        source_command_id="FEM_SolverControl",
    ),
    _action(
        "VibeCAD_AnalyzeReadResult", ("analyze",), ("task_panel",),
        "read", "analyze.inspect", "result",
        "ExactFemResultOrPostState", "none",
        source_command_id="FEM_ResultShow",
    ),
    _action(
        "VibeCAD_AnalyzeReadOpenFOAMFlow", ("analyze",), ("task_panel",),
        "read", "analyze.flow_results", "read",
        "CurrentNamedOpenFOAMResult", "none",
        source_command_id="FEM_ResultShow",
    ),
    _action(
        "VibeCAD_AnalyzeMeasureOpenFOAMFlow", ("analyze",), ("task_panel",),
        "read", "analyze.flow_performance", "measure",
        "CurrentNamedOpenFOAMResultAndBoundaries", "none",
        source_command_id="FEM_ResultShow",
    ),
    _action(
        "VibeCAD_AnalyzeCompareOpenFOAMFlow", ("analyze",), ("task_panel",),
        "read", "analyze.compare_flow", "compare",
        "TwoCurrentNamedOpenFOAMResultsAndBoundaries", "none",
        source_command_id="FEM_ResultShow",
    ),
    _action(
        "VibeCAD_AnalyzeShowOpenFOAMFlow", ("analyze",), ("task_panel",),
        "view", "analyze.show_flow", "show",
        "CurrentNamedOpenFOAMResultPresentation", "presentation",
        source_command_id="FEM_PostApplyChanges",
    ),
    _action(
        "VibeCAD_AnalyzeReadMechanicalResult", ("analyze",), ("task_panel",),
        "read", "analyze.mechanical_results", "read",
        "CurrentNamedMechanicalResult", "none",
        source_command_id="FEM_ResultShow",
    ),
    _action(
        "VibeCAD_AnalyzeShowMechanicalResult", ("analyze",), ("task_panel",),
        "view", "analyze.show_mechanical", "show",
        "CurrentNamedMechanicalResult", "presentation",
        source_command_id="FEM_ResultShow",
    ),
    _action(
        "VibeCAD_AnalyzeReadTemperatureResult", ("analyze",), ("task_panel",),
        "read", "analyze.temperature_results", "read",
        "CurrentNamedTemperatureResult", "none",
        source_command_id="FEM_ResultShow",
    ),
    _action(
        "VibeCAD_AnalyzeShowTemperatureResult", ("analyze",), ("task_panel",),
        "view", "analyze.show_temperature", "show",
        "CurrentNamedTemperatureResult", "presentation",
        source_command_id="FEM_ResultShow",
    ),
    _action(
        "AssemblyContextToggleActive", ("assemble",), ("tree_context",),
        "human_only", "assembly.create", None, "Assembly::Assembly",
        "human", interactive=True,
    ),
    _action(
        "AssemblyContextMakeFlexible", ("assemble",), ("tree_context",),
        "mutation", "assembly.rigidity", "make_flexible",
        "HumanActiveAssemblyExactAssemblyLinkAndFrozenAssemblyState", "document",
    ),
    _action(
        "AssemblyContextMakeRigid", ("assemble",), ("tree_context",),
        "mutation", "assembly.rigidity", "make_rigid",
        "HumanActiveAssemblyExactAssemblyLinkAndFrozenAssemblyState", "document",
    ),
    _action(
        "Assembly_LinkSelectLinked", ("assemble",), ("tree_context", "menu"),
        "read", "assembly.linked_assembly", "linked_source",
        "HumanSelectionExactActiveAssemblyLink", "none",
        source_command_id="Assembly_LinkSelectLinked",
    ),
    _action(
        "Assembly_ExportASMT", ("assemble",), ("menu",),
        "export", "assembly.export", "asmt",
        "HumanActiveAssemblyAndAuthorizedOutputPath", "output",
        source_command_id="Assembly_ExportASMT",
    ),
    _action(
        "AssemblyContextPlaySimulation", ("assemble",), ("tree_context",),
        "view", "assembly.playback", "show",
        "ActiveAssemblySimulationAndFrozenState", "presentation",
        source_command_id="Assembly_EditHistoryOperation",
        interactive=True,
    ),
    _action(
        "AssemblySimulationSeek", ("assemble",), ("task_panel",),
        "view", "assembly.playback", "seek",
        "NativeOwnedAssemblyPlaybackAndTime", "presentation",
        interactive=True,
    ),
    _action(
        "AssemblySimulationStep", ("assemble",), ("task_panel",),
        "view", "assembly.playback", "step",
        "NativeOwnedAssemblyPlayback", "presentation",
        interactive=True,
    ),
    _action(
        "AssemblySimulationPlay", ("assemble",), ("task_panel",),
        "view", "assembly.playback", "play",
        "NativeOwnedAssemblyPlayback", "presentation",
        interactive=True,
    ),
    _action(
        "AssemblySimulationPause", ("assemble",), ("task_panel",),
        "view", "assembly.playback", "pause",
        "NativeOwnedAssemblyPlayback", "presentation",
        interactive=True,
    ),
    _action(
        "AssemblySimulationClose", ("assemble",), ("task_panel",),
        "view", "assembly.playback", "close",
        "NativeOwnedAssemblyPlayback", "presentation",
        interactive=True,
    ),
    _action(
        "VibeCAD_ManufactureReadJob", ("manufacture",), ("task_panel",),
        "read", "manufacture.read_setup", "read_job",
        "ExactCamJobGraphAndState", "none",
        source_command_id="CAM_Job",
    ),
    _action(
        "VibeCAD_ManufactureReadThreadCatalog", ("manufacture",), ("task_panel",),
        "read", "manufacture.threads", "read_thread_catalog",
        "ShippedCamThreadCatalog", "none",
        source_command_id="CAM_ThreadMilling",
    ),
    _action(
        "VibeCAD_ManufactureListTools", ("manufacture",), ("task_panel",),
        "read", "manufacture.tool_catalog", "list_tools",
        "ExactCamToolCatalogState", "none",
        source_command_id="CAM_ToolBitDock",
    ),
    _action(
        "VibeCAD_ManufactureReadTool", ("manufacture",), ("task_panel",),
        "read", "manufacture.tool_catalog", "read_tool",
        "ExactCamCatalogToolDefinition", "none",
        source_command_id="CAM_ToolBitDock",
    ),
    _action(
        "VibeCAD_ManufactureUpdateController", ("manufacture",), ("task_panel",),
        "mutation", "manufacture.set_controller", "update_controller",
        "ExactCamToolControllerState", "document",
        source_command_id="CAM_ToolBitDock",
    ),
    _action(
        "VibeCAD_ManufactureUpdateToolBit", ("manufacture",), ("task_panel",),
        "mutation", "manufacture.update_tool", "update_tool_bit",
        "ExactCamToolBitState", "document",
        source_command_id="CAM_ToolBitDock",
    ),
    _action(
        "CAMSimulationClose", ("manufacture",), ("task_panel",),
        "view", "manufacture.close_simulation", "close",
        "NativeOwnedCamSimulation", "presentation", interactive=True,
    ),
    _action(
        "CAM_ExportTemplate", ("manufacture",), ("workbench_context", "menu"),
        "export", "manufacture.template", "export_template",
        "ExactCamJobTemplateContentAndHumanAuthorizedOutput", "output",
        source_command_id="CAM_ExportTemplate",
    ),
    _action(
        "CAM_SetStartPoint", ("manufacture",), ("workbench_context",),
        "mutation", "manufacture.start_point", "set_start_point",
        "ExactCamJobOperationAndPlanarStartPoint", "document",
        source_command_id="CAM_SetStartPoint",
    ),
    _action(
        "CAM_ToolBitSave", ("manufacture",), ("workbench_context",),
        "export", "manufacture.tool_output", "save",
        "ExactCamToolBitAndAuthorizedOutputPath", "output",
        source_command_id="CAM_ToolBitSave",
    ),
    _action(
        "CAM_ToolBitSaveAs", ("manufacture",), ("workbench_context",),
        "export", "manufacture.tool_output", "save_as",
        "ExactCamToolBitAndAuthorizedOutputPath", "output",
        source_command_id="CAM_ToolBitSaveAs",
    ),
    _action(
        "TechDrawContextEditBalloon", ("drawing",), ("tree_context",),
        "human_only", "drawing.balloon", None,
        "TechDraw::DrawViewBalloon", "human", interactive=True,
    ),
    _action(
        "TechDrawContextEditDimension", ("drawing",), ("tree_context",),
        "human_only", "drawing.edit_dimension", None,
        "TechDraw::DrawViewDimension", "human", interactive=True,
    ),
    _action(
        "TechDrawContextShowDrawing", ("drawing",), ("tree_context",),
        "view", "drawing.show_page", "show", "TechDraw::DrawPage", "presentation",
    ),
    _action(
        "TechDrawContextToggleKeepUpdated", ("drawing",),
        ("tree_context", "drawing_canvas_context"), "mutation", "drawing.page_updates",
        "set_keep_updated", "ExactDrawingPageAndUpdatePolicyState", "document",
    ),
    _action(
        "TechDrawContextToggleFrames", ("drawing",), ("drawing_canvas_context",),
        "view", "drawing.page_frames", "set_visibility",
        "HumanActiveDrawingPageAndExactFrameVisibilityState", "presentation",
    ),
    _action(
        "TechDrawContextToggleGrid", ("drawing",), ("drawing_canvas_context",),
        "view", "drawing.page_grid", "set_visibility",
        "HumanActiveDrawingPageAndExactGridVisibilityState", "presentation",
    ),
    _action(
        "TechDrawContextExportSVG", ("drawing",), ("drawing_canvas_context",),
        "export", "drawing.export", "svg", "TechDraw::DrawPage",
        "background_output", background_required=True,
    ),
    _action(
        "TechDrawContextExportDXF", ("drawing",), ("drawing_canvas_context",),
        "export", "drawing.export", "dxf", "TechDraw::DrawPage",
        "background_output", background_required=True,
    ),
    _action(
        "TechDrawContextExportPDF", ("drawing",), ("drawing_canvas_context",),
        "export", "drawing.export", "pdf", "TechDraw::DrawPage",
        "background_output", background_required=True,
    ),
    _action(
        "TechDrawContextPrintAll", ("drawing",), ("drawing_canvas_context",),
        "export", "drawing.export", "print_all", "App::Document",
        "background_output", background_required=True,
    ),
    _action(
        "InspectionContextAnnotation", _INSPECTION_SURFACES,
        ("inspection_view_context",), "human_only", "inspect.interactive", None,
        "Inspection::Session", "human", interactive=True,
    ),
    _action(
        "InspectionContextLeaveInfoMode", _INSPECTION_SURFACES,
        ("inspection_view_context",), "human_only", "inspect.interactive", None,
        "Inspection::Session", "human", interactive=True,
    ),
)

if len({action.action_id for action in NATIVE_CONTEXT_ACTIONS}) != len(NATIVE_CONTEXT_ACTIONS):
    raise NativeContextManifestError("Native context-action IDs must be unique.")


def context_actions_for_surface(surface_id: str) -> tuple[NativeContextActionPlan, ...]:
    """Return classified context actions for one human-selected surface."""

    if surface_id not in SURFACE_IDS or surface_id == "unavailable":
        raise NativeContextManifestError(f"Unknown Native surface {surface_id!r}.")
    return tuple(
        action for action in NATIVE_CONTEXT_ACTIONS if surface_id in action.surface_ids
    )


def provider_context_actions_for_surface(
    surface_id: str,
) -> tuple[NativeContextActionPlan, ...]:
    """Return only context-equivalent operations the provider may receive."""

    return tuple(
        action
        for action in context_actions_for_surface(surface_id)
        if not action.classification.human_only
    )
