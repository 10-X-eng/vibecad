# SPDX-License-Identifier: LGPL-2.1-or-later

"""Geometry fixtures for the real Native Mesh modification lifecycle gate."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import FreeCAD as App
import Mesh


def tetrahedron(offset: float = 0.0, *, inconsistent: bool = False) -> Any:
    a = App.Vector(offset + 0.0, 0.0, 0.0)
    b = App.Vector(offset + 8.0, 0.0, 0.0)
    c = App.Vector(offset + 0.0, 7.0, 0.0)
    d = App.Vector(offset + 0.0, 0.0, 6.0)
    base = (a, b, c) if inconsistent else (a, c, b)
    return Mesh.Mesh([base, (a, b, d), (b, c, d), (c, a, d)])


def open_tetrahedron(offset: float = 0.0) -> Any:
    a = App.Vector(offset + 0.0, 0.0, 0.0)
    b = App.Vector(offset + 8.0, 0.0, 0.0)
    c = App.Vector(offset + 0.0, 7.0, 0.0)
    d = App.Vector(offset + 0.0, 0.0, 6.0)
    return Mesh.Mesh([(a, c, b), (a, b, d), (c, a, d)])


def two_components() -> Any:
    result = tetrahedron()
    result.addFacet(
        App.Vector(30.0, 0.0, 0.0),
        App.Vector(34.0, 0.0, 0.0),
        App.Vector(30.0, 3.0, 0.0),
    )
    return result


def smoothing_patch() -> Any:
    lower_left = App.Vector(0.0, 0.0, 0.0)
    lower_right = App.Vector(10.0, 0.0, 0.0)
    upper_right = App.Vector(10.0, 10.0, 0.0)
    upper_left = App.Vector(0.0, 10.0, 0.0)
    center = App.Vector(5.0, 5.0, 4.0)
    return Mesh.Mesh(
        [
            (lower_left, lower_right, center),
            (lower_right, upper_right, center),
            (upper_right, upper_left, center),
            (upper_left, lower_left, center),
        ]
    )


def point_index(mesh: Any, coordinate: tuple[float, float, float]) -> int:
    points, _facets = mesh.Topology
    for index, point in enumerate(points):
        if all(
            abs(actual - expected) < 1.0e-7
            for actual, expected in zip((point.x, point.y, point.z), coordinate)
        ):
            return index
    raise AssertionError(f"Mesh point {coordinate!r} is absent")


def add_source(document: Any, name: str, mesh: Any) -> Any:
    document.openTransaction(f"Create {name}")
    try:
        source = document.addObject("Mesh::Feature", name)
        source.Label = name.replace("_", " ")
        source.Mesh = mesh
        assert document.recompute([source], True, True) is not False
        assert source.Mesh.CountFacets > 0 and source.isValid(), source.getStatusString()
        document.publishProvisionalTimelineOperationBlock(source, (), ())
        document.commitTransaction()
        return source
    except Exception:
        document.abortTransaction()
        raise


def add_sources(document: Any, definitions: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    return {name: add_source(document, name, mesh) for name, mesh in definitions}


def write_fake_gmsh(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
import pathlib
import struct
import sys

output = pathlib.Path(sys.argv[sys.argv.index('-o') + 1])
triangles = (
    (0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 4.0, 0.0, 0.0, 4.0, 3.0, 0.0),
    (0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 4.0, 3.0, 0.0, 0.0, 3.0, 1.0),
)
with output.open('wb') as stream:
    stream.write(b'VibeCAD fake Gmsh result'.ljust(80, b' '))
    stream.write(struct.pack('<I', len(triangles)))
    for triangle in triangles:
        stream.write(struct.pack('<12fH', *triangle, 0))
""",
        encoding="utf-8",
    )
    path.chmod(0o700)
