# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for Gmsh refinement-field composition."""

from __future__ import annotations

from VibeCADNativeAnalyzeMeshRefinementSchema import _TARGET, _closed, _references
from VibeCADNativeAnalyzeMeshSchema import _TARGET as _MESH_TARGET
from VibeCADNativeAnalyzeModelSchema import _LABEL
from VibeCADNativeAnalyzeMeshFieldValues import ADVANCED_KINDS, MANIPULATION_KINDS
from VibeCADNativeAnalyzeResultState import RESULT_TARGET
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


ANALYZE_MESH_FIELD_CAPABILITY_NAME = "analyze.mesh_field"
_POSITIVE = {"type": "number", "exclusiveMinimum": 0.0, "maximum": 1.0e12}
_BOOLEAN = {"type": "boolean"}
_SAMPLING = {"type": "integer", "minimum": 1, "maximum": 1000}
_EXPRESSION = {"type": "string", "minLength": 1, "maxLength": 2048}
_INPUTS = {
    "type": "array",
    "items": _TARGET,
    "minItems": 0,
    "maxItems": 8,
    "uniqueItems": True,
}
_METRIC = _closed(
    {name: _EXPRESSION for name in ("m11", "m12", "m13", "m22", "m23", "m33")},
    ("m11", "m12", "m13", "m22", "m23", "m33"),
)
_DEFINITIONS = {
    "restrict": _closed({"include_boundary": _BOOLEAN}, ("include_boundary",)),
    "threshold": _closed(
        {
            "input_minimum_mm": _POSITIVE,
            "input_maximum_mm": _POSITIVE,
            "size_minimum_mm": _POSITIVE,
            "size_maximum_mm": _POSITIVE,
            "linear_interpolation": _BOOLEAN,
            "stop_at_input_maximum": _BOOLEAN,
        },
        (
            "input_minimum_mm",
            "input_maximum_mm",
            "size_minimum_mm",
            "size_maximum_mm",
            "linear_interpolation",
            "stop_at_input_maximum",
        ),
    ),
    "mean": _closed({"delta_mm": _POSITIVE}, ("delta_mm",)),
    "gradient": _closed(
        {
            "delta_mm": _POSITIVE,
            "component": {"type": "string", "enum": ["x", "y", "z", "mean"]},
        },
        ("delta_mm", "component"),
    ),
    "curvature": _closed({"delta_mm": _POSITIVE}, ("delta_mm",)),
    "laplacian": _closed({"delta_mm": _POSITIVE}, ("delta_mm",)),
    "attractor_aniso_curve": _closed(
        {
            "distance_minimum_mm": _POSITIVE,
            "distance_maximum_mm": _POSITIVE,
            "size_minimum_normal_mm": _POSITIVE,
            "size_maximum_normal_mm": _POSITIVE,
            "size_minimum_tangent_mm": _POSITIVE,
            "size_maximum_tangent_mm": _POSITIVE,
            "sampling": _SAMPLING,
        },
        (
            "distance_minimum_mm",
            "distance_maximum_mm",
            "size_minimum_normal_mm",
            "size_maximum_normal_mm",
            "size_minimum_tangent_mm",
            "size_maximum_tangent_mm",
            "sampling",
        ),
    ),
    "math_eval": _closed({"equation": _EXPRESSION}, ("equation",)),
    "math_eval_aniso": _closed({"metric": _METRIC}, ("metric",)),
    "distance": _closed({"sampling": _SAMPLING}, ("sampling",)),
    "result": _closed(
        {"field": {"type": "string", "minLength": 1, "maxLength": 160}},
        ("field",),
    ),
}
_REFERENCE_SCHEMAS = {
    "restrict": _references(("Solid", "Face", "Edge", "Vertex")),
    "attractor_aniso_curve": _references(("Edge",)),
    "distance": _references(("Face", "Edge", "Vertex")),
}
_CREATE_ACTIONS = {
    "restrict": "FEM_MeshManipulate",
    "threshold": "VibeCAD_AnalyzeCreateMeshThreshold",
    "mean": "VibeCAD_AnalyzeCreateMeshMean",
    "gradient": "VibeCAD_AnalyzeCreateMeshGradient",
    "curvature": "VibeCAD_AnalyzeCreateMeshCurvature",
    "laplacian": "VibeCAD_AnalyzeCreateMeshLaplacian",
    "attractor_aniso_curve": "FEM_MeshAdvanced",
    "math_eval": "VibeCAD_AnalyzeCreateMeshMathEval",
    "math_eval_aniso": "VibeCAD_AnalyzeCreateMeshMathEvalAniso",
    "distance": "VibeCAD_AnalyzeCreateMeshFieldDistance",
    "result": "VibeCAD_AnalyzeCreateMeshResult",
}


