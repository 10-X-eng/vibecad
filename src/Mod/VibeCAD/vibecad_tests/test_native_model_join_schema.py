# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json

from VibeCADNativeModelJoinSchema import model_join_capability_definition


def _branch(operation: str):
    definition = model_join_capability_definition()
    schema = definition.provider_schema((operation,))
    branch = schema["parameters"]["oneOf"][0]
    return definition, schema, branch, branch["properties"]["definition"]


def test_join_contract_maps_each_live_leaf_to_one_exact_variant() -> None:
    definition = model_join_capability_definition()

    assert definition.name == "model.join"
    assert tuple(variant.operation for variant in definition.variants) == (
        "connect",
        "embed",
        "cutout",
    )
    assert tuple(variant.action_ids for variant in definition.variants) == (
        frozenset({"Part_JoinConnect"}),
        frozenset({"Part_JoinEmbed"}),
        frozenset({"Part_JoinCutout"}),
    )
    assert all(variant.surface_ids == frozenset({"model"}) for variant in definition.variants)
    assert all(variant.transaction_behavior == "document" for variant in definition.variants)
    assert all(not variant.background_required for variant in definition.variants)


def test_connect_schema_is_closed_bounded_and_exposes_durable_controls() -> None:
    _definition, _schema, branch, join = _branch("connect")

    assert branch["required"] == ["label", "definition"]
    assert branch["additionalProperties"] is False
    assert branch["properties"]["operation"]["const"] == "connect"
    assert join["required"] == ["sources", "refine", "tolerance_mm"]
    assert join["additionalProperties"] is False
    assert set(join["properties"]) == {"sources", "refine", "tolerance_mm"}
    sources = join["properties"]["sources"]
    assert (sources["minItems"], sources["maxItems"], sources["uniqueItems"]) == (
        1,
        32,
        True,
    )
    assert sources["items"]["required"] == ["object_name"]
    assert join["properties"]["tolerance_mm"] == {
        "type": "number",
        "minimum": 0,
        "maximum": 1_000_000,
    }


def test_embed_and_cutout_use_an_ordered_exact_base_and_tool() -> None:
    for operation in ("embed", "cutout"):
        _definition, _schema, branch, join = _branch(operation)
        assert branch["properties"]["operation"]["const"] == operation
        assert join["required"] == ["base", "tool", "refine", "tolerance_mm"]
        assert join["additionalProperties"] is False
        assert set(join["properties"]) == {
            "base",
            "tool",
            "refine",
            "tolerance_mm",
        }
        assert join["properties"]["base"]["required"] == ["object_name"]
        assert join["properties"]["tool"]["required"] == ["object_name"]


def test_complete_join_schema_uses_the_compact_closed_multi_operation_encoding() -> None:
    schema = model_join_capability_definition().provider_schema(
        ("connect", "embed", "cutout")
    )
    parameters = schema["parameters"]
    encoded = json.dumps(
        schema,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    serialized = encoded.decode("utf-8")

    assert parameters["additionalProperties"] is False
    assert parameters["properties"]["operation"]["enum"] == [
        "connect",
        "embed",
        "cutout",
    ]
    assert set(parameters["properties"]) == {
        "operation",
        "label",
        "definition",
    }
    assert parameters["required"] == ["operation", "label", "definition"]
    join = parameters["properties"]["definition"]
    assert join["additionalProperties"] is False
    assert set(join["properties"]) == {
        "sources",
        "base",
        "tool",
        "refine",
        "tolerance_mm",
    }
    assert join["required"] == ["refine", "tolerance_mm"]
    assert join["description"] == (
        "Fields: connect=sources; embed|cutout=base,tool."
    )
    assert len(encoded) < 2_100
    for forbidden in ("selection", "runCommand", "workbench", "ribbon"):
        assert forbidden not in serialized
