# SPDX-License-Identifier: LGPL-2.1-or-later

"""Workbench-specific VibeCAD tool-surface metadata.

A workbench lists provider tools only after that surface has a complete,
native, exact-target implementation. Each pack owns its complete provider
surface; tools from adjacent workbenches are never injected. Legacy
FreeCAD-command wrappers are never exposed; every listed tool is an AI-native implementation. Long-tail
workbenches expose a read tool only when native object identity is available.
TestWorkbench and NoneWorkbench intentionally list no tools.
"""

from __future__ import annotations

from dataclasses import dataclass, field


SKETCHER_PACK_TOOL_NAMES: tuple[str, ...] = (
    "sketcher.close_sketch",
    "sketcher.draw_rectangle",
    "sketcher.add_polyline",
    "sketcher.add_arc",
    "sketcher.add_circle",
    "sketcher.add_ellipse",
    "sketcher.add_spline",
    "sketcher.add_hole_pattern",
    "sketcher.add_slot",
    "sketcher.measure",
    "sketcher.constrain",
    "sketcher.edit_constraint",
    "sketcher.move_point",
    "sketcher.translate_geometry",
    "sketcher.modify_geometry",
    "sketcher.add_external_geometry",
    "sketcher.remove_external_geometry",
    "sketcher.delete_items",
    "sketcher.set_construction",
)

PARTDESIGN_PACK_TOOL_NAMES: tuple[str, ...] = (
    "partdesign.find_subelements",
    "partdesign.measure",
    "partdesign.create_body",
    "partdesign.create_sketch",
    "partdesign.edit_sketch",
    "partdesign.create_datum_plane",
    "partdesign.create_datum_axis",
    "partdesign.create_datum_point",
    "partdesign.create_shape_binder",
    "partdesign.create_subshape_binder",
    "partdesign.pad",
    "partdesign.pocket",
    "partdesign.hole",
    "partdesign.revolution",
    "partdesign.groove",
    "partdesign.additive_loft",
    "partdesign.thin_loft",
    "partdesign.subtractive_loft",
    "partdesign.additive_pipe",
    "partdesign.subtractive_pipe",
    "partdesign.additive_helix",
    "partdesign.subtractive_helix",
    "partdesign.linear_pattern",
    "partdesign.polar_pattern",
    "partdesign.mirror",
    "partdesign.multi_transform",
    "partdesign.fillet",
    "partdesign.chamfer",
    "partdesign.draft",
    "partdesign.thickness",
    "partdesign.boolean",
    "partdesign.set_tip",
)

# Compatibility export retained for callers that imported the old metadata.
# Exact surface resolution deliberately does not borrow the Sketcher pack:
# entering Sketcher activates SketcherWorkbench and its own native surface.
PARTDESIGN_REQUIRED_ADJACENT_TOOL_NAMES: tuple[str, ...] = ()

PART_PACK_TOOL_NAMES: tuple[str, ...] = (
    "part.find_subelements",
    "part.measure",
    "part.boolean",
    "part.extrude",
    "part.revolve",
    "part.mirror",
    "part.fillet",
    "part.chamfer",
)

DRAFT_PACK_TOOL_NAMES: tuple[str, ...] = (
    "draft.list_objects",
    "draft.create_wire",
    "draft.create_circle",
    "draft.create_rectangle",
    "draft.create_bspline",
    "draft.create_array",
    "draft.create_text",
)

SPREADSHEET_PACK_TOOL_NAMES: tuple[str, ...] = (
    "spreadsheet.create_sheet",
    "spreadsheet.set_cells",
    "spreadsheet.read_sheet",
)

SURFACE_PACK_TOOL_NAMES: tuple[str, ...] = (
    "surface.fill",
    "surface.loft",
    "surface.blend",
    "surface.extend",
    "surface.thicken",
)

ASSEMBLY_PACK_TOOL_NAMES: tuple[str, ...] = (
    "assembly.list_structure",
    "assembly.create_assembly",
    "assembly.insert_component",
    "assembly.ground_component",
    "assembly.create_joint",
    "assembly.solve",
)

BIM_PACK_TOOL_NAMES: tuple[str, ...] = (
    "bim.list_structure",
    "bim.create_spatial_structure",
    "bim.create_wall",
    "bim.create_structure",
    "bim.add_window",
)

TECHDRAW_PACK_TOOL_NAMES: tuple[str, ...] = (
    "techdraw.list_pages",
    "techdraw.create_page",
    "techdraw.add_view",
    "techdraw.add_dimension",
    "techdraw.add_annotation",
)

MATERIAL_PACK_TOOL_NAMES: tuple[str, ...] = (
    "material.list_materials",
    "material.apply_material",
    "material.set_appearance",
)

