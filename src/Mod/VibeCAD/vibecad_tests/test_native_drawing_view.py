# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused contracts for responsive exact Native Drawing projections."""

from __future__ import annotations

import json

from VibeCADNativeBackgroundSchema import native_background_capability_definition
from VibeCADNativeDrawingProjectionChild import _fit_projection_group_layout
from VibeCADNativeDrawingDraftSchema import drawing_draft_capability_definition
from VibeCADNativeDrawingView import _spec, standard_view_line_flags
from VibeCADNativeDrawingProjectionGroup import projection_group_directions
from VibeCADNativeDrawingViewRuntime import _projection_fit_bounds
from VibeCADNativeDrawingViewSchema import drawing_view_capability_definitions
from VibeCADNativeDrawingViewState import (
    DRAWING_VIEW_ORIENTATIONS,
    MAX_DRAWING_BREAKS,
    MAX_DRAWING_VIEW_SOURCES,
)


def _branch(schema: dict, operation: str) -> dict:
    return next(
        branch
        for branch in schema["parameters"]["oneOf"]
        if branch["properties"]["operation"]["const"] == operation
    )


def test_drawing_views_are_three_focused_background_tools() -> None:
    definitions = drawing_view_capability_definitions()
    assert tuple(definition.name for definition in definitions) == (
        "drawing.standard_view",
        "drawing.projection_group",
        "drawing.broken_view",
    )
    assert all(definition.primary_classification == "mutation" for definition in definitions)
    assert all(len(definition.variants) == 1 for definition in definitions)
    variant, projection_group, broken = (
        definition.variants[0] for definition in definitions
    )
    assert variant.operation == "create_standard_view"
    assert variant.action_ids == frozenset({"TechDraw_View"})
    assert variant.surface_ids == frozenset({"drawing"})
    assert variant.exact_target_type == "ExactDrawingPageSourcesAndProjectionSettings"
    assert variant.transaction_behavior == "background"
    assert variant.background_required is True

    schema = definitions[0].provider_schema((variant.operation,))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":")).casefold()
    assert "unknown" not in encoded
    assert "path" not in encoded
    branch = _branch(schema, variant.operation)
    assert branch["additionalProperties"] is False
    assert branch["required"] == [
        "label",
        "page",
        "sources",
        "orientation",
        "position",
        "line_style",
    ]
    assert branch["properties"]["sources"]["maxItems"] == MAX_DRAWING_VIEW_SOURCES
    assert branch["properties"]["orientation"]["enum"] == list(
        DRAWING_VIEW_ORIENTATIONS
    )
    assert branch["properties"]["line_style"]["enum"] == [
        "visible",
        "visible_and_hidden",
        "hard_only",
    ]
    assert branch["properties"]["scale"] == {
        "oneOf": [
            {
                "type": "number",
                "exclusiveMinimum": 0.0,
                "maximum": 1_000.0,
            },
            {
                "type": "object",
                "properties": {"kind": {"type": "string", "const": "page"}},
                "required": ["kind"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "const": "custom"},
                    "value": {
                        "type": "number",
                        "exclusiveMinimum": 0.0,
                        "maximum": 1_000.0,
                    },
                },
                "required": ["kind", "value"],
                "additionalProperties": False,
            },
        ]
    }

    assert projection_group.operation == "create_projection_group"
    assert projection_group.action_ids == frozenset({"TechDraw_ProjectionGroup"})
    assert projection_group.surface_ids == frozenset({"drawing"})
    assert projection_group.exact_target_type == (
        "ExactDrawingPageSourcesProjectionSetAndConvention"
    )
    assert projection_group.transaction_behavior == "background"
    assert projection_group.background_required is True
    projection_schema = definitions[1].provider_schema((projection_group.operation,))
    projection_branch = _branch(projection_schema, projection_group.operation)
    assert projection_branch["additionalProperties"] is False
    assert projection_branch["required"] == [
        "label",
        "page",
        "sources",
        "front_orientation",
        "views",
        "convention",
        "line_style",
    ]
    assert projection_branch["properties"]["views"] == {
        "type": "array",
        "items": {
            "type": "string",
            "enum": ["front", "top", "right", "left", "bottom", "rear"],
        },
        "minItems": 2,
        "maxItems": 6,
        "uniqueItems": True,
    }
    assert projection_branch["properties"]["convention"]["enum"] == [
        "first_angle",
        "third_angle",
    ]
    assert not {"position", "scale", "spacing"} & set(
        projection_branch["properties"]
    )

    assert broken.operation == "create_broken_view"
    assert broken.action_ids == frozenset({"TechDraw_BrokenView"})
    assert broken.surface_ids == frozenset({"drawing"})
    assert broken.exact_target_type == (
        "ExactDrawingPageSourcesBreakDefinitionsAndProjectionSettings"
    )
    assert broken.transaction_behavior == "background"
    assert broken.background_required is True
    broken_schema = definitions[2].provider_schema((broken.operation,))
    broken_encoded = json.dumps(
        broken_schema,
        sort_keys=True,
        separators=(",", ":"),
    ).casefold()
    assert "unknown" not in broken_encoded
    assert "path" not in broken_encoded
    broken_branch = _branch(broken_schema, broken.operation)
    assert broken_branch["additionalProperties"] is False
    assert broken_branch["required"] == [
        "label",
        "page",
        "sources",
        "breaks",
        "gap_mm",
        "orientation",
        "position",
        "line_style",
    ]
    assert broken_branch["properties"]["breaks"]["maxItems"] == MAX_DRAWING_BREAKS
    assert broken_branch["properties"]["gap_mm"] == {
        "type": "number",
        "minimum": 0.0,
        "maximum": 10_000.0,
    }


