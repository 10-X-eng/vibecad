# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed provider schemas for exact curve-relation constraints."""

from __future__ import annotations

from VibeCADNativeDesignSchema import OBJECT_NAME_SCHEMA, parameters_schema
from VibeCADNativeSketchConstraintSchemaCommon import element_schema


def equal_parameters() -> dict:
    return parameters_schema(
        {
            "sketch": parameters_schema(
                {"object_name": OBJECT_NAME_SCHEMA},
                ("object_name",),
            ),
            "expected_geometry_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            "expected_constraint_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            "expected_external_geometry_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            "selection": {
                "type": "array",
                "items": element_schema(("whole",)),
                "minItems": 2,
                "maxItems": 17,
            },
        },
        (
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "selection",
        ),
    )


def block_parameters() -> dict:
    return parameters_schema(
        {
            "sketch": parameters_schema(
                {"object_name": OBJECT_NAME_SCHEMA},
                ("object_name",),
            ),
            "expected_geometry_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            "expected_constraint_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            "expected_external_geometry_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            "selection": {
                "type": "array",
                "items": element_schema(("whole",)),
                "minItems": 1,
                "maxItems": 16,
            },
        },
        (
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "selection",
        ),
    )


def group_parameters() -> dict:
    return parameters_schema(
        {
            "sketch": parameters_schema(
                {"object_name": OBJECT_NAME_SCHEMA},
                ("object_name",),
            ),
            "expected_geometry_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            "expected_constraint_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            "expected_external_geometry_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            "selection": {
                "type": "array",
                "items": element_schema(("whole",)),
                "minItems": 2,
                "maxItems": 16,
            },
        },
        (
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "selection",
        ),
    )


def _constraint_toggle_parameters(expected_state_field: str) -> dict:
    return parameters_schema(
        {
            "sketch": parameters_schema(
                {"object_name": OBJECT_NAME_SCHEMA},
                ("object_name",),
            ),
            "expected_geometry_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            "expected_constraint_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            "expected_external_geometry_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            "targets": {
                "type": "array",
                "items": parameters_schema(
                    {
                        "constraint_index": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 999_999,
                        },
                        expected_state_field: {"type": "boolean"},
                    },
                    ("constraint_index", expected_state_field),
                ),
                "minItems": 1,
                "maxItems": 16,
                "uniqueItems": True,
            },
        },
        (
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "targets",
        ),
    )


def driving_parameters() -> dict:
    return _constraint_toggle_parameters("expected_driving")


def active_parameters() -> dict:
    return _constraint_toggle_parameters("expected_active")


def _perpendicular_target_schema() -> dict:
    curve = element_schema(("whole",))
    endpoint = element_schema(("start", "end"))
    point = element_schema(("start", "end", "center"))
    return {
        "oneOf": [
            parameters_schema(
                {
                    "form": {"type": "string", "const": "curve_curve"},
                    "first_curve": curve,
                    "second_curve": curve,
                },
                ("form", "first_curve", "second_curve"),
            ),
            parameters_schema(
                {
                    "form": {"type": "string", "const": "endpoint_curve"},
                    "endpoint": endpoint,
                    "curve": curve,
                },
                ("form", "endpoint", "curve"),
            ),
            parameters_schema(
                {
                    "form": {"type": "string", "const": "endpoint_endpoint"},
                    "first_endpoint": endpoint,
                    "second_endpoint": endpoint,
                },
                ("form", "first_endpoint", "second_endpoint"),
            ),
            parameters_schema(
                {
                    "form": {"type": "string", "const": "point_pair_line"},
                    "first_point": endpoint,
                    "second_point": endpoint,
                    "line": curve,
                },
                ("form", "first_point", "second_point", "line"),
            ),
            parameters_schema(
                {
                    "form": {"type": "string", "const": "curves_via_point"},
                    "first_curve": curve,
                    "second_curve": curve,
                    "point": point,
                },
                ("form", "first_curve", "second_curve", "point"),
            ),
        ]
    }


