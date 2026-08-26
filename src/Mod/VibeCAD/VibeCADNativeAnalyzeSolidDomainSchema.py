# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused provider contract for multipart solid analysis domains."""

from __future__ import annotations

from VibeCADNativeAnalyzeModelSchema import _LABEL, _OBJECT_NAME
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


ANALYZE_SOLID_DOMAIN = "analyze.solid_domain"


def analyze_solid_domain_capability_definition() -> NativeCapabilityDefinition:
    description = "Create one meshable domain from separate solid objects."
    return NativeCapabilityDefinition(
        name=ANALYZE_SOLID_DOMAIN,
        description=description,
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="create",
                description=description,
                action_ids=frozenset({"VibeCAD_AnalyzeCreateSolidDomain"}),
                surface_ids=frozenset({"analyze"}),
                exact_target_type="CurrentSolidGeometrySources",
                transaction_behavior="document",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "source_names": {
                            "type": "array",
                            "items": _OBJECT_NAME,
                            "minItems": 2,
                            "maxItems": 256,
                            "uniqueItems": True,
                            "description": "Separate solid object names.",
                        },
                        "interface_mode": {
                            "type": "string",
                            "enum": ["separate", "shared"],
                            "description": (
                                "separate retains faces for tie or contact; shared "
                                "creates conformal bonded interfaces."
                            ),
                        },
                        "label": {**_LABEL, "default": "Solid analysis domain"},
                    },
                    "required": ["source_names", "interface_mode"],
                    "additionalProperties": False,
                },
                provider_supplemental=True,
            ),
        ),
    )


def register_analyze_solid_domain_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(analyze_solid_domain_capability_definition())