def test_standard_view_line_styles_are_complete_and_unambiguous() -> None:
    visible = standard_view_line_flags("visible")
    hidden = standard_view_line_flags("visible_and_hidden")
    hard = standard_view_line_flags("hard_only")
    expected = {
        "SmoothVisible",
        "SeamVisible",
        "IsoVisible",
        "HardHidden",
        "SmoothHidden",
        "SeamHidden",
        "IsoHidden",
    }
    assert set(visible) == set(hidden) == set(hard) == expected
    assert not any(visible[name] for name in expected if name.endswith("Hidden"))
    assert hidden["HardHidden"] and hidden["SmoothHidden"]
    assert hard["SmoothVisible"] is False


def test_standard_view_scale_accepts_the_shared_natural_scale_shape() -> None:
    values = {
        "label": "Front",
        "orientation": "front",
        "position": {"x_mm": 80.0, "y_mm": 120.0},
        "line_style": "visible",
    }

    page = _spec({**values, "scale": {"kind": "page"}})
    custom = _spec({**values, "scale": {"kind": "custom", "value": 0.5}})
    legacy_number = _spec({**values, "scale": 0.75})

    assert (page.scale_kind, page.scale) == ("page", None)
    assert (custom.scale_kind, custom.scale) == ("custom", 0.5)
    assert (legacy_number.scale_kind, legacy_number.scale) == ("custom", 0.75)


def test_draft_view_contract_names_its_actual_source_kind() -> None:
    definition = drawing_draft_capability_definition()
    variant = definition.variants[0]

    assert "Draft Workbench object" in definition.description
    assert "Draft Workbench object" in variant.description


def test_projection_group_directions_follow_the_selected_front() -> None:
    directions = projection_group_directions("front")
    assert directions == {
        "front": ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0)),
        "top": ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
        "right": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
        "left": ((-1.0, 0.0, 0.0), (0.0, -1.0, 0.0)),
        "bottom": ((0.0, 0.0, -1.0), (1.0, 0.0, 0.0)),
        "rear": ((0.0, 1.0, 0.0), (-1.0, 0.0, 0.0)),
    }

    top_front = projection_group_directions("top")
    assert top_front["front"] == ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0))
    assert top_front["top"] == ((0.0, 1.0, 0.0), (1.0, 0.0, 0.0))


