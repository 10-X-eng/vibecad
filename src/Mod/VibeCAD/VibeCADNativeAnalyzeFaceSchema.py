# SPDX-License-Identifier: LGPL-2.1-or-later

"""Single-purpose provider contract for exact FEM source faces."""

from __future__ import annotations

from VibeCADNativeAnalyzeMeshSchema import _SOURCE
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


ANALYZE_FACE_CAPABILITY_NAME = "analyze.faces"


def analyze_face_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ANALYZE_FACE_CAPABILITY_NAME,
        description="Read exact current faces of one active analysis shape.",
        primary_classification="read",
        variants=(
            NativeCapabilityVariant(
                operation="read",
                description="Read one bounded page of exact FaceN geometry.",
                action_ids=frozenset({"VibeCAD_AnalyzeReadGeometrySource"}),
                surface_ids=frozenset({"analyze"}),
                exact_target_type="BoundedExactGeometryFacePage",
                transaction_behavior="none",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "target": _SOURCE,
                        "offset": {
                            "type": "integer",
                            "minimum": 0,
                            "default": 0,
                        },
                        "page_size": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 64,
                            "default": 64,
                        },
                    },
                    "required": ["target"],
                    "additionalProperties": False,
                },
                provider_supplemental=True,
            ),
        ),
    )


def register_analyze_face_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(analyze_face_capability_definition())
