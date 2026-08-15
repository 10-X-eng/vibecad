# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact bounded state for FEM mesh refinement resources."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeMeshState import fem_mesher_kind
from VibeCADNativeAnalyzeState import is_live
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeSnapshot import concise_object


_MODES = {
    "Fem::MeshRegion": "region",
    "Fem::MeshGroup": "group",
    "Fem::MeshDistance": "distance",
    "Fem::MeshBoundaryLayer": "boundary_layer",
    "Fem::MeshShape": "shape",
    "Fem::MeshTransfiniteCurve": "transfinite_curve",
    "Fem::MeshTransfiniteSurface": "transfinite_surface",
    "Fem::MeshTransfiniteVolume": "transfinite_volume",
    "Fem::MeshManipulate": "manipulate",
    "Fem::MeshAdvanced": "advanced",
}


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _finite(value: Any) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise NativeAnalyzeError("A FEM mesh refinement contains a non-finite value.")
    return float(format(number, ".15g"))


def _mm(value: Any) -> float:
    return _finite(value.getValueAs("mm").Value)


def _vector(value: Any) -> dict[str, float]:
    return {axis: _finite(getattr(value, axis)) for axis in ("x", "y", "z")}


def mesh_refinement_mode(obj: Any) -> str:
    proxy_type = str(getattr(getattr(obj, "Proxy", None), "Type", "") or "")
    mode = _MODES.get(proxy_type)
    if mode is None:
        raise NativeAnalyzeError(
            "The exact target is not a supported FEM mesh refinement.",
            error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
        )
    return mode


def _owner(obj: Any) -> tuple[Any, str, int]:
    document = obj.Document
    owners = []
    for candidate in tuple(document.Objects):
        try:
            kind = fem_mesher_kind(candidate)
        except NativeAnalyzeError:
            continue
        refinements = tuple(getattr(candidate, "MeshRefinementList", ()) or ())
        groups = tuple(getattr(candidate, "MeshGroupList", ()) or ())
        matches = [
            (kind, index)
            for values in (refinements, groups)
            for index, resource in enumerate(values)
            if resource is obj
        ]
        owners.extend((candidate, owner_kind, index) for owner_kind, index in matches)
    if len(owners) != 1:
        raise NativeAnalyzeError(
            "The FEM mesh refinement must belong to exactly one mesh definition."
        )
    return owners[0]


