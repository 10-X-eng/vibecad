# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded contracts for Model structure and reusable Sketch setup."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


MODEL_SURFACE = frozenset({"model"})
REUSABLE_SKETCH_SURFACES = frozenset({"model", "sketch.setup"})
_LABEL = {
    "type": "string",
    "minLength": 1,
    "maxLength": 160,
}
_OBJECT_NAME = {
    "type": "string",
    "maxLength": 128,
    "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
}
_FACE_NAME = {
    "type": "string",
    "maxLength": 32,
    "pattern": r"^Face[1-9][0-9]*$",
}
_SUBELEMENT_NAME = {
    "type": "string",
    "maxLength": 32,
    "pattern": r"^(?:Face|Edge|Vertex)[1-9][0-9]*$",
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


def _nullable_object_ref() -> dict[str, Any]:
    return {"oneOf": [_object_ref(), {"type": "null"}]}


def _binder_reference() -> dict[str, Any]:
    return _parameters(
        {
            "object_name": _OBJECT_NAME,
            "subelements": {
                "type": "array",
                "items": _SUBELEMENT_NAME,
                "minItems": 0,
                "maxItems": 64,
                "uniqueItems": True,
            },
        },
        ("object_name", "subelements"),
    )


def _sketch_support() -> dict[str, Any]:
    return {
        "oneOf": [
            _parameters(
                {
                    "kind": {"type": "string", "const": "base_plane"},
                    "plane": {
                        "type": "string",
                        "enum": ["XY", "XZ", "YZ"],
                    },
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
                            "subelement": _FACE_NAME,
                        },
                        ("object_name", "subelement"),
                    ),
                },
                ("kind", "target"),
            ),
        ]
    }


def _variant(
    operation: str,
    description: str,
    action_id: str,
    parameters: dict[str, Any],
    *,
    exact_target_type: str | None,
    transaction_behavior: str,
    surface_ids: frozenset[str] = MODEL_SURFACE,
) -> NativeCapabilityVariant:
    return NativeCapabilityVariant(
        operation=operation,
        description=description,
        action_ids=frozenset({action_id}),
        surface_ids=surface_ids,
        exact_target_type=exact_target_type,
        transaction_behavior=transaction_behavior,
        background_required=False,
        parameters=parameters,
    )


def model_structure_capability_definitions() -> tuple[NativeCapabilityDefinition, ...]:
    structure = NativeCapabilityDefinition(
        name="model.structure",
        description="Create exact Design components, Bodies, references, and Body clones.",
        primary_classification="mutation",
        variants=(
            _variant(
                "new_component",
                "Create one physical Design component, optionally nested explicitly.",
                "PartDesign_NewComponent",
                _parameters(
                    {"label": _LABEL, "parent_component": _nullable_object_ref()},
                    ("label", "parent_component"),
                ),
                exact_target_type="PartDesign::Component?",
                transaction_behavior="document",
            ),
            _variant(
                "new_body",
                "Create one empty physical Body in an explicit component or at Design root.",
                "PartDesign_NewBody",
                _parameters(
                    {"label": _LABEL, "component": _nullable_object_ref()},
                    ("label", "component"),
                ),
                exact_target_type="PartDesign::Component?",
                transaction_behavior="document",
            ),
            _variant(
                "sub_shape_binder",
                "Create one global reusable reference from exact current History geometry.",
                "PartDesign_SubShapeBinder",
                _parameters(
                    {
                        "label": _LABEL,
                        "references": {
                            "type": "array",
                            "items": _binder_reference(),
                            "minItems": 1,
                            "maxItems": 32,
                        },
                    },
                    ("label", "references"),
                ),
                exact_target_type="DesignReference[]",
                transaction_behavior="document",
            ),
            _variant(
                "clone",
                "Clone one exact Body History state into one independently identified Body.",
                "PartDesign_Clone",
                _parameters(
                    {
                        "source_body": _object_ref(),
                        "label": _LABEL,
                        "output_body_label": _LABEL,
                    },
                    ("source_body", "label", "output_body_label"),
                ),
                exact_target_type="PartDesign::Body",
                transaction_behavior="document",
            ),
            _variant(
                "separate",
                "Separate every solid in one reusable root definition into a stable Body.",
                "PartDesign_Separate",
                _parameters(
                    {
                        "label": _LABEL,
                        "source": _object_ref(),
                        "destination_component": _nullable_object_ref(),
                    },
                    ("label", "source", "destination_component"),
                ),
                exact_target_type="ReusableMultiSolidDefinitionAndComponent?",
                transaction_behavior="document",
            ),
        ),
    )
    sketch = NativeCapabilityDefinition(
        name="model.sketch",
        description="Create one global reusable Sketch without entering Sketch edit mode.",
        primary_classification="mutation",
        variants=(
            _variant(
                "new_sketch",
                "Create one empty reusable Sketch on an explicit base plane, datum, or face.",
                "Sketcher_NewSketch",
                _parameters(
                    {"label": _LABEL, "support": _sketch_support()},
                    ("label", "support"),
                ),
                exact_target_type="SketchSupport",
                transaction_behavior="document",
                surface_ids=REUSABLE_SKETCH_SURFACES,
            ),
        ),
    )
    validation = NativeCapabilityDefinition(
        name="sketch.validate",
        description="Read exact reusable Sketch readiness without editing or repairing it.",
        primary_classification="read",
        variants=(
            _variant(
                "validate_sketch",
                "Read attachment, profile, constraint, and Design-history readiness.",
                "Sketcher_ValidateSketch",
                _parameters({"target": _object_ref()}, ("target",)),
                exact_target_type="Sketcher::SketchObject",
                transaction_behavior="none",
                surface_ids=REUSABLE_SKETCH_SURFACES,
            ),
        ),
    )
    return structure, sketch, validation


def register_model_structure_capability_definitions(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for definition in model_structure_capability_definitions():
        registry.register_definition(definition)
