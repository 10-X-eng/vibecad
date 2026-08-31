# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused provider contract for exact Drawing line attributes."""

from __future__ import annotations

import json

from VibeCADNativeCapabilityRegistry import NativeCapabilityRegistry
from VibeCADNativeDrawingLineAttributesBindings import (
    register_drawing_line_attributes_capability_implementation,
)
from VibeCADNativeDrawingLineAttributesSchema import (
    DRAWING_LINE_ATTRIBUTES_CAPABILITY_NAME,
    DRAWING_LINE_ATTRIBUTES_OPERATIONS,
    drawing_line_attributes_capability_definition,
    register_drawing_line_attributes_capability_definition,
)
from vibecad_tests.schema_test_helpers import exact_provider_branches


def test_line_attributes_schema_is_closed_exact_and_bounded() -> None:
    definition = drawing_line_attributes_capability_definition()
    schema = definition.provider_schema(DRAWING_LINE_ATTRIBUTES_OPERATIONS)

    assert DRAWING_LINE_ATTRIBUTES_OPERATIONS == ("set", "read_view")
    assert definition.primary_classification == "mutation"
    assert "oneOf" not in schema["parameters"]
    assert schema["parameters"]["properties"]["operation"]["enum"] == [
        "set",
        "read_view",
    ]
    branches = exact_provider_branches(
        definition, DRAWING_LINE_ATTRIBUTES_OPERATIONS
    )

    set_branch = branches["set"]
    assert set_branch["additionalProperties"] is False
    assert set_branch["properties"]["targets"]["minItems"] == 1
    assert set_branch["properties"]["targets"]["maxItems"] == 32
    target = set_branch["properties"]["targets"]["items"]
    assert len(target["oneOf"]) == 2
    persistent_target, projected_target = target["oneOf"]
    assert persistent_target["additionalProperties"] is False
    assert persistent_target["properties"]["kind"]["enum"] == [
        "cosmetic_edge",
        "centerline",
    ]
    assert projected_target["additionalProperties"] is False
    assert projected_target["properties"]["kind"]["const"] == "projected_edge"
    assert projected_target["properties"]["subelement"]["pattern"].startswith(
        "^Edge"
    )
    attributes = set_branch["properties"]["attributes"]
    assert attributes["additionalProperties"] is False
    assert attributes["properties"]["width_choice"]["enum"] == [
        "thin",
        "middle",
        "thick",
    ]
    for channel in ("red", "green", "blue"):
        color = attributes["properties"]["color_rgb"]["properties"][channel]
        assert color["minimum"] == 0.0
        assert color["maximum"] == 1.0

    read_branch = branches["read_view"]
    assert read_branch["properties"]["offset"]["maximum"] == 512
    assert read_branch["properties"]["page_size"]["maximum"] == 48

    set_variant, read_variant = definition.variants
    assert set_variant.action_ids == frozenset(
        {"TechDraw_ExtensionChangeLineAttributes", "TechDraw_DecorateLine"}
    )
    assert set_variant.exact_target_type == (
        "ExactDrawingLinesAndCompleteFormat"
    )
    assert set_variant.transaction_behavior == "document"
    assert set_variant.provider_supplemental is False
    assert read_variant.transaction_behavior == "none"
    assert read_variant.provider_supplemental is True

    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 8 * 1024


def test_line_attributes_registry_has_one_definition_and_implementation() -> None:
    registry = NativeCapabilityRegistry()
    register_drawing_line_attributes_capability_definition(registry)
    register_drawing_line_attributes_capability_implementation(registry)

    assert registry.definition_names == (DRAWING_LINE_ATTRIBUTES_CAPABILITY_NAME,)
    assert registry.implementation_names == registry.definition_names
