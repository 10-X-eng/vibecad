# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact conditional action graphs for Native Analyze, Manufacture, and Drawing.

The ribbon controller remains the live authority.  These variants constrain
that live graph to combinations the shipped FEM and CAM workbenches can
actually produce; accepting the union of conditional command IDs would also
accept impossible and unsafe mixtures.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Iterable, Mapping, Sequence

from VibeCADRibbonSurface import RibbonSurface, RibbonSurfaceEnvironment


class NativeSurfaceVariantError(RuntimeError):
    """A conditional live graph is not one the shipped ribbon can produce."""


@dataclass(frozen=True, slots=True)
class NativeSurfaceVariant:
    """One exact ordered group and composite graph."""

    name: str
    groups: tuple[tuple[str, tuple[str, ...]], ...]
    composites: tuple[tuple[str, tuple[str, ...]], ...]

    @property
    def command_ids(self) -> tuple[str, ...]:
        return tuple(
            command_id
            for _label, command_ids in self.groups
            for command_id in command_ids
        )

    @property
    def composite_map(self) -> dict[str, tuple[str, ...]]:
        return dict(self.composites)


_ANALYZE_GROUP_ANCHORS = (
    ("View", "Std_ViewFitAll"),
    ("Model", "VibeCAD_AnalyzeStudySetup"),
    ("Electromagnetics", "FEM_CompEmConstraints"),
    ("Fluids", "FEM_ConstraintFluidBoundary"),
    ("Geometry", "FEM_ConstraintTransform"),
    ("Mechanics", "FEM_ConstraintFixed"),
    ("Thermal", "FEM_ConstraintTemperature"),
    ("Mesh", "FEM_MeshGmshFromShape"),
    ("Solve", "FEM_CompSolvers"),
    ("Results", "FEM_ResultShow"),
    ("Utilities", "FEM_Examples"),
    ("Inspect", "Std_Measure"),
)

_ANALYZE_VTK_COMMANDS = frozenset(
    {
        "FEM_PostApplyChanges",
        "FEM_PostPipelineFromResult",
        "FEM_PostBranchFilter",
        "FEM_PostFilterWarp",
        "FEM_PostFilterClipScalar",
        "FEM_PostFilterCutFunction",
        "FEM_PostFilterClipRegion",
        "FEM_PostFilterContours",
        "FEM_PostFilterDataAlongLine",
        "FEM_PostFilterLinearizedStresses",
        "FEM_PostFilterDataAtPoint",
        "FEM_PostFilterCalculator",
        "FEM_PostCreateFunctions",
        "FEM_PostCreateFunctionPlane",
        "FEM_PostCreateFunctionSphere",
        "FEM_PostCreateFunctionCylinder",
        "FEM_PostCreateFunctionBox",
    }
)

_ANALYZE_VTK_PYTHON_COMMANDS = frozenset(
    {
        "FEM_PostFilterGlyph",
        "FEM_PostVisualization",
        "FEM_PostVisualizationLineplot",
        "FEM_PostVisualizationHistogram",
        "FEM_PostVisualizationTable",
    }
)

_ANALYZE_BASE_COMPOSITES = {
    "FEM_CompEmConstraints": (
        "FEM_ConstraintElectromagnetic",
        "FEM_ConstraintCurrentDensity",
        "FEM_ConstraintMagnetization",
        "FEM_ConstraintElectricChargeDensity",
    ),
    "FEM_MeshGMSHRefinement": (
        "FEM_MeshDistance",
        "FEM_MeshBoundaryLayer",
        "FEM_MeshShape",
        "FEM_MeshManipulate",
        "FEM_MeshAdvanced",
        "FEM_MeshTransfiniteCurve",
        "FEM_MeshTransfiniteSurface",
        "FEM_MeshTransfiniteVolume",
    ),
    "FEM_CompSolvers": (
        "FEM_SolverCalculiX",
        "FEM_SolverElmer",
        "FEM_SolverMystran",
        "FEM_SolverZ88",
        "FEM_SolverOpenFOAM",
    ),
    "FEM_CompMechEquations": (
        "FEM_EquationElasticity",
        "FEM_EquationDeformation",
    ),
    "FEM_CompEmEquations": (
        "FEM_EquationElectrostatic",
        "FEM_EquationElectricforce",
        "FEM_EquationMagnetodynamic",
        "FEM_EquationMagnetodynamic2D",
        "FEM_EquationStaticCurrent",
    ),
}

