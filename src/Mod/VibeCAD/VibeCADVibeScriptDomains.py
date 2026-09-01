# SPDX-License-Identifier: LGPL-2.1-or-later

"""Workbench-qualified VibeScript domain contracts.

Every supported workbench, including Part Design, uses this one versioned
program contract and lifecycle registry.  A pack is never considered available
merely because its metadata exists: worker, validator, publisher, persistence,
inspection, and deletion adapters must all be registered.
"""

from __future__ import annotations

import ast
import copy
from dataclasses import dataclass
import hashlib
import inspect
import json
import math
from pathlib import Path, PurePath
import re
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from jsonschema import Draft202012Validator

from VibeCADDocumentReferences import (
    DocumentReferenceError,
    is_document_reference,
    normalize_document_reference,
)

PROGRAM_SCHEMA = "vibecad-vibescript-program-v2"
PROGRAM_VERSION = 2
VIBESCRIPT_VERSION = "2"
PARTDESIGN_V1_SCHEMA = "vibecad-vibescript-model-v1"
MAX_SOURCE_BYTES = 256_000
MAX_INPUT_BYTES = 256_000
MAX_INPUT_DEPTH = 8
MAX_ARRAY_ITEMS = 4096
MAX_OUTPUTS = 64
MAX_PART_CONTEXT_SHAPES = 24
MAX_PART_CONTEXT_SUBELEMENTS = 32
MAX_SKETCHER_CONTEXT_SKETCHES = 32
MAX_SKETCHER_CONTEXT_ITEMS = 128
MAX_DRAFT_CONTEXT_OBJECTS = 64
MAX_DRAFT_CONTEXT_POINTS = 128
MAX_SPREADSHEET_CONTEXT_SHEETS = 32
MAX_SPREADSHEET_CONTEXT_CELLS = 128
MAX_MATERIAL_CONTEXT_TARGETS = 512
MAX_MESH_CONTEXT_OBJECTS = 128
MAX_POINTS_CONTEXT_OBJECTS = 128
MAX_POINTS_CONTEXT_SAMPLE = 8
MAX_INSPECTION_CONTEXT_OBJECTS = 128
MAX_ROBOT_CONTEXT_OBJECTS = 128
MAX_FEM_CONTEXT_OBJECTS = 256
MAX_FEM_CONTEXT_LINKS = 128
MAX_CAM_CONTEXT_OBJECTS = 256
MAX_CAM_CONTEXT_LINKS = 128
MAX_CAM_CONTEXT_COMMANDS = 4096
MAX_CAM_CONTEXT_VALIDATION_BYTES = 256_000
MAX_TECHDRAW_CONTEXT_OBJECTS = 256
MAX_TECHDRAW_CONTEXT_LINKS = 128
MAX_TECHDRAW_CONTEXT_TEXT_LINES = 64
MAX_TECHDRAW_CONTEXT_TEXT_CHARS = 4096
MAX_TECHDRAW_CONTEXT_VALIDATION_BYTES = 256_000
MAX_DOMAIN_CONTEXT_PROGRAMS = 32
MAX_CONTEXT_REFERENCES_PER_PROGRAM = 16
MAX_CONTEXT_VALUE_BYTES = 16_384

PROP_PROGRAM_ID = "VibeCADVibeScriptProgramId"
PROP_PROGRAM_DOMAIN = "VibeCADVibeScriptDomain"
PROP_PROGRAM_WORKBENCH = "VibeCADVibeScriptWorkbench"
PROP_PROGRAM_REVISION = "VibeCADVibeScriptRevision"
PROP_PROGRAM_OUTPUT = "VibeCADVibeScriptOutputName"
PROP_PROGRAM_LABEL = "VibeCADVibeScriptProgramLabel"
PROP_PROGRAM_CONTRACT = "VibeCADVibeScriptProgramContract"
PROP_PROGRAM_EDITOR_DRAFT = "VibeCADVibeScriptEditorDraft"

DOCUMENT_PROGRAM_SCHEMA = "vibecad-vibescript-document-program-v1"
EDITOR_DRAFT_SCHEMA = "vibecad-vibescript-editor-draft-v1"
MAX_DOCUMENT_PROGRAM_BYTES = MAX_SOURCE_BYTES + (2 * MAX_INPUT_BYTES) + (256 * 1024)

LIFECYCLE_OPERATIONS: tuple[str, ...] = (
    "describe_api",
    "inspect_program",
    "create_program",
    "edit_source",
    "set_inputs",
    "reconfigure_program",
    "delete_program",
)

UNIVERSAL_SOURCE_OPERATIONS: tuple[str, ...] = (
    "read_source",
    "read_operation",
    "read_api",
    "read_geometry",
    "read_placement",
    "create_program",
    "build_program",
    "edit_source",
    "set_inputs",
    "reconfigure_program",
    "delete_output",
    "delete_program",
    "delete_object",
)

MODEL_ASSEMBLY_SOURCE_OPERATIONS: tuple[str, ...] = (
    "create_part",
    "create_assembly",
)

# These are discovery groups, not separate APIs.  They let an agent request the
# small, relevant part of a workbench API without guessing names or consuming a
# complete description.  Every exported callable is assigned exactly once by
# ``api_groups`` below; newly added exports fall into ``other`` until named.
_API_GROUPS_BY_DOMAIN: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "partdesign": (
        ("references_and_hardware", ("from_object", "fastener", "involute_gear")),
        (
            "primitives",
            ("box", "wedge", "plane", "prism", "cylinder", "cone", "sphere", "torus"),
        ),
        (
            "sketches",
            (
                "point",
                "line",
                "arc",
                "circle",
                "ellipse",
                "bspline",
                "external_geometry",
                "constraint",
                "sketch",
            ),
        ),
        (
            "spatial_geometry",
            (
                "line_3d",
                "arc_3d",
                "circle_3d",
                "ellipse_3d",
                "bezier_3d",
                "bspline_3d",
                "nurbs_curve",
                "helix_curve",
                "wire",
                "face",
                "shell",
                "solid",
                "compound",
                "subshape",
            ),
        ),
        (
            "features",
            (
                "extrude",
                "revolve",
                "loft",
                "sweep",
                "helix",
                "boolean",
                "union",
                "cut",
                "intersect",
                "section",
                "general_fuse",
                "slice",
                "ruled_surface",
                "filled_surface",
            ),
        ),
        (
            "patterns_and_transforms",
            (
                "polar_pattern",
                "linear_pattern",
                "multi_transform",
                "mirror",
                "transform",
            ),
        ),
        (
            "dressups_and_holes",
            (
                "fillet",
                "chamfer",
                "thickness",
                "move_planar_faces",
                "hole",
                "holes",
                "bosses",
                "fastener_hole",
                "draft",
            ),
        ),
        (
            "repair",
            (
                "defeature",
                "to_nurbs",
                "reverse",
                "sew",
                "repair",
                "offset",
                "offset2d",
                "project",
                "refine",
            ),
        ),
        ("verification", ("find_subelements", "measure", "minimum_distance")),
        ("materials_and_publication", ("material", "appearance", "body", "publish")),
    ),
    "assembly": (
        ("components", ("component", "instances", "fastener")),
        ("structure_and_joints", ("connector", "joint", "assembly", "solve")),
        ("verification_and_motion", ("mechanism_check", "motion", "simulation")),
        ("deliverables", ("exploded_view", "bill_of_materials")),
    ),
    "sketcher": (
        (
            "geometry",
            (
                "point",
                "line",
                "arc",
                "circle",
                "ellipse",
                "elliptic_arc",
                "hyperbolic_arc",
                "parabolic_arc",
                "bspline",
                "external_geometry",
            ),
        ),
        ("constraints", ("constraint",)),
        ("publication", ("sketch",)),
    ),
    "material": (
        ("catalog", ("material",)),
        ("assignment", ("assign", "appearance")),
    ),
    "mesh": (
        ("creation", ("mesh", "from_object", "mesh_from_shape", "shape_from_mesh")),
        ("editing", ("transform", "union", "difference", "intersection", "repair")),
        ("verification", ("diagnostics",)),
    ),
    "meshpart": (("conversion", ("mesh_from_shape", "shape_from_mesh")),),
    "draft": (
        ("geometry", ("wire", "circle", "rectangle", "bspline", "text")),
        ("patterns", ("array",)),
    ),
    "surface": (
        ("curves", ("line", "circle", "bezier", "bspline", "wire", "from_object")),
        (
            "surfaces",
            (
                "face",
                "surface",
                "boundary",
                "curve_constraint",
                "face_constraint",
                "point_constraint",
                "fill",
                "blend",
                "extend",
                "loft",
                "thicken",
                "shell",
            ),
        ),
    ),
    "spreadsheet": (("sheets", ("sheet", "cell", "range_style")),),
    "points": (("point_clouds", ("point_cloud",)),),
    "reverse_engineering": (
        ("fitting", ("fit_curve", "fit_surface", "reconstruct", "segment")),
        ("verification", ("fit_metrics",)),
    ),
    "inspection": (
        ("inspection", ("comparison", "group", "measurement")),
        ("reporting", ("report",)),
    ),
    "robot": (
        ("programming", ("robot", "waypoint", "trajectory", "dressup")),
        ("verification", ("simulate",)),
    ),
    "fem": (
        (
            "setup",
            ("analysis", "solver", "material", "constraint", "load_case", "mesh"),
        ),
        ("solve", ("solve",)),
    ),
    "cam": (
        ("setup", ("job", "stock", "tool", "operation")),
        ("output", ("generate_toolpath", "postprocess")),
    ),
    "techdraw": (
        ("page", ("page", "template")),
        ("views", ("view", "projection")),
        ("documentation", ("dimension", "annotation")),
    ),
    "part": (),
}

# Exact, high-frequency calls placed in turn-start context. Less-common calls
# remain discoverable through api_groups and vibescript.read_api, so the active
# context stays small without asking a model to guess primitive contracts.
_CORE_API_EXPORTS_BY_DOMAIN: dict[str, tuple[str, ...]] = {
    "assembly": (
        "component",
        "connector",
        "joint",
        "assembly",
        "solve",
        "mechanism_check",
        "motion",
        "simulation",
        "bill_of_materials",
    ),
    "partdesign": (
        "from_object",
        "box",
        "wedge",
        "plane",
        "prism",
        "cylinder",
        "cone",
        "sphere",
        "torus",
        "compound",
        "union",
        "cut",
        "intersect",
        "holes",
        "bosses",
        "find_subelements",
        "move_planar_faces",
        "fillet",
        "chamfer",
        "transform",
        "measure",
    ),
}


def api_groups(pack: "VibeScriptWorkbenchPack") -> dict[str, list[str]]:
    """Return deterministic, exhaustive discovery groups for one domain API."""

    exported = tuple(pack.api_exports)
    exported_set = set(exported)
    result: dict[str, list[str]] = {}
    assigned: set[str] = set()
    for group_name, raw_names in _API_GROUPS_BY_DOMAIN.get(pack.domain, ()):
        names = [name for name in raw_names if name in exported_set]
        overlap = assigned.intersection(names)
        if overlap:
            raise RuntimeError(
                f"VibeScript API group {group_name!r} duplicates {sorted(overlap)!r}."
            )
        if names:
            result[group_name] = names
            assigned.update(names)
    remaining = [name for name in exported if name not in assigned]
    if remaining:
        result["other"] = remaining
    return result


def _core_api_snapshot(pack: "VibeScriptWorkbenchPack") -> dict[str, Any]:
    """Return compact, authoritative contracts for common source operations."""

    names = _CORE_API_EXPORTS_BY_DOMAIN.get(pack.domain, ())
    if not names:
        return {}
    from vibescript_domain_api import create_domain_api

    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
    calls: dict[str, str] = {}
    for name in names:
        if name not in api.exported_names:
            raise RuntimeError(
                f"Core VibeScript API {pack.domain}.{name} is not exported."
            )
        member = getattr(api, name)
        signature = inspect.signature(member)
        description = str(inspect.getdoc(member) or "").strip()
        summary = description.split("\n\n", 1)[0].replace("\n", " ")
        calls[name] = f"api.{name}{signature}. " + " ".join(summary.split())
    source_contract = (
        "Python using api, inputs, and doc. Define main(). Read components as "
        "inputs['name']. Own components and joints in stable-key mappings. Return "
        "{'assembly': model, 'solver_diagnostics': api.solve(model)}."
        if pack.domain == "assembly"
        else (
            "Python using api, inputs, and doc. Define main(). Return the final value "
            "for one output or an ordered mapping matching multiple expected_outputs."
        )
    )
    result_rule = (
        "main() returns one assembly and its solver_diagnostics. Stable-key "
        "api.assembly mappings own their components and joints."
        if pack.domain == "assembly"
        else (
            "main() returns the final value for one output or an ordered mapping whose "
            "keys match multiple expected_outputs."
        )
    )
    create_program = {
        "required_fields": [
            "program_name",
            "source",
            "input_schema",
            "inputs",
            "expected_outputs",
        ],
        "not_a_field": ["expected_revision"],
        "no_inputs": {
            "input_schema": {
                "properties": {},
                "additionalProperties": False,
            },
            "inputs": {},
        },
        "output_types": list(pack.output_types),
        "result_rule": result_rule,
    }
    if pack.domain == "assembly":
        create_program.update(
            {
                "reference_input": {
                    "input_schema": {
                        "properties": {
                            "base": {
                                "type": "object",
                                "x-vibecad-reference": True,
                            },
                            "moving": {
                                "type": "object",
                                "x-vibecad-reference": True,
                            },
                        },
                        "required": ["base", "moving"],
                        "additionalProperties": False,
                    },
                    "inputs": {
                        "base": {"catalog_key": "component-1"},
                        "moving": {"catalog_key": "component-2"},
                    },
                    "source": "inputs['base']; inputs['moving']",
                },
                "result_example": (
                    "return {'assembly': model, "
                    "'solver_diagnostics': api.solve(model)}"
                ),
            }
        )
    return {
        "domain": pack.domain,
        "source": source_contract,
        "create_program": create_program,
        "api": calls,
    }


PROVIDER_DOMAIN_OPERATIONS: tuple[str, ...] = (
    "create_program",
    "set_inputs",
    "reconfigure_program",
    "delete_program",
)


