# SPDX-License-Identifier: LGPL-2.1-or-later

"""Service-backed VibeCAD tool registration.

Each module in this package owns one provider-visible tool shape and must expose
``run(service, **kwargs)``.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

TOOL_MODULE_NAMES = (
    "conversation_ask_user",
    "conversation_review_design",
    "fastener_catalog_search",
    "component_catalog_search",
    "material_catalog_search",
    "core_capture_view_screenshot",
    "core_set_view",
    "draft_list_objects",
    "spreadsheet_read_sheet",
    "assembly_list_structure",
    "assembly_play_simulation",
    "assembly_stop_simulation",
    "techdraw_list_pages",
    "material_list_materials",
    "mesh_list_meshes",
    "fem_list_analysis",
    "cam_list_jobs",
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
