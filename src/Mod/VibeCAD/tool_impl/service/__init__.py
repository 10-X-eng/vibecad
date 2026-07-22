# SPDX-License-Identifier: LGPL-2.1-or-later

"""Service-backed VibeCAD tool registration.

Each module in this package owns one provider-visible tool shape and must expose
``run(service, **kwargs)``.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

TOOL_MODULE_NAMES = (
    "openscad_inspect_model",
    "openscad_create_model",
    "openscad_edit_source",
    "openscad_set_parameters",
    "openscad_set_conversion_mode",
    "openscad_delete_model",
    "build123d_inspect_model",
    "build123d_create_model",
    "build123d_edit_source",
    "build123d_set_parameters",
    "build123d_set_inputs",
    "build123d_reconfigure_model",
    "build123d_delete_model",
    "conversation_ask_user",
    "conversation_review_design",
    "core_inspect",
    "core_capture_view_screenshot",
    "core_set_view",
    "partdesign_create_body",
    "partdesign_create_sketch",
    "partdesign_edit_sketch",
    "partdesign_create_datum_plane",
    "partdesign_create_datum_axis",
    "partdesign_create_datum_point",
    "partdesign_create_shape_binder",
    "partdesign_create_subshape_binder",
    "partdesign_pad",
    "partdesign_pocket",
    "partdesign_hole",
    "partdesign_revolution",
    "partdesign_groove",
    "partdesign_additive_loft",
    "partdesign_thin_loft",
    "partdesign_subtractive_loft",
    "partdesign_additive_pipe",
    "partdesign_subtractive_pipe",
    "partdesign_additive_helix",
    "partdesign_subtractive_helix",
    "partdesign_linear_pattern",
    "partdesign_polar_pattern",
    "partdesign_mirror",
    "partdesign_multi_transform",
    "partdesign_fillet",
    "partdesign_chamfer",
    "partdesign_draft",
    "partdesign_thickness",
    "partdesign_boolean",
    "partdesign_set_tip",
    "partdesign_find_subelements",
    "partdesign_measure",
    "part_find_subelements",
    "part_measure",
    "part_boolean",
    "part_extrude",
    "part_revolve",
    "part_mirror",
    "part_fillet",
    "part_chamfer",
    "surface_fill",
    "surface_loft",
    "surface_blend",
    "surface_extend",
    "surface_thicken",
    "draft_list_objects",
    "draft_create_wire",
    "draft_create_circle",
    "draft_create_rectangle",
    "draft_create_bspline",
    "draft_create_array",
    "draft_create_text",
    "spreadsheet_create_sheet",
    "spreadsheet_set_cells",
    "spreadsheet_read_sheet",
    "assembly_list_structure",
    "assembly_create_assembly",
    "assembly_insert_component",
    "assembly_ground_component",
    "assembly_create_joint",
    "assembly_solve",
    "techdraw_list_pages",
    "techdraw_create_page",
    "techdraw_add_view",
    "techdraw_add_dimension",
    "techdraw_add_annotation",
    "material_list_materials",
    "material_apply_material",
    "material_set_appearance",
    "mesh_list_meshes",
    "mesh_analyze",
    "mesh_repair",
    "meshpart_mesh_from_shape",
    "meshpart_shape_from_mesh",
    "fem_list_analysis",
    "fem_create_analysis",
    "fem_add_material",
    "fem_add_constraint",
    "fem_mesh_analysis",
    "fem_solve",
    "cam_list_jobs",
    "cam_create_job",
    "cam_add_tool",
    "cam_add_operation",
    "points_list_clouds",
    "inspection_list_features",
    "robot_list_setup",
)


def register_tools(registry: Any, service: Any) -> None:
    for module_name in TOOL_MODULE_NAMES:
        module = import_module(f"{__name__}.{module_name}")
        spec = module.TOOL_SPEC
        if bool(getattr(module, "RUNNER_HANDLED", False)):
            registry.register_spec(spec, None)
            continue
        module_run = getattr(module, "run", None)
        if not callable(module_run):
            raise ValueError(f"VibeCAD service tool module has no run(): {module_name}")

        def handler(_module=module, **kwargs):
            return _module.run(service, **kwargs)

        registry.register_spec(spec, handler)