def test_projection_group_layout_fits_all_six_views_at_a_standard_scale() -> None:
    bounds = {
        "front": (-40.0, -6.0, 40.0, 6.0),
        "top": (-40.0, -25.0, 40.0, 25.0),
        "right": (-25.0, -6.0, 25.0, 6.0),
        "left": (-25.0, -6.0, 25.0, 6.0),
        "bottom": (-40.0, -25.0, 40.0, 25.0),
        "rear": (-40.0, -6.0, 40.0, 6.0),
    }
    layout = _fit_projection_group_layout(
        bounds,
        convention="third_angle",
        page_width_mm=297.0,
        page_height_mm=210.0,
        spacing_x_mm=15.0,
        spacing_y_mm=15.0,
    )
    assert layout["scale"] == 0.75
    assert set(layout["positions_mm"]) == set(bounds)
    assert layout["positions_mm"]["left"][0] < layout["positions_mm"]["front"][0]
    assert layout["positions_mm"]["right"][0] > layout["positions_mm"]["front"][0]
    assert layout["positions_mm"]["top"][1] > layout["positions_mm"]["front"][1]
    assert layout["positions_mm"]["bottom"][1] < layout["positions_mm"]["front"][1]
    for minimum_x, minimum_y, maximum_x, maximum_y in layout["page_bounds_mm"].values():
        assert 0.0 <= minimum_x <= maximum_x <= 297.0
        assert 0.0 <= minimum_y <= maximum_y <= 210.0


def test_first_angle_projection_layout_places_views_by_convention() -> None:
    bounds = {
        "front": (-20.0, -10.0, 20.0, 10.0),
        "top": (-20.0, -15.0, 20.0, 15.0),
        "right": (-15.0, -10.0, 15.0, 10.0),
        "left": (-15.0, -10.0, 15.0, 10.0),
    }
    layout = _fit_projection_group_layout(
        bounds,
        convention="first_angle",
        page_width_mm=297.0,
        page_height_mm=210.0,
        spacing_x_mm=15.0,
        spacing_y_mm=15.0,
    )
    assert layout["positions_mm"]["right"][0] < layout["positions_mm"]["front"][0]
    assert layout["positions_mm"]["left"][0] > layout["positions_mm"]["front"][0]
    assert layout["positions_mm"]["top"][1] < layout["positions_mm"]["front"][1]


def test_projection_group_layout_uses_the_template_drawable_region() -> None:
    bounds = {
        "front": (-40.0, -10.0, 40.0, 10.0),
        "top": (-40.0, -25.0, 40.0, 25.0),
        "right": (-25.0, -10.0, 25.0, 10.0),
    }
    layout = _fit_projection_group_layout(
        bounds,
        convention="third_angle",
        page_width_mm=297.0,
        page_height_mm=210.0,
        spacing_x_mm=15.0,
        spacing_y_mm=15.0,
        drawable_bounds_mm=(20.0, 10.0, 287.0, 152.0),
    )

    assert layout["drawable_bounds_mm"] == [20.0, 10.0, 287.0, 152.0]
    for minimum_x, minimum_y, maximum_x, maximum_y in layout["page_bounds_mm"].values():
        assert 20.0 <= minimum_x <= maximum_x <= 287.0
        assert 10.0 <= minimum_y <= maximum_y <= 152.0


def test_projection_group_reserves_the_template_annotation_clearance() -> None:
    geometry = {
        "drawing_bounds_mm": {
            "min_x_mm": 27.0,
            "min_y_mm": 65.0,
            "max_x_mm": 280.0,
            "max_y_mm": 193.0,
        },
        "drawing_clearance_mm": 7.0,
    }

    bounds = _projection_fit_bounds(geometry)

    assert bounds == (34.0, 72.0, 273.0, 186.0)
    layout = _fit_projection_group_layout(
        {
            "front": (-40.0, -10.0, 40.0, 10.0),
            "top": (-40.0, -25.0, 40.0, 25.0),
            "right": (-25.0, -10.0, 25.0, 10.0),
        },
        convention="third_angle",
        page_width_mm=297.0,
        page_height_mm=210.0,
        spacing_x_mm=15.0,
        spacing_y_mm=15.0,
        drawable_bounds_mm=bounds,
    )
    assert layout["scale"] == 1.0


def test_drawing_surface_receives_bounded_job_status_and_cancel() -> None:
    definition = native_background_capability_definition()
    assert tuple(variant.operation for variant in definition.variants) == (
        "status",
        "cancel",
    )
    assert all("drawing" in variant.surface_ids for variant in definition.variants)
    schema = definition.provider_schema(("status", "cancel"))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":")).casefold()
    assert schema["parameters"]["properties"]["operation"]["enum"] == [
        "status",
        "cancel",
    ]
    assert "path" not in encoded
