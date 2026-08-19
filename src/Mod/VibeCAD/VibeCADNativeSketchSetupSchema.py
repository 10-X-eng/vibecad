# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact provider contract for reusable Sketch setup operations."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


_SURFACES = frozenset({"sketch.setup"})
_LABEL = {"type": "string", "minLength": 1, "maxLength": 160}
_OBJECT_NAME = {
    "type": "string",
    "maxLength": 128,
    "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
}


def _parameters(
    properties: dict[str, Any],
    required: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": deepcopy(properties),
        "required": list(required),
        "additionalProperties": False,
    }


def _object_ref() -> dict[str, Any]:
    return _parameters({"object_name": _OBJECT_NAME}, ("object_name",))


def _support() -> dict[str, Any]:
    return {
        "oneOf": [
            _parameters(
                {
                    "kind": {"type": "string", "const": "base_plane"},
                    "plane": {"type": "string", "enum": ["XY", "XZ", "YZ"]},
                    "offset_mm": {
                        "type": "number",
                        "minimum": -1_000_000.0,
                        "maximum": 1_000_000.0,
                    },
                },
                ("kind", "plane", "offset_mm"),
            ),
            _parameters(
                {
                    "kind": {"type": "string", "const": "datum_plane"},
                    "target": _object_ref(),
                },
                ("kind", "target"),
            ),
            _parameters(
                {
                    "kind": {"type": "string", "const": "planar_face"},
                    "target": _parameters(
                        {
                            "object_name": _OBJECT_NAME,
                            "subelement": {
                                "type": "string",
                                "maxLength": 32,
                                "pattern": r"^Face[1-9][0-9]*$",
                            },
                        },
                        ("object_name", "subelement"),
                    ),
                },
                ("kind", "target"),
            ),
        ]
    }


def _sources(*, minimum: int) -> dict[str, Any]:
    return {
        "type": "array",
        "items": _object_ref(),
        "minItems": minimum,
        "maxItems": 16,
        "uniqueItems": True,
    }


def _variant(
    operation: str,
    description: str,
    action_id: str,
    parameters: dict[str, Any],
    exact_target_type: str,
) -> NativeCapabilityVariant:
    return NativeCapabilityVariant(
        operation=operation,
        description=description,
        action_ids=frozenset({action_id}),
        surface_ids=_SURFACES,
        exact_target_type=exact_target_type,
        transaction_behavior="document",
        background_required=False,
        parameters=parameters,
    )


def sketch_setup_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name="sketch.setup",
        description=(
            "Attach, reorient, merge, or mirror exact reusable Sketch definitions "
            "without entering edit mode."
        ),
        primary_classification="mutation",
        variants=(
            _variant(
                "map_sketch",
                "Map one closed reusable Sketch to a plane or planar History face.",
                "Sketcher_MapSketch",
                _parameters(
                    {"target": _object_ref(), "support": _support()},
                    ("target", "support"),
                ),
                "ExactReusableSketchAndSupport",
            ),
            _variant(
                "reorient_sketch",
                "Place one closed reusable Sketch on an explicit global plane.",
                "Sketcher_ReorientSketch",
                _parameters(
                    {
                        "target": _object_ref(),
                        "plane": {
                            "type": "string",
                            "enum": ["XY", "XZ", "YZ"],
                        },
                        "offset_mm": {
                            "type": "number",
                            "minimum": -1_000_000.0,
                            "maximum": 1_000_000.0,
                        },
                        "reverse_normal": {"type": "boolean"},
                    },
                    ("target", "plane", "offset_mm", "reverse_normal"),
                ),
                "ExactReusableSketchAndBasePlane",
            ),
            _variant(
                "merge_sketches",
                "Merge reusable Sketches with self-contained geometry.",
                "Sketcher_MergeSketches",
                _parameters(
                    {"sources": _sources(minimum=2), "label": _LABEL},
                    ("sources", "label"),
                ),
                "ExactReusableSketchSet",
            ),
            _variant(
                "mirror_sketch",
                "Create a mirrored reusable Sketch from each self-contained source.",
                "Sketcher_MirrorSketch",
                _parameters(
                    {
                        "sources": _sources(minimum=1),
                        "reference": {
                            "type": "string",
                            "enum": ["x_axis", "y_axis", "origin"],
                        },
                        "label_prefix": _LABEL,
                    },
                    ("sources", "reference", "label_prefix"),
                ),
                "ExactReusableSketchSetAndMirrorReference",
            ),
        ),
    )


def register_sketch_setup_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(sketch_setup_capability_definition())
