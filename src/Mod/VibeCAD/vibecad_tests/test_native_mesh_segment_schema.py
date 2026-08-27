# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused provider contracts for Mesh combine and separation."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityRegistry,
    provider_visible_native_schema,
)
from VibeCADNativeMeshSegmentRuntime import _focused_segment_arguments
from VibeCADNativeMeshSegmentSchema import (
    MESH_COMBINE_CAPABILITY_NAME,
    MESH_SEGMENT_CAPABILITY_NAME,
    MESH_SEPARATE_CAPABILITY_NAME,
    register_mesh_segment_capability_definition,
)


def _branch(registry: NativeCapabilityRegistry, name: str) -> dict:
    definition = registry.definition(name)
    assert definition is not None and len(definition.variants) == 1
    operation = definition.variants[0].operation
    return provider_visible_native_schema(
        definition.provider_schema((operation,))
    )["parameters"]["oneOf"][0]


def test_combine_and_separate_publish_one_obvious_target_contract() -> None:
    registry = NativeCapabilityRegistry()
    register_mesh_segment_capability_definition(registry)

    combine = _branch(registry, MESH_COMBINE_CAPABILITY_NAME)
    assert set(combine["properties"]) == {"sources", "result_label"}
    assert combine["required"] == ["sources"]
    assert combine["properties"]["sources"]["minItems"] == 2

    separate = _branch(registry, MESH_SEPARATE_CAPABILITY_NAME)
    assert set(separate["properties"]) == {"target", "result_label_prefix"}
    assert separate["required"] == ["target"]

    legacy = registry.definition(MESH_SEGMENT_CAPABILITY_NAME)
    assert legacy is not None
    variants = {variant.operation: variant for variant in legacy.variants}
    assert variants["merge"].parameters["required"] == ["sources", "result_label"]
    assert variants["split_components"].parameters["required"] == [
        "target",
        "result_label_prefix",
    ]


def test_focused_combine_and_separate_supply_neutral_labels() -> None:
    source = {"object_name": "Mesh", "expected_state_sha256": "0" * 64}
    assert _focused_segment_arguments(
        MESH_COMBINE_CAPABILITY_NAME,
        {"operation": "merge", "sources": [source, source]},
    ) == {
        "operation": "merge",
        "sources": [source, source],
        "result_label": "Combined Mesh",
    }
    assert _focused_segment_arguments(
        MESH_SEPARATE_CAPABILITY_NAME,
        {"operation": "split_components", "target": source},
    ) == {
        "operation": "split_components",
        "target": source,
        "result_label_prefix": "Component",
    }
