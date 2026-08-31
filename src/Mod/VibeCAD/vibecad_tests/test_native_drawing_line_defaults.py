# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused provider contract for current TechDraw line defaults."""

from __future__ import annotations

import json

from VibeCADNativeCapabilityRegistry import NativeCapabilityRegistry
from VibeCADNativeDrawingLineDefaultsBindings import (
    register_drawing_line_defaults_capability_implementation,
)
from VibeCADNativeDrawingLineDefaultsSchema import (
    DRAWING_LINE_DEFAULTS_CAPABILITY_NAME,
    DRAWING_LINE_DEFAULTS_OPERATIONS,
    drawing_line_defaults_capability_definition,
    register_drawing_line_defaults_capability_definition,
)


def test_line_defaults_schema_is_one_argument_free_read() -> None:
    definition = drawing_line_defaults_capability_definition()
    schema = definition.provider_schema(DRAWING_LINE_DEFAULTS_OPERATIONS)
    branches = schema["parameters"]["oneOf"]

    assert DRAWING_LINE_DEFAULTS_OPERATIONS == ("read_current",)
    assert definition.primary_classification == "read"
    assert len(branches) == 1
    branch = branches[0]
    assert branch["type"] == "object"
    assert branch["required"] == []
    assert branch["additionalProperties"] is False
    assert set(branch["properties"]) == {"operation"}
    operation = branch["properties"]["operation"]
    assert operation["type"] == "string"
    assert operation["const"] == "read_current"
    assert operation["description"]

    variant = definition.variants[0]
    assert variant.action_ids == frozenset(
        {"TechDraw_ExtensionSelectLineAttributes"}
    )
    assert variant.surface_ids == frozenset({"drawing"})
    assert variant.exact_target_type == (
        "CurrentTechDrawLineAndPlacementDefaults"
    )
    assert variant.transaction_behavior == "none"
    assert variant.background_required is False
    assert variant.provider_supplemental is False

    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 2 * 1024


def test_line_defaults_registry_has_one_definition_and_implementation() -> None:
    registry = NativeCapabilityRegistry()
    register_drawing_line_defaults_capability_definition(registry)
    register_drawing_line_defaults_capability_implementation(registry)

    assert registry.definition_names == (DRAWING_LINE_DEFAULTS_CAPABILITY_NAME,)
    assert registry.implementation_names == registry.definition_names
