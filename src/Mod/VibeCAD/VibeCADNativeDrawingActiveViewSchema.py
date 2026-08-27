# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for one exact active-viewport Drawing image."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


DRAWING_ACTIVE_VIEW_CAPABILITY_NAME = "drawing.active_view"
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
    {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _SHA256,
    },
    ("object_name", "expected_state_sha256"),
)
_VIEWPORT = _closed(
    {"expected_state_sha256": _SHA256},
    ("expected_state_sha256",),
)
_POSITION = _closed(
    {
        "x_mm": {"type": "number", "minimum": -10_000.0, "maximum": 10_000.0},
        "y_mm": {"type": "number", "minimum": -10_000.0, "maximum": 10_000.0},
    },
    ("x_mm", "y_mm"),
)
_CROP = {
    "oneOf": [
        _closed({"kind": {"type": "string", "const": "full"}}, ("kind",)),
        _closed(
            {
                "kind": {"type": "string", "const": "rectangle"},
                "width_mm": {
                    "type": "number",
                    "minimum": 0.1,
                    "maximum": 1000.0,
                },
                "height_mm": {
                    "type": "number",
                    "minimum": 0.1,
                    "maximum": 1000.0,
                },
            },
            ("kind", "width_mm", "height_mm"),
        ),
    ],
    "description": (
        "Capture the human command's fixed 1280 x 1024 frame, or crop both the "
        "render and Drawing image to an exact physical rectangle."
    ),
}
_RGB = _closed(
    {
        name: {"type": "integer", "minimum": 0, "maximum": 255}
        for name in ("red", "green", "blue")
    },
    ("red", "green", "blue"),
)
_BACKGROUND = {
    "oneOf": [
        _closed(
            {"kind": {"type": "string", "const": "transparent"}},
            ("kind",),
        ),
        _closed(
            {"kind": {"type": "string", "const": "viewport"}},
            ("kind",),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "solid"},
                "rgb": _RGB,
            },
            ("kind", "rgb"),
        ),
    ],
    "description": "Transparent, current 3D-view, or exact solid RGB background.",
}


def drawing_active_view_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=DRAWING_ACTIVE_VIEW_CAPABILITY_NAME,
        description=(
            "Embed one internally managed PNG of the human's exact turn-start 3D "
            "viewport on one exact Drawing page."
        ),
        primary_classification="mutation",
        # This private closed-path operation is routed by its explicit operation
        # name even though it currently has only one variant.  Preserve that
        # discriminator so future variants remain additive and callers never
        # have to change the request shape.
        preserve_operation_discriminator=True,
        variants=(
            NativeCapabilityVariant(
                operation="create_active_view",
                description=(
                    "Capture the current 3D viewport without changing its camera, "
                    "selection, visibility, or presentation state."
                ),
                action_ids=frozenset({"TechDraw_ActiveView"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type=(
                    "ExactDrawingPageActive3DViewportAndCaptureSettings"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "label": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160,
                        },
                        "page": _PAGE,
                        "viewport": _VIEWPORT,
                        "position": _POSITION,
                        "scale": {
                            "type": "number",
                            "minimum": 1.0e-6,
                            "maximum": 1000.0,
                        },
                        "crop": _CROP,
                        "background": _BACKGROUND,
                    },
                    (
                        "label",
                        "page",
                        "viewport",
                        "position",
                        "scale",
                        "crop",
                        "background",
                    ),
                ),
            ),
        ),
    )


def register_drawing_active_view_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(drawing_active_view_capability_definition())
