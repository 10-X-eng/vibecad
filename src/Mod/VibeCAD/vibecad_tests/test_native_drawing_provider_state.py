# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider-facing Drawing state keeps exact targets without duplicate noise."""

from __future__ import annotations

import json

from VibeCADNativeCapabilityRegistry import provider_visible_native_schema
from VibeCADNativeDrawingCircleCenterLineSchema import (
    drawing_circle_center_line_capability_definition,
)
from VibeCADNativeDrawingProjectionGroup import projection_group_summary
from VibeCADNativeDrawingProviderState import compact_drawing_provider_state
from VibeCADNativeProviderContext import provider_visible_native_state


_SOURCE_SHA = "1" * 64
_PAGE_SHA = "2" * 64
_VIEW_SHA = "3" * 64


def _state() -> dict:
    source = {
        "object_name": "Body",
        "label": "Machined Bracket",
        "type_id": "PartDesign::Body",
        "state_sha256": _SOURCE_SHA,
        "shape_type": "Solid",
        "shape_sha256": "4" * 64,
        "placement": {
            "base_mm": [0.0, 0.0, 0.0],
            "quaternion": [0.0, 0.0, 0.0, 1.0],
        },
        "topology": {"solids": 1, "faces": 10, "edges": 24},
        "bounds_size_mm": [48.0, 32.0, 12.0],
    }
    page = {
        "object_name": "Page",
        "label": "Bracket Drawing",
        "type_id": "TechDraw::DrawPage",
        "state_sha256": _PAGE_SHA,
        "keep_updated": True,
        "projection_type": "Third angle",
        "scale": 1.0,
        "view_count": 1,
        "template": {
            "object_name": "Template",
            "label": "Template",
            "type_id": "TechDraw::DrawSVGTemplate",
        },
        "template_geometry": {
            "width_mm": 297.0,
            "height_mm": 210.0,
            "orientation": "Landscape",
            "drawing_bounds_mm": {
                "min_x_mm": 20.0,
                "min_y_mm": 10.0,
                "max_x_mm": 287.0,
                "max_y_mm": 152.0,
            },
        },
        "template_content": {"available": True, "size_bytes": 1024, "sha256": "5" * 64},
        "editable_field_count": 1,
        "editable_fields_supported": True,
        "update_status": {"keep_updated": True, "current": True, "state_messages": []},
        "unresolved_references": [],
        "export_readiness": {"ready": True, "issues": []},
        "view_locks": {"view_count": 1, "locked_count": 0, "unlocked_count": 1},
        "views": [
            {
                "object_name": "Front",
                "label": "Front",
                "type_id": "TechDraw::DrawProjGroupItem",
                "state_sha256": _VIEW_SHA,
                "visible_edge_count": 12,
                "hidden_edge_count": 2,
                "sources": [{"object_name": "Body", "type_id": "PartDesign::Body"}],
                "x": 105.0,
                "y": 90.0,
                "scale": 1.0,
                "placement": {
                    "placement_target": {"object_name": "Front"},
                    "position_on_page_mm": {"x_mm": 105.0, "y_mm": 90.0},
                    "locked": False,
                },
            }
        ],
    }
    return {
        "surface_id": "drawing",
        "document": {"document_uid": "document-a", "document_name": "Bracket"},
        "structural_revision": 8,
        "domain": {
            "kind": "drawing",
            "line_defaults": {"state_sha256": "6" * 64, "style_name": "Continuous"},
            "hatch_defaults": None,
            "rich_annotation_defaults": None,
            "weld_symbol_catalog": {"catalog_sha256": "7" * 64, "item_count": 18},
            "leader_defaults": {},
            "source_count": 1,
            "sources": [source],
            "sources_truncated": False,
            "page_count": 1,
            "pages": [page],
            "active_page_resolution": "only_page",
            "active_page": {
                "object_name": "Page",
                "state_sha256": _PAGE_SHA,
                "presentation": {"fit": "page", "zoom": 1.0},
            },
            "selected_sources": [source],
            "selected_break_definitions": [],
            "selected_projected_geometry": [
                {
                    "view_object_name": "Front",
                    "projection_state_sha256": "8" * 64,
                    "elements": [{"subelement": "Edge1"}],
                }
            ],
            "selected_dimensions": [],
            "active_3d_viewport": {"camera": "orthographic"},
        },
        "selection": {"items": [{"object": {"object_name": "Body"}}]},
        "working_set": [
            {"object_name": "Body", "type_id": "PartDesign::Body"},
            {"object_name": "Unrepresented", "type_id": "Part::Feature"},
        ],
    }