MESH_PACK_TOOL_NAMES: tuple[str, ...] = (
    "mesh.list_meshes",
    "mesh.analyze",
    "mesh.repair",
)

MESHPART_PACK_TOOL_NAMES: tuple[str, ...] = (
    "meshpart.mesh_from_shape",
    "meshpart.shape_from_mesh",
)

FEM_PACK_TOOL_NAMES: tuple[str, ...] = (
    "fem.list_analysis",
    "fem.create_analysis",
    "fem.add_material",
    "fem.add_constraint",
    "fem.mesh_analysis",
    "fem.solve",
)

CAM_PACK_TOOL_NAMES: tuple[str, ...] = (
    "cam.list_jobs",
    "cam.create_job",
    "cam.add_tool",
    "cam.add_operation",
)

POINTS_PACK_TOOL_NAMES: tuple[str, ...] = ("points.list_clouds",)

INSPECTION_PACK_TOOL_NAMES: tuple[str, ...] = ("inspection.list_features",)

ROBOT_PACK_TOOL_NAMES: tuple[str, ...] = ("robot.list_setup",)


@dataclass(frozen=True)
class WorkbenchToolPack:
    workbench: str
    domain: str
    instructions: str
    command_prefixes: tuple[str, ...]
    object_types: tuple[str, ...] = ()
    object_templates: tuple[dict[str, str], ...] = ()
    tool_names: tuple[str, ...] = field(default=())
    # Retained for additive metadata compatibility. Production packs must leave
    # this empty: a provider surface is exactly the active workbench's own tools.
    required_adjacent_tool_names: tuple[str, ...] = field(default=())

    def provider_tool_names(self) -> tuple[str, ...]:
        names: list[str] = []
        for tool_name in self.tool_names + self.required_adjacent_tool_names:
            if tool_name not in names:
                names.append(tool_name)
        return tuple(names)

    def summary(self) -> dict[str, object]:
        return {
            "workbench": self.workbench,
            "domain": self.domain,
            "instructions": self.instructions,
            "command_prefixes": list(self.command_prefixes),
            "object_types": list(self.object_types),
            "object_templates": list(self.object_templates),
            "tool_names": list(self.tool_names),
            "required_adjacent_tool_names": list(self.required_adjacent_tool_names),
            "provider_tool_names": list(self.provider_tool_names()),
        }