def _references(obj: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    visible = []
    exact = []
    for raw in tuple(getattr(obj, "References", ()) or ()):
        if not isinstance(raw, tuple) or len(raw) != 2:
            raise NativeAnalyzeError("A FEM mesh refinement has malformed references.")
        source, raw_names = raw
        names = (raw_names,) if isinstance(raw_names, str) else tuple(raw_names or ())
        record = {
            "object_name": str(getattr(source, "Name", "") or ""),
            "subelements": [str(name) for name in names],
        }
        visible.append(record)
        try:
            source_state = mesh_object_state(source)["state_sha256"]
        except Exception:
            source_state = None
        exact.append(
            {
                **record,
                "object_id": int(getattr(source, "ID", -1)),
                "source_state_sha256": source_state,
            }
        )
    return visible, exact


def _definition(obj: Any, mode: str) -> dict[str, Any]:
    if mode == "region":
        return {"element_size_mm": _mm(obj.CharacteristicLength)}
    if mode == "group":
        return {"export_identifier": "label" if bool(obj.UseLabel) else "object_name"}
    if mode == "distance":
        return {
            "distance_minimum_mm": _mm(obj.DistanceMinimum),
            "distance_maximum_mm": _mm(obj.DistanceMaximum),
            "size_minimum_mm": _mm(obj.SizeMinimum),
            "size_maximum_mm": _mm(obj.SizeMaximum),
            "linear_interpolation": bool(obj.LinearInterpolation),
            "sampling": int(obj.Sampling),
        }
    if mode == "boundary_layer":
        return {
            "minimum_thickness_mm": _mm(obj.MinimumThickness),
            "number_of_layers": int(obj.NumberOfLayers),
            "growth_rate": _finite(obj.GrowthRate),
        }
    if mode == "transfinite_curve":
        return {
            "nodes": int(obj.Nodes),
            "coefficient": _finite(obj.Coefficient),
            "distribution": str(obj.Distribution).lower(),
            "inverted": bool(obj.Invert),
        }
    if mode in {"transfinite_surface", "transfinite_volume"}:
        definition = {
            "recombine": bool(obj.Recombine),
            "triangle_orientation": {
                "Left": "left",
                "Right": "right",
                "AlternateRight": "alternate_right",
                "AlternateLeft": "alternate_left",
            }[str(obj.TriangleOrientation)],
            "use_automation": bool(obj.UseAutomation),
            "nodes": int(obj.Nodes),
            "coefficient": _finite(obj.Coefficient),
            "distribution": str(obj.Distribution).lower(),
            "inverted": bool(obj.Invert),
        }
        if mode == "transfinite_volume":
            definition["mixed_elements"] = bool(obj.MixedElements)
        return definition
    if mode == "manipulate":
        kind = str(obj.Type).lower()
        definition = {
            "kind": kind,
            "input_refinement": {
                "object_name": str(obj.Refinement.Name) if obj.Refinement else "",
            },
        }
        if kind == "restrict":
            definition["include_boundary"] = bool(obj.IncludeBoundary)
        elif kind == "threshold":
            definition.update(
                {
                    "input_minimum_mm": _mm(obj.InputMinimum),
                    "input_maximum_mm": _mm(obj.InputMaximum),
                    "size_minimum_mm": _mm(obj.SizeMinimum),
                    "size_maximum_mm": _mm(obj.SizeMaximum),
                    "linear_interpolation": bool(obj.LinearInterpolation),
                    "stop_at_input_maximum": bool(obj.StopAtInputMax),
                }
            )
        elif kind in {"mean", "curvature", "laplacian"}:
            definition["delta_mm"] = _mm(obj.Delta)
        elif kind == "gradient":
            definition["delta_mm"] = _mm(obj.Delta)
            definition["component"] = str(obj.Kind).lower()
        else:
            raise NativeAnalyzeError("A mesh manipulation has an invalid native type.")
        return definition
    if mode == "advanced":
        kind = {
            "AttractorAnisoCurve": "attractor_aniso_curve",
            "MathEval": "math_eval",
            "MathEvalAniso": "math_eval_aniso",
            "Distance": "distance",
            "Result": "result",
        }.get(str(obj.Type))
        if kind is None:
            raise NativeAnalyzeError("An advanced mesh field has an invalid native type.")
        definition = {
            "kind": kind,
            "input_refinements": [
                {"object_name": str(value.Name)}
                for value in tuple(obj.Refinements or ())
            ],
        }
        if kind == "attractor_aniso_curve":
            definition.update(
                {
                    "distance_minimum_mm": _mm(obj.DistanceMin),
                    "distance_maximum_mm": _mm(obj.DistanceMax),
                    "size_minimum_normal_mm": _mm(obj.SizeMinNormal),
                    "size_maximum_normal_mm": _mm(obj.SizeMaxNormal),
                    "size_minimum_tangent_mm": _mm(obj.SizeMinTangent),
                    "size_maximum_tangent_mm": _mm(obj.SizeMaxTangent),
                    "sampling": int(obj.Sampling),
                }
            )
        elif kind == "math_eval":
            definition["equation"] = str(obj.Equation)
        elif kind == "math_eval_aniso":
            definition["metric"] = {
                name.lower(): str(getattr(obj, name))
                for name in ("M11", "M12", "M13", "M22", "M23", "M33")
            }
        elif kind == "distance":
            definition["sampling"] = int(obj.Sampling)
        else:
            definition["result"] = {
                "object_name": str(obj.ResultObject.Name) if obj.ResultObject else "",
                "field": str(obj.ResultField),
            }
        return definition
    shape_type = str(obj.ShapeType).lower()
    shape: dict[str, Any]
    if shape_type == "box":
        shape = {
            "kind": "box",
            "center_mm": _vector(obj.BoxCenter),
            "length_mm": _mm(obj.BoxLength),
            "width_mm": _mm(obj.BoxWidth),
            "height_mm": _mm(obj.BoxHeight),
        }
    elif shape_type == "sphere":
        shape = {
            "kind": "sphere",
            "center_mm": _vector(obj.SphereCenter),
            "radius_mm": _mm(obj.SphereRadius),
        }
    elif shape_type == "cylinder":
        shape = {
            "kind": "cylinder",
            "center_mm": _vector(obj.CylinderCenter),
            "axis": _vector(obj.CylinderAxis),
            "radius_mm": _mm(obj.CylinderRadius),
        }
    else:
        raise NativeAnalyzeError("A FEM mesh-shape refinement has an invalid shape type.")
    return {
        "shape": shape,
        "size_inside_mm": _mm(obj.SizeIn),
        "size_outside_mm": _mm(obj.SizeOut),
        "transition_thickness_mm": _mm(obj.Thickness),
    }


def mesh_refinement_state(obj: Any) -> dict[str, Any]:
    document = getattr(obj, "Document", None)
    if not is_live(document, obj):
        raise NativeAnalyzeError("The FEM mesh refinement is no longer live.")
    mode = mesh_refinement_mode(obj)
    owner, backend, owner_index = _owner(obj)
    references, exact_references = _references(obj)
    definition = _definition(obj, mode)
    exact_definition = definition
    if mode == "advanced" and definition.get("kind") == "result":
        source = getattr(obj, "ResultObject", None)
        if not is_live(document, source):
            raise NativeAnalyzeError(
                "The Result-backed Gmsh field has no live post-processing source."
            )
        from VibeCADNativeAnalyzeResultState import result_reference_state

        source_state = result_reference_state(source)
        exact_definition = {
            **definition,
            "result_source_identity": {
                "object_name": source_state["object_name"],
                "object_id": int(source.ID),
                "state_sha256": source_state["state_sha256"],
            },
        }
    result = {
        **concise_object(obj),
        "refinement_mode": mode,
        "mesh_definition": {
            "object_name": str(owner.Name),
            "mesher": "netgen" if backend == "netgen_legacy" else backend,
        },
        "definition": definition,
        "references": references,
        "suppressed": bool(getattr(obj, "Suppressed", False)),
    }
    result["state_sha256"] = _digest(
        {
            "object_name": str(obj.Name),
            "object_id": int(obj.ID),
            "label": str(obj.Label),
            "mode": mode,
            "mesh_object_name": str(owner.Name),
            "mesh_object_id": int(owner.ID),
            "owner_index": owner_index,
            "definition": exact_definition,
            "references": exact_references,
            "suppressed": result["suppressed"],
        }
    )
    return result


def mesh_refinement_still_exact(obj: Any, expected_sha256: str) -> bool:
    try:
        return mesh_refinement_state(obj)["state_sha256"] == expected_sha256
    except NativeAnalyzeError:
        return False
