# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for exact Drawing image and geometric hatches."""

from __future__ import annotations

from copy import deepcopy

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDrawingHatchState import MAX_DRAWING_HATCH_FACES


DRAWING_HATCH_CAPABILITY_NAME = "drawing.hatch"
DRAWING_HATCH_OPERATIONS = (
    "create_image_default",
    "create_geometric_default",
    "create_image_file",
    "create_geometric_file",
    "read_defaults",
)
_IMAGE_ACTIONS = frozenset({"TechDraw_Hatch"})
_GEOMETRIC_ACTIONS = frozenset({"TechDraw_GeometricHatch"})
_ALL_ACTIONS = _IMAGE_ACTIONS | _GEOMETRIC_ACTIONS
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
_VIEW = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _SHA256,
        "expected_projection_state_sha256": _SHA256,
    },
    (
        "object_name",
        "expected_state_sha256",
        "expected_projection_state_sha256",
    ),
)
_FACE = _closed(
    {
        "subelement": {
            "type": "string",
            "pattern": r"^Face(?:0|[1-9][0-9]*)$",
            "maxLength": 32,
        },
        "expected_element_state_sha256": _SHA256,
    },
    ("subelement", "expected_element_state_sha256"),
)
_OFFSET = _closed(
    {
        "x_mm": {"type": "number", "minimum": -1_000_000.0, "maximum": 1_000_000.0},
        "y_mm": {"type": "number", "minimum": -1_000_000.0, "maximum": 1_000_000.0},
    },
    ("x_mm", "y_mm"),
)
_COLOR = _closed(
    {
        "red": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "green": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "blue": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
    ("red", "green", "blue"),
)
_STYLE_COMMON = {
    "scale": {"type": "number", "exclusiveMinimum": 0.0, "maximum": 1000.0},
    "rotation_degrees": {"type": "number", "minimum": -360.0, "maximum": 360.0},
    "offset_mm": _OFFSET,
    "color_rgb": _COLOR,
}
_IMAGE_STYLE = _closed(
    deepcopy(_STYLE_COMMON),
    ("scale", "rotation_degrees", "offset_mm", "color_rgb"),
)
_GEOMETRIC_STYLE = _closed(
    {
        **deepcopy(_STYLE_COMMON),
        "line_width_mm": {"type": "number", "minimum": 0.0, "maximum": 100.0},
    },
    (
        "scale",
        "rotation_degrees",
        "offset_mm",
        "line_width_mm",
        "color_rgb",
    ),
)


def _parameters(kind: str) -> dict:
    properties = {
        "page": _PAGE,
        "view": _VIEW,
        "faces": {
            "type": "array",
            "items": _FACE,
            "minItems": 1,
            "maxItems": MAX_DRAWING_HATCH_FACES,
            "uniqueItems": True,
            "description": (
                "Distinct exact projected FaceN states from drawing_projected_geometry."
            ),
        },
        "label": {
            "type": "string",
            "minLength": 1,
            "maxLength": 160,
            "description": (
                "Preferred document label. FreeCAD may append or replace a trailing "
                "numeric suffix to keep labels unique; the result reports the exact label."
            ),
        },
        "style": _IMAGE_STYLE if kind == "image" else _GEOMETRIC_STYLE,
    }
    required = ["page", "view", "faces", "label", "style"]
    if kind == "geometric":
        properties["pattern_name"] = {
            "type": "string",
            "minLength": 1,
            "maxLength": 128,
            "description": (
                "Exact PAT catalog name; read_defaults reports configured names."
            ),
        }
        required.append("pattern_name")
    return _closed(properties, tuple(required))


def drawing_hatch_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=DRAWING_HATCH_CAPABILITY_NAME,
        description=(
            "Apply durable image or PAT hatches to exact projected faces; custom files "
            "are selected only by the human and paths are never provider-visible."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="create_image_default",
                description=(
                    "Create one image hatch from the configured pattern with explicit "
                    "label, exact faces, scale, rotation, offset, and color."
                ),
                action_ids=_IMAGE_ACTIONS,
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingProjectedFacesAndImageHatchStyle",
                transaction_behavior="document",
                background_required=False,
                parameters=_parameters("image"),
            ),
            NativeCapabilityVariant(
                operation="create_geometric_default",
                description=(
                    "Create one PAT hatch from the configured catalog with an exact "
                    "pattern name, faces, label, and complete line style."
                ),
                action_ids=_GEOMETRIC_ACTIONS,
                surface_ids=frozenset({"drawing"}),
                exact_target_type=(
                    "ExactDrawingProjectedFacesAndGeometricHatchStyle"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters=_parameters("geometric"),
            ),
            NativeCapabilityVariant(
                operation="create_image_file",
                description=(
                    "Ask the human for one SVG or bitmap pattern, then create one exact "
                    "durable image hatch without exposing the selected path."
                ),
                action_ids=_IMAGE_ACTIONS,
                surface_ids=frozenset({"drawing"}),
                exact_target_type=(
                    "HumanAuthorizedImagePatternAndExactDrawingProjectedFaces"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters=_parameters("image"),
                provider_supplemental=True,
            ),
            NativeCapabilityVariant(
                operation="create_geometric_file",
                description=(
                    "Ask the human for one PAT file, then create one exact durable "
                    "geometric hatch using the requested catalog name."
                ),
                action_ids=_GEOMETRIC_ACTIONS,
                surface_ids=frozenset({"drawing"}),
                exact_target_type=(
                    "HumanAuthorizedPatPatternAndExactDrawingProjectedFaces"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters=_parameters("geometric"),
                provider_supplemental=True,
            ),
            NativeCapabilityVariant(
                operation="read_defaults",
                description=(
                    "Read the configured path-free image pattern summary, PAT catalog, "
                    "and default styles before choosing a hatch request."
                ),
                action_ids=_ALL_ACTIONS,
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ConfiguredDrawingHatchDefaultsAndPatCatalog",
                transaction_behavior="none",
                background_required=False,
                parameters=_closed({}, ()),
                provider_supplemental=True,
            ),
        ),
    )


def register_drawing_hatch_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(drawing_hatch_capability_definition())
