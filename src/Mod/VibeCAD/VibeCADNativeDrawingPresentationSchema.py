# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for exact Drawing presentation state."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


DRAWING_PRESENTATION_CAPABILITY_NAME = "drawing.presentation"
DRAWING_PRESENTATION_OPERATIONS = (
    "show",
    "set_frame_visibility",
    "set_grid_visibility",
    "set_hidden_edges_visible",
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


def drawing_presentation_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=DRAWING_PRESENTATION_CAPABILITY_NAME,
        description=(
            "Set transient presentation state on the human-active Drawing page "
            "without changing the document."
        ),
        primary_classification="view",
        variants=(
            NativeCapabilityVariant(
                operation="show",
                description=(
                    "Open and activate one exact current-History Drawing page "
                    "without changing the document or using GUI selection."
                ),
                action_ids=frozenset({"TechDrawContextShowDrawing"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type="TechDraw::DrawPage",
                transaction_behavior="presentation",
                background_required=False,
                parameters=_closed({"page": _PAGE}, ("page",)),
            ),
            NativeCapabilityVariant(
                operation="set_frame_visibility",
                description=(
                    "Show or hide view frames and vertices explicitly on one exact, "
                    "human-active Drawing page. View Frames Visibility must be Manual."
                ),
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
                            "description": (
                                "Explicit desired visibility; never a toggle."
                            ),
                        },
                    },
                    ("page", "visible"),
                ),
            ),
            NativeCapabilityVariant(
                operation="set_grid_visibility",
                description=(
                    "Show or hide the Drawing grid explicitly on one exact, "
                    "human-active page."
                ),
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
                            "description": "Explicit desired visibility; never a toggle.",
                        },
                    },
                    ("page", "visible"),
                ),
            ),
            NativeCapabilityVariant(
                operation="set_hidden_edges_visible",
                description=(
                    "Explicitly show or hide otherwise invisible projected edges "
                    "for one exact view on the human-active Drawing page."
                ),
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
                            "description": "Explicit desired visibility; never a toggle.",
                        },
                    },
                    ("view", "visible"),
                ),
            ),
        ),
    )


def register_drawing_presentation_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(drawing_presentation_capability_definition())
