# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for exact Drawing presentation state."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


DRAWING_PRESENTATION_CAPABILITY_NAMES = (
    "drawing.show_page",
    "drawing.page_frames",
    "drawing.page_grid",
    "drawing.hidden_edges",
)
_OBJECT_NAME = {
    "type": "string",
    "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
    "maxLength": 128,
}
_SHA256 = {
    "type": "string",
    "pattern": r"^[0-9a-f]{64}$",
    "minLength": 64,
    "maxLength": 64,
}


def _closed(properties: dict, required: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_FRAME_PAGE = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _SHA256,
        "expected_frame_visibility_state_sha256": _SHA256,
    },
    (
        "object_name",
        "expected_state_sha256",
        "expected_frame_visibility_state_sha256",
    ),
)
_PAGE = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _SHA256,
    },
    ("object_name", "expected_state_sha256"),
)
_GRID_PAGE = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _SHA256,
        "expected_grid_visibility_state_sha256": _SHA256,
    },
    (
        "object_name",
        "expected_state_sha256",
        "expected_grid_visibility_state_sha256",
    ),
)
_VIEW = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _SHA256,
        "expected_hidden_edge_visibility_state_sha256": _SHA256,
    },
    (
        "object_name",
        "expected_state_sha256",
        "expected_hidden_edge_visibility_state_sha256",
    ),
)


def drawing_presentation_capability_definitions() -> tuple[
    NativeCapabilityDefinition, ...
]:
    return (
        NativeCapabilityDefinition(
            name="drawing.show_page",
            description="Show a Drawing page.",
            primary_classification="view",
            variants=(
            NativeCapabilityVariant(
                operation="show",
                description="Show a Drawing page.",
                action_ids=frozenset({"TechDrawContextShowDrawing"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type="TechDraw::DrawPage",
                transaction_behavior="presentation",
                background_required=False,
                parameters=_closed({"page": _PAGE}, ("page",)),
            ),
            ),
        ),
        NativeCapabilityDefinition(
            name="drawing.page_frames",
            description="Set Drawing frame and vertex visibility.",
            primary_classification="view",
            variants=(
            NativeCapabilityVariant(
                operation="set_visibility",
                description="Set frame and vertex visibility on one active Drawing page.",
                action_ids=frozenset(
                    {"TechDraw_ToggleFrame", "TechDrawContextToggleFrames"}
                ),
                surface_ids=frozenset({"drawing"}),
                exact_target_type=(
                    "HumanActiveDrawingPageAndExactFrameVisibilityState"
                ),
                transaction_behavior="presentation",
                background_required=False,
                parameters=_closed(
                    {
                        "page": _FRAME_PAGE,
                        "visible": {
                            "type": "boolean",
                            "description": "Explicit desired visibility.",
                        },
                    },
                    ("page", "visible"),
                ),
            ),
            ),
        ),
        NativeCapabilityDefinition(
            name="drawing.page_grid",
            description="Set Drawing grid visibility.",
            primary_classification="view",
            variants=(
            NativeCapabilityVariant(
                operation="set_visibility",
                description="Set Drawing grid visibility.",
                action_ids=frozenset({"TechDrawContextToggleGrid"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type=("HumanActiveDrawingPageAndExactGridVisibilityState"),
                transaction_behavior="presentation",
                background_required=False,
                parameters=_closed(
                    {
                        "page": _GRID_PAGE,
                        "visible": {
                            "type": "boolean",
                            "description": "Explicit desired visibility.",
                        },
                    },
                    ("page", "visible"),
                ),
            ),
            ),
        ),
        NativeCapabilityDefinition(
            name="drawing.hidden_edges",
            description="Set hidden projected-edge visibility.",
            primary_classification="view",
            variants=(
            NativeCapabilityVariant(
                operation="set_visibility",
                description="Set hidden projected-edge visibility.",
                action_ids=frozenset({"TechDraw_ShowAll"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type=(
                    "HumanActiveDrawingViewAndExactHiddenEdgeVisibilityState"
                ),
                transaction_behavior="presentation",
                background_required=False,
                parameters=_closed(
                    {
                        "view": _VIEW,
                        "visible": {
                            "type": "boolean",
                            "description": "Explicit desired visibility.",
                        },
                    },
                    ("view", "visible"),
                ),
            ),
            ),
        ),
    )


def register_drawing_presentation_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for definition in drawing_presentation_capability_definitions():
        registry.register_definition(definition)
