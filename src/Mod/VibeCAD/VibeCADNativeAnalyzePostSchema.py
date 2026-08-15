# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp Native contracts for FEM post-processing graphs."""

from __future__ import annotations

from VibeCADNativeAnalyzeModelSchema import _ANALYSIS_TARGET
from VibeCADNativeAnalyzeResultState import RESULT_TARGET
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


ANALYZE_POST_CAPABILITY_NAME = "analyze.post"

_COORDINATE_MM = {
    "type": "number",
    "minimum": -1000000000.0,
    "maximum": 1000000000.0,
}
_POINT_MM = {
    "type": "object",
    "properties": {
        "x": _COORDINATE_MM,
        "y": _COORDINATE_MM,
        "z": _COORDINATE_MM,
    },
    "required": ["x", "y", "z"],
    "additionalProperties": False,
}
_CALCULATOR_TOKEN = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "const": "number"},
                "value": {
                    "type": "number",
                    "minimum": -1.0e100,
                    "maximum": 1.0e100,
                },
            },
            "required": ["kind", "value"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "const": "field"},
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 160,
                },
                "component": {
                    "type": "string",
                    "enum": [
                        "scalar",
                        "vector",
                        "x",
                        "y",
                        "z",
                        "xx",
                        "yy",
                        "zz",
                        "xy",
                        "yz",
                        "zx",
                    ],
                },
            },
            "required": ["kind", "name", "component"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "const": "coordinate"},
                "component": {
                    "type": "string",
                    "enum": ["vector", "x", "y", "z"],
                },
            },
            "required": ["kind", "component"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "const": "basis_vector"},
                "axis": {"type": "string", "enum": ["x", "y", "z"]},
            },
            "required": ["kind", "axis"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "const": "operator"},
                "operation": {
                    "type": "string",
                    "enum": [
                        "add",
                        "subtract",
                        "multiply",
                        "divide",
                        "power",
                        "cross",
                        "dot",
                        "negate",
                        "absolute",
                        "cosine",
                        "sine",
                        "tangent",
                        "exponential",
                        "natural_log",
                        "square_root",
                        "magnitude",
                        "normalize",
                    ],
                },
            },
            "required": ["kind", "operation"],
            "additionalProperties": False,
        },
    ]
}
_CALCULATOR_INVALID_VALUES = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "const": "reject"},
            },
            "required": ["mode"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "const": "replace"},
                "value": {
                    "type": "number",
                    "minimum": -1.0e100,
                    "maximum": 1.0e100,
                },
            },
            "required": ["mode", "value"],
            "additionalProperties": False,
        },
    ]
}
_GLYPH_ORIENTATION = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "const": "none"},
            },
            "required": ["mode"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "const": "vector_field"},
                "field": {"type": "string", "minLength": 1, "maxLength": 160},
            },
            "required": ["mode", "field"],
            "additionalProperties": False,
        },
    ]
}
_GLYPH_SCALING = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "const": "none"},
            },
            "required": ["mode"],
            "additionalProperties": False,
        },
        *[
            {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "const": mode},
                    "field": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 160,
                    },
                    "factor": {
                        "type": "number",
                        "exclusiveMinimum": 0.0,
                        "maximum": 1.0e12,
                    },
                },
                "required": ["mode", "field", "factor"],
                "additionalProperties": False,
            }
            for mode in ("scalar_field", "vector_magnitude", "vector_components")
        ],
    ]
}
_GLYPH_SAMPLING = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "const": "all"},
            },
            "required": ["mode"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "const": "every_nth"},
                "stride": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 999999999,
                },
            },
            "required": ["mode", "stride"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "const": "uniform"},
                "maximum_points": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 25000,
                },
            },
            "required": ["mode", "maximum_points"],
            "additionalProperties": False,
        },
    ]
}


