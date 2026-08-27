# SPDX-License-Identifier: LGPL-2.1-or-later
"""Validated reader for Fusion's OGS current-display graphics cache.

Fusion stores the scene description in ``world`` and indexed vertex buffers in
``Fusion_mesh_*``.  A buffer descriptor is accepted only when the descriptors
tile the complete blob without a gap or overlap.  That invariant prevents a
binary payload from being guessed into plausible-looking geometry.

The descriptor layout is documented by the MIT-licensed ``ezf3d`` project's
public clean-room format research.  This implementation is dependency-free and
targets the Python runtime shipped with VibeCAD.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Iterator, List, Sequence, Tuple

from cad_geometry import Triangle, Vec3, scale_triangles

_MAGIC = "ARenderList"
_MAX_NAME = 256
_DESCRIPTOR_OFFSET = 45
_HAS_BUFFER = 0x800
_TRIANGLES = 7
_POLYLINE = 6
_STRIDE = {_TRIANGLES: 32, _POLYLINE: 24}
_WELD_TOLERANCE_CM = 1.0e-4


class OgsError(ValueError):
    """The graphics cache is missing, malformed, or not safely understood."""


@dataclass(frozen=True)
class _Node:
    name: str
    payload: int
    end: int


@dataclass(frozen=True)
class _Buffer:
    owner: str
    kind: int
    offset: int
    vertices: int
    indices: int

    @property
    def stride(self) -> int:
        return _STRIDE[self.kind]

    @property
    def size(self) -> int:
        return self.vertices * self.stride + self.indices * 4


def parse_ogs_cache(
    world_data: bytes, mesh_data: bytes, scale: float = 1.0
) -> List[List[Triangle]]:
    """Return one triangle group per connected placed body in an OGS cache."""
    _check_magic(world_data)
    buffers, unread = _read_buffers(world_data, len(mesh_data))
    if not buffers:
        raise OgsError("OGS world contains no understood geometry buffers")
    gap, overlap = _coverage(buffers, len(mesh_data))
    if gap or overlap:
        raise OgsError(
            "OGS buffer coverage is incomplete or ambiguous "
            f"(gap={gap} bytes, overlap={overlap} bytes, unread={unread})"
        )

    triangles: List[Triangle] = []
    seen = set()
    for buffer in buffers:
        if buffer.kind != _TRIANGLES:
            continue
        for triangle in _read_face_triangles(mesh_data, buffer):
            key = _triangle_key(triangle)
            if key in seen:
                continue
            seen.add(key)
            triangles.append(triangle)
    if not triangles:
        raise OgsError("OGS cache contains no indexed face triangles")

    groups = _connected_components(triangles)
    return [scale_triangles(group, scale) for group in groups]


def pack_ogs_cache(
    triangles: Sequence[Triangle], repeated_references: int = 0
) -> Tuple[bytes, bytes]:
    """Create a minimal standards-shaped cache for deterministic tests/fixtures."""
    if not triangles:
        raise ValueError("at least one triangle is required")
    mesh = bytearray()
    vertices = []
    for triangle in triangles:
        normal = _normal(*triangle)
        for vertex in triangle:
            vertices.append(vertex)
            mesh.extend(struct.pack("<ffffffff", *vertex, *normal, 0.0, 0.0))
    indices = list(range(len(vertices)))
    mesh.extend(struct.pack("<" + "I" * len(indices), *indices))

    lower = tuple(min(vertex[i] for vertex in vertices) for i in range(3))
    upper = tuple(max(vertex[i] for vertex in vertices) for i in range(3))
    descriptor = bytearray(45)
    descriptor.extend(struct.pack("<III", _HAS_BUFFER, _TRIANGLES, 0))
    descriptor.extend(
        struct.pack(
            "<IIIII",
            0,
            len(vertices) * 3,
            len(vertices) * 3,
            len(vertices) * 2,
            len(indices),
        )
    )
    descriptor.extend(struct.pack("<I", 0))
    descriptor.extend(struct.pack("<dddddd", *lower, *upper))

    world = bytearray(_pack_wstr(_MAGIC))
    world.extend(_pack_node("Face", bytes(descriptor)))
    for _ in range(max(0, int(repeated_references))):
        world.extend(_pack_node("Face", bytes(49)))
    return bytes(world), bytes(mesh)


def _read_buffers(world: bytes, blob_size: int) -> Tuple[List[_Buffer], int]:
    buffers: List[_Buffer] = []
    unread = 0
    for node in _walk(world):
        head = node.payload + _DESCRIPTOR_OFFSET
        if head + 4 > node.end:
            continue
        if struct.unpack_from("<I", world, head)[0] != _HAS_BUFFER:
            continue
        descriptor = _read_descriptor(world, node, blob_size)
        if descriptor is None:
            unread += 1
        else:
            buffers.append(descriptor)
    return buffers, unread


def _read_descriptor(data: bytes, node: _Node, blob_size: int) -> _Buffer | None:
    head = node.payload + _DESCRIPTOR_OFFSET
    if head + 24 > node.end:
        return None
    flags, kind, blob_index = struct.unpack_from("<III", data, head)
    if flags != _HAS_BUFFER or kind not in _STRIDE or blob_index != 0:
        return None
    offset, positions, normals = struct.unpack_from("<III", data, head + 12)
    if positions != normals or positions % 3:
        return None
    vertices = positions // 3
    indices = 0
    tail = head + 24
    if kind == _TRIANGLES:
        if tail + 12 > node.end:
            return None
        textures, indices, bounded_edges = struct.unpack_from("<III", data, tail)
        if textures != 2 * vertices or indices % 3:
            return None
        tail += 12 + 4 * bounded_edges
        if tail > node.end:
            return None
    result = _Buffer(node.name, kind, offset, vertices, indices)
    if offset > blob_size or result.size > blob_size - offset:
        return None
    return result


def _read_face_triangles(data: bytes, buffer: _Buffer) -> Iterator[Triangle]:
    vertices: List[Vec3] = []
    for index in range(buffer.vertices):
        record = struct.unpack_from(
            "<ffffffff", data, buffer.offset + index * buffer.stride
        )
        vertex = (record[0], record[1], record[2])
        normal = (record[3], record[4], record[5])
        if not _finite(vertex) or not _finite(normal):
            raise OgsError("OGS vertex contains a non-finite coordinate or normal")
        magnitude = math.sqrt(sum(component * component for component in normal))
        if not 0.5 <= magnitude <= 1.5:
            raise OgsError("OGS vertex normal is not plausible")
        vertices.append(vertex)

    index_start = buffer.offset + buffer.vertices * buffer.stride
    indices = struct.unpack_from("<" + "I" * buffer.indices, data, index_start)
    for start in range(0, len(indices), 3):
        a, b, c = indices[start : start + 3]
        if max(a, b, c) >= len(vertices):
            raise OgsError("OGS face index exceeds its vertex buffer")
        triangle = (vertices[a], vertices[b], vertices[c])
        if len(set(triangle)) == 3:
            yield triangle


def _connected_components(triangles: Sequence[Triangle]) -> List[List[Triangle]]:
    """Group triangle shells by welded shared edges, preserving file order."""
    parent = list(range(len(triangles)))
    rank = [0] * len(triangles)
    edge_owner = {}

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left = find(left)
        right = find(right)
        if left == right:
            return
        if rank[left] < rank[right]:
            left, right = right, left
        parent[right] = left
        if rank[left] == rank[right]:
            rank[left] += 1

    for triangle_index, triangle in enumerate(triangles):
        points = [_point_key(vertex) for vertex in triangle]
        for first, second in ((0, 1), (1, 2), (2, 0)):
            edge = tuple(sorted((points[first], points[second])))
            previous = edge_owner.setdefault(edge, triangle_index)
            union(triangle_index, previous)

    grouped = {}
    for index, triangle in enumerate(triangles):
        grouped.setdefault(find(index), []).append((index, triangle))
    ordered = sorted(grouped.values(), key=lambda group: group[0][0])
    return [[triangle for _index, triangle in group] for group in ordered]


def _coverage(buffers: Sequence[_Buffer], blob_size: int) -> Tuple[int, int]:
    spans = sorted((buffer.offset, buffer.offset + buffer.size) for buffer in buffers)
    gap = overlap = reach = 0
    for start, stop in spans:
        if start > reach:
            gap += start - reach
        elif start < reach:
            overlap += min(reach - start, stop - start)
        reach = max(reach, stop)
    gap += max(0, blob_size - reach)
    return gap, overlap


def _walk(data: bytes) -> Iterator[_Node]:
    found = []
    position = 0
    while position < len(data):
        if data[position] == 1:
            probe = _read_wstr(data, position + 1)
            if probe is not None and _valid_name(probe[0]):
                found.append((probe[0], probe[1]))
                position = probe[1]
                continue
        position += 1
    for index, (name, payload) in enumerate(found):
        next_header = found[index + 1][1] if index + 1 < len(found) else len(data)
        if index + 1 < len(found):
            next_name, next_payload = found[index + 1]
            next_header = next_payload - (5 + len(next_name) * 2)
        yield _Node(name, payload, next_header)


def _read_wstr(data: bytes, position: int) -> Tuple[str, int] | None:
    if position + 4 > len(data):
        return None
    count = int.from_bytes(data[position : position + 4], "little")
    if not 1 <= count <= _MAX_NAME:
        return None
    end = position + 4 + count * 2
    if end > len(data):
        return None
    raw = data[position + 4 : end]
    if any(raw[i + 1] or not 0x20 <= raw[i] < 0x7F for i in range(0, len(raw), 2)):
        return None
    return raw.decode("utf-16-le"), end


def _check_magic(data: bytes) -> None:
    head = _read_wstr(data, 0)
    if head is None or head[0] != _MAGIC:
        raise OgsError(f"not an OGS world: expected {_MAGIC!r}")


def _valid_name(name: str) -> bool:
    return name[:1].isalpha() and all(
        character.isalnum() or character in "_<>" for character in name
    )


def _point_key(vertex: Vec3) -> Tuple[int, int, int]:
    return tuple(round(value / _WELD_TOLERANCE_CM) for value in vertex)  # type: ignore[return-value]


def _triangle_key(triangle: Triangle):
    return tuple(sorted(_point_key(vertex) for vertex in triangle))


def _finite(vector: Vec3) -> bool:
    return all(
        math.isfinite(component) and abs(component) < 1.0e12 for component in vector
    )


def _normal(a: Vec3, b: Vec3, c: Vec3) -> Vec3:
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx = uy * vz - uz * vy
    ny = uz * vx - ux * vz
    nz = ux * vy - uy * vx
    magnitude = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
    return nx / magnitude, ny / magnitude, nz / magnitude


def _pack_wstr(value: str) -> bytes:
    return struct.pack("<I", len(value)) + value.encode("utf-16-le")


def _pack_node(name: str, payload: bytes) -> bytes:
    return b"\x01" + _pack_wstr(name) + payload
