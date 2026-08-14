# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for Drawing format customization."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDrawingFormatState import MAX_DRAWING_FORMAT_CHARACTERS


DRAWING_FORMAT_CAPABILITY_NAME = "drawing.format"
DRAWING_FORMAT_OPERATIONS = (
    "set_dimension_format",
    "set_balloon_text",
    "apply_iso_286_fit",
)
_ACTION = frozenset({"TechDraw_ExtensionCustomizeFormat"})
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


_TARGET = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_format_state_sha256": _SHA256,
    },
    ("object_name", "expected_format_state_sha256"),
)
_VALUE = {
    "type": "string",
    "maxLength": MAX_DRAWING_FORMAT_CHARACTERS,
}


def drawing_format_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=DRAWING_FORMAT_CAPABILITY_NAME,
        description=(
            "Replace the complete host-validated format of one exact Drawing "
            "dimension or the literal text of one exact Balloon."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="set_dimension_format",
                description=(
                    "Replace one hash-pinned dimension FormatSpec. TechDraw "
                    "validates the complete format and derives its preview."
                ),
                action_ids=_ACTION,
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingDimensionAndCompleteFormat",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "dimension": _TARGET,
                        "format_spec": {
                            **_VALUE,
                            "description": (
                                "Complete replacement format. Use one numeric "
                                "placeholder such as %f, %.2f, %g, %w, or %r."
                            ),
                        },
                    },
                    ("dimension", "format_spec"),
                ),
            ),
            NativeCapabilityVariant(
                operation="set_balloon_text",
                description=(
                    "Replace the literal text of one hash-pinned Balloon, "
                    "including a host-measured annotation Balloon."
                ),
                action_ids=_ACTION,
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingBalloonAndLiteralText",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "balloon": _TARGET,
                        "text": {
                            **_VALUE,
                            "description": "Complete literal replacement text.",
                        },
                    },
                    ("balloon", "text"),
                ),
                provider_supplemental=True,
            ),
            NativeCapabilityVariant(
                operation="apply_iso_286_fit",
                description=(
                    "Apply one explicit ISO 286 tolerance class to an exact "
                    "length or diameter dimension and derive its limits."
                ),
                action_ids=frozenset({"TechDraw_HoleShaftFit"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingDimensionAndIso286ToleranceClass",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "dimension": _TARGET,
                        "tolerance_class": {
                            "type": "string",
                            "enum": [
                                "c11", "f7", "h6", "h7", "h9", "k6",
                                "n6", "r6", "s6", "D10", "E9", "F8",
                                "G7", "H7", "H8", "H11", "K7", "N7",
                                "R7", "S7",
                            ],
                        },
                    },
                    ("dimension", "tolerance_class"),
                ),
            ),
        ),
    )


def register_drawing_format_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(drawing_format_capability_definition())