def test_compact_drawing_state_preserves_targets_and_removes_duplicates() -> None:
    compact = compact_drawing_provider_state(_state())

    assert compact["surface_id"] == "drawing"
    assert compact["structural_revision"] == 8
    domain = compact["domain"]
    assert domain["source_count"] == 1
    source = domain["sources"][0]
    assert source["source_name"] == "Body"
    assert source["source_target"] == {"object_name": "Body"}
    assert source["selected"] is True
    assert "shape_sha256" not in source
    assert "selected_sources" not in domain

    page = domain["pages"][0]
    assert page["page_target"] == {"object_name": "Page"}
    assert page["active"] is True
    assert page["template_geometry"]["drawing_bounds_mm"] == {
        "min_x_mm": 20.0,
        "min_y_mm": 10.0,
        "max_x_mm": 287.0,
        "max_y_mm": 152.0,
    }
    assert page["presentation"] == {"fit": "page", "zoom": 1.0}
    assert page["views"][0]["view_name"] == "Front"
    assert page["views"][0]["view_target"] == {"object_name": "Front"}
    assert page["views"][0]["placement"] == {
        "placement_target": {"object_name": "Front"},
        "position_on_page_mm": {"x_mm": 105.0, "y_mm": 90.0},
        "locked": False,
    }
    assert domain["context"] == {
        "selected_projected_geometry": [
            {
                "view_object_name": "Front",
                "elements": [{"subelement": "Edge1"}],
            }
        ]
    }
    assert compact["working_set"] == [
        {"object_name": "Unrepresented", "type_id": "Part::Feature"}
    ]
    assert "selection" not in compact


def test_drawing_provider_schema_keeps_state_hashes_internal() -> None:
    definition = drawing_circle_center_line_capability_definition()
    schema = provider_visible_native_schema(
        definition.provider_schema(("create",))
    )

    assert "expected_" not in json.dumps(schema, sort_keys=True)


def test_provider_context_compacts_drawing_state() -> None:
    assert provider_visible_native_state(_state()) == compact_drawing_provider_state(
        _state()
    )


def test_truncated_source_state_points_to_the_next_exact_catalog_page() -> None:
    state = _state()
    state["domain"]["source_count"] = 100
    state["domain"]["sources_truncated"] = True
    state["domain"]["source_next_offset"] = 48

    domain = compact_drawing_provider_state(state)["domain"]

    assert domain["sources_truncated"] is True
    assert domain["source_next_offset"] == 48


class _Vector:
    def __init__(self, x: float, y: float, z: float) -> None:
        self.x = x
        self.y = y
        self.z = z


class _ProjectionChild:
    TypeId = "TechDraw::DrawProjGroupItem"

    def __init__(
        self,
        name: str,
        orientation: str,
        x: float,
        y: float,
        direction: tuple[float, float, float],
        x_direction: tuple[float, float, float],
    ) -> None:
        self.Name = name
        self.Label = name
        self.Type = orientation
        self.X = x
        self.Y = y
        self.Direction = _Vector(*direction)
        self.XDirection = _Vector(*x_direction)


def test_projection_group_summary_preserves_semantics_and_exact_child_targets() -> None:
    group = type(
        "ProjectionGroup",
        (),
        {
            "TypeId": "TechDraw::DrawProjGroup",
            "Name": "ProjectionGroup",
            "ProjectionType": "Third angle",
            "Scale": 2.0,
            "X": 100.0,
            "Y": 70.0,
            "Views": (
                _ProjectionChild(
                    "Front",
                    "Front",
                    0.0,
                    0.0,
                    (0.0, -1.0, 0.0),
                    (1.0, 0.0, 0.0),
                ),
                _ProjectionChild(
                    "Top",
                    "Top",
                    0.0,
                    40.0,
                    (0.0, 0.0, 1.0),
                    (1.0, 0.0, 0.0),
                ),
            ),
        },
    )()

    assert projection_group_summary(group) == {
        "convention": "third_angle",
        "scale": 2.0,
        "front_direction": [0.0, -1.0, 0.0],
        "front_x_direction": [1.0, 0.0, 0.0],
        "views": [
            {
                    "orientation": "front",
                    "view_name": "Front",
                    "view_target": {"object_name": "Front"},
                    "placement_parent": {"object_name": "ProjectionGroup"},
                    "placement_target": {"object_name": "ProjectionGroup"},
                    "position_on_page_mm": {"x_mm": 100.0, "y_mm": 70.0},
                },
            {
                    "orientation": "top",
                    "view_name": "Top",
                    "view_target": {"object_name": "Top"},
                    "placement_parent": {"object_name": "ProjectionGroup"},
                    "placement_target": {"object_name": "ProjectionGroup"},
                    "position_on_page_mm": {"x_mm": 100.0, "y_mm": 110.0},
                },
        ],
    }