_ANALYZE_VTK_COMPOSITE = (
    "FEM_PostCreateFunctions",
    (
        "FEM_PostCreateFunctionPlane",
        "FEM_PostCreateFunctionSphere",
        "FEM_PostCreateFunctionCylinder",
        "FEM_PostCreateFunctionBox",
    ),
)

_ANALYZE_VTK_PYTHON_COMPOSITE = (
    "FEM_PostVisualization",
    (
        "FEM_PostVisualizationLineplot",
        "FEM_PostVisualizationHistogram",
        "FEM_PostVisualizationTable",
    ),
)

_MANUFACTURE_GROUP_ANCHORS = (
    ("View", "Std_ViewFitAll"),
    ("Setup", "CAM_Job"),
    ("Tools", "CAM_SimTools"),
    ("Program", "CAM_Comment"),
    ("Operations", "CAM_Profile"),
    ("Modify", "CAM_OperationCopy"),
    ("Robot", "Robot_Edge2Trac"),
    ("Export", "Robot_ExportKukaCompact"),
    ("Inspect", "Std_Measure"),
)

_MANUFACTURE_BASE_COMPOSITES = {
    "CAM_PostTools": ("CAM_Post", "CAM_PostSelected"),
    "CAM_DrillingTools": ("CAM_Drilling", "CAM_ThreadMilling"),
    "CAM_EngraveTools": ("CAM_Engrave", "CAM_Deburr", "CAM_Vcarve"),
    "CAM_DressupTools": (
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
    ),
}

_DRAWING_GROUP_ANCHORS = (
    ("View", "Std_ViewFitAll"),
    ("Pages", "TechDraw_PageDefault"),
    ("Views", "TechDraw_View"),
    ("Stacking", "TechDraw_StackGroup"),
    ("Dimensions", "TechDraw_CompDimensionTools"),
    ("Attributes", "TechDraw_ExtensionSelectLineAttributes"),
    ("Centerlines", "TechDraw_ExtensionCircleCenterLinesGroup"),
    ("Extend", "TechDraw_ExtensionInsertPrefixGroup"),
    ("Files", "TechDraw_ExportPageSVG"),
    ("Decoration", "TechDraw_ToggleFrame"),
    ("Annotation", "TechDraw_RichTextAnnotation"),
    ("Inspect", "Std_Measure"),
)

_DRAWING_COMBINED_DIMENSION_CHILDREN = (
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
)

