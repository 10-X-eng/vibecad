# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp Native contracts for durable FEM result visualizations."""

from __future__ import annotations

from VibeCADNativeAnalyzeModelSchema import _ANALYSIS_TARGET
from VibeCADNativeAnalyzeResultState import RESULT_TARGET
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


ANALYZE_VISUALIZATION_CAPABILITY_NAME = "analyze.visualization"

_TEXT = {"type": "string", "minLength": 1, "maxLength": 160}
_OPTIONAL_TEXT = {"type": "string", "maxLength": 160}
_COMPONENT = {
    "type": "string",
    "enum": ["scalar", "x", "y", "z", "xx", "yy", "zz", "xy", "xz", "yz"],
}
_FIELD_SELECTOR = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "const": "field"},
        "name": _TEXT,
        "component": _COMPONENT,
    },
    "required": ["kind", "name", "component"],
    "additionalProperties": False,
}
_POSITION_SELECTOR = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "const": "position"},
        "component": {"type": "string", "enum": ["x", "y", "z"]},
    },
    "required": ["kind", "component"],
    "additionalProperties": False,
}
_POINT_INDEX_SELECTOR = {
    "type": "object",
    "properties": {"kind": {"type": "string", "const": "point_index"}},
    "required": ["kind"],
    "additionalProperties": False,
}
_X_SELECTOR = {"oneOf": [_POINT_INDEX_SELECTOR, _POSITION_SELECTOR, _FIELD_SELECTOR]}
_Y_SELECTOR = {"oneOf": [_POSITION_SELECTOR, _FIELD_SELECTOR]}
_POINT_INDEX = {
    "type": "integer",
    "minimum": 0,
    "maximum": 2_147_483_647,
    "description": "Zero-based point index on the exact post source.",
}

_DATA_1D = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "const": "field"},
                "value": _X_SELECTOR,
                "all_frames": {"type": "boolean"},
                "series_name": _TEXT,
            },
            "required": ["mode", "value", "all_frames", "series_name"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "const": "point_over_frames"},
                "point_index": _POINT_INDEX,
                "value": _X_SELECTOR,
                "series_name": _TEXT,
            },
            "required": ["mode", "point_index", "value", "series_name"],
            "additionalProperties": False,
        },
    ]
}

_DATA_2D = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "const": "field"},
                "x": _X_SELECTOR,
                "y": _Y_SELECTOR,
                "all_frames": {"type": "boolean"},
                "series_name": _TEXT,
            },
            "required": ["mode", "x", "y", "all_frames", "series_name"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "const": "point_over_frames"},
                "point_index": _POINT_INDEX,
                "y": _Y_SELECTOR,
                "series_name": _TEXT,
            },
            "required": ["mode", "point_index", "y", "series_name"],
            "additionalProperties": False,
        },
    ]
}

_LEGEND = {
    "type": "object",
    "properties": {
        "show": {"type": "boolean"},
        "location": {
            "type": "string",
            "enum": [
                "best",
                "upper right",
                "upper left",
                "lower left",
                "lower right",
                "right",
                "center left",
                "center right",
                "lower center",
                "upper center",
                "center",
            ],
        },
    },
    "required": ["show", "location"],
    "additionalProperties": False,
}

_HISTOGRAM_VIEW = {
    "type": "object",
    "properties": {
        "bins": {"type": "integer", "minimum": 1, "maximum": 10_000},
        "type": {
            "type": "string",
            "enum": ["bar", "barstacked", "step", "stepfilled"],
        },
        "cumulative": {"type": "boolean"},
        "bar_width": {
            "type": "number",
            "exclusiveMinimum": 0.0,
            "maximum": 1.0,
            "description": "Dimensionless histogram bar-width fraction.",
        },
        "hatch_line_width": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 99.0,
            "description": "VTK display line width in device-independent pixels.",
        },
        "title": _OPTIONAL_TEXT,
        "x_label": _OPTIONAL_TEXT,
        "y_label": _OPTIONAL_TEXT,
        "legend": _LEGEND,
    },
    "required": [
        "bins",
        "type",
        "cumulative",
        "bar_width",
        "hatch_line_width",
        "title",
        "x_label",
        "y_label",
        "legend",
    ],
    "additionalProperties": False,
}

_LINE_VIEW = {
    "type": "object",
    "properties": {
        "scale": {"type": "string", "enum": ["linear", "log_x", "log_y", "log_xy"]},
        "grid": {"type": "boolean"},
        "title": _OPTIONAL_TEXT,
        "x_label": _OPTIONAL_TEXT,
        "y_label": _OPTIONAL_TEXT,
        "legend": _LEGEND,
    },
    "required": ["scale", "grid", "title", "x_label", "y_label", "legend"],
    "additionalProperties": False,
}


def _parameters(data: dict, view: dict | None = None) -> dict:
    properties = {
        "analysis": _ANALYSIS_TARGET,
        "source": RESULT_TARGET,
        "label": _TEXT,
        "data": data,
    }
    required = ["analysis", "source", "label", "data"]
    if view is not None:
        properties["view"] = view
        required.append("view")
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def analyze_visualization_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ANALYZE_VISUALIZATION_CAPABILITY_NAME,
        description=(
            "Create durable FEM tables and plots from exact typed post-result data."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="create_table",
                description=(
                    "Create a durable table root and one exact 1D extractor without "
                    "returning raw result arrays."
                ),
                action_ids=frozenset({"FEM_PostVisualizationTable"}),
                surface_ids=frozenset({"analyze"}),
                exact_target_type="ExactAnalysisPostSourceExtractionAndHistory",
                transaction_behavior="document",
                background_required=False,
                parameters=_parameters(_DATA_1D),
            ),
            NativeCapabilityVariant(
                operation="create_histogram",
                description=(
                    "Create a durable histogram root, exact 1D extractor, and bounded "
                    "plot presentation."
                ),
                action_ids=frozenset({"FEM_PostVisualizationHistogram"}),
                surface_ids=frozenset({"analyze"}),
                exact_target_type="ExactAnalysisPostSourceExtractionAndHistory",
                transaction_behavior="document",
                background_required=False,
                parameters=_parameters(_DATA_1D, _HISTOGRAM_VIEW),
            ),
            NativeCapabilityVariant(
                operation="create_line_plot",
                description=(
                    "Create a durable line-plot root, exact paired 2D extractor, and "
                    "bounded plot presentation."
                ),
                action_ids=frozenset({"FEM_PostVisualizationLineplot"}),
                surface_ids=frozenset({"analyze"}),
                exact_target_type="ExactAnalysisPostSourceExtractionAndHistory",
                transaction_behavior="document",
                background_required=False,
                parameters=_parameters(_DATA_2D, _LINE_VIEW),
            ),
        ),
    )


def register_analyze_visualization_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(analyze_visualization_capability_definition())
