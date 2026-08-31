# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused contracts for exact human-authorized CAM Job templates."""

from __future__ import annotations

import json

import pytest

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureTemplate import _content
from VibeCADNativeManufactureTemplateSchema import (
    manufacture_template_capability_definition,
)


def _values() -> dict:
    return {
        "description": "Reusable roughing setup",
        "include_postprocessing": True,
        "tool_controllers": [],
        "stock": {"kind": "include", "extent": True, "placement": False},
        "setup_sheet": {
            "tool_rapids": True,
            "coolant": True,
            "operation_heights": True,
            "operation_depths": True,
            "operation_settings": [],
        },
    }


def test_schema_exposes_every_template_choice_but_no_output_path() -> None:
    definition = manufacture_template_capability_definition()
    schema = definition.provider_schema(("export_template",))
    branch = schema["parameters"]["oneOf"][0]

    assert definition.primary_classification == "export"
    assert branch["additionalProperties"] is False
    assert branch["required"] == [
        "job",
        "description",
        "include_postprocessing",
        "tool_controllers",
        "stock",
        "setup_sheet",
    ]
    assert branch["properties"]["tool_controllers"]["maxItems"] == 32
    assert branch["properties"]["setup_sheet"]["properties"][
        "operation_settings"
    ]["maxItems"] == 64
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    for forbidden in ('"path"', '"destination"', '"file_name"'):
        assert forbidden not in encoded


def test_content_requires_discriminated_stock_and_distinct_bounded_settings() -> None:
    included = _content(_values())
    assert included.stock_kind == "include"
    assert included.stock_extent is True
    assert included.stock_placement is False

    excluded_values = _values()
    excluded_values["stock"] = {"kind": "exclude"}
    excluded = _content(excluded_values)
    assert excluded.stock_kind == "exclude"
    assert excluded.stock_extent is False
    assert excluded.stock_placement is False

    invalid = _values()
    invalid["stock"] = {"kind": "exclude", "extent": True}
    with pytest.raises(NativeManufactureError, match="exact exclude or include"):
        _content(invalid)

    duplicate = _values()
    duplicate["setup_sheet"]["operation_settings"] = ["Profile", "Profile"]
    with pytest.raises(NativeManufactureError, match="distinct"):
        _content(duplicate)
