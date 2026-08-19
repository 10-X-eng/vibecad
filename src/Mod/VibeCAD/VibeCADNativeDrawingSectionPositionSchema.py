# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for exact Drawing section-view positioning."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


DRAWING_SECTION_POSITION_CAPABILITY_NAME = "drawing.section_position"
DRAWING_SECTION_POSITION_OPERATIONS = ("align_axis", "align_edge_to_vertex")
_ACTIONS = frozenset({"TechDraw_ExtensionPositionSectionView"})
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


_PAGE = _closed(
    {"object_name": _OBJECT_NAME, "expected_state_sha256": _SHA256},
    ("object_name", "expected_state_sha256"),
)
_SECTION = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_section_position_state_sha256": _SHA256,
    },
    ("object_name", "expected_section_position_state_sha256"),
)
_SECTION_WITH_PROJECTION = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_section_position_state_sha256": _SHA256,
        "expected_projection_state_sha256": _SHA256,
    },
    (
        "object_name",
        "expected_section_position_state_sha256",
        "expected_projection_state_sha256",
    ),
)
_BASE_VIEW = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _SHA256,
        "expected_projection_state_sha256": _SHA256,
        "expected_alignment_base_state_sha256": _SHA256,
    },
    (
        "object_name",
        "expected_state_sha256",
        "expected_projection_state_sha256",
        "expected_alignment_base_state_sha256",
    ),
)


def _element(kind: str) -> dict:
    prefix = "Edge" if kind == "edge" else "Vertex"
    return _closed(
        {
            "name": {
                "type": "string",
                "pattern": rf"^{prefix}(?:0|[1-9][0-9]*)$",
                "maxLength": 32,
            },
            "expected_element_state_sha256": _SHA256,
        },
        ("name", "expected_element_state_sha256"),
    )


def drawing_section_position_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=DRAWING_SECTION_POSITION_CAPABILITY_NAME,
        description=(
            "Position one exact standard section view either on an explicit base "
            "axis or by aligning one exact section edge to one exact base vertex."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="align_axis",
                description="Align the section view to an explicit base axis.",
                action_ids=_ACTIONS,
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingSectionViewAndExplicitBaseAxis",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "page": _PAGE,
                        "section_view": _SECTION,
                        "axis": {
                            "type": "string",
                            "enum": ["horizontal", "vertical"],
                        },
                    },
                    ("page", "section_view", "axis"),
                ),
            ),
            NativeCapabilityVariant(
                operation="align_edge_to_vertex",
                description=(
                    "Move one exact section view so one exact straight section "
                    "edge passes through one exact projected base vertex."
                ),
                action_ids=_ACTIONS,
                surface_ids=frozenset({"drawing"}),
                exact_target_type=(
                    "ExactDrawingSectionEdgeAndBaseVertexAlignment"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "page": _PAGE,
                        "section_view": _SECTION_WITH_PROJECTION,
                        "section_edge": _element("edge"),
                        "base_view": _BASE_VIEW,
                        "base_vertex": _element("vertex"),
                    },
                    (
                        "page",
                        "section_view",
                        "section_edge",
                        "base_view",
                        "base_vertex",
                    ),
                ),
            ),
        ),
    )


def register_drawing_section_position_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(drawing_section_position_capability_definition())
