# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused contracts for responsive exact Native Drawing projections."""

from __future__ import annotations

import json

from VibeCADNativeBackgroundSchema import native_background_capability_definition
from VibeCADNativeDrawingView import standard_view_line_flags
from VibeCADNativeDrawingViewSchema import drawing_view_capability_definition
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


def test_drawing_views_are_two_closed_background_operations() -> None:
    definition = drawing_view_capability_definition()
    assert definition.primary_classification == "mutation"
    assert len(definition.variants) == 2
    variant, broken = definition.variants
    assert variant.operation == "create_standard_view"
    assert variant.action_ids == frozenset({"TechDraw_View"})
    assert variant.surface_ids == frozenset({"drawing"})
    assert variant.exact_target_type == "ExactDrawingPageSourcesAndProjectionSettings"
    assert variant.transaction_behavior == "background"
    assert variant.background_required is True

    schema = definition.provider_schema((variant.operation,))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":")).casefold()
    assert "unknown" not in encoded
    assert "path" not in encoded
    branch = _branch(schema, variant.operation)
    assert branch["additionalProperties"] is False
    assert branch["required"] == [
        "operation",
        "label",
        "page",
        "sources",
        "orientation",
        "position",
        "scale",
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

    assert broken.operation == "create_broken_view"
    assert broken.action_ids == frozenset({"TechDraw_BrokenView"})
    assert broken.surface_ids == frozenset({"drawing"})
    assert broken.exact_target_type == (
        "ExactDrawingPageSourcesBreakDefinitionsAndProjectionSettings"
    )
    assert broken.transaction_behavior == "background"
    assert broken.background_required is True
    broken_schema = definition.provider_schema((broken.operation,))
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
        "operation",
        "label",
        "page",
        "sources",
        "breaks",
        "gap_mm",
        "orientation",
        "position",
        "scale",
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
