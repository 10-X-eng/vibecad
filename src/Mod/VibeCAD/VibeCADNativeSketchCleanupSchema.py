# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused provider contracts for destructive Sketch curve editing."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDesignSchema import (
    OBJECT_NAME_SCHEMA,
    SIGNED_MM_SCHEMA,
    parameters_schema,
)


SKETCH_CUT_CAPABILITY_NAME = "sketch.cut"
SKETCH_EXTEND_CAPABILITY_NAME = "sketch.extend"
SKETCH_DELETE_CAPABILITY_NAME = "sketch.delete"
SKETCH_CLEANUP_CAPABILITY_NAMES = (
    SKETCH_CUT_CAPABILITY_NAME,
    SKETCH_EXTEND_CAPABILITY_NAME,
    SKETCH_DELETE_CAPABILITY_NAME,
)


def _point_2d_schema() -> dict:
    return parameters_schema(
        {"x": SIGNED_MM_SCHEMA, "y": SIGNED_MM_SCHEMA},
        ("x", "y"),
    )


def _active_sketch_parameters(
    properties: dict,
    required: tuple[str, ...],
    *,
    external_count: bool = True,
) -> dict:
    external = (
        {
            "expected_external_geometry_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
        }
        if external_count
        else {}
    )
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
            **external,
            **properties,
        },
        (
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            *(("expected_external_geometry_count",) if external_count else ()),
            *required,
        ),
    )


def _curve_point_parameters() -> dict:
    return _active_sketch_parameters(
        {
            "target": parameters_schema(
                {
                    "geometry_index": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 999_999,
                    },
                    "reference_point_mm": _point_2d_schema(),
                },
                ("geometry_index", "reference_point_mm"),
            ),
        },
        ("target",),
    )


def _extend_parameters() -> dict:
    return _active_sketch_parameters(
        {
            "target": parameters_schema(
                {
                    "geometry_index": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 999_999,
                    },
                    "endpoint": {"type": "string", "enum": ["start", "end"]},
                    "target_point_mm": _point_2d_schema(),
                },
                ("geometry_index", "endpoint", "target_point_mm"),
            ),
        },
        ("target",),
    )


def _delete_parameters() -> dict:
    return _active_sketch_parameters(
        {
            "geometry_indices": {
                "type": "array",
                "items": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 999_999,
                },
                "minItems": 1,
                "maxItems": 64,
                "uniqueItems": True,
            },
        },
        ("geometry_indices",),
        external_count=False,
    )


def sketch_cleanup_capability_definitions() -> tuple[NativeCapabilityDefinition, ...]:
    curve_point = _curve_point_parameters()
    cut = NativeCapabilityDefinition(
        name=SKETCH_CUT_CAPABILITY_NAME,
        description="Cut a curve.",
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="trim",
                description="Trim the selected portion at one exact point on the curve.",
                action_ids=frozenset({"Sketcher_Trimming"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactTrimTargetAndExpectedState",
                transaction_behavior="document",
                background_required=False,
                parameters=curve_point,
            ),
            NativeCapabilityVariant(
                operation="split",
                description="Split one exact curve into two curves at the selected point.",
                action_ids=frozenset({"Sketcher_Split"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactSplitTargetAndExpectedState",
                transaction_behavior="document",
                background_required=False,
                parameters=curve_point,
            ),
        ),
    )
    extend = NativeCapabilityDefinition(
        name=SKETCH_EXTEND_CAPABILITY_NAME,
        description="Extend a curve.",
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="extend",
                description="Extend or shorten one exact endpoint to a projected target point.",
                action_ids=frozenset({"Sketcher_Extend"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactExtendTargetAndExpectedState",
                transaction_behavior="document",
                background_required=False,
                parameters=_extend_parameters(),
            ),
        ),
    )
    delete = NativeCapabilityDefinition(
        name=SKETCH_DELETE_CAPABILITY_NAME,
        description="Delete geometry.",
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="delete_geometry",
                description="Delete one through sixty-four exact ungrouped geometry elements.",
                action_ids=frozenset({"SketchEditDeleteGeometry"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactGeometryDeletionAndExpectedState",
                transaction_behavior="document",
                background_required=False,
                parameters=_delete_parameters(),
            ),
        ),
    )
    return cut, extend, delete


def register_sketch_cleanup_capability_definitions(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for definition in sketch_cleanup_capability_definitions():
        registry.register_definition(definition)
