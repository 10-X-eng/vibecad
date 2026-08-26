# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused provider contract for exact Native Drawing dimension repair."""

from __future__ import annotations

import json

from VibeCADNativeCapabilityRegistry import NativeCapabilityRegistry
from VibeCADNativeDrawingDimensionRepair import _spec
from VibeCADNativeDrawingDimensionRepairBindings import (
    register_drawing_dimension_repair_capability_implementation,
)
from VibeCADNativeDrawingDimensionRepairSchema import (
    DRAWING_DIMENSION_REPAIR_CAPABILITY_NAME,
    DRAWING_DIMENSION_REPAIR_OPERATIONS,
    drawing_dimension_repair_capability_definition,
    register_drawing_dimension_repair_capability_definition,
)


_REPAIR_KINDS = {
    "length",
    "horizontal",
    "vertical",
    "radius",
    "diameter",
    "angle",
    "three_point_angle",
    "area",
    "horizontal_extent",
    "vertical_extent",
    "horizontal_chamfer",
    "vertical_chamfer",
    "arc_length",
    "axonometric_length",
}


def test_dimension_repair_schema_is_one_closed_exact_operation() -> None:
    definition = drawing_dimension_repair_capability_definition()
    schema = definition.provider_schema(DRAWING_DIMENSION_REPAIR_OPERATIONS)
    branches = schema["parameters"]["oneOf"]

    assert definition.preserve_operation_branches is False
    assert DRAWING_DIMENSION_REPAIR_OPERATIONS == ("repair_references",)
    assert len(branches) == 1
    operation = branches[0]
    assert operation["properties"]["operation"]["type"] == "string"
    assert operation["properties"]["operation"]["const"] == "repair_references"
    assert operation["additionalProperties"] is False
    assert set(operation["required"]) == {
        "dimension",
        "page",
        "view",
        "replacement",
    }

    dimension = operation["properties"]["dimension"]
    assert dimension["additionalProperties"] is False
    assert dimension["required"] == [
        "object_name",
        "expected_repair_state_sha256",
    ]
    assert dimension["properties"]["expected_repair_state_sha256"]["pattern"] == (
        "^[0-9a-f]{64}$"
    )

    replacement = operation["properties"]["replacement"]
    repair_branches = {
        branch["properties"]["kind"]["const"]: branch
        for branch in replacement["oneOf"]
    }
    assert set(repair_branches) == _REPAIR_KINDS
    assert all(
        branch["additionalProperties"] is False
        and "kind" in branch["required"]
        for branch in repair_branches.values()
    )
    assert repair_branches["radius"]["required"] == ["kind", "edge"]
    assert repair_branches["radius"]["properties"]["allow_approximate"] == {
        "type": "boolean",
        "default": False,
    }
    assert repair_branches["three_point_angle"]["required"] == [
        "kind",
        "first_arm_point",
        "apex_point",
        "second_arm_point",
    ]
    assert repair_branches["axonometric_length"]["required"] == [
        "kind",
        "measurement",
        "extension_direction_edge",
        "expected_value_mode",
    ]

    variant = definition.variants[0]
    assert variant.action_ids == frozenset({"TechDraw_DimensionRepair"})
    assert variant.surface_ids == frozenset({"drawing"})
    assert variant.exact_target_type == (
        "ExactDrawingDimensionAndReplacementReferences"
    )
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False
    assert variant.provider_supplemental is False

    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "path" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 24 * 1024


def test_radial_dimension_repair_defaults_to_exact_geometry() -> None:
    spec = _spec({"kind": "diameter", "edge": {"subelement": "Edge9"}})
    assert spec.kind == "diameter"
    assert spec.allow_approximate is False


def test_dimension_repair_registry_has_one_definition_and_implementation() -> None:
    registry = NativeCapabilityRegistry()
    register_drawing_dimension_repair_capability_definition(registry)
    register_drawing_dimension_repair_capability_implementation(registry)

    assert registry.definition_names == (
        DRAWING_DIMENSION_REPAIR_CAPABILITY_NAME,
    )
    assert registry.implementation_names == registry.definition_names
