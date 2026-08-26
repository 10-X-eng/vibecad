# SPDX-License-Identifier: LGPL-2.1-or-later
"""Shared tessellation helpers for Fusion and JT importers."""

from __future__ import annotations

import math
import struct
from typing import Iterable, List, Sequence, Tuple

Vec3 = Tuple[float, float, float]
Triangle = Tuple[Vec3, Vec3, Vec3]


def _finite(v: Vec3) -> bool:
    return all(math.isfinite(c) and abs(c) < 1.0e12 for c in v)


def pack_fusion_mesh(triangles: Sequence[Triangle]) -> bytes:
    """Encode triangles as Fusion OGS display mesh: pos+normal float32 vertices."""
    out = bytearray()
    for a, b, c in triangles:
        nx, ny, nz = _normal(a, b, c)
        for v in (a, b, c):
            out.extend(struct.pack("<ffffff", v[0], v[1], v[2], nx, ny, nz))
    return bytes(out)


def parse_fusion_mesh(data: bytes) -> List[Triangle]:
    """Decode a Fusion OGS `Fusion_mesh_*` blob into triangles."""
    if len(data) < 36:
        return []
    # Prefer pos+normal stride (24 bytes/vertex); fall back to position-only.
    for stride in (24, 12):
        tris = _triangles_from_stride(data, stride)
        if tris:
            return tris
    return []


def _triangles_from_stride(data: bytes, stride: int) -> List[Triangle]:
    nvert = len(data) // stride
    if nvert < 3:
        return []
    nvert -= nvert % 3
    verts: List[Vec3] = []
    for i in range(nvert):
        x, y, z = struct.unpack_from("<fff", data, i * stride)
        verts.append((x, y, z))
    if not verts or not all(_finite(v) for v in verts):
        return []
    tris: List[Triangle] = []
    for i in range(0, len(verts), 3):
        a, b, c = verts[i], verts[i + 1], verts[i + 2]
        if a == b or b == c or a == c:
            continue
        tris.append((a, b, c))
    return tris


def extract_float_triangles(data: bytes) -> List[Triangle]:
    """Pull a triangle soup of IEEE-754 little-endian float triples from a blob."""
    n = len(data) // 12
    if n < 3:
        return []
    coords: List[Vec3] = []
    for i in range(n):
        v = struct.unpack_from("<fff", data, i * 12)
        if not _finite(v):
            if len(coords) >= 3:
                break
            coords.clear()
            continue
        coords.append(v)
    nvert = len(coords) - (len(coords) % 3)
    tris: List[Triangle] = []
    for i in range(0, nvert, 3):
        a, b, c = coords[i], coords[i + 1], coords[i + 2]
        if a == b or b == c or a == c:
            continue
        tris.append((a, b, c))
    return tris


def cube_triangles(size: float = 10.0) -> List[Triangle]:
    """Closed cube from the origin, two triangles per face."""
    s = float(size)
    faces = [
        ((0.0, 0.0, 0.0), (s, 0.0, 0.0), (s, s, 0.0)),
        ((0.0, 0.0, 0.0), (s, s, 0.0), (0.0, s, 0.0)),
        ((0.0, 0.0, s), (s, s, s), (s, 0.0, s)),
        ((0.0, 0.0, s), (0.0, s, s), (s, s, s)),
        ((0.0, 0.0, 0.0), (s, 0.0, s), (s, 0.0, 0.0)),
        ((0.0, 0.0, 0.0), (0.0, 0.0, s), (s, 0.0, s)),
        ((0.0, s, 0.0), (s, s, 0.0), (s, s, s)),
        ((0.0, s, 0.0), (s, s, s), (0.0, s, s)),
        ((0.0, 0.0, 0.0), (0.0, s, 0.0), (0.0, s, s)),
        ((0.0, 0.0, 0.0), (0.0, s, s), (0.0, 0.0, s)),
        ((s, 0.0, 0.0), (s, 0.0, s), (s, s, s)),
        ((s, 0.0, 0.0), (s, s, s), (s, s, 0.0)),
    ]
    return faces


def _normal(a: Vec3, b: Vec3, c: Vec3) -> Vec3:
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx = uy * vz - uz * vy
    ny = uz * vx - ux * vz
    nz = ux * vy - uy * vx
    mag = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
    return (nx / mag, ny / mag, nz / mag)


def mesh_facets(triangles: Iterable[Triangle]) -> List[Tuple[float, ...]]:
    """9-tuple facet list accepted by `Mesh.Mesh`."""
    return [tuple(a) + tuple(b) + tuple(c) for a, b, c in triangles]


def add_triangles_to_document(doc, triangles: Sequence[Triangle], label: str):
    """Place mesh and, when possible, a Part Shape/Solid into *doc*.

    Returns the Part object when a shape is built, otherwise the Mesh object.
    """
    import Mesh
    import Part

    if not triangles:
        raise ValueError("no triangles to import")
    facets = mesh_facets(triangles)
    mesh = Mesh.Mesh(facets)
    mesh_obj = doc.addObject("Mesh::Feature", _unique(doc, label + "Mesh"))
    mesh_obj.Mesh = mesh

    shape = Part.Shape()
    try:
        shape.makeShapeFromMesh(mesh.Topology, 1.0e-4)
    except Exception:
        shape = _compound_faces(triangles)

    if shape is None or shape.isNull() or not shape.Faces:
        shape = _compound_faces(triangles)

    if shape is None or shape.isNull() or not shape.Faces:
        return mesh_obj

    try:
        if shape.isClosed():
            solid = Part.Solid(shape)
            if solid and not solid.isNull() and solid.Volume > 0:
                shape = solid
    except Exception:
        pass

    part_obj = doc.addObject("Part::Feature", _unique(doc, label))
    part_obj.Shape = shape
    return part_obj


def _compound_faces(triangles: Sequence[Triangle]):
    import Part

    faces = []
    for a, b, c in triangles:
        try:
            wire = Part.makePolygon(
                [Part.Vector(*a), Part.Vector(*b), Part.Vector(*c), Part.Vector(*a)]
            )
            faces.append(Part.Face(wire))
        except Exception:
            continue
    if not faces:
        return None
    return Part.Compound(faces)


def _unique(doc, name: str) -> str:
    existing = {obj.Name for obj in doc.Objects}
    if name not in existing:
        return name
    i = 1
    while f"{name}{i:03d}" in existing:
        i += 1
    return f"{name}{i:03d}"