@runtime_checkable
class VibeScriptDomainAdapter(Protocol):
    """Complete adapter boundary required before a domain may be surfaced."""

    production_ready: bool

    def describe_api(self) -> dict[str, Any]: ...

    def validate_source(self, source: str) -> None: ...

    def execute_candidate(
        self,
        prepared: dict[str, Any],
        *,
        cancellation_check: Callable[[], bool] | None,
    ) -> dict[str, Any]: ...

    def validate_result(
        self, prepared: dict[str, Any], execution: dict[str, Any]
    ) -> dict[str, Any]: ...

    def publish(
        self,
        service: Any,
        prepared: dict[str, Any],
        validated: dict[str, Any],
    ) -> dict[str, Any]: ...

    def inspect(
        self, captured: dict[str, Any], contract: dict[str, Any]
    ) -> dict[str, Any]: ...

    def delete(
        self, service: Any, captured: dict[str, Any], contract: dict[str, Any]
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class VibeScriptWorkbenchPack:
    workbench: str
    domain: str
    title: str
    output_types: tuple[str, ...]
    instructions: str
    api_exports: tuple[str, ...]
    production_ready: bool = False

    @property
    def tool_names(self) -> tuple[str, ...]:
        return (
            *(f"vibescript.{operation}" for operation in UNIVERSAL_SOURCE_OPERATIONS),
            *(
                f"vibescript.{operation}"
                for operation in MODEL_ASSEMBLY_SOURCE_OPERATIONS
                if self.domain in {"partdesign", "assembly"}
            ),
            *(
                f"vibescript.{self.domain}.{operation}"
                for operation in PROVIDER_DOMAIN_OPERATIONS
            ),
        )

    @property
    def provider_tool_names(self) -> tuple[str, ...]:
        """Canonical workbench-neutral lifecycle exposed to the operating model."""

        return (
            *(
                f"vibescript.{operation}"
                for operation in UNIVERSAL_SOURCE_OPERATIONS
                if not (
                    self.domain in {"partdesign", "assembly"}
                    and operation == "create_program"
                )
            ),
            *(
                f"vibescript.{operation}"
                for operation in MODEL_ASSEMBLY_SOURCE_OPERATIONS
                if self.domain in {"partdesign", "assembly"}
            ),
        )

    @property
    def surface_id(self) -> str:
        return f"vibescript:{self.domain}:v2"

    def summary(self, *, available: bool, reason: str = "") -> dict[str, Any]:
        return {
            "workbench": self.workbench,
            "domain": self.domain,
            "title": self.title,
            "surface_id": self.surface_id,
            "program_schema": PROGRAM_SCHEMA,
            "output_types": list(self.output_types),
            "api_exports": list(self.api_exports),
            "tool_names": list(self.tool_names),
            "provider_tool_names": list(self.provider_tool_names),
            "available": bool(available),
            "unavailable_reason": str(reason or ""),
            "production_ready": self.production_ready,
        }


def _pack(
    workbench: str,
    domain: str,
    title: str,
    outputs: tuple[str, ...],
    instructions: str,
    api_exports: tuple[str, ...],
    *,
    production_ready: bool = False,
) -> VibeScriptWorkbenchPack:
    return VibeScriptWorkbenchPack(
        workbench=workbench,
        domain=domain,
        title=title,
        output_types=outputs,
        instructions=instructions,
        api_exports=api_exports,
        production_ready=production_ready,
    )


VIBESCRIPT_WORKBENCH_PACKS: dict[str, VibeScriptWorkbenchPack] = {
    "PartDesignWorkbench": _pack(
        "PartDesignWorkbench",
        "partdesign",
        "Part Design",
        ("solid", "shell", "face", "wire", "compound", "component_link"),
        "Source defines editable native history. Prefer primitives plus boolean when "
        "they exactly express the part; use sketch plus a feature for other planar "
        "profiles. Extrude constant sections; loft only changing sections. Cuts require "
        "base; set feature direction explicitly to along_normal, opposite_normal, or "
        "symmetric. Sketch planes: XY [X,Y]/+Z, XZ [X,Z]/-Y, YZ [Y,Z]/+X. Use direct "
        "3D topology only for nonplanar or standalone geometry. Boolean output_type solid "
        "requires one connected solid; compound retains separate shapes. body publishes "
        "a final Body feature; publish handles standalone topology.",
        (
            "from_object",
            "box",
            "wedge",
            "plane",
            "prism",
            "cylinder",
            "cone",
            "sphere",
            "torus",
            "fastener",
            "component",
            "instances",
            "point",
            "line",
            "arc",
            "circle",
            "ellipse",
            "bspline",
            "external_geometry",
            "constraint",
            "sketch",
            "line_3d",
            "arc_3d",
            "circle_3d",
            "ellipse_3d",
            "bezier_3d",
            "bspline_3d",
            "nurbs_curve",
            "helix_curve",
            "wire",
            "face",
            "shell",
            "solid",
            "compound",
            "subshape",
            "extrude",
            "revolve",
            "loft",
            "sweep",
            "helix",
            "boolean",
            "union",
            "cut",
            "intersect",
            "section",
            "general_fuse",
            "slice",
            "ruled_surface",
            "filled_surface",
            "polar_pattern",
            "linear_pattern",
            "multi_transform",
            "mirror",
            "fillet",
            "chamfer",
            "thickness",
            "move_planar_faces",
            "hole",
            "holes",
            "bosses",
            "fastener_hole",
            "involute_gear",
            "draft",
            "defeature",
            "to_nurbs",
            "reverse",
            "sew",
            "repair",
            "offset",
            "offset2d",
            "transform",
            "project",
            "refine",
            "find_subelements",
            "measure",
            "minimum_distance",
            "material",
            "appearance",
            "body",
            "publish",
        ),
        production_ready=True,
    ),
    "SketcherWorkbench": _pack(
        "SketcherWorkbench",
        "sketcher",
        "Sketcher",
        ("sketch",),
        "Define stable sketches with geometry, construction state, constraints, "
        "expressions, support, attachment, and profile-readiness expectations.",
        (
            "point",
            "line",
            "arc",
            "circle",
            "ellipse",
            "elliptic_arc",
            "hyperbolic_arc",
            "parabolic_arc",
            "bspline",
            "external_geometry",
            "constraint",
            "sketch",
        ),
        production_ready=True,
    ),
    "PartWorkbench": _pack(
        "PartWorkbench",
        "part",
        "Part",
        ("solid", "shell", "face", "wire", "compound"),
        "Build direct OCC shapes and declare the exact accepted shape class for "
        "each stable output.",
        (
            "from_object",
            "box",
            "wedge",
            "plane",
            "prism",
            "cylinder",
            "cone",
            "sphere",
            "torus",
            "line",
            "arc",
            "circle",
            "ellipse",
            "bezier",
            "bspline",
            "nurbs_curve",
            "helix",
            "wire",
            "face",
            "shell",
            "solid",
            "compound",
            "subshape",
            "extrude",
            "revolve",
            "loft",
            "sweep",
            "ruled_surface",
            "filled_surface",
            "fuse",
            "cut",
            "common",
            "section",
            "general_fuse",
            "slice",
            "defeature",
            "to_nurbs",
            "reverse",
            "sew",
            "repair",
            "fillet",
            "chamfer",
            "offset",
            "offset2d",
            "thicken",
            "transform",
            "mirror",
            "project",
            "refine",
        ),
        production_ready=True,
    ),
    "DraftWorkbench": _pack(
        "DraftWorkbench",
        "draft",
        "Draft",
        ("wire", "circle", "rectangle", "bspline", "array", "text"),
        "Define parametric Draft objects; publications retain their native Draft "
        "type and editable parameters instead of flattening to BREP.",
        ("wire", "circle", "rectangle", "bspline", "array", "text"),
        production_ready=True,
    ),
    "SurfaceWorkbench": _pack(
        "SurfaceWorkbench",
        "surface",
        "Surface",
        ("surface", "face", "shell", "fill", "blend", "extension", "loft", "solid"),
        "Build validated non-solid or solid surface results while preserving the "
        "declared output class.",
        (
            "line",
            "circle",
            "bezier",
            "bspline",
            "wire",
            "from_object",
            "face",
            "surface",
            "boundary",
            "curve_constraint",
            "face_constraint",
            "point_constraint",
            "fill",
            "blend",
            "extend",
            "loft",
            "thicken",
            "shell",
        ),
        production_ready=True,
    ),
    "AssemblyWorkbench": _pack(
        "AssemblyWorkbench",
        "assembly",
        "Assembly",
        (
            "assembly",
            "component_link",
            "joint",
            "solver_diagnostics",
            "mechanism_verification",
            "motion",
            "simulation",
            "exploded_view",
            "bom",
        ),
        "Link components; never rebuild their geometry in Assembly. Ground at least one "
        "occurrence, create connectors and joints, then solve. Use simulation for "
        "kinematics, exploded_view for presentation, and bill_of_materials for a BOM. "
        "assembly.play_simulation controls GUI playback of a published simulation.",
        (
            "assembly",
            "component",
            "instances",
            "fastener",
            "connector",
            "joint",
            "solve",
            "mechanism_check",
            "motion",
            "simulation",
            "exploded_view",
            "bill_of_materials",
        ),
        production_ready=True,
    ),
    "SpreadsheetWorkbench": _pack(
        "SpreadsheetWorkbench",
        "spreadsheet",
        "Spreadsheet",
        ("sheet",),
        "Apply a complete validated batch of cells, aliases, expressions, units, "
        "and formatting to one stable native sheet.",
        ("sheet", "cell", "range_style"),
        production_ready=True,
    ),
    "MaterialWorkbench": _pack(
        "MaterialWorkbench",
        "material",
        "Material",
        ("material_assignment", "appearance"),
        "Resolve material cards and keep physical assignments distinct from "
        "display-only appearance.",
        ("material", "assign", "appearance"),
        production_ready=True,
    ),
    "MeshWorkbench": _pack(
        "MeshWorkbench",
        "mesh",
        "Mesh",
        ("mesh", "solid", "shell", "face", "wire", "compound"),
        "Acquire, generate, transform, combine, repair, or convert native meshes "
        "and BREP shapes with bounded topology diagnostics.",
        (
            "mesh",
            "from_object",
            "transform",
            "union",
            "difference",
            "intersection",
            "repair",
            "diagnostics",
            "mesh_from_shape",
            "shape_from_mesh",
        ),
        production_ready=True,
    ),
    "MeshPartWorkbench": _pack(
        "MeshPartWorkbench",
        "meshpart",
        "MeshPart",
        ("mesh", "solid", "shell", "face", "wire", "compound"),
        "Perform explicit BREP-to-mesh or mesh-to-shape conversion and declare the "
        "published artifact type.",
        ("mesh_from_shape", "shape_from_mesh"),
        production_ready=True,
    ),
    "PointsWorkbench": _pack(
        "PointsWorkbench",
        "points",
        "Points",
        ("points",),
        "Read only project-approved point artifacts, then transform, filter, or "
        "downsample them into stable point-cloud outputs.",
        ("point_cloud",),
        production_ready=True,
    ),
    "ReverseEngineeringWorkbench": _pack(
        "ReverseEngineeringWorkbench",
        "reverse_engineering",
        "Reverse Engineering",
        ("curve", "surface", "brep", "mesh", "fit_metrics"),
        "Approximate, segment, triangulate, and reconstruct source data with native "
        "ReverseEngineering algorithms and explicit fit metrics.",
        (
            "fit_curve",
            "fit_surface",
            "reconstruct",
            "segment",
            "fit_metrics",
        ),
        production_ready=True,
    ),
    "InspectionWorkbench": _pack(
        "InspectionWorkbench",
        "inspection",
        "Inspection",
        ("inspection_group", "inspection_feature", "measurement", "report"),
        "Define nominal-versus-actual measurements, tolerances, and pass/fail "
        "reports as native inspection objects.",
        ("comparison", "group", "measurement", "report"),
        production_ready=True,
    ),
    "RobotWorkbench": _pack(
        "RobotWorkbench",
        "robot",
        "Robot",
        ("component_link", "robot", "trajectory", "dressup", "simulation"),
        "Place reusable equipment, then define robots, waypoints, trajectories, "
        "dress-ups, and worker-computed simulation diagnostics.",
        (
            "component",
            "instances",
            "robot",
            "waypoint",
            "trajectory",
            "dressup",
            "simulate",
        ),
        production_ready=True,
    ),
    "FemWorkbench": _pack(
        "FemWorkbench",
        "fem",
        "FEM",
        ("analysis", "solver", "material", "constraint", "load_case", "mesh", "result"),
        "Define native FEM analyses through semantic geometry references; meshing "
        "and solves remain cancellable worker operations.",
        ("analysis", "solver", "material", "constraint", "load_case", "mesh", "solve"),
        production_ready=True,
    ),
    "CAMWorkbench": _pack(
        "CAMWorkbench",
        "cam",
        "CAM",
        ("job", "stock", "tool", "operation", "toolpath"),
        "Define native jobs, stock, tools, operations, and worker-generated "
        "toolpaths. Generated files remain project artifacts until human export.",
        ("job", "stock", "tool", "operation", "generate_toolpath", "postprocess"),
        production_ready=False,
    ),
    "TechDrawWorkbench": _pack(
        "TechDrawWorkbench",
        "techdraw",
        "TechDraw",
        ("page", "template", "view", "projection", "dimension", "annotation"),
        "Define native drawing pages and consume only worker-precomputed projection "
        "state on the document thread.",
        ("page", "template", "view", "projection", "dimension", "annotation"),
        production_ready=True,
    ),
}


_ADAPTERS: dict[str, VibeScriptDomainAdapter] = {}
_INSTALLING_BUILTINS = False
_BUILTINS_INSTALLED = False


def _ensure_builtin_domain_adapters() -> None:
    global _BUILTINS_INSTALLED, _INSTALLING_BUILTINS
    if _INSTALLING_BUILTINS or _BUILTINS_INSTALLED:
        return
    _INSTALLING_BUILTINS = True
    try:
        from VibeCADVibeScriptDomainRuntime import install_builtin_adapters

        install_builtin_adapters()
        _BUILTINS_INSTALLED = True
    finally:
        _INSTALLING_BUILTINS = False


def register_domain_adapter(domain: str, adapter: VibeScriptDomainAdapter) -> None:
    clean = str(domain or "").strip().lower()
    packs = [
        pack for pack in VIBESCRIPT_WORKBENCH_PACKS.values() if pack.domain == clean
    ]
    if len(packs) != 1:
        raise ValueError(f"Unknown VibeScript domain: {domain!r}.")
    missing = [
        name
        for name in (
            "describe_api",
            "validate_source",
            "execute_candidate",
            "validate_result",
            "publish",
            "inspect",
            "delete",
        )
        if not callable(getattr(adapter, name, None))
    ]
    if missing:
        raise TypeError(
            f"VibeScript domain adapter {clean!r} is incomplete: {', '.join(missing)}."
        )
    if not isinstance(getattr(adapter, "production_ready", None), bool):
        raise TypeError(
            f"VibeScript domain adapter {clean!r} must declare a boolean production_ready status."
        )
    if clean in _ADAPTERS:
        raise ValueError(f"VibeScript domain adapter already registered: {clean}.")
    _ADAPTERS[clean] = adapter


def get_vibescript_pack(workbench: str | None) -> VibeScriptWorkbenchPack | None:
    return VIBESCRIPT_WORKBENCH_PACKS.get(str(workbench or ""))


def get_vibescript_pack_for_domain(
    domain: str | None,
) -> VibeScriptWorkbenchPack | None:
    """Return the single registered pack that owns an exact source domain."""

    clean = str(domain or "").strip().lower()
    matches = [
        pack for pack in VIBESCRIPT_WORKBENCH_PACKS.values() if pack.domain == clean
    ]
    return matches[0] if len(matches) == 1 else None


def get_domain_adapter(domain: str) -> VibeScriptDomainAdapter | None:
    _ensure_builtin_domain_adapters()
    return _ADAPTERS.get(str(domain or "").strip().lower())


def domain_availability(workbench: str | None) -> tuple[bool, str]:
    _ensure_builtin_domain_adapters()
    pack = get_vibescript_pack(workbench)
    if pack is None:
        return (
            False,
            f"No VibeScript domain is registered for {workbench or 'no workbench'}.",
        )
    if not pack.production_ready:
        return (
            False,
            f"The {pack.title} VibeScript domain is still under implementation and "
            "has not passed its production-readiness gate.",
        )
    adapter = _ADAPTERS.get(pack.domain)
    if adapter is None:
        return (
            False,
            f"The {pack.title} VibeScript domain has no complete worker/validator/"
            "publisher/persistence adapter.",
        )
    if not adapter.production_ready:
        return (
            False,
            f"The {pack.title} VibeScript adapter has not passed its production-readiness gate.",
        )
    return True, ""


def list_vibescript_packs() -> list[dict[str, Any]]:
    result = []
    for pack in VIBESCRIPT_WORKBENCH_PACKS.values():
        available, reason = domain_availability(pack.workbench)
        result.append(pack.summary(available=available, reason=reason))
    return result


def capture_document_program_payload(
    doc: Any,
    domain: str,
    program_id: str,
) -> dict[str, str]:
    """Capture one bounded portable contract/draft without touching geometry."""

    clean_domain = str(domain or "").strip().lower()
    clean_program_id = str(program_id or "").strip().lower()
    contracts: set[str] = set()
    drafts: set[str] = set()
    labels: set[str] = set()
    for obj in list(getattr(doc, "Objects", []) or []):
        properties = set(getattr(obj, "PropertiesList", []) or [])
        if not {PROP_PROGRAM_ID, PROP_PROGRAM_DOMAIN} <= properties:
            continue
        if (
            str(getattr(obj, PROP_PROGRAM_ID, "") or "") != clean_program_id
            or str(getattr(obj, PROP_PROGRAM_DOMAIN, "") or "") != clean_domain
        ):
            continue
        label = str(getattr(obj, PROP_PROGRAM_LABEL, "") or "").strip()
        if label:
            labels.add(label)
        contract = str(getattr(obj, PROP_PROGRAM_CONTRACT, "") or "")
        if contract:
            contracts.add(contract)
        draft = str(getattr(obj, PROP_PROGRAM_EDITOR_DRAFT, "") or "")
        if draft:
            drafts.add(draft)
    if len(contracts) > 1:
        raise ValueError(
            f"Program {clean_program_id} has conflicting portable document contracts."
        )
    if len(drafts) > 1:
        raise ValueError(
            f"Program {clean_program_id} has conflicting document editor drafts."
        )
    if len(labels) > 1:
        raise ValueError(f"Program {clean_program_id} has conflicting document labels.")
    return {
        "program_id": clean_program_id,
        "domain": clean_domain,
        "label": next(iter(labels), ""),
        "contract": next(iter(contracts), ""),
        "editor_draft": next(iter(drafts), ""),
    }


def capture_domain_programs(doc: Any, domain: str) -> list[dict[str, Any]]:
    """Capture bounded live identities without filesystem or geometry traversal."""

    clean_domain = str(domain or "").strip().lower()
    programs: dict[str, dict[str, Any]] = {}
    for obj in list(getattr(doc, "Objects", []) or []):
        properties = set(getattr(obj, "PropertiesList", []) or [])
        if PROP_PROGRAM_ID not in properties or PROP_PROGRAM_DOMAIN not in properties:
            continue
        if str(getattr(obj, PROP_PROGRAM_DOMAIN, "") or "") != clean_domain:
            continue
        program_id = str(getattr(obj, PROP_PROGRAM_ID, "") or "")
        if not program_id:
            continue
        item = programs.setdefault(
            program_id,
            {
                "program_id": program_id,
                "domain": clean_domain,
                "workbench": str(getattr(obj, PROP_PROGRAM_WORKBENCH, "") or ""),
                "label": str(getattr(obj, PROP_PROGRAM_LABEL, "") or ""),
                "working_revision": str(getattr(obj, PROP_PROGRAM_REVISION, "") or ""),
                "portable_document_contract": bool(
                    str(getattr(obj, PROP_PROGRAM_CONTRACT, "") or "")
                ),
                "editor_draft": bool(
                    str(getattr(obj, PROP_PROGRAM_EDITOR_DRAFT, "") or "")
                ),
                "live_outputs": [],
            },
        )
        if not item["label"]:
            item["label"] = str(getattr(obj, PROP_PROGRAM_LABEL, "") or "")
        item["portable_document_contract"] = bool(
            item["portable_document_contract"]
            or str(getattr(obj, PROP_PROGRAM_CONTRACT, "") or "")
        )
        item["editor_draft"] = bool(
            item["editor_draft"]
            or str(getattr(obj, PROP_PROGRAM_EDITOR_DRAFT, "") or "")
        )
        output_name = str(getattr(obj, PROP_PROGRAM_OUTPUT, "") or "")
        if output_name:
            view = getattr(obj, "ViewObject", None)
            item["live_outputs"].append(
                {
                    "name": output_name,
                    "object_name": str(getattr(obj, "Name", "") or ""),
                    "label": str(getattr(obj, "Label", "") or ""),
                    "type_id": str(getattr(obj, "TypeId", "") or ""),
                    "visible": bool(getattr(view, "Visibility", False))
                    if view is not None
                    else None,
                    "derived_state": str(getattr(obj, "VibeCADDerivedState", "") or ""),
                    "stale_reason": str(getattr(obj, "VibeCADStaleReason", "") or ""),
                    "source_revision": str(
                        getattr(obj, "VibeCADSourceRevision", "") or ""
                    ),
                }
            )
    return [programs[key] for key in sorted(programs)]


def capture_editable_sources_snapshot(service: Any, domain: str) -> dict[str, Any]:
    """Capture identities for the provider's editable-source index.

    This half is safe to run on the owning document thread.  Persisted program
    manifests are deliberately read later so failed and not-yet-published
    programs can be discovered without performing artifact I/O on that thread.
    """

    clean_domain = str(domain or "").strip().lower()
    pack = get_vibescript_pack(service.active_workbench_name())
    if pack is None or pack.domain != clean_domain:
        raise RuntimeError(
            f"The active workbench does not authorize VibeScript domain {clean_domain!r}."
        )
    scope_getter = getattr(service, "project_scope_snapshot", None)
    scope = scope_getter() if callable(scope_getter) else {}
    if not isinstance(scope, Mapping):
        scope = {}
    doc = service._active_document()
    native_programs = (
        capture_domain_programs(doc, clean_domain) if doc is not None else []
    )
    return {
        "_vibecad_deferred_vibescript_program_index": True,
        "domain": clean_domain,
        "workbench": pack.workbench,
        "surface_id": pack.surface_id,
        "project_root": str(scope.get("root") or ""),
        "document_name": str(getattr(doc, "Name", "") or "") if doc is not None else "",
        "document_uid": str(getattr(doc, "Uid", "") or "") if doc is not None else "",
        "native_program_count": len(native_programs),
        "native_programs": native_programs[:MAX_DOMAIN_CONTEXT_PROGRAMS],
    }


def _editable_source_outputs(program: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_outputs = program.get("live_outputs")
    if isinstance(raw_outputs, Mapping):
        candidates = [
            {"name": str(name), **dict(output)}
            for name, output in sorted(
                raw_outputs.items(),
                key=lambda item: str(item[0]),
            )
            if isinstance(output, Mapping)
        ]
    else:
        candidates = [
            dict(output)
            for output in list(raw_outputs or [])
            if isinstance(output, Mapping)
        ]
    return [
        {
            "name": str(output.get("name") or "")[:120],
            "object_name": str(output.get("object_name") or "")[:255],
            "label": str(output.get("label") or "")[:240],
            "type_id": str(output.get("type_id") or "")[:160],
            **(
                {"visible": bool(output["visible"])}
                if output.get("visible") is not None
                else {}
            ),
        }
        for output in candidates
        if str(output.get("name") or "") and str(output.get("object_name") or "")
    ]


def _compact_editable_source_candidate(
    program: Mapping[str, Any],
) -> dict[str, Any] | None:
    raw_candidate = program.get("latest_candidate")
    if not isinstance(raw_candidate, Mapping):
        return None
    candidate = {
        key: str(raw_candidate.get(key) or "")[:limit]
        for key, limit in (("status", 64), ("revision", 128), ("attempt_id", 160))
        if raw_candidate.get(key) not in (None, "")
    }
    raw_failure = raw_candidate.get("failure")
    if isinstance(raw_failure, Mapping):
        failure = {
            key: str(raw_failure.get(key) or "")[:2000]
            for key in ("failure_code", "failure_stage", "error")
            if raw_failure.get(key) not in (None, "")
        }
        if failure:
            candidate["failure"] = failure
    return candidate or None


def _editable_source_status(program: Mapping[str, Any]) -> str:
    if bool(program.get("editor_draft")):
        return "editor_draft"
    if str(program.get("state") or "") == "invalid_artifact":
        return "invalid_artifact"
    candidate = program.get("latest_candidate")
    candidate_status = (
        str(candidate.get("status") or "").strip().lower()
        if isinstance(candidate, Mapping)
        else ""
    )
    if candidate_status == "failed":
        return "build_failed"
    working_revision = str(program.get("working_revision") or "")
    accepted_revision = str(program.get("accepted_revision") or "")
    if candidate_status == "validated" and working_revision != accepted_revision:
        return "validated_unpublished"
    if accepted_revision and accepted_revision == working_revision:
        return "accepted"
    if working_revision:
        return "working_candidate"
    if bool(program.get("portable_document_contract")):
        return "portable_source"
    return "live_outputs_only" if program.get("live_outputs") else "source_metadata"


def complete_editable_sources_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Complete a compact index from document and persisted program identities."""

    completed = complete_domain_program_index(snapshot)
    domain = str(completed.get("domain") or "")
    pack = next(
        (
            candidate
            for candidate in VIBESCRIPT_WORKBENCH_PACKS.values()
            if candidate.domain == domain
        ),
        None,
    )
    grouped_api = api_groups(pack) if pack is not None else {}
    sources = []
    document = dict(completed.get("document") or {})
    document_name = str(document.get("name") or "")
    for program in list(completed.get("programs") or []):
        if not isinstance(program, Mapping):
            continue
        program_domain = str(program.get("domain") or "")
        if program_domain and program_domain != domain:
            continue
        source_id = str(program.get("program_id") or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{32}", source_id):
            continue
        revision = (
            str(
                program.get("working_revision")
                or program.get("accepted_revision")
                or ""
            )
            .strip()
            .lower()[:128]
        )
        program_reference = "/".join(
            (
                document_name,
                domain,
                str(program.get("label") or "")[:240],
            )
        )
        source = {
            "source_id": source_id,
            "source_kind": "vibescript_program",
            "domain": domain,
            "workbench": str(completed.get("workbench") or ""),
            "label": str(program.get("label") or "")[:240],
            "program": program_reference,
            "current_revision": revision,
            "status": _editable_source_status(program),
            "affected_outputs": _editable_source_outputs(program),
            "read_tool": "vibescript.read_source",
            "read_arguments": {
                "program": program_reference,
                "include_logs": False,
            },
            "build_tool": "vibescript.build_program",
            "edit_tool": "vibescript.edit_source",
            "delete_output_tool": "vibescript.delete_output",
            "delete_program_tool": "vibescript.delete_program",
        }
        accepted_revision = str(program.get("accepted_revision") or "")[:128]
        if accepted_revision:
            source["accepted_revision"] = accepted_revision
        if re.fullmatch(r"[0-9a-f]{64}", revision):
            source["build_arguments"] = {
                "program": source["program"],
                "expected_revision": revision,
            }
            source["edit_target_arguments"] = {
                "program": source["program"],
                "expected_revision": revision,
            }
            source["delete_target_arguments"] = {
                "program": source["program"],
                "expected_revision": revision,
                "reason": "Remove this source and its owned outputs.",
            }
        candidate = _compact_editable_source_candidate(program)
        if candidate is not None:
            source["latest_candidate"] = candidate
        if source["status"] == "invalid_artifact" and program.get("error"):
            source["error"] = str(program["error"])[:2000]
        sources.append(source)
    return {
        "schema": "vibecad-editable-sources-v1",
        "domain": domain,
        "workbench": str(completed.get("workbench") or ""),
        "source_count": len(sources),
        "source_limit": int(
            completed.get("program_limit") or MAX_DOMAIN_CONTEXT_PROGRAMS
        ),
        "sources_truncated": bool(completed.get("programs_truncated")),
        "sources_omitted": int(completed.get("programs_omitted") or 0),
        "api_groups": grouped_api,
        **(
            {"core_api": _core_api_snapshot(pack)}
            if (
                pack is not None
                and pack.domain != "assembly"
                and _CORE_API_EXPORTS_BY_DOMAIN.get(pack.domain)
            )
            else {}
        ),
        "tools": {
            "read_source": "vibescript.read_source",
            "read_api": "vibescript.read_api",
            "read_geometry": "vibescript.read_geometry",
            "read_placement": "vibescript.read_placement",
            "read_api_arguments": {
                "names": ["exact_callable_name"],
            },
            "read_api_group_arguments": {"groups": ["one_available_group"]},
            "available_api_groups": list(grouped_api),
            "create_program": (
                "vibescript.create_assembly"
                if pack is not None and pack.domain == "assembly"
                else (
                    "vibescript.create_part"
                    if pack is not None and pack.domain == "partdesign"
                    else "vibescript.create_program"
                )
            ),
            "build_program": "vibescript.build_program",
            "edit_source": "vibescript.edit_source",
            "set_inputs": "vibescript.set_inputs",
            "reconfigure_program": "vibescript.reconfigure_program",
            "delete_output": "vibescript.delete_output",
            "delete_program": "vibescript.delete_program",
            "edit_source_arguments": [
                "program",
                "expected_revision",
                "source",
            ],
            "source_argument": (
                "Pass the complete updated source text returned by "
                "vibescript.read_source."
            ),
        },
        "sources": sources,
    }


def editable_sources_snapshot(service: Any, domain: str) -> dict[str, Any]:
    """Return the completed editable-source index for compatibility callers."""

    return complete_editable_sources_snapshot(
        capture_editable_sources_snapshot(service, domain)
    )


def _assembly_context_metadata(obj: Any) -> dict[str, Any]:
    """Return bounded semantic connector hints without resolving geometry."""

    import VibeCADReferenceContracts as reference_contracts
    import VibeCADScriptedPublication as publication

    published = reference_contracts.published_object(obj)
    program_id = str(getattr(obj, PROP_PROGRAM_ID, "") or "")
    result: dict[str, Any] = {
        "transient_topology": bool(published is not None or program_id),
        "requires_semantic_interfaces": published is not None,
    }
    is_assembly = bool(
        getattr(obj, "isDerivedFrom", lambda _type: False)("Assembly::AssemblyObject")
    )
    is_part = bool(
        getattr(obj, "isDerivedFrom", lambda _type: False)("App::Part")
    ) and not bool(getattr(obj, "isDerivedFrom", lambda _type: False)("Part::Feature"))
    if is_assembly or is_part:
        from VibeCADAssemblyHierarchy import (
            AssemblyHierarchyError,
            capture_assembly_hierarchy,
            hierarchy_context,
        )

        try:
            hierarchy = capture_assembly_hierarchy(obj, detach_shapes=False)
        except AssemblyHierarchyError as exc:
            if is_assembly:
                result["eligible_flexible_subassembly"] = False
            result["eligible_detailed_bom_hierarchy"] = False
            result["assembly_hierarchy_error"] = str(exc)
        else:
            if is_assembly:
                result["eligible_flexible_subassembly"] = True
            else:
                result["eligible_flexible_subassembly"] = False
            result["eligible_detailed_bom_hierarchy"] = True
            result["assembly_hierarchy"] = hierarchy_context(hierarchy)
    if published is None:
        return result
    try:
        root = publication.model_root_for(published)
        table = json.loads(
            str(getattr(root, publication.PROP_INTERFACES, "{}") or "{}")
        )
    except (publication.PublicationError, ValueError) as exc:
        return {**result, "interface_error": str(exc)}
    if not isinstance(table, dict):
        return {
            **result,
            "interface_error": "Published interface table is not an object.",
        }
    output_key = str(getattr(published, publication.PROP_OUTPUT_KEY, "") or "")
    interfaces = []
    definitions = reference_contracts.interface_definitions_for_output(
        table,
        output_key,
    )
    for name, definition in sorted(definitions.items())[:64]:
        resolved = definition.get("resolved")
        if not isinstance(resolved, dict):
            continue
        geometry = list(resolved.get("geometry") or [])
        interfaces.append(
            {
                "interface_name": str(name),
                "subelements": list(resolved.get("subelements") or []),
                "geometry_types": [
                    str(item.get("geometry_type") or "")
                    for item in geometry
                    if isinstance(item, dict)
                ],
                **(
                    {"connector": dict(definition["connector"])}
                    if isinstance(definition.get("connector"), Mapping)
                    else {}
                ),
                **(
                    {"frame": dict(resolved["connector_frame"])}
                    if isinstance(resolved.get("connector_frame"), Mapping)
                    else {}
                ),
            }
        )
    result["published_interfaces"] = interfaces
    result["interfaces_truncated"] = len(definitions) > 64
    return result


def _part_document_shape_snapshot(
    service: Any,
    doc: Any,
    *,
    assembly_components: bool = False,
) -> dict[str, Any]:
    """Detach a bounded, relevance-first set of shapes without topology analysis."""

    document_uid = str(getattr(doc, "Uid", "") or "")
    priority_names: list[str] = []
    try:
        working = service.provider_working_set()
        for item in list(working.get("targets") or []):
            name = str(item.get("name") or "")
            if name and name not in priority_names:
                priority_names.append(name)
    except Exception:
        pass
    try:
        selection = service.selection_summary()
        for item in reversed(list(selection.get("selection") or [])):
            name = str(item.get("object") or "")
            if name:
                if name in priority_names:
                    priority_names.remove(name)
                priority_names.insert(0, name)
    except Exception:
        pass
    all_shape_objects: list[Any] = []
    objects_by_name: dict[str, Any] = {}
    for obj in list(getattr(doc, "Objects", []) or []):
        try:
            shape = getattr(obj, "Shape", None)
        except Exception:
            continue
        if shape is None:
            continue
        name = str(getattr(obj, "Name", "") or "")
        all_shape_objects.append(obj)
        if name:
            objects_by_name[name] = obj
    ordered: list[Any] = []
    ordered_names: set[str] = set()
    for name in priority_names:
        obj = objects_by_name.get(name)
        if obj is not None and name not in ordered_names:
            ordered.append(obj)
            ordered_names.add(name)
    for obj in all_shape_objects:
        name = str(getattr(obj, "Name", "") or "")
        if name not in ordered_names:
            ordered.append(obj)
            ordered_names.add(name)
    captured: list[dict[str, Any]] = []
    for obj in ordered[:MAX_PART_CONTEXT_SHAPES]:
        object_name = str(getattr(obj, "Name", "") or "")
        shape = getattr(obj, "Shape", None)
        try:
            detached = shape.copy()
        except Exception as exc:
            captured.append(
                {
                    "name": object_name,
                    "label": str(getattr(obj, "Label", "") or ""),
                    "type_id": str(getattr(obj, "TypeId", "") or ""),
                    "reference": {
                        "document_uid": document_uid,
                        "object_name": object_name,
                    },
                    "error": f"Could not detach shape: {exc}",
                }
            )
            continue
        captured.append(
            {
                "name": object_name,
                "label": str(getattr(obj, "Label", "") or ""),
                "type_id": str(getattr(obj, "TypeId", "") or ""),
                "reference": {
                    "document_uid": document_uid,
                    "object_name": object_name,
                },
                "_detached_shape": detached,
                **(_assembly_context_metadata(obj) if assembly_components else {}),
            }
        )
    return {
        "object_count": len(all_shape_objects),
        "object_limit": MAX_PART_CONTEXT_SHAPES,
        "objects_truncated": len(all_shape_objects) > len(captured),
        "objects_omitted": max(0, len(all_shape_objects) - len(captured)),
        "objects": captured,
    }


def _sketcher_document_snapshot(doc: Any) -> dict[str, Any]:
    """Capture bounded native sketch state without solving or traversing topology."""

    all_sketches = [
        obj
        for obj in list(getattr(doc, "Objects", []) or [])
        if bool(
            getattr(obj, "isDerivedFrom", lambda _type: False)("Sketcher::SketchObject")
        )
    ]
    captured = []
    for obj in all_sketches[:MAX_SKETCHER_CONTEXT_SKETCHES]:
        geometry_count = int(getattr(obj, "GeometryCount", 0))
        constraint_count = int(getattr(obj, "ConstraintCount", 0))
        geometry = []
        for index, native in enumerate(
            list(getattr(obj, "Geometry", []) or [])[:MAX_SKETCHER_CONTEXT_ITEMS]
        ):
            try:
                construction = bool(obj.getConstruction(index))
            except Exception:
                construction = False
            geometry.append(
                {
                    "index": index,
                    "native_type": type(native).__name__,
                    "construction": construction,
                }
            )
        constraints = []
        for index, native in enumerate(
            list(getattr(obj, "Constraints", []) or [])[:MAX_SKETCHER_CONTEXT_ITEMS]
        ):
            value = float(getattr(native, "Value", 0.0) or 0.0)
            constraints.append(
                {
                    "index": index,
                    "name": str(getattr(native, "Name", "") or ""),
                    "native_type": str(getattr(native, "Type", "") or ""),
                    "value": value if math.isfinite(value) else None,
                    "driving": bool(getattr(native, "Driving", True)),
                    "active": bool(getattr(native, "IsActive", True)),
                    "virtual": bool(getattr(native, "InVirtualSpace", False)),
                }
            )
        expressions = []
        for raw in list(getattr(obj, "ExpressionEngine", []) or [])[
            :MAX_SKETCHER_CONTEXT_ITEMS
        ]:
            if not isinstance(raw, (list, tuple)) or len(raw) < 2:
                continue
            expressions.append({"path": str(raw[0]), "expression": str(raw[1])})
        attachment_support = []
        for raw in list(getattr(obj, "AttachmentSupport", []) or [])[:4]:
            if not isinstance(raw, (list, tuple)) or len(raw) < 2:
                continue
            target = raw[0]
            attachment_support.append(
                {
                    "object_name": str(getattr(target, "Name", "") or ""),
                    "subelements": [str(value) for value in list(raw[1] or [])[:4]],
                }
            )
        native_external_groups = list(getattr(obj, "ExternalGeometry", []) or [])
        external_geometry = []
        native_reference_index = 0
        for group_index, raw in enumerate(native_external_groups):
            if len(external_geometry) >= MAX_SKETCHER_CONTEXT_ITEMS:
                break
            if not isinstance(raw, (list, tuple)) or len(raw) < 2:
                external_geometry.append(
                    {
                        "link_index": native_reference_index,
                        "link_group_index": group_index,
                        "link_subelement_index": 0,
                        "native_geometry_id": -3 - native_reference_index,
                        "error": "FreeCAD returned malformed external geometry metadata.",
                    }
                )
                native_reference_index += 1
                continue
            target = raw[0]
            raw_subelements = raw[1]
            subelements = (
                [str(raw_subelements)]
                if isinstance(raw_subelements, str)
                else [str(value) for value in list(raw_subelements or [])]
            )
            for subelement_index, subelement in enumerate(subelements):
                if len(external_geometry) >= MAX_SKETCHER_CONTEXT_ITEMS:
                    break
                external_geometry.append(
                    {
                        "link_index": native_reference_index,
                        "link_group_index": group_index,
                        "link_subelement_index": subelement_index,
                        "native_geometry_id": -3 - native_reference_index,
                        "object_name": str(getattr(target, "Name", "") or ""),
                        "object_label": str(getattr(target, "Label", "") or ""),
                        "object_type_id": str(getattr(target, "TypeId", "") or ""),
                        "subelements": [subelement],
                    }
                )
                native_reference_index += 1
        external_geometry_count = sum(
            1
            if not isinstance(raw, (list, tuple)) or len(raw) < 2
            else len([raw[1]] if isinstance(raw[1], str) else list(raw[1] or []))
            for raw in native_external_groups
        )
        placement = getattr(obj, "AttachmentOffset", None)
        position = getattr(placement, "Base", None)
        rotation = getattr(placement, "Rotation", None)
        item: dict[str, Any] = {
            "name": str(getattr(obj, "Name", "") or ""),
            "label": str(getattr(obj, "Label", "") or ""),
            "type_id": str(getattr(obj, "TypeId", "") or ""),
            "geometry_count": geometry_count,
            "geometry": geometry,
            "geometry_truncated": geometry_count > len(geometry),
            "external_geometry_count": external_geometry_count,
            "external_geometry": external_geometry,
            "external_geometry_truncated": external_geometry_count
            > len(external_geometry),
            "constraint_count": constraint_count,
            "constraints": constraints,
            "constraints_truncated": constraint_count > len(constraints),
            "expressions": expressions,
            "expressions_truncated": len(
                list(getattr(obj, "ExpressionEngine", []) or [])
            )
            > len(expressions),
            "degrees_of_freedom": int(getattr(obj, "DoF", 0)),
            "fully_constrained": bool(getattr(obj, "FullyConstrained", False)),
            "conflicting_constraints": sorted(
                int(value)
                for value in list(getattr(obj, "ConflictingConstraints", []) or [])
            ),
            "redundant_constraints": sorted(
                int(value)
                for value in list(getattr(obj, "RedundantConstraints", []) or [])
            ),
            "partially_redundant_constraints": sorted(
                int(value)
                for value in list(
                    getattr(obj, "PartiallyRedundantConstraints", []) or []
                )
            ),
            "malformed_constraints": sorted(
                int(value)
                for value in list(getattr(obj, "MalformedConstraints", []) or [])
            ),
            "map_mode": str(getattr(obj, "MapMode", "") or ""),
            "attachment_support": attachment_support,
            "attachment_offset": {
                "position": [
                    float(getattr(position, axis, 0.0)) for axis in ("x", "y", "z")
                ],
                "rotation": [
                    float(value) for value in getattr(rotation, "Q", (0, 0, 0, 1))
                ],
            },
        }
        shape = getattr(obj, "Shape", None)
        if shape is not None:
            try:
                item["_detached_shape"] = shape.copy()
            except Exception as exc:
                item["profile_error"] = f"Could not detach sketch shape: {exc}"
        captured.append(item)
    return {
        "sketch_count": len(all_sketches),
        "sketch_limit": MAX_SKETCHER_CONTEXT_SKETCHES,
        "sketches_truncated": len(all_sketches) > len(captured),
        "sketches_omitted": max(0, len(all_sketches) - len(captured)),
        "sketches": captured,
    }


def _draft_document_snapshot(doc: Any) -> dict[str, Any]:
    """Capture bounded editable Draft properties without recompute or topology work."""

    try:
        from draftutils.utils import get_type
    except Exception:
        get_type = lambda _obj: ""  # noqa: E731
    supported = {"Wire", "Circle", "Rectangle", "BSpline", "Array", "Text"}
    all_objects: list[tuple[Any, str]] = []
    for obj in list(getattr(doc, "Objects", []) or []):
        try:
            draft_type = str(get_type(obj) or "")
        except Exception:
            draft_type = ""
        if draft_type in supported:
            all_objects.append((obj, draft_type))
    captured = []
    for obj, draft_type in all_objects[:MAX_DRAFT_CONTEXT_OBJECTS]:
        placement = getattr(obj, "Placement", None)
        position = getattr(placement, "Base", None)
        rotation = getattr(placement, "Rotation", None)
        item: dict[str, Any] = {
            "name": str(getattr(obj, "Name", "") or ""),
            "label": str(getattr(obj, "Label", "") or ""),
            "type_id": str(getattr(obj, "TypeId", "") or ""),
            "draft_type": draft_type,
            "proxy_class": type(getattr(obj, "Proxy", None)).__name__,
            "placement": {
                "position": [
                    float(getattr(position, axis, 0.0)) for axis in ("x", "y", "z")
                ],
                "rotation": [
                    float(value)
                    for value in getattr(rotation, "Q", (0.0, 0.0, 0.0, 1.0))
                ],
            },
            "program_id": str(getattr(obj, PROP_PROGRAM_ID, "") or ""),
            "program_output": str(getattr(obj, PROP_PROGRAM_OUTPUT, "") or ""),
            "program_revision": str(getattr(obj, PROP_PROGRAM_REVISION, "") or ""),
        }
        if draft_type in {"Wire", "BSpline"}:
            points = list(getattr(obj, "Points", []) or [])
            item.update(
                {
                    "point_count": len(points),
                    "points": [
                        [float(point.x), float(point.y), float(point.z)]
                        for point in points[:MAX_DRAFT_CONTEXT_POINTS]
                    ],
                    "points_truncated": len(points) > MAX_DRAFT_CONTEXT_POINTS,
                    "closed": bool(getattr(obj, "Closed", False)),
                    "make_face": bool(getattr(obj, "MakeFace", False)),
                }
            )
            if draft_type == "BSpline":
                item["parameterization"] = float(getattr(obj, "Parameterization", 1.0))
        elif draft_type == "Circle":
            item.update(
                {
                    "radius": float(getattr(obj, "Radius", 0.0)),
                    "start_angle": float(getattr(obj, "FirstAngle", 0.0)),
                    "end_angle": float(getattr(obj, "LastAngle", 0.0)),
                    "make_face": bool(getattr(obj, "MakeFace", False)),
                }
            )
        elif draft_type == "Rectangle":
            item.update(
                {
                    "length": float(getattr(obj, "Length", 0.0)),
                    "height": float(getattr(obj, "Height", 0.0)),
                    "make_face": bool(getattr(obj, "MakeFace", False)),
                }
            )
        elif draft_type == "Array":
            base = getattr(obj, "Base", None)
            item.update(
                {
                    "base": {
                        "name": str(getattr(base, "Name", "") or ""),
                        "label": str(getattr(base, "Label", "") or ""),
                    },
                    "array_kind": str(getattr(obj, "ArrayType", "") or ""),
                    "use_link": bool(
                        getattr(getattr(obj, "Proxy", None), "use_link", False)
                    ),
                    "fuse": bool(getattr(obj, "Fuse", False)),
                    "count": int(getattr(obj, "Count", 0)),
                    "number_x": int(getattr(obj, "NumberX", 0)),
                    "number_y": int(getattr(obj, "NumberY", 0)),
                    "number_z": int(getattr(obj, "NumberZ", 0)),
                    "number_polar": int(getattr(obj, "NumberPolar", 0)),
                    "angle_degrees": float(getattr(obj, "Angle", 0.0)),
                    "interval_x": [
                        float(value) for value in getattr(obj, "IntervalX", (0, 0, 0))
                    ],
                    "interval_y": [
                        float(value) for value in getattr(obj, "IntervalY", (0, 0, 0))
                    ],
                    "interval_z": [
                        float(value) for value in getattr(obj, "IntervalZ", (0, 0, 0))
                    ],
                    "center": [
                        float(value) for value in getattr(obj, "Center", (0, 0, 0))
                    ],
                    "axis": [float(value) for value in getattr(obj, "Axis", (0, 0, 1))],
                }
            )
        else:
            lines = [str(value) for value in list(getattr(obj, "Text", []) or [])]
            view = getattr(obj, "ViewObject", None)
            item.update(
                {
                    "line_count": len(lines),
                    "lines": lines[:MAX_DRAFT_CONTEXT_POINTS],
                    "lines_truncated": len(lines) > MAX_DRAFT_CONTEXT_POINTS,
                    "display_mode": str(getattr(view, "DisplayMode", "") or ""),
                    "height": (
                        float(getattr(view, "FontSize"))
                        if view is not None and hasattr(view, "FontSize")
                        else None
                    ),
                    "line_spacing": (
                        float(getattr(view, "LineSpacing"))
                        if view is not None and hasattr(view, "LineSpacing")
                        else None
                    ),
                }
            )
        captured.append(item)
    return {
        "object_count": len(all_objects),
        "object_limit": MAX_DRAFT_CONTEXT_OBJECTS,
        "objects_truncated": len(all_objects) > len(captured),
        "objects_omitted": max(0, len(all_objects) - len(captured)),
        "objects": captured,
    }


_SPREADSHEET_ADDRESS = re.compile(r"^([A-Z]{1,2})([1-9][0-9]{0,4})$")


def _spreadsheet_column_number(label: str) -> int:
    value = 0
    for character in label:
        value = value * 26 + ord(character) - ord("A") + 1
    return value


def _spreadsheet_column_label(number: int) -> str:
    value = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        value = chr(ord("A") + remainder) + value
    return value


def _spreadsheet_document_snapshot(doc: Any) -> dict[str, Any]:
    """Capture bounded native sheet state without enumerating every non-empty cell."""

    all_sheets = [
        obj
        for obj in list(getattr(doc, "Objects", []) or [])
        if str(getattr(obj, "TypeId", "")) == "Spreadsheet::Sheet"
    ]
    sheets: list[dict[str, Any]] = []
    for sheet in all_sheets[:MAX_SPREADSHEET_CONTEXT_SHEETS]:
        raw_range = sheet.getUsedRange()
        used_range = (
            [str(value) for value in raw_range]
            if isinstance(raw_range, tuple)
            and len(raw_range) == 2
            and all(_SPREADSHEET_ADDRESS.fullmatch(str(value)) for value in raw_range)
            else []
        )
        addresses: list[str] = []
        span_area = 0
        if used_range:
            first = _SPREADSHEET_ADDRESS.fullmatch(used_range[0])
            last = _SPREADSHEET_ADDRESS.fullmatch(used_range[1])
            assert first is not None and last is not None
            first_column = _spreadsheet_column_number(first.group(1))
            last_column = _spreadsheet_column_number(last.group(1))
            first_row = int(first.group(2))
            last_row = int(last.group(2))
            width = last_column - first_column + 1
            height = last_row - first_row + 1
            span_area = width * height
            sample_count = min(span_area, MAX_SPREADSHEET_CONTEXT_CELLS)
            if sample_count == span_area:
                indexes = range(span_area)
            elif sample_count > 1:
                indexes = [
                    round(index * (span_area - 1) / (sample_count - 1))
                    for index in range(sample_count)
                ]
            else:
                indexes = [0]
            for flat_index in indexes:
                row_offset, column_offset = divmod(flat_index, width)
                addresses.append(
                    f"{_spreadsheet_column_label(first_column + column_offset)}"
                    f"{first_row + row_offset}"
                )
        cells = []
        sampled_merges: dict[str, dict[str, Any]] = {}
        address_merges: dict[str, dict[str, Any]] = {}
        for address in addresses:
            if hasattr(sheet, "getCellMerge"):
                try:
                    raw_merge = sheet.getCellMerge(address)
                    if (
                        isinstance(raw_merge, tuple)
                        and len(raw_merge) == 3
                        and isinstance(raw_merge[0], str)
                        and type(raw_merge[1]) is int
                        and type(raw_merge[2]) is int
                        and (raw_merge[1] > 1 or raw_merge[2] > 1)
                    ):
                        anchor = str(raw_merge[0])
                        match = _SPREADSHEET_ADDRESS.fullmatch(anchor)
                        if match is not None:
                            end_column = _spreadsheet_column_label(
                                _spreadsheet_column_number(match.group(1))
                                + int(raw_merge[2])
                                - 1
                            )
                            end_row = int(match.group(2)) + int(raw_merge[1]) - 1
                            record = {
                                "range_address": f"{anchor}:{end_column}{end_row}",
                                "anchor": anchor,
                                "rows": int(raw_merge[1]),
                                "columns": int(raw_merge[2]),
                            }
                            sampled_merges[anchor] = record
                            address_merges[address] = record
                except (AttributeError, TypeError, ValueError):
                    pass
            try:
                contents = str(sheet.getContents(address) or "")
            except (KeyError, ValueError):
                contents = ""
            try:
                alias = str(sheet.getAlias(address) or "")
            except (KeyError, ValueError):
                alias = ""
            if not contents and not alias:
                continue
            try:
                display_unit = str(sheet.getDisplayUnit(address) or "")
            except (KeyError, ValueError):
                display_unit = ""
            item = {
                "address": address,
                "contents": contents[:4096],
                "contents_truncated": len(contents) > 4096,
                "alias": alias,
                "display_unit": display_unit,
            }
            if address in address_merges:
                item["merged_range"] = dict(address_merges[address])
            try:
                style = sheet.getStyle(address)
                alignment = sheet.getAlignment(address)
                item["style"] = sorted(str(value) for value in style) if style else []
                item["alignment"] = (
                    sorted(str(value) for value in alignment) if alignment else []
                )
            except (KeyError, ValueError):
                item["style"] = []
                item["alignment"] = []
            cells.append(item)
        merged_ranges = list(sampled_merges.values())
        merged_ranges_source = "bounded_native_sampling"
        raw_validation = str(getattr(sheet, "VibeCADSpreadsheetValidation", "") or "")
        if raw_validation and len(raw_validation) <= 65_536:
            try:
                validation = json.loads(raw_validation)
                accepted_merges = validation.get("merged_ranges")
                if (
                    isinstance(accepted_merges, list)
                    and len(accepted_merges) <= 256
                    and all(
                        isinstance(item, dict)
                        and set(item) == {"range_address", "anchor", "rows", "columns"}
                        and isinstance(item["range_address"], str)
                        and isinstance(item["anchor"], str)
                        and type(item["rows"]) is int
                        and type(item["columns"]) is int
                        for item in accepted_merges
                    )
                ):
                    merged_ranges = [dict(item) for item in accepted_merges]
                    merged_ranges_source = "accepted_vibescript_validation"
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        sheets.append(
            {
                "name": str(getattr(sheet, "Name", "") or ""),
                "label": str(getattr(sheet, "Label", "") or ""),
                "type_id": "Spreadsheet::Sheet",
                "used_range": used_range,
                "used_span_cell_count": span_area,
                "sampled_address_count": len(addresses),
                "sampled_addresses_truncated": span_area > len(addresses),
                "nonempty_sample_count": len(cells),
                "cells": cells,
                "merged_ranges": merged_ranges,
                "merged_ranges_source": merged_ranges_source,
                "merged_ranges_may_be_truncated": (
                    merged_ranges_source == "bounded_native_sampling"
                    and span_area > len(addresses)
                ),
                "native_state": sorted(
                    str(value) for value in list(getattr(sheet, "State", []) or [])
                ),
                "native_status": str(sheet.getStatusString()),
            }
        )
    return {
        "sheet_count": len(all_sheets),
        "sheet_limit": MAX_SPREADSHEET_CONTEXT_SHEETS,
        "sheets_truncated": len(all_sheets) > len(sheets),
        "sheets_omitted": max(0, len(all_sheets) - len(sheets)),
        "cell_sample_limit_per_sheet": MAX_SPREADSHEET_CONTEXT_CELLS,
        "sheets": sheets,
    }


def _material_document_snapshot(doc: Any) -> dict[str, Any]:
    """Capture bounded physical/view capability without opening the material catalog."""

    document_uid = str(getattr(doc, "Uid", "") or "")
    capable: list[Any] = []
    for obj in list(getattr(doc, "Objects", []) or []):
        view = getattr(obj, "ViewObject", None)
        if hasattr(obj, "ShapeMaterial") or view is not None:
            capable.append(obj)
    targets = []
    for obj in capable[:MAX_MATERIAL_CONTEXT_TARGETS]:
        view = getattr(obj, "ViewObject", None)
        material = (
            getattr(obj, "ShapeMaterial", None)
            if hasattr(obj, "ShapeMaterial")
            else None
        )
        supported = []
        appearance: dict[str, Any] = {}
        display_modes: list[str] = []
        if view is not None:
            if hasattr(view, "ShapeAppearance"):
                supported.append("ShapeAppearance")
                try:
                    materials = list(view.ShapeAppearance or [])
                    appearance["shape_appearance_count"] = len(materials)
                    if hasattr(view, "ShapeColor"):
                        appearance["shape_color"] = [
                            float(value) for value in tuple(view.ShapeColor)[:3]
                        ]
                    elif materials:
                        appearance["shape_color"] = [
                            float(value)
                            for value in tuple(materials[0].DiffuseColor)[:3]
                        ]
                    if hasattr(view, "Transparency"):
                        appearance["transparency"] = int(view.Transparency)
                    elif materials:
                        appearance["transparency"] = int(
                            round(float(materials[0].Transparency) * 100.0)
                        )
                except Exception as exc:
                    appearance["surface_error"] = str(exc)
            for api_name, native_name in (
                ("line_color", "LineColor"),
                ("point_color", "PointColor"),
                ("line_width", "LineWidth"),
                ("point_size", "PointSize"),
                ("display_mode", "DisplayMode"),
                ("visibility", "Visibility"),
                ("selectable", "Selectable"),
            ):
                if not hasattr(view, native_name):
                    continue
                supported.append(native_name)
                try:
                    value = getattr(view, native_name)
                    if native_name in {"LineColor", "PointColor"}:
                        value = [float(channel) for channel in tuple(value)[:3]]
                    elif native_name in {"LineWidth", "PointSize"}:
                        value = float(value)
                    elif native_name in {"Visibility", "Selectable"}:
                        value = bool(value)
                    else:
                        value = str(value)
                    appearance[api_name] = value
                except Exception as exc:
                    appearance[f"{api_name}_error"] = str(exc)
            getter = getattr(view, "getEnumerationsOfProperty", None)
            if callable(getter) and hasattr(view, "DisplayMode"):
                try:
                    display_modes = [
                        str(value) for value in list(getter("DisplayMode") or [])
                    ]
                except Exception:
                    display_modes = []
        managed_program = str(getattr(obj, PROP_PROGRAM_DOMAIN, "") or "") == "material"
        targets.append(
            {
                "name": str(getattr(obj, "Name", "") or ""),
                "label": str(getattr(obj, "Label", "") or ""),
                "type_id": str(getattr(obj, "TypeId", "") or ""),
                "reference": {
                    "document_uid": document_uid,
                    "object_name": str(getattr(obj, "Name", "") or ""),
                },
                "physical_assignment_supported": material is not None,
                "current_material": (
                    {
                        "uuid": str(getattr(material, "UUID", "") or ""),
                        "name": str(getattr(material, "Name", "") or ""),
                    }
                    if material is not None
                    else None
                ),
                "appearance_supported_properties": sorted(set(supported)),
                "display_modes": display_modes[:128],
                "display_modes_truncated": len(display_modes) > 128,
                "appearance": appearance,
                "managed_material_output": managed_program,
                "eligible_target": not managed_program
                and bool(material is not None or supported),
                "ineligible_reason": (
                    "Managed Material carriers cannot be targets."
                    if managed_program
                    else ""
                ),
            }
        )
    return {
        "target_count": len(capable),
        "target_limit": MAX_MATERIAL_CONTEXT_TARGETS,
        "targets_truncated": len(capable) > len(targets),
        "targets_omitted": max(0, len(capable) - len(targets)),
        "targets": targets,
    }


def _mesh_document_snapshot(doc: Any) -> dict[str, Any]:
    """Capture bounded, already-published Mesh state without running diagnostics."""

    all_objects = [
        obj
        for obj in list(getattr(doc, "Objects", []) or [])
        if str(getattr(obj, "TypeId", "") or "") == "Mesh::Feature"
    ]
    document_uid = str(getattr(doc, "Uid", "") or "")
    captured = []
    for obj in all_objects[:MAX_MESH_CONTEXT_OBJECTS]:
        item: dict[str, Any] = {
            "name": str(getattr(obj, "Name", "") or ""),
            "label": str(getattr(obj, "Label", "") or ""),
            "type_id": "Mesh::Feature",
            "program_id": str(getattr(obj, PROP_PROGRAM_ID, "") or ""),
            "program_output": str(getattr(obj, PROP_PROGRAM_OUTPUT, "") or ""),
            "program_revision": str(getattr(obj, PROP_PROGRAM_REVISION, "") or ""),
            "reference": {
                "document_uid": document_uid,
                "object_name": str(getattr(obj, "Name", "") or ""),
            },
        }
        try:
            mesh = obj.Mesh
            box = mesh.BoundBox
            bounds = [
                float(box.XMin),
                float(box.YMin),
                float(box.ZMin),
                float(box.XMax),
                float(box.YMax),
                float(box.ZMax),
            ]
            item["native_summary"] = {
                "points": int(mesh.CountPoints),
                "facets": int(mesh.CountFacets),
                "edges": int(mesh.CountEdges),
                "segments": int(mesh.countSegments()),
                "bounds": {
                    "minimum": [
                        value if math.isfinite(value) else None for value in bounds[:3]
                    ],
                    "maximum": [
                        value if math.isfinite(value) else None for value in bounds[3:]
                    ],
                },
            }
        except Exception as exc:
            item["native_summary_error"] = f"{type(exc).__name__}: {exc}"
        validation_property = (
            "VibeCADReverseEngineeringValidation"
            if hasattr(obj, "VibeCADReverseEngineeringValidation")
            else (
                "VibeCADMeshPartValidation"
                if hasattr(obj, "VibeCADMeshPartValidation")
                else "VibeCADMeshValidation"
            )
        )
        validation = str(getattr(obj, validation_property, "") or "")
        if validation:
            item["accepted_validation_property"] = validation_property
            encoded_size = len(validation.encode("utf-8", errors="replace"))
            if encoded_size <= MAX_CONTEXT_VALUE_BYTES:
                item["_validation_json"] = validation
            else:
                item["accepted_validation_omitted"] = True
                item["accepted_validation_json_bytes"] = encoded_size
        captured.append(item)
    return {
        "object_count": len(all_objects),
        "object_limit": MAX_MESH_CONTEXT_OBJECTS,
        "objects_truncated": len(all_objects) > len(captured),
        "objects_omitted": max(0, len(all_objects) - len(captured)),
        "objects": captured,
    }


def _points_document_snapshot(doc: Any) -> dict[str, Any]:
    """Capture bounded native Points state without materializing whole clouds."""

    all_objects = []
    for obj in list(getattr(doc, "Objects", []) or []):
        try:
            matches = bool(obj.isDerivedFrom("Points::Feature"))
        except Exception:
            matches = str(getattr(obj, "TypeId", "") or "").startswith("Points::")
        if matches:
            all_objects.append(obj)
    document_uid = str(getattr(doc, "Uid", "") or "")
    captured = []
    for obj in all_objects[:MAX_POINTS_CONTEXT_OBJECTS]:
        item: dict[str, Any] = {
            "name": str(getattr(obj, "Name", "") or ""),
            "label": str(getattr(obj, "Label", "") or ""),
            "type_id": str(getattr(obj, "TypeId", "") or ""),
            "program_id": str(getattr(obj, PROP_PROGRAM_ID, "") or ""),
            "program_output": str(getattr(obj, PROP_PROGRAM_OUTPUT, "") or ""),
            "program_revision": str(getattr(obj, PROP_PROGRAM_REVISION, "") or ""),
            "reference": {
                "document_uid": document_uid,
                "object_name": str(getattr(obj, "Name", "") or ""),
            },
        }
        try:
            kernel = obj.Points
            count = int(kernel.CountPoints)
            box = kernel.BoundBox
            sample_count = min(MAX_POINTS_CONTEXT_SAMPLE, max(0, count))
            sample = (
                list(kernel.fromSegment(range(sample_count)).Points)
                if sample_count
                else []
            )
            bounds = [
                float(box.XMin),
                float(box.YMin),
                float(box.ZMin),
                float(box.XMax),
                float(box.YMax),
                float(box.ZMax),
            ]
            width = int(getattr(obj, "Width", 0) or 0)
            height = int(getattr(obj, "Height", 0) or 0)
            item["native_summary"] = {
                "points": count,
                "bounds": {
                    "minimum": [
                        value if math.isfinite(value) else None for value in bounds[:3]
                    ],
                    "maximum": [
                        value if math.isfinite(value) else None for value in bounds[3:]
                    ],
                },
                "sample": [
                    [float(point.x), float(point.y), float(point.z)] for point in sample
                ],
                "sample_truncated": count > sample_count,
                "structured": (
                    {"width": width, "height": height}
                    if width > 0 and height > 0 and width * height == count
                    else None
                ),
                "attribute_properties": [
                    name
                    for name in ("Color", "Intensity", "Normal")
                    if name in set(getattr(obj, "PropertiesList", []) or [])
                ],
            }
        except Exception as exc:
            item["native_summary_error"] = f"{type(exc).__name__}: {exc}"
        validation = str(getattr(obj, "VibeCADPointsValidation", "") or "")
        if validation:
            encoded_size = len(validation.encode("utf-8", errors="replace"))
            if encoded_size <= MAX_CONTEXT_VALUE_BYTES:
                item["_validation_json"] = validation
            else:
                item["accepted_validation_omitted"] = True
                item["accepted_validation_json_bytes"] = encoded_size
        captured.append(item)
    return {
        "object_count": len(all_objects),
        "object_limit": MAX_POINTS_CONTEXT_OBJECTS,
        "objects_truncated": len(all_objects) > len(captured),
        "objects_omitted": max(0, len(all_objects) - len(captured)),
        "sample_limit_per_object": MAX_POINTS_CONTEXT_SAMPLE,
        "objects": captured,
    }


def _inspection_document_snapshot(doc: Any) -> dict[str, Any]:
    """Capture bounded native Inspection definitions without recomputation."""

    all_objects = [
        obj
        for obj in list(getattr(doc, "Objects", []) or [])
        if str(getattr(obj, "TypeId", "") or "")
        in {"Inspection::Feature", "Inspection::Group"}
    ]
    captured = []
    for obj in all_objects[:MAX_INSPECTION_CONTEXT_OBJECTS]:
        type_id = str(getattr(obj, "TypeId", "") or "")
        item: dict[str, Any] = {
            "name": str(getattr(obj, "Name", "") or ""),
            "label": str(getattr(obj, "Label", "") or ""),
            "type_id": type_id,
            "program_id": str(getattr(obj, PROP_PROGRAM_ID, "") or ""),
            "program_output": str(getattr(obj, PROP_PROGRAM_OUTPUT, "") or ""),
            "program_revision": str(getattr(obj, PROP_PROGRAM_REVISION, "") or ""),
        }
        if type_id == "Inspection::Feature":
            actual = getattr(obj, "Actual", None)
            nominals = list(getattr(obj, "Nominals", []) or [])
            item.update(
                {
                    "actual": str(getattr(actual, "Name", "") or ""),
                    "nominals": [
                        str(getattr(value, "Name", "") or "") for value in nominals[:16]
                    ],
                    "nominals_truncated": len(nominals) > 16,
                    "search_radius": float(getattr(obj, "SearchRadius", 0.0) or 0.0),
                    "thickness": float(getattr(obj, "Thickness", 0.0) or 0.0),
                    "distance_count": len(getattr(obj, "Distances", []) or []),
                    "derived_state": str(getattr(obj, "VibeCADDerivedState", "") or ""),
                    "passed": (
                        bool(getattr(obj, "VibeCADPassed"))
                        if hasattr(obj, "VibeCADPassed")
                        else None
                    ),
                }
            )
        else:
            members = list(getattr(obj, "Group", []) or [])
            item.update(
                {
                    "members": [
                        str(getattr(value, "Name", "") or "") for value in members[:64]
                    ],
                    "members_truncated": len(members) > 64,
                }
            )
        captured.append(item)
    return {
        "object_count": len(all_objects),
        "object_limit": MAX_INSPECTION_CONTEXT_OBJECTS,
        "objects_truncated": len(all_objects) > len(captured),
        "objects_omitted": max(0, len(all_objects) - len(captured)),
        "objects": captured,
    }


def _robot_document_snapshot(doc: Any) -> dict[str, Any]:
    """Capture bounded native Robot state without copying trajectories."""

    native_types = {
        "Robot::RobotObject",
        "Robot::TrajectoryObject",
        "Robot::TrajectoryDressUpObject",
    }
    all_objects = [
        obj
        for obj in list(getattr(doc, "Objects", []) or [])
        if str(getattr(obj, "TypeId", "") or "") in native_types
        or str(getattr(obj, PROP_PROGRAM_DOMAIN, "") or "") == "robot"
    ]
    captured = []
    for obj in all_objects[:MAX_ROBOT_CONTEXT_OBJECTS]:
        type_id = str(getattr(obj, "TypeId", "") or "")
        item: dict[str, Any] = {
            "name": str(getattr(obj, "Name", "") or ""),
            "label": str(getattr(obj, "Label", "") or ""),
            "type_id": type_id,
            "program_id": str(getattr(obj, PROP_PROGRAM_ID, "") or ""),
            "program_output": str(getattr(obj, PROP_PROGRAM_OUTPUT, "") or ""),
            "program_revision": str(getattr(obj, PROP_PROGRAM_REVISION, "") or ""),
        }
        if type_id == "Robot::RobotObject":
            item["native_summary"] = {
                "axis_positions": [
                    float(getattr(obj, f"Axis{axis}", 0.0) or 0.0)
                    for axis in range(1, 7)
                ],
                "home": [
                    float(value) for value in list(getattr(obj, "Home", []) or [])
                ],
                "tcp_position": [float(value) for value in obj.Tcp.Base],
                "base_position": [float(value) for value in obj.Base.Base],
                "tool_position": [float(value) for value in obj.Tool.Base],
            }
        elif type_id in {
            "Robot::TrajectoryObject",
            "Robot::TrajectoryDressUpObject",
        }:
            item["native_summary"] = {
                "waypoint_count": int(getattr(obj, "VibeCADWaypointCount", 0) or 0),
                "length": float(getattr(obj, "VibeCADTrajectoryLength", 0.0) or 0.0),
                "duration": float(
                    getattr(obj, "VibeCADTrajectoryDuration", 0.0) or 0.0
                ),
                "source": str(getattr(getattr(obj, "Source", None), "Name", "") or ""),
                "frozen": (
                    bool(obj.isFrozen())
                    if type_id == "Robot::TrajectoryDressUpObject"
                    and callable(getattr(obj, "isFrozen", None))
                    else False
                ),
            }
        else:
            item["native_summary"] = {
                "robot": str(
                    getattr(getattr(obj, "VibeCADRobot", None), "Name", "") or ""
                ),
                "trajectory": str(
                    getattr(getattr(obj, "VibeCADTrajectory", None), "Name", "") or ""
                ),
                "sample_count": int(getattr(obj, "VibeCADSampleCount", 0) or 0),
                "reachable_count": int(getattr(obj, "VibeCADReachableCount", 0) or 0),
                "unreachable_count": int(
                    getattr(obj, "VibeCADUnreachableCount", 0) or 0
                ),
            }
        validation = str(getattr(obj, "VibeCADRobotValidation", "") or "")
        if validation:
            encoded_size = len(validation.encode("utf-8", errors="replace"))
            if encoded_size <= MAX_CONTEXT_VALUE_BYTES:
                item["_validation_json"] = validation
            else:
                item["accepted_validation_omitted"] = True
                item["accepted_validation_json_bytes"] = encoded_size
        captured.append(item)
    return {
        "object_count": len(all_objects),
        "object_limit": MAX_ROBOT_CONTEXT_OBJECTS,
        "objects_truncated": len(all_objects) > len(captured),
        "objects_omitted": max(0, len(all_objects) - len(captured)),
        "objects": captured,
    }


def _fem_document_snapshot(doc: Any) -> dict[str, Any]:
    """Capture bounded native FEM graph state without meshing or solving."""

    native_types = {
        "Fem::FemAnalysis",
        "Fem::FemSolverObjectPython",
        "App::MaterialObjectPython",
        "Fem::ConstraintFixed",
        "Fem::ConstraintForce",
        "Fem::ConstraintPressure",
        "Fem::FemMeshShapeBaseObjectPython",
        "Fem::FemResultObjectPython",
    }

    def is_fem_object(obj: Any) -> bool:
        type_id = str(getattr(obj, "TypeId", "") or "")
        if type_id in native_types:
            return True
        return bool(
            type_id == "App::DocumentObjectGroup"
            and (
                str(getattr(obj, PROP_PROGRAM_DOMAIN, "") or "") == "fem"
                or "VibeCADConstraints" in set(getattr(obj, "PropertiesList", []) or [])
            )
        )

    def link_names(values: Any) -> tuple[list[str], bool]:
        links = list(values or [])
        names = [
            str(getattr(value, "Name", "") or "")
            for value in links[:MAX_FEM_CONTEXT_LINKS]
        ]
        return names, len(links) > len(names)

    def references(value: Any) -> tuple[list[dict[str, Any]], bool]:
        rows = []
        raw_rows = list(value or [])
        for target, subelements in raw_rows[:MAX_FEM_CONTEXT_LINKS]:
            names = [str(name) for name in list(subelements or [])]
            rows.append(
                {
                    "target": str(getattr(target, "Name", "") or ""),
                    "subelements": names[:MAX_FEM_CONTEXT_LINKS],
                    "subelements_truncated": len(names) > MAX_FEM_CONTEXT_LINKS,
                }
            )
        return rows, len(raw_rows) > len(rows)

    all_objects = [
        obj for obj in list(getattr(doc, "Objects", []) or []) if is_fem_object(obj)
    ]
    captured = []
    for obj in all_objects[:MAX_FEM_CONTEXT_OBJECTS]:
        type_id = str(getattr(obj, "TypeId", "") or "")
        output_type = str(getattr(obj, "VibeCADVibeScriptOutputType", "") or "")
        item: dict[str, Any] = {
            "name": str(getattr(obj, "Name", "") or ""),
            "label": str(getattr(obj, "Label", "") or ""),
            "type_id": type_id,
            "output_type": output_type,
            "program_id": str(getattr(obj, PROP_PROGRAM_ID, "") or ""),
            "program_output": str(getattr(obj, PROP_PROGRAM_OUTPUT, "") or ""),
            "program_revision": str(getattr(obj, PROP_PROGRAM_REVISION, "") or ""),
        }
        try:
            if type_id == "Fem::FemAnalysis":
                names, truncated = link_names(getattr(obj, "Group", []))
                item["native_summary"] = {
                    "members": names,
                    "members_truncated": truncated,
                }
            elif type_id == "Fem::FemSolverObjectPython":
                item["native_summary"] = {
                    "analysis_type": str(getattr(obj, "AnalysisType", "") or ""),
                    "matrix_solver": str(getattr(obj, "MatrixSolverType", "") or ""),
                    "geometrical_nonlinearity": bool(
                        getattr(obj, "GeometricalNonlinearity", False)
                    ),
                    "material_nonlinearity": bool(
                        getattr(obj, "MaterialNonlinearity", False)
                    ),
                }
            elif type_id == "App::MaterialObjectPython":
                material = dict(getattr(obj, "Material", {}) or {})
                item["native_summary"] = {
                    "category": str(getattr(obj, "Category", "") or ""),
                    "material": {
                        key: str(material[key])
                        for key in (
                            "Name",
                            "YoungsModulus",
                            "PoissonRatio",
                            "Density",
                            "ThermalExpansionCoefficient",
                        )
                        if key in material
                    },
                }
            elif type_id.startswith("Fem::Constraint"):
                rows, truncated = references(getattr(obj, "References", []))
                kind = {
                    "Fem::ConstraintFixed": "fixed",
                    "Fem::ConstraintForce": "force",
                    "Fem::ConstraintPressure": "pressure",
                }.get(type_id, "unknown")
                summary: dict[str, Any] = {
                    "kind": kind,
                    "references": rows,
                    "references_truncated": truncated,
                    "suppressed": bool(getattr(obj, "Suppressed", False)),
                }
                if kind == "force":
                    force = getattr(obj, "Force", 0.0)
                    direction = getattr(obj, "DirectionVector", None)
                    summary.update(
                        {
                            "magnitude_n": float(
                                force.getValueAs("N").Value
                                if callable(getattr(force, "getValueAs", None))
                                else force
                            ),
                            "direction": [
                                float(getattr(direction, axis, 0.0))
                                for axis in ("x", "y", "z")
                            ],
                            "reversed": bool(getattr(obj, "Reversed", False)),
                        }
                    )
                elif kind == "pressure":
                    pressure = getattr(obj, "Pressure", 0.0)
                    summary.update(
                        {
                            "magnitude_mpa": float(
                                pressure.getValueAs("MPa").Value
                                if callable(getattr(pressure, "getValueAs", None))
                                else pressure
                            ),
                            "reversed": bool(getattr(obj, "Reversed", False)),
                        }
                    )
                item["native_summary"] = summary
            elif type_id == "App::DocumentObjectGroup":
                constraints, constraints_truncated = link_names(
                    getattr(obj, "VibeCADConstraints", [])
                )
                group, group_truncated = link_names(getattr(obj, "Group", []))
                item["native_summary"] = {
                    "constraints": constraints,
                    "constraints_truncated": constraints_truncated,
                    "group": group,
                    "group_truncated": group_truncated,
                }
            elif type_id == "Fem::FemMeshShapeBaseObjectPython":
                mesh = obj.FemMesh
                box = mesh.BoundBox
                bounds = [
                    float(box.XMin),
                    float(box.YMin),
                    float(box.ZMin),
                    float(box.XMax),
                    float(box.YMax),
                    float(box.ZMax),
                ]
                item["native_summary"] = {
                    "source": str(
                        getattr(getattr(obj, "Shape", None), "Name", "") or ""
                    ),
                    "node_count": int(mesh.NodeCount),
                    "edge_count": int(mesh.EdgeCount),
                    "face_count": int(mesh.FaceCount),
                    "volume_count": int(mesh.VolumeCount),
                    "element_order": str(getattr(obj, "ElementOrder", "") or ""),
                    "element_dimension": str(
                        getattr(obj, "ElementDimension", "") or ""
                    ),
                    "bounds": {
                        "minimum": [
                            value if math.isfinite(value) else None
                            for value in bounds[:3]
                        ],
                        "maximum": [
                            value if math.isfinite(value) else None
                            for value in bounds[3:]
                        ],
                    },
                }
            elif type_id == "Fem::FemResultObjectPython":
                result_fields = {
                    name: len(list(getattr(obj, name, []) or []))
                    for name in (
                        "DisplacementLengths",
                        "DisplacementVectors",
                        "NodeStressXX",
                        "NodeStressYY",
                        "NodeStressZZ",
                        "vonMises",
                        "Temperature",
                    )
                    if hasattr(obj, name) and getattr(obj, name, None)
                }
                item["native_summary"] = {
                    "mesh": str(getattr(getattr(obj, "Mesh", None), "Name", "") or ""),
                    "analysis": str(
                        getattr(obj, "VibeCADAnalysisObjectName", "") or ""
                    ),
                    "status": str(getattr(obj, "VibeCADFEMStatus", "") or ""),
                    "solver_executed": bool(
                        getattr(obj, "VibeCADSolverExecuted", False)
                    ),
                    "node_count": len(list(getattr(obj, "NodeNumbers", []) or [])),
                    "result_field_counts": result_fields,
                    "time": float(getattr(obj, "Time", 0.0) or 0.0),
                }
        except Exception as exc:
            item["native_summary_error"] = f"{type(exc).__name__}: {exc}"
        validation = str(getattr(obj, "VibeCADFEMValidation", "") or "")
        if validation:
            encoded_size = len(validation.encode("utf-8", errors="replace"))
            if encoded_size <= MAX_CONTEXT_VALUE_BYTES:
                item["_validation_json"] = validation
            else:
                item["accepted_validation_omitted"] = True
                item["accepted_validation_json_bytes"] = encoded_size
        captured.append(item)
    return {
        "object_count": len(all_objects),
        "object_limit": MAX_FEM_CONTEXT_OBJECTS,
        "objects_truncated": len(all_objects) > len(captured),
        "objects_omitted": max(0, len(all_objects) - len(captured)),
        "link_limit_per_object": MAX_FEM_CONTEXT_LINKS,
        "objects": captured,
    }


def _cam_document_snapshot(doc: Any) -> dict[str, Any]:
    """Capture bounded native CAM graph state without generating toolpaths."""

    def is_cam_object(obj: Any) -> bool:
        if str(getattr(obj, PROP_PROGRAM_DOMAIN, "") or "") == "cam":
            return True
        type_id = str(getattr(obj, "TypeId", "") or "")
        proxy = getattr(obj, "Proxy", None)
        proxy_module = str(type(proxy).__module__ or "") if proxy is not None else ""
        return type_id.startswith("Path::") or proxy_module.startswith("Path.")

    def link_name(obj: Any, name: str) -> str:
        return str(getattr(getattr(obj, name, None), "Name", "") or "")

    def quantity(obj: Any, name: str) -> float:
        value = getattr(obj, name, 0.0)
        return float(getattr(value, "Value", value) or 0.0)

    def bounded_names(values: Any) -> list[str]:
        return [str(value) for value in list(values or [])[:MAX_CAM_CONTEXT_LINKS]]

    def path_summary(obj: Any) -> dict[str, Any]:
        path = getattr(obj, "Path", None)
        commands = list(getattr(path, "Commands", []) or [])
        sampled = commands[:MAX_CAM_CONTEXT_COMMANDS]
        counts: dict[str, int] = {}
        for command in sampled:
            name = str(getattr(command, "Name", "") or "")
            counts[name] = counts.get(name, 0) + 1
        return {
            "command_count": len(commands),
            "command_type_counts": counts,
            "commands_sampled": len(sampled),
            "commands_truncated": len(commands) > len(sampled),
        }

    all_objects = [
        obj for obj in list(getattr(doc, "Objects", []) or []) if is_cam_object(obj)
    ]
    captured = []
    for obj in all_objects[:MAX_CAM_CONTEXT_OBJECTS]:
        properties = set(getattr(obj, "PropertiesList", []) or [])
        type_id = str(getattr(obj, "TypeId", "") or "")
        output_type = str(getattr(obj, "VibeCADVibeScriptOutputType", "") or "")
        proxy = getattr(obj, "Proxy", None)
        item: dict[str, Any] = {
            "name": str(getattr(obj, "Name", "") or ""),
            "label": str(getattr(obj, "Label", "") or ""),
            "type_id": type_id,
            "output_type": output_type,
            "proxy_kind": str(getattr(obj, "VibeCADCAMProxyKind", "") or ""),
            "proxy_module": str(type(proxy).__module__ or "") if proxy else "",
            "proxy_class": type(proxy).__name__ if proxy else "",
            "program_id": str(getattr(obj, PROP_PROGRAM_ID, "") or ""),
            "program_output": str(getattr(obj, PROP_PROGRAM_OUTPUT, "") or ""),
            "program_revision": str(getattr(obj, PROP_PROGRAM_REVISION, "") or ""),
            "frozen": (
                bool(obj.isFrozen())
                if callable(getattr(obj, "isFrozen", None))
                else False
            ),
        }
        summary: dict[str, Any] = {}
        try:
            if (
                output_type == "job"
                or {
                    "Stock",
                    "Operations",
                    "Model",
                    "Tools",
                }
                <= properties
            ):
                summary = {
                    "stock": link_name(obj, "Stock"),
                    "operations_group": link_name(obj, "Operations"),
                    "setup_sheet": link_name(obj, "SetupSheet"),
                    "model_group": link_name(obj, "Model"),
                    "tools_group": link_name(obj, "Tools"),
                    "postprocessor": str(getattr(obj, "PostProcessor", "") or ""),
                    "geometry_tolerance_mm": quantity(obj, "GeometryTolerance"),
                    "fixtures": [
                        str(value)
                        for value in list(getattr(obj, "Fixtures", []) or [])[
                            :MAX_CAM_CONTEXT_LINKS
                        ]
                    ],
                    "path": path_summary(obj),
                }
            elif (
                output_type == "stock"
                or {
                    "ExtXneg",
                    "ExtXpos",
                    "ExtYneg",
                    "ExtYpos",
                    "ExtZneg",
                    "ExtZpos",
                }
                <= properties
            ):
                summary = {
                    "model_group": link_name(obj, "Base"),
                    "margins_mm": {
                        name: quantity(obj, name)
                        for name in (
                            "ExtXneg",
                            "ExtXpos",
                            "ExtYneg",
                            "ExtYpos",
                            "ExtZneg",
                            "ExtZpos",
                        )
                    },
                }
            elif output_type == "tool" or {"ToolNumber", "Tool"} <= properties:
                summary = {
                    "tool_number": int(getattr(obj, "ToolNumber", 0) or 0),
                    "tool_bit": link_name(obj, "Tool"),
                    "spindle_rpm": float(getattr(obj, "SpindleSpeed", 0.0) or 0.0),
                    "spindle_direction": str(getattr(obj, "SpindleDir", "") or ""),
                    "horizontal_feed_mm_per_min": quantity(obj, "HorizFeed"),
                    "vertical_feed_mm_per_min": quantity(obj, "VertFeed"),
                }
            elif (
                output_type == "operation"
                or {
                    "ToolController",
                    "StartDepth",
                    "FinalDepth",
                }
                <= properties
            ):
                base_rows = list(getattr(obj, "Base", []) or [])
                summary = {
                    "tool_controller": link_name(obj, "ToolController"),
                    "selections": [
                        {
                            "object": str(getattr(target, "Name", "") or ""),
                            "subelements": bounded_names(subelements),
                        }
                        for target, subelements in base_rows[:MAX_CAM_CONTEXT_LINKS]
                    ],
                    "selections_truncated": len(base_rows) > MAX_CAM_CONTEXT_LINKS,
                    "strategy": str(getattr(obj, "Strategy", "") or ""),
                    "start_depth_mm": quantity(obj, "StartDepth"),
                    "final_depth_mm": quantity(obj, "FinalDepth"),
                    "step_down_mm": quantity(obj, "StepDown"),
                    "side": str(getattr(obj, "Side", "") or ""),
                    "boundary": str(getattr(obj, "BoundaryShape", "") or ""),
                    "coolant": str(getattr(obj, "CoolantMode", "") or ""),
                    "path": path_summary(obj),
                }
            elif "Path" in properties:
                summary = {"path": path_summary(obj)}
            elif "ShapeID" in properties:
                summary = {
                    "shape_id": str(getattr(obj, "ShapeID", "") or ""),
                    "shape_type": str(getattr(obj, "ShapeType", "") or ""),
                    "diameter_mm": quantity(obj, "Diameter"),
                    "length_mm": quantity(obj, "Length"),
                    "flutes": int(getattr(obj, "Flutes", 0) or 0),
                }
            elif "Group" in properties:
                members = list(getattr(obj, "Group", []) or [])
                summary = {
                    "members": [
                        str(getattr(value, "Name", "") or "")
                        for value in members[:MAX_CAM_CONTEXT_LINKS]
                    ],
                    "members_truncated": len(members) > MAX_CAM_CONTEXT_LINKS,
                }
        except Exception as exc:
            item["native_summary_error"] = f"{type(exc).__name__}: {exc}"
        if summary:
            item["native_summary"] = summary
        validation = str(getattr(obj, "VibeCADCAMValidation", "") or "")
        if validation:
            encoded_size = len(validation.encode("utf-8", errors="replace"))
            if encoded_size <= MAX_CAM_CONTEXT_VALIDATION_BYTES:
                item["_validation_json"] = validation
            else:
                item["accepted_validation_omitted"] = True
                item["accepted_validation_json_bytes"] = encoded_size
        captured.append(item)
    return {
        "object_count": len(all_objects),
        "object_limit": MAX_CAM_CONTEXT_OBJECTS,
        "objects_truncated": len(all_objects) > len(captured),
        "objects_omitted": max(0, len(all_objects) - len(captured)),
        "link_limit_per_object": MAX_CAM_CONTEXT_LINKS,
        "command_sample_limit_per_object": MAX_CAM_CONTEXT_COMMANDS,
        "objects": captured,
    }


def _techdraw_document_snapshot(doc: Any) -> dict[str, Any]:
    """Capture bounded native drawing properties without evaluating projections."""

    def is_techdraw_object(obj: Any) -> bool:
        return str(getattr(obj, PROP_PROGRAM_DOMAIN, "") or "") == "techdraw" or str(
            getattr(obj, "TypeId", "") or ""
        ).startswith("TechDraw::")

    def link_name(value: Any) -> str:
        return str(getattr(value, "Name", "") or "")

    def bounded_link_names(values: Any) -> tuple[list[str], bool]:
        links = list(values or [])
        return (
            [link_name(value) for value in links[:MAX_TECHDRAW_CONTEXT_LINKS]],
            len(links) > MAX_TECHDRAW_CONTEXT_LINKS,
        )

    def number(obj: Any, name: str) -> float:
        value = getattr(obj, name, 0.0)
        return float(getattr(value, "Value", value) or 0.0)

    def vector(obj: Any, name: str) -> list[float]:
        value = getattr(obj, name, ())
        return [float(component) for component in list(value or ())[:3]]

    def bounded_text(value: Any) -> tuple[str, bool]:
        text = str(value)
        return (
            text[:MAX_TECHDRAW_CONTEXT_TEXT_CHARS],
            len(text) > MAX_TECHDRAW_CONTEXT_TEXT_CHARS,
        )

    all_objects = [
        obj
        for obj in list(getattr(doc, "Objects", []) or [])
        if is_techdraw_object(obj)
    ]
    captured = []
    for obj in all_objects[:MAX_TECHDRAW_CONTEXT_OBJECTS]:
        properties = set(getattr(obj, "PropertiesList", []) or [])
        type_id = str(getattr(obj, "TypeId", "") or "")
        output_type = str(getattr(obj, "VibeCADVibeScriptOutputType", "") or "")
        item: dict[str, Any] = {
            "name": str(getattr(obj, "Name", "") or ""),
            "label": str(getattr(obj, "Label", "") or ""),
            "type_id": type_id,
            "output_type": output_type,
            "program_id": str(getattr(obj, PROP_PROGRAM_ID, "") or ""),
            "program_output": str(getattr(obj, PROP_PROGRAM_OUTPUT, "") or ""),
            "program_revision": str(getattr(obj, PROP_PROGRAM_REVISION, "") or ""),
            "frozen": (
                bool(obj.isFrozen())
                if callable(getattr(obj, "isFrozen", None))
                else False
            ),
        }
        summary: dict[str, Any] = {}
        try:
            if type_id in {"TechDraw::DrawTemplate", "TechDraw::DrawSVGTemplate"}:
                editable = dict(getattr(obj, "EditableTexts", {}) or {})
                editable_items = list(editable.items())
                compact_editable = {}
                editable_text_truncated = False
                for raw_name, raw_text in editable_items[
                    :MAX_TECHDRAW_CONTEXT_TEXT_LINES
                ]:
                    text, truncated = bounded_text(raw_text)
                    compact_editable[str(raw_name)] = text
                    editable_text_truncated = editable_text_truncated or truncated
                summary = {
                    "width_mm": number(obj, "Width"),
                    "height_mm": number(obj, "Height"),
                    "orientation": str(getattr(obj, "Orientation", "") or ""),
                    "editable_texts": compact_editable,
                    "editable_text_count": len(editable_items),
                    "editable_texts_truncated": (
                        len(editable_items) > MAX_TECHDRAW_CONTEXT_TEXT_LINES
                        or editable_text_truncated
                    ),
                }
            elif type_id == "TechDraw::DrawPage":
                views, views_truncated = bounded_link_names(getattr(obj, "Views", []))
                summary = {
                    "template": link_name(getattr(obj, "Template", None)),
                    "views": views,
                    "views_truncated": views_truncated,
                    "projection_type": str(getattr(obj, "ProjectionType", "") or ""),
                    "scale": number(obj, "Scale"),
                    "keep_updated": bool(getattr(obj, "KeepUpdated", False)),
                }
            elif type_id == "TechDraw::DrawProjGroup":
                sources, sources_truncated = bounded_link_names(
                    getattr(obj, "Source", [])
                )
                views, views_truncated = bounded_link_names(getattr(obj, "Views", []))
                summary = {
                    "sources": sources,
                    "sources_truncated": sources_truncated,
                    "views": views,
                    "views_truncated": views_truncated,
                    "projection_type": str(getattr(obj, "ProjectionType", "") or ""),
                    "scale_type": str(getattr(obj, "ScaleType", "") or ""),
                    "scale": number(obj, "Scale"),
                    "position_mm": [number(obj, "X"), number(obj, "Y")],
                    "spacing_mm": [number(obj, "spacingX"), number(obj, "spacingY")],
                    "auto_distribute": bool(getattr(obj, "AutoDistribute", False)),
                }
            elif type_id in {
                "TechDraw::DrawViewPart",
                "TechDraw::DrawProjGroupItem",
            }:
                sources, sources_truncated = bounded_link_names(
                    getattr(obj, "Source", [])
                )
                summary = {
                    "sources": sources,
                    "sources_truncated": sources_truncated,
                    "projection_direction": str(getattr(obj, "Type", "") or ""),
                    "direction": vector(obj, "Direction"),
                    "x_direction": vector(obj, "XDirection"),
                    "scale_type": str(getattr(obj, "ScaleType", "") or ""),
                    "scale": number(obj, "Scale"),
                    "position_mm": [number(obj, "X"), number(obj, "Y")],
                    "line_flags": {
                        name: bool(getattr(obj, name))
                        for name in (
                            "HardHidden",
                            "SmoothHidden",
                            "SeamHidden",
                            "IsoHidden",
                            "SmoothVisible",
                            "SeamVisible",
                            "IsoVisible",
                        )
                        if name in properties
                    },
                }
            elif type_id == "TechDraw::DrawViewDimension":
                references = list(getattr(obj, "References2D", []) or [])
                summary = {
                    "dimension_type": str(getattr(obj, "Type", "") or ""),
                    "measure_type": str(getattr(obj, "MeasureType", "") or ""),
                    "references": [
                        {
                            "view": link_name(value[0]),
                            "subelements": [
                                str(subelement)
                                for subelement in (
                                    value[1]
                                    if isinstance(value[1], (tuple, list))
                                    else (value[1],)
                                )
                            ],
                        }
                        for value in references[:MAX_TECHDRAW_CONTEXT_LINKS]
                        if isinstance(value, (tuple, list)) and len(value) == 2
                    ],
                    "references_truncated": (
                        len(references) > MAX_TECHDRAW_CONTEXT_LINKS
                    ),
                    "position_mm": [number(obj, "X"), number(obj, "Y")],
                    "format_spec": str(getattr(obj, "FormatSpec", "") or ""),
                    "over_tolerance": number(obj, "OverTolerance"),
                    "under_tolerance": number(obj, "UnderTolerance"),
                    "show_units": bool(getattr(obj, "ShowUnits", False)),
                }
            elif type_id == "TechDraw::DrawViewAnnotation":
                raw_text = list(getattr(obj, "Text", []) or [])
                text = []
                text_truncated = len(raw_text) > MAX_TECHDRAW_CONTEXT_TEXT_LINES
                for value in raw_text[:MAX_TECHDRAW_CONTEXT_TEXT_LINES]:
                    clean, truncated = bounded_text(value)
                    text.append(clean)
                    text_truncated = text_truncated or truncated
                summary = {
                    "text": text,
                    "text_line_count": len(raw_text),
                    "text_truncated": text_truncated,
                    "position_mm": [number(obj, "X"), number(obj, "Y")],
                    "text_size_mm": number(obj, "TextSize"),
                    "text_alignment": str(getattr(obj, "TextAlignment", "") or ""),
                }
            else:
                if "Source" in properties:
                    sources, sources_truncated = bounded_link_names(
                        getattr(obj, "Source", [])
                    )
                    summary["sources"] = sources
                    summary["sources_truncated"] = sources_truncated
                if "Views" in properties:
                    views, views_truncated = bounded_link_names(
                        getattr(obj, "Views", [])
                    )
                    summary["views"] = views
                    summary["views_truncated"] = views_truncated
        except Exception as exc:
            item["native_summary_error"] = f"{type(exc).__name__}: {exc}"
        if summary:
            item["native_summary"] = summary
        validation = str(getattr(obj, "VibeCADTechDrawValidation", "") or "")
        if validation:
            encoded_size = len(validation.encode("utf-8", errors="replace"))
            if encoded_size <= MAX_TECHDRAW_CONTEXT_VALIDATION_BYTES:
                item["_validation_json"] = validation
            else:
                item["accepted_validation_omitted"] = True
                item["accepted_validation_json_bytes"] = encoded_size
        captured.append(item)
    return {
        "object_count": len(all_objects),
        "object_limit": MAX_TECHDRAW_CONTEXT_OBJECTS,
        "objects_truncated": len(all_objects) > len(captured),
        "objects_omitted": max(0, len(all_objects) - len(captured)),
        "link_limit_per_object": MAX_TECHDRAW_CONTEXT_LINKS,
        "text_line_limit_per_object": MAX_TECHDRAW_CONTEXT_TEXT_LINES,
        "text_character_limit": MAX_TECHDRAW_CONTEXT_TEXT_CHARS,
        "objects": captured,
    }


def domain_context_snapshot(service: Any, domain: str) -> dict[str, Any]:
    """Capture the document-affine half of one domain provider context."""

    clean_domain = str(domain or "").strip().lower()
    pack = next(
        (
            candidate
            for candidate in VIBESCRIPT_WORKBENCH_PACKS.values()
            if candidate.domain == clean_domain
        ),
        None,
    )
    if pack is None:
        raise RuntimeError(f"Unknown shared VibeScript domain: {clean_domain!r}.")
    from VibeCADModelingSurface import resolve_service_surface

    resolution = resolve_service_surface(service, service.active_workbench_name())
    if (
        resolution.engine != "vibescript"
        or resolution.domain != clean_domain
        or not resolution.available
    ):
        raise RuntimeError(
            f"The live modeling surface does not authorize domain {clean_domain!r}."
        )
    scope = service.project_scope_snapshot()
    doc = service._active_document()
    native_programs = (
        capture_domain_programs(doc, clean_domain) if doc is not None else []
    )
    return {
        "_vibecad_deferred_vibescript_domain_context": True,
        "domain": clean_domain,
        "workbench": pack.workbench,
        "surface_id": resolution.surface_id,
        "project_root": str(scope.get("root") or ""),
        "document_name": str(getattr(doc, "Name", "") or "") if doc is not None else "",
        "document_uid": str(getattr(doc, "Uid", "") or "") if doc is not None else "",
        "native_program_count": len(native_programs),
        "native_programs": native_programs[:MAX_DOMAIN_CONTEXT_PROGRAMS],
        "part_document_shapes": (
            _part_document_shape_snapshot(service, doc)
            if clean_domain in {"part", "partdesign"} and doc is not None
            else None
        ),
        "assembly_component_shapes": (
            _part_document_shape_snapshot(
                service,
                doc,
                assembly_components=True,
            )
            if clean_domain == "assembly" and doc is not None
            else None
        ),
        "sketcher_document": (
            _sketcher_document_snapshot(doc)
            if clean_domain == "sketcher" and doc is not None
            else None
        ),
        "sketch_support_shapes": (
            _part_document_shape_snapshot(
                service,
                doc,
                assembly_components=True,
            )
            if clean_domain == "sketcher" and doc is not None
            else None
        ),
        "draft_document": (
            _draft_document_snapshot(doc)
            if clean_domain == "draft" and doc is not None
            else None
        ),
        "draft_array_source_shapes": (
            _part_document_shape_snapshot(service, doc)
            if clean_domain == "draft" and doc is not None
            else None
        ),
        "surface_input_shapes": (
            _part_document_shape_snapshot(
                service,
                doc,
                assembly_components=True,
            )
            if clean_domain == "surface" and doc is not None
            else None
        ),
        "spreadsheet_document": (
            _spreadsheet_document_snapshot(doc)
            if clean_domain == "spreadsheet" and doc is not None
            else None
        ),
        "material_document": (
            _material_document_snapshot(doc)
            if clean_domain == "material" and doc is not None
            else None
        ),
        "mesh_document": (
            _mesh_document_snapshot(doc)
            if clean_domain == "mesh" and doc is not None
            else None
        ),
        "mesh_shape_sources": (
            _part_document_shape_snapshot(service, doc)
            if clean_domain == "mesh" and doc is not None
            else None
        ),
        "meshpart_shape_sources": (
            _part_document_shape_snapshot(service, doc)
            if clean_domain == "meshpart" and doc is not None
            else None
        ),
        "meshpart_mesh_sources": (
            _mesh_document_snapshot(doc)
            if clean_domain == "meshpart" and doc is not None
            else None
        ),
        "points_document": (
            _points_document_snapshot(doc)
            if clean_domain == "points" and doc is not None
            else None
        ),
        "reverse_point_sources": (
            _points_document_snapshot(doc)
            if clean_domain == "reverse_engineering" and doc is not None
            else None
        ),
        "reverse_mesh_sources": (
            _mesh_document_snapshot(doc)
            if clean_domain == "reverse_engineering" and doc is not None
            else None
        ),
        "inspection_document": (
            _inspection_document_snapshot(doc)
            if clean_domain == "inspection" and doc is not None
            else None
        ),
        "inspection_shape_sources": (
            _part_document_shape_snapshot(service, doc)
            if clean_domain == "inspection" and doc is not None
            else None
        ),
        "inspection_mesh_sources": (
            _mesh_document_snapshot(doc)
            if clean_domain == "inspection" and doc is not None
            else None
        ),
        "inspection_point_sources": (
            _points_document_snapshot(doc)
            if clean_domain == "inspection" and doc is not None
            else None
        ),
        "robot_document": (
            _robot_document_snapshot(doc)
            if clean_domain == "robot" and doc is not None
            else None
        ),
        "fem_document": (
            _fem_document_snapshot(doc)
            if clean_domain == "fem" and doc is not None
            else None
        ),
        "fem_shape_sources": (
            _part_document_shape_snapshot(service, doc)
            if clean_domain == "fem" and doc is not None
            else None
        ),
        "cam_document": (
            _cam_document_snapshot(doc)
            if clean_domain == "cam" and doc is not None
            else None
        ),
        "cam_shape_sources": (
            _part_document_shape_snapshot(
                service,
                doc,
                assembly_components=True,
            )
            if clean_domain == "cam" and doc is not None
            else None
        ),
        "techdraw_document": (
            _techdraw_document_snapshot(doc)
            if clean_domain == "techdraw" and doc is not None
            else None
        ),
        "techdraw_shape_sources": (
            _part_document_shape_snapshot(
                service,
                doc,
                assembly_components=True,
            )
            if clean_domain == "techdraw" and doc is not None
            else None
        ),
        "contract": {
            "program_schema": PROGRAM_SCHEMA,
            "output_types": list(pack.output_types),
            "api_exports": list(pack.api_exports),
            "instructions": pack.instructions,
        },
    }


def domain_program_index_snapshot(service: Any, domain: str) -> dict[str, Any]:
    """Capture only the identities needed by the human program editor.

    Unlike :func:`domain_context_snapshot`, this function deliberately does not
    inspect domain objects, detach Shapes, enumerate mesh/point payloads, or
    construct model-facing context.  The editor uses it to populate one program
    selector and nothing else.
    """

    clean_domain = str(domain or "").strip().lower()
    pack = next(
        (
            candidate
            for candidate in VIBESCRIPT_WORKBENCH_PACKS.values()
            if candidate.domain == clean_domain
        ),
        None,
    )
    if pack is None:
        raise RuntimeError(f"Unknown shared VibeScript domain: {clean_domain!r}.")
    from VibeCADModelingSurface import resolve_service_surface

    resolution = resolve_service_surface(service, service.active_workbench_name())
    if (
        resolution.engine != "vibescript"
        or resolution.domain != clean_domain
        or not resolution.available
    ):
        raise RuntimeError(
            f"The live modeling surface does not authorize domain {clean_domain!r}."
        )
    scope = service.project_scope_snapshot()
    doc = service._active_document()
    native_programs = (
        capture_domain_programs(doc, clean_domain) if doc is not None else []
    )
    return {
        "_vibecad_deferred_vibescript_program_index": True,
        "domain": clean_domain,
        "workbench": pack.workbench,
        "surface_id": resolution.surface_id,
        "project_root": str(scope.get("root") or ""),
        "document_name": str(getattr(doc, "Name", "") or "") if doc is not None else "",
        "document_uid": str(getattr(doc, "Uid", "") or "") if doc is not None else "",
        "native_program_count": len(native_programs),
        "native_programs": native_programs[:MAX_DOMAIN_CONTEXT_PROGRAMS],
    }


def complete_domain_program_index(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Read the editor's bounded program manifests away from the GUI thread."""

    if snapshot.get("_vibecad_deferred_vibescript_program_index") is not True:
        raise RuntimeError("Invalid deferred VibeScript program index snapshot.")
    # Program persistence and v1 migration are shared with provider context, but
    # the editor snapshot contains none of the expensive domain-state fields.
    completed = complete_domain_context(
        {
            "_vibecad_deferred_vibescript_domain_context": True,
            **{
                key: snapshot.get(key)
                for key in (
                    "domain",
                    "workbench",
                    "surface_id",
                    "project_root",
                    "document_name",
                    "document_uid",
                    "native_program_count",
                    "native_programs",
                )
            },
            "contract": {},
        }
    )
    return {
        "ok": True,
        "domain": str(completed.get("domain") or ""),
        "workbench": str(completed.get("workbench") or ""),
        "surface_id": str(completed.get("surface_id") or ""),
        "document": dict(completed.get("document") or {}),
        "program_count": int(completed.get("program_count") or 0),
        "program_limit": int(completed.get("program_limit") or 0),
        "programs_truncated": bool(completed.get("programs_truncated")),
        "programs_omitted": int(completed.get("programs_omitted") or 0),
        "programs": list(completed.get("programs") or []),
    }


def _program_artifact_root(project_root: str | Path, domain: str) -> Path:
    return Path(project_root) / "vibescript" / str(domain)


def _bounded_context_value(value: Any) -> Any:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) <= MAX_CONTEXT_VALUE_BYTES:
        return value
    return {
        "_vibecad_context_omitted": True,
        "json_bytes": len(encoded),
        "reason": "Use vibescript.read_source for the complete persisted value.",
    }


def _compact_context_facts(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    result = {
        key: item
        for key, item in value.items()
        if key not in {"face_details", "edge_details"}
    }
    face_details = value.get("face_details")
    edge_details = value.get("edge_details")
    if isinstance(face_details, list) or isinstance(edge_details, list):
        result["subelement_details_context_omitted"] = True
        result["subelement_details_guidance"] = (
            "Use vibescript.read_source for the owning source and accepted output details."
        )
    return result


def _compact_context_outputs(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    result: dict[str, Any] = {}
    for name, raw in value.items():
        if not isinstance(raw, Mapping):
            result[str(name)] = raw
            continue
        item = dict(raw)
        if "facts" in item:
            item["facts"] = _compact_context_facts(item["facts"])
        result[str(name)] = item
    return result


def _compact_techdraw_validation(
    value: Any,
    *,
    depth: int = 0,
) -> Any:
    """Remove publication-only projection payloads from provider context."""

    if depth > 12:
        return {"_vibecad_context_omitted": True, "reason": "depth limit"}
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return value[:MAX_TECHDRAW_CONTEXT_TEXT_CHARS]
    if isinstance(value, list):
        items = [
            _compact_techdraw_validation(item, depth=depth + 1)
            for item in value[:MAX_TECHDRAW_CONTEXT_LINKS]
        ]
        if len(value) > MAX_TECHDRAW_CONTEXT_LINKS:
            items.append(
                {"_vibecad_items_omitted": (len(value) - MAX_TECHDRAW_CONTEXT_LINKS)}
            )
        return items
    if isinstance(value, Mapping):
        omitted_payloads = {
            "descriptors",
            "edge_classes",
            "edge_visibility",
            "source_indices",
            "vectors",
            "scalars",
            "flags",
        }
        result = {}
        for raw_key, item in list(value.items())[:MAX_TECHDRAW_CONTEXT_LINKS]:
            key = str(raw_key)
            if key == "artifact_path" or key in omitted_payloads:
                continue
            result[key] = _compact_techdraw_validation(item, depth=depth + 1)
        if len(value) > MAX_TECHDRAW_CONTEXT_LINKS:
            result["_vibecad_fields_omitted"] = len(value) - MAX_TECHDRAW_CONTEXT_LINKS
        return result
    return {"_vibecad_context_omitted": True, "reason": "unsupported value"}


def _compact_context_references(value: Any) -> tuple[list[Any], int]:
    references = list(value or []) if isinstance(value, list) else []
    result = []
    for raw in references[:MAX_CONTEXT_REFERENCES_PER_PROGRAM]:
        if not isinstance(raw, Mapping):
            result.append(raw)
            continue
        item = dict(raw)
        if "facts" in item:
            item["facts"] = _compact_context_facts(item["facts"])
        result.append(item)
    return result, max(0, len(references) - len(result))


def complete_domain_context(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Read persisted v2 programs away from the FreeCAD document thread."""

    if snapshot.get("_vibecad_deferred_vibescript_domain_context") is not True:
        raise RuntimeError("Invalid deferred VibeScript domain context snapshot.")
    domain = str(snapshot.get("domain") or "")
    project_root = str(snapshot.get("project_root") or "").strip()
    root = _program_artifact_root(project_root, domain)
    raw_native_programs = [
        item
        for item in list(snapshot.get("native_programs") or [])
        if isinstance(item, dict) and str(item.get("program_id") or "")
    ]
    artifact_directories = (
        [
            directory
            for directory in root.iterdir()
            if directory.is_dir() and not directory.name.startswith(".")
        ]
        if project_root and root.is_dir()
        else []
    )
    if project_root and domain == "partdesign":
        v1_root = Path(project_root) / "vibescript"
        if v1_root.is_dir():
            artifact_directories.extend(
                directory
                for directory in v1_root.iterdir()
                if directory.is_dir()
                and re.fullmatch(r"[0-9a-f]{32}", directory.name)
                and directory not in artifact_directories
            )
    artifact_by_id = {directory.name: directory for directory in artifact_directories}
    native_ids = [str(item["program_id"]) for item in raw_native_programs]

    def artifact_mtime(program_id: str) -> float:
        try:
            return artifact_by_id[program_id].stat().st_mtime
        except (KeyError, OSError):
            return -1.0

    ordered_ids = list(dict.fromkeys(native_ids))
    ordered_ids.extend(
        program_id
        for program_id in sorted(
            (set(artifact_by_id) - set(ordered_ids)),
            key=artifact_mtime,
            reverse=True,
        )
    )
    selected_ids = ordered_ids[:MAX_DOMAIN_CONTEXT_PROGRAMS]
    selected_id_set = set(selected_ids)
    native_by_id: dict[str, dict[str, Any]] = {}
    for raw in raw_native_programs:
        program_id = str(raw.get("program_id") or "")
        if program_id not in selected_id_set:
            continue
        item = dict(raw)
        item["live_outputs"] = {
            str(output.get("name") or ""): {
                key: output.get(key)
                for key in (
                    "object_name",
                    "label",
                    "type_id",
                    "visible",
                    "derived_state",
                    "stale_reason",
                    "source_revision",
                )
                if output.get(key) not in (None, "")
            }
            for output in list(raw.get("live_outputs") or [])
            if isinstance(output, dict)
            and str(output.get("name") or "")
            and "." not in str(output.get("name") or "")
        }
        if item.get("portable_document_contract"):
            item["accepted_revision"] = str(item.get("working_revision") or "")
            item["state"] = "accepted_document"
        else:
            item["state"] = "live_outputs_only"
        native_by_id[program_id] = item
    programs: dict[str, dict[str, Any]] = dict(native_by_id)
    if artifact_by_id:
        for program_id in selected_ids:
            directory = artifact_by_id.get(program_id)
            if directory is None:
                continue
            manifest_path = directory / "program.json"
            if not manifest_path.is_file() and domain == "partdesign":
                manifest_path = directory / "manifest.json"
            if not manifest_path.is_file():
                continue
            try:
                raw = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest = migrate_program_manifest(raw, artifact_directory=directory)
            except (OSError, ValueError, TypeError) as exc:
                programs[directory.name] = {
                    "program_id": directory.name,
                    "domain": domain,
                    "state": "invalid_artifact",
                    "error": str(exc),
                    "artifact_directory": str(directory),
                }
                continue
            program_id = str(manifest.get("program_id") or directory.name)
            live = native_by_id.get(program_id, {})
            compact = {
                key: manifest.get(key)
                for key in (
                    "program_id",
                    "domain",
                    "workbench",
                    "label",
                    "expected_outputs",
                    "working_revision",
                    "accepted_revision",
                    "latest_candidate",
                    "imported_from_schema",
                    "migration_required",
                    "migration_reason",
                    "migration_action",
                )
                if manifest.get(key) not in (None, "", [], {})
            }
            for key in ("input_schema", "inputs"):
                if manifest.get(key) not in (None, "", [], {}):
                    compact[key] = _bounded_context_value(manifest[key])
            references, references_omitted = _compact_context_references(
                manifest.get("resolved_references")
            )
            if references:
                compact["resolved_references"] = references
            if references_omitted:
                compact["resolved_references_omitted"] = references_omitted
            persisted_outputs = dict(manifest.get("live_outputs") or {})
            live_outputs = dict(live.get("live_outputs") or {})
            if persisted_outputs or live_outputs:
                compact["live_outputs"] = {
                    name: {
                        **dict(
                            _compact_context_outputs(
                                {name: persisted_outputs.get(name) or {}}
                            ).get(name)
                            or {}
                        ),
                        **dict(live_outputs.get(name) or {}),
                    }
                    for name in sorted(set(persisted_outputs) | set(live_outputs))
                }
            if live.get("editor_draft"):
                compact["editor_draft"] = True
            if live.get("portable_document_contract"):
                compact["portable_document_contract"] = True
            compact["artifact_directory"] = str(directory)
            compact["state"] = (
                "reconfiguration_required"
                if compact.get("migration_required")
                else "accepted"
                if compact.get("accepted_revision")
                else "working_candidate"
            )
            programs[program_id] = compact
    result = {
        "domain": domain,
        "workbench": str(snapshot.get("workbench") or ""),
        "surface_id": str(snapshot.get("surface_id") or ""),
        "document": {
            "name": str(snapshot.get("document_name") or ""),
            "uid": str(snapshot.get("document_uid") or ""),
        },
        "contract": dict(snapshot.get("contract") or {}),
        "programs": [programs[key] for key in sorted(programs)],
        "program_limit": MAX_DOMAIN_CONTEXT_PROGRAMS,
    }
    available_program_count = max(
        int(snapshot.get("native_program_count") or 0),
        len(set(native_ids) | set(artifact_by_id)),
    )
    result["program_count"] = available_program_count
    result["programs_truncated"] = available_program_count > len(result["programs"])
    result["programs_omitted"] = max(
        0,
        available_program_count - len(result["programs"]),
    )
    raw_shapes = snapshot.get("part_document_shapes")
    if domain in {"part", "partdesign"} and isinstance(raw_shapes, Mapping):
        from vibescript_part_worker import part_shape_facts

        completed_shapes = []
        for raw in list(raw_shapes.get("objects") or []):
            if not isinstance(raw, dict):
                continue
            item = {
                key: value for key, value in raw.items() if key != "_detached_shape"
            }
            shape = raw.get("_detached_shape")
            if shape is not None:
                try:
                    item["facts"] = part_shape_facts(
                        shape,
                        max_subelements=MAX_PART_CONTEXT_SUBELEMENTS,
                    )
                except Exception as exc:
                    item["error"] = f"Could not inspect detached shape: {exc}"
            completed_shapes.append(item)
        result["document_shapes"] = {
            key: raw_shapes.get(key)
            for key in (
                "object_count",
                "object_limit",
                "objects_truncated",
                "objects_omitted",
            )
        }
        result["document_shapes"]["objects"] = completed_shapes
    raw_shape_sources = (
        snapshot.get("inspection_shape_sources")
        if domain == "inspection"
        else (
            snapshot.get("mesh_shape_sources")
            if domain == "mesh"
            else snapshot.get("meshpart_shape_sources")
        )
    )
    if domain in {"mesh", "meshpart", "inspection"} and isinstance(
        raw_shape_sources, Mapping
    ):
        from vibescript_part_worker import part_shape_facts

        completed_shapes = []
        for raw in list(raw_shape_sources.get("objects") or []):
            if not isinstance(raw, dict):
                continue
            item = {
                key: value for key, value in raw.items() if key != "_detached_shape"
            }
            eligibility_key = (
                "eligible_for_inspection"
                if domain == "inspection"
                else "eligible_for_mesh_from_shape"
            )
            item[eligibility_key] = False
            shape = raw.get("_detached_shape")
            if shape is not None:
                try:
                    item["facts"] = part_shape_facts(
                        shape,
                        max_subelements=MAX_PART_CONTEXT_SUBELEMENTS,
                    )
                    eligible = bool(
                        item["facts"].get("valid")
                        and not item["facts"].get("null")
                        and (
                            item["facts"].get("vertices")
                            if domain == "inspection"
                            else item["facts"].get("faces")
                        )
                    )
                    item[eligibility_key] = eligible
                except Exception as exc:
                    item["error"] = f"Could not inspect detached shape: {exc}"
            if not item[eligibility_key]:
                item["ineligible_reason"] = (
                    "Inspection requires a valid non-null BREP with vertices."
                    if domain == "inspection"
                    else "api.mesh_from_shape requires a valid non-null BREP with faces."
                )
            completed_shapes.append(item)
        result["document_shape_sources"] = {
            key: raw_shape_sources.get(key)
            for key in (
                "object_count",
                "object_limit",
                "objects_truncated",
                "objects_omitted",
            )
        }
        result["document_shape_sources"]["objects"] = completed_shapes
    raw_components = snapshot.get("assembly_component_shapes")
    if domain == "assembly" and isinstance(raw_components, Mapping):
        from vibescript_part_worker import part_shape_facts

        candidates = []
        for raw in list(raw_components.get("objects") or []):
            if not isinstance(raw, dict):
                continue
            item = {
                key: value for key, value in raw.items() if key != "_detached_shape"
            }
            shape = raw.get("_detached_shape")
            if shape is not None:
                try:
                    facts = part_shape_facts(
                        shape,
                        max_subelements=MAX_PART_CONTEXT_SUBELEMENTS,
                    )
                    item["facts"] = facts
                    item["eligible_component_shape"] = bool(
                        facts.get("valid")
                        and not facts.get("null")
                        and int(facts.get("solids") or 0) > 0
                    )
                    if not item["eligible_component_shape"]:
                        item["ineligible_reason"] = (
                            "Assembly components require a valid shape with at least one solid."
                        )
                except Exception as exc:
                    item["eligible_component_shape"] = False
                    item["error"] = f"Could not inspect detached component shape: {exc}"
            candidates.append(item)
        result["component_candidates"] = {
            key: raw_components.get(key)
            for key in (
                "object_count",
                "object_limit",
                "objects_truncated",
                "objects_omitted",
            )
        }
        result["component_candidates"]["objects"] = candidates
    raw_sketches = snapshot.get("sketcher_document")
    if domain == "sketcher" and isinstance(raw_sketches, Mapping):
        sketches = []
        for raw in list(raw_sketches.get("sketches") or []):
            if not isinstance(raw, dict):
                continue
            item = {
                key: value for key, value in raw.items() if key != "_detached_shape"
            }
            shape = raw.get("_detached_shape")
            if shape is not None:
                try:
                    wires = list(getattr(shape, "Wires", []) or [])
                    edges = list(getattr(shape, "Edges", []) or [])
                    closed_wires = sum(bool(wire.isClosed()) for wire in wires)
                    item["profile"] = {
                        "edge_count": len(edges),
                        "wire_count": len(wires),
                        "closed_wire_count": closed_wires,
                        "open_wire_count": len(wires) - closed_wires,
                        "profile_ready": bool(wires and closed_wires == len(wires)),
                    }
                except Exception as exc:
                    item["profile_error"] = f"Could not inspect detached sketch: {exc}"
            sketches.append(item)
        result["document_sketches"] = {
            key: raw_sketches.get(key)
            for key in (
                "sketch_count",
                "sketch_limit",
                "sketches_truncated",
                "sketches_omitted",
            )
        }
        result["document_sketches"]["sketches"] = sketches
    raw_supports = snapshot.get("sketch_support_shapes")
    if domain == "sketcher" and isinstance(raw_supports, Mapping):
        from vibescript_part_worker import part_shape_facts

        supports = []
        for raw in list(raw_supports.get("objects") or []):
            if not isinstance(raw, dict):
                continue
            item = {
                key: value for key, value in raw.items() if key != "_detached_shape"
            }
            shape = raw.get("_detached_shape")
            if shape is not None:
                try:
                    facts = part_shape_facts(
                        shape,
                        max_subelements=MAX_PART_CONTEXT_SUBELEMENTS,
                    )
                    item["facts"] = facts
                    item["eligible_support_shape"] = bool(
                        facts.get("valid")
                        and not facts.get("null")
                        and (
                            int(facts.get("faces") or 0)
                            or int(facts.get("edges") or 0)
                            or int(facts.get("vertices") or 0)
                        )
                    )
                    if not item["eligible_support_shape"]:
                        item["ineligible_reason"] = (
                            "Sketch support requires valid Face, Edge, or Vertex topology."
                        )
                except Exception as exc:
                    item["eligible_support_shape"] = False
                    item["error"] = f"Could not inspect detached support shape: {exc}"
            item["selection_contract"] = (
                "published_interface"
                if bool(item.get("transient_topology"))
                or bool(item.get("requires_semantic_interfaces"))
                else "subelements"
            )
            supports.append(item)
        result["support_candidates"] = {
            key: raw_supports.get(key)
            for key in (
                "object_count",
                "object_limit",
                "objects_truncated",
                "objects_omitted",
            )
        }
        result["support_candidates"]["objects"] = supports
    raw_draft = snapshot.get("draft_document")
    if domain == "draft" and isinstance(raw_draft, Mapping):
        result["document_draft_objects"] = {
            key: raw_draft.get(key)
            for key in (
                "object_count",
                "object_limit",
                "objects_truncated",
                "objects_omitted",
                "objects",
            )
        }
    raw_draft_sources = snapshot.get("draft_array_source_shapes")
    if domain == "draft" and isinstance(raw_draft_sources, Mapping):
        from vibescript_part_worker import part_shape_facts

        sources = []
        for raw in list(raw_draft_sources.get("objects") or []):
            if not isinstance(raw, dict):
                continue
            item = {
                key: value for key, value in raw.items() if key != "_detached_shape"
            }
            shape = raw.get("_detached_shape")
            if shape is not None:
                try:
                    facts = part_shape_facts(
                        shape,
                        max_subelements=0,
                    )
                    item["facts"] = facts
                    item["eligible_array_source"] = bool(
                        facts.get("valid")
                        and not facts.get("null")
                        and (
                            int(facts.get("solids") or 0)
                            or int(facts.get("shells") or 0)
                            or int(facts.get("faces") or 0)
                            or int(facts.get("wires") or 0)
                            or int(facts.get("edges") or 0)
                            or int(facts.get("vertices") or 0)
                        )
                    )
                    if not item["eligible_array_source"]:
                        item["ineligible_reason"] = (
                            "Draft arrays require one valid non-empty Shape."
                        )
                except Exception as exc:
                    item["eligible_array_source"] = False
                    item["error"] = f"Could not inspect detached array source: {exc}"
            sources.append(item)
        result["array_source_candidates"] = {
            key: raw_draft_sources.get(key)
            for key in (
                "object_count",
                "object_limit",
                "objects_truncated",
                "objects_omitted",
            )
        }
        result["array_source_candidates"]["objects"] = sources
    raw_surface = snapshot.get("surface_input_shapes")
    if domain == "surface" and isinstance(raw_surface, Mapping):
        from vibescript_part_worker import part_shape_facts

        candidates = []
        for raw in list(raw_surface.get("objects") or []):
            if not isinstance(raw, dict):
                continue
            item = {
                key: value for key, value in raw.items() if key != "_detached_shape"
            }
            shape = raw.get("_detached_shape")
            if shape is not None:
                try:
                    facts = part_shape_facts(
                        shape,
                        max_subelements=MAX_PART_CONTEXT_SUBELEMENTS,
                    )
                    item["facts"] = facts
                    item["eligible_surface_input"] = bool(
                        facts.get("valid")
                        and not facts.get("null")
                        and any(
                            int(facts.get(name) or 0)
                            for name in (
                                "solids",
                                "shells",
                                "faces",
                                "wires",
                                "edges",
                                "vertices",
                            )
                        )
                    )
                    if not item["eligible_surface_input"]:
                        item["ineligible_reason"] = (
                            "Surface inputs require one valid non-empty BREP Shape."
                        )
                except Exception as exc:
                    item["eligible_surface_input"] = False
                    item["error"] = f"Could not inspect detached Surface input: {exc}"
            item["selection_contract"] = (
                "published_interface"
                if bool(item.get("transient_topology"))
                or bool(item.get("requires_semantic_interfaces"))
                else "whole_shape_or_exact_subelement"
            )
            candidates.append(item)
        result["surface_input_candidates"] = {
            key: raw_surface.get(key)
            for key in (
                "object_count",
                "object_limit",
                "objects_truncated",
                "objects_omitted",
            )
        }
        result["surface_input_candidates"]["objects"] = candidates
    raw_spreadsheet = snapshot.get("spreadsheet_document")
    if domain == "spreadsheet" and isinstance(raw_spreadsheet, Mapping):
        result["document_sheets"] = {
            key: raw_spreadsheet.get(key)
            for key in (
                "sheet_count",
                "sheet_limit",
                "sheets_truncated",
                "sheets_omitted",
                "cell_sample_limit_per_sheet",
                "sheets",
            )
        }
    raw_material = snapshot.get("material_document")
    if domain == "material" and isinstance(raw_material, Mapping):
        result["material_targets"] = {
            key: raw_material.get(key)
            for key in (
                "target_count",
                "target_limit",
                "targets_truncated",
                "targets_omitted",
                "targets",
            )
        }
    if domain in {"material", "partdesign"}:
        try:
            from vibescript_material_worker import material_catalog_index

            result["material_catalog"] = {
                "available": True,
                **material_catalog_index(),
            }
        except Exception as exc:
            result["material_catalog"] = {
                "available": False,
                "error": f"{type(exc).__name__}: {exc}",
                "correction": "Repair or install the native FreeCAD Material catalog.",
            }
    raw_inspection = snapshot.get("inspection_document")
    if domain == "inspection" and isinstance(raw_inspection, Mapping):
        result["document_inspections"] = {
            key: raw_inspection.get(key)
            for key in (
                "object_count",
                "object_limit",
                "objects_truncated",
                "objects_omitted",
                "objects",
            )
        }
    raw_robot = snapshot.get("robot_document")
    if domain == "robot" and isinstance(raw_robot, Mapping):
        robots = []
        for raw in list(raw_robot.get("objects") or []):
            if not isinstance(raw, Mapping):
                continue
            item = {
                key: value for key, value in raw.items() if key != "_validation_json"
            }
            validation_json = raw.get("_validation_json")
            if isinstance(validation_json, str):
                try:
                    validation = json.loads(validation_json)
                    if not isinstance(validation, Mapping):
                        raise ValueError("validation is not an object")
                    item["accepted_validation"] = {
                        key: validation.get(key)
                        for key in (
                            "schema",
                            "operation",
                            "native_type",
                            "waypoint_count",
                            "length",
                            "duration",
                            "sample_count",
                            "reachable_count",
                            "unreachable_count",
                            "artifact_sha256",
                        )
                        if validation.get(key) not in (None, "")
                    }
                except (TypeError, ValueError) as exc:
                    item["accepted_validation_error"] = str(exc)
            robots.append(item)
        result["document_robots"] = {
            key: raw_robot.get(key)
            for key in (
                "object_count",
                "object_limit",
                "objects_truncated",
                "objects_omitted",
            )
        }
        result["document_robots"]["objects"] = robots
    raw_fem = snapshot.get("fem_document")
    if domain == "fem" and isinstance(raw_fem, Mapping):
        fem_objects = []
        for raw in list(raw_fem.get("objects") or []):
            if not isinstance(raw, Mapping):
                continue
            item = {
                key: value for key, value in raw.items() if key != "_validation_json"
            }
            validation_json = raw.get("_validation_json")
            if isinstance(validation_json, str):
                try:
                    validation = json.loads(validation_json)
                    if not isinstance(validation, Mapping):
                        raise ValueError("validation is not an object")
                    accepted = {
                        key: validation.get(key)
                        for key in (
                            "native_type",
                            "kind",
                            "method",
                            "analysis_type",
                            "matrix_solver",
                            "status",
                            "execution",
                            "solver_executed",
                            "facts",
                            "result_summary",
                        )
                        if validation.get(key) not in (None, "")
                    }
                    deck = validation.get("input_deck")
                    if isinstance(deck, Mapping):
                        accepted["input_deck"] = {
                            key: deck.get(key)
                            for key in (
                                "artifact_kind",
                                "artifact_sha256",
                                "artifact_bytes",
                            )
                            if deck.get(key) not in (None, "")
                        }
                    mapping = validation.get("mesh_constraint_mapping")
                    if isinstance(mapping, Mapping):
                        accepted["mesh_constraint_mapping"] = {
                            "constraint_count": int(
                                mapping.get("constraint_count") or 0
                            )
                        }
                    item["accepted_validation"] = accepted
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    item["accepted_validation_error"] = f"{type(exc).__name__}: {exc}"
            fem_objects.append(item)
        result["document_fem"] = {
            key: raw_fem.get(key)
            for key in (
                "object_count",
                "object_limit",
                "objects_truncated",
                "objects_omitted",
                "link_limit_per_object",
            )
        }
        result["document_fem"]["objects"] = fem_objects
    raw_fem_shapes = snapshot.get("fem_shape_sources")
    if domain == "fem" and isinstance(raw_fem_shapes, Mapping):
        from vibescript_part_worker import part_shape_facts

        sources = []
        for raw in list(raw_fem_shapes.get("objects") or []):
            if not isinstance(raw, dict):
                continue
            item = {
                key: value for key, value in raw.items() if key != "_detached_shape"
            }
            shape = raw.get("_detached_shape")
            if shape is not None:
                try:
                    item["facts"] = part_shape_facts(
                        shape,
                        max_subelements=MAX_PART_CONTEXT_SUBELEMENTS,
                    )
                    item["eligible_for_fem_reference"] = bool(
                        item["facts"].get("valid")
                        and not item["facts"].get("null")
                        and item["facts"].get("faces")
                    )
                except Exception as exc:
                    item["error"] = f"Could not inspect detached shape: {exc}"
                    item["eligible_for_fem_reference"] = False
            sources.append(item)
        result["fem_reference_candidates"] = {
            key: raw_fem_shapes.get(key)
            for key in (
                "object_count",
                "object_limit",
                "objects_truncated",
                "objects_omitted",
            )
        }
        result["fem_reference_candidates"]["objects"] = sources
    raw_cam = snapshot.get("cam_document")
    if domain == "cam" and isinstance(raw_cam, Mapping):
        cam_objects = []
        for raw in list(raw_cam.get("objects") or []):
            if not isinstance(raw, Mapping):
                continue
            item = {
                key: value for key, value in raw.items() if key != "_validation_json"
            }
            validation_json = raw.get("_validation_json")
            if isinstance(validation_json, str):
                try:
                    validation = json.loads(validation_json)
                    if not isinstance(validation, Mapping):
                        raise ValueError("validation is not an object")
                    accepted = {
                        key: validation.get(key)
                        for key in (
                            "native_type",
                            "proxy_module",
                            "proxy_class",
                            "kind",
                            "strategy",
                            "job_output",
                            "stock_output",
                            "tool_output",
                            "tool_outputs",
                            "operation_outputs",
                            "toolpath_output",
                            "path_summary",
                            "combined_path_summary",
                            "collision_free",
                            "simulation_resolution_mm",
                            "require_collision_free",
                        )
                        if validation.get(key) not in (None, "", [], {})
                    }
                    simulation = validation.get("simulation")
                    if isinstance(simulation, Mapping):
                        accepted["simulation"] = {
                            key: simulation.get(key)
                            for key in (
                                "complete",
                                "stage",
                                "simulation_scope",
                                "command_count",
                                "executed_sweeps",
                                "cutting_sweeps",
                                "resolution_mm",
                                "grid",
                                "initial_volume_mm3",
                                "removed_volume_mm3",
                                "remaining_volume_mm3",
                                "modified_cells",
                                "removed_bounds",
                                "protected_model_checked",
                                "protected_model_collision",
                                "protected_model_volume_mm3",
                                "protected_model_volume_aggregation",
                                "holder_checked",
                                "fixture_checked",
                                "unavailable_checks",
                            )
                            if simulation.get(key) not in (None, "")
                        }
                    postprocess = validation.get("postprocess")
                    if isinstance(postprocess, Mapping):
                        accepted["postprocess"] = {
                            key: postprocess.get(key)
                            for key in (
                                "artifact_sha256",
                                "artifact_bytes",
                                "line_count",
                                "processor",
                                "processor_module",
                                "processor_class",
                                "units",
                                "comments",
                                "line_numbers",
                                "machine_configured",
                                "machine_name",
                                "machine_limits_checked",
                                "configuration_scope",
                            )
                            if postprocess.get(key) not in (None, "")
                        }
                    shape_facts = validation.get("shape_facts")
                    if isinstance(shape_facts, Mapping):
                        accepted["shape_facts"] = _compact_context_facts(shape_facts)
                    item["accepted_validation"] = accepted
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    item["accepted_validation_error"] = f"{type(exc).__name__}: {exc}"
            cam_objects.append(item)
        result["document_cam"] = {
            key: raw_cam.get(key)
            for key in (
                "object_count",
                "object_limit",
                "objects_truncated",
                "objects_omitted",
                "link_limit_per_object",
                "command_sample_limit_per_object",
            )
        }
        result["document_cam"]["objects"] = cam_objects
    raw_cam_shapes = snapshot.get("cam_shape_sources")
    if domain == "cam" and isinstance(raw_cam_shapes, Mapping):
        from vibescript_part_worker import part_shape_facts

        sources = []
        for raw in list(raw_cam_shapes.get("objects") or []):
            if not isinstance(raw, dict):
                continue
            item = {
                key: value for key, value in raw.items() if key != "_detached_shape"
            }
            shape = raw.get("_detached_shape")
            if shape is not None:
                try:
                    item["facts"] = part_shape_facts(
                        shape,
                        max_subelements=MAX_PART_CONTEXT_SUBELEMENTS,
                    )
                    item["eligible_for_cam_reference"] = bool(
                        item["facts"].get("valid")
                        and not item["facts"].get("null")
                        and item["facts"].get("solids")
                    )
                    if not item["eligible_for_cam_reference"]:
                        item["ineligible_reason"] = (
                            "CAM model references require a valid solid-bearing BREP."
                        )
                except Exception as exc:
                    item["error"] = f"Could not inspect detached shape: {exc}"
                    item["eligible_for_cam_reference"] = False
            item["selection_contract"] = (
                "published_interface"
                if bool(item.get("transient_topology"))
                or bool(item.get("requires_semantic_interfaces"))
                else "whole_shape_or_exact_face"
            )
            sources.append(item)
        result["cam_reference_candidates"] = {
            key: raw_cam_shapes.get(key)
            for key in (
                "object_count",
                "object_limit",
                "objects_truncated",
                "objects_omitted",
            )
        }
        result["cam_reference_candidates"]["objects"] = sources
    raw_techdraw = snapshot.get("techdraw_document")
    if domain == "techdraw" and isinstance(raw_techdraw, Mapping):
        techdraw_objects = []
        for raw in list(raw_techdraw.get("objects") or []):
            if not isinstance(raw, Mapping):
                continue
            item = {
                key: value for key, value in raw.items() if key != "_validation_json"
            }
            validation_json = raw.get("_validation_json")
            if isinstance(validation_json, str):
                try:
                    validation = json.loads(validation_json)
                    if not isinstance(validation, Mapping):
                        raise ValueError("validation is not an object")
                    item["accepted_validation"] = _bounded_context_value(
                        _compact_techdraw_validation(validation)
                    )
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    item["accepted_validation_error"] = f"{type(exc).__name__}: {exc}"
            techdraw_objects.append(item)
        result["document_techdraw"] = {
            key: raw_techdraw.get(key)
            for key in (
                "object_count",
                "object_limit",
                "objects_truncated",
                "objects_omitted",
                "link_limit_per_object",
                "text_line_limit_per_object",
                "text_character_limit",
            )
        }
        result["document_techdraw"]["objects"] = techdraw_objects
    raw_techdraw_shapes = snapshot.get("techdraw_shape_sources")
    if domain == "techdraw" and isinstance(raw_techdraw_shapes, Mapping):
        from vibescript_part_worker import part_shape_facts

        sources = []
        for raw in list(raw_techdraw_shapes.get("objects") or []):
            if not isinstance(raw, dict):
                continue
            item = {
                key: value for key, value in raw.items() if key != "_detached_shape"
            }
            shape = raw.get("_detached_shape")
            if shape is not None:
                try:
                    item["facts"] = part_shape_facts(
                        shape,
                        max_subelements=MAX_PART_CONTEXT_SUBELEMENTS,
                    )
                    item["eligible_for_techdraw_reference"] = bool(
                        item["facts"].get("valid")
                        and not item["facts"].get("null")
                        and item["facts"].get("edges")
                    )
                    if not item["eligible_for_techdraw_reference"]:
                        item["ineligible_reason"] = (
                            "TechDraw sources require a valid non-null BREP with edges."
                        )
                except Exception as exc:
                    item["error"] = f"Could not inspect detached shape: {exc}"
                    item["eligible_for_techdraw_reference"] = False
            item["selection_contract"] = "whole_object"
            sources.append(item)
        result["techdraw_reference_candidates"] = {
            key: raw_techdraw_shapes.get(key)
            for key in (
                "object_count",
                "object_limit",
                "objects_truncated",
                "objects_omitted",
            )
        }
        result["techdraw_reference_candidates"]["objects"] = sources
    raw_mesh = snapshot.get("mesh_document")
    if domain == "mesh" and isinstance(raw_mesh, Mapping):
        meshes = []
        for raw in list(raw_mesh.get("objects") or []):
            if not isinstance(raw, Mapping):
                continue
            item = {
                key: value for key, value in raw.items() if key != "_validation_json"
            }
            native_summary = item.get("native_summary")
            eligible = bool(
                isinstance(native_summary, Mapping)
                and type(native_summary.get("facets")) is int
                and native_summary["facets"] > 0
            )
            item["eligible_for_from_object"] = eligible
            item["eligible_for_shape_from_mesh"] = eligible
            if not eligible:
                item["ineligible_reason"] = (
                    "api.from_object and api.shape_from_mesh require a native "
                    "Mesh::Feature with at least one facet."
                )
            validation_json = raw.get("_validation_json")
            if isinstance(validation_json, str):
                try:
                    validation = json.loads(validation_json)
                    if not isinstance(validation, Mapping):
                        raise ValueError("validation is not an object")
                    diagnostics = validation.get("diagnostics")
                    item["accepted_validation"] = {
                        key: validation.get(key)
                        for key in ("schema", "operation", "label", "artifact_sha256")
                        if validation.get(key) not in (None, "")
                    }
                    if isinstance(diagnostics, Mapping):
                        item["accepted_validation"]["diagnostics"] = dict(diagnostics)
                    trace = validation.get("operation_trace")
                    if isinstance(trace, list):
                        item["accepted_validation"]["operation_trace"] = [
                            str(entry.get("operation") or "")
                            for entry in trace
                            if isinstance(entry, Mapping)
                        ]
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    item["accepted_validation_error"] = f"{type(exc).__name__}: {exc}"
            meshes.append(item)
        result["document_meshes"] = {
            key: raw_mesh.get(key)
            for key in (
                "object_count",
                "object_limit",
                "objects_truncated",
                "objects_omitted",
            )
        }
        result["document_meshes"]["objects"] = meshes
    raw_meshpart_meshes = (
        snapshot.get("reverse_mesh_sources")
        if domain == "reverse_engineering"
        else (
            snapshot.get("inspection_mesh_sources")
            if domain == "inspection"
            else snapshot.get("meshpart_mesh_sources")
        )
    )
    if domain in {"meshpart", "reverse_engineering", "inspection"} and isinstance(
        raw_meshpart_meshes, Mapping
    ):
        meshes = []
        for raw in list(raw_meshpart_meshes.get("objects") or []):
            if not isinstance(raw, Mapping):
                continue
            item = {
                key: value for key, value in raw.items() if key != "_validation_json"
            }
            native_summary = item.get("native_summary")
            eligible = bool(
                isinstance(native_summary, Mapping)
                and int(native_summary.get("facets") or 0) > 0
            )
            item[
                "eligible_for_inspection"
                if domain == "inspection"
                else "eligible_for_shape_from_mesh"
            ] = eligible
            if not eligible:
                item["ineligible_reason"] = (
                    "Inspection requires a native Mesh::Feature with at least one facet."
                    if domain == "inspection"
                    else "api.shape_from_mesh requires a native Mesh::Feature with at least one facet."
                )
            validation_json = raw.get("_validation_json")
            if isinstance(validation_json, str):
                try:
                    validation = json.loads(validation_json)
                    if not isinstance(validation, Mapping):
                        raise ValueError("validation is not an object")
                    if domain == "reverse_engineering":
                        accepted = {
                            key: validation.get(key)
                            for key in (
                                "schema",
                                "operation",
                                "label",
                                "source",
                                "geometry_fingerprint",
                                "operation_trace",
                                "fit_metrics",
                                "facts",
                            )
                            if validation.get(key) not in (None, "")
                        }
                    else:
                        accepted = {
                            key: validation.get(key)
                            for key in (
                                "schema",
                                "operation",
                                "label",
                                "artifact_sha256",
                                "method",
                                "mesher_backend",
                            )
                            if validation.get(key) not in (None, "")
                        }
                        diagnostics = validation.get("diagnostics")
                        if isinstance(diagnostics, Mapping):
                            accepted["diagnostics"] = dict(diagnostics)
                        segments = validation.get("segments")
                        if isinstance(segments, list):
                            accepted["segment_count"] = len(segments)
                    item["accepted_validation"] = accepted
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    item["accepted_validation_error"] = f"{type(exc).__name__}: {exc}"
            meshes.append(item)
        result["document_mesh_sources"] = {
            key: raw_meshpart_meshes.get(key)
            for key in (
                "object_count",
                "object_limit",
                "objects_truncated",
                "objects_omitted",
            )
        }
        result["document_mesh_sources"]["objects"] = meshes
    raw_points = (
        snapshot.get("reverse_point_sources")
        if domain == "reverse_engineering"
        else (
            snapshot.get("inspection_point_sources")
            if domain == "inspection"
            else snapshot.get("points_document")
        )
    )
    if domain in {"points", "reverse_engineering", "inspection"} and isinstance(
        raw_points, Mapping
    ):
        clouds = []
        for raw in list(raw_points.get("objects") or []):
            if not isinstance(raw, Mapping):
                continue
            item = {
                key: value for key, value in raw.items() if key != "_validation_json"
            }
            validation_json = raw.get("_validation_json")
            if isinstance(validation_json, str):
                try:
                    validation = json.loads(validation_json)
                    if not isinstance(validation, Mapping):
                        raise ValueError("validation is not an object")
                    accepted = {
                        key: validation.get(key)
                        for key in (
                            "schema",
                            "operation",
                            "label",
                            "source",
                            "input_point_count",
                            "output_point_count",
                            "invalid_points_removed",
                            "preserve_attributes",
                            "facts",
                        )
                        if validation.get(key) not in (None, "")
                    }
                    trace = validation.get("operation_trace")
                    if isinstance(trace, list):
                        accepted["operation_trace"] = [
                            {
                                key: entry.get(key)
                                for key in (
                                    "op",
                                    "method",
                                    "input_count",
                                    "output_count",
                                )
                            }
                            for entry in trace
                            if isinstance(entry, Mapping)
                        ]
                    item["accepted_validation"] = accepted
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    item["accepted_validation_error"] = f"{type(exc).__name__}: {exc}"
            clouds.append(item)
        result["document_point_clouds"] = {
            key: raw_points.get(key)
            for key in (
                "object_count",
                "object_limit",
                "objects_truncated",
                "objects_omitted",
                "sample_limit_per_object",
            )
        }
        result["document_point_clouds"]["objects"] = clouds
        if domain in {"points", "reverse_engineering"}:
            project_root = str(snapshot.get("project_root") or "").strip()
            if project_root:
                try:
                    from VibeCADPointArtifacts import point_artifacts_summary

                    result["approved_point_artifacts"] = point_artifacts_summary(
                        project_root
                    )
                except Exception as exc:
                    result["approved_point_artifacts"] = {
                        "available": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
            else:
                result["approved_point_artifacts"] = {
                    "available": False,
                    "error": "The active project has no artifact root.",
                }
    if domain == "reverse_engineering":
        try:
            from vibescript_reverse_engineering_worker import native_capabilities

            result["native_reverse_engineering_capabilities"] = native_capabilities()
        except Exception as exc:
            result["native_reverse_engineering_capabilities"] = {
                "available": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
    return result


_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_PROGRAM_NAME_PATTERN = r"^[A-Za-z][A-Za-z0-9 ._-]{0,119}$"
_PROJECT_ARTIFACT_ID = re.compile(r"^[0-9a-f]{32}$")
_DRIVE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_BLOCKED_NAMES = frozenset(
    {
        "__import__",
        "breakpoint",
        "compile",
        "eval",
        "exec",
        "globals",
        "help",
        "input",
        "locals",
        "open",
        "vars",
    }
)
_BLOCKED_DOC_METHODS = frozenset(
    {
        "close",
        "open",
        "restore",
        "save",
        "saveAs",
        "saveCopy",
        "saveToFile",
    }
)


def validate_program_source(source: str) -> None:
    text = str(source or "")
    encoded = text.encode("utf-8")
    if not text.strip():
        raise ValueError("VibeScript program source is required.")
    if len(encoded) > MAX_SOURCE_BYTES:
        raise ValueError(f"VibeScript source exceeds {MAX_SOURCE_BYTES} UTF-8 bytes.")
    if "\x00" in text:
        raise ValueError("VibeScript source cannot contain NUL bytes.")
    try:
        tree = ast.parse(text, filename="<vibecad-domain-vibescript>", mode="exec")
    except SyntaxError as exc:
        raise ValueError(f"VibeScript source has invalid syntax: {exc}") from exc
    violations: list[str] = []
    for node in ast.walk(tree):
        line = int(getattr(node, "lineno", 0) or 0)
        if isinstance(node, ast.Import):
            if any(alias.name != "api" for alias in node.names):
                violations.append(
                    f"line {line}: only the prebound api module may be imported"
                )
        elif isinstance(node, ast.ImportFrom):
            if node.level != 0 or node.module != "api":
                violations.append(
                    f"line {line}: only names from the prebound api module may be imported"
                )
        elif isinstance(node, ast.Name) and node.id in _BLOCKED_NAMES:
            violations.append(f"line {line}: name {node.id!r} is not allowed")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                violations.append(f"line {line}: private attributes are not allowed")
            if (
                isinstance(node.value, ast.Name)
                and node.value.id == "doc"
                and node.attr in _BLOCKED_DOC_METHODS
            ):
                violations.append(
                    f"line {line}: document lifecycle method {node.attr!r} is not allowed"
                )
    if violations:
        raise ValueError(
            "VibeScript source policy violation: " + "; ".join(violations[:12])
        )


def _json_size(value: Any, label: str) -> int:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be bounded JSON: {exc}") from exc
    if len(encoded) > MAX_INPUT_BYTES:
        raise ValueError(f"{label} exceeds {MAX_INPUT_BYTES} JSON bytes.")
    return len(encoded)


def _validate_json_value(value: Any, *, path: str, depth: int = 0) -> None:
    if depth > MAX_INPUT_DEPTH:
        raise ValueError(f"{path} exceeds the maximum JSON nesting depth.")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must be finite.")
        return
    if isinstance(value, str):
        if len(value) > 16_384:
            raise ValueError(f"{path} exceeds 16384 characters.")
        if value.startswith(("/", "\\")) or _DRIVE_PATH.match(value):
            raise ValueError(f"{path} cannot contain a raw filesystem path.")
        parts = PurePath(value.replace("\\", "/")).parts
        if ".." in parts:
            raise ValueError(f"{path} cannot traverse a filesystem path.")
        return
    if isinstance(value, list):
        if len(value) > MAX_ARRAY_ITEMS:
            raise ValueError(f"{path} exceeds {MAX_ARRAY_ITEMS} array items.")
        for index, item in enumerate(value):
            _validate_json_value(item, path=f"{path}[{index}]", depth=depth + 1)
        return
    if isinstance(value, dict):
        if is_document_reference(value):
            if not all(
                isinstance(value.get(name), str) and str(value[name]).strip()
                for name in ("document_uid", "object_name")
            ):
                raise ValueError(f"{path} contains an invalid stable object reference.")
            try:
                normalize_document_reference(value)
            except DocumentReferenceError as exc:
                raise ValueError(
                    f"{path} contains an invalid stable object reference: {exc}"
                ) from exc
            return
        if set(value) == {"artifact_id"}:
            artifact_id = value.get("artifact_id")
            if not isinstance(artifact_id, str) or not _PROJECT_ARTIFACT_ID.fullmatch(
                artifact_id
            ):
                raise ValueError(
                    f"{path} contains an invalid stable artifact reference."
                )
            return
        raise ValueError(
            f"{path} contains an arbitrary object; only stable document or project "
            "artifact references are allowed."
        )
    raise ValueError(f"{path} contains unsupported value type {type(value).__name__}.")


def validate_inputs(inputs: Any) -> dict[str, Any]:
    if not isinstance(inputs, dict):
        raise ValueError("inputs must be a JSON object.")
    _json_size(inputs, "inputs")
    result: dict[str, Any] = {}
    for key, value in inputs.items():
        clean = str(key or "")
        if not _IDENTIFIER.fullmatch(clean):
            raise ValueError(f"Invalid VibeScript input name: {key!r}.")
        _validate_json_value(value, path=f"inputs.{clean}")
        result[clean] = value
    return result


def _infer_input_value_schema(value: Any) -> dict[str, Any]:
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        branches: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in value:
            branch = _infer_input_value_schema(item)
            key = json.dumps(branch, sort_keys=True, separators=(",", ":"))
            if key not in seen:
                seen.add(key)
                branches.append(branch)
        if not branches:
            items: dict[str, Any] = {}
        elif len(branches) == 1:
            items = branches[0]
        else:
            items = {"oneOf": branches[:8]}
        return {
            "type": "array",
            "items": items,
            "maxItems": MAX_ARRAY_ITEMS,
        }
    if isinstance(value, dict):
        properties = {
            str(name): _infer_input_value_schema(item) for name, item in value.items()
        }
        schema = {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }
        if is_document_reference(value):
            schema["x-vibecad-reference"] = True
            schema["required"] = ["document_uid", "object_name"]
        elif set(value) == {"artifact_id"}:
            schema["x-vibecad-point-artifact"] = True
            schema["properties"]["artifact_id"]["pattern"] = "^[0-9a-f]{32}$"
        return schema
    return {}


def synchronize_input_schema(
    schema: Mapping[str, Any],
    inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep existing constraints while making edited input names buildable."""

    current = dict(schema) if isinstance(schema, Mapping) else {}
    old_properties = (
        dict(current.get("properties") or {})
        if isinstance(current.get("properties"), Mapping)
        else {}
    )
    old_required = {
        str(name)
        for name in list(current.get("required") or [])
        if isinstance(name, str)
    }
    clean_inputs = {str(name): value for name, value in inputs.items()}
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, value in clean_inputs.items():
        if name in old_properties and isinstance(old_properties[name], Mapping):
            property_schema = dict(old_properties[name])
            if (
                is_document_reference(value)
                and property_schema.get("x-vibecad-reference") is True
                and "document_path" in value
            ):
                reference_properties = dict(property_schema.get("properties") or {})
                reference_properties.setdefault(
                    "document_path",
                    {"type": "string"},
                )
                property_schema["properties"] = reference_properties
            properties[name] = property_schema
            if name in old_required:
                required.append(name)
        else:
            properties[name] = _infer_input_value_schema(value)
            required.append(name)
    for name, property_schema in old_properties.items():
        clean_name = str(name)
        if (
            clean_name not in properties
            and clean_name not in old_required
            and isinstance(property_schema, Mapping)
        ):
            properties[clean_name] = dict(property_schema)
    result = {
        key: value
        for key, value in current.items()
        if key not in {"type", "properties", "required", "additionalProperties"}
    }
    result.update(
        {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }
    )
    return result


def _validate_input_schema_node(schema: Any, *, path: str, depth: int = 0) -> None:
    if not isinstance(schema, dict):
        raise ValueError(f"{path} must be a JSON Schema object.")
    if depth > MAX_INPUT_DEPTH:
        raise ValueError(f"{path} exceeds the supported schema depth.")
    forbidden = set(schema) & {
        "$anchor",
        "$dynamicRef",
        "$id",
        "$ref",
        "contentEncoding",
        "contentMediaType",
        "patternProperties",
        "unevaluatedProperties",
    }
    if forbidden:
        raise ValueError(
            f"{path} uses unsupported schema keywords: {sorted(forbidden)}."
        )
    combinators = [name for name in ("oneOf", "anyOf", "allOf") if name in schema]
    if combinators:
        if len(combinators) != 1 or combinators[0] != "oneOf":
            raise ValueError(f"{path} may use only one bounded oneOf combinator.")
        if schema.get("type") is not None:
            raise ValueError(f"{path} cannot combine type with oneOf.")
        branches = schema.get("oneOf")
        if not isinstance(branches, list) or not 1 <= len(branches) <= 8:
            raise ValueError(f"{path}.oneOf must contain 1-8 schema branches.")
        for index, branch in enumerate(branches):
            _validate_input_schema_node(
                branch,
                path=f"{path}.oneOf[{index}]",
                depth=depth + 1,
            )
        return
    raw_type = schema.get("type")
    types = set(raw_type if isinstance(raw_type, list) else [raw_type])
    types.discard(None)
    if not types <= {
        "null",
        "boolean",
        "integer",
        "number",
        "string",
        "array",
        "object",
    }:
        raise ValueError(f"{path} declares unsupported JSON types: {sorted(types)}.")
    if "array" in types:
        maximum = schema.get("maxItems")
        if not isinstance(maximum, int) or not 0 <= maximum <= MAX_ARRAY_ITEMS:
            raise ValueError(
                f"{path} arrays must declare maxItems between 0 and {MAX_ARRAY_ITEMS}."
            )
        _validate_input_schema_node(
            schema.get("items"), path=f"{path}.items", depth=depth + 1
        )
    if "object" in types:
        if schema.get("x-vibecad-reference") is True:
            properties = schema.get("properties")
            property_names = set(properties) if isinstance(properties, dict) else set()
            if (
                not isinstance(properties, dict)
                or not {"document_uid", "object_name"} <= property_names
                or property_names
                - {
                    "document_uid",
                    "object_name",
                    "document_path",
                }
            ):
                raise ValueError(
                    f"{path} stable references require document_uid and object_name "
                    "and may optionally declare document_path."
                )
            if set(schema.get("required") or []) != {"document_uid", "object_name"}:
                raise ValueError(
                    f"{path} stable references must require document_uid and object_name."
                )
            if schema.get("additionalProperties") is not False:
                raise ValueError(
                    f"{path} stable references must set additionalProperties to false."
                )
            if any(
                not isinstance(properties[name], dict)
                or properties[name].get("type") != "string"
                for name in property_names
            ):
                raise ValueError(f"{path} stable reference fields must be strings.")
        elif schema.get("x-vibecad-point-artifact") is True:
            properties = schema.get("properties")
            if not isinstance(properties, dict) or set(properties) != {"artifact_id"}:
                raise ValueError(
                    f"{path} stable point artifacts require exactly artifact_id."
                )
            artifact_schema = properties["artifact_id"]
            if (
                set(schema.get("required") or []) != {"artifact_id"}
                or schema.get("additionalProperties") is not False
                or not isinstance(artifact_schema, dict)
                or artifact_schema.get("type") != "string"
                or artifact_schema.get("pattern") != "^[0-9a-f]{32}$"
            ):
                raise ValueError(
                    f"{path} stable point artifact ids must use the exact bounded "
                    "32-character schema."
                )
        elif depth == 0:
            properties = schema.get("properties")
            if not isinstance(properties, dict):
                raise ValueError("input_schema requires a properties object.")
            if schema.get("additionalProperties") is not False:
                raise ValueError("input_schema must set additionalProperties to false.")
            for name, child in properties.items():
                if not _IDENTIFIER.fullmatch(str(name or "")):
                    raise ValueError(f"Invalid input_schema property name: {name!r}.")
                _validate_input_schema_node(
                    child, path=f"{path}.properties.{name}", depth=depth + 1
                )
        else:
            raise ValueError(
                f"{path} arbitrary object inputs are forbidden; use a stable reference."
            )


def validate_input_schema(schema: Any) -> dict[str, Any]:
    if not isinstance(schema, dict):
        raise ValueError("input_schema must be a JSON Schema object.")
    _json_size(schema, "input_schema")
    _validate_input_schema_node(schema, path="input_schema")
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        raise ValueError(f"input_schema is not valid JSON Schema: {exc}") from exc
    return json.loads(json.dumps(schema))


def validate_expected_outputs(
    pack: VibeScriptWorkbenchPack, value: Any
) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise ValueError("expected_outputs must be a non-empty array.")
    if len(value) > MAX_OUTPUTS:
        raise ValueError(f"expected_outputs may contain at most {MAX_OUTPUTS} entries.")
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise ValueError(f"expected_outputs[{index}] must be an object.")
        if set(raw) != {"name", "type"}:
            raise ValueError(
                f"expected_outputs[{index}] must contain exactly name and type."
            )
        name = str(raw.get("name") or "")
        output_type = str(raw.get("type") or "")
        if not _IDENTIFIER.fullmatch(name):
            raise ValueError(f"Invalid output name: {name!r}.")
        if name in seen:
            raise ValueError(f"Duplicate output name: {name!r}.")
        if output_type not in pack.output_types:
            raise ValueError(
                f"Output {name!r} type must be one of {sorted(pack.output_types)}."
            )
        seen.add(name)
        result.append({"name": name, "type": output_type})
    return result


def validate_program_contract(
    pack: VibeScriptWorkbenchPack,
    *,
    source: str,
    input_schema: Any,
    inputs: Any,
    expected_outputs: Any,
) -> dict[str, Any]:
    validate_program_source(source)
    adapter = get_domain_adapter(pack.domain)
    if adapter is not None:
        adapter.validate_source(source)
    clean_inputs = validate_inputs(inputs)
    normalized_schema = copy.deepcopy(input_schema)
    if isinstance(normalized_schema, dict):
        properties = normalized_schema.get("properties")
        if isinstance(properties, dict):
            for name, value in clean_inputs.items():
                property_schema = properties.get(name)
                if (
                    is_document_reference(value)
                    and isinstance(property_schema, dict)
                    and property_schema.get("x-vibecad-reference") is True
                ):
                    property_schema.setdefault("type", "object")
                    reference_properties = property_schema.setdefault("properties", {})
                    if isinstance(reference_properties, dict):
                        reference_properties.setdefault(
                            "document_uid", {"type": "string"}
                        )
                        reference_properties.setdefault(
                            "object_name", {"type": "string"}
                        )
                        if "document_path" in value:
                            reference_properties.setdefault(
                                "document_path", {"type": "string"}
                            )
                    property_schema.setdefault(
                        "required", ["document_uid", "object_name"]
                    )
                    property_schema.setdefault("additionalProperties", False)
    clean_schema = validate_input_schema(normalized_schema)
    errors = sorted(
        Draft202012Validator(clean_schema).iter_errors(clean_inputs),
        key=lambda error: tuple(str(item) for item in error.absolute_path),
    )
    if errors:
        error = errors[0]
        location = ".".join(str(item) for item in error.absolute_path)
        raise ValueError(
            "inputs do not satisfy input_schema"
            + (f" at {location}" if location else "")
            + f": {error.message}"
        )
    outputs = validate_expected_outputs(pack, expected_outputs)
    return {
        "source": str(source),
        "input_schema": clean_schema,
        "inputs": clean_inputs,
        "expected_outputs": outputs,
    }


def program_revision(
    *,
    domain: str,
    source: str,
    input_schema: Mapping[str, Any],
    inputs: Mapping[str, Any],
    expected_outputs: list[dict[str, str]],
) -> str:
    payload = {
        "schema": PROGRAM_SCHEMA,
        "domain": str(domain),
        "source": str(source),
        "input_schema": dict(input_schema),
        "inputs": dict(inputs),
        "expected_outputs": list(expected_outputs),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def program_revision_with_references(
    *,
    contract_revision: str,
    references: list[Mapping[str, Any]],
) -> str:
    """Bind a revision to exact detached document or approved input artifacts.

    Existing BREP dependency payloads intentionally retain their byte-for-byte
    v2 shape so accepted revisions in production domains do not churn. MeshPart
    adds an explicitly typed mesh digest only when the source is a mesh. Points
    uses new typed payloads for point-document snapshots and project approvals.
    """

    dependencies = []
    for item in references:
        if item.get("reference_kind") == "point_artifact":
            dependency = {
                "reference_kind": "point_artifact",
                "artifact_id": str(item.get("artifact_id") or ""),
                "artifact_sha256": str(item.get("artifact_sha256") or ""),
            }
            if not re.fullmatch(
                r"[0-9a-f]{32}", dependency["artifact_id"]
            ) or not re.fullmatch(r"[0-9a-f]{64}", dependency["artifact_sha256"]):
                raise ValueError(
                    "Resolved point artifacts require a valid id and SHA-256."
                )
            dependencies.append(dependency)
            continue
        dependency = {
            "document_uid": str(item.get("document_uid") or ""),
            "object_name": str(item.get("object_name") or ""),
        }
        if item.get("artifact_kind") == "points_asc":
            dependency["artifact_kind"] = "points_asc"
            dependency["points_sha256"] = str(item.get("artifact_sha256") or "")
            if item.get("reference_contract_sha256"):
                dependency["reference_contract_sha256"] = str(
                    item.get("reference_contract_sha256") or ""
                )
            if (
                not dependency["document_uid"]
                or not dependency["object_name"]
                or not re.fullmatch(r"[0-9a-f]{64}", dependency["points_sha256"])
            ):
                raise ValueError(
                    "Resolved Points document references require identity and a "
                    "valid artifact SHA-256."
                )
            dependencies.append(dependency)
            continue
        if item.get("artifact_kind") == "component_identity":
            dependency["artifact_kind"] = "component_identity"
            dependency["type_id"] = str(item.get("type_id") or "")
            if item.get("reference_contract_sha256"):
                dependency["reference_contract_sha256"] = str(
                    item.get("reference_contract_sha256") or ""
                )
            if (
                not dependency["document_uid"]
                or not dependency["object_name"]
                or not dependency["type_id"]
            ):
                raise ValueError(
                    "Resolved component identities require document, object, and "
                    "native type identity."
                )
            dependencies.append(dependency)
            continue
        brep_digest = str(item.get("brep_sha256") or "")
        mesh_digest = str(item.get("mesh_sha256") or "")
        if bool(brep_digest) == bool(mesh_digest):
            raise ValueError(
                "Resolved document references require exactly one BREP or mesh SHA-256."
            )
        if brep_digest:
            dependency["brep_sha256"] = brep_digest
        else:
            dependency["artifact_kind"] = "mesh_bms"
            dependency["mesh_sha256"] = mesh_digest
        if item.get("reference_contract_sha256"):
            dependency["reference_contract_sha256"] = str(
                item.get("reference_contract_sha256") or ""
            )
        dependencies.append(dependency)
    if any(
        not item["document_uid"]
        or not item["object_name"]
        or (
            "brep_sha256" in item
            and not re.fullmatch(r"[0-9a-f]{64}", item["brep_sha256"])
        )
        or (
            "mesh_sha256" in item
            and (
                item.get("artifact_kind") != "mesh_bms"
                or not re.fullmatch(r"[0-9a-f]{64}", item["mesh_sha256"])
            )
        )
        or (
            "points_sha256" in item
            and (
                item.get("artifact_kind") != "points_asc"
                or not re.fullmatch(r"[0-9a-f]{64}", item["points_sha256"])
            )
        )
        or (
            item.get("artifact_kind") == "component_identity"
            and not item.get("type_id")
        )
        for item in dependencies
        if item.get("reference_kind") != "point_artifact"
    ):
        raise ValueError(
            "Resolved document references require identity and a valid artifact SHA-256."
        )
    if any(
        "reference_contract_sha256" in item
        and not re.fullmatch(r"[0-9a-f]{64}", item["reference_contract_sha256"])
        for item in dependencies
        if item.get("reference_kind") != "point_artifact"
    ):
        raise ValueError("Resolved reference contracts require a SHA-256 digest.")
    encoded = json.dumps(
        {
            "schema": PROGRAM_SCHEMA,
            "contract_revision": str(contract_revision),
            "resolved_references": dependencies,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _encode_document_program_payload(payload: Mapping[str, Any], label: str) -> str:
    encoded = json.dumps(
        dict(payload),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    size = len(encoded.encode("utf-8"))
    if size > MAX_DOCUMENT_PROGRAM_BYTES:
        raise ValueError(
            f"{label} is {size} bytes; the portable document limit is "
            f"{MAX_DOCUMENT_PROGRAM_BYTES} bytes."
        )
    return encoded


def _decode_document_program_payload(raw: str, label: str) -> dict[str, Any]:
    encoded = str(raw or "")
    size = len(encoded.encode("utf-8"))
    if not encoded:
        raise ValueError(f"{label} is empty.")
    if size > MAX_DOCUMENT_PROGRAM_BYTES:
        raise ValueError(
            f"{label} is {size} bytes; the portable document limit is "
            f"{MAX_DOCUMENT_PROGRAM_BYTES} bytes."
        )
    try:
        value = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain one JSON object.")
    return value


def encode_document_program_contract(
    pack: VibeScriptWorkbenchPack,
    *,
    program_id: str,
    label: str,
    revision: str,
    source: str,
    input_schema: Any,
    inputs: Any,
    expected_outputs: Any,
) -> str:
    """Serialize an accepted program contract into its owning FCStd object."""

    clean_program_id = str(program_id or "").strip().lower()
    clean_revision = str(revision or "").strip().lower()
    clean_label = str(label or "").strip()
    if not re.fullmatch(r"[0-9a-f]{32}", clean_program_id):
        raise ValueError("A portable VibeScript program requires a stable program id.")
    if not re.fullmatch(r"[0-9a-f]{64}", clean_revision):
        raise ValueError("A portable VibeScript program requires an accepted revision.")
    if not clean_label or len(clean_label) > 120:
        raise ValueError("A portable VibeScript program requires a bounded label.")
    clean = validate_program_contract(
        pack,
        source=source,
        input_schema=input_schema,
        inputs=inputs,
        expected_outputs=expected_outputs,
    )
    contract_revision = program_revision(domain=pack.domain, **clean)
    return _encode_document_program_payload(
        {
            "schema": DOCUMENT_PROGRAM_SCHEMA,
            "program_schema": PROGRAM_SCHEMA,
            "program_id": clean_program_id,
            "domain": pack.domain,
            "workbench": pack.workbench,
            "label": clean_label,
            "revision": clean_revision,
            "contract_revision": contract_revision,
            **clean,
        },
        "Portable VibeScript program contract",
    )


def decode_document_program_contract(
    raw: str,
    pack: VibeScriptWorkbenchPack,
    *,
    expected_program_id: str = "",
    expected_revision: str = "",
) -> dict[str, Any]:
    """Validate an FCStd-embedded program and return a normal v2 manifest."""

    value = _decode_document_program_payload(
        raw,
        "Portable VibeScript program contract",
    )
    if value.get("schema") != DOCUMENT_PROGRAM_SCHEMA:
        raise ValueError("Unsupported portable VibeScript program schema.")
    if value.get("program_schema") != PROGRAM_SCHEMA:
        raise ValueError(
            "The portable program does not contain a VibeScript v2 contract."
        )
    program_id = str(value.get("program_id") or "").strip().lower()
    revision = str(value.get("revision") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{32}", program_id):
        raise ValueError("The portable VibeScript program id is invalid.")
    if not re.fullmatch(r"[0-9a-f]{64}", revision):
        raise ValueError("The portable VibeScript revision is invalid.")
    if expected_program_id and program_id != str(expected_program_id).strip().lower():
        raise ValueError("The portable VibeScript program id does not match its owner.")
    if expected_revision and revision != str(expected_revision).strip().lower():
        raise ValueError("The portable VibeScript revision does not match its owner.")
    if str(value.get("domain") or "") != pack.domain:
        raise ValueError("The portable VibeScript program belongs to another domain.")
    if str(value.get("workbench") or "") != pack.workbench:
        raise ValueError(
            "The portable VibeScript program belongs to another workbench."
        )
    label = str(value.get("label") or "").strip()
    if not label or len(label) > 120:
        raise ValueError("The portable VibeScript program label is invalid.")
    clean = validate_program_contract(
        pack,
        source=str(value.get("source") or ""),
        input_schema=value.get("input_schema"),
        inputs=value.get("inputs"),
        expected_outputs=value.get("expected_outputs"),
    )
    contract_revision = program_revision(domain=pack.domain, **clean)
    if contract_revision != str(value.get("contract_revision") or ""):
        raise ValueError("The portable VibeScript program contract digest changed.")
    accepted_contract = {**clean, "revision": revision}
    return {
        "schema": PROGRAM_SCHEMA,
        "version": PROGRAM_VERSION,
        "program_id": program_id,
        "domain": pack.domain,
        "workbench": pack.workbench,
        "label": label,
        **clean,
        "working_revision": revision,
        "accepted_revision": revision,
        "accepted_contract": accepted_contract,
        "live_outputs": {},
        "latest_candidate": {
            "revision": revision,
            "status": "accepted",
            "portable_document_contract": True,
        },
        "portable_document_contract": True,
    }


def encode_editor_draft(
    *,
    program_id: str,
    domain: str,
    base_revision: str,
    source: str,
    input_schema: Mapping[str, Any],
    inputs_json: str,
    expected_outputs: list[Mapping[str, Any]],
) -> str:
    """Serialize unvalidated editor text without requiring a successful build."""

    clean_program_id = str(program_id or "").strip().lower()
    clean_domain = str(domain or "").strip().lower()
    clean_revision = str(base_revision or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{32}", clean_program_id):
        raise ValueError("A VibeScript editor draft requires a stable program id.")
    if not clean_domain:
        raise ValueError("A VibeScript editor draft requires a domain.")
    if clean_revision and not re.fullmatch(r"[0-9a-f]{64}", clean_revision):
        raise ValueError("A VibeScript editor draft has an invalid base revision.")
    if len(str(source).encode("utf-8")) > MAX_SOURCE_BYTES:
        raise ValueError("The VibeScript editor draft source is too large.")
    if len(str(inputs_json).encode("utf-8")) > MAX_INPUT_BYTES:
        raise ValueError("The VibeScript editor draft inputs are too large.")
    return _encode_document_program_payload(
        {
            "schema": EDITOR_DRAFT_SCHEMA,
            "program_id": clean_program_id,
            "domain": clean_domain,
            "base_revision": clean_revision,
            "source": str(source),
            "input_schema": dict(input_schema),
            "inputs_json": str(inputs_json),
            "expected_outputs": [dict(item) for item in expected_outputs],
        },
        "VibeScript editor draft",
    )


def decode_editor_draft(
    raw: str,
    *,
    expected_program_id: str = "",
    expected_domain: str = "",
) -> dict[str, Any]:
    """Read an editor draft while deliberately leaving source/inputs unvalidated."""

    value = _decode_document_program_payload(raw, "VibeScript editor draft")
    if value.get("schema") != EDITOR_DRAFT_SCHEMA:
        raise ValueError("Unsupported VibeScript editor draft schema.")
    program_id = str(value.get("program_id") or "").strip().lower()
    domain = str(value.get("domain") or "").strip().lower()
    base_revision = str(value.get("base_revision") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{32}", program_id):
        raise ValueError("The VibeScript editor draft program id is invalid.")
    if expected_program_id and program_id != str(expected_program_id).strip().lower():
        raise ValueError("The VibeScript editor draft belongs to another program.")
    if expected_domain and domain != str(expected_domain).strip().lower():
        raise ValueError("The VibeScript editor draft belongs to another domain.")
    if base_revision and not re.fullmatch(r"[0-9a-f]{64}", base_revision):
        raise ValueError("The VibeScript editor draft base revision is invalid.")
    source = value.get("source")
    inputs_json = value.get("inputs_json")
    input_schema = value.get("input_schema")
    expected_outputs = value.get("expected_outputs")
    if (
        not isinstance(source, str)
        or not isinstance(inputs_json, str)
        or not isinstance(input_schema, dict)
        or not isinstance(expected_outputs, list)
        or any(not isinstance(item, dict) for item in expected_outputs)
    ):
        raise ValueError("The VibeScript editor draft fields are malformed.")
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise ValueError("The VibeScript editor draft source is too large.")
    if len(inputs_json.encode("utf-8")) > MAX_INPUT_BYTES:
        raise ValueError("The VibeScript editor draft inputs are too large.")
    return {
        "program_id": program_id,
        "domain": domain,
        "base_revision": base_revision,
        "source": source,
        "input_schema": dict(input_schema),
        "inputs_json": inputs_json,
        "expected_outputs": [dict(item) for item in expected_outputs],
    }


def migrate_program_manifest(
    manifest: Mapping[str, Any],
    *,
    artifact_directory: str | Path | None = None,
) -> dict[str, Any]:
    """Return one v2 view without relocating a v1 Part Design directory."""

    raw = dict(manifest)
    if raw.get("schema") == PROGRAM_SCHEMA and int(raw.get("version") or 0) == 2:
        result = dict(raw)
    elif raw.get("schema") == PARTDESIGN_V1_SCHEMA:
        directory = Path(artifact_directory) if artifact_directory is not None else None
        source = str(raw.get("source") or "")
        if not source and directory is not None and (directory / "model.py").is_file():
            source = (directory / "model.py").read_text(encoding="utf-8")
        parameters = raw.get("parameters")
        if (
            parameters is None
            and directory is not None
            and (directory / "parameters.json").is_file()
        ):
            parameters = json.loads(
                (directory / "parameters.json").read_text(encoding="utf-8")
            )
        if not isinstance(parameters, dict):
            parameters = {}
        output_map = raw.get("outputs") if isinstance(raw.get("outputs"), dict) else {}
        declarations = raw.get("expected_outputs") or list(output_map)
        names = [
            str(item.get("name") or "") if isinstance(item, Mapping) else str(item)
            for item in declarations
        ]
        names = [name for name in names if name]
        result = {
            "schema": PROGRAM_SCHEMA,
            "version": PROGRAM_VERSION,
            "program_id": str(raw.get("model_id") or raw.get("program_id") or ""),
            "domain": "partdesign",
            "workbench": "PartDesignWorkbench",
            "label": str(raw.get("label") or raw.get("model_name") or ""),
            "source": source,
            "input_schema": {
                "type": "object",
                "properties": {str(name): {"type": "number"} for name in parameters},
                "additionalProperties": False,
            },
            "inputs": dict(parameters),
            "expected_outputs": [
                {"name": str(name), "type": "solid"} for name in names
            ],
            "working_revision": str(
                raw.get("working_revision") or raw.get("revision") or ""
            ),
            "accepted_revision": str(
                raw.get("accepted_revision") or raw.get("revision") or ""
            ),
            "live_outputs": dict(output_map),
            "imported_from_schema": PARTDESIGN_V1_SCHEMA,
            "migration_required": True,
            "migration_reason": (
                "The saved source uses the v1 Part Design execution contract. The "
                "accepted live objects remain available, but this source cannot execute "
                "in the v2 domain runtime."
            ),
            "migration_action": "vibescript.edit_source",
        }
    else:
        raise ValueError("Unsupported VibeScript program manifest schema.")
    if artifact_directory is not None:
        result["artifact_directory"] = str(Path(artifact_directory))
    return result


def _property_schema(description: str, **schema: Any) -> dict[str, Any]:
    return {"description": description, **schema}


def _base_tool_spec(
    pack: VibeScriptWorkbenchPack,
    operation: str,
    *,
    description: str,
    properties: dict[str, Any],
    required: tuple[str, ...],
    safety: str,
) -> dict[str, Any]:
    return {
        "name": f"vibescript.{pack.domain}.{operation}",
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": list(required),
            "additionalProperties": False,
        },
        "safety": safety,
        "workbench": pack.workbench,
        "contextual": True,
        "requires_document": operation != "describe_api",
        "edit_modes": ["none"],
    }


def universal_tool_specs() -> tuple[dict[str, Any], ...]:
    from VibeCADAssemblyGraphProgram import assembly_program_tool_spec

    domain = _property_schema(
        "Authoring domain; omit for the active surface default.",
        type="string",
        enum=sorted(
            {pack.domain for pack in VIBESCRIPT_WORKBENCH_PACKS.values()}
        ),
    )
    program = _property_schema(
        "Program name in the active document/domain, or returned document/domain/name.",
        type="string",
        minLength=1,
        maxLength=300,
        pattern="^[^/]+(?:/[^/]+/[^/]+)?$",
    )
    revision = _property_schema(
        "Exact latest source revision.",
        type="string",
        pattern="^[0-9a-f]{64}$",
    )
    source = _property_schema(
        (
            "Python using api, inputs, and doc. Define main(). Return the final value "
            "for one output or an ordered mapping matching multiple expected_outputs."
        ),
        type="string",
        minLength=1,
        maxLength=MAX_SOURCE_BYTES,
    )
    input_schema = _property_schema(
        "JSON Schema for inputs; component references use x-vibecad-reference=true.",
        type="object",
    )
    inputs = _property_schema(
        (
            "Values for input_schema. Component inputs use catalog_key; source uses "
            "api.component(inputs['name'])."
        ),
        type="object",
    )
    outputs = _property_schema(
        "Published deliverables; names equal mapping keys returned by main().",
        type="array",
        minItems=1,
        maxItems=MAX_OUTPUTS,
        items={
            "type": "object",
            "properties": {
                "name": {"type": "string", "pattern": _IDENTIFIER.pattern},
                "type": {"type": "string", "pattern": _IDENTIFIER.pattern},
            },
            "required": ["name", "type"],
            "additionalProperties": False,
        },
    )
    program_name = _property_schema(
        "Program label.",
        type="string",
        minLength=1,
        maxLength=120,
        pattern=_PROGRAM_NAME_PATTERN,
    )
    component_outputs = copy.deepcopy(outputs)
    component_outputs["items"]["properties"]["type"]["enum"] = [
        output_type
        for output_type in next(
            pack.output_types
            for pack in VIBESCRIPT_WORKBENCH_PACKS.values()
            if pack.domain == "partdesign"
        )
        if output_type != "component_link"
    ]
    geometry_reference = {
        "description": "Exact object reference returned by VibeCAD.",
        "type": "object",
        "x-vibecad-reference": True,
        "properties": {
            "document_uid": {"type": "string", "minLength": 1},
            "object_name": {"type": "string", "minLength": 1},
            "document_path": {"type": "string", "minLength": 1},
        },
        "required": ["document_uid", "object_name"],
        "additionalProperties": False,
    }
    geometry_vector = {
        "type": "array",
        "minItems": 3,
        "maxItems": 3,
        "items": {"type": "number"},
    }
    geometry_query = {
        "type": "object",
        "properties": {
            "name": _property_schema(
                "Unique query result name.",
                type="string",
                minLength=1,
                maxLength=64,
            ),
            "element_type": {
                "description": "Topology kind.",
                "type": "string",
                "enum": ["face", "edge"],
            },
            "geometry_type": _property_schema(
                "OCC type, such as Plane, Cylinder, Circle, or Line.",
                type="string",
                minLength=1,
            ),
            "normal": {
                "description": "Face normal [x,y,z].",
                **geometry_vector,
            },
            "direction": {
                "description": "Edge tangent [x,y,z].",
                **geometry_vector,
            },
            "axis_direction": {
                "description": "Analytic axis [x,y,z].",
                **geometry_vector,
            },
            "radius_mm": _property_schema(
                "Analytic radius in mm.",
                type="number",
                minimum=0,
            ),
            "radius_tolerance_mm": _property_schema(
                "Radius tolerance in mm.",
                type="number",
                minimum=0,
            ),
            "min_area_mm2": _property_schema(
                "Minimum face area.",
                type="number",
                minimum=0,
            ),
            "max_area_mm2": _property_schema(
                "Maximum face area.",
                type="number",
                minimum=0,
            ),
            "min_length_mm": _property_schema(
                "Minimum edge length.",
                type="number",
                minimum=0,
            ),
            "max_length_mm": _property_schema(
                "Maximum edge length.",
                type="number",
                minimum=0,
            ),
            "near_point_mm": {
                "description": "Nearby center [x,y,z] in mm.",
                **geometry_vector,
            },
            "max_distance_mm": _property_schema(
                "Maximum distance from near_point_mm.",
                type="number",
                minimum=0,
            ),
            "angle_tolerance_degrees": _property_schema(
                "Direction tolerance in degrees.",
                type="number",
                minimum=0,
                maximum=180,
            ),
            "expected_count": _property_schema(
                "Required match count.",
                type="integer",
                minimum=1,
                maximum=256,
            ),
            "max_results": _property_schema(
                "Maximum records returned.",
                type="integer",
                minimum=1,
                maximum=16,
            ),
        },
        "required": ["name", "element_type"],
        "additionalProperties": False,
    }
    return (
        assembly_program_tool_spec(),
        {
            "name": "vibescript.read_source",
            "description": (
                "List programs when program is omitted, or read one source and state. "
                "Use line bounds for a slice and include_logs only for diagnostics."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "program": program,
                    "line_start": _property_schema(
                        "First source line (1-based).",
                        type="integer",
                        minimum=1,
                    ),
                    "line_end": _property_schema(
                        "Last source line (inclusive).",
                        type="integer",
                        minimum=1,
                    ),
                    "include_logs": _property_schema(
                        "Include raw build logs.",
                        type="boolean",
                        default=False,
                    ),
                    "log_tail_lines": _property_schema(
                        "Final lines per included log.",
                        type="integer",
                        minimum=1,
                        maximum=1000,
                    ),
                },
                "required": [],
                "additionalProperties": False,
            },
            "safety": "READ",
            "contextual": True,
            "requires_document": True,
            "edit_modes": ["none", "sketch"],
        },
        {
            "name": "vibescript.read_operation",
            "description": (
                "Wait for or inspect a background VibeScript mutation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "operation_id": _property_schema(
                        "Operation id returned by a mutation.",
                        type="string",
                        pattern="^operation-[1-9][0-9]*$",
                    ),
                    "wait_seconds": _property_schema(
                        "Maximum wait; omit for 30 seconds, zero polls immediately.",
                        type="number",
                        minimum=0,
                        maximum=60,
                    ),
                },
                "required": ["operation_id"],
                "additionalProperties": False,
            },
            "safety": "READ",
            "contextual": True,
            "requires_document": False,
            "edit_modes": ["none", "sketch"],
        },
        {
            "name": "vibescript.read_api",
            "description": (
                "Return exact api signatures. Supply planned callable names from "
                "api_groups. Use domain for a new source or program for an existing source."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "program": program,
                    "domain": domain,
                    "names": _property_schema(
                        "Exact api callable names needed by the planned source.",
                        type="array",
                        maxItems=128,
                        uniqueItems=True,
                        items={
                            "type": "string",
                            "pattern": "^(?:api\\.)?[A-Za-z_][A-Za-z0-9_]*$",
                        },
                    ),
                    "groups": _property_schema(
                        "Discovery only when no callable listed in api_groups fits.",
                        type="array",
                        maxItems=32,
                        uniqueItems=True,
                        items={"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
                    ),
                },
                "required": [],
                "additionalProperties": False,
            },
            "safety": "READ",
            "contextual": True,
            "requires_document": False,
            "edit_modes": ["none", "sketch"],
        },
        {
            "name": "vibescript.read_geometry",
            "description": (
                "Inspect an exact native/imported B-rep. topology returns bounds, counts, "
                "and query matches; full adds validity and mass properties. Matches include "
                "copy-ready api.subshape selectors. Units are mm/degrees."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reference": geometry_reference,
                    "analysis_level": {
                        "description": "Inspection depth; default full.",
                        "type": "string",
                        "enum": ["topology", "full"],
                    },
                    "include_subelements": _property_schema(
                        "Include bounded 1-based face/edge facts.",
                        type="boolean",
                    ),
                    "max_subelements": _property_schema(
                        "Returned faces and edges; omit for 32, maximum 32.",
                        type="integer",
                        minimum=1,
                        maximum=32,
                    ),
                    "queries": {
                        "description": "Exact face/edge searches.",
                        "type": "array",
                        "maxItems": 16,
                        "uniqueItems": True,
                        "items": geometry_query,
                    },
                },
                "required": ["reference"],
                "additionalProperties": False,
            },
            "safety": "READ",
            "contextual": True,
            "requires_document": True,
            "edit_modes": ["none", "sketch"],
        },
        {
            "name": "vibescript.read_placement",
            "description": (
                "Resolve axes and transform for api.sketch/box/wedge before using an "
                "unfamiliar plane or orientation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": _property_schema(
                        "Operation being planned.",
                        type="string",
                        enum=["sketch", "box", "wedge"],
                    ),
                    "plane": _property_schema(
                        "Principal sketch plane; default XY.",
                        type="string",
                        enum=["XY", "XZ", "YZ"],
                    ),
                    "plane_offset_mm": _property_schema(
                        "Offset along plane normal.",
                        type="number",
                    ),
                    "placement": _property_schema(
                        "Explicit sketch placement; excludes plane_offset_mm.",
                        type="object",
                        properties={
                            "origin": {
                                "type": "array",
                                "minItems": 3,
                                "maxItems": 3,
                                "items": {"type": "number"},
                            },
                            "normal": {
                                "type": "array",
                                "minItems": 3,
                                "maxItems": 3,
                                "items": {"type": "number"},
                            },
                            "x_direction": {
                                "type": "array",
                                "minItems": 3,
                                "maxItems": 3,
                                "items": {"type": "number"},
                            },
                        },
                        required=["origin", "normal", "x_direction"],
                        additionalProperties=False,
                    ),
                    "origin": _property_schema(
                        "Primitive origin [x,y,z].",
                        type="array",
                        minItems=3,
                        maxItems=3,
                        items={"type": "number"},
                    ),
                    "direction": _property_schema(
                        "Primitive local +Z direction.",
                        type="array",
                        minItems=3,
                        maxItems=3,
                        items={"type": "number"},
                    ),
                    "x_direction": _property_schema(
                        "Primitive local +X; sets roll and cannot be parallel to direction.",
                        type="array",
                        minItems=3,
                        maxItems=3,
                        items={"type": "number"},
                    ),
                },
                "required": ["operation"],
                "additionalProperties": False,
            },
            "safety": "READ",
            "contextual": True,
            "requires_document": False,
            "edit_modes": ["none", "sketch"],
        },
        {
            "name": "vibescript.create_part",
            "description": "Author reusable Part Design geometry from source.",
            "parameters": {
                "type": "object",
                "properties": {
                    "program_name": program_name,
                    "source": source,
                    "input_schema": input_schema,
                    "inputs": inputs,
                    "expected_outputs": component_outputs,
                },
                "required": [
                    "program_name",
                    "source",
                    "input_schema",
                    "inputs",
                    "expected_outputs",
                ],
                "additionalProperties": False,
            },
            "safety": "SAFE_WRITE",
            "contextual": True,
            "requires_document": True,
            "edit_modes": ["none"],
        },
        {
            "name": "vibescript.create_program",
            "description": (
                "Create and publish one complete program after reading its api signatures. "
                "program_name is a label. Poll the returned operation_id with read_operation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": domain,
                    "program_name": program_name,
                    "source": source,
                    "input_schema": input_schema,
                    "inputs": inputs,
                    "expected_outputs": outputs,
                },
                "required": [
                    "program_name",
                    "source",
                    "input_schema",
                    "inputs",
                    "expected_outputs",
                ],
                "additionalProperties": False,
            },
            "safety": "SAFE_WRITE",
            "contextual": True,
            "requires_document": True,
            "edit_modes": ["none"],
        },
        {
            "name": "vibescript.build_program",
            "description": (
                "Build saved source unchanged, then read_operation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "program": program,
                    "expected_revision": revision,
                },
                "required": ["program", "expected_revision"],
                "additionalProperties": False,
            },
            "safety": "SAFE_WRITE",
            "contextual": True,
            "requires_document": True,
            "edit_modes": ["none"],
        },
        {
            "name": "vibescript.edit_source",
            "description": (
                "Replace an existing program's complete source, build it, then "
                "read_operation. Omitted inputs/schema/outputs remain unchanged."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "program": program,
                    "expected_revision": revision,
                    "source": source,
                    "input_schema": input_schema,
                    "inputs": inputs,
                    "expected_outputs": outputs,
                },
                "required": ["program", "expected_revision", "source"],
                "additionalProperties": False,
            },
            "safety": "SAFE_WRITE",
            "contextual": True,
            "requires_document": True,
            "edit_modes": ["none"],
        },
        {
            "name": "vibescript.set_inputs",
            "description": (
                "Patch input values without changing source or outputs, rebuild, then "
                "read_operation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "program": program,
                    "expected_revision": revision,
                    "patch": _property_schema(
                        "RFC 7396 merge patch; null removes an optional input.",
                        type="object",
                        minProperties=1,
                    ),
                },
                "required": ["program", "expected_revision", "patch"],
                "additionalProperties": False,
            },
            "safety": "SAFE_WRITE",
            "contextual": True,
            "requires_document": True,
            "edit_modes": ["none"],
        },
        {
            "name": "vibescript.reconfigure_program",
            "description": (
                "Replace source, schema, inputs, and outputs together, then "
                "read_operation. Prefer edit_source for new calls."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "program": program,
                    "expected_revision": revision,
                    "source": source,
                    "input_schema": input_schema,
                    "inputs": inputs,
                    "expected_outputs": outputs,
                },
                "required": [
                    "program",
                    "expected_revision",
                    "source",
                    "input_schema",
                    "inputs",
                    "expected_outputs",
                ],
                "additionalProperties": False,
            },
            "safety": "SAFE_WRITE",
            "contextual": True,
            "requires_document": True,
            "edit_modes": ["none"],
        },
        {
            "name": "vibescript.delete_output",
            "description": (
                "Delete one output while retaining its program and other outputs. Send "
                "complete source without that result key, then read_operation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "program": program,
                    "expected_revision": revision,
                    "output_name": _property_schema(
                        "Exact expected output name.",
                        type="string",
                        pattern=_IDENTIFIER.pattern,
                    ),
                    "source": source,
                    "reason": _property_schema(
                        "Reason for deletion.",
                        type="string",
                        minLength=1,
                        maxLength=500,
                    ),
                },
                "required": [
                    "program",
                    "expected_revision",
                    "output_name",
                    "source",
                    "reason",
                ],
                "additionalProperties": False,
            },
            "safety": "SAFE_WRITE",
            "contextual": True,
            "requires_document": True,
            "edit_modes": ["none"],
        },
        {
            "name": "vibescript.delete_program",
            "description": (
                "Delete one source and all owned outputs using its "
                "delete_target_arguments, then read_operation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "program": program,
                    "expected_revision": revision,
                    "reason": _property_schema(
                        "Reason for deletion.",
                        type="string",
                        minLength=1,
                        maxLength=500,
                    ),
                },
                "required": ["program", "expected_revision", "reason"],
                "additionalProperties": False,
            },
            "safety": "SAFE_WRITE",
            "contextual": True,
            "requires_document": True,
            "edit_modes": ["none"],
        },
        {
            "name": "vibescript.delete_object",
            "description": (
                "Delete one unowned/imported object and children. Managed outputs "
                "require delete_output or delete_program."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reference": geometry_reference,
                    "reason": _property_schema(
                        "Reason for deletion.",
                        type="string",
                        minLength=1,
                        maxLength=500,
                    ),
                },
                "required": ["reference", "reason"],
                "additionalProperties": False,
            },
            "safety": "SAFE_WRITE",
            "contextual": True,
            "requires_document": True,
            "edit_modes": ["none"],
        },
    )


