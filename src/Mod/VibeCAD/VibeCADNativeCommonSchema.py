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


def _drawing_projected_view_ref() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "object_name": deepcopy(_OBJECT_NAME),
            "expected_state_sha256": deepcopy(_SHA256),
        },
        "required": ["object_name", "expected_state_sha256"],
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
        description="Read concise live state owned by the current Native ribbon.",
        primary_classification="read",
        variants=(
            _variant(
                "active",
                "Read the active ribbon domain, revision, and bounded working set.",
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
        description="Control or capture the active view without changing model structure.",
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
                "set_grid",
                "Set grid visibility explicitly.",
                ("VibeCAD_ToggleGrid",),
                parameters=_parameters(
                    {"visible": {"type": "boolean"}},
                    ("visible",),
                ),
                transaction_behavior="presentation",
            ),
            _variant(
                "set_object_visibility",
                (
                    "Show or hide exact scene-bearing model objects. Use state.read first; "
                    "this does not alter geometry or History ownership."
                ),
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
                "Capture a bounded image framed around all visible geometry.",
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
                "capture_active_sketch",
                "Capture a bounded image framed around the human-opened sketch.",
                ("VibeCAD_NativeCaptureView",),
            ),
        ),
    )
    inspect = NativeCapabilityDefinition(
        name="inspect.query",
        description="Measure or inspect exact live geometry with explicit units.",
        primary_classification="read",
        variants=(
            _variant(
                "distance",
                "Measure the shortest distance between two exact elements in mm.",
                ("Std_Measure",),
                parameters=_parameters(
                    {"first": _element_ref(), "second": _element_ref()},
                    ("first", "second"),
                ),
                exact_target_type="SubelementPair",
            ),
            _variant(
                "angle",
                "Measure the angle between exact edge tangents or face normals in degrees.",
                ("Std_Measure",),
                parameters=_parameters(
                    {"first": _element_ref(), "second": _element_ref()},
                    ("first", "second"),
                ),
                exact_target_type="EdgeOrFacePair",
            ),
            _variant(
                "radius",
                "Measure one exact circular edge or cylindrical face radius in mm.",
                ("Std_Measure",),
                parameters=_parameters({"target": _element_ref()}, ("target",)),
                exact_target_type="EdgeOrFace",
            ),
            _variant(
                "mass_properties",
                "Read bounded volume, area, density, mass, and centers for exact objects.",
                ("Std_MassProperties",),
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
                exact_target_type="PartFeature[]",
            ),
            _variant(
                "visual_result",
                "Read bounded statistics from one exact visual-inspection result.",
                ("Inspection_VisualInspection",),
                parameters=_parameters({"target": _object_ref()}, ("target",)),
                exact_target_type="Inspection::Feature",
            ),
            _variant(
                "element",
                "Read concise geometry for one exact subelement.",
                ("Inspection_InspectElement",),
                parameters=_parameters({"target": _element_ref()}, ("target",)),
                exact_target_type="Subelement",
            ),
            _variant(
                "drawing_projected_geometry",
                (
                    "Read up to 48 exact Edge0, Vertex0, or Face0 elements from one "
                    "Drawing view. Use an empty prior hash only at offset zero."
                ),
                ("VibeCAD_DrawingInspectProjectedGeometry",),
                parameters=_parameters(
                    {
                        "view": _drawing_projected_view_ref(),
                        "offset": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 4096,
                        },
                        "page_size": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 48,
                        },
                        "expected_projection_state_sha256": {
                            "anyOf": [
                                {"type": "string", "const": ""},
                                deepcopy(_SHA256),
                            ]
                        },
                    },
                    (
                        "view",
                        "offset",
                        "page_size",
                        "expected_projection_state_sha256",
                    ),
                ),
                exact_target_type="ExactDrawingProjectedGeometryPage",
                surface_ids=frozenset({"drawing"}),
                provider_supplemental=True,
            ),
            _variant(
                "validity",
                "Check topology counts and validity for one exact Part object.",
                ("Part_CheckGeometry",),
                parameters=_parameters({"target": _object_ref()}, ("target",)),
                exact_target_type="Part::Feature",
            ),
        ),
    )
    save = NativeCapabilityDefinition(
        name="document.save",
        description="Save the exact active document to its existing file path.",
        primary_classification="export",
        variants=(
            _variant(
                "existing_path",
                "Save only after the human has chosen a file path with Save As.",
                ("Std_Save",),
                transaction_behavior="none",
                surface_ids=DOCUMENT_SAVE_SURFACES,
            ),
        ),
    )
    undo = NativeCapabilityDefinition(
        name="document.undo",
        description="Undo only the latest exact operation from this assistant run.",
        primary_classification="mutation",
        variants=(
            _variant(
                "assistant_local",
                "Undo the latest assistant-owned history entry; never undo human history.",
                ("Std_Undo",),
                transaction_behavior="history",
            ),
        ),
    )
    return state, view, inspect, save, undo


def register_common_capability_definitions(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for definition in common_capability_definitions():
        registry.register_shared_definition(definition)