def _dependency_field(kind: str) -> tuple[str, dict] | None:
    if kind in MANIPULATION_KINDS:
        return "input_refinement", _TARGET
    if kind in {"math_eval", "math_eval_aniso"}:
        return "input_refinements", _INPUTS
    return None


def _create(kind: str) -> dict:
    fields = {
        "mesh": _MESH_TARGET,
        "label": _LABEL,
        "definition": _DEFINITIONS[kind],
    }
    dependency = _dependency_field(kind)
    if dependency is not None:
        fields[dependency[0]] = dependency[1]
    if kind in _REFERENCE_SCHEMAS:
        fields["references"] = _REFERENCE_SCHEMAS[kind]
    if kind == "result":
        fields["result"] = RESULT_TARGET
    return _closed(fields, tuple(fields))


def _update(kind: str) -> dict:
    fields = {
        "target": _TARGET,
        "label": _LABEL,
        "definition": _DEFINITIONS[kind],
    }
    dependency = _dependency_field(kind)
    if dependency is not None:
        fields[dependency[0]] = dependency[1]
    if kind in _REFERENCE_SCHEMAS:
        fields["references"] = _REFERENCE_SCHEMAS[kind]
    if kind == "result":
        fields["result"] = RESULT_TARGET
    # Provider arguments already carry the operation discriminator. Requiring
    # three properties therefore means target plus at least one actual edit.
    return _closed(fields, ("target",), minimum=3)


def _title(kind: str) -> str:
    return " ".join(part.title() for part in kind.split("_"))


def _update_action(kind: str) -> str:
    if kind == "distance":
        return "VibeCAD_AnalyzeUpdateMeshFieldDistance"
    if kind == "result":
        return "VibeCAD_AnalyzeUpdateMeshResult"
    return "VibeCAD_AnalyzeUpdateMesh" + "".join(
        part.title() for part in kind.split("_")
    )


def _variant(
    operation: str,
    description: str,
    action_id: str,
    parameters: dict,
) -> NativeCapabilityVariant:
    return NativeCapabilityVariant(
        operation=operation,
        description=description,
        action_ids=frozenset({action_id}),
        surface_ids=frozenset({"analyze"}),
        exact_target_type="ExactAcyclicGmshRefinementFieldGraphAndGeometry",
        transaction_behavior="document",
        background_required=False,
        parameters=parameters,
    )


def analyze_mesh_field_capability_definition() -> NativeCapabilityDefinition:
    variants = []
    for kind in (*MANIPULATION_KINDS, *ADVANCED_KINDS):
        title = _title(kind)
        variants.append(
            _variant(
                f"create_{kind}",
                f"Create a {title} Gmsh field.",
                _CREATE_ACTIONS[kind],
                _create(kind),
            )
        )
        variants.append(
            _variant(
                f"update_{kind}",
                f"Edit a {title} Gmsh field.",
                _update_action(kind),
                _update(kind),
            )
        )
    return NativeCapabilityDefinition(
        name=ANALYZE_MESH_FIELD_CAPABILITY_NAME,
        description="Create or edit Gmsh size fields.",
        primary_classification="mutation",
        variants=tuple(variants),
    )


def register_analyze_mesh_field_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(analyze_mesh_field_capability_definition())
