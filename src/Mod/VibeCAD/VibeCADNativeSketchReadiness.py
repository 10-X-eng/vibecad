# SPDX-License-Identifier: LGPL-2.1-or-later

"""Read-only readiness report for one exact reusable Sketch."""

from __future__ import annotations

from typing import Any

from VibeCADNativeTargets import NativeObjectRef, object_reference, resolve_object


def sketch_readiness(document: Any, target: NativeObjectRef) -> dict[str, Any]:
    import PartDesign

    sketch = resolve_object(
        document,
        target,
        expected_types=("Sketcher::SketchObject",),
    )
    geometry_count = int(getattr(sketch, "GeometryCount", 0) or 0)
    constraints = list(getattr(sketch, "Constraints", []) or [])
    construction_count = sum(
        bool(sketch.getConstruction(index)) for index in range(geometry_count)
    )
    shape = getattr(sketch, "Shape", None)
    shape_valid = bool(
        shape is not None and not shape.isNull() and shape.isValid()
    )
    wires = list(getattr(shape, "Wires", []) or []) if shape is not None else []
    closed_wires = sum(bool(wire.isClosed()) for wire in wires)
    open_wires = len(wires) - closed_wires
    design_valid = True
    design_error = ""
    try:
        PartDesign.validateDesign(sketch)
    except Exception as exc:
        design_valid = False
        design_error = " ".join(str(exc).split())[:240]
    native_valid = bool(sketch.isValid())
    result: dict[str, Any] = {
        "sketch": object_reference(sketch),
        "geometry_count": geometry_count,
        "construction_geometry_count": construction_count,
        "constraint_count": len(constraints),
        "fully_constrained": bool(getattr(sketch, "FullyConstrained", False)),
        "map_mode": str(getattr(sketch, "MapMode", "") or ""),
        "attachment": [
            {
                "object": object_reference(obj),
                "subelements": [str(name) for name in list(subelements or [])],
            }
            for obj, subelements in list(getattr(sketch, "AttachmentSupport", []) or [])
        ],
        "profile": {
            "wire_count": len(wires),
            "closed_wire_count": closed_wires,
            "open_wire_count": open_wires,
            "edge_count": len(list(getattr(shape, "Edges", []) or [])) if shape else 0,
        },
        "valid": native_valid and design_valid,
        "surface_feature_ready": bool(
            native_valid and design_valid and shape_valid and geometry_count > 0
        ),
        "solid_feature_ready": bool(
            native_valid
            and design_valid
            and shape_valid
            and closed_wires > 0
            and open_wires == 0
        ),
    }
    if design_error:
        result["design_error"] = design_error
    status = " ".join(str(getattr(sketch, "getStatusString", lambda: "")()).split())
    if status:
        result["status"] = status[:240]
    return result
