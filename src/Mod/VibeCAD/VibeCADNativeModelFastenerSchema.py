# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for standard fasteners on the Model ribbon."""

from __future__ import annotations

from typing import Any

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDesignSchema import (
    LABEL_SCHEMA,
    OBJECT_NAME_SCHEMA,
    POSITIVE_MM_SCHEMA,
    object_reference_schema,
    parameters_schema,
)


_MODEL_SURFACE = frozenset({"model"})
_CATALOG_TEXT = {"type": "string", "maxLength": 128}
_MATCHING_HOLE_TARGETS = {
    "type": "array",
    "items": object_reference_schema(),
    "minItems": 1,
    "maxItems": 16,
    "uniqueItems": True,
}
_ATTACHMENT_HOST = parameters_schema(
    {
        "object_name": OBJECT_NAME_SCHEMA,
        "subelement": {
            "type": "string",
            "maxLength": 64,
            "pattern": r"^Edge[1-9][0-9]*$",
        },
    },
    ("object_name", "subelement"),
)


def standard_fastener_options_schema() -> dict[str, Any]:
    schema = parameters_schema(
        {
            "body_width_code": _CATALOG_TEXT,
            "pitch": _CATALOG_TEXT,
            "thickness_code": _CATALOG_TEXT,
            "slot_width": _CATALOG_TEXT,
            "key_size": _CATALOG_TEXT,
            "blind": {"type": "boolean"},
            "external_diameter_mm": POSITIVE_MM_SCHEMA,
            "thread_length_mm": POSITIVE_MM_SCHEMA,
            "number_of_starts": {
                "type": "integer",
                "minimum": 1,
                "maximum": 4,
            },
        },
        (),
    )
    schema["description"] = (
        "Non-default values listed by model.catalog for this standard and size."
    )
    return schema


def standard_fastener_definition_schema() -> dict[str, Any]:
    fields = {
        "standard": _CATALOG_TEXT,
        "nominal_thread": _CATALOG_TEXT,
        "length_mm": POSITIVE_MM_SCHEMA,
        "model_thread": {"type": "boolean"},
        "left_handed": {"type": "boolean"},
        "catalog_option_overrides": standard_fastener_options_schema(),
    }
    return parameters_schema(
        fields,
        (
            "standard",
            "nominal_thread",
            "model_thread",
            "left_handed",
        ),
    )


def model_fastener_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name="model.fastener",
        description="Create or edit fasteners.",
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="insert_standard_fastener",
                description=(
                    "Insert one catalog-resolved standard fastener as a retained "
                    "Design operation and stable Body."
                ),
                action_ids=frozenset({"VibeCAD_InsertStandardFastener"}),
                surface_ids=_MODEL_SURFACE,
                exact_target_type="ExactCatalogFastener",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {
                        "label": LABEL_SCHEMA,
                        "definition": standard_fastener_definition_schema(),
                    },
                    ("label", "definition"),
                ),
            ),
            NativeCapabilityVariant(
                operation="edit_standard_fastener",
                description=(
                    "Edit one exact retained standard-fastener Body in place "
                    "without replacing its document identities."
                ),
                action_ids=frozenset({"VibeCAD_EditStandardFastener"}),
                surface_ids=_MODEL_SURFACE,
                exact_target_type="PartDesign::Body",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {
                        "target": object_reference_schema(),
                        "label": LABEL_SCHEMA,
                        "definition": standard_fastener_definition_schema(),
                    },
                    ("target", "label", "definition"),
                ),
            ),
            NativeCapabilityVariant(
                operation="create_matching_fastener_hole",
                description=(
                    "Cut one standard-derived through hole from an exact reusable "
                    "Sketch into explicit Bodies."
                ),
                action_ids=frozenset({"VibeCAD_CreateMatchingFastenerHole"}),
                surface_ids=_MODEL_SURFACE,
                exact_target_type=(
                    "PartDesign::Body fastener + Sketcher::SketchObject + "
                    "PartDesign::Body[]"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {
                        "label": LABEL_SCHEMA,
                        "fastener": object_reference_schema(),
                        "profile": object_reference_schema(),
                        "purpose": {
                            "type": "string",
                            "enum": [
                                "clearance",
                                "tapped",
                                "counterbore",
                                "countersink",
                            ],
                        },
                        "fit": {
                            "type": "string",
                            "enum": ["normal", "close", "loose"],
                        },
                        "targets": _MATCHING_HOLE_TARGETS,
                    },
                    (
                        "label",
                        "fastener",
                        "profile",
                        "purpose",
                        "fit",
                        "targets",
                    ),
                ),
            ),
            NativeCapabilityVariant(
                operation="attach_standard_fastener",
                description=(
                    "Align one exact retained standard fastener to one exact "
                    "circular host edge."
                ),
                action_ids=frozenset({"VibeCAD_AttachStandardFastener"}),
                surface_ids=_MODEL_SURFACE,
                exact_target_type="RetainedFastenerBody + DesignBody.CircularEdge",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {
                        "fastener": object_reference_schema(),
                        "host": _ATTACHMENT_HOST,
                    },
                    ("fastener", "host"),
                ),
            ),
        ),
    )


def register_model_fastener_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(model_fastener_capability_definition())
