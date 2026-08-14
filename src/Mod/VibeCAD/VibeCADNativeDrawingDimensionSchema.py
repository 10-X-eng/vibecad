# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for explicit projected Drawing dimensions."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDrawingMeasurementAnnotationSchema import (
    DRAWING_MEASUREMENT_ANNOTATION_OPERATIONS,
    drawing_measurement_annotation_variants,
)
from VibeCADNativeDrawingSpecialDimensionSchema import (
    DRAWING_SPECIAL_DIMENSION_OPERATIONS,
    drawing_special_dimension_variants,
)


DRAWING_DIMENSION_CAPABILITY_NAME = "drawing.dimension"
DRAWING_GENERAL_DIMENSION_OPERATIONS = (
    "create_length",
    "create_horizontal",
    "create_vertical",
    "create_radius",
    "create_diameter",
    "create_angle",
    "create_three_point_angle",
    "create_area",
    "create_horizontal_extent",
    "create_vertical_extent",
    "create_axonometric_length",
)
DRAWING_DIMENSION_OPERATIONS = (
    *DRAWING_GENERAL_DIMENSION_OPERATIONS,
    *DRAWING_SPECIAL_DIMENSION_OPERATIONS,
    *DRAWING_MEASUREMENT_ANNOTATION_OPERATIONS,
    "edit",
)
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


def _closed(properties: dict, required: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_PAGE = _closed(
    {"object_name": _OBJECT_NAME, "expected_state_sha256": _SHA256},
    ("object_name", "expected_state_sha256"),
)
_VIEW = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _SHA256,
        "expected_projection_state_sha256": _SHA256,
    },
    (
        "object_name",
        "expected_state_sha256",
        "expected_projection_state_sha256",
    ),
)
_LABEL = {
    "type": "string",
    "minLength": 1,
    "maxLength": 160,
    "description": (
        "Preferred document label. FreeCAD may replace or append a trailing "
        "numeric suffix when the label must be unique; the result reports the "
        "exact assigned label."
    ),
}
_LABEL_POSITION = _closed(
    {
        "x_mm": {"type": "number", "minimum": -10_000.0, "maximum": 10_000.0},
        "y_mm": {"type": "number", "minimum": -10_000.0, "maximum": 10_000.0},
    },
    ("x_mm", "y_mm"),
)
_DIMENSION_EDIT_TARGET = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_edit_state_sha256": _SHA256,
    },
    ("object_name", "expected_edit_state_sha256"),
)
_FORMAT_SPEC = {"type": "string", "maxLength": 512}
_DIMENSION_EDIT_DISPLAY = _closed(
    {
        "format_spec": {
            **_FORMAT_SPEC,
            "description": (
                "Complete displayed value text. Unless arbitrary is true, include "
                "one numeric placeholder such as %f, %.2f, %g, %w, or %r."
            ),
        },
        "arbitrary": {"type": "boolean"},
    },
    ("format_spec", "arbitrary"),
)
_DIMENSION_EDIT_TOLERANCE = _closed(
    {
        "unit": {"type": "string", "enum": ["mm", "degrees"]},
        "theoretical_exact": {"type": "boolean"},
        "equal": {"type": "boolean"},
        "over": {"type": "number", "minimum": -1_000_000.0, "maximum": 1_000_000.0},
        "under": {"type": "number", "minimum": -1_000_000.0, "maximum": 1_000_000.0},
        "arbitrary": {"type": "boolean"},
        "over_format_spec": _FORMAT_SPEC,
        "under_format_spec": _FORMAT_SPEC,
    },
    (
        "unit",
        "theoretical_exact",
        "equal",
        "over",
        "under",
        "arbitrary",
        "over_format_spec",
        "under_format_spec",
    ),
)
_DIMENSION_EDIT_LAYOUT = _closed(
    {
        "label_position_in_view_mm": _LABEL_POSITION,
        "angle_override": {"type": "boolean"},
        "line_angle_degrees": {
            "type": "number",
            "minimum": -360.0,
            "maximum": 360.0,
        },
        "extension_angle_degrees": {
            "type": "number",
            "minimum": -360.0,
            "maximum": 360.0,
        },
    },
    (
        "label_position_in_view_mm",
        "angle_override",
        "line_angle_degrees",
        "extension_angle_degrees",
    ),
)
_DIMENSION_EDIT_APPEARANCE = _closed(
    {
        "flip_arrowheads": {"type": "boolean"},
        "color_rgb": _closed(
            {
                "red": {"type": "integer", "minimum": 0, "maximum": 255},
                "green": {"type": "integer", "minimum": 0, "maximum": 255},
                "blue": {"type": "integer", "minimum": 0, "maximum": 255},
            },
            ("red", "green", "blue"),
        ),
        "font_size_mm": {
            "type": "number",
            "exclusiveMinimum": 0.0,
            "maximum": 1_000.0,
        },
        "standard_and_style": {
            "type": "string",
            "enum": [
                "iso_oriented",
                "iso_referencing",
                "asme_inlined",
                "asme_referencing",
            ],
        },
    },
    ("flip_arrowheads", "color_rgb", "font_size_mm", "standard_and_style"),
)


