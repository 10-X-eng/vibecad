# SPDX-License-Identifier: LGPL-2.1-or-later

"""Strong provider contract for structured transfinite mesh resources."""

from __future__ import annotations

from VibeCADNativeAnalyzeMeshRefinementSchema import (
    _TARGET,
    _closed,
    _references,
)
from VibeCADNativeAnalyzeMeshSchema import _TARGET as _MESH_TARGET
from VibeCADNativeAnalyzeModelSchema import _LABEL
from VibeCADNativeAnalyzeMeshRefinementValues import STRUCTURED_MODES
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


ANALYZE_STRUCTURED_MESH_CAPABILITY_NAME = "analyze.structured_mesh"

_UPDATE_EXACT_TARGET_BY_OPERATION = {
    "update_transfinite_curve": "ExactGmshTransfiniteCurveAndEdges",
    "update_transfinite_surface": "ExactGmshTransfiniteSurfaceAndGeometry",
    "update_transfinite_volume": "ExactGmshTransfiniteVolumeAndSolids",
}
_POSITIVE = {"type": "number", "exclusiveMinimum": 0.0, "maximum": 1.0e12}
_DISTRIBUTION = {"type": "string", "enum": ["constant", "bump", "progression"]}
_ORIENTATION = {
    "type": "string",
    "enum": ["left", "right", "alternate_right", "alternate_left"],
}
_AUTOMATION = {
    "recombine": {"type": "boolean"},
    "triangle_orientation": _ORIENTATION,
    "use_automation": {"type": "boolean"},
    "nodes": {"type": "integer", "minimum": 2, "maximum": 1000000},
    "coefficient": _POSITIVE,
    "distribution": _DISTRIBUTION,
    "inverted": {"type": "boolean"},
}
_DEFINITIONS = {
    "transfinite_curve": _closed(
        {
            "nodes": {"type": "integer", "minimum": 2, "maximum": 1000000},
            "coefficient": _POSITIVE,
            "distribution": _DISTRIBUTION,
            "inverted": {"type": "boolean"},
        },
        ("nodes", "coefficient", "distribution", "inverted"),
    ),
    "transfinite_surface": _closed(
        dict(_AUTOMATION),
        tuple(_AUTOMATION),
    ),
    "transfinite_volume": _closed(
        {"mixed_elements": {"type": "boolean"}, **_AUTOMATION},
        ("mixed_elements", *tuple(_AUTOMATION)),
    ),
}
_REFERENCES = {
    "transfinite_curve": _references(("Edge",)),
    "transfinite_surface": _references(("Face", "Vertex")),
    "transfinite_volume": _references(("Solid",)),
}
_ACTIONS = {
    "transfinite_curve": "FEM_MeshTransfiniteCurve",
    "transfinite_surface": "FEM_MeshTransfiniteSurface",
    "transfinite_volume": "FEM_MeshTransfiniteVolume",
}


def _create(mode: str) -> dict:
    return _closed(
        {
            "mesh": _MESH_TARGET,
            "label": _LABEL,
            "references": _REFERENCES[mode],
            "definition": _DEFINITIONS[mode],
        },
        ("mesh", "label", "references", "definition"),
    )


def _update(mode: str) -> dict:
    return _closed(
        {
            "target": _TARGET,
            "label": _LABEL,
            "references": _REFERENCES[mode],
            "definition": _DEFINITIONS[mode],
        },
        ("target",),
        minimum=3,
    )


def _variant(operation: str, description: str, action_id: str, parameters: dict) -> NativeCapabilityVariant:
    return NativeCapabilityVariant(
        operation=operation,
        description=description,
        action_ids=frozenset({action_id}),
        surface_ids=frozenset({"analyze"}),
        exact_target_type=_UPDATE_EXACT_TARGET_BY_OPERATION.get(
            operation,
            "ExactGmshTransfiniteResourceAndGeometry",
        ),
        transaction_behavior="document",
        background_required=False,
        parameters=parameters,
    )


def analyze_structured_mesh_capability_definition() -> NativeCapabilityDefinition:
    names = {
        "transfinite_curve": "curve node distribution",
        "transfinite_surface": "structured surface definition",
        "transfinite_volume": "structured volume definition",
    }
    variants = []
    for mode in STRUCTURED_MODES:
        variants.append(
            _variant(
                f"create_{mode}",
                f"Create one typed transfinite {names[mode]} on exact geometry.",
                _ACTIONS[mode],
                _create(mode),
            )
        )
        variants.append(
            _variant(
                f"update_{mode}",
                f"Edit one exact transfinite {names[mode]} in place.",
                "VibeCAD_AnalyzeUpdate" + "".join(part.title() for part in mode.split("_")),
                _update(mode),
            )
        )
    return NativeCapabilityDefinition(
        name=ANALYZE_STRUCTURED_MESH_CAPABILITY_NAME,
        description=(
            "Create or edit exact Gmsh transfinite curve, surface, and volume resources "
            "with explicit distribution and automation settings."
        ),
        primary_classification="mutation",
        variants=tuple(variants),
    )


def register_analyze_structured_mesh_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(analyze_structured_mesh_capability_definition())
