# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json

from VibeCADNativeModelBooleanSchema import model_boolean_capability_definition


def _section_schema_parts():
    definition = model_boolean_capability_definition()
    schema = definition.provider_schema(("section",))
    branch = schema["parameters"]["oneOf"][0]
    return definition, schema, branch, branch["properties"]["definition"]


def _combine_schema_parts():
    definition = model_boolean_capability_definition()
    schema = definition.provider_schema(("combine",))
    branch = schema["parameters"]["oneOf"][0]
    return definition, schema, branch, branch["properties"]["definition"]


def _split_schema_parts():
    definition = model_boolean_capability_definition()
    schema = definition.provider_schema(("split",))
    branch = schema["parameters"]["oneOf"][0]
    return definition, schema, branch, branch["properties"]["definition"]


def test_part_section_contract_matches_the_live_immediate_command() -> None:
    definition, _schema, branch, section = _section_schema_parts()

    assert definition.name == "model.boolean"
    assert branch["properties"]["operation"]["const"] == "section"
    variant = definition.variants[0]
    assert variant.action_ids == frozenset({"Part_Section"})
    assert variant.surface_ids == frozenset({"model"})
    assert variant.exact_target_type == "TwoOrderedExactCurrentShapes"
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False
    assert branch["required"] == ["label", "definition"]
    assert branch["additionalProperties"] is False
    assert section["required"] == ["operands"]
    assert section["additionalProperties"] is False
    assert set(section["properties"]) == {"operands"}


def test_part_section_operands_are_ordered_exact_and_compact() -> None:
    _definition, schema, _branch, section = _section_schema_parts()

    operands = section["properties"]["operands"]
    assert (operands["minItems"], operands["maxItems"], operands["uniqueItems"]) == (
        2,
        2,
        True,
    )
    item = operands["items"]
    assert item["required"] == ["object_name"]
    assert item["additionalProperties"] is False
    assert set(item["properties"]) == {"object_name"}
    encoded = json.dumps(
        schema,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) < 1_200


def test_design_combine_contract_covers_every_human_result_mode() -> None:
    definition, schema, branch, combine = _combine_schema_parts()

    variant = next(item for item in definition.variants if item.operation == "combine")
    assert variant.action_ids == frozenset({"PartDesign_Combine"})
    assert variant.exact_target_type == "ResultBodyAndOrderedToolBodies"
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False
    assert branch["properties"]["operation"]["const"] == "combine"
    assert branch["required"] == ["label", "definition"]
    assert combine["additionalProperties"] is False
    assert combine["required"] == [
        "mode",
        "source_body",
        "tool_bodies",
        "keep_tools",
    ]
    assert set(combine["properties"]) == {
        "mode",
        "source_body",
        "tool_bodies",
        "keep_tools",
    }
    assert combine["properties"]["mode"]["enum"] == [
        "join",
        "cut",
        "intersect",
    ]
    tools = combine["properties"]["tool_bodies"]
    assert (tools["minItems"], tools["maxItems"], tools["uniqueItems"]) == (
        1,
        15,
        True,
    )
    encoded = json.dumps(
        schema,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) < 1_500


def test_design_split_contract_matches_the_live_identity_safe_task() -> None:
    definition, schema, branch, split = _split_schema_parts()
    variant = next(item for item in definition.variants if item.operation == "split")

    assert variant.action_ids == frozenset({"PartDesign_Split"})
    assert variant.exact_target_type == "SourceBodySplittersAndRetainedRegion"
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False
    assert branch["properties"]["operation"]["const"] == "split"
    assert branch["required"] == ["label", "definition"]
    assert branch["additionalProperties"] is False
    assert split["required"] == [
        "source_body",
        "splitters",
        "retained_region_index",
    ]
    assert split["additionalProperties"] is False
    assert set(split["properties"]) == {
        "source_body",
        "splitters",
        "retained_region_index",
    }
    splitters = split["properties"]["splitters"]
    assert (splitters["minItems"], splitters["maxItems"], splitters["uniqueItems"]) == (
        1,
        32,
        True,
    )
    item = splitters["items"]
    assert item["required"] == ["object_name", "subelements"]
    assert item["additionalProperties"] is False
    subelements = item["properties"]["subelements"]
    assert (subelements["minItems"], subelements["maxItems"], subelements["uniqueItems"]) == (
        0,
        64,
        True,
    )
    assert subelements["items"]["pattern"] == r"^(?:Face|Shell|Solid)[1-9][0-9]*$"
    assert split["properties"]["retained_region_index"] == {
        "type": "integer",
        "minimum": 0,
        "maximum": 255,
    }
    encoded = json.dumps(
        schema,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) < 1_800
