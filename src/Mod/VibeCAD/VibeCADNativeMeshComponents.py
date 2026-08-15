# SPDX-License-Identifier: LGPL-2.1-or-later

"""Deterministic connected-component identities for exact Mesh state."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import struct
from typing import Any, Mapping

from VibeCADNativeMeshErrors import NativeMeshError


COMPONENT_ID_PREFIX = "component-v1:"


@dataclass(frozen=True, slots=True)
class NativeMeshComponent:
    component_id: str
    facet_indices: tuple[int, ...]
    point_indices: tuple[int, ...]
    minimum_mm: tuple[float, float, float]
    maximum_mm: tuple[float, float, float]

    def summary(self) -> dict[str, Any]:
        return {
            "component_id": self.component_id,
            "facet_count": len(self.facet_indices),
            "point_count": len(self.point_indices),
            "bounds": {
                "minimum_mm": list(self.minimum_mm),
                "maximum_mm": list(self.maximum_mm),
            },
        }


def _topology(mesh: Any) -> tuple[tuple[Any, ...], tuple[tuple[int, int, int], ...]]:
    try:
        raw_points, raw_facets = mesh.Topology
        points = tuple(raw_points)
        facets = tuple(tuple(int(value) for value in facet) for facet in raw_facets)
    except Exception as exc:
        raise NativeMeshError(
            "The exact Mesh topology could not be read for component analysis."
        ) from exc
    if len(facets) < 1 or len(points) < 3:
        raise NativeMeshError("Component analysis requires one nonempty Mesh.")
    for facet in facets:
        if len(facet) != 3 or len(set(facet)) != 3:
            raise NativeMeshError("The exact Mesh contains an invalid triangular facet.")
        if min(facet) < 0 or max(facet) >= len(points):
            raise NativeMeshError("The exact Mesh contains an invalid point reference.")
    return points, facets  # type: ignore[return-value]


def _component_id(
    points: tuple[Any, ...],
    facets: tuple[tuple[int, int, int], ...],
    facet_indices: tuple[int, ...],
) -> str:
    digest = hashlib.sha256()
    digest.update(struct.pack("!Q", len(facet_indices)))
    for facet_index in facet_indices:
        facet = facets[facet_index]
        digest.update(struct.pack("!QQQQ", facet_index, *facet))
        for point_index in facet:
            point = points[point_index]
            digest.update(
                struct.pack(
                    "!Qddd",
                    point_index,
                    float(point.x),
                    float(point.y),
                    float(point.z),
                )
            )
    return COMPONENT_ID_PREFIX + digest.hexdigest()


def mesh_components(mesh: Any) -> tuple[NativeMeshComponent, ...]:
    """Return components connected through shared edges, ordered by source facet."""

    points, facets = _topology(mesh)
    parents = list(range(len(facets)))
    ranks = [0] * len(facets)

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(first: int, second: int) -> None:
        first_root = find(first)
        second_root = find(second)
        if first_root == second_root:
            return
        if ranks[first_root] < ranks[second_root]:
            first_root, second_root = second_root, first_root
        parents[second_root] = first_root
        if ranks[first_root] == ranks[second_root]:
            ranks[first_root] += 1

    first_facet_by_edge: dict[tuple[int, int], int] = {}
    for facet_index, facet in enumerate(facets):
        for first, second in (
            (facet[0], facet[1]),
            (facet[1], facet[2]),
            (facet[2], facet[0]),
        ):
            edge = (first, second) if first < second else (second, first)
            prior = first_facet_by_edge.setdefault(edge, facet_index)
            if prior != facet_index:
                union(prior, facet_index)

    facets_by_root: dict[int, list[int]] = {}
    for facet_index in range(len(facets)):
        facets_by_root.setdefault(find(facet_index), []).append(facet_index)

    components: list[NativeMeshComponent] = []
    for facet_group in sorted(facets_by_root.values(), key=lambda value: value[0]):
        facet_indices = tuple(facet_group)
        point_indices = tuple(
            sorted({point for facet_index in facet_indices for point in facets[facet_index]})
        )
        coordinates = [
            (
                float(points[index].x),
                float(points[index].y),
                float(points[index].z),
            )
            for index in point_indices
        ]
        minimum = tuple(min(value[axis] for value in coordinates) for axis in range(3))
        maximum = tuple(max(value[axis] for value in coordinates) for axis in range(3))
        components.append(
            NativeMeshComponent(
                _component_id(points, facets, facet_indices),
                facet_indices,
                point_indices,
                minimum,  # type: ignore[arg-type]
                maximum,  # type: ignore[arg-type]
            )
        )
    return tuple(components)


def resolve_component_facets(mesh: Any, selection: Mapping[str, Any]) -> tuple[int, ...]:
    if not isinstance(selection, Mapping):
        raise NativeMeshError("selection must identify components by size or exact component IDs.")
    components = mesh_components(mesh)
    kind = str(selection.get("kind") or "")
    if kind == "maximum_facets" and set(selection) == {"kind", "maximum_facets"}:
        maximum = selection.get("maximum_facets")
        if type(maximum) is not int or maximum < 1:
            raise NativeMeshError("maximum_facets must be a positive integer.")
        selected = [component for component in components if len(component.facet_indices) <= maximum]
    elif kind == "component_ids" and set(selection) == {"kind", "component_ids"}:
        requested = selection.get("component_ids")
        if not isinstance(requested, list) or not requested:
            raise NativeMeshError("component_ids must contain exact IDs from Mesh inspection.")
        exact = tuple(str(value) for value in requested)
        if len(exact) != len(set(exact)):
            raise NativeMeshError("component_ids must not repeat an ID.")
        by_id = {component.component_id: component for component in components}
        missing = [value for value in exact if value not in by_id]
        if missing:
            raise NativeMeshError(
                "A component ID is stale for the exact Mesh state.",
                error_code="NATIVE_MESH_COMPONENT_STALE",
                repair={
                    "available_component_ids": [component.component_id for component in components[:256]],
                    "component_count": len(components),
                    "truncated": len(components) > 256,
                },
            )
        selected = [by_id[value] for value in exact]
    else:
        raise NativeMeshError("selection must use maximum_facets or component_ids exactly.")
    facets = tuple(sorted({index for component in selected for index in component.facet_indices}))
    if not facets:
        raise NativeMeshError("The component selection matches no facets on the exact Mesh.")
    return facets
