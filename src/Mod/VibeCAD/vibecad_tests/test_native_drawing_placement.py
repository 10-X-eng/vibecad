# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused contracts for deterministic Drawing page layout repair."""

from __future__ import annotations

import json

from VibeCADNativeCapabilityRegistry import NativeCapabilityRegistry
from VibeCADNativeDrawingPlacementBindings import (
    register_drawing_placement_capability_implementations,
)
from VibeCADNativeDrawingPlacementSchema import (
    DRAWING_PLACEMENT_CAPABILITY_NAMES,
    drawing_placement_capability_definitions,
    register_drawing_placement_capability_definitions,
)
from VibeCADNativeDrawingPlacementState import (
    drawing_dimension_label_placement_state,
    drawing_note_placement_state,
    drawing_view_placement_state,
)


class _Document:
    def isObjectUsableAtCurrentTimelinePosition(self, _obj) -> bool:
        return True


class _Page:
    TypeId = "TechDraw::DrawPage"
    Name = "Page"

    def __init__(self) -> None:
        self.Document = _Document()
        self.Views = []

    def isDerivedFrom(self, type_id: str) -> bool:
        return type_id == "TechDraw::DrawPage"


class _DrawingObject:
    Label = "Drawing item"

    def __init__(self, type_id: str, name: str, page: _Page) -> None:
        self.TypeId = type_id
        self.Name = name
        self.Document = page.Document
        self.X = 25.0
        self.Y = 40.0
        self.LockPosition = False
        self._page = page
        page.Views.append(self)

    def isDerivedFrom(self, type_id: str) -> bool:
        if self.TypeId == "TechDraw::DrawViewDimension":
            return type_id in {"TechDraw::DrawView", self.TypeId}
        if self.TypeId == "TechDraw::DrawProjGroup":
            return type_id in {"TechDraw::DrawView", self.TypeId}
        return type_id == self.TypeId

    def findParentPage(self):
        return self._page

    def isValid(self) -> bool:
        return True


def _branch(definition, operation: str) -> dict:
    return next(
        branch
        for branch in definition.provider_schema((operation,))["parameters"]["oneOf"]
        if branch["properties"]["operation"]["const"] == operation
    )


def test_placement_schemas_are_three_small_focused_batch_tools() -> None:
    view_definition, dimension_definition, note_definition = (
        drawing_placement_capability_definitions()
    )
    assert DRAWING_PLACEMENT_CAPABILITY_NAMES == (
        "drawing.place_views",
        "drawing.place_dimension_labels",
        "drawing.place_notes",
    )
    view = _branch(view_definition, "place_views")
    dimension = _branch(dimension_definition, "place_dimension_labels")
    note = _branch(note_definition, "place_notes")

    assert view["additionalProperties"] is False
    assert set(view["required"]) == {"page", "views"}
    assert view["properties"]["views"]["minItems"] == 1
    assert view["properties"]["views"]["maxItems"] == 64
    assert set(view["properties"]["views"]["items"]["required"]) == {
        "object_name",
        "expected_placement_state_sha256",
        "position_on_page_mm",
    }
    assert view["properties"]["views"]["items"]["properties"][
        "object_name"
    ]["description"] == (
        "Top-level view or projected child object_name from page state."
    )

    assert dimension["additionalProperties"] is False
    assert set(dimension["required"]) == {"page", "dimensions"}
    assert dimension["properties"]["dimensions"]["minItems"] == 1
    assert dimension["properties"]["dimensions"]["maxItems"] == 64
    assert set(dimension["properties"]["dimensions"]["items"]["required"]) == {
        "object_name",
        "expected_placement_state_sha256",
        "label_position_on_page_mm",
    }
    assert dimension_definition.description == (
        "Move existing Drawing dimension labels to page coordinates."
    )
    assert note["additionalProperties"] is False
    assert set(note["required"]) == {"page", "notes"}
    assert note["properties"]["notes"]["minItems"] == 1
    assert note["properties"]["notes"]["maxItems"] == 64
    assert set(note["properties"]["notes"]["items"]["required"]) == {
        "object_name",
        "expected_placement_state_sha256",
        "position_on_page_mm",
    }
    assert note_definition.description == (
        "Move existing Drawing notes to page coordinates."
    )

    encoded = json.dumps(
        (view_definition.provider_schema(("place_views",)),
         dimension_definition.provider_schema(("place_dimension_labels",)),
         note_definition.provider_schema(("place_notes",))),
        sort_keys=True,
        separators=(",", ":"),
    )
    assert "unknown" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 6 * 1024


def test_placement_state_is_exact_for_views_dimensions_and_notes() -> None:
    page = _Page()
    group = _DrawingObject("TechDraw::DrawProjGroup", "Group", page)
    dimension = _DrawingObject(
        "TechDraw::DrawViewDimension", "Dimension", page
    )
    note = _DrawingObject("TechDraw::DrawRichAnno", "Note", page)
    note.AnnoParent = None
    note.AnnoText = "<p>INSPECT</p>"
    note.MaxWidth = -1.0
    note.OriginCentered = False
    note.ShowFrame = False
    note.ViewObject = type(
        "_ViewObject",
        (),
        {"LineStyle": "NoLine", "LineWidth": 0.35, "LineColor": (0.0, 0.0, 0.0)},
    )()

    group_state = drawing_view_placement_state(group)
    dimension_state = drawing_dimension_label_placement_state(dimension)
    note_state = drawing_note_placement_state(note)
    assert group_state["position_on_page_mm"] == {"x_mm": 25.0, "y_mm": 40.0}
    assert group_state["locked"] is False
    assert dimension_state["label_position_in_view_mm"] == {
        "x_mm": 25.0,
        "y_mm": 40.0,
    }
    assert note_state["position_on_page_mm"] == {"x_mm": 25.0, "y_mm": 40.0}

    group.X = 26.0
    dimension.Y = 41.0
    note.X = 27.0
    assert (
        drawing_view_placement_state(group)["placement_state_sha256"]
        != group_state["placement_state_sha256"]
    )
    assert (
        drawing_dimension_label_placement_state(dimension)[
            "placement_state_sha256"
        ]
        != dimension_state["placement_state_sha256"]
    )
    assert (
        drawing_note_placement_state(note)["placement_state_sha256"]
        != note_state["placement_state_sha256"]
    )


def test_placement_registry_is_complete() -> None:
    registry = NativeCapabilityRegistry()
    register_drawing_placement_capability_definitions(registry)
    register_drawing_placement_capability_implementations(registry)
    assert registry.definition_names == tuple(sorted(DRAWING_PLACEMENT_CAPABILITY_NAMES))
    assert registry.implementation_names == registry.definition_names