def _element(kind: str, description: str) -> dict:
    return {
        **_closed(
            {
                "subelement": {
                    "type": "string",
                    "pattern": rf"^{kind}(0|[1-9][0-9]*)$",
                    "maxLength": 32,
                },
                "expected_element_state_sha256": _SHA256,
            },
            ("subelement", "expected_element_state_sha256"),
        ),
        "description": description,
    }


_LINEAR_REFERENCE = _element(
    "(?:Edge|Vertex)",
    "One exact projected EdgeN or VertexN from the target view.",
)
_EDGE = _element("Edge", "One exact projected EdgeN from the target view.")
_VERTEX = _element("Vertex", "One exact projected VertexN from the target view.")
_FACE = _element("Face", "One exact projected FaceN from the target view.")
_EXTENT_TARGET = {
    "oneOf": [
        _closed(
            {
                "scope": {
                    "type": "string",
                    "const": "whole_view",
                    "description": "Measure the complete projected view extent.",
                }
            },
            ("scope",),
        ),
        _closed(
            {
                "scope": {"type": "string", "const": "edges"},
                "edges": {
                    "type": "array",
                    "items": _EDGE,
                    "minItems": 1,
                    "maxItems": 64,
                    "description": (
                        "Unique exact projected EdgeN references whose combined "
                        "overall extent will be measured."
                    ),
                },
            },
            ("scope", "edges"),
        ),
    ],
    "description": (
        "A closed discriminated target: the complete projected view, or one "
        "to sixty-four exact projected edges."
    ),
}
_AXONOMETRIC_MEASUREMENT = {
    "oneOf": [
        _closed(
            {
                "kind": {"type": "string", "const": "edge"},
                "dimension_edge": _EDGE,
            },
            ("kind", "dimension_edge"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "vertex_pair"},
                "first_vertex": _VERTEX,
                "second_vertex": _VERTEX,
                "dimension_direction_edge": _EDGE,
            },
            (
                "kind",
                "first_vertex",
                "second_vertex",
                "dimension_direction_edge",
            ),
        ),
    ],
    "description": (
        "Measure one exact projected edge, or measure between two exact "
        "vertices while a separate exact edge defines the dimension-line direction."
    ),
}


_COMMON = {
    "label": _LABEL,
    "page": _PAGE,
    "view": _VIEW,
    "label_position_in_view_mm": {
        **_LABEL_POSITION,
        "description": (
            "Dimension-label center in the projected view coordinate system: "
            "+X right and +Y up, in scaled view millimetres."
        ),
    },
}
_COMMON_REQUIRED = (
    "label",
    "page",
    "view",
    "label_position_in_view_mm",
)


def _linear_parameters() -> dict:
    return _closed(
        {
            **_COMMON,
            "references": {
                "type": "array",
                "items": _LINEAR_REFERENCE,
                "minItems": 1,
                "maxItems": 2,
                "description": (
                    "One edge measures that edge; two edges or vertices measure "
                    "between the exact projected references."
                ),
            },
        },
        (*_COMMON_REQUIRED, "references"),
    )


def _radial_parameters() -> dict:
    return _closed(
        {
            **_COMMON,
            "edge": _EDGE,
            "allow_approximate": {
                "type": "boolean",
                "description": (
                    "Must be true to accept an ellipse or circle-like B-spline. "
                    "Exact circles do not require approximation."
                ),
            },
        },
        (*_COMMON_REQUIRED, "edge", "allow_approximate"),
    )


def _variant(
    operation: str,
    description: str,
    action_id: str,
    parameters: dict,
    exact_target_type: str,
) -> NativeCapabilityVariant:
    return NativeCapabilityVariant(
        operation=operation,
        description=description,
        action_ids=frozenset({action_id}),
        surface_ids=frozenset({"drawing"}),
        exact_target_type=exact_target_type,
        transaction_behavior="document",
        background_required=False,
        parameters=parameters,
    )


