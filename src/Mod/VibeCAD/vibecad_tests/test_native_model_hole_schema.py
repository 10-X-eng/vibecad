# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json

import pytest

from VibeCADNativeDesignHole import prepare_design_hole
from VibeCADNativeDesignHoleCatalog import require_hole_catalog_selection
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeModelHoleSchema import (
    model_hole_capability_definition,
)
from VibeCADNativeModelCatalogSchema import model_catalog_capability_definition


def _plain_arguments() -> dict[str, object]:
    return {
        "label": "Mounting Hole",
        "profile": {"object_name": "HoleCenters"},
        "base_profile": "circles_and_arcs",
        "hole_type": {"kind": "plain", "diameter_mm": 5.0},
        "head": {"kind": "none"},
        "depth": {"kind": "dimension", "depth_mm": 8.0},
        "drill_point": {"kind": "flat"},
        "taper": {"kind": "straight"},
        "reversed": False,
        "targets": [{"object_name": "BracketBody"}],
    }


def test_hole_contract_matches_the_current_typed_task_controls() -> None:
    definition = model_hole_capability_definition()
    schema = definition.provider_schema(("hole",))
    branch = schema["parameters"]["oneOf"][0]

    assert definition.name == "model.hole"
    assert definition.variants[0].action_ids == frozenset({"PartDesign_Hole"})
    assert set(branch["required"]) == set(_plain_arguments())
    assert branch["additionalProperties"] is False
    assert branch["properties"]["targets"]["minItems"] == 1
    assert branch["properties"]["targets"]["maxItems"] == 16
    hole_kinds = [
        item["properties"]["kind"]["const"]
        for item in branch["properties"]["hole_type"]["oneOf"]
    ]
    assert hole_kinds == [
        "plain",
        "clearance",
        "tap_drill",
        "threaded_cosmetic",
        "threaded_modeled",
    ]


def test_hole_contract_omits_controls_not_present_in_the_current_task() -> None:
    definition = model_hole_capability_definition()
    serialized = json.dumps(definition.provider_schema(("hole",)), sort_keys=True)

    for forbidden in (
        "midplane",
        "refine",
        "new_body",
        "join",
        "intersect",
        "selection",
        "runCommand",
        "workbench",
    ):
        assert forbidden not in serialized


def test_hole_catalog_contract_is_a_bounded_model_read() -> None:
    definition = model_catalog_capability_definition()
    schema = definition.provider_schema(("hole_threads",))
    branch = schema["parameters"]["oneOf"][0]

    assert definition.name == "model.catalog"
    assert definition.primary_classification == "read"
    assert branch["required"] == []
    assert branch["additionalProperties"] is False
    assert branch["properties"]["standard"]["enum"]


def test_hole_parser_builds_exact_cut_targets_and_rejects_extra_nested_fields() -> None:
    values = _plain_arguments()
    prepared = prepare_design_hole("document-a", values)

    assert prepared.profile.object_ref.object_name == "HoleCenters"
    assert prepared.profile.subelements == ()
    assert prepared.result.mode == "cut"
    assert [ref.object_name for ref in prepared.result.target_refs] == ["BracketBody"]
    assert prepared.hole_type.kind == "plain"

    values["hole_type"] = {
        "kind": "plain",
        "diameter_mm": 5.0,
        "unexpected": True,
    }
    with pytest.raises(NativeModelError, match="type fields"):
        prepare_design_hole("document-a", values)


def test_modeled_thread_custom_clearance_preserves_signed_task_values() -> None:
    values = _plain_arguments()
    values["hole_type"] = {
        "kind": "threaded_modeled",
        "standard": "ISOMetricProfile",
        "size": "M6x1.0",
        "thread_class": "6H",
        "direction": "left",
        "thread_depth": {"kind": "dimension", "depth_mm": 6.0},
        "custom_clearance_mm": -0.05,
    }

    prepared = prepare_design_hole("document-a", values)

    assert prepared.hole_type.custom_clearance_mm == -0.05
    assert prepared.hole_type.direction == "left"
    assert prepared.hole_type.thread_depth_mm == 6.0


def test_catalog_selection_validates_standard_size_class_and_fit() -> None:
    catalog = {
        "ISOMetricProfile": {
            "sizes": [{"designation": "M6x1.0"}],
            "classes": ["6H"],
            "fits": ["Normal"],
        }
    }

    selected = require_hole_catalog_selection(
        catalog,
        standard="ISOMetricProfile",
        size="M6x1.0",
        thread_class="6H",
        fit="Normal",
    )
    assert selected is catalog["ISOMetricProfile"]

    with pytest.raises(NativeModelError, match="size is unavailable"):
        require_hole_catalog_selection(
            catalog,
            standard="ISOMetricProfile",
            size="M8x1.25",
        )
