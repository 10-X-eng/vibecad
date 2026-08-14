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


DRAWING_PAGE_CAPABILITY_NAME = "drawing.page"
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
}
_FIELD_VALUE = {
    "type": "string",
    "maxLength": MAX_TEMPLATE_FIELD_VALUE_CHARACTERS,
}
_FIELD_UPDATE = _closed(
    {
        "field_name": _FIELD_NAME,
        "expected_value": _FIELD_VALUE,
        "value": _FIELD_VALUE,
    },
    ("field_name", "expected_value", "value"),
)


def drawing_page_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=DRAWING_PAGE_CAPABILITY_NAME,
        description=(
            "Create or redraw one exact Drawing page, edit bounded SVG "
            "template fields, or set its persistent update policy; custom "
            "templates are chosen only by the human."
        ),
        primary_classification="mutation",
        variants=(
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
                parameters=_closed({}, ()),
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
                description=(
                    "Set explicit editable fields on one exact Drawing page; each "
                    "change includes the value observed at turn start."
                ),
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
                            "description": "Explicit desired state; never a toggle.",
                        },
                    },
                    ("page", "keep_updated"),
                ),
            ),
        ),
    )


def register_drawing_page_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(drawing_page_capability_definition())
