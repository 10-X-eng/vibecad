# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for explicit projected Drawing dimensions."""

from __future__ import annotations

from copy import deepcopy

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDrawingMeasurementAnnotationSchema import drawing_measurement_annotation_variants
from VibeCADNativeDrawingSpecialDimensionSchema import drawing_special_dimension_variants


DRAWING_DIMENSION_OPERATIONS = (
    "create_linear",
    "create_radial",
    "create_angle",
    "create_three_point_angle",
    "create_area",
    "create_view_extent",
    "create_edge_extent",
    "create_axonometric_length",
    "create_chamfer",
    "create_arc_length_dimension",
    "create_area_annotation",
    "create_arc_length_annotation",
    "edit",
)
DRAWING_DIMENSION_CAPABILITY_BY_OPERATION = {
    "create_linear": "drawing.linear_dimension",
    "create_radial": "drawing.radial_dimension",
    "create_angle": "drawing.angle_dimension",
    "create_three_point_angle": "drawing.three_point_angle",
    "create_area": "drawing.area_dimension",
    "create_view_extent": "drawing.view_extent_dimension",
    "create_edge_extent": "drawing.edge_extent_dimension",
    "create_axonometric_length": "drawing.axonometric_dimension",
    "create_chamfer": "drawing.chamfer_dimension",
    "create_arc_length_dimension": "drawing.arc_length_dimension",
    "create_area_annotation": "drawing.area_annotation",
    "create_arc_length_annotation": "drawing.arc_length_annotation",
    "edit": "drawing.edit_dimension",
}
DRAWING_DIMENSION_CAPABILITY_NAMES = tuple(
    DRAWING_DIMENSION_CAPABILITY_BY_OPERATION.values()
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
    "description": "Preferred document label; the result reports the assigned label.",
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
            },
            ("subelement",),
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
_EXTENT_DIRECTION = {
    "type": "string",
    "enum": ["horizontal", "vertical"],
}
_EXTENT_EDGES = {
    "type": "array",
    "items": _EDGE,
    "minItems": 1,
    "maxItems": 64,
    "description": (
        "One to 64 projected edges whose combined horizontal or vertical "
        "bounding extent is measured."
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
    "label_position_on_page_mm": {
        **_LABEL_POSITION,
        "description": "Dimension-label center in page coordinates, in mm.",
    },
}
_COMMON_REQUIRED = (
    "label",
    "page",
    "view",
    "label_position_on_page_mm",
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
                    "One line edge measures its length. Two parallel line edges "
                    "measure perpendicular separation. Two vertices must differ "
                    "on the chosen axis."
                ),
            },
            "direction": {
                "type": "string",
                "enum": ["aligned", "horizontal", "vertical"],
                "description": (
                    "Measurement axis. For one line edge, use aligned or its "
                    "valid_dimensions value. For parallel line edges, use the "
                    "perpendicular axis."
                ),
            },
        },
        (*_COMMON_REQUIRED, "references", "direction"),
    )


def _radial_parameters() -> dict:
    return _closed(
        {
            **_COMMON,
            "edge": _EDGE,
            "allow_approximate": {
                "type": "boolean",
                "default": False,
                "description": "Enable ellipse or circle-like B-spline approximation.",
            },
            "kind": {"type": "string", "enum": ["radius", "diameter"]},
        },
        (*_COMMON_REQUIRED, "edge", "kind"),
    )


def _variant(
    operation: str,
    description: str,
    action_id: str | tuple[str, ...],
    parameters: dict,
    exact_target_type: str,
) -> NativeCapabilityVariant:
    return NativeCapabilityVariant(
        operation=operation,
        description=description,
        action_ids=frozenset(
            (action_id,) if isinstance(action_id, str) else action_id
        ),
        surface_ids=frozenset({"drawing"}),
        exact_target_type=exact_target_type,
        transaction_behavior="document",
        background_required=False,
        parameters=parameters,
    )


def _drawing_dimension_variants() -> tuple[NativeCapabilityVariant, ...]:
    special = {
        variant.operation: variant for variant in drawing_special_dimension_variants()
    }
    annotations = {
        variant.operation: variant
        for variant in drawing_measurement_annotation_variants()
    }
    chamfer_parameters = deepcopy(special["create_horizontal_chamfer"].parameters)
    chamfer_parameters["properties"]["direction"] = {
        "type": "string",
        "enum": ["horizontal", "vertical"],
    }
    chamfer_parameters["required"].append("direction")
    return (
            _variant(
                "create_linear",
                (
                    "Dimension one projected edge, two vertices, or the separation "
                    "between two parallel edges."
                ),
                (
                    "TechDraw_LengthDimension",
                    "TechDraw_HorizontalDimension",
                    "TechDraw_VerticalDimension",
                ),
                _linear_parameters(),
                "ExactDrawingLinearDimensionReferencesAndDirection",
            ),
            _variant(
                "create_radial",
                "Create a radius or diameter from one projected circular edge.",
                ("TechDraw_RadiusDimension", "TechDraw_DiameterDimension"),
                _radial_parameters(),
                "ExactDrawingRadialEdgeAndKind",
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
                "create_view_extent",
                (
                    "Dimension an entire projected view's overall width or height "
                    "without selecting edges."
                ),
                (
                    "TechDraw_HorizontalExtentDimension",
                    "TechDraw_VerticalExtentDimension",
                ),
                _closed(
                    {
                        **_COMMON,
                        "direction": _EXTENT_DIRECTION,
                    },
                    (*_COMMON_REQUIRED, "direction"),
                ),
                "ExactDrawingViewExtentAndDirection",
            ),
            _variant(
                "create_edge_extent",
                "Dimension the combined width or height of a selected subset of projected edges.",
                (
                    "TechDraw_HorizontalExtentDimension",
                    "TechDraw_VerticalExtentDimension",
                ),
                _closed(
                    {
                        **_COMMON,
                        "edges": _EXTENT_EDGES,
                        "direction": _EXTENT_DIRECTION,
                    },
                    (*_COMMON_REQUIRED, "edges", "direction"),
                ),
                "ExactDrawingEdgeExtentAndDirection",
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
                            "description": "The value mode observed at turn start.",
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
            NativeCapabilityVariant(
                operation="create_chamfer",
                description="Create a horizontal or vertical size-and-angle chamfer.",
                action_ids=frozenset(
                    {
                        "TechDraw_ExtensionCreateHorizChamferDimension",
                        "TechDraw_ExtensionCreateVertChamferDimension",
                    }
                ),
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingChamferVerticesAndDirection",
                transaction_behavior="document",
                background_required=False,
                parameters=chamfer_parameters,
            ),
            special["create_arc_length_dimension"],
            annotations["create_area_annotation"],
            annotations["create_arc_length_annotation"],
            NativeCapabilityVariant(
                operation="edit",
                description=(
                    "Replace a dimension's complete display, tolerance, layout, "
                    "and appearance."
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
    )


def drawing_dimension_capability_definitions() -> tuple[NativeCapabilityDefinition, ...]:
    variants = _drawing_dimension_variants()
    if tuple(variant.operation for variant in variants) != DRAWING_DIMENSION_OPERATIONS:
        raise RuntimeError("Drawing dimension operations and focused tools diverged")
    return tuple(
        NativeCapabilityDefinition(
            name=DRAWING_DIMENSION_CAPABILITY_BY_OPERATION[variant.operation],
            description=variant.description,
            primary_classification="mutation",
            variants=(variant,),
        )
        for variant in variants
    )


def register_drawing_dimension_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for definition in drawing_dimension_capability_definitions():
        registry.register_shared_definition(definition)
