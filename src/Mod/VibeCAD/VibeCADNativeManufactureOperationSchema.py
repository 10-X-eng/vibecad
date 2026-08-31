# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contracts for CAM machining operations."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeManufactureArraySchema import ARRAY_PARAMETERS_SCHEMA
from VibeCADNativeManufactureContract import (
    PATH_OPERATION_LABEL_SCHEMA as LABEL_SCHEMA,
)


MANUFACTURE_OPERATION_CAPABILITY_NAME = "manufacture.operation"
_OBJECT_NAME = {
    "type": "string",
    "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
    "maxLength": 128,
}
_SHA256 = {
    "type": "string",
    "pattern": r"^[0-9a-f]{64}$",
    "minLength": 64,
    "maxLength": 64,
}
_DISTANCE_MM = {
    "type": "number",
    "minimum": -1_000_000.0,
    "maximum": 1_000_000.0,
}
_NONNEGATIVE_DISTANCE_MM = {
    "type": "number",
    "minimum": 0.0,
    "maximum": 1_000_000.0,
}
_POSITIVE_DISTANCE_MM = {
    "type": "number",
    "exclusiveMinimum": 0.0,
    "maximum": 1_000_000.0,
}
_COOLANT_SCHEMA = {
    "type": "string",
    "enum": ["none", "flood", "mist"],
}
def _closed(properties: dict, required: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_POINT_MM = _closed(
    {
        "x_mm": _DISTANCE_MM,
        "y_mm": _DISTANCE_MM,
        "z_mm": _DISTANCE_MM,
    },
    ("x_mm", "y_mm", "z_mm"),
)
_PLANAR_POINT_MM = _closed(
    {
        "x_mm": _DISTANCE_MM,
        "y_mm": _DISTANCE_MM,
    },
    ("x_mm", "y_mm"),
)


_EXACT_TARGET = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _SHA256,
    },
    ("object_name", "expected_state_sha256"),
)
_SUBELEMENTS = {
    "type": "array",
    "items": {
        "type": "string",
        "pattern": r"^(?:Face|Edge)[1-9][0-9]*$",
        "maxLength": 32,
    },
    "minItems": 1,
    "maxItems": 64,
    "uniqueItems": True,
}
_GEOMETRY = {
    "oneOf": [
        _closed(
            {
                "kind": {
                    "type": "string",
                    "const": "entire_job",
                    "description": "Profile every model in the exact Job.",
                }
            },
            ("kind",),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "subelements"},
                "items": {
                    "type": "array",
                    "items": _closed(
                        {
                            "model": _EXACT_TARGET,
                            "subelements": _SUBELEMENTS,
                        },
                        ("model", "subelements"),
                    ),
                    "minItems": 1,
                    "maxItems": 32,
                },
            },
            ("kind", "items"),
        ),
    ]
}
_POCKET_GEOMETRY = _closed(
    {
        "kind": {"type": "string", "const": "subelements"},
        "items": {
            "type": "array",
            "items": _closed(
                {
                    "model": _EXACT_TARGET,
                    "subelements": _SUBELEMENTS,
                },
                ("model", "subelements"),
            ),
            "minItems": 1,
            "maxItems": 32,
        },
    },
    ("kind", "items"),
)
_FEATURE_SELECTION = {
    "type": "array",
    "items": _closed(
        {
            "model": _EXACT_TARGET,
            "subelements": _SUBELEMENTS,
        },
        ("model", "subelements"),
    ),
    "minItems": 1,
    "maxItems": 32,
    "description": "Exact model Faces or Edges to machine.",
}
_PROFILE_SETTINGS = _closed(
    {
        "direction": {
            "type": "string",
            "enum": ["clockwise", "counterclockwise"],
        },
        "cut_side": {"type": "string", "enum": ["outside", "inside"]},
        "cutter_compensation": {"type": "boolean"},
        "extra_offset_mm": _DISTANCE_MM,
        "pass_count": {"type": "integer", "minimum": 1, "maximum": 99999},
        "stepover_mm": _NONNEGATIVE_DISTANCE_MM,
        "multiple_features": {
            "type": "string",
            "enum": ["collectively", "individually"],
        },
        "sorting": {"type": "string", "enum": ["automatic", "manual"]},
        "start_on_longest_edge": {"type": "boolean"},
        "profile_outer_perimeter": {"type": "boolean"},
        "profile_noncircular_holes": {"type": "boolean"},
        "profile_circular_holes": {"type": "boolean"},
    },
    (
        "direction",
        "cut_side",
        "cutter_compensation",
        "extra_offset_mm",
        "pass_count",
        "stepover_mm",
        "multiple_features",
        "sorting",
        "start_on_longest_edge",
        "profile_outer_perimeter",
        "profile_noncircular_holes",
        "profile_circular_holes",
    ),
)
_DEPTHS = _closed(
    {
        "start_depth_mm": _DISTANCE_MM,
        "final_depth_mm": _DISTANCE_MM,
        "step_down_mm": _POSITIVE_DISTANCE_MM,
    },
    ("start_depth_mm", "final_depth_mm", "step_down_mm"),
)
_HEIGHTS = _closed(
    {
        "safe_height_mm": _DISTANCE_MM,
        "clearance_height_mm": _DISTANCE_MM,
    },
    ("safe_height_mm", "clearance_height_mm"),
)
_POCKET_PATTERN = {
    "oneOf": [
        _closed(
            {"kind": {"type": "string", "const": "offset"}},
            ("kind",),
        ),
        _closed(
            {
                "kind": {
                    "type": "string",
                    "enum": ["zigzag", "zigzag_offset", "line", "grid"],
                },
                "angle_degrees": {
                    "type": "number",
                    "minimum": -360_000.0,
                    "maximum": 360_000.0,
                },
            },
            ("kind", "angle_degrees"),
        ),
    ]
}
_POCKET_SETTINGS = _closed(
    {
        "cut_mode": {
            "type": "string",
            "enum": ["climb", "conventional"],
        },
        "pattern": _POCKET_PATTERN,
        "stepover_percent": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
        },
        "material_allowance_mm": _DISTANCE_MM,
        "ignore_holes": {"type": "boolean"},
        "minimize_travel": {"type": "boolean"},
        "rest_machining": {"type": "boolean"},
    },
    (
        "cut_mode",
        "pattern",
        "stepover_percent",
        "material_allowance_mm",
        "ignore_holes",
        "minimize_travel",
        "rest_machining",
    ),
)
_POCKET_DEPTHS = _closed(
    {
        "start_depth_mm": _DISTANCE_MM,
        "final_depth_mm": _DISTANCE_MM,
        "step_down_mm": _POSITIVE_DISTANCE_MM,
        "finish_step_mm": _NONNEGATIVE_DISTANCE_MM,
    },
    (
        "start_depth_mm",
        "final_depth_mm",
        "step_down_mm",
        "finish_step_mm",
    ),
)
_POCKET_3D_START = {
    "oneOf": [
        _closed(
            {"kind": {"type": "string", "const": "automatic"}},
            ("kind",),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "point"},
                "point_mm": _POINT_MM,
                "minimize_travel": {"type": "boolean"},
            },
            ("kind", "point_mm", "minimize_travel"),
        ),
    ]
}
_POCKET_3D_SETTINGS = _closed(
    {
        "cut_mode": {
            "type": "string",
            "enum": ["climb", "conventional"],
        },
        "pattern": _POCKET_PATTERN,
        "stepover_percent": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
        },
        "pass_extension_mm": _DISTANCE_MM,
        "rest_machining": {"type": "boolean"},
        "start": _POCKET_3D_START,
    },
    (
        "cut_mode",
        "pattern",
        "stepover_percent",
        "pass_extension_mm",
        "rest_machining",
        "start",
    ),
)
_POCKET_3D_DEPTHS = _closed(
    {
        "start_depth_mm": _DISTANCE_MM,
        "step_down_mm": _POSITIVE_DISTANCE_MM,
        "finish_step_mm": _NONNEGATIVE_DISTANCE_MM,
    },
    ("start_depth_mm", "step_down_mm", "finish_step_mm"),
)
_FACE_SUBELEMENTS = {
    "type": "array",
    "items": {
        "type": "string",
        "pattern": r"^Face[1-9][0-9]*$",
        "maxLength": 32,
    },
    "minItems": 1,
    "maxItems": 64,
    "uniqueItems": True,
}
_SURFACE_GEOMETRY = {
    "oneOf": [
        _closed(
            {
                "kind": {
                    "type": "string",
                    "const": "entire_job",
                    "description": "Machine every model in the exact Job.",
                }
            },
            ("kind",),
        ),
        _closed(
            {
                "kind": {
                    "type": "string",
                    "const": "faces",
                },
                "items": {
                    "type": "array",
                    "items": _closed(
                        {
                            "model": _EXACT_TARGET,
                            "faces": _FACE_SUBELEMENTS,
                        },
                        ("model", "faces"),
                    ),
                    "minItems": 1,
                    "maxItems": 32,
                },
                "avoid_last_face_count": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 63,
                    "description": "Trailing ordered Faces treated as avoidance regions.",
                },
                "avoid_internal_features": {"type": "boolean"},
            },
            (
                "kind",
                "items",
                "avoid_last_face_count",
                "avoid_internal_features",
            ),
        ),
    ]
}
_WATERLINE_GEOMETRY = {
    "oneOf": [
        _closed(
            {
                "kind": {
                    "type": "string",
                    "const": "entire_job",
                    "description": "Machine every model in the exact Job.",
                }
            },
            ("kind",),
        ),
        _closed(
            {
                "kind": {
                    "type": "string",
                    "const": "faces",
                    "description": "Machine ordered exact Faces with adaptive Waterline.",
                },
                "items": {
                    "type": "array",
                    "items": _closed(
                        {
                            "model": _EXACT_TARGET,
                            "faces": _FACE_SUBELEMENTS,
                        },
                        ("model", "faces"),
                    ),
                    "minItems": 1,
                    "maxItems": 32,
                },
                "avoid_last_face_count": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 63,
                    "description": "Trailing ordered Faces treated as avoidance regions.",
                },
                "avoid_internal_features": {"type": "boolean"},
            },
            (
                "kind",
                "items",
                "avoid_last_face_count",
                "avoid_internal_features",
            ),
        ),
    ]
}
_POINT_XY_MM = _closed(
    {"x_mm": _DISTANCE_MM, "y_mm": _DISTANCE_MM},
    ("x_mm", "y_mm"),
)
_SURFACE_PATTERN_CENTER = {
    "oneOf": [
        _closed(
            {
                "kind": {
                    "type": "string",
                    "enum": [
                        "center_of_mass",
                        "bounding_box_center",
                        "minimum_xy",
                    ],
                }
            },
            ("kind",),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "point"},
                "point_mm": _POINT_XY_MM,
            },
            ("kind", "point_mm"),
        ),
    ]
}
_SURFACE_PATTERN = {
    "oneOf": [
        _closed(
            {
                "kind": {"type": "string", "enum": ["line", "zigzag"]},
                "angle_degrees": {
                    "type": "number",
                    "minimum": -360.0,
                    "exclusiveMaximum": 360.0,
                },
            },
            ("kind", "angle_degrees"),
        ),
        _closed(
            {"kind": {"type": "string", "const": "offset"}},
            ("kind",),
        ),
        _closed(
            {
                "kind": {
                    "type": "string",
                    "enum": ["circular", "circular_zigzag"],
                },
                "center": _SURFACE_PATTERN_CENTER,
                "emit_arcs": {"type": "boolean"},
            },
            ("kind", "center", "emit_arcs"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "spiral"},
                "center": _SURFACE_PATTERN_CENTER,
            },
            ("kind", "center"),
        ),
    ]
}
_SURFACE_LAYERS = {
    "oneOf": [
        _closed(
            {"kind": {"type": "string", "const": "single_pass"}},
            ("kind",),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "multi_pass"},
                "step_down_mm": _POSITIVE_DISTANCE_MM,
            },
            ("kind", "step_down_mm"),
        ),
    ]
}
_SURFACE_START = {
    "oneOf": [
        _closed(
            {"kind": {"type": "string", "const": "automatic"}},
            ("kind",),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "point"},
                "point_mm": _POINT_MM,
            },
            ("kind", "point_mm"),
        ),
    ]
}
_SURFACE_SETTINGS = _closed(
    {
        "bounds": {"type": "string", "enum": ["model", "stock"]},
        "cut_mode": {
            "type": "string",
            "enum": ["climb", "conventional"],
        },
        "pattern": _SURFACE_PATTERN,
        "layers": _SURFACE_LAYERS,
        "stepover_percent": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
        },
        "depth_offset_mm": _DISTANCE_MM,
        "sample_interval_mm": {
            "type": "number",
            "minimum": 0.001,
            "maximum": 25.4,
        },
        "profile_edges": {
            "type": "string",
            "enum": ["none", "only", "first", "last"],
        },
        "boundary": _closed(
            {
                "enforce": {"type": "boolean"},
                "adjustment_mm": _DISTANCE_MM,
            },
            ("enforce", "adjustment_mm"),
        ),
        "internal_features": _closed(
            {
                "cut": {"type": "boolean"},
                "adjustment_mm": _DISTANCE_MM,
            },
            ("cut", "adjustment_mm"),
        ),
        "multiple_features": {
            "type": "string",
            "enum": ["collectively", "individually"],
        },
        "reverse_pass_order": {"type": "boolean"},
        "optimization": _closed(
            {
                "linear_paths": {"type": "boolean"},
                "stepover_transitions": {"type": "boolean"},
                "gap_threshold_mm": _NONNEGATIVE_DISTANCE_MM,
            },
            ("linear_paths", "stepover_transitions", "gap_threshold_mm"),
        ),
        "start": _SURFACE_START,
        "mesh_deflection_mm": {
            "type": "number",
            "minimum": 0.001,
            "maximum": 25.4,
        },
    },
    (
        "bounds",
        "cut_mode",
        "pattern",
        "layers",
        "stepover_percent",
        "depth_offset_mm",
        "sample_interval_mm",
        "profile_edges",
        "boundary",
        "internal_features",
        "multiple_features",
        "reverse_pass_order",
        "optimization",
        "start",
        "mesh_deflection_mm",
    ),
)
_SURFACE_DEPTHS = _closed(
    {
        "start_depth_mm": _DISTANCE_MM,
        "final_depth_mm": _DISTANCE_MM,
    },
    ("start_depth_mm", "final_depth_mm"),
)
_WATERLINE_CLEAR_PATTERN = {
    "oneOf": [
        _closed(
            {"kind": {"type": "string", "const": "offset"}},
            ("kind",),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "line"},
                "angle_degrees": {
                    "type": "number",
                    "minimum": -360.0,
                    "exclusiveMaximum": 360.0,
                },
            },
            ("kind", "angle_degrees"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "zigzag"},
                "angle_degrees": {
                    "type": "number",
                    "minimum": -360.0,
                    "exclusiveMaximum": 360.0,
                },
            },
            ("kind", "angle_degrees"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "circular"},
                "center": _SURFACE_PATTERN_CENTER,
            },
            ("kind", "center"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "circular_zigzag"},
                "center": _SURFACE_PATTERN_CENTER,
            },
            ("kind", "center"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "spiral"},
                "center": _SURFACE_PATTERN_CENTER,
            },
            ("kind", "center"),
        ),
    ]
}
_WATERLINE_CLEARING = {
    "oneOf": [
        _closed(
            {
                "kind": {
                    "type": "string",
                    "const": "waterline_only",
                    "description": "Cut constant-Z boundary contours.",
                }
            },
            ("kind",),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "every_layer"},
                "pattern": _WATERLINE_CLEAR_PATTERN,
                "stepover_percent": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                },
            },
            ("kind", "pattern", "stepover_percent"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "final_layer"},
                "pattern": _WATERLINE_CLEAR_PATTERN,
                "stepover_percent": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                },
            },
            ("kind", "pattern", "stepover_percent"),
        ),
    ]
}
_WATERLINE_ALGORITHM = {
    "oneOf": [
        _closed(
            {
                "kind": {"type": "string", "const": "drop_cutter"},
                "bounds": {"type": "string", "enum": ["model", "stock"]},
                "sample_interval_mm": {
                    "type": "number",
                    "minimum": 0.001,
                    "maximum": 25.4,
                },
                "mesh_deflection_mm": {
                    "type": "number",
                    "minimum": 0.001,
                    "maximum": 25.4,
                },
            },
            ("kind", "bounds", "sample_interval_mm", "mesh_deflection_mm"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "adaptive"},
                "sample_interval_mm": {
                    "type": "number",
                    "minimum": 0.001,
                    "maximum": 25.4,
                },
                "minimum_sample_interval_mm": {
                    "type": "number",
                    "minimum": 0.001,
                    "maximum": 25.4,
                },
                "optimize_linear_paths": {"type": "boolean"},
                "mesh_deflection_mm": {
                    "type": "number",
                    "minimum": 0.001,
                    "maximum": 25.4,
                },
            },
            (
                "kind",
                "sample_interval_mm",
                "minimum_sample_interval_mm",
                "optimize_linear_paths",
                "mesh_deflection_mm",
            ),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "experimental"},
                "bounds": {"type": "string", "enum": ["model", "stock"]},
                "clearing": _WATERLINE_CLEARING,
                "boundary_adjustment_mm": _DISTANCE_MM,
                "ignore_outer_above_mm": _DISTANCE_MM,
            },
            (
                "kind",
                "bounds",
                "clearing",
                "boundary_adjustment_mm",
                "ignore_outer_above_mm",
            ),
        ),
    ]
}
_WATERLINE_SETTINGS = _closed(
    {
        "algorithm": _WATERLINE_ALGORITHM,
        "cut_mode": {"type": "string", "enum": ["climb", "conventional"]},
        "layers": _SURFACE_LAYERS,
        "depth_offset_mm": _DISTANCE_MM,
        "geometry_handling": _closed(
            {
                "boundary_enforcement": {"type": "boolean"},
                "internal_features": _closed(
                    {
                        "cut": {"type": "boolean"},
                        "adjustment_mm": _DISTANCE_MM,
                    },
                    ("cut", "adjustment_mm"),
                ),
                "multiple_features": {
                    "type": "string",
                    "enum": ["collectively", "individually"],
                },
            },
            ("boundary_enforcement", "internal_features", "multiple_features"),
        ),
        "reverse_pass_order": {"type": "boolean"},
        "optimization": _closed(
            {
                "stepover_transitions": {"type": "boolean"},
                "gap_threshold_mm": _NONNEGATIVE_DISTANCE_MM,
            },
            ("stepover_transitions", "gap_threshold_mm"),
        ),
        "start": _SURFACE_START,
    },
    (
        "algorithm",
        "cut_mode",
        "layers",
        "depth_offset_mm",
        "geometry_handling",
        "reverse_pass_order",
        "optimization",
        "start",
    ),
)
_ROTARY_SURFACE_GEOMETRY = {
    "oneOf": [
        _closed(
            {
                "kind": {
                    "type": "string",
                    "const": "entire_job",
                    "description": "Machine the exact CAM Job model.",
                }
            },
            ("kind",),
        ),
        _closed(
            {
                "kind": {
                    "type": "string",
                    "const": "faces",
                    "description": (
                        "Restrict a climb-cut parallel or rings path to the projected "
                        "axial and angular bounds of ordered exact current Faces."
                    ),
                },
                "items": {
                    "type": "array",
                    "items": _closed(
                        {
                            "model": _EXACT_TARGET,
                            "faces": _FACE_SUBELEMENTS,
                        },
                        ("model", "faces"),
                    ),
                    "minItems": 1,
                    "maxItems": 32,
                },
            },
            ("kind", "items"),
        ),
    ]
}
_ROTARY_SURFACE_PATTERN = {
    "oneOf": [
        _closed(
            {
                "kind": {"type": "string", "const": "spiral"},
                "axial_pitch_mm": {
                    "type": "number",
                    "minimum": 0.001,
                    "maximum": 1_000_000.0,
                    "description": "Axial advance per complete rotary revolution.",
                },
                "start_angle_degrees": {
                    "type": "number",
                    "minimum": 0.0,
                    "exclusiveMaximum": 360.0,
                },
            },
            ("kind", "axial_pitch_mm", "start_angle_degrees"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "parallel"},
                "surface_stepover_mm": {
                    "type": "number",
                    "minimum": 0.001,
                    "maximum": 1_000_000.0,
                    "description": (
                        "Target circumferential surface spacing between axial passes."
                    ),
                },
                "start_angle_degrees": {
                    "type": "number",
                    "minimum": -360.0,
                    "maximum": 360.0,
                },
                "sweep_degrees": {
                    "type": "number",
                    "exclusiveMinimum": 0.0,
                    "maximum": 360.0,
                    "description": (
                        "Positive sweep magnitude. Climb advances from the start angle; "
                        "conventional reverses from it."
                    ),
                },
            },
            (
                "kind",
                "surface_stepover_mm",
                "start_angle_degrees",
                "sweep_degrees",
            ),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "rings"},
                "axial_spacing_mm": {
                    "type": "number",
                    "minimum": 0.001,
                    "maximum": 1_000_000.0,
                    "description": "Axial spacing between circumferential rings.",
                },
                "start_angle_degrees": {
                    "type": "number",
                    "minimum": -360.0,
                    "maximum": 360.0,
                },
                "sweep_degrees": {
                    "type": "number",
                    "exclusiveMinimum": 0.0,
                    "maximum": 360.0,
                    "description": (
                        "Positive sweep magnitude per ring. Climb advances from the "
                        "start angle; conventional reverses from it."
                    ),
                },
            },
            (
                "kind",
                "axial_spacing_mm",
                "start_angle_degrees",
                "sweep_degrees",
            ),
        ),
    ]
}
_ROTARY_SURFACE_AXIAL_WINDOW = {
    "oneOf": [
        _closed(
            {"kind": {"type": "string", "const": "stock"}},
            ("kind",),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "explicit"},
                "start_mm": _DISTANCE_MM,
                "stop_mm": _DISTANCE_MM,
            },
            ("kind", "start_mm", "stop_mm"),
        ),
    ]
}
_ROTARY_SURFACE_SETTINGS = _closed(
    {
        "pattern": _ROTARY_SURFACE_PATTERN,
        "cut_mode": {"type": "string", "enum": ["climb", "conventional"]},
        "axial_window": _ROTARY_SURFACE_AXIAL_WINDOW,
        "angular_resolution_degrees": {
            "type": "number",
            "minimum": 0.05,
            "maximum": 45.0,
            "description": "Angular spacing of OpenCamLib samples and rotary moves.",
        },
        "radial_stock_to_leave_mm": _NONNEGATIVE_DISTANCE_MM,
        "layers": _SURFACE_LAYERS,
        "feed_mode": {
            "type": "string",
            "enum": ["axial_only", "surface_speed"],
        },
        "maximum_effective_feed_mm_per_min": {
            "type": "number",
            "minimum": 0.001,
            "maximum": 10_000_000.0,
            "description": "Centerline-safe effective feed ceiling.",
        },
        "mesh": _closed(
            {
                "linear_deflection_mm": {
                    "type": "number",
                    "minimum": 0.001,
                    "maximum": 25.4,
                },
                "angular_deflection_radians": {
                    "type": "number",
                    "minimum": 0.001,
                    "maximum": 1.570796327,
                },
            },
            ("linear_deflection_mm", "angular_deflection_radians"),
        ),
    },
    (
        "pattern",
        "cut_mode",
        "axial_window",
        "angular_resolution_degrees",
        "radial_stock_to_leave_mm",
        "layers",
        "feed_mode",
        "maximum_effective_feed_mm_per_min",
        "mesh",
    ),
)
_EXTENSION_ITEM = _closed(
    {
        "model": _EXACT_TARGET,
        "feature": {
            "type": "string",
            "pattern": r"^Face[1-9][0-9]*$",
            "maxLength": 32,
        },
        "edges": {
            "type": "array",
            "items": {
                "type": "string",
                "pattern": r"^Edge[1-9][0-9]*$",
                "maxLength": 32,
            },
            "minItems": 1,
            "maxItems": 64,
            "uniqueItems": True,
        },
    },
    ("model", "feature", "edges"),
)
_POCKET_EXTENSIONS = {
    "oneOf": [
        _closed({"kind": {"type": "string", "const": "none"}}, ("kind",)),
        _closed(
            {
                "kind": {"type": "string", "const": "explicit"},
                "default_length_mm": _POSITIVE_DISTANCE_MM,
                "extend_corners": {"type": "boolean"},
                "items": {
                    "type": "array",
                    "items": _EXTENSION_ITEM,
                    "minItems": 1,
                    "maxItems": 64,
                },
            },
            ("kind", "default_length_mm", "extend_corners", "items"),
        ),
    ]
}
_FACING_SETTINGS = _closed(
    {
        "cut_mode": {
            "type": "string",
            "enum": ["climb", "conventional"],
        },
        "pattern": {
            "type": "string",
            "enum": ["zigzag", "bidirectional", "directional", "spiral"],
        },
        "angle_degrees": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 180.0,
        },
        "reverse": {"type": "boolean"},
        "stepover_percent": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
        },
        "axial_stock_to_leave_mm": _NONNEGATIVE_DISTANCE_MM,
        "pass_extension_mm": _DISTANCE_MM,
        "stock_extension_mm": _DISTANCE_MM,
    },
    (
        "cut_mode",
        "pattern",
        "angle_degrees",
        "reverse",
        "stepover_percent",
        "axial_stock_to_leave_mm",
        "pass_extension_mm",
        "stock_extension_mm",
    ),
)
_LINKING_SETTINGS = _closed(
    {
        "strategy": {
            "type": "string",
            "enum": [
                "clearance_height",
                "retract_height",
                "line_of_sight",
                "tool_diameter",
                "tool_shape",
            ],
        },
        "collision_clearance_mm": _NONNEGATIVE_DISTANCE_MM,
    },
    ("strategy", "collision_clearance_mm"),
)
_HELIX_SETTINGS = _closed(
    {
        "start_at": {
            "type": "string",
            "enum": ["inside", "outside"],
            "description": "Begin at the inner or outer toolpath radius.",
        },
        "cut_mode": {
            "type": "string",
            "enum": ["climb", "conventional"],
        },
        "max_pitch_mm": {
            **_NONNEGATIVE_DISTANCE_MM,
            "description": "Maximum descent per revolution; zero disables this limit.",
        },
        "max_ramp_angle_degrees": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 90.0,
            "description": "Maximum ramp angle; zero disables this limit.",
        },
        "stepover_percent": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
        },
        "radial_stock_to_leave_outer_mm": _DISTANCE_MM,
        "sorting": {
            "type": "string",
            "enum": ["automatic", "manual"],
            "description": (
                "Automatic optimizes feature order from the origin; manual preserves "
                "the request order."
            ),
        },
    },
    (
        "start_at",
        "cut_mode",
        "max_pitch_mm",
        "max_ramp_angle_degrees",
        "stepover_percent",
        "radial_stock_to_leave_outer_mm",
        "sorting",
    ),
)
_ADAPTIVE_SETTINGS = _closed(
    {
        "cut_region": {
            "type": "string",
            "enum": ["inside", "outside"],
            "description": "Machine inside or outside the exact selected regions.",
        },
        "operation_type": {
            "type": "string",
            "enum": ["clearing", "profiling"],
        },
        "tolerance_mm": {
            "type": "number",
            "minimum": 0.001,
            "maximum": 0.15,
            "description": (
                "Accuracy/performance tolerance exposed by the human Adaptive panel."
            ),
        },
        "stepover_percent": {
            "type": "number",
            "minimum": 0.1,
            "maximum": 100.0,
        },
        "lift_distance_mm": _NONNEGATIVE_DISTANCE_MM,
        "keep_tool_down_ratio": {
            "type": "number",
            "exclusiveMinimum": 0.0,
            "maximum": 1_000_000.0,
            "description": (
                "Maximum cleared linking-path length divided by direct distance."
            ),
        },
        "xy_stock_to_leave_mm": _DISTANCE_MM,
        "force_inside_out": {"type": "boolean"},
        "finishing_profile": {"type": "boolean"},
        "use_outline": {"type": "boolean"},
        "rest_machining": {"type": "boolean"},
    },
    (
        "cut_region",
        "operation_type",
        "tolerance_mm",
        "stepover_percent",
        "lift_distance_mm",
        "keep_tool_down_ratio",
        "xy_stock_to_leave_mm",
        "force_inside_out",
        "finishing_profile",
        "use_outline",
        "rest_machining",
    ),
)
_ADAPTIVE_HELIX_ENTRY = _closed(
    {
        "max_pitch_mm": {
            **_NONNEGATIVE_DISTANCE_MM,
            "description": "Maximum descent per revolution; zero disables this limit.",
        },
        "max_ramp_angle_degrees": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 90.0,
            "description": "Maximum ramp angle; zero disables this limit.",
        },
        "cone_angle_degrees": {
            "type": "number",
            "minimum": 0.0,
            "exclusiveMaximum": 90.0,
        },
        "max_diameter_percent": {
            "type": "integer",
            "minimum": 10,
            "maximum": 100,
        },
        "min_diameter_percent": {
            "type": "integer",
            "minimum": 10,
            "maximum": 100,
        },
    },
    (
        "max_pitch_mm",
        "max_ramp_angle_degrees",
        "cone_angle_degrees",
        "max_diameter_percent",
        "min_diameter_percent",
    ),
)
_ADAPTIVE_DEPTHS = _closed(
    {
        "start_depth_mm": _DISTANCE_MM,
        "final_depth_mm": _DISTANCE_MM,
        "step_down_mm": _POSITIVE_DISTANCE_MM,
        "finish_step_mm": _NONNEGATIVE_DISTANCE_MM,
    },
    (
        "start_depth_mm",
        "final_depth_mm",
        "step_down_mm",
        "finish_step_mm",
    ),
)
_SLOT_REFERENCE = {
    "type": "string",
    "enum": [
        "center_of_mass",
        "bounding_box_center",
        "lowest_point",
        "highest_point",
    ],
}
_SLOT_ORIENTATION = {
    "type": "string",
    "enum": ["start_to_end", "perpendicular"],
}
_SLOT_PATH = {
    "oneOf": [
        _closed(
            {
                "kind": {"type": "string", "const": "custom_points"},
                "start_point_mm": _POINT_MM,
                "end_point_mm": _POINT_MM,
            },
            ("kind", "start_point_mm", "end_point_mm"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "single_edge"},
                "model": _EXACT_TARGET,
                "edge": {
                    "type": "string",
                    "pattern": r"^Edge[1-9][0-9]*$",
                    "maxLength": 32,
                },
                "orientation": _SLOT_ORIENTATION,
            },
            ("kind", "model", "edge", "orientation"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "single_horizontal_face"},
                "model": _EXACT_TARGET,
                "face": {
                    "type": "string",
                    "pattern": r"^Face[1-9][0-9]*$",
                    "maxLength": 32,
                },
                "span": {"type": "string", "enum": ["long_edge", "short_edge"]},
                "orientation": _SLOT_ORIENTATION,
            },
            ("kind", "model", "face", "span", "orientation"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "single_vertical_face"},
                "model": _EXACT_TARGET,
                "face": {
                    "type": "string",
                    "pattern": r"^Face[1-9][0-9]*$",
                    "maxLength": 32,
                },
                "orientation": _SLOT_ORIENTATION,
            },
            ("kind", "model", "face", "orientation"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "two_vertices"},
                "model": _EXACT_TARGET,
                "start_vertex": {
                    "type": "string",
                    "pattern": r"^Vertex[1-9][0-9]*$",
                    "maxLength": 32,
                },
                "end_vertex": {
                    "type": "string",
                    "pattern": r"^Vertex[1-9][0-9]*$",
                    "maxLength": 32,
                },
                "orientation": _SLOT_ORIENTATION,
            },
            ("kind", "model", "start_vertex", "end_vertex", "orientation"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "two_edges"},
                "model": _EXACT_TARGET,
                "start_edge": {
                    "type": "string",
                    "pattern": r"^Edge[1-9][0-9]*$",
                    "maxLength": 32,
                },
                "start_reference": _SLOT_REFERENCE,
                "end_edge": {
                    "type": "string",
                    "pattern": r"^Edge[1-9][0-9]*$",
                    "maxLength": 32,
                },
                "end_reference": _SLOT_REFERENCE,
                "orientation": _SLOT_ORIENTATION,
            },
            (
                "kind",
                "model",
                "start_edge",
                "start_reference",
                "end_edge",
                "end_reference",
                "orientation",
            ),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "two_vertical_faces"},
                "model": _EXACT_TARGET,
                "start_face": {
                    "type": "string",
                    "pattern": r"^Face[1-9][0-9]*$",
                    "maxLength": 32,
                },
                "start_reference": _SLOT_REFERENCE,
                "end_face": {
                    "type": "string",
                    "pattern": r"^Face[1-9][0-9]*$",
                    "maxLength": 32,
                },
                "end_reference": _SLOT_REFERENCE,
                "orientation": _SLOT_ORIENTATION,
            },
            (
                "kind",
                "model",
                "start_face",
                "start_reference",
                "end_face",
                "end_reference",
                "orientation",
            ),
        ),
    ]
}
_SLOT_SETTINGS = _closed(
    {
        "path": _SLOT_PATH,
        "extend_start_mm": _DISTANCE_MM,
        "extend_end_mm": _DISTANCE_MM,
        "layer_mode": {
            "type": "string",
            "enum": ["directional", "bidirectional"],
        },
        "reverse_direction": {"type": "boolean"},
    },
    (
        "path",
        "extend_start_mm",
        "extend_end_mm",
        "layer_mode",
        "reverse_direction",
    ),
)