def drawing_dimension_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=DRAWING_DIMENSION_CAPABILITY_NAME,
        description=(
            "Create explicit projected TechDraw dimensions from hash-pinned "
            "semantic references, or replace one exact dimension's complete edit state."
        ),
        primary_classification="mutation",
        variants=(
            _variant(
                "create_length",
                "Create a projected aligned length from one edge or one/two references.",
                "TechDraw_LengthDimension",
                _linear_parameters(),
                "ExactDrawingAlignedDimensionReferences",
            ),
            _variant(
                "create_horizontal",
                "Create a projected horizontal distance from one or two references.",
                "TechDraw_HorizontalDimension",
                _linear_parameters(),
                "ExactDrawingHorizontalDimensionReferences",
            ),
            _variant(
                "create_vertical",
                "Create a projected vertical distance from one or two references.",
                "TechDraw_VerticalDimension",
                _linear_parameters(),
                "ExactDrawingVerticalDimensionReferences",
            ),
            _variant(
                "create_radius",
                "Create a projected radius from one circular or explicitly accepted approximate edge.",
                "TechDraw_RadiusDimension",
                _radial_parameters(),
                "ExactDrawingRadialEdge",
            ),
            _variant(
                "create_diameter",
                "Create a projected diameter from one circular or explicitly accepted approximate edge.",
                "TechDraw_DiameterDimension",
                _radial_parameters(),
                "ExactDrawingRadialEdge",
            ),
            _variant(
                "create_angle",
                "Create the angle between two exact projected edges.",
                "TechDraw_AngleDimension",
                _closed(
                    {
                        **_COMMON,
                        "first_edge": _EDGE,
                        "second_edge": _EDGE,
                    },
                    (*_COMMON_REQUIRED, "first_edge", "second_edge"),
                ),
                "ExactDrawingTwoEdgeAngle",
            ),
            _variant(
                "create_three_point_angle",
                (
                    "Create an angle from three projected points ordered as "
                    "first arm point, apex, then second arm point."
                ),
                "TechDraw_3PtAngleDimension",
                _closed(
                    {
                        **_COMMON,
                        "first_arm_point": _VERTEX,
                        "apex_point": _VERTEX,
                        "second_arm_point": _VERTEX,
                    },
                    (
                        *_COMMON_REQUIRED,
                        "first_arm_point",
                        "apex_point",
                        "second_arm_point",
                    ),
                ),
                "ExactDrawingOrderedThreePointAngle",
            ),
            _variant(
                "create_area",
                "Create a projected area dimension from one exact projected face.",
                "TechDraw_AreaDimension",
                _closed(
                    {**_COMMON, "face": _FACE},
                    (*_COMMON_REQUIRED, "face"),
                ),
                "ExactDrawingProjectedFace",
            ),
            _variant(
                "create_horizontal_extent",
                "Create the overall horizontal extent of a whole view or exact edge subset.",
                "TechDraw_HorizontalExtentDimension",
                _closed(
                    {**_COMMON, "extent": _EXTENT_TARGET},
                    (*_COMMON_REQUIRED, "extent"),
                ),
                "ExactDrawingHorizontalExtentTarget",
            ),
            _variant(
                "create_vertical_extent",
                "Create the overall vertical extent of a whole view or exact edge subset.",
                "TechDraw_VerticalExtentDimension",
                _closed(
                    {**_COMMON, "extent": _EXTENT_TARGET},
                    (*_COMMON_REQUIRED, "extent"),
                ),
                "ExactDrawingVerticalExtentTarget",
            ),
            _variant(
                "create_axonometric_length",
                (
                    "Create an axonometric projected length with exact dimension and "
                    "extension directions and an explicitly predicted value mode."
                ),
                "TechDraw_AxoLengthDimension",
                _closed(
                    {
                        **_COMMON,
                        "measurement": _AXONOMETRIC_MEASUREMENT,
                        "extension_direction_edge": _EDGE,
                        "expected_value_mode": {
                            "type": "string",
                            "enum": [
                                "projected",
                                "x_axis_true_length",
                                "y_axis_true_length",
                                "z_axis_true_length",
                            ],
                            "description": (
                                "The value mode observed before mutation. Native refuses "
                                "the call if current axis classification differs."
                            ),
                        },
                    },
                    (
                        *_COMMON_REQUIRED,
                        "measurement",
                        "extension_direction_edge",
                        "expected_value_mode",
                    ),
                ),
                "ExactDrawingAxonometricMeasurementDirectionsAndValueMode",
            ),
            *drawing_special_dimension_variants(),
            *drawing_measurement_annotation_variants(),
            NativeCapabilityVariant(
                operation="edit",
                description=(
                    "Replace one hash-pinned dimension's complete display, tolerance, "
                    "layout, and appearance state without opening its human task dialog."
                ),
                action_ids=frozenset({"TechDrawContextEditDimension"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingDimensionAndCompleteEditState",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "dimension": _DIMENSION_EDIT_TARGET,
                        "display": _DIMENSION_EDIT_DISPLAY,
                        "tolerance": _DIMENSION_EDIT_TOLERANCE,
                        "layout": _DIMENSION_EDIT_LAYOUT,
                        "appearance": _DIMENSION_EDIT_APPEARANCE,
                    },
                    ("dimension", "display", "tolerance", "layout", "appearance"),
                ),
                provider_supplemental=True,
            ),
        ),
    )


def register_drawing_dimension_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(drawing_dimension_capability_definition())
