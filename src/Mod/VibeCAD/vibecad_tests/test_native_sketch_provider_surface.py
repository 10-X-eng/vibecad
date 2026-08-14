# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json

from VibeCADNativeSketchConstraintSchema import sketch_constraint_capability_definition
from VibeCADNativeSketchGeometrySchema import sketch_geometry_capability_definition
from VibeCADNativeSketchProviderSchema import (
    SKETCH_PROVIDER_CAPABILITY_NAMES,
    sketch_provider_capability_definitions,
)


def _composition_paths(value, path="") -> list[str]:
    result = []
    if isinstance(value, dict):
        for name, item in value.items():
            child = f"{path}.{name}" if path else name
            if name in {"oneOf", "anyOf", "allOf"}:
                result.append(child)
            result.extend(_composition_paths(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            result.extend(_composition_paths(item, f"{path}[{index}]"))
    return result


def test_compact_sketch_surface_covers_every_exact_internal_operation_once() -> None:
    definitions = sketch_provider_capability_definitions()
    assert {definition.name for definition in definitions} == (
        SKETCH_PROVIDER_CAPABILITY_NAMES
    )

    published = [
        variant.operation
        for definition in definitions
        for variant in definition.variants
        if definition.name
        not in {"sketch.batch", "sketch.inspect", "sketch.presentation", "sketch.control"}
    ]
    exact = {
        *(variant.operation for variant in sketch_geometry_capability_definition().variants),
        *(variant.operation for variant in sketch_constraint_capability_definition().variants),
        "trim",
        "split",
        "extend",
        "delete_geometry",
    }
    assert len(published) == len(set(published))
    assert set(published) == exact


def test_compact_sketch_provider_contract_has_no_nested_union_types_or_count_triplets() -> None:
    definitions = sketch_provider_capability_definitions()
    encoded_size = 0
    for definition in definitions:
        schema = definition.provider_schema(
            tuple(variant.operation for variant in definition.variants)
        )
        parameters = schema["parameters"]
        root_single_variant = (
            set(parameters) == {"oneOf"}
            and isinstance(parameters["oneOf"], list)
            and len(parameters["oneOf"]) == 1
        )
        inspected = parameters["oneOf"][0] if root_single_variant else parameters
        assert _composition_paths(inspected) == []
        text = json.dumps(inspected, ensure_ascii=True, separators=(",", ":"))
        assert '"sketch"' not in text
        assert '"expected_geometry_count"' not in text
        assert '"expected_constraint_count"' not in text
        assert '"expected_external_geometry_count"' not in text
        encoded_size += len(text.encode("utf-8"))
    assert encoded_size < 48 * 1024


def test_read_state_bootstraps_revision_and_every_other_sketch_call_requires_it() -> None:
    definitions = sketch_provider_capability_definitions()
    for definition in definitions:
        for variant in definition.variants:
            properties = variant.parameters["properties"]
            required = set(variant.parameters["required"])
            if definition.name == "sketch.inspect" and variant.operation == "read_state":
                assert "revision" not in properties
                assert "revision" not in required
            else:
                assert properties["revision"]["pattern"].startswith("^sketch-v1:")
                assert "revision" in required