def analyze_post_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ANALYZE_POST_CAPABILITY_NAME,
        description=(
            "Build exact FEM post-processing graphs through concise typed operations."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="create_pipeline",
                description=(
                    "Create one post-processing pipeline from an exact legacy mechanical "
                    "result in one exact analysis."
                ),
                action_ids=frozenset({"FEM_PostPipelineFromResult"}),
                surface_ids=frozenset({"analyze"}),
                exact_target_type="ExactAnalysisLegacyResultAndHistory",
                transaction_behavior="document",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "analysis": _ANALYSIS_TARGET,
                        "result": RESULT_TARGET,
                        "label": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160,
                        },
                    },
                    "required": ["analysis", "result", "label"],
                    "additionalProperties": False,
                },
            ),
            NativeCapabilityVariant(
                operation="create_branch",
                description=(
                    "Create and configure one branch under an exact post-processing "
                    "source. Serial chains child filters; parallel gives each child "
                    "the branch input. Passthrough exposes the input until an appended "
                    "child output is wanted."
                ),
                action_ids=frozenset({"FEM_PostBranchFilter"}),
                surface_ids=frozenset({"analyze"}),
                exact_target_type="ExactPostSourceAndHistory",
                transaction_behavior="document",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "source": RESULT_TARGET,
                        "label": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160,
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["serial", "parallel"],
                        },
                        "output": {
                            "type": "string",
                            "enum": ["passthrough", "append"],
                        },
                    },
                    "required": ["source", "label", "mode", "output"],
                    "additionalProperties": False,
                },
            ),
            NativeCapabilityVariant(
                operation="create_warp",
                description=(
                    "Create one warp filter from an exact post source, an available "
                    "three-component point field, and a finite deformation factor."
                ),
                action_ids=frozenset({"FEM_PostFilterWarp"}),
                surface_ids=frozenset({"analyze"}),
                exact_target_type="ExactPostSourceFieldAndHistory",
                transaction_behavior="document",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "source": RESULT_TARGET,
                        "label": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160,
                        },
                        "vector_field": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160,
                        },
                        "factor": {
                            "type": "number",
                            "minimum": -1000000.0,
                            "maximum": 1000000.0,
                        },
                    },
                    "required": ["source", "label", "vector_field", "factor"],
                    "additionalProperties": False,
                },
            ),
            NativeCapabilityVariant(
                operation="create_scalar_clip",
                description=(
                    "Create one scalar clip from an exact post source, an available "
                    "scalar point field, an in-range threshold, and an explicit side."
                ),
                action_ids=frozenset({"FEM_PostFilterClipScalar"}),
                surface_ids=frozenset({"analyze"}),
                exact_target_type="ExactPostSourceScalarFieldAndHistory",
                transaction_behavior="document",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "source": RESULT_TARGET,
                        "label": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160,
                        },
                        "scalar_field": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160,
                        },
                        "threshold": {"type": "number"},
                        "inside_out": {"type": "boolean"},
                    },
                    "required": [
                        "source",
                        "label",
                        "scalar_field",
                        "threshold",
                        "inside_out",
                    ],
                    "additionalProperties": False,
                },
            ),
            NativeCapabilityVariant(
                operation="create_cut",
                description=(
                    "Cut an exact post source with an exact plane, sphere, cylinder, "
                    "or box function owned by the same pipeline."
                ),
                action_ids=frozenset({"FEM_PostFilterCutFunction"}),
                surface_ids=frozenset({"analyze"}),
                exact_target_type="ExactPostSourceFunctionAndHistory",
                transaction_behavior="document",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "source": RESULT_TARGET,
                        "function": RESULT_TARGET,
                        "label": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160,
                        },
                    },
                    "required": ["source", "function", "label"],
                    "additionalProperties": False,
                },
            ),
            NativeCapabilityVariant(
                operation="create_region_clip",
                description=(
                    "Clip an exact post source by an exact same-pipeline implicit "
                    "function, with explicit side and whole-cell/interpolated behavior."
                ),
                action_ids=frozenset({"FEM_PostFilterClipRegion"}),
                surface_ids=frozenset({"analyze"}),
                exact_target_type="ExactPostSourceFunctionAndHistory",
                transaction_behavior="document",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "source": RESULT_TARGET,
                        "function": RESULT_TARGET,
                        "label": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160,
                        },
                        "inside_out": {"type": "boolean"},
                        "cut_cells": {"type": "boolean"},
                    },
                    "required": [
                        "source",
                        "function",
                        "label",
                        "inside_out",
                        "cut_cells",
                    ],
                    "additionalProperties": False,
                },
            ),
            NativeCapabilityVariant(
                operation="create_contours",
                description=(
                    "Create iso-contours from an exact source field/component with "
                    "explicit count, coloring, smoothing, and relaxation."
                ),
                action_ids=frozenset({"FEM_PostFilterContours"}),
                surface_ids=frozenset({"analyze"}),
                exact_target_type="ExactPostSourceFieldAndHistory",
                transaction_behavior="document",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "source": RESULT_TARGET,
                        "label": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160,
                        },
                        "field": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160,
                        },
                        "component": {
                            "type": "string",
                            "enum": ["scalar", "magnitude", "x", "y", "z"],
                        },
                        "count": {"type": "integer", "minimum": 1, "maximum": 1000},
                        "color_by_field": {"type": "boolean"},
                        "smoothing": {"type": "boolean"},
                        "relaxation": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                        },
                    },
                    "required": [
                        "source",
                        "label",
                        "field",
                        "component",
                        "count",
                        "color_by_field",
                        "smoothing",
                        "relaxation",
                    ],
                    "additionalProperties": False,
                },
            ),
            NativeCapabilityVariant(
                operation="create_line_sample",
                description=(
                    "Sample one point field along an exact nonzero line. The result "
                    "retains its source and returns compact statistics, not arrays."
                ),
                action_ids=frozenset({"FEM_PostFilterDataAlongLine"}),
                surface_ids=frozenset({"analyze"}),
                exact_target_type="ExactPostSourceFieldLineAndHistory",
                transaction_behavior="document",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "source": RESULT_TARGET,
                        "label": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160,
                        },
                        "field": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160,
                        },
                        "component": {
                            "type": "string",
                            "enum": [
                                "scalar",
                                "magnitude",
                                "x",
                                "y",
                                "z",
                                "xx",
                                "yy",
                                "zz",
                                "xy",
                                "yz",
                                "zx",
                            ],
                        },
                        "start_mm": _POINT_MM,
                        "end_mm": _POINT_MM,
                        "resolution": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 100000,
                        },
                    },
                    "required": [
                        "source",
                        "label",
                        "field",
                        "component",
                        "start_mm",
                        "end_mm",
                        "resolution",
                    ],
                    "additionalProperties": False,
                },
            ),
            NativeCapabilityVariant(
                operation="create_point_sample",
                description=(
                    "Probe one point field at an exact point. Scalar fields return the "
                    "scalar; multicomponent fields return their magnitude."
                ),
                action_ids=frozenset({"FEM_PostFilterDataAtPoint"}),
                surface_ids=frozenset({"analyze"}),
                exact_target_type="ExactPostSourceFieldPointAndHistory",
                transaction_behavior="document",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "source": RESULT_TARGET,
                        "label": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160,
                        },
                        "field": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160,
                        },
                        "point_mm": _POINT_MM,
                    },
                    "required": ["source", "label", "field", "point_mm"],
                    "additionalProperties": False,
                },
            ),
            NativeCapabilityVariant(
                operation="create_calculated_field",
                description=(
                    "Create one validated scalar or vector point field from typed "
                    "reverse-Polish tokens; arbitrary native expressions are not accepted."
                ),
                action_ids=frozenset({"FEM_PostFilterCalculator"}),
                surface_ids=frozenset({"analyze"}),
                exact_target_type="ExactPostSourceTypedExpressionAndHistory",
                transaction_behavior="document",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "source": RESULT_TARGET,
                        "label": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160,
                        },
                        "result_field": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 80,
                            "pattern": "^[A-Za-z][A-Za-z0-9_ ]{0,79}$",
                        },
                        "result_unit": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 32,
                        },
                        "expression": {
                            "type": "array",
                            "description": (
                                "Postfix tokens: emit operands first, then the operator "
                                "that consumes them."
                            ),
                            "minItems": 1,
                            "maxItems": 64,
                            "items": _CALCULATOR_TOKEN,
                        },
                        "invalid_values": _CALCULATOR_INVALID_VALUES,
                    },
                    "required": [
                        "source",
                        "label",
                        "result_field",
                        "result_unit",
                        "expression",
                        "invalid_values",
                    ],
                    "additionalProperties": False,
                },
            ),
            NativeCapabilityVariant(
                operation="create_glyphs",
                description=(
                    "Visualize exact point data with bounded native glyphs, explicit "
                    "orientation/scaling fields, and deterministic sampling limits."
                ),
                action_ids=frozenset({"FEM_PostFilterGlyph"}),
                surface_ids=frozenset({"analyze"}),
                exact_target_type="ExactPostSourceGlyphFieldsSamplingAndHistory",
                transaction_behavior="document",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "source": RESULT_TARGET,
                        "label": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160,
                        },
                        "glyph": {
                            "type": "string",
                            "enum": [
                                "arrow",
                                "cone",
                                "cube",
                                "cylinder",
                                "line",
                                "sphere",
                            ],
                        },
                        "orientation": _GLYPH_ORIENTATION,
                        "scaling": _GLYPH_SCALING,
                        "sampling": _GLYPH_SAMPLING,
                    },
                    "required": [
                        "source",
                        "label",
                        "glyph",
                        "orientation",
                        "scaling",
                        "sampling",
                    ],
                    "additionalProperties": False,
                },
            ),
        ),
    )


def register_analyze_post_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(analyze_post_capability_definition())