_DRILL_FEATURE = _closed(
    {
        "subelement": {
            "type": "string",
            "pattern": r"^(?:Face|Edge)[1-9][0-9]*$",
            "maxLength": 32,
        },
        "enabled": {"type": "boolean"},
    },
    ("subelement", "enabled"),
)
_HOLE_FEATURE_GROUPS = {
    "type": "array",
    "items": _closed(
        {
            "model": _EXACT_TARGET,
            "features": {
                "type": "array",
                "items": _DRILL_FEATURE,
                "minItems": 1,
                "maxItems": 64,
            },
        },
        ("model", "features"),
    ),
    "minItems": 0,
    "maxItems": 32,
}
_DRILL_TARGETS = _closed(
    {
        "feature_groups": _HOLE_FEATURE_GROUPS,
        "locations_mm": {
            "type": "array",
            "items": _closed(
                {
                    "x_mm": _DISTANCE_MM,
                    "y_mm": _DISTANCE_MM,
                },
                ("x_mm", "y_mm"),
            ),
            "minItems": 0,
            "maxItems": 64,
        },
        "sorting": {
            "type": "string",
            "enum": ["automatic", "manual"],
        },
    },
    ("feature_groups", "locations_mm", "sorting"),
)
_DRILL_CYCLE = {
    "oneOf": [
        _closed({"kind": {"type": "string", "const": "standard"}}, ("kind",)),
        _closed(
            {
                "kind": {"type": "string", "const": "peck"},
                "depth_mm": _POSITIVE_DISTANCE_MM,
                "chip_break": {"type": "boolean"},
            },
            ("kind", "depth_mm", "chip_break"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "dwell"},
                "time_seconds": {
                    "type": "number",
                    "exclusiveMinimum": 0.0,
                    "maximum": 86_400.0,
                },
            },
            ("kind", "time_seconds"),
        ),
        _closed(
            {"kind": {"type": "string", "const": "feed_retract"}},
            ("kind",),
        ),
    ]
}
_TAP_CYCLE = {
    "oneOf": [
        _closed({"kind": {"type": "string", "const": "standard"}}, ("kind",)),
        _closed(
            {
                "kind": {"type": "string", "const": "dwell"},
                "time_seconds": {
                    "type": "number",
                    "exclusiveMinimum": 0.0,
                    "maximum": 86_400.0,
                },
            },
            ("kind", "time_seconds"),
        ),
    ]
}
_DEPTH_EXTENSION = {
    "type": "string",
    "enum": ["none", "drill_tip", "two_drill_tips"],
}
_DRILL_PROCESS = {
    "oneOf": [
        _closed(
            {
                "kind": {"type": "string", "const": "drilling"},
                "cycle": _DRILL_CYCLE,
                "depth_extension": _DEPTH_EXTENSION,
                "keep_tool_down": {"type": "boolean"},
            },
            ("kind", "cycle", "depth_extension", "keep_tool_down"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "tapping"},
                "cycle": _TAP_CYCLE,
                "depth_extension": _DEPTH_EXTENSION,
                "keep_tool_down": {"type": "boolean"},
            },
            ("kind", "cycle", "depth_extension", "keep_tool_down"),
        ),
    ]
}
_DRILL_DEPTHS = _closed(
    {
        "start_depth_mm": _DISTANCE_MM,
        "final_depth_mm": _DISTANCE_MM,
    },
    ("start_depth_mm", "final_depth_mm"),
)
_THREAD_TARGETS = _closed(
    {
        "feature_groups": {
            **_HOLE_FEATURE_GROUPS,
            "minItems": 1,
        },
        "sorting": {"type": "string", "enum": ["automatic", "manual"]},
    },
    ("feature_groups", "sorting"),
)
_CUSTOM_THREAD_PITCH = {
    "oneOf": [
        _closed(
            {
                "kind": {"type": "string", "const": "pitch_mm"},
                "value": _POSITIVE_DISTANCE_MM,
            },
            ("kind", "value"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "threads_per_inch"},
                "value": {"type": "integer", "minimum": 1, "maximum": 99},
            },
            ("kind", "value"),
        ),
    ]
}
_THREAD_DEFINITION = {
    "oneOf": [
        _closed(
            {
                "kind": {"type": "string", "const": "custom"},
                "side": {"type": "string", "enum": ["internal", "external"]},
                "major_diameter_mm": _POSITIVE_DISTANCE_MM,
                "minor_diameter_mm": _POSITIVE_DISTANCE_MM,
                "pitch": _CUSTOM_THREAD_PITCH,
            },
            (
                "kind",
                "side",
                "major_diameter_mm",
                "minor_diameter_mm",
                "pitch",
            ),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "standard"},
                "series": {
                    "type": "string",
                    "enum": [
                        "imperial_external_2a",
                        "imperial_external_3a",
                        "imperial_internal_2b",
                        "imperial_internal_3b",
                        "metric_external_4g6g",
                        "metric_external_6g",
                        "metric_internal_6h",
                    ],
                },
                "designation": {"type": "string", "minLength": 1, "maxLength": 80},
                "fit_percent": {"type": "integer", "minimum": 0, "maximum": 100},
            },
            ("kind", "series", "designation", "fit_percent"),
        ),
    ]
}
_THREAD_SETTINGS = _closed(
    {
        "definition": _THREAD_DEFINITION,
        "orientation": {"type": "string", "enum": ["left_hand", "right_hand"]},
        "direction": {"type": "string", "enum": ["climb", "conventional"]},
        "passes": {"type": "integer", "minimum": 1, "maximum": 99},
        "lead_in_out": {"type": "boolean"},
    },
    ("definition", "orientation", "direction", "passes", "lead_in_out"),
)
_ENGRAVE_GEOMETRY = {
    "oneOf": [
        _closed(
            {
                "kind": {
                    "type": "string",
                    "const": "entire_job",
                    "description": "Engrave every zero-volume wire model in the exact Job.",
                }
            },
            ("kind",),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "whole_models"},
                "models": {
                    "type": "array",
                    "items": _EXACT_TARGET,
                    "minItems": 1,
                    "maxItems": 32,
                },
            },
            ("kind", "models"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "edges"},
                "items": {
                    "type": "array",
                    "items": _closed(
                        {
                            "model": _EXACT_TARGET,
                            "edges": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "pattern": r"^Edge[1-9][0-9]*$",
                                    "maxLength": 32,
                                },
                                "minItems": 1,
                                "maxItems": 64,
                                "uniqueItems": True,
                            },
                        },
                        ("model", "edges"),
                    ),
                    "minItems": 1,
                    "maxItems": 32,
                },
            },
            ("kind", "items"),
        ),
    ]
}
_ENGRAVE_SETTINGS = _closed(
    {
        "start_vertex": {
            "type": "integer",
            "minimum": 0,
            "maximum": 999999,
        }
    },
    ("start_vertex",),
)
_DEBURR_GEOMETRY = _closed(
    {
        "kind": {"type": "string", "const": "features"},
        "items": {
            "type": "array",
            "items": _closed(
                {
                    "model": _EXACT_TARGET,
                    "features": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "pattern": r"^(?:Face|Edge)[1-9][0-9]*$",
                            "maxLength": 32,
                        },
                        "minItems": 1,
                        "maxItems": 64,
                        "uniqueItems": True,
                    },
                },
                ("model", "features"),
            ),
            "minItems": 1,
            "maxItems": 32,
        },
    },
    ("kind", "items"),
)
_DEBURR_SETTINGS = _closed(
    {
        "width_mm": _POSITIVE_DISTANCE_MM,
        "extra_depth_mm": _NONNEGATIVE_DISTANCE_MM,
        "direction": {
            "type": "string",
            "enum": ["clockwise", "counterclockwise"],
        },
    },
    ("width_mm", "extra_depth_mm", "direction"),
)
_DEBURR_DEPTHS = _closed(
    {"step_down_mm": _NONNEGATIVE_DISTANCE_MM},
    ("step_down_mm",),
)
_VCARVE_GEOMETRY = {
    "oneOf": [
        _closed(
            {
                "kind": {"type": "string", "const": "whole_models"},
                "models": {
                    "type": "array",
                    "items": _EXACT_TARGET,
                    "minItems": 1,
                    "maxItems": 32,
                },
            },
            ("kind", "models"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "faces"},
                "items": {
                    "type": "array",
                    "items": _closed(
                        {
                            "model": _EXACT_TARGET,
                            "faces": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "pattern": r"^Face[1-9][0-9]*$",
                                    "maxLength": 32,
                                },
                                "minItems": 1,
                                "maxItems": 64,
                                "uniqueItems": True,
                            },
                        },
                        ("model", "faces"),
                    ),
                    "minItems": 1,
                    "maxItems": 32,
                },
            },
            ("kind", "items"),
        ),
    ]
}
_VCARVE_SETTINGS = _closed(
    {
        "discretization_deflection_mm": {
            "type": "number",
            "minimum": 0.001,
            "maximum": 1.0,
        },
        "colinear_filter_degrees": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 90.0,
        },
        "optimize_movements": {"type": "boolean"},
        "finishing": {
            "oneOf": [
                _closed(
                    {"enabled": {"type": "boolean", "const": False}},
                    ("enabled",),
                ),
                _closed(
                    {
                        "enabled": {"type": "boolean", "const": True},
                        "z_offset_mm": _DISTANCE_MM,
                    },
                    ("enabled", "z_offset_mm"),
                ),
            ]
        },
    },
    (
        "discretization_deflection_mm",
        "colinear_filter_degrees",
        "optimize_movements",
        "finishing",
    ),
)
_VCARVE_DEPTHS = _closed(
    {
        "final_depth_mm": _DISTANCE_MM,
        "step_down_mm": _NONNEGATIVE_DISTANCE_MM,
    },
    ("final_depth_mm", "step_down_mm"),
)


