# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact creation and verification of parametric Mesh regular solids."""

from __future__ import annotations

import math
from typing import Any, Mapping

from VibeCADNativeDesignResults import placement_from_mapping
from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


_SOLID_TYPES = {
    "box": ("Mesh::Cube", "Cube"),
    "cylinder": ("Mesh::Cylinder", "Cylinder"),
    "cone": ("Mesh::Cone", "Cone"),
    "sphere": ("Mesh::Sphere", "Sphere"),
    "ellipsoid": ("Mesh::Ellipsoid", "Ellipsoid"),
    "torus": ("Mesh::Torus", "Torus"),
}


def _number(value: Any, field: str, *, allow_zero: bool = False) -> float:
    if type(value) not in {int, float}:
        raise NativeMeshError(f"{field} must be one finite length in millimetres.")
    result = float(value)
    if not math.isfinite(result) or result < 0.0 or (not allow_zero and result == 0.0):
        qualifier = "non-negative" if allow_zero else "positive"
        raise NativeMeshError(f"{field} must be one finite {qualifier} length.")
    return result


def _sampling(value: Any) -> int:
    if type(value) is not int or not 3 <= value <= 1000:
        raise NativeMeshError("sampling must be an integer from 3 through 1000.")
    return value


def _set_common(obj: Any, label: str, placement: Mapping[str, Any]) -> None:
    obj.Label = label
    try:
        obj.Placement = placement_from_mapping(placement)
    except Exception as exc:
        raise NativeMeshError(
            "placement must contain a finite origin and a non-zero axis rotation."
        ) from exc


def create_regular_solid(
    document: Any,
    *,
    label: str,
    placement: Mapping[str, Any],
    solid: Mapping[str, Any],
) -> NativeMutationDraft:
    if not isinstance(solid, Mapping):
        raise NativeMeshError("solid must be one exact regular-solid definition.")
    kind = str(solid.get("kind") or "")
    definition = _SOLID_TYPES.get(kind)
    if definition is None:
        raise NativeMeshError("solid.kind must be box, cylinder, cone, sphere, ellipsoid, or torus.")
    type_id, base_name = definition
    obj = document.addObject(type_id, document.getUniqueObjectName(base_name))
    if obj is None:
        raise NativeMeshError("The parametric Mesh solid could not be created.")
    _set_common(obj, label, placement)

    if kind == "box":
        obj.Length = _number(solid["length_mm"], "length_mm")
        obj.Width = _number(solid["width_mm"], "width_mm")
        obj.Height = _number(solid["height_mm"], "height_mm")
    elif kind == "cylinder":
        obj.Radius = _number(solid["radius_mm"], "radius_mm")
        obj.Length = _number(solid["length_mm"], "length_mm")
        obj.EdgeLength = _number(
            solid["edge_length_mm"], "edge_length_mm", allow_zero=True
        )
        obj.Sampling = _sampling(solid["sampling"])
        obj.Closed = bool(solid["closed"])
    elif kind == "cone":
        radius1 = _number(solid["radius1_mm"], "radius1_mm", allow_zero=True)
        radius2 = _number(solid["radius2_mm"], "radius2_mm", allow_zero=True)
        if radius1 == 0.0 and radius2 == 0.0:
            raise NativeMeshError("A cone requires at least one positive radius.")
        obj.Radius1 = radius1
        obj.Radius2 = radius2
        obj.Length = _number(solid["length_mm"], "length_mm")
        obj.EdgeLength = _number(
            solid["edge_length_mm"], "edge_length_mm", allow_zero=True
        )
        obj.Sampling = _sampling(solid["sampling"])
        obj.Closed = bool(solid["closed"])
    elif kind == "sphere":
        obj.Radius = _number(solid["radius_mm"], "radius_mm")
        obj.Sampling = _sampling(solid["sampling"])
    elif kind == "ellipsoid":
        obj.Radius1 = _number(solid["radius1_mm"], "radius1_mm")
        obj.Radius2 = _number(solid["radius2_mm"], "radius2_mm")
        obj.Sampling = _sampling(solid["sampling"])
    else:
        major = _number(solid["major_radius_mm"], "major_radius_mm")
        minor = _number(solid["minor_radius_mm"], "minor_radius_mm")
        if minor >= major:
            raise NativeMeshError("minor_radius_mm must be smaller than major_radius_mm.")
        obj.Radius1 = major
        obj.Radius2 = minor
        obj.Sampling = _sampling(solid["sampling"])
    return NativeMutationDraft(
        value={"object": obj, "kind": kind},
        recompute_targets=(obj,),
        created=(object_identity(obj),),
    )


def verify_regular_solid(_document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    value = draft.value
    obj = value.get("object") if isinstance(value, Mapping) else None
    kind = str(value.get("kind") or "") if isinstance(value, Mapping) else ""
    if obj is None or str(getattr(obj, "TypeId", "")) != _SOLID_TYPES[kind][0]:
        raise NativeMeshError("The regular-solid result identity changed before commit.")
    facets = int(getattr(getattr(obj, "Mesh", None), "CountFacets", 0) or 0)
    if facets <= 0:
        raise NativeMeshError(
            "The regular-solid settings produced no usable facets.",
            repair={"operation": "regular_solid", "solid_kind": kind},
        )
    return {
        "created": mesh_object_state(obj),
        "solid_kind": kind,
    }
