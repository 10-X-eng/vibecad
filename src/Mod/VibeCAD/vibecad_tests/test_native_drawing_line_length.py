# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused provider contract for symmetric Drawing line resizing."""

from __future__ import annotations

import json

from VibeCADNativeCapabilityRegistry import NativeCapabilityRegistry
from VibeCADNativeDrawingLineLengthBindings import (
    register_drawing_line_length_capability_implementation,
)
from VibeCADNativeDrawingLineLengthSchema import (
    DRAWING_LINE_LENGTH_CAPABILITY_NAME,
    DRAWING_LINE_LENGTH_OPERATIONS,
    drawing_line_length_capability_definition,
    register_drawing_line_length_capability_definition,
)
from vibecad_tests.schema_test_helpers import exact_provider_branches


def test_line_length_schema_is_closed_exact_explicit_and_bounded() -> None:
    definition = drawing_line_length_capability_definition()
    schema = definition.provider_schema(DRAWING_LINE_LENGTH_OPERATIONS)

    assert DRAWING_LINE_LENGTH_OPERATIONS == ("extend", "shorten", "read_view")
    assert definition.primary_classification == "mutation"
    assert "oneOf" not in schema["parameters"]
    assert schema["parameters"]["properties"]["operation"]["enum"] == [
        "extend",
        "shorten",
        "read_view",
    ]
    branches = exact_provider_branches(definition, DRAWING_LINE_LENGTH_OPERATIONS)

    for operation in ("extend", "shorten"):
        branch = branches[operation]
        assert branch["additionalProperties"] is False
        target = branch["properties"]["target"]
        assert target["additionalProperties"] is False
        assert target["properties"]["kind"]["enum"] == [
            "cosmetic_edge",
            "centerline",
        ]
        assert target["properties"]["tag"]["maxLength"] == 36
        delta = branch["properties"]["delta_distance_mm"]
        assert delta["minimum"] == 0.000001
        assert delta["maximum"] == 1_000_000.0

    read_branch = branches["read_view"]
    assert read_branch["properties"]["offset"]["maximum"] == 512
    assert read_branch["properties"]["page_size"]["maximum"] == 48

    extend, shorten, read_view = definition.variants
    assert extend.action_ids == frozenset({"TechDraw_ExtensionExtendLine"})
    assert shorten.action_ids == frozenset({"TechDraw_ExtensionShortenLine"})
    assert extend.exact_target_type == (
        "ExactDrawingStraightPersistentLineAndSymmetricDelta"
    )
    assert shorten.exact_target_type == extend.exact_target_type
    assert extend.transaction_behavior == "document"
    assert shorten.transaction_behavior == "document"
    assert read_view.transaction_behavior == "none"
    assert read_view.provider_supplemental is True

    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 10 * 1024


def test_line_length_registry_has_one_definition_and_implementation() -> None:
    registry = NativeCapabilityRegistry()
    register_drawing_line_length_capability_definition(registry)
    register_drawing_line_length_capability_implementation(registry)

    assert registry.definition_names == (DRAWING_LINE_LENGTH_CAPABILITY_NAME,)
    assert registry.implementation_names == registry.definition_names