def manufacture_adaptive_defaults_variant() -> NativeCapabilityVariant:
    """Focused Adaptive clearing with the shipped human-operation defaults."""
    return NativeCapabilityVariant(
        operation="adaptive",
        description=(
            "Adaptively clear exact planar Faces or closed Edge loops using setup defaults."
        ),
        action_ids=frozenset({"CAM_Adaptive"}),
        surface_ids=frozenset({"manufacture"}),
        exact_target_type="ExactCamJobAdaptiveRegionsAndController",
        transaction_behavior="background",
        background_required=True,
        parameters=_closed(
            {
                "label": LABEL_SCHEMA,
                "job": _EXACT_TARGET,
                "tool_controller": _EXACT_TARGET,
                "geometry": _FEATURE_SELECTION,
                "coolant": _COOLANT_SCHEMA,
            },
            ("job", "tool_controller", "geometry"),
        ),
    )


def manufacture_operation_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=MANUFACTURE_OPERATION_CAPABILITY_NAME,
        description=(
            "Create exact Job-owned machining operations and apply focused in-place "
            "operation settings with explicit geometry and process parameters."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="profile",
                description=(
                    "Machine exact Faces or Edges on the inside or outside using setup defaults."
                ),
                action_ids=frozenset({"CAM_Profile"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactCamJobProfileGeometryAndController",
                transaction_behavior="background",
                background_required=True,
                parameters=_closed(
                    {
                        "label": LABEL_SCHEMA,
                        "job": _EXACT_TARGET,
                        "tool_controller": _EXACT_TARGET,
                        "geometry": _FEATURE_SELECTION,
                        "cut_side": {
                            "type": "string",
                            "enum": ["outside", "inside"],
                        },
                        "coolant": _COOLANT_SCHEMA,
                    },
                    (
                        "job",
                        "tool_controller",
                        "geometry",
                        "cut_side",
                    ),
                ),
            ),
            NativeCapabilityVariant(
                operation="pocket_shape",
                description=(
                    "Clear exact planar Faces or closed Edge loops using the setup defaults."
                ),
                action_ids=frozenset({"CAM_Pocket_Shape"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactCamJobPocketGeometryAndController",
                transaction_behavior="background",
                background_required=True,
                parameters=_closed(
                    {
                        "label": LABEL_SCHEMA,
                        "job": _EXACT_TARGET,
                        "tool_controller": _EXACT_TARGET,
                        "geometry": _FEATURE_SELECTION,
                        "coolant": _COOLANT_SCHEMA,
                    },
                    (
                        "job",
                        "tool_controller",
                        "geometry",
                    ),
                ),
            ),
            NativeCapabilityVariant(
                operation="pocket_3d",
                description=(
                    "Clear bounded 3D pockets from Faces or closed horizontal Edge loops."
                ),
                action_ids=frozenset({"CAM_Pocket3D"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type=(
                    "ExactCamJobPocket3DFeaturesControllerAndParameters"
                ),
                transaction_behavior="background",
                background_required=True,
                parameters=_closed(
                    {
                        "label": LABEL_SCHEMA,
                        "job": _EXACT_TARGET,
                        "tool_controller": _EXACT_TARGET,
                        "geometry": _POCKET_GEOMETRY,
                        "pocket": _POCKET_3D_SETTINGS,
                        "depths": _POCKET_3D_DEPTHS,
                        "heights": _HEIGHTS,
                        "coolant": {
                            "type": "string",
                            "enum": ["none", "flood", "mist"],
                        },
                    },
                    (
                        "label",
                        "job",
                        "tool_controller",
                        "geometry",
                        "pocket",
                        "depths",
                        "heights",
                        "coolant",
                    ),
                ),
            ),
            NativeCapabilityVariant(
                operation="surface",
                description=(
                    "Finish 3D surfaces with planar OpenCamLib paths over a setup or "
                    "selected Faces."
                ),
                action_ids=frozenset({"CAM_Surface"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type=(
                    "ExactCamJobSurfaceFacesControllerAndParameters"
                ),
                transaction_behavior="background",
                background_required=True,
                parameters=_closed(
                    {
                        "label": LABEL_SCHEMA,
                        "job": _EXACT_TARGET,
                        "tool_controller": _EXACT_TARGET,
                        "geometry": _SURFACE_GEOMETRY,
                        "surface": _SURFACE_SETTINGS,
                        "depths": _SURFACE_DEPTHS,
                        "heights": _HEIGHTS,
                        "coolant": {
                            "type": "string",
                            "enum": ["none", "flood", "mist"],
                        },
                    },
                    (
                        "label",
                        "job",
                        "tool_controller",
                        "geometry",
                        "surface",
                        "depths",
                        "heights",
                        "coolant",
                    ),
                ),
            ),
            NativeCapabilityVariant(
                operation="waterline",
                description=(
                    "Machine constant-Z contours over a setup or selected Faces."
                ),
                action_ids=frozenset({"CAM_Waterline"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type=(
                    "ExactCamJobWaterlineFacesControllerAlgorithmAndParameters"
                ),
                transaction_behavior="background",
                background_required=True,
                parameters=_closed(
                    {
                        "label": LABEL_SCHEMA,
                        "job": _EXACT_TARGET,
                        "tool_controller": _EXACT_TARGET,
                        "geometry": _WATERLINE_GEOMETRY,
                        "waterline": _WATERLINE_SETTINGS,
                        "depths": _SURFACE_DEPTHS,
                        "heights": _HEIGHTS,
                        "coolant": {
                            "type": "string",
                            "enum": ["none", "flood", "mist"],
                        },
                    },
                    (
                        "label",
                        "job",
                        "tool_controller",
                        "geometry",
                        "waterline",
                        "depths",
                        "heights",
                        "coolant",
                    ),
                ),
            ),
            NativeCapabilityVariant(
                operation="rotary_surface",
                description="Create a bounded four-axis Rotary Surface toolpath.",
                action_ids=frozenset({"CAM_RotarySurface"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type=(
                    "ExactCamJobMachineCylinderRotaryFacesControllerAndParameters"
                ),
                transaction_behavior="background",
                background_required=True,
                parameters=_closed(
                    {
                        "label": LABEL_SCHEMA,
                        "job": _EXACT_TARGET,
                        "tool_controller": _EXACT_TARGET,
                        "geometry": _ROTARY_SURFACE_GEOMETRY,
                        "rotary_surface": _ROTARY_SURFACE_SETTINGS,
                        "heights": _HEIGHTS,
                        "coolant": {
                            "type": "string",
                            "enum": ["none", "flood", "mist"],
                        },
                    },
                    (
                        "label",
                        "job",
                        "tool_controller",
                        "geometry",
                        "rotary_surface",
                        "heights",
                        "coolant",
                    ),
                ),
            ),
            NativeCapabilityVariant(
                operation="mill_facing",
                description=(
                    "Face the exact setup stock using its machining defaults."
                ),
                action_ids=frozenset({"CAM_MillFacing"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactCamJobStockAndController",
                transaction_behavior="background",
                background_required=True,
                parameters=_closed(
                    {
                        "label": LABEL_SCHEMA,
                        "job": _EXACT_TARGET,
                        "tool_controller": _EXACT_TARGET,
                        "coolant": _COOLANT_SCHEMA,
                    },
                    (
                        "job",
                        "tool_controller",
                    ),
                ),
            ),
            NativeCapabilityVariant(
                operation="helix",
                description=(
                    "Helically mill selected circular Faces or Edges."
                ),
                action_ids=frozenset({"CAM_Helix"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactCamJobHoleFeaturesControllerAndHelixParameters",
                transaction_behavior="background",
                background_required=True,
                parameters=_closed(
                    {
                        "label": LABEL_SCHEMA,
                        "job": _EXACT_TARGET,
                        "tool_controller": _EXACT_TARGET,
                        "geometry": _POCKET_GEOMETRY,
                        "helix": _HELIX_SETTINGS,
                        "depths": _DEPTHS,
                        "heights": _HEIGHTS,
                        "linking": _LINKING_SETTINGS,
                        "coolant": {
                            "type": "string",
                            "enum": ["none", "flood", "mist"],
                        },
                    },
                    (
                        "label",
                        "job",
                        "tool_controller",
                        "geometry",
                        "helix",
                        "depths",
                        "heights",
                        "linking",
                        "coolant",
                    ),
                ),
            ),
            NativeCapabilityVariant(
                operation="adaptive",
                description=(
                    "Adaptively clear or profile selected Face and Edge regions."
                ),
                action_ids=frozenset({"CAM_Adaptive"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type=(
                    "ExactCamJobAdaptiveRegionsControllerExtensionsAndParameters"
                ),
                transaction_behavior="background",
                background_required=True,
                parameters=_closed(
                    {
                        "label": LABEL_SCHEMA,
                        "job": _EXACT_TARGET,
                        "tool_controller": _EXACT_TARGET,
                        "geometry": _POCKET_GEOMETRY,
                        "adaptive": _ADAPTIVE_SETTINGS,
                        "helix_entry": _ADAPTIVE_HELIX_ENTRY,
                        "depths": _ADAPTIVE_DEPTHS,
                        "heights": _HEIGHTS,
                        "extensions": _POCKET_EXTENSIONS,
                        "coolant": {
                            "type": "string",
                            "enum": ["none", "flood", "mist"],
                        },
                    },
                    (
                        "label",
                        "job",
                        "tool_controller",
                        "geometry",
                        "adaptive",
                        "helix_entry",
                        "depths",
                        "heights",
                        "extensions",
                        "coolant",
                    ),
                ),
            ),
            NativeCapabilityVariant(
                operation="slot",
                description=(
                    "Mill a horizontal slot from explicit points or supported model "
                    "features."
                ),
                action_ids=frozenset({"CAM_Slot"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactCamJobSlotPathControllerAndParameters",
                transaction_behavior="background",
                background_required=True,
                parameters=_closed(
                    {
                        "label": LABEL_SCHEMA,
                        "job": _EXACT_TARGET,
                        "tool_controller": _EXACT_TARGET,
                        "slot": _SLOT_SETTINGS,
                        "depths": _DEPTHS,
                        "heights": _HEIGHTS,
                        "coolant": {
                            "type": "string",
                            "enum": ["none", "flood", "mist"],
                        },
                    },
                    (
                        "label",
                        "job",
                        "tool_controller",
                        "slot",
                        "depths",
                        "heights",
                        "coolant",
                    ),
                ),
            ),
            NativeCapabilityVariant(
                operation="drilling",
                description=(
                    "Drill exact circular Faces or Edges using the setup defaults."
                ),
                action_ids=frozenset({"CAM_Drilling"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactCamJobDrillableGeometryAndController",
                transaction_behavior="background",
                background_required=True,
                parameters=_closed(
                    {
                        "label": LABEL_SCHEMA,
                        "job": _EXACT_TARGET,
                        "tool_controller": _EXACT_TARGET,
                        "geometry": _FEATURE_SELECTION,
                        "coolant": _COOLANT_SCHEMA,
                    },
                    (
                        "job",
                        "tool_controller",
                        "geometry",
                    ),
                ),
            ),
            NativeCapabilityVariant(
                operation="thread_milling",
                description=(
                    "Mill internal or external threads in selected circular features."
                ),
                action_ids=frozenset({"CAM_ThreadMilling"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type=(
                    "ExactCamJobHoleFeaturesControllerAndThreadDefinition"
                ),
                transaction_behavior="background",
                background_required=True,
                parameters=_closed(
                    {
                        "label": LABEL_SCHEMA,
                        "job": _EXACT_TARGET,
                        "tool_controller": _EXACT_TARGET,
                        "targets": _THREAD_TARGETS,
                        "thread": _THREAD_SETTINGS,
                        "depths": _DRILL_DEPTHS,
                        "heights": _HEIGHTS,
                        "linking": _LINKING_SETTINGS,
                        "coolant": {
                            "type": "string",
                            "enum": ["none", "flood", "mist"],
                        },
                    },
                    (
                        "label",
                        "job",
                        "tool_controller",
                        "targets",
                        "thread",
                        "depths",
                        "heights",
                        "linking",
                        "coolant",
                    ),
                ),
            ),
            NativeCapabilityVariant(
                operation="engrave",
                description=(
                    "Engrave selected Edges, wire models, or engravable setup models."
                ),
                action_ids=frozenset({"CAM_Engrave"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactCamJobEngraveGeometryControllerAndParameters",
                transaction_behavior="background",
                background_required=True,
                parameters=_closed(
                    {
                        "label": LABEL_SCHEMA,
                        "job": _EXACT_TARGET,
                        "tool_controller": _EXACT_TARGET,
                        "geometry": _ENGRAVE_GEOMETRY,
                        "engrave": _ENGRAVE_SETTINGS,
                        "depths": _DEPTHS,
                        "heights": _HEIGHTS,
                        "linking": _LINKING_SETTINGS,
                        "coolant": {
                            "type": "string",
                            "enum": ["none", "flood", "mist"],
                        },
                    },
                    (
                        "label",
                        "job",
                        "tool_controller",
                        "geometry",
                        "engrave",
                        "depths",
                        "heights",
                        "linking",
                        "coolant",
                    ),
                ),
            ),
            NativeCapabilityVariant(
                operation="deburr",
                description=(
                    "Chamfer or deburr selected Edges and Faces."
                ),
                action_ids=frozenset({"CAM_Deburr"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactCamJobDeburrFeaturesControllerAndParameters",
                transaction_behavior="background",
                background_required=True,
                parameters=_closed(
                    {
                        "label": LABEL_SCHEMA,
                        "job": _EXACT_TARGET,
                        "tool_controller": _EXACT_TARGET,
                        "geometry": _DEBURR_GEOMETRY,
                        "deburr": _DEBURR_SETTINGS,
                        "depths": _DEBURR_DEPTHS,
                        "heights": _HEIGHTS,
                        "linking": _LINKING_SETTINGS,
                        "coolant": {
                            "type": "string",
                            "enum": ["none", "flood", "mist"],
                        },
                    },
                    (
                        "label",
                        "job",
                        "tool_controller",
                        "geometry",
                        "deburr",
                        "depths",
                        "heights",
                        "linking",
                        "coolant",
                    ),
                ),
            ),
            NativeCapabilityVariant(
                operation="v_carve",
                description=(
                    "V-carve horizontal Faces or face-bearing models with a V-bit."
                ),
                action_ids=frozenset({"CAM_Vcarve"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactCamJobVCarveFacesControllerAndParameters",
                transaction_behavior="background",
                background_required=True,
                parameters=_closed(
                    {
                        "label": LABEL_SCHEMA,
                        "job": _EXACT_TARGET,
                        "tool_controller": _EXACT_TARGET,
                        "geometry": _VCARVE_GEOMETRY,
                        "v_carve": _VCARVE_SETTINGS,
                        "depths": _VCARVE_DEPTHS,
                        "heights": _HEIGHTS,
                        "coolant": {
                            "type": "string",
                            "enum": ["none", "flood", "mist"],
                        },
                    },
                    (
                        "label",
                        "job",
                        "tool_controller",
                        "geometry",
                        "v_carve",
                        "depths",
                        "heights",
                        "coolant",
                    ),
                ),
            ),
            NativeCapabilityVariant(
                operation="set_start_point",
                description=(
                    "Set the planar start point of one exact current Job operation. "
                    "Z is always derived from that operation's frozen Clearance Height, "
                    "matching the human Start Point Selection action."
                ),
                action_ids=frozenset({"CAM_SetStartPoint"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactCamJobOperationAndPlanarStartPoint",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "job": _EXACT_TARGET,
                        "target": _EXACT_TARGET,
                        "point_mm": _PLANAR_POINT_MM,
                    },
                    ("job", "target", "point_mm"),
                ),
            ),
            NativeCapabilityVariant(
                operation="array",
                description="Create a parametric Array from ordered exact Job toolpaths.",
                action_ids=frozenset({"CAM_Array"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type=(
                    "ExactCamJobBaseToolpathsArrayPatternAndPointSources"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters=ARRAY_PARAMETERS_SCHEMA,
            ),
            NativeCapabilityVariant(
                operation="simple_copy",
                description="Flatten ordered exact Job toolpaths into one Custom operation.",
                action_ids=frozenset({"CAM_SimpleCopy"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactCamJobPlacedToolpathFlatteningSet",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "label": LABEL_SCHEMA,
                        "job": _EXACT_TARGET,
                        "source_operations": {
                            "type": "array",
                            "items": _EXACT_TARGET,
                            "minItems": 1,
                            "maxItems": 64,
                            "uniqueItems": True,
                            "description": (
                                "Ordered exact Job operation outputs to flatten after "
                                "applying each operation's placement."
                            ),
                        },
                    },
                    ("label", "job", "source_operations"),
                ),
            ),
        ),
    )


def register_manufacture_operation_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(manufacture_operation_capability_definition())
