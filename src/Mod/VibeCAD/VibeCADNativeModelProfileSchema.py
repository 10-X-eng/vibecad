# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded provider variants for current reusable-profile Design features."""

from __future__ import annotations

from typing import Any

from VibeCADNativeCapabilityRegistry import NativeCapabilityVariant
from VibeCADNativeDesignSchema import (
    LABEL_SCHEMA,
    POSITIVE_MM_SCHEMA,
    SIGNED_MM_SCHEMA,
    design_link_schema,
    global_axis_schema,
    object_reference_schema,
    parameters_schema,
    vector_schema,
)


MODEL_SURFACE = frozenset({"model"})
_PROFILE = design_link_schema(
    "regions",
    r"^InternalFace[1-9][0-9]*$",
    minimum=0,
    maximum=64,
)
_AXIS = design_link_schema(
    "subelements",
    r"^(H_Axis|V_Axis|N_Axis|Edge[1-9][0-9]*|Face[1-9][0-9]*)$",
    minimum=1,
    maximum=1,
)
_FACE = design_link_schema(
    "subelements",
    r"^Face[1-9][0-9]*$",
    minimum=1,
    maximum=1,
)
_SHAPE = design_link_schema(
    "subelements",
    r"^Face[1-9][0-9]*$",
    minimum=0,
    maximum=64,
)
_PATH = design_link_schema(
    "subelements",
    r"^Edge[1-9][0-9]*$",
    minimum=1,
    maximum=64,
)
_TAPER = {
    "type": "number",
    "minimum": -89.0,
    "maximum": 89.0,
}
_ANGLE = {
    "type": "number",
    "exclusiveMinimum": 0.0,
    "maximum": 360.0,
}
_SIGNED_ANGLE = {
    "type": "number",
    "minimum": -89.0,
    "maximum": 89.0,
}


def _exact_kind(kind: str, properties: dict[str, Any]) -> dict[str, Any]:
    fields = {"kind": {"type": "string", "const": kind}, **properties}
    return parameters_schema(fields, tuple(fields))


def _kind_with_optional(
    kind: str,
    required: dict[str, Any],
    optional: dict[str, Any],
) -> dict[str, Any]:
    fields = {"kind": {"type": "string", "const": kind}, **required, **optional}
    return parameters_schema(fields, ("kind", *required))


def _side_termination() -> dict[str, Any]:
    return {
        "oneOf": [
            _exact_kind(
                "length",
                {
                    "length_mm": POSITIVE_MM_SCHEMA,
                    "taper_degrees": _TAPER,
                },
            ),
            _exact_kind("up_to_last", {"offset_mm": SIGNED_MM_SCHEMA}),
            _exact_kind("up_to_first", {"offset_mm": SIGNED_MM_SCHEMA}),
            _exact_kind(
                "up_to_face",
                {"target": _FACE, "offset_mm": SIGNED_MM_SCHEMA},
            ),
            _exact_kind(
                "up_to_shape",
                {"target": _SHAPE, "offset_mm": SIGNED_MM_SCHEMA},
            ),
        ]
    }


def _extrude_extent() -> dict[str, Any]:
    one_side = {
        "type": "array",
        "items": _side_termination(),
        "minItems": 1,
        "maxItems": 1,
    }
    two_sides = {
        "type": "array",
        "items": _side_termination(),
        "minItems": 2,
        "maxItems": 2,
    }
    return {
        "oneOf": [
            _kind_with_optional(
                "one_side",
                {"sides": one_side},
                {"reversed": {"type": "boolean"}},
            ),
            _exact_kind("symmetric", {"sides": one_side}),
            _kind_with_optional(
                "two_sides",
                {"sides": two_sides},
                {"reversed": {"type": "boolean"}},
            ),
        ]
    }


def _focused_side_termination() -> dict[str, Any]:
    return {
        "oneOf": [
            _kind_with_optional(
                "length",
                {"length_mm": POSITIVE_MM_SCHEMA},
                {"taper_degrees": _TAPER},
            ),
            _kind_with_optional(
                "up_to_last",
                {},
                {"offset_mm": SIGNED_MM_SCHEMA},
            ),
            _kind_with_optional(
                "up_to_first",
                {},
                {"offset_mm": SIGNED_MM_SCHEMA},
            ),
            _kind_with_optional(
                "up_to_face",
                {"target": _FACE},
                {"offset_mm": SIGNED_MM_SCHEMA},
            ),
            _kind_with_optional(
                "up_to_shape",
                {"target": _SHAPE},
                {"offset_mm": SIGNED_MM_SCHEMA},
            ),
        ]
    }


