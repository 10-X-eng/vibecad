# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded provider contracts for capabilities shared by Native surfaces."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeInspect import MAX_INSPECTION_ELEMENTS
from VibeCADNativeMeasure import MAX_RADIUS_MEASUREMENTS
from VibeCADRibbonSurface import SURFACE_IDS


COMMON_NATIVE_SURFACES = frozenset(SURFACE_IDS - {"unavailable"})
DOCUMENT_SAVE_SURFACES = frozenset(COMMON_NATIVE_SURFACES - {"sketch.edit"})
_OBJECT_NAME = {
    "type": "string",
    "minLength": 1,
    "maxLength": 128,
    "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
}
_SUBELEMENT_NAME = {
    "type": "string",
    "pattern": r"^(?:Face|Edge|Vertex)[1-9][0-9]*$",
    "maxLength": 32,
}
_SHA256 = {
    "type": "string",
    "pattern": r"^[0-9a-f]{64}$",
    "minLength": 64,
    "maxLength": 64,
}


def _parameters(
    properties: dict[str, Any] | None = None,
    required: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": deepcopy(properties or {}),
        "required": list(required),
        "additionalProperties": False,
    }


def _object_ref() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"object_name": deepcopy(_OBJECT_NAME)},
        "required": ["object_name"],
        "additionalProperties": False,
    }


def _element_ref() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "object_name": deepcopy(_OBJECT_NAME),
            "subelement": deepcopy(_SUBELEMENT_NAME),
        },
        "required": ["object_name", "subelement"],
        "additionalProperties": False,
    }


def _targets(
    item: dict[str, Any],
    *,
    minimum: int,
    maximum: int,
) -> dict[str, Any]:
    return {
        "type": "array",
        "items": item,
        "minItems": minimum,
        "maxItems": maximum,
        "uniqueItems": True,
    }


def _drawing_projected_view_ref() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "object_name": deepcopy(_OBJECT_NAME),
            "expected_state_sha256": {
                "default": "",
                "anyOf": [
                    {"type": "string", "const": ""},
                    deepcopy(_SHA256),
                ],
            },
            "expected_projection_state_sha256": {
                "default": "",
                "anyOf": [
                    {"type": "string", "const": ""},
                    deepcopy(_SHA256),
                ],
            },
        },
        "required": ["object_name"],
        "additionalProperties": False,
    }


def _variant(
    operation: str,
    description: str,
    action_ids: tuple[str, ...],
    *,
    parameters: dict[str, Any] | None = None,
    exact_target_type: str | None = None,
    transaction_behavior: str = "none",
    surface_ids: frozenset[str] = COMMON_NATIVE_SURFACES,
    provider_supplemental: bool = False,
) -> NativeCapabilityVariant:
    return NativeCapabilityVariant(
        operation=operation,
        description=description,
        action_ids=frozenset(action_ids),
        surface_ids=surface_ids,
        exact_target_type=exact_target_type,
        transaction_behavior=transaction_behavior,
        background_required=False,
        parameters=parameters or _parameters(),
        provider_supplemental=provider_supplemental,
    )


