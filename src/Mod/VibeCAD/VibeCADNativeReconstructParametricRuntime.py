# SPDX-License-Identifier: LGPL-2.1-or-later

"""Rebuild editable B-rep from a printables reverse IR. Not mesh.to_shape."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket

from VibeCADNativeMeshReconstructParametricSchema import (
    MESH_RECONSTRUCT_PARAMETRIC_CAPABILITY_NAME,
)

_VARIANTS = {
    "from_printables_ir": frozenset(
        {"ir_path", "result_label", "step_path", "stl_path"}
    ),
}


def load_printables_ir(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise NativeMeshError(
            "printables IR must be a JSON object",
            error_code="NATIVE_RECONSTRUCT_IR_INVALID",
        )
    return data


def printables_ir_plan(ir: Mapping[str, Any]) -> dict[str, Any]:
    """Validate printables reverse IR schema_version 1 and extract rebuild ops."""
    if ir.get("schema_version") != 1:
        raise NativeMeshError(
            "printables IR schema_version must be 1",
            error_code="NATIVE_RECONSTRUCT_IR_INVALID",
        )
    if ir.get("units") != "mm":
        raise NativeMeshError(
            "printables IR units must be mm",
            error_code="NATIVE_RECONSTRUCT_IR_INVALID",
        )
    klass = ir.get("class")
    if klass not in {"parametric", "analytic", "organic", "failed"}:
        raise NativeMeshError(
            "printables IR class is not a known reconstruction class",
            error_code="NATIVE_RECONSTRUCT_IR_INVALID",
        )
    forbidden = ir.get("forbidden") or {}
    if forbidden.get("triangle_wrapped_step") is not True:
        raise NativeMeshError(
            "printables IR must forbid triangle-wrapped STEP",
            error_code="NATIVE_RECONSTRUCT_IR_INVALID",
        )
    sketches = {s["id"]: s for s in ir.get("sketches") or [] if isinstance(s, dict)}
    extrude = None
    holes: list[dict[str, Any]] = []
    for feat in ir.get("features") or []:
        if not isinstance(feat, dict):
            continue
        if feat.get("type") == "extrude" and feat.get("op") == "add" and extrude is None:
            extrude = feat
        if feat.get("type") == "hole":
            holes.append(feat)
    return {
        "class": klass,
        "body": ir.get("body") or "body",
        "expected_shells": int(ir.get("expected_shells") or 1),
        "extrude": extrude,
        "holes": holes,
        "sketches": sketches,
        "input_triangles": int(ir.get("input_triangles") or 0),
    }


def _outer_uv(sketch: Mapping[str, Any]) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for prof in sketch.get("profiles") or []:
        if prof.get("role") == "hole" or str(prof.get("id", "")).startswith("hole"):
            continue
        for ent in prof.get("entities") or []:
            if ent.get("type") == "line":
                a = ent.get("a_mm") or [0.0, 0.0]
                b = ent.get("b_mm") or [0.0, 0.0]
                if not pts:
                    pts.append((float(a[0]), float(a[1])))
                pts.append((float(b[0]), float(b[1])))
            elif ent.get("type") == "polyline":
                for p in ent.get("points_mm") or []:
                    pts.append((float(p[0]), float(p[1])))
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    return pts


def _build_solid(plan: Mapping[str, Any]):
    import FreeCAD as App
    import Part

    extrude = plan.get("extrude")
    sketches = plan.get("sketches") or {}
    if not extrude:
        raise NativeMeshError(
            "IR has no extrude feature to rebuild",
            error_code="NATIVE_RECONSTRUCT_NO_EXTRUDE",
        )
    sketch = sketches.get(extrude.get("sketch"))
    if not isinstance(sketch, dict):
        raise NativeMeshError(
            "IR extrude sketch is missing",
            error_code="NATIVE_RECONSTRUCT_NO_SKETCH",
        )
    outer = _outer_uv(sketch)
    if len(outer) < 3:
        raise NativeMeshError(
            "IR sketch outer profile is empty",
            error_code="NATIVE_RECONSTRUCT_NO_PROFILE",
        )
    origin = App.Vector(*(float(x) for x in sketch["origin_mm"]))
    x_axis = App.Vector(*(float(x) for x in sketch["x_axis"]))
    y_axis = App.Vector(*(float(x) for x in sketch["y_axis"]))
    direction = App.Vector(*(float(x) for x in extrude.get("direction") or sketch["normal"]))
    depth = float(extrude.get("depth_mm") or 0.0)
    pts = [origin + x_axis * u + y_axis * v for u, v in outer]
    pts.append(pts[0])
    face = Part.Face(Part.makePolygon(pts))
    solid = face.extrude(direction * depth)
    for hole in plan.get("holes") or []:
        uv = hole.get("uv_mm")
        if not uv:
            origin_mm = hole.get("origin_mm") or [0.0, 0.0, 0.0]
            rel = App.Vector(*origin_mm) - origin
            uv = (rel.dot(x_axis), rel.dot(y_axis))
        radius = float(hole.get("diameter_mm") or 0.0) / 2.0
        hole_origin = origin + x_axis * float(uv[0]) + y_axis * float(uv[1]) - direction
        cutter = Part.makeCylinder(radius, depth + 2.0, hole_origin, direction)
        solid = solid.cut(cutter)
    solids = getattr(solid, "Solids", None) or [solid]
    expected = int(plan.get("expected_shells") or 1)
    if len(solids) != expected:
        raise NativeMeshError(
            f"HARD: solid count {len(solids)} != expected_shells {expected}",
            error_code="NATIVE_RECONSTRUCT_SHELL_COUNT",
        )
    return solids[0] if solids else solid


def _commit_reconstruction(document: Any, plan: Mapping[str, Any], values: Mapping[str, Any]) -> Mapping[str, Any]:
    import Mesh
    import Part

    if plan["class"] == "failed":
        raise NativeMeshError(
            "IR class is failed; no STEP/STL claim",
            error_code="NATIVE_RECONSTRUCT_FAILED_CLASS",
        )
    shape = _build_solid(plan)
    label = str(values.get("result_label") or plan["body"])
    feat = document.addObject("Part::Feature", label)
    feat.Shape = shape
    document.recompute()
    step_path = values.get("step_path")
    stl_path = values.get("stl_path")
    if stl_path:
        Path(str(stl_path)).parent.mkdir(parents=True, exist_ok=True)
        Mesh.export([feat], str(stl_path))
    if step_path:
        Path(str(step_path)).parent.mkdir(parents=True, exist_ok=True)
        Part.export([feat], str(step_path))
    return {
        "capability": MESH_RECONSTRUCT_PARAMETRIC_CAPABILITY_NAME,
        "class": plan["class"],
        "object_name": feat.Name,
        "solid_count": len(feat.Shape.Solids),
        "used_mesh_to_shape": False,
    }


class NativeReconstructParametricRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    @property
    def capability_name(self) -> str:
        return MESH_RECONSTRUCT_PARAMETRIC_CAPABILITY_NAME

    def execute(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(arguments, _VARIANTS)
        if operation != "from_printables_ir":
            raise NativeMeshError(
                "unsupported reconstruct operation",
                error_code="NATIVE_RECONSTRUCT_OPERATION",
            )
        if not isinstance(ticket, NativeCallTicket):
            raise TypeError("ticket must be a NativeCallTicket")
        ir = load_printables_ir(values["ir_path"])
        plan = printables_ir_plan(ir)

        def mutate(document: Any) -> Mapping[str, Any]:
            return _commit_reconstruction(document, plan, values)

        return run_immediate_mutation(
            self._context,
            ticket=ticket,
            transaction_name="Reconstruct Parametric",
            mutate=mutate,
        )
