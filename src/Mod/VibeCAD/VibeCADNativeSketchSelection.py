# SPDX-License-Identifier: LGPL-2.1-or-later

"""Translate the human's live Sketcher selection into Native tool targets."""

from __future__ import annotations

import re
from typing import Any, Mapping


_INDEXED_SUFFIX = re.compile(
    r"^(?:e)?(?P<kind>Edge|ExternalEdge|Vertex|Constraint)(?P<number>[1-9][0-9]*)$"
)
_POSITION_NAMES = {1: "start", 2: "end", 3: "center"}


def _count(sketch: Any, property_name: str, collection_name: str) -> int:
    try:
        return int(getattr(sketch, property_name))
    except (TypeError, ValueError, AttributeError, RuntimeError):
        return len(list(getattr(sketch, collection_name, []) or []))


def _selection_belongs_to_sketch(
    item: Mapping[str, Any],
    sketch_name: str,
) -> bool:
    if not sketch_name:
        return False
    reference = item.get("object")
    if isinstance(reference, Mapping) and reference.get("object_name") == sketch_name:
        return True
    return any(
        sketch_name in str(value).split(".")[:-1]
        for value in list(item.get("subelements") or [])
    )


def _subelement_suffix(value: Any) -> str:
    suffix = str(value or "").rsplit(".", 1)[-1]
    if suffix.startswith("vVertex"):
        return suffix[1:]
    return suffix


def _element_target(sketch: Any, suffix: str) -> dict[str, Any] | None:
    if suffix == "H_Axis":
        return {"geometry_index": -1, "position": "whole"}
    if suffix == "V_Axis":
        return {"geometry_index": -2, "position": "whole"}
    if suffix == "RootPoint":
        return {"geometry_index": -1, "position": "start"}
    match = _INDEXED_SUFFIX.fullmatch(suffix)
    if match is None:
        return None
    number = int(match.group("number"))
    kind = match.group("kind")
    if kind == "Edge":
        if number > _count(sketch, "GeometryCount", "Geometry"):
            return None
        return {"geometry_index": number - 1, "position": "whole"}
    if kind == "ExternalEdge":
        external_count = max(
            0,
            len(list(getattr(sketch, "ExternalGeo", []) or [])) - 2,
        )
        if number > external_count:
            return None
        return {"geometry_index": -number - 2, "position": "whole"}
    if kind != "Vertex":
        return None
    get_geo_vertex_index = getattr(sketch, "getGeoVertexIndex", None)
    if not callable(get_geo_vertex_index):
        return None
    try:
        geometry_index, position_code = get_geo_vertex_index(number - 1)
        position = _POSITION_NAMES.get(int(position_code))
        geometry_index = int(geometry_index)
    except (TypeError, ValueError, IndexError, AttributeError, RuntimeError):
        return None
    if position is None or geometry_index == -2000:
        return None
    return {"geometry_index": geometry_index, "position": position}


def semantic_sketch_selection(
    sketch: Any,
    selection: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Return exact Native targets for selected geometry, points, and constraints."""

    if not isinstance(selection, Mapping):
        return None
    sketch_name = str(getattr(sketch, "Name", "") or "")
    elements: list[dict[str, Any]] = []
    constraints: list[dict[str, int]] = []
    element_keys: set[tuple[int, str]] = set()
    constraint_indices: set[int] = set()
    truncated = False
    for item in list(selection.get("items") or []):
        if not isinstance(item, Mapping) or not _selection_belongs_to_sketch(
            item, sketch_name
        ):
            continue
        truncated = truncated or item.get("subelements_truncated") is True
        for raw_name in list(item.get("subelements") or []):
            suffix = _subelement_suffix(raw_name)
            match = _INDEXED_SUFFIX.fullmatch(suffix)
            if match is not None and match.group("kind") == "Constraint":
                index = int(match.group("number")) - 1
                if index >= _count(sketch, "ConstraintCount", "Constraints"):
                    continue
                if index not in constraint_indices:
                    constraint_indices.add(index)
                    constraints.append({"constraint_index": index})
                continue
            target = _element_target(sketch, suffix)
            if target is None:
                continue
            key = (int(target["geometry_index"]), str(target["position"]))
            if key not in element_keys:
                element_keys.add(key)
                elements.append(target)
    if not elements and not constraints:
        return None
    result: dict[str, Any] = {
        "meaning": "Exact turn-start targets for 'this', 'these', or 'selected'."
    }
    if elements:
        result["elements"] = elements
    if constraints:
        result["constraints"] = constraints
    if truncated:
        result["truncated"] = True
    return result
