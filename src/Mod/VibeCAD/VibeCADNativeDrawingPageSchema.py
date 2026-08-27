# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for Drawing pages and SVG template fields."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDrawingState import (
    MAX_EDITABLE_TEMPLATE_FIELDS,
    MAX_TEMPLATE_FIELD_NAME_CHARACTERS,
    MAX_TEMPLATE_FIELD_VALUE_CHARACTERS,
)
from VibeCADNativeDrawingPage import BUILT_IN_DRAWING_TEMPLATES


DRAWING_PAGE_CAPABILITY_NAMES = (
    "drawing.create_page",
    "drawing.choose_page_template",
    "drawing.template_fields",
    "drawing.redraw_page",
    "drawing.page_updates",
    "drawing.page_readiness",
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


_PAGE_TARGET = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _SHA256,
    },
    ("object_name", "expected_state_sha256"),
)
_FIELD_NAME = {
    "type": "string",
    "minLength": 1,
    "maxLength": MAX_TEMPLATE_FIELD_NAME_CHARACTERS,
    "description": "Exact field_name from page.editable_fields.",
}
_FIELD_VALUE = {
    "type": "string",
    "maxLength": MAX_TEMPLATE_FIELD_VALUE_CHARACTERS,
}
_EXPECTED_FIELD_VALUE = {
    **_FIELD_VALUE,
    "description": "Optional compare-and-set value.",
}
_FIELD_UPDATE = _closed(
    {
        "field_name": _FIELD_NAME,
        "expected_value": _EXPECTED_FIELD_VALUE,
        "value": _FIELD_VALUE,
    },
    ("field_name", "value"),
)


def drawing_page_capability_definitions() -> tuple[NativeCapabilityDefinition, ...]:
    variants = (
            NativeCapabilityVariant(
                operation="page_default",
                description=(
                    "Create one page with the configured default SVG template, "
                    "falling back to VibeCAD's built-in default."
                ),
                action_ids=frozenset({"TechDraw_PageDefault"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type="NewDrawingPageWithConfiguredTemplate",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "template": {
                            "type": "string",
                            "enum": list(BUILT_IN_DRAWING_TEMPLATES),
                            "description": "Named built-in paper, standard, and orientation.",
                        }
                    },
                    (),
                ),
            ),
            NativeCapabilityVariant(
                operation="page_template",
                description=(
                    "Ask the human to choose one SVG template, then create one "
                    "exact page from the authorized content."
                ),
                action_ids=frozenset({"TechDraw_PageTemplate"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type="HumanAuthorizedSvgTemplateForNewDrawingPage",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed({}, ()),
            ),
            NativeCapabilityVariant(
                operation="fill_template_fields",
                description="Set explicit editable fields on one exact Drawing page.",
                action_ids=frozenset({"TechDraw_FillTemplateFields"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingPageAndEditableTemplateFields",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "page": _PAGE_TARGET,
                        "updates": {
                            "type": "array",
                            "items": _FIELD_UPDATE,
                            "minItems": 1,
                            "maxItems": MAX_EDITABLE_TEMPLATE_FIELDS,
                        },
                    },
                    ("page", "updates"),
                ),
            ),
            NativeCapabilityVariant(
                operation="redraw_page",
                description=(
                    "Recompute every active view on one exact page outside the "
                    "UI process, then atomically adopt authenticated view caches."
                ),
                action_ids=frozenset({"TechDraw_RedrawPage"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingPageAndActiveViewGraph",
                transaction_behavior="background",
                background_required=True,
                parameters=_closed({"page": _PAGE_TARGET}, ("page",)),
            ),
            NativeCapabilityVariant(
                operation="set_keep_updated",
                description=(
                    "Set the persistent automatic-update policy explicitly on one "
                    "exact Drawing page."
                ),
                action_ids=frozenset({"TechDrawContextToggleKeepUpdated"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingPageAndUpdatePolicyState",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "page": _PAGE_TARGET,
                        "keep_updated": {
                            "type": "boolean",
                            "description": "Explicit desired update state.",
                        },
                    },
                    ("page", "keep_updated"),
                ),
            ),
            NativeCapabilityVariant(
                operation="inspect_page_readiness",
                description=(
                    "Read rendered bounds, collisions, references, duplicate "
                    "dimensions, template fields, and export readiness for one page."
                ),
                action_ids=frozenset({"VibeCAD_DrawingInspectPageReadiness"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactRenderedDrawingPageReadiness",
                transaction_behavior="none",
                background_required=False,
                parameters=_closed(
                    {
                        "page": _PAGE_TARGET,
                        "offset": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 1_000_000,
                            "default": 0,
                        },
                    },
                    ("page",),
                ),
                provider_supplemental=True,
            ),
    )
    descriptions = (
        "Create a Drawing page with the default or a named built-in template.",
        "Ask the human to choose a template for a new Drawing page.",
        "Set editable fields on one exact Drawing page.",
        "Recompute every active view on one exact Drawing page.",
        "Set automatic updates on one exact Drawing page.",
        "Inspect exact rendered readiness for one Drawing page.",
    )
    classifications = ("mutation",) * 5 + ("read",)
    return tuple(
        NativeCapabilityDefinition(
            name=name,
            description=description,
            primary_classification=classification,
            variants=(variant,),
        )
        for name, description, variant, classification in zip(
            DRAWING_PAGE_CAPABILITY_NAMES,
            descriptions,
            variants,
            classifications,
            strict=True,
        )
    )


def register_drawing_page_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for definition in drawing_page_capability_definitions():
        if definition.name == "drawing.page_readiness":
            registry.register_shared_definition(definition)
        else:
            registry.register_definition(definition)