_DRAWING_BASE_COMPOSITES = {
    "TechDraw_SectionGroup": (
        "TechDraw_SectionView",
        "TechDraw_ComplexSection",
    ),
    "TechDraw_StackGroup": (
        "TechDraw_StackTop",
        "TechDraw_StackBottom",
        "TechDraw_StackUp",
        "TechDraw_StackDown",
    ),
    "TechDraw_CompDimensionTools": _DRAWING_COMBINED_DIMENSION_CHILDREN,
    "TechDraw_ExtensionExtendShortenLineGroup": (
        "TechDraw_ExtensionExtendLine",
        "TechDraw_ExtensionShortenLine",
    ),
    "TechDraw_ExtensionCircleCenterLinesGroup": (
        "TechDraw_ExtensionCircleCenterLines",
        "TechDraw_ExtensionHoleCircle",
    ),
    "TechDraw_ExtensionThreadsGroup": (
        "TechDraw_ExtensionThreadHoleSide",
        "TechDraw_ExtensionThreadHoleBottom",
        "TechDraw_ExtensionThreadBoltSide",
        "TechDraw_ExtensionThreadBoltBottom",
    ),
    "TechDraw_CommandVertexCreationGroup": (
        "TechDraw_ExtensionVertexAtIntersection",
        "TechDraw_CommandAddOffsetVertex",
    ),
    "TechDraw_ExtensionDrawCirclesGroup": (
        "TechDraw_CosmeticCircle",
        "TechDraw_ExtensionDrawCosmCircle",
        "TechDraw_ExtensionDrawCosmCircle3Points",
        "TechDraw_ExtensionDrawCosmArc",
    ),
    "TechDraw_ExtensionLinePPGroup": (
        "TechDraw_ExtensionLineParallel",
        "TechDraw_ExtensionLinePerpendicular",
    ),
    "TechDraw_ExtensionInsertPrefixGroup": (
        "TechDraw_ExtensionInsertDiameter",
        "TechDraw_ExtensionInsertSquare",
        "TechDraw_ExtensionInsertRepetition",
        "TechDraw_ExtensionRemovePrefixChar",
    ),
    "TechDraw_ExtensionIncreaseDecreaseGroup": (
        "TechDraw_ExtensionIncreaseDecimal",
        "TechDraw_ExtensionDecreaseDecimal",
    ),
    "TechDraw_CosmeticVertexGroup": (
        "TechDraw_CosmeticVertex",
        "TechDraw_Midpoints",
        "TechDraw_Quadrants",
    ),
    "TechDraw_CenterLineGroup": (
        "TechDraw_FaceCenterLine",
        "TechDraw_2LineCenterLine",
        "TechDraw_2PointCenterLine",
    ),
}

_DRAWING_SEPARATED_COMPOSITES = {
    "TechDraw_ExtentGroup": (
        "TechDraw_HorizontalExtentDimension",
        "TechDraw_VerticalExtentDimension",
    ),
    "TechDraw_ExtensionCreateChainDimensionGroup": (
        "TechDraw_ExtensionCreateHorizChainDimension",
        "TechDraw_ExtensionCreateVertChainDimension",
        "TechDraw_ExtensionCreateObliqueChainDimension",
    ),
    "TechDraw_ExtensionCreateCoordDimensionGroup": (
        "TechDraw_ExtensionCreateHorizCoordDimension",
        "TechDraw_ExtensionCreateVertCoordDimension",
        "TechDraw_ExtensionCreateObliqueCoordDimension",
    ),
    "TechDraw_ExtensionChamferDimensionGroup": (
        "TechDraw_ExtensionCreateHorizChamferDimension",
        "TechDraw_ExtensionCreateVertChamferDimension",
    ),
}

_DRAWING_SEPARATED_DIMENSION_LEAVES = (
    "TechDraw_LengthDimension",
    "TechDraw_HorizontalDimension",
    "TechDraw_VerticalDimension",
    "TechDraw_RadiusDimension",
    "TechDraw_DiameterDimension",
    "TechDraw_AngleDimension",
    "TechDraw_3PtAngleDimension",
    "TechDraw_AreaDimension",
)

_DRAWING_DIMENSION_TAIL = (
    "TechDraw_Balloon",
    "TechDraw_AxoLengthDimension",
    "TechDraw_DimensionRepair",
)


def _ordered_values(
    values: Sequence[tuple[str, bool]],
    *,
    field: str,
) -> dict[str, bool]:
    result = dict(values)
    if len(result) != len(values):
        raise NativeSurfaceVariantError(f"{field} contains duplicate keys.")
    return result


def _environment_values(
    environment: RibbonSurfaceEnvironment,
) -> tuple[dict[str, bool], dict[str, bool]]:
    if not isinstance(environment, RibbonSurfaceEnvironment):
        raise TypeError("environment must be a RibbonSurfaceEnvironment")
    return (
        _ordered_values(environment.build_features, field="build_features"),
        _ordered_values(environment.preferences, field="preferences"),
    )