def domain_tool_specs(pack: VibeScriptWorkbenchPack) -> tuple[dict[str, Any], ...]:
    program_id = _property_schema(
        "Stable program id returned by create_program or another domain write.",
        type="string",
        pattern="^[0-9a-f]{32}$",
    )
    revision = _property_schema(
        "Current working revision returned by the latest write or inspection.",
        type="string",
        pattern="^[0-9a-f]{64}$",
    )
    source = _property_schema(
        (
            f"{pack.title} Python using api, inputs, and doc. Define main(). Return the "
            "final value for one output or an ordered mapping matching multiple "
            "expected_outputs."
        ),
        type="string",
        minLength=1,
        maxLength=MAX_SOURCE_BYTES,
    )
    input_schema = _property_schema(
        "Complete bounded JSON Schema for this program's named inputs.",
        type="object",
    )
    inputs = _property_schema(
        "Values for input_schema; component inputs use a listed catalog_key.",
        type="object",
    )
    outputs = _property_schema(
        f"{pack.title} deliverables returned by main().",
        type="array",
        minItems=1,
        maxItems=MAX_OUTPUTS,
        items={
            "type": "object",
            "properties": {
                "name": {"type": "string", "pattern": _IDENTIFIER.pattern},
                "type": {"type": "string", "enum": list(pack.output_types)},
            },
            "required": ["name", "type"],
            "additionalProperties": False,
        },
    )
    return (
        _base_tool_spec(
            pack,
            "create_program",
            description=(
                f"Create and publish a new {pack.title} program. Use only when no "
                "existing program owns the requested output; submit one complete final "
                "program, never staged intermediate geometry."
            ),
            properties={
                "program_name": _property_schema(
                    "Human-readable stable label, never an attachment filename or path.",
                    type="string",
                    minLength=1,
                    maxLength=120,
                    pattern=_PROGRAM_NAME_PATTERN,
                ),
                "source": source,
                "input_schema": input_schema,
                "inputs": inputs,
                "expected_outputs": outputs,
            },
            required=(
                "program_name",
                "source",
                "input_schema",
                "inputs",
                "expected_outputs",
            ),
            safety="SAFE_WRITE",
        ),
        _base_tool_spec(
            pack,
            "set_inputs",
            description=(
                f"Change only input values of an existing {pack.title} program, then "
                "regenerate and publish it. Source and output declarations stay unchanged."
            ),
            properties={
                "program_id": program_id,
                "expected_revision": revision,
                "patch": _property_schema(
                    "RFC 7396 input merge patch; null removes an optional input.",
                    type="object",
                    minProperties=1,
                ),
            },
            required=("program_id", "expected_revision", "patch"),
            safety="SAFE_WRITE",
        ),
        _base_tool_spec(
            pack,
            "reconfigure_program",
            description=(
                f"Compatibility alias for editing a {pack.title} program. New callers "
                "should use vibescript.edit_source."
            ),
            properties={
                "program_id": program_id,
                "expected_revision": revision,
                "source": source,
                "input_schema": input_schema,
                "inputs": inputs,
                "expected_outputs": outputs,
            },
            required=(
                "program_id",
                "expected_revision",
                "source",
                "input_schema",
                "inputs",
                "expected_outputs",
            ),
            safety="SAFE_WRITE",
        ),
        _base_tool_spec(
            pack,
            "delete_program",
            description=(
                f"Delete one guarded {pack.title} VibeScript program, its stable "
                "live outputs, and its persisted artifacts."
            ),
            properties={
                "program_id": program_id,
                "expected_revision": revision,
                "reason": _property_schema(
                    "Why this program and its outputs should be removed.",
                    type="string",
                    minLength=1,
                    maxLength=500,
                ),
            },
            required=("program_id", "expected_revision", "reason"),
            safety="SAFE_WRITE",
        ),
    )


def register_domain_tools(registry: Any, service: Any) -> None:
    """Register only packs backed by a complete adapter."""

    if any(
        domain_availability(pack.workbench)[0]
        for pack in VIBESCRIPT_WORKBENCH_PACKS.values()
    ):
        for raw_spec in universal_tool_specs():
            registry.register_spec(raw_spec, None)
    for pack in VIBESCRIPT_WORKBENCH_PACKS.values():
        available, _reason = domain_availability(pack.workbench)
        if not available:
            continue
        for raw_spec in domain_tool_specs(pack):
            registry.register_spec(raw_spec, None)
