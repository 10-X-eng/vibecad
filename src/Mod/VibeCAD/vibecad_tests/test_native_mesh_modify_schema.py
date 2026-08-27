# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused provider contracts for retained Mesh modifications."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityRegistry,
    provider_visible_native_schema,
)
from VibeCADNativeMeshModifySchema import (
    MESH_DECIMATE_CAPABILITY_NAME,
    MESH_FILL_HOLES_CAPABILITY_NAME,
    MESH_MODIFY_CAPABILITY_NAME,
    MESH_REPAIR_CAPABILITY_NAME,
    MESH_SMOOTH_CAPABILITY_NAME,
    register_mesh_modify_capability_definition,
)
from VibeCADNativeMeshModifyRuntime import _focused_modify_arguments


def _branch(registry: NativeCapabilityRegistry, name: str) -> dict:
    definition = registry.definition(name)
    assert definition is not None and len(definition.variants) == 1
    operation = definition.variants[0].operation
    return provider_visible_native_schema(
        definition.provider_schema((operation,))
    )["parameters"]["oneOf"][0]


def test_focused_smoothing_and_decimation_publish_flat_typed_fields() -> None:
    registry = NativeCapabilityRegistry()
    register_mesh_modify_capability_definition(registry)

    smooth = _branch(registry, MESH_SMOOTH_CAPABILITY_NAME)
    assert set(smooth["properties"]) == {
        "targets",
        "method",
        "iterations",
        "lambda",
        "mu",
    }
    assert smooth["required"] == ["targets", "method", "iterations"]
    assert smooth["properties"]["method"]["enum"] == [
        "taubin",
        "laplace",
        "median",
    ]

    decimate = _branch(registry, MESH_DECIMATE_CAPABILITY_NAME)
    assert set(decimate["properties"]) == {
        "targets",
        "mode",
        "target_facet_count",
        "reduction_percent",
        "tolerance_mm",
    }
    assert decimate["required"] == ["targets", "mode"]
    assert decimate["properties"]["mode"]["enum"] == [
        "target_facets",
        "percentage",
    ]

    legacy = registry.definition(MESH_MODIFY_CAPABILITY_NAME)
    assert legacy is not None
    variants = {variant.operation: variant for variant in legacy.variants}
    assert "settings" in variants["smooth"].parameters["properties"]
    assert "settings" in variants["decimate"].parameters["properties"]

    repair = _branch(registry, MESH_REPAIR_CAPABILITY_NAME)
    assert set(repair["properties"]) == {"targets", "defects", "max_iterations"}
    assert repair["required"] == ["targets", "defects"]
    assert repair["properties"]["defects"]["items"]["enum"] == [
        "non_uniform_orientation",
        "duplicated_facets",
        "duplicated_points",
        "non_manifold_edges",
        "non_manifold_points",
        "facet_indices_out_of_range",
        "point_indices_out_of_range",
        "corrupted_facets",
        "invalid_neighbourhood",
        "degenerated_facets",
        "self_intersections",
        "surface_folds",
        "boundary_folds",
    ]

    fill_holes = _branch(registry, MESH_FILL_HOLES_CAPABILITY_NAME)
    assert set(fill_holes["properties"]) == {
        "targets",
        "maximum_boundary_edges",
    }
    assert fill_holes["required"] == ["targets", "maximum_boundary_edges"]


def test_focused_fields_lower_to_the_shared_retained_operations() -> None:
    target = {"object_name": "Mesh", "label": "Result"}
    assert _focused_modify_arguments(
        MESH_SMOOTH_CAPABILITY_NAME,
        {
            "operation": "smooth",
            "targets": [target],
            "method": "laplace",
            "iterations": 4,
            "lambda": 0.5,
        },
    ) == {
        "operation": "smooth",
        "targets": [target],
        "settings": {"method": "laplace", "iterations": 4, "lambda": 0.5},
    }
    assert _focused_modify_arguments(
        MESH_DECIMATE_CAPABILITY_NAME,
        {
            "operation": "decimate",
            "targets": [target],
            "mode": "target_facets",
            "target_facet_count": 100,
        },
    ) == {
        "operation": "decimate",
        "targets": [target],
        "settings": {"mode": "target_facets", "target_facet_count": 100},
    }
    assert _focused_modify_arguments(
        MESH_REPAIR_CAPABILITY_NAME,
        {
            "operation": "repair",
            "targets": [target],
            "defects": ["duplicated_facets", "non_manifold_edges"],
        },
    ) == {
        "operation": "repair",
        "targets": [target],
        "settings": {
            "repairs": ["duplicates", "non_manifold_topology"],
            "maximum_boundary_edges": 0,
            "max_iterations": 1,
        },
    }
    assert _focused_modify_arguments(
        MESH_FILL_HOLES_CAPABILITY_NAME,
        {
            "operation": "fill_holes",
            "targets": [target],
            "maximum_boundary_edges": 12,
        },
    ) == {
        "operation": "fill_holes",
        "targets": [target],
        "maximum_boundary_edges": 12,
    }