def _focused_extrude_extent() -> dict[str, Any]:
    direction = {
        "type": "string",
        "enum": ["forward", "reverse", "symmetric"],
    }
    single_side = _focused_side_termination()["oneOf"]
    return {
        "oneOf": [
            _kind_with_optional(
                branch["properties"]["kind"]["const"],
                {
                    name: schema
                    for name, schema in branch["properties"].items()
                    if name != "kind" and name in branch["required"]
                },
                {
                    **{
                        name: schema
                        for name, schema in branch["properties"].items()
                        if name != "kind" and name not in branch["required"]
                    },
                    "direction": direction,
                },
            )
            for branch in single_side
        ]
        + [
            _kind_with_optional(
                "two_sides",
                {
                    "side1": _focused_side_termination(),
                    "side2": _focused_side_termination(),
                },
                {"reversed": {"type": "boolean"}},
            )
        ]
    }


def _extrude_direction() -> dict[str, Any]:
    return {
        "oneOf": [
            _exact_kind("sketch_normal", {}),
            _exact_kind(
                "reference_axis",
                {
                    "target": _AXIS,
                    "along_sketch_normal": {"type": "boolean"},
                },
            ),
            _exact_kind(
                "custom_vector",
                {
                    "vector": vector_schema(
                        minimum=-1_000_000.0,
                        maximum=1_000_000.0,
                    ),
                    "along_sketch_normal": {"type": "boolean"},
                },
            ),
        ]
    }


def _profile_axis() -> dict[str, Any]:
    return {
        "oneOf": [
            global_axis_schema(),
            _exact_kind(
                "subelement",
                {
                    "object_name": object_reference_schema()["properties"]["object_name"],
                    "subelement": {
                        "type": "string",
                        "maxLength": 64,
                        "pattern": (
                            r"^(?:H_Axis|V_Axis|N_Axis|Axis[0-9]+|"
                            r"Edge[1-9][0-9]*|Face[1-9][0-9]*)$"
                        ),
                    },
                },
            ),
        ]
    }


def _revolve_extent() -> dict[str, Any]:
    direction = {
        "type": "string",
        "enum": ["forward", "reverse"],
    }
    return {
        "oneOf": [
            _kind_with_optional(
                "angle",
                {"angle_degrees": _ANGLE},
                {
                    "direction": {
                        "type": "string",
                        "enum": ["forward", "reverse", "symmetric"],
                    }
                },
            ),
            _exact_kind("up_to_last", {}),
            _kind_with_optional("up_to_first", {}, {"direction": direction}),
            _kind_with_optional(
                "up_to_face",
                {"target": _FACE},
                {"direction": direction},
            ),
            _kind_with_optional(
                "two_angles",
                {
                    "angle1_degrees": _ANGLE,
                    "angle2_degrees": _ANGLE,
                },
                {"direction": direction},
            ),
        ]
    }


def _sweep_options() -> dict[str, Any]:
    orientation = {
        "oneOf": [
            _exact_kind("standard", {}),
            _exact_kind("fixed", {}),
            _exact_kind("frenet", {}),
            _exact_kind(
                "auxiliary",
                {
                    "spine": _PATH,
                    "tangent": {"type": "boolean"},
                    "curvilinear": {"type": "boolean"},
                },
            ),
            _exact_kind(
                "binormal",
                {"vector": vector_schema(minimum=-1.0, maximum=1.0)},
            ),
        ]
    }
    transformation = {
        "oneOf": [
            _exact_kind("constant", {}),
            _exact_kind(
                "multisection",
                {
                    "sections": {
                        "type": "array",
                        "items": _PROFILE,
                        "minItems": 1,
                        "maxItems": 32,
                    }
                },
            ),
            _exact_kind("linear", {}),
            _exact_kind("s_shape", {}),
            _exact_kind("interpolation", {}),
        ]
    }
    return parameters_schema(
        {
            "spine_tangent": {"type": "boolean"},
            "orientation": orientation,
            "transition": {
                "type": "string",
                "enum": ["transformed", "right_corner", "round_corner"],
            },
            "transformation": transformation,
        },
        (
            "spine_tangent",
            "orientation",
            "transition",
            "transformation",
        ),
    )


def _helix_definition() -> dict[str, Any]:
    positive_turns = {
        "type": "number",
        "exclusiveMinimum": 0.0,
        "maximum": 1_000_000.0,
    }
    return {
        "oneOf": [
            _exact_kind(
                "pitch_height_angle",
                {
                    "pitch_mm": POSITIVE_MM_SCHEMA,
                    "height_mm": POSITIVE_MM_SCHEMA,
                    "angle_degrees": _SIGNED_ANGLE,
                },
            ),
            _exact_kind(
                "pitch_turns_angle",
                {
                    "pitch_mm": POSITIVE_MM_SCHEMA,
                    "turns": positive_turns,
                    "angle_degrees": _SIGNED_ANGLE,
                },
            ),
            _exact_kind(
                "height_turns_angle",
                {
                    "height_mm": POSITIVE_MM_SCHEMA,
                    "turns": positive_turns,
                    "angle_degrees": _SIGNED_ANGLE,
                },
            ),
            _exact_kind(
                "height_turns_growth",
                {
                    "height_mm": POSITIVE_MM_SCHEMA,
                    "turns": positive_turns,
                    "growth_mm": SIGNED_MM_SCHEMA,
                },
            ),
        ]
    }