def common_capability_definitions() -> tuple[NativeCapabilityDefinition, ...]:
    state = NativeCapabilityDefinition(
        name="state.read",
        description="Read current CAD state or the current mouse selection.",
        primary_classification="read",
        variants=(
            _variant(
                "active",
                "Read the current CAD work, revision, and bounded working set.",
                ("VibeCAD_NativeReadState",),
            ),
            _variant(
                "selection",
                "Read the exact ordered current selection and subelements.",
                ("VibeCAD_NativeReadSelection",),
            ),
        ),
    )
    view = NativeCapabilityDefinition(
        name="view.control",
        description="Control or capture the active view.",
        primary_classification="view",
        variants=(
            _variant(
                "fit_all",
                "Fit all visible geometry.",
                ("Std_ViewFitAll",),
                transaction_behavior="presentation",
            ),
            _variant(
                "isometric",
                "Set the active 3D view to isometric.",
                ("Std_ViewIsometric",),
                transaction_behavior="presentation",
            ),
            _variant(
                "set_isometric",
                "Set the active 3D view to isometric.",
                ("Std_ViewIsometric",),
                transaction_behavior="presentation",
            ),
            *(
                _variant(
                    f"set_{orientation}",
                    f"Set the active 3D view to {orientation}.",
                    (action_id,),
                    transaction_behavior="presentation",
                )
                for orientation, action_id in (
                    ("front", "Std_ViewFront"),
                    ("rear", "Std_ViewRear"),
                    ("left", "Std_ViewLeft"),
                    ("right", "Std_ViewRight"),
                    ("top", "Std_ViewTop"),
                    ("bottom", "Std_ViewBottom"),
                )
            ),
            _variant(
                "set_grid",
                "Set grid visibility explicitly.",
                ("VibeCAD_ToggleGrid",),
                parameters=_parameters(
                    {"visible": {"type": "boolean", "default": True}},
                ),
                transaction_behavior="presentation",
            ),
            _variant(
                "set_section_view",
                "Set 3D section-view clipping explicitly.",
                ("VibeCAD_SectionView",),
                parameters=_parameters(
                    {"visible": {"type": "boolean"}},
                    ("visible",),
                ),
                transaction_behavior="presentation",
            ),
            _variant(
                "set_object_visibility",
                "Set exact model-object visibility.",
                ("VibeCAD_NativeSetObjectVisibility",),
                parameters=_parameters(
                    {
                        "targets": {
                            "type": "array",
                            "items": _object_ref(),
                            "minItems": 1,
                            "maxItems": 16,
                            "uniqueItems": True,
                        },
                        "visible": {"type": "boolean"},
                    },
                    ("targets", "visible"),
                ),
                exact_target_type="ModelPresentationObject[]",
                transaction_behavior="presentation",
                surface_ids=frozenset({"model"}),
            ),
            _variant(
                "capture_all",
                "Capture the active Drawing page or all visible 3D geometry.",
                ("VibeCAD_NativeCaptureView",),
            ),
            _variant(
                "capture_selection",
                "Capture a bounded image framed around the current selection.",
                ("VibeCAD_NativeCaptureView",),
            ),
            _variant(
                "capture_objects",
                "Capture a bounded image framed around exact objects.",
                ("VibeCAD_NativeCaptureView",),
                parameters=_parameters(
                    {
                        "targets": {
                            "type": "array",
                            "items": _object_ref(),
                            "minItems": 1,
                            "maxItems": 16,
                            "uniqueItems": True,
                        }
                    },
                    ("targets",),
                ),
                exact_target_type="DocumentObject[]",
            ),
            _variant(
                "capture_drawing_page",
                "Capture one exact Drawing page by its internal object name.",
                ("VibeCAD_NativeCaptureView",),
                parameters=_parameters(
                    {"page": _object_ref()},
                    ("page",),
                ),
                exact_target_type="TechDraw::DrawPage",
                surface_ids=frozenset({"drawing"}),
            ),
            _variant(
                "capture_active_sketch",
                "Capture a bounded image framed around the active Sketch.",
                ("VibeCAD_NativeCaptureView",),
                surface_ids=frozenset({"sketch.edit"}),
            ),
        ),
    )
    inspect = NativeCapabilityDefinition(
        name="inspect.query",
        description="Inspect 3D model geometry by Face1, Edge1, or Vertex1.",
        primary_classification="read",
        variants=(
            _variant(
                "distance",
                "Measure the shortest distance between two exact elements in mm.",
                ("Std_Measure",),
                parameters=_parameters(
                    {"targets": _targets(_element_ref(), minimum=2, maximum=2)},
                    ("targets",),
                ),
                exact_target_type="SubelementPair",
            ),
            _variant(
                "angle",
                "Measure the angle between exact edge tangents or face normals in degrees.",
                ("Std_Measure",),
                parameters=_parameters(
                    {"targets": _targets(_element_ref(), minimum=2, maximum=2)},
                    ("targets",),
                ),
                exact_target_type="EdgeOrFacePair",
            ),
            _variant(
                "radius",
                "Measure known circular edges or cylindrical faces in mm.",
                ("Std_Measure",),
                parameters=_parameters(
                    {
                        "targets": _targets(
                            _element_ref(),
                            minimum=1,
                            maximum=MAX_RADIUS_MEASUREMENTS,
                        )
                    },
                    ("targets",),
                ),
                exact_target_type="EdgeOrFace[]",
            ),
            _variant(
                "mass_properties",
                "Read volume, area, mass, and centers.",
                ("Std_MassProperties",),
                parameters=_parameters(
                    {
                        "targets": _targets(
                            _object_ref(),
                            minimum=1,
                            maximum=16,
                        )
                    },
                    ("targets",),
                ),
                exact_target_type="PartFeature[]",
            ),
            _variant(
                "inspection_result",
                "Read statistics from one Inspection result.",
                ("Inspection_VisualInspection",),
                parameters=_parameters(
                    {"targets": _targets(_object_ref(), minimum=1, maximum=1)},
                    ("targets",),
                ),
                exact_target_type="Inspection::Feature",
            ),
            _variant(
                "element",
                "Read each exact subelement's type and available size, endpoints, "
                "radius, center, or normal.",
                ("Inspection_InspectElement",),
                parameters=_parameters(
                    {
                        "targets": _targets(
                            _element_ref(),
                            minimum=1,
                            maximum=MAX_INSPECTION_ELEMENTS,
                        )
                    },
                    ("targets",),
                ),
                exact_target_type="Subelement[]",
            ),
            _variant(
                "validity",
                "Check topology counts and validity for one exact Part object.",
                ("Part_CheckGeometry",),
                parameters=_parameters(
                    {"targets": _targets(_object_ref(), minimum=1, maximum=1)},
                    ("targets",),
                ),
                exact_target_type="Part::Feature",
            ),
        ),
    )
    drawing_sources = NativeCapabilityDefinition(
        name="drawing.sources",
        description="List exact whole-object sources available for Drawing views.",
        primary_classification="read",
        variants=(
            _variant(
                "list",
                "Read one bounded page of exact Drawing sources.",
                ("VibeCAD_DrawingListSources",),
                parameters=_parameters(
                    {
                        "offset": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 1_000_000,
                            "default": 0,
                        },
                    }
                ),
                exact_target_type="ExactDrawingSourcePage",
                surface_ids=frozenset({"drawing"}),
                provider_supplemental=True,
            ),
        ),
    )
    projected_geometry = NativeCapabilityDefinition(
        name="drawing.projected_geometry",
        description=(
            "Read projected Drawing geometry by Face0, Edge0, or Vertex0."
        ),
        primary_classification="read",
        variants=(
            _variant(
                "read",
                "Read one bounded page of exact projected Drawing elements.",
                ("VibeCAD_DrawingInspectProjectedGeometry",),
                parameters=_parameters(
                    {
                        "view": _drawing_projected_view_ref(),
                        "offset": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 4096,
                            "default": 0,
                        },
                    },
                    ("view",),
                ),
                exact_target_type="ExactDrawingProjectedGeometryPage",
                surface_ids=frozenset({"drawing"}),
                provider_supplemental=True,
            ),
        ),
    )
    save = NativeCapabilityDefinition(
        name="document.save",
        description="Save the document.",
        primary_classification="export",
        variants=(
            _variant(
                "existing_path",
                "Save at the document's existing human-chosen path.",
                ("Std_Save",),
                transaction_behavior="none",
                surface_ids=DOCUMENT_SAVE_SURFACES,
            ),
        ),
    )
    undo = NativeCapabilityDefinition(
        name="document.undo",
        description="Undo the latest unchanged assistant operation.",
        primary_classification="mutation",
        variants=(
            _variant(
                "assistant_local",
                "Undo the latest unchanged assistant-owned history entry.",
                ("Std_Undo",),
                transaction_behavior="history",
            ),
        ),
    )
    return state, view, inspect, drawing_sources, projected_geometry, save, undo


def register_common_capability_definitions(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for definition in common_capability_definitions():
        registry.register_shared_definition(definition)
