# SPDX-License-Identifier: LGPL-2.1-or-later
"""Surface/volume field result contracts and CAD-face aggregation helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Mapping, Sequence


@dataclass(frozen=True)
class SurfaceSample:
    triangle_index: int
    source_face_index: int
    area_m2: float
    pressure_pa: float | None = None
    cp: float | None = None
    wall_shear_pa: tuple[float, float, float] | None = None


@dataclass(frozen=True)
class SurfaceField:
    samples: tuple[SurfaceSample, ...]
    geometry_sha256: str
    mapping_version: str = "triangle_to_cad_face/1"
    metadata: Mapping[str, object] = field(default_factory=dict)

    def validate(self) -> None:
        seen: set[int] = set()
        for sample in self.samples:
            if sample.triangle_index in seen:
                raise ValueError(f"duplicate triangle sample: {sample.triangle_index}")
            seen.add(sample.triangle_index)
            if sample.source_face_index < 0 or sample.area_m2 <= 0.0:
                raise ValueError("invalid source face mapping/area")

    def aggregate(self, field_name: str) -> dict[int, float]:
        """Area-weight one scalar field back to original CAD face indices."""
        if field_name not in {"pressure_pa", "cp"}:
            raise ValueError("field_name must be pressure_pa or cp")
        self.validate()
        weighted: dict[int, float] = {}
        areas: dict[int, float] = {}
        for sample in self.samples:
            value = getattr(sample, field_name)
            if value is None or not math.isfinite(float(value)):
                continue
            face = sample.source_face_index
            weighted[face] = weighted.get(face, 0.0) + float(value) * sample.area_m2
            areas[face] = areas.get(face, 0.0) + sample.area_m2
        return {face: weighted[face] / areas[face] for face in weighted if areas[face] > 0.0}


def linear_diverging_color(value: float, low: float, high: float) -> tuple[float, float, float, float]:
    """Small dependency-free blue/white/red scalar map for FreeCAD face display."""
    if not math.isfinite(value):
        return (0.5, 0.5, 0.5, 1.0)
    if high <= low:
        return (1.0, 1.0, 1.0, 1.0)
    t = min(1.0, max(0.0, (value - low) / (high - low)))
    if t <= 0.5:
        r = g = 2.0 * t
        b = 1.0
    else:
        r = 1.0
        g = b = 2.0 * (1.0 - t)
    return (r, g, b, 1.0)


def apply_face_scalar_colors(obj, face_values: Mapping[int, float]) -> None:
    """Apply one color per CAD face through FreeCAD's DiffuseColor property.

    Face indices in ``face_values`` are zero-based contract indices; FreeCAD's
    ``Shape.Faces`` list is also addressed here by zero-based Python position.
    Missing faces retain their current/default shape color.
    """
    if obj is None or not hasattr(obj, "Shape") or not hasattr(obj, "ViewObject"):
        raise TypeError("FreeCAD shape object with ViewObject required")
    face_count = len(obj.Shape.Faces)
    if not face_values:
        return
    values = [float(v) for v in face_values.values() if math.isfinite(float(v))]
    if not values:
        return
    low, high = min(values), max(values)
    default = getattr(obj.ViewObject, "ShapeColor", (0.8, 0.8, 0.8))
    if len(default) == 3:
        base = (float(default[0]), float(default[1]), float(default[2]), 1.0)
    else:
        base = tuple(default)
    colors = [base] * face_count
    for index, value in face_values.items():
        if index < 0 or index >= face_count:
            raise IndexError(f"source face index {index} outside 0..{face_count-1}")
        colors[index] = linear_diverging_color(float(value), low, high)
    obj.ViewObject.DiffuseColor = colors
