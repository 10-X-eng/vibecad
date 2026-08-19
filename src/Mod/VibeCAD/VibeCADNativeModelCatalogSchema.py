# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded discovery contracts shared by Native Model operations."""

from __future__ import annotations

from typing import Any

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDesignSchema import POSITIVE_MM_SCHEMA, parameters_schema
from VibeCADNativeModelHoleSchema import THREAD_STANDARDS


_MODEL_SURFACE = frozenset({"model"})
_FASTENER_SURFACES = frozenset({"model", "assemble"})
_CATALOG_TEXT = {"type": "string", "maxLength": 128}


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"oneOf": [schema, {"type": "null"}]}


def model_catalog_capability_definition() -> NativeCapabilityDefinition:
    hole_standard = _nullable(
        {"type": "string", "enum": list(THREAD_STANDARDS)}
    )
    fastener_parameters = {
        "query": {"type": "string", "maxLength": 256},
        "family": _nullable(_CATALOG_TEXT),
        "standard": _nullable(_CATALOG_TEXT),
        "nominal_thread": _nullable(_CATALOG_TEXT),
        "length_mm": _nullable(POSITIVE_MM_SCHEMA),
        "limit": {"type": "integer", "minimum": 1, "maximum": 25},
    }
    return NativeCapabilityDefinition(
        name="model.catalog",
        description="Read fastener catalogs.",
        primary_classification="read",
        variants=(
            NativeCapabilityVariant(
                operation="hole_threads",
                description="List Hole standards or the sizes for one standard.",
                action_ids=frozenset({"VibeCAD_NativeHoleCatalog"}),
                surface_ids=_MODEL_SURFACE,
                exact_target_type=None,
                transaction_behavior="none",
                background_required=False,
                parameters=parameters_schema(
                    {"standard": hole_standard},
                    ("standard",),
                ),
            ),
            NativeCapabilityVariant(
                operation="fasteners",
                description=(
                    "Search exact standards, sizes, lengths, options, and "
                    "constructor values in the bundled fastener catalog."
                ),
                action_ids=frozenset({"VibeCAD_NativeFastenerCatalog"}),
                surface_ids=_FASTENER_SURFACES,
                exact_target_type=None,
                transaction_behavior="none",
                background_required=False,
                parameters=parameters_schema(
                    fastener_parameters,
                    tuple(fastener_parameters),
                ),
            ),
        ),
    )


def register_model_catalog_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_shared_definition(model_catalog_capability_definition())