def _partition_baseline(
    baseline: Sequence[str],
    anchors: Sequence[tuple[str, str]],
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    command_ids = tuple(baseline)
    positions = []
    for label, anchor in anchors:
        try:
            positions.append((label, command_ids.index(anchor)))
        except ValueError as exc:
            raise NativeSurfaceVariantError(
                f"Conditional surface baseline is missing anchor {anchor!r}."
            ) from exc
    indexes = tuple(position for _label, position in positions)
    if not indexes or indexes[0] != 0 or indexes != tuple(sorted(set(indexes))):
        raise NativeSurfaceVariantError(
            "Conditional surface baseline anchors are not strictly ordered."
        )
    groups = tuple(
        (
            label,
            command_ids[start : indexes[index + 1] if index + 1 < len(indexes) else None],
        )
        for index, (label, start) in enumerate(positions)
    )
    if tuple(
        command_id
        for _label, group_commands in groups
        for command_id in group_commands
    ) != command_ids:
        raise NativeSurfaceVariantError(
            "Conditional surface baseline partition changed command order."
        )
    return groups


def _replace_group(
    groups: Sequence[tuple[str, tuple[str, ...]]],
    label: str,
    command_ids: Iterable[str],
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    replacement = tuple(command_ids)
    if sum(group_label == label for group_label, _commands in groups) != 1:
        raise NativeSurfaceVariantError(
            f"Conditional surface baseline does not contain one {label!r} group."
        )
    return tuple(
        (group_label, replacement if group_label == label else commands)
        for group_label, commands in groups
    )


def _remove_groups(
    groups: Sequence[tuple[str, tuple[str, ...]]],
    labels: Iterable[str],
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    removed = frozenset(labels)
    return tuple(group for group in groups if group[0] not in removed)


def _insert_group_before(
    groups: Sequence[tuple[str, tuple[str, ...]]],
    before: str,
    group: tuple[str, tuple[str, ...]],
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    result = []
    inserted = False
    for current in groups:
        if current[0] == before:
            result.append(group)
            inserted = True
        result.append(current)
    if not inserted:
        raise NativeSurfaceVariantError(
            f"Conditional surface baseline is missing group {before!r}."
        )
    return tuple(result)


def analyze_surface_variant(
    baseline: Sequence[str],
    environment: RibbonSurfaceEnvironment,
) -> NativeSurfaceVariant:
    """Return the sole valid Analyze graph for one compiled environment."""

    features, preferences = _environment_values(environment)
    if preferences:
        raise NativeSurfaceVariantError(
            "Analyze does not have ribbon-shaping preferences."
        )
    if not features["fem"]:
        raise NativeSurfaceVariantError(
            "The Analyze surface cannot exist when FEM is not compiled."
        )
    vtk = features["fem_vtk"]
    vtk_python = features["fem_vtk_python"]
    if vtk_python and not vtk:
        raise NativeSurfaceVariantError(
            "FEM VTK Python support requires compiled FEM VTK support."
        )

    groups = _partition_baseline(baseline, _ANALYZE_GROUP_ANCHORS)
    maximum_results = dict(groups)["Results"]
    excluded = set()
    if not vtk:
        excluded.update(_ANALYZE_VTK_COMMANDS)
    if not vtk_python:
        excluded.update(_ANALYZE_VTK_PYTHON_COMMANDS)
    groups = _replace_group(
        groups,
        "Results",
        (command_id for command_id in maximum_results if command_id not in excluded),
    )

    composites = dict(_ANALYZE_BASE_COMPOSITES)
    if vtk:
        composites[_ANALYZE_VTK_COMPOSITE[0]] = _ANALYZE_VTK_COMPOSITE[1]
    if vtk_python:
        composites[_ANALYZE_VTK_PYTHON_COMPOSITE[0]] = (
            _ANALYZE_VTK_PYTHON_COMPOSITE[1]
        )
    return NativeSurfaceVariant(
        name=(
            "analyze:"
            f"vtk={int(vtk)},vtk_python={int(vtk_python)},"
            f"netgen={int(features['fem_netgen'])}"
        ),
        groups=groups,
        composites=tuple(composites.items()),
    )


def manufacture_surface_variants(
    baseline: Sequence[str],
    environment: RibbonSurfaceEnvironment,
) -> tuple[NativeSurfaceVariant, ...]:
    """Return every valid runtime-optional CAM graph for one preference state."""

    features, preferences = _environment_values(environment)
    if not features["cam"]:
        raise NativeSurfaceVariantError(
            "The Manufacture surface cannot exist when CAM is not compiled."
        )
    expected_preferences = {
        "cam.default_simulator_legacy",
        "cam.enable_advanced_ocl_features",
        "cam.enable_experimental_features",
    }
    if set(preferences) != expected_preferences:
        raise NativeSurfaceVariantError(
            "Manufacture preferences do not match the exact supported set."
        )

    legacy = preferences["cam.default_simulator_legacy"]
    advanced = preferences["cam.enable_advanced_ocl_features"]
    experimental = preferences["cam.enable_experimental_features"]
    runtime_options = (
        tuple(product((False, True), repeat=2))
        if advanced
        else ((False, False),)
    )
    result = []
    for ocl_available, camotics_available in runtime_options:
        groups = _partition_baseline(baseline, _MANUFACTURE_GROUP_ANCHORS)
        tools = list(dict(groups)["Tools"])
        simulator_children = (
            ("CAM_Simulator", "CAM_SimulatorGL")
            if legacy
            else ("CAM_SimulatorGL", "CAM_Simulator")
        ) + ("CAM_RetainSimulationResult",)
        simulator_index = tools.index("CAM_SimTools")
        tools[simulator_index + 1 : simulator_index + 4] = simulator_children
        if camotics_available:
            tools.insert(tools.index("CAM_ToolBitDock") + 1, "CAM_Camotics")
        groups = _replace_group(groups, "Tools", tools)

        composites = dict(_MANUFACTURE_BASE_COMPOSITES)
        composites["CAM_SimTools"] = simulator_children
        if ocl_available:
            operations = list(dict(groups)["Operations"])
            pocket_index = operations.index("CAM_Pocket3D")
            advanced_children = [
                "CAM_Pocket3D",
                "CAM_Surface",
                "CAM_Waterline",
            ]
            if experimental:
                advanced_children.append("CAM_RotarySurface")
            operations[pocket_index : pocket_index + 1] = (
                "CAM_3dTools",
                *advanced_children,
            )
            groups = _replace_group(groups, "Operations", operations)
            composites["CAM_3dTools"] = tuple(advanced_children)

        if experimental:
            groups = _insert_group_before(
                groups,
                "Robot",
                ("Area", ("CAM_Area", "CAM_Area_Workplane")),
            )
        if not features["robot"]:
            groups = _remove_groups(groups, ("Robot", "Export"))

        result.append(
            NativeSurfaceVariant(
                name=(
                    "manufacture:"
                    f"legacy={int(legacy)},advanced={int(advanced)},"
                    f"experimental={int(experimental)},robot={int(features['robot'])},"
                    f"ocl={int(ocl_available)},camotics={int(camotics_available)}"
                ),
                groups=groups,
                composites=tuple(composites.items()),
            )
        )
    return tuple(result)


def drawing_surface_variant(
    baseline: Sequence[str],
    environment: RibbonSurfaceEnvironment,
) -> NativeSurfaceVariant:
    """Return the sole valid Drawing graph for one dimension preference state."""

    features, preferences = _environment_values(environment)
    if not features["techdraw"]:
        raise NativeSurfaceVariantError(
            "The Drawing surface cannot exist when TechDraw is not compiled."
        )
    expected_preferences = {
        "techdraw.separated_dimensioning_tools",
        "techdraw.single_dimensioning_tool",
    }
    if set(preferences) != expected_preferences:
        raise NativeSurfaceVariantError(
            "Drawing preferences do not match the exact supported set."
        )

    separated = preferences["techdraw.separated_dimensioning_tools"]
    single = preferences["techdraw.single_dimensioning_tool"]
    groups = _partition_baseline(baseline, _DRAWING_GROUP_ANCHORS)
    composites = dict(_DRAWING_BASE_COMPOSITES)

    if separated:
        dimensions = (
            *(("TechDraw_Dimension",) if single else ()),
            *_DRAWING_SEPARATED_DIMENSION_LEAVES,
            "TechDraw_ExtentGroup",
            *_DRAWING_SEPARATED_COMPOSITES["TechDraw_ExtentGroup"],
            *_DRAWING_DIMENSION_TAIL,
        )
        groups = _replace_group(groups, "Dimensions", dimensions)

        attributes = list(dict(groups)["Attributes"])
        customize_index = attributes.index("TechDraw_ExtensionCustomizeFormat")
        attributes[customize_index:customize_index] = (
            "TechDraw_ExtensionAreaAnnotation",
            "TechDraw_ExtensionArcLengthAnnotation",
        )
        groups = _replace_group(groups, "Attributes", attributes)

        separated_extend = (
            "TechDraw_ExtensionCreateChainDimensionGroup",
            *_DRAWING_SEPARATED_COMPOSITES[
                "TechDraw_ExtensionCreateChainDimensionGroup"
            ],
            "TechDraw_ExtensionCreateCoordDimensionGroup",
            *_DRAWING_SEPARATED_COMPOSITES[
                "TechDraw_ExtensionCreateCoordDimensionGroup"
            ],
            "TechDraw_ExtensionChamferDimensionGroup",
            *_DRAWING_SEPARATED_COMPOSITES[
                "TechDraw_ExtensionChamferDimensionGroup"
            ],
            "TechDraw_ExtensionCreateLengthArc",
            *dict(groups)["Extend"],
        )
        groups = _replace_group(groups, "Extend", separated_extend)
        composites.pop("TechDraw_CompDimensionTools")
        composites.update(_DRAWING_SEPARATED_COMPOSITES)
    elif not single:
        groups = _replace_group(groups, "Dimensions", _DRAWING_DIMENSION_TAIL)
        composites.pop("TechDraw_CompDimensionTools")

    return NativeSurfaceVariant(
        name=(
            "drawing:"
            f"separated={int(separated)},single={int(single)}"
        ),
        groups=groups,
        composites=tuple(composites.items()),
    )


def expected_surface_variants(
    surface_id: str,
    baseline: Sequence[str],
    environment: RibbonSurfaceEnvironment,
) -> tuple[NativeSurfaceVariant, ...]:
    """Resolve exact conditional variants for a supported surface."""

    if surface_id == "analyze":
        return (analyze_surface_variant(baseline, environment),)
    if surface_id == "manufacture":
        return manufacture_surface_variants(baseline, environment)
    if surface_id == "drawing":
        return (drawing_surface_variant(baseline, environment),)
    return ()


def _surface_graph(
    surface: RibbonSurface,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple(
        (
            group.label,
            tuple(
                flattened.command_id
                for action in group.actions
                for flattened in action.flattened()
            ),
        )
        for group in surface.groups
    )


def _surface_composites(
    surface: RibbonSurface,
) -> dict[str, tuple[str, ...]]:
    return {
        action.command_id: tuple(child.command_id for child in action.children)
        for group in surface.groups
        for action in group.actions
        if action.children
    }


def validate_surface_variant(
    surface: RibbonSurface,
    baseline: Sequence[str],
) -> NativeSurfaceVariant | None:
    """Return the exact matching conditional variant or reject the graph."""

    if not isinstance(surface, RibbonSurface):
        raise TypeError("surface must be a RibbonSurface")
    environment = surface.environment
    if environment is None:  # pragma: no cover - RibbonSurface guards this
        raise NativeSurfaceVariantError("Ribbon surface environment is unavailable.")
    variants = expected_surface_variants(
        surface.surface_id,
        baseline,
        environment,
    )
    if not variants:
        return None
    graph = _surface_graph(surface)
    composites = _surface_composites(surface)
    for variant in variants:
        if graph == variant.groups and composites == variant.composite_map:
            return variant
    names = tuple(variant.name for variant in variants)
    raise NativeSurfaceVariantError(
        f"Ribbon surface {surface.surface_id!r} does not match its exact "
        f"compiled/preference graph; allowed variants are {names!r}."
    )


def variant_graph_mapping(
    variant: NativeSurfaceVariant,
) -> Mapping[str, tuple[str, ...]]:
    """Expose an ordered-label mapping for concise tests and diagnostics."""

    if not isinstance(variant, NativeSurfaceVariant):
        raise TypeError("variant must be a NativeSurfaceVariant")
    return dict(variant.groups)