def perpendicular_parameters() -> dict:
    return parameters_schema(
        {
            "sketch": parameters_schema(
                {"object_name": OBJECT_NAME_SCHEMA},
                ("object_name",),
            ),
            "expected_geometry_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            "expected_constraint_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            "expected_external_geometry_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            "target": _perpendicular_target_schema(),
        },
        (
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "target",
        ),
    )


def _tangent_target_schema() -> dict:
    curve = element_schema(("whole",))
    endpoint = element_schema(("start", "end"))
    point = element_schema(("start", "end", "center"))
    constraint_index = {"type": "integer", "minimum": 0, "maximum": 999_999}
    return {
        "oneOf": [
            parameters_schema(
                {
                    "form": {"type": "string", "const": "curve_curve"},
                    "first_curve": curve,
                    "second_curve": curve,
                },
                ("form", "first_curve", "second_curve"),
            ),
            parameters_schema(
                {
                    "form": {"type": "string", "const": "endpoint_curve"},
                    "endpoint": endpoint,
                    "curve": curve,
                },
                ("form", "endpoint", "curve"),
            ),
            parameters_schema(
                {
                    "form": {"type": "string", "const": "endpoint_endpoint"},
                    "first_endpoint": endpoint,
                    "second_endpoint": endpoint,
                },
                ("form", "first_endpoint", "second_endpoint"),
            ),
            parameters_schema(
                {
                    "form": {"type": "string", "const": "curves_via_point"},
                    "first_curve": curve,
                    "second_curve": curve,
                    "point": point,
                },
                ("form", "first_curve", "second_curve", "point"),
            ),
            parameters_schema(
                {
                    "form": {
                        "type": "string",
                        "const": "replace_with_endpoint_curve",
                    },
                    "constraint_index": constraint_index,
                    "endpoint": endpoint,
                    "curve": curve,
                },
                ("form", "constraint_index", "endpoint", "curve"),
            ),
            parameters_schema(
                {
                    "form": {
                        "type": "string",
                        "const": "replace_with_endpoint_endpoint",
                    },
                    "constraint_index": constraint_index,
                    "first_endpoint": endpoint,
                    "second_endpoint": endpoint,
                },
                (
                    "form",
                    "constraint_index",
                    "first_endpoint",
                    "second_endpoint",
                ),
            ),
        ]
    }


def tangent_parameters() -> dict:
    return parameters_schema(
        {
            "sketch": parameters_schema(
                {"object_name": OBJECT_NAME_SCHEMA},
                ("object_name",),
            ),
            "expected_geometry_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            "expected_constraint_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            "expected_external_geometry_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            "target": _tangent_target_schema(),
        },
        (
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "target",
        ),
    )


def _symmetric_target_schema() -> dict:
    point = element_schema(("start", "end", "center"))
    line = element_schema(("whole",))
    curve = element_schema(("whole",))
    return {
        "oneOf": [
            parameters_schema(
                {
                    "form": {"type": "string", "const": "points_about_line"},
                    "first_point": point,
                    "second_point": point,
                    "symmetry_line": line,
                },
                ("form", "first_point", "second_point", "symmetry_line"),
            ),
            parameters_schema(
                {
                    "form": {"type": "string", "const": "points_about_point"},
                    "first_point": point,
                    "second_point": point,
                    "symmetry_point": point,
                },
                ("form", "first_point", "second_point", "symmetry_point"),
            ),
            parameters_schema(
                {
                    "form": {"type": "string", "const": "curve_about_line"},
                    "curve": curve,
                    "symmetry_line": line,
                },
                ("form", "curve", "symmetry_line"),
            ),
            parameters_schema(
                {
                    "form": {"type": "string", "const": "curve_about_point"},
                    "curve": curve,
                    "symmetry_point": point,
                },
                ("form", "curve", "symmetry_point"),
            ),
        ]
    }


def symmetric_parameters() -> dict:
    return parameters_schema(
        {
            "sketch": parameters_schema(
                {"object_name": OBJECT_NAME_SCHEMA},
                ("object_name",),
            ),
            "expected_geometry_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            "expected_constraint_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            "expected_external_geometry_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            "target": _symmetric_target_schema(),
        },
        (
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "target",
        ),
    )
