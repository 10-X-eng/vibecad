# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json

from VibeCADNativeModelFeatureSchema import model_feature_capability_definition
from VibeCADNativeModelPrimitiveSchema import model_primitive_capability_definition


EXPECTED_PRIMITIVES = (
    "box",
    "cylinder",
    "sphere",
    "cone",
    "ellipsoid",
    "torus",
    "prism",
    "wedge",
    "tube",
)


def test_model_primitive_owns_every_body_primitive() -> None:
    definition = model_primitive_capability_definition()
    variants = {variant.operation: variant for variant in definition.variants}

    assert definition.name == "model.primitive"
    assert tuple(variants) == EXPECTED_PRIMITIVES
    assert {
        operation: variant.action_ids
        for operation, variant in variants.items()
    } == {
        operation: frozenset({f"PartDesign::Design{operation.title()}"})
        for operation in EXPECTED_PRIMITIVES
    }
    assert all("center_mm" in variant.parameters["required"] for variant in variants.values())


def test_primitive_operations_expose_only_their_dimensions() -> None:
    definition = model_primitive_capability_definition()
    variants = {variant.operation: variant for variant in definition.variants}

    assert variants["box"].parameters["required"] == [
        "label", "center_mm", "length_mm", "width_mm", "height_mm"
    ]
    assert variants["cylinder"].parameters["required"] == [
        "label", "center_mm", "radius_mm", "height_mm"
    ]
    assert variants["tube"].parameters["required"] == [
        "label", "center_mm", "outer_radius_mm", "inner_radius_mm", "height_mm"
    ]
    assert all(
        "rotation" in variant.parameters["properties"]
        and "rotation" not in variant.parameters["required"]
        for variant in variants.values()
    )


def test_model_feature_has_no_primitive_route() -> None:
    operations = tuple(
        variant.operation for variant in model_feature_capability_definition().variants
    )

    assert operations == ("create",)


def test_primitive_provider_contract_is_closed_and_compact() -> None:
    definition = model_primitive_capability_definition()
    schema = definition.provider_schema(EXPECTED_PRIMITIVES)
    encoded = json.dumps(
        schema,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    assert schema["parameters"]["additionalProperties"] is False
    assert schema["parameters"]["properties"]["operation"]["enum"] == list(
        EXPECTED_PRIMITIVES
    )
    assert len(encoded) < 8_000
