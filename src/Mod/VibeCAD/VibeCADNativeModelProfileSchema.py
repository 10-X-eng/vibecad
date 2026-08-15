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
    design_result_schema,
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


def _compact_kinded(
    kinds: tuple[str, ...],
    properties: dict[str, Any],
    description: str,
) -> dict[str, Any]:
    schema = parameters_schema(
        {
            "kind": {"type": "string", "enum": list(kinds)},
            **properties,
        },
        ("kind",),
    )
    schema["description"] = description
    return schema


def _exact_kind(kind: str, properties: dict[str, Any]) -> dict[str, Any]:
    fields = {"kind": {"type": "string", "const": kind}, **properties}
    return parameters_schema(fields, tuple(fields))


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
    schema = parameters_schema(
        {
            "kind": {
                "type": "string",
                "enum": ["one_side", "symmetric", "two_sides"],
            },
            "sides": {
                "type": "array",
                "items": _side_termination(),
                "minItems": 1,
                "maxItems": 2,
            },
            "reversed": {"type": "boolean"},
        },
        ("kind", "sides", "reversed"),
    )
    schema["description"] = (
        "one_side/symmetric use one side; two_sides uses two."
    )
    return schema


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


def _revolve_extent() -> dict[str, Any]:
    return _compact_kinded(
        ("angle", "up_to_last", "up_to_first", "up_to_face", "two_angles"),
        {
            "angle_degrees": _ANGLE,
            "angle1_degrees": _ANGLE,
            "angle2_degrees": _ANGLE,
            "target": _FACE,
            "symmetric": {"type": "boolean"},
            "reversed": {"type": "boolean"},
        },
        "Kind fields: angle angle/symmetric/reversed; up_to_last uses kind only; "
        "up_to_first reversed; up_to_face target/reversed; two_angles "
        "angle1/angle2/reversed.",
    )


def _sweep_options() -> dict[str, Any]:
    orientation = _compact_kinded(
        ("standard", "fixed", "frenet", "auxiliary", "binormal"),
        {
            "spine": _PATH,
            "tangent": {"type": "boolean"},
            "curvilinear": {"type": "boolean"},
            "vector": vector_schema(minimum=-1.0, maximum=1.0),
        },
        "Kind fields: standard/fixed/frenet none; auxiliary spine/tangent/curvilinear; "
        "binormal vector.",
    )
    return parameters_schema(
        {
            "spine_tangent": {"type": "boolean"},
            "orientation": orientation,
            "transition": {
                "type": "string",
                "enum": ["transformed", "right_corner", "round_corner"],
            },
            "transformation": {
                "type": "string",
                "enum": ["constant", "multisection", "linear", "s_shape", "interpolation"],
            },
            "sections": {
                "type": "array",
                "items": _PROFILE,
                "minItems": 0,
                "maxItems": 32,
            },
        },
        (
            "spine_tangent",
            "orientation",
            "transition",
            "transformation",
            "sections",
        ),
    )


def _helix_definition() -> dict[str, Any]:
    positive_turns = {
        "type": "number",
        "exclusiveMinimum": 0.0,
        "maximum": 1_000_000.0,
    }
    return _compact_kinded(
        (
            "pitch_height_angle",
            "pitch_turns_angle",
            "height_turns_angle",
            "height_turns_growth",
        ),
        {
            "pitch_mm": POSITIVE_MM_SCHEMA,
            "height_mm": POSITIVE_MM_SCHEMA,
            "turns": positive_turns,
            "angle_degrees": _SIGNED_ANGLE,
            "growth_mm": SIGNED_MM_SCHEMA,
        },
        "Kind fields: pitch_height_angle pitch/height/angle; pitch_turns_angle "
        "pitch/turns/angle; height_turns_angle height/turns/angle; "
        "height_turns_growth height/turns/growth.",
    )


def _profile_definition() -> dict[str, Any]:
    return _compact_kinded(
        ("extrude", "revolve", "loft", "sweep", "helix"),
        {
            "direction": _extrude_direction(),
            "extent": {"anyOf": [_extrude_extent(), _revolve_extent()]},
            "axis": _AXIS,
            "sections": {
                "type": "array",
                "items": _PROFILE,
                "minItems": 1,
                "maxItems": 32,
            },
            "ruled": {"type": "boolean"},
            "closed": {"type": "boolean"},
            "path": _PATH,
            "options": _sweep_options(),
            "parameters": _helix_definition(),
            "left_handed": {"type": "boolean"},
            "reversed": {"type": "boolean"},
            "outside": {"type": "boolean"},
            "tolerance": {
                "type": "number",
                "minimum": 0.1,
                "maximum": 1_000_000.0,
                "description": "Dimensionless PartDesign helix fusion-tolerance multiplier.",
            },
        },
        "Kind fields: extrude direction/extent; revolve axis/extent; loft "
        "sections/ruled/closed; sweep path/options; helix axis/parameters/"
        "left_handed/reversed/outside/tolerance.",
    )


def model_profile_variants() -> tuple[NativeCapabilityVariant, ...]:
    fields = {
        "label": LABEL_SCHEMA,
        "profile": _PROFILE,
        "result": design_result_schema(),
        "definition": _profile_definition(),
    }
    return (
        NativeCapabilityVariant(
            operation="profile",
            description="Create or apply one typed reusable-profile Design feature.",
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
            parameters=parameters_schema(fields, tuple(fields)),
        ),
    )
