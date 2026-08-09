# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

from jsonschema import Draft202012Validator
import pytest

from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeSketchBatchBindings import SKETCH_BATCH_CAPABILITY_NAME
from VibeCADNativeSketchBatchPlan import prepare_sketch_batch
from VibeCADNativeSketchBatchSchema import sketch_batch_capability_definition
from VibeCADNativeSketchErrors import NativeSketchError
from vibecad_tests.native_sketch_batch_test_support import (
    constrained_rectangle_arguments,
)


def _arguments() -> dict:
    return constrained_rectangle_arguments(SimpleNamespace(Name="Sketch"))


def _values(arguments: dict) -> dict:
    return {name: value for name, value in arguments.items() if name != "operation"}


def test_batch_contract_is_shared_only_with_the_active_sketch_surface() -> None:
    definition = sketch_batch_capability_definition()
    variant = definition.variants[0]

    assert definition.name == SKETCH_BATCH_CAPABILITY_NAME
    assert definition.primary_classification == "mutation"
    assert variant.operation == "create"
    assert variant.surface_ids == frozenset({"sketch.edit"})
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False

    registry = build_native_capability_registry()
    assert SKETCH_BATCH_CAPABILITY_NAME in registry.shared_definition_names
    assert registry.definition(SKETCH_BATCH_CAPABILITY_NAME) == definition
    assert registry.implementation(SKETCH_BATCH_CAPABILITY_NAME) is not None


def test_batch_schema_is_closed_bounded_and_concise() -> None:
    schema = sketch_batch_capability_definition().provider_schema(("create",))
    validator = Draft202012Validator(schema["parameters"])
    valid = _arguments()

    assert list(validator.iter_errors(valid)) == []
    encoded = json.dumps(
        schema,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) <= 4_300

    for invalid in (
        {**valid, "unexpected": True},
        {name: value for name, value in valid.items() if name != "geometry"},
        {**valid, "geometry": []},
        {**valid, "geometry": valid["geometry"] * 9},
        {**valid, "constraints": []},
        {**valid, "constraints": valid["constraints"] * 2},
        {
            **valid,
            "geometry": [{**valid["geometry"][0], "unexpected": True}],
        },
        {
            **valid,
            "constraints": [{**valid["constraints"][0], "unexpected": True}],
        },
    ):
        assert list(validator.iter_errors(invalid))


def test_batch_plan_resolves_every_local_reference_before_mutation() -> None:
    plan = prepare_sketch_batch("document-uid", _values(_arguments()))

    assert [item.local_ref for item in plan.geometry] == [
        "bottom",
        "right",
        "top",
        "left",
    ]
    assert [item.local_ref for item in plan.constraints] == [
        "join_bottom_right",
        "join_right_top",
        "join_top_left",
        "join_left_bottom",
        "bottom_horizontal",
        "right_vertical",
        "top_horizontal",
        "left_vertical",
        "anchor_origin",
        "width",
        "height",
    ]
    assert plan.constraints[8].points[0].is_origin is True
    assert plan.constraints[9].value == 40.0
    assert plan.constraints[10].value == 20.0


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (
            lambda value: value["constraints"][0]["first"].update(
                {"geometry_ref": "missing"}
            ),
            "unknown geometry",
        ),
        (
            lambda value: value["geometry"][1].update({"ref": "bottom"}),
            "geometry refs must be unique",
        ),
        (
            lambda value: value["constraints"][0]["first"].update(
                {"position": "center"}
            ),
            "must use end, start",
        ),
        (
            lambda value: value["geometry"][0].update(
                {"end_mm": {"x": 0.0, "y": 0.0}}
            ),
            "endpoints must be distinct",
        ),
        (
            lambda value: value["constraints"][4].update(
                {"geometry_ref": "missing"}
            ),
            "unknown geometry",
        ),
    ),
)
def test_batch_plan_rejects_invalid_local_semantics(
    mutate,
    message: str,
) -> None:
    values = copy.deepcopy(_values(_arguments()))
    mutate(values)

    with pytest.raises(NativeSketchError, match=message):
        prepare_sketch_batch("document-uid", values)