def _body_combination() -> dict[str, Any]:
    return parameters_schema(
        {
            "kind": {
                "type": "string",
                "enum": ["join", "cut", "intersect"],
            },
            "bodies": {
                "type": "array",
                "items": object_reference_schema(),
                "minItems": 1,
                "maxItems": 16,
                "uniqueItems": True,
            },
        },
        ("kind", "bodies"),
    )


def _profile_feature_schemas() -> dict[str, dict[str, Any]]:
    return {
        "extrude": parameters_schema(
            {
                "kind": {"type": "string", "const": "extrude"},
                "direction": _extrude_direction(),
                "extent": _extrude_extent(),
            },
            ("kind", "direction", "extent"),
        ),
        "revolve": parameters_schema(
            {
                "kind": {"type": "string", "const": "revolve"},
                "axis": _profile_axis(),
                "extent": _revolve_extent(),
            },
            ("kind", "axis", "extent"),
        ),
        "loft": parameters_schema(
            {
                "kind": {"type": "string", "const": "loft"},
                "sections": {
                    "type": "array",
                    "items": _PROFILE,
                    "minItems": 1,
                    "maxItems": 32,
                },
                "ruled": {"type": "boolean"},
                "closed": {"type": "boolean"},
            },
            ("kind", "sections", "ruled", "closed"),
        ),
        "sweep": parameters_schema(
            {
                "kind": {"type": "string", "const": "sweep"},
                "path": _PATH,
                "options": _sweep_options(),
            },
            ("kind", "path", "options"),
        ),
        "helix": parameters_schema(
            {
                "kind": {"type": "string", "const": "helix"},
                "axis": _profile_axis(),
                "parameters": _helix_definition(),
                "left_handed": {"type": "boolean"},
                "reversed": {"type": "boolean"},
                "outside": {"type": "boolean"},
                "tolerance": {
                    "type": "number",
                    "minimum": 0.1,
                    "maximum": 1_000_000.0,
                },
            },
            (
                "kind",
                "axis",
                "parameters",
                "left_handed",
                "reversed",
                "outside",
                "tolerance",
            ),
        ),
    }


def focused_model_profile_variant(
    kind: str,
    action_id: str,
) -> NativeCapabilityVariant:
    """Return one direct, operation-specific profile feature contract."""

    feature = _profile_feature_schemas().get(kind)
    if feature is None:
        raise ValueError(f"Unknown profile feature kind {kind!r}")
    if kind == "extrude":
        feature_properties = {
            "extent": _focused_extrude_extent(),
            "direction_axis": _AXIS,
            "direction_vector": vector_schema(
                minimum=-1_000_000.0,
                maximum=1_000_000.0,
            ),
            "align_with_sketch_normal": {"type": "boolean"},
        }
        feature_required = ("extent",)
    else:
        feature_properties = {
            name: schema
            for name, schema in feature["properties"].items()
            if name != "kind"
        }
        feature_required = tuple(
            name for name in feature["required"] if name != "kind"
        )
    return NativeCapabilityVariant(
        operation="create",
        description=f"Create one {kind} Body feature.",
        action_ids=frozenset({action_id}),
        surface_ids=MODEL_SURFACE,
        exact_target_type="DesignResult",
        transaction_behavior="document",
        background_required=False,
        parameters=parameters_schema(
            {
                "label": LABEL_SCHEMA,
                "profile": object_reference_schema(),
                "profile_scope": {
                    "type": "string",
                    "enum": ["entire_sketch", "selected_internal_faces"],
                },
                "internal_faces": {
                    "type": "array",
                    "items": _PROFILE["properties"]["regions"]["items"],
                    "minItems": 1,
                    "maxItems": 64,
                    "uniqueItems": True,
                },
                **feature_properties,
                "combine": _body_combination(),
                "destination_component": object_reference_schema(),
            },
            ("label", "profile", "profile_scope", *feature_required),
        ),
    )


def model_profile_variants() -> tuple[NativeCapabilityVariant, ...]:
    feature = {"oneOf": list(_profile_feature_schemas().values())}
    return (
        NativeCapabilityVariant(
            operation="create",
            description="Create one profile-driven Body feature.",
            action_ids=frozenset(
                {
                    "PartDesign_DesignExtrude",
                    "PartDesign_DesignRevolve",
                    "PartDesign_DesignLoft",
                    "PartDesign_DesignSweep",
                    "PartDesign_DesignHelix",
                }
            ),
            surface_ids=MODEL_SURFACE,
            exact_target_type="DesignResult",
            transaction_behavior="document",
            background_required=False,
            parameters=parameters_schema(
                {
                    "label": LABEL_SCHEMA,
                    "profile": _PROFILE,
                    "feature": feature,
                    "combine": _body_combination(),
                    "destination_component": object_reference_schema(),
                },
                ("label", "profile", "feature"),
            ),
        ),
    )
