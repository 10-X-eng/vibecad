# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused provider contract for exact Drawing section-view positioning."""

from __future__ import annotations

import json

from VibeCADNativeActionManifest import _plan
from VibeCADNativeCapabilityRegistry import NativeCapabilityRegistry
from VibeCADNativeDrawingSectionPositionBindings import (
    register_drawing_section_position_capability_implementation,
)
from VibeCADNativeDrawingSectionPositionSchema import (
    DRAWING_SECTION_POSITION_CAPABILITY_NAME,
    DRAWING_SECTION_POSITION_OPERATIONS,
    drawing_section_position_capability_definition,
    register_drawing_section_position_capability_definition,
)
from VibeCADRibbonSurface import RibbonAction


def _branch(schema: dict, operation: str) -> dict:
    return next(
        branch
        for branch in schema["parameters"]["oneOf"]
        if branch["properties"]["operation"].get("const") == operation
    )


def test_section_position_schema_is_closed_exact_and_unambiguous() -> None:
    definition = drawing_section_position_capability_definition()
    schema = definition.provider_schema(DRAWING_SECTION_POSITION_OPERATIONS)

    assert DRAWING_SECTION_POSITION_OPERATIONS == (
        "align_axis",
        "align_edge_to_vertex",
    )
    assert definition.primary_classification == "mutation"
    parameters = schema["parameters"]
    assert "oneOf" not in parameters
    assert parameters["additionalProperties"] is False
    assert parameters["required"] == ["operation", "page", "section_view"]
    assert parameters["properties"]["operation"]["enum"] == [
        "align_axis",
        "align_edge_to_vertex",
    ]
    assert set(parameters["properties"]) == {
        "operation",
        "page",
        "section_view",
        "axis",
        "section_edge",
        "base_view",
        "base_vertex",
    }

    exact_schema = {
        "parameters": {
            "oneOf": [
                variant.provider_parameters() for variant in definition.variants
            ]
        }
    }

    axis = _branch(exact_schema, "align_axis")
    assert axis["additionalProperties"] is False
    assert axis["properties"]["axis"]["enum"] == ["horizontal", "vertical"]
    assert axis["required"] == ["operation", "page", "section_view", "axis"]
    assert axis["properties"]["section_view"]["additionalProperties"] is False

    geometry = _branch(exact_schema, "align_edge_to_vertex")
    assert geometry["additionalProperties"] is False
    assert geometry["required"] == [
        "operation",
        "page",
        "section_view",
        "section_edge",
        "base_view",
        "base_vertex",
    ]
    assert geometry["properties"]["section_edge"]["properties"]["name"][
        "pattern"
    ].startswith("^Edge")
    assert geometry["properties"]["base_vertex"]["properties"]["name"][
        "pattern"
    ].startswith("^Vertex")
    assert all(
        value["additionalProperties"] is False
        for name, value in geometry["properties"].items()
        if name not in {"operation"}
    )

    axis_variant, geometry_variant = definition.variants
    assert axis_variant.action_ids == frozenset(
        {"TechDraw_ExtensionPositionSectionView"}
    )
    assert axis_variant.exact_target_type == (
        "ExactDrawingSectionViewAndExplicitBaseAxis"
    )
    assert geometry_variant.exact_target_type == (
        "ExactDrawingSectionEdgeAndBaseVertexAlignment"
    )
    assert all(
        variant.transaction_behavior == "document"
        and not variant.background_required
        for variant in definition.variants
    )

    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 10 * 1024


def test_section_position_action_resolves_to_explicit_axis_variant() -> None:
    plan = _plan(
        "drawing",
        "Attributes",
        RibbonAction(
            command_id="TechDraw_ExtensionPositionSectionView",
            label="Position Section View",
            available=True,
            kind="command",
        ),
    )
    assert (
        plan.capability_family,
        plan.operation_variant,
        plan.exact_target_type,
        plan.transaction_behavior,
        plan.background_required,
    ) == (
        DRAWING_SECTION_POSITION_CAPABILITY_NAME,
        "align_axis",
        "ExactDrawingSectionViewAndExplicitBaseAxis",
        "document",
        False,
    )


def test_section_position_registry_has_definition_and_implementation() -> None:
    registry = NativeCapabilityRegistry()
    register_drawing_section_position_capability_definition(registry)
    register_drawing_section_position_capability_implementation(registry)

    assert registry.definition_names == (
        DRAWING_SECTION_POSITION_CAPABILITY_NAME,
    )
    assert registry.implementation_names == registry.definition_names
