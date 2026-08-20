# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contracts for the current Design Hole task surface."""

from __future__ import annotations

from typing import Any

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDesignSchema import (
    LABEL_SCHEMA,
    POSITIVE_MM_SCHEMA,
    SIGNED_MM_SCHEMA,
    object_reference_schema,
    parameters_schema,
)


MODEL_SURFACE = frozenset({"model"})
THREAD_STANDARDS = (
    "ISOMetricProfile",
    "ISOMetricFineProfile",
    "UNC",
    "UNF",
    "UNEF",
    "NPT",
    "BSP",
    "BSW",
    "BSF",
    "ISOTyre",
)
_CATALOG_TEXT = {
    "type": "string",
    "minLength": 1,
    "maxLength": 64,
}
_ANGLE = {
    "type": "number",
    "exclusiveMinimum": 0.0,
    "exclusiveMaximum": 180.0,
}
_PROFILE = object_reference_schema()


def _kinded(kind: str, properties: dict[str, Any]) -> dict[str, Any]:
    fields = {"kind": {"type": "string", "const": kind}, **properties}
    return parameters_schema(fields, tuple(fields))


def _thread_depth() -> dict[str, Any]:
    return {
        "oneOf": [
            _kinded("hole_depth", {}),
            _kinded("dimension", {"depth_mm": POSITIVE_MM_SCHEMA}),
            _kinded("tapped_din76", {}),
        ]
    }


def _hole_type() -> dict[str, Any]:
    standard = {"type": "string", "enum": list(THREAD_STANDARDS)}
    common = {"standard": standard, "size": _CATALOG_TEXT}
    threaded = {
        **common,
        "thread_class": _CATALOG_TEXT,
        "direction": {"type": "string", "enum": ["right", "left"]},
        "thread_depth": _thread_depth(),
    }
    return {
        "oneOf": [
            _kinded("plain", {"diameter_mm": POSITIVE_MM_SCHEMA}),
            _kinded("clearance", {**common, "fit": _CATALOG_TEXT}),
            _kinded("tap_drill", common),
            _kinded("threaded_cosmetic", threaded),
            _kinded(
                "threaded_modeled",
                {
                    **threaded,
                    "custom_clearance_mm": {
                        "oneOf": [SIGNED_MM_SCHEMA, {"type": "null"}],
                    },
                },
            ),
        ]
    }


def _catalog_override() -> dict[str, Any]:
    return {
        "oneOf": [
            {"type": "null"},
            _kinded(
                "counterbore",
                {
                    "diameter_mm": POSITIVE_MM_SCHEMA,
                    "depth_mm": POSITIVE_MM_SCHEMA,
                },
            ),
            _kinded(
                "countersink",
                {
                    "diameter_mm": POSITIVE_MM_SCHEMA,
                    "angle_degrees": _ANGLE,
                },
            ),
        ]
    }


def _head() -> dict[str, Any]:
    return {
        "oneOf": [
            _kinded("none", {}),
            _kinded(
                "counterbore",
                {
                    "diameter_mm": POSITIVE_MM_SCHEMA,
                    "depth_mm": POSITIVE_MM_SCHEMA,
                },
            ),
            _kinded(
                "countersink",
                {
                    "diameter_mm": POSITIVE_MM_SCHEMA,
                    "angle_degrees": _ANGLE,
                },
            ),
            _kinded(
                "counterdrill",
                {
                    "diameter_mm": POSITIVE_MM_SCHEMA,
                    "depth_mm": POSITIVE_MM_SCHEMA,
                    "angle_degrees": _ANGLE,
                },
            ),
            _kinded(
                "catalog",
                {
                    "designation": _CATALOG_TEXT,
                    "override": _catalog_override(),
                },
            ),
        ]
    }


def _depth() -> dict[str, Any]:
    return {
        "oneOf": [
            _kinded("dimension", {"depth_mm": POSITIVE_MM_SCHEMA}),
            _kinded("through_all", {}),
        ]
    }


def _drill_point() -> dict[str, Any]:
    return {
        "oneOf": [
            _kinded("flat", {}),
            _kinded(
                "angled",
                {
                    "angle_degrees": _ANGLE,
                    "depth_reference": {
                        "type": "string",
                        "enum": ["full_diameter", "tip"],
                    },
                },
            ),
        ]
    }


def _taper() -> dict[str, Any]:
    return {
        "oneOf": [
            _kinded("straight", {}),
            _kinded("tapered", {"angle_degrees": _ANGLE}),
        ]
    }


def model_hole_capability_definition() -> NativeCapabilityDefinition:
    targets = {
        "type": "array",
        "items": object_reference_schema(),
        "minItems": 1,
        "maxItems": 16,
        "uniqueItems": True,
    }
    parameters = parameters_schema(
        {
            "label": LABEL_SCHEMA,
            "profile": _PROFILE,
            "base_profile": {
                "type": "string",
                "enum": [
                    "circles_and_arcs",
                    "points_circles_and_arcs",
                    "points",
                ],
            },
            "hole_type": _hole_type(),
            "head": _head(),
            "depth": _depth(),
            "drill_point": _drill_point(),
            "taper": _taper(),
            "reversed": {"type": "boolean"},
            "targets": targets,
            "stage": {
                "type": "string",
                "enum": ["propose", "apply"],
            },
            "preview_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 64,
            },
        },
        (
            "label",
            "profile",
            "base_profile",
            "hole_type",
            "head",
            "depth",
            "drill_point",
            "taper",
            "reversed",
            "targets",
        ),
    )
    return NativeCapabilityDefinition(
        name="model.hole",
        description="Cut profile-driven holes.",
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="hole",
                description="Create one current Design Hole operation.",
                action_ids=frozenset({"PartDesign_Hole"}),
                surface_ids=MODEL_SURFACE,
                exact_target_type="Part::Part2DObject + PartDesign::Body[]",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters,
            ),
        ),
    )


def register_model_hole_capability_definitions(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(model_hole_capability_definition())
