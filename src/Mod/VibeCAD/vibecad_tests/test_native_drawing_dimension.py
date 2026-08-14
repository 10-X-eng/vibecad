# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused contracts for exact explicit Native Drawing dimensions."""

from __future__ import annotations

import json

from VibeCADNativeDrawingDimension import _matches_document_label
from VibeCADNativeDrawingDimensionSchema import (
    DRAWING_GENERAL_DIMENSION_OPERATIONS,
    drawing_dimension_capability_definition,
)
from VibeCADNativeDrawingDimensionState import drawing_dimension_state


def _branches() -> dict[str, dict]:
    definition = drawing_dimension_capability_definition()
    schema = definition.provider_schema(DRAWING_GENERAL_DIMENSION_OPERATIONS)
    return {
        branch["properties"]["operation"]["const"]: branch
        for branch in schema["parameters"]["oneOf"]
    }


def test_dimension_schema_keeps_closed_discriminated_operations() -> None:
    definition = drawing_dimension_capability_definition()
    schema = definition.provider_schema(DRAWING_GENERAL_DIMENSION_OPERATIONS)
    branches = _branches()

    assert definition.preserve_operation_branches is True
    assert tuple(branches) == DRAWING_GENERAL_DIMENSION_OPERATIONS
    assert all(branch["additionalProperties"] is False for branch in branches.values())
    assert set(branches["create_radius"]["required"]) >= {
        "operation",
        "edge",
        "allow_approximate",
    }
    assert set(branches["create_angle"]["required"]) >= {
        "operation",
        "first_edge",
        "second_edge",
    }
    assert set(branches["create_three_point_angle"]["required"]) >= {
        "operation",
        "first_arm_point",
        "apex_point",
        "second_arm_point",
    }
    assert set(branches["create_area"]["required"]) >= {"operation", "face"}
    for operation in ("create_horizontal_extent", "create_vertical_extent"):
        assert set(branches[operation]["required"]) >= {"operation", "extent"}
        extent = branches[operation]["properties"]["extent"]
        assert len(extent["oneOf"]) == 2
        scopes = {
            branch["properties"]["scope"]["const"]: branch
            for branch in extent["oneOf"]
        }
        assert set(scopes) == {"whole_view", "edges"}
        assert scopes["edges"]["properties"]["edges"]["maxItems"] == 64
    axonometric = branches["create_axonometric_length"]
    assert set(axonometric["required"]) >= {
        "operation",
        "measurement",
        "extension_direction_edge",
        "expected_value_mode",
    }
    measurement = axonometric["properties"]["measurement"]
    kinds = {
        branch["properties"]["kind"]["const"]: branch
        for branch in measurement["oneOf"]
    }
    assert set(kinds) == {"edge", "vertex_pair"}
    assert axonometric["properties"]["expected_value_mode"]["enum"] == [
        "projected",
        "x_axis_true_length",
        "y_axis_true_length",
        "z_axis_true_length",
    ]
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 28 * 1024


def test_document_label_matching_accepts_only_freecad_unique_forms() -> None:
    assert _matches_document_label("Width", "Width")
    assert _matches_document_label("Width001", "Width")
    assert _matches_document_label("Width125", "Width009")
    assert _matches_document_label("124", "123")
    assert not _matches_document_label("Width01", "Width")
    assert not _matches_document_label("Other001", "Width")
    assert not _matches_document_label("Width001extra", "Width")


class _Document:
    @staticmethod
    def isObjectUsableAtCurrentTimelinePosition(_obj) -> bool:
        return True


class _Object:
    def __init__(self, name: str, document: _Document) -> None:
        self.Name = name
        self.Document = document


class _Dimension(_Object):
    TypeId = "TechDraw::DrawViewDimension"
    Type = "Distance"
    MeasureType = "Projected"
    Label = "Width"
    X = 12.0
    Y = 8.0
    VibeCADTimelineRole = "operation"
    VibeCADTimelineOwner = None

    def __init__(self) -> None:
        document = _Document()
        super().__init__("Dimension", document)
        self.view = _Object("View", document)
        self.page = _Object("Page", document)
        self.References2D = ((self.view, ("Edge0",)),)
        self.State = ("Up-to-date",)

    @staticmethod
    def isDerivedFrom(type_id: str) -> bool:
        return type_id == "TechDraw::DrawViewDimension"

    def findParentPage(self):
        return self.page

    @staticmethod
    def getRawValue() -> float:
        return 40.0

    @staticmethod
    def getText() -> str:
        return "40.00 mm"

    @staticmethod
    def isValid() -> bool:
        return True


def test_transient_document_status_is_reported_but_not_hashed() -> None:
    dimension = _Dimension()
    current = drawing_dimension_state(dimension)
    dimension.State = ("Touched",)
    touched = drawing_dimension_state(dimension)

    assert current["state_messages"] == ["Up-to-date"]
    assert touched["state_messages"] == ["Touched"]
    assert touched["state_sha256"] == current["state_sha256"]