WORKBENCH_TOOL_PACKS: dict[str, WorkbenchToolPack] = {
    "AssemblyWorkbench": WorkbenchToolPack(
        "AssemblyWorkbench",
        "assemblies",
        "Build assemblies from existing parts: create the container, insert "
        "components as links, ground the base component, then relate "
        "components with joints. The solver positions unfixed components; "
        "check its verdict after every joint. Use only exact component and "
        "subelement names present in the active Assembly context, and verify "
        "solved positions from the returned placements or a screenshot.",
        ("Assembly_",),
        ("Assembly::AssemblyObject",),
        ({"name": "assembly", "object_type": "Assembly::AssemblyObject"},),
        tool_names=ASSEMBLY_PACK_TOOL_NAMES,
    ),
    "BIMWorkbench": WorkbenchToolPack(
        "BIMWorkbench",
        "BIM",
        "Model buildings top-down: create the spatial structure (site, "
        "building, levels) first, then elements assigned to levels. Walls "
        "follow existing wire baselines and slabs extrude existing closed "
        "profiles, addressed by their exact active-document names. If a "
        "required profile is absent, ask the human to prepare it in its owning "
        "workbench before continuing. Openings cut their host wall "
        "automatically; verify them with a screenshot.",
        ("BIM_", "Arch_", "Draft_"),
        ("Arch::", "BIM::"),
        (
            {"name": "building", "object_type": "App::DocumentObjectGroup"},
            {"name": "level", "object_type": "App::DocumentObjectGroup"},
        ),
        tool_names=BIM_PACK_TOOL_NAMES,
    ),
    "CAMWorkbench": WorkbenchToolPack(
        "CAMWorkbench",
        "CAM",
        "Create a machining job for shaped model objects, add cutting "
        "tools, then add operations (profile, pocket, drilling, face). "
        "Depths are absolute Z and face references must be exact; derive both "
        "from the bounded active-document context or ask the human to confirm "
        "them. An operation reporting an empty toolpath cut nothing; fix "
        "depths or faces before continuing. G-code "
        "postprocessing to files is left to the user in the FreeCAD GUI.",
        ("CAM_",),
        ("Path::FeaturePython",),
        ({"name": "job_container", "object_type": "App::DocumentObjectGroup"},),
        tool_names=CAM_PACK_TOOL_NAMES,
    ),
    "DraftWorkbench": WorkbenchToolPack(
        "DraftWorkbench",
        "drafting",
        "2D wires, circles, rectangles, splines on the global XY plane; "
        "arrays and text annotations. Closed profiles with make_face=true are "
        "native planar faces that other workbenches can consume later.",
        ("Draft_",),
        ("Part::Part2DObject",),
        (
            {"name": "draft_group", "object_type": "App::DocumentObjectGroup"},
            {"name": "annotation_group", "object_type": "App::DocumentObjectGroup"},
        ),
        tool_names=DRAFT_PACK_TOOL_NAMES,
    ),
    "FemWorkbench": WorkbenchToolPack(
        "FemWorkbench",
        "FEA",
        "Finite element analysis on solid models: create an analysis with "
        "a CalculiX solver, add a library material, add fixed supports and "
        "loads on exact model subelements from the active FEM context, "
        "generate a Gmsh mesh, then solve. If an exact subelement name or "
        "material UUID is unavailable, ask the human to provide it rather "
        "than guessing or calling another workbench's tools. "
        "fem.solve reports peak von Mises stress and displacement; compare "
        "them against the material's yield strength. Solving requires the "
        "external Gmsh and CalculiX binaries and fails with instructions "
        "when they are missing.",
        ("Fem_",),
        ("Fem::",),
        (
            {"name": "analysis_group", "object_type": "App::DocumentObjectGroup"},
            {"name": "constraint_group", "object_type": "App::DocumentObjectGroup"},
        ),
        tool_names=FEM_PACK_TOOL_NAMES,
    ),
    "InspectionWorkbench": WorkbenchToolPack(
        "InspectionWorkbench",
        "inspection",
        "Read nominal-versus-actual geometry comparisons. List existing "
        "inspection features and their computed distances; creating new "
        "comparisons runs in the FreeCAD GUI.",
        ("Inspection_",),
        (),
        ({"name": "inspection_group", "object_type": "App::DocumentObjectGroup"},),
        tool_names=INSPECTION_PACK_TOOL_NAMES,
    ),
    "MaterialWorkbench": WorkbenchToolPack(
        "MaterialWorkbench",
        "materials",
        "Assign materials and appearance to shaped objects. Find the material "
        "card's exact UUID with material.list_materials, then apply it with "
        "material.apply_material; the card carries physical properties used "
        "by FEM. Use material.set_appearance for display color/transparency "
        "only, without physical properties.",
        ("Material_", "Mat"),
        (),
        ({"name": "material_group", "object_type": "App::DocumentObjectGroup"},),
        tool_names=MATERIAL_PACK_TOOL_NAMES,
    ),
    "MeshWorkbench": WorkbenchToolPack(
        "MeshWorkbench",
        "mesh",
        "Inspect and repair triangle meshes. List meshes for exact names, "
        "analyze one mesh to see its defects, then repair only what the "
        "analysis justifies and re-analyze to confirm. A watertight, "
        "defect-free mesh is the goal before conversion or export.",
        ("Mesh_",),
        ("Mesh::",),
        ({"name": "mesh_group", "object_type": "App::DocumentObjectGroup"},),
        tool_names=MESH_PACK_TOOL_NAMES,
    ),
    "MeshPartWorkbench": WorkbenchToolPack(
        "MeshPartWorkbench",
        "mesh conversion",
        "Convert between meshes and BREP shapes. mesh_from_shape "
        "tessellates a shaped object into a triangle mesh; shape_from_mesh "
        "sews a mesh into a faceted BREP shape. A solid result requires an "
        "already validated watertight source mesh; ask the human to validate "
        "it in the owning workbench if that state is not in context. Sources "
        "are never modified.",
        ("MeshPart_",),
        ("Mesh::", "Part::"),
        ({"name": "mesh_from_shape", "object_type": "Mesh::Feature"},),
        tool_names=MESHPART_PACK_TOOL_NAMES,
    ),
    "NoneWorkbench": WorkbenchToolPack(
        "NoneWorkbench",
        "no active workbench",
        "Inspect the current document.",
        (),
        (),
        ({"name": "context_group", "object_type": "App::DocumentObjectGroup"},),
    ),
    "PartDesignWorkbench": WorkbenchToolPack(
        "PartDesignWorkbench",
        "solids",
        "Body, sketch, feature. Verify topology and profile readiness.",
        ("PartDesign_", "Sketcher_"),
        ("PartDesign::", "Sketcher::SketchObject"),
        (
            {"name": "body", "object_type": "PartDesign::Body"},
            {"name": "sketch", "object_type": "Sketcher::SketchObject"},
        ),
        tool_names=PARTDESIGN_PACK_TOOL_NAMES,
    ),
    "PartWorkbench": WorkbenchToolPack(
        "PartWorkbench",
        "boundary-representation solids",
        "Direct BREP operations on intentional existing profiles and solids. "
        "Resolve subelements by guarded geometry queries before finishing edges.",
        ("Part_",),
        ("Part::",),
        (
            {"name": "box", "object_type": "Part::Box"},
            {"name": "cylinder", "object_type": "Part::Cylinder"},
            {"name": "sphere", "object_type": "Part::Sphere"},
        ),
        tool_names=PART_PACK_TOOL_NAMES,
    ),
    "PointsWorkbench": WorkbenchToolPack(
        "PointsWorkbench",
        "point clouds",
        "Read point-cloud data. List clouds for exact names, counts, and "
        "bounds. Clouds are source data — never modify or delete them; "
        "import and conversion run in the FreeCAD GUI.",
        ("Points_",),
        ("Points::",),
        ({"name": "points_group", "object_type": "App::DocumentObjectGroup"},),
        tool_names=POINTS_PACK_TOOL_NAMES,
    ),
    "ReverseEngineeringWorkbench": WorkbenchToolPack(
        "ReverseEngineeringWorkbench",
        "reverse engineering",
        "Reverse-engineering reconstruction remains human-driven until native "
        "source and fitted-output provenance is exposed.",
        ("ReverseEngineering_",),
        (),
        (
            {
                "name": "reverse_engineering_group",
                "object_type": "App::DocumentObjectGroup",
            },
        ),
    ),
    "RobotWorkbench": WorkbenchToolPack(
        "RobotWorkbench",
        "robot simulation",
        "Read the robot-simulation setup. List robots, trajectories, and "
        "related geometry with their roles; placement and trajectory editing "
        "run in the FreeCAD GUI.",
        ("Robot_",),
        ("Robot::",),
        (
            {
                "name": "robot_simulation_group",
                "object_type": "App::DocumentObjectGroup",
            },
        ),
        tool_names=ROBOT_PACK_TOOL_NAMES,
    ),
    "SketcherWorkbench": WorkbenchToolPack(
        "SketcherWorkbench",
        "sketching",
        "Lines/arcs/splines/slots. Constrain with meaningful dimensions and relationships.",
        ("Sketcher_",),
        ("Sketcher::SketchObject",),
        ({"name": "sketch", "object_type": "Sketcher::SketchObject"},),
        tool_names=SKETCHER_PACK_TOOL_NAMES,
    ),
    "SpreadsheetWorkbench": WorkbenchToolPack(
        "SpreadsheetWorkbench",
        "spreadsheet",
        "Parametric data sheets. Read before writing; aliases make cells "
        "addressable as SheetName.alias from expressions in other objects.",
        ("Spreadsheet_",),
        ("Spreadsheet::Sheet",),
        ({"name": "sheet", "object_type": "Spreadsheet::Sheet"},),
        tool_names=SPREADSHEET_PACK_TOOL_NAMES,
    ),
    "SurfaceWorkbench": WorkbenchToolPack(
        "SurfaceWorkbench",
        "surfaces",
        "Freeform surfacing: fill closed edge loops, loft through profiles, "
        "blend between edges, extend faces, thicken into solids. Reference "
        "only exact edge and face names present in the active Surface context; "
        "ask the human to identify missing prerequisites rather than guessing.",
        ("Surface_",),
        ("Surface::",),
        (
            {"name": "filling", "object_type": "Surface::Filling"},
            {"name": "geom_fill_surface", "object_type": "Surface::GeomFillSurface"},
            {"name": "sections", "object_type": "Surface::Sections"},
        ),
        tool_names=SURFACE_PACK_TOOL_NAMES,
    ),
    "TechDrawWorkbench": WorkbenchToolPack(
        "TechDrawWorkbench",
        "drawings",
        "2D technical drawings: create a page, add projected views of 3D "
        "objects, then dimensions and notes. Projected elements are named "
        "Edge0/Vertex0 within each view and differ from the 3D model's "
        "element names. Capture a screenshot to verify page layout.",
        ("TechDraw_",),
        ("TechDraw::",),
        (
            {"name": "page", "object_type": "TechDraw::DrawPage"},
            {"name": "view", "object_type": "TechDraw::DrawViewPart"},
            {"name": "dimension", "object_type": "TechDraw::DrawViewDimension"},
        ),
        tool_names=TECHDRAW_PACK_TOOL_NAMES,
    ),
    "TestWorkbench": WorkbenchToolPack(
        "TestWorkbench",
        "test framework",
        "Read-only.",
        ("Test_", "Std_Test"),
        (),
        ({"name": "test_group", "object_type": "App::DocumentObjectGroup"},),
    ),
}


def get_tool_pack(workbench: str | None) -> WorkbenchToolPack | None:
    if not workbench:
        return None
    return WORKBENCH_TOOL_PACKS.get(workbench)


def list_tool_packs() -> list[dict[str, object]]:
    return [pack.summary() for pack in WORKBENCH_TOOL_PACKS.values()]
