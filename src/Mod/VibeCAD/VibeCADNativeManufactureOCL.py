# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared exact geometry and cutter preflight for OpenCamLib operations."""

from __future__ import annotations

from dataclasses import dataclass
import math
from types import SimpleNamespace
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureOperationSupport import (
    PreparedOperationBoundary,
    exact_fields,
)


_OCL_TOOL_METHODS = {
    "endmill": "cylindrical",
    "ballend": "ball",
    "bullnose": "bullnose",
    "taperedballnose": "ball",
    "drill": "conical",
    "engraver": "conical",
    "v_bit": "conical",
    "v-bit": "conical",
    "vbit": "conical",
}
_SETTING_TOLERANCE = 1.0e-7


@dataclass(frozen=True, slots=True)
class OCLGeometryRequest:
    shared_request: Mapping[str, Any]
    requested_kind: str
    avoid_last_face_count: int
    avoid_internal_features: bool


@dataclass(frozen=True, slots=True)
class OCLToolFacts:
    shape_type: str
    ocl_cutter: str
    diameter_mm: float


@dataclass(frozen=True, slots=True)
class OCLGeometryFacts:
    face_count: int
    cutting_face_count: int
    avoided_face_count: int
    derived_source_top_mm: float
    derived_operation_floor_mm: float
    stock_bottom_mm: float
    spans_xy_mm: tuple[tuple[float, float], ...]


def _error(message: str, code: str = "NATIVE_ARGUMENTS_INVALID") -> None:
    raise NativeManufactureError(message, error_code=code)


def _boolean(value: Any, noun: str) -> bool:
    if not isinstance(value, bool):
        _error(f"{noun} must be true or false.")
    return value


def normalize_ocl_geometry(raw: Any, *, noun: str) -> OCLGeometryRequest:
    """Normalize a complete-Job or ordered exact-Face request."""

    if not isinstance(raw, Mapping):
        _error(f"{noun} geometry must be one closed geometry request.")
    kind = str(raw.get("kind") or "")
    if kind == "entire_job":
        exact_fields(raw, frozenset({"kind"}), f"{noun} entire Job geometry")
        return OCLGeometryRequest(
            shared_request={"kind": "entire_job"},
            requested_kind=kind,
            avoid_last_face_count=0,
            avoid_internal_features=True,
        )
    if kind != "faces":
        _error(f"{noun} geometry kind must be entire_job or faces.")
    value = exact_fields(
        raw,
        frozenset(
            {
                "kind",
                "items",
                "avoid_last_face_count",
                "avoid_internal_features",
            }
        ),
        f"{noun} face geometry",
    )
    items = value["items"]
    if not isinstance(items, list) or not 1 <= len(items) <= 32:
        _error(f"{noun} face geometry requires 1 through 32 exact model items.")
    shared_items = []
    total = 0
    for item in items:
        exact = exact_fields(item, frozenset({"model", "faces"}), f"{noun} face item")
        faces = exact["faces"]
        if not isinstance(faces, list) or not faces:
            _error(f"Each {noun} face item requires at least one exact Face name.")
        names = [str(face) for face in faces]
        if len(names) != len(set(names)):
            _error(f"{noun} Face names must be unique within each model item.")
        total += len(names)
        if total > 64:
            _error(f"A {noun} request accepts at most 64 total Faces.")
        shared_items.append({"model": exact["model"], "subelements": names})
    avoid_count = value["avoid_last_face_count"]
    if isinstance(avoid_count, bool) or not isinstance(avoid_count, int):
        _error(f"{noun} avoid_last_face_count must be an integer.")
    if avoid_count < 0 or avoid_count >= total:
        _error(
            f"{noun} avoid_last_face_count must be nonnegative and leave at "
            "least one earlier Face to cut."
        )
    return OCLGeometryRequest(
        shared_request={"kind": "subelements", "items": shared_items},
        requested_kind=kind,
        avoid_last_face_count=avoid_count,
        avoid_internal_features=_boolean(
            value["avoid_internal_features"],
            f"{noun} avoid_internal_features",
        ),
    )


def _tool_quantity(
    tool: Any,
    name: str,
    noun: str,
    *,
    operation_noun: str,
    allow_zero: bool = False,
) -> float:
    value = getattr(getattr(tool, name, None), "Value", getattr(tool, name, None))
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = -1.0
    if not math.isfinite(result) or result < 0.0 or (result == 0.0 and not allow_zero):
        _error(
            f"{operation_noun} requires its exact cutter to provide a positive {noun}.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    return round(result, 9)


def validate_ocl_tool(
    boundary: PreparedOperationBoundary,
    *,
    noun: str,
) -> OCLToolFacts:
    """Validate and translate the exact controller's ToolBit for OCL."""

    tool = getattr(boundary.controller, "Tool", None)
    shape_type = str(getattr(tool, "ShapeType", "") or "").lower()
    cutter = _OCL_TOOL_METHODS.get(shape_type)
    if cutter is None:
        _error(
            f"{noun} supports OpenCamLib endmill, ballend, bullnose, "
            "taperedballnose, drill, engraver, or v-bit cutters; the exact "
            f"controller uses {shape_type or 'an unidentified cutter'}.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    diameter = _tool_quantity(
        tool,
        "Diameter",
        "diameter",
        operation_noun=noun,
    )
    cutting_height = _tool_quantity(
        tool,
        "CuttingEdgeHeight",
        "cutting-edge height",
        operation_noun=noun,
        allow_zero=cutter == "ball",
    )
    if cutter == "bullnose":
        if hasattr(tool, "CornerRadius"):
            corner = _tool_quantity(
                tool,
                "CornerRadius",
                "corner radius",
                operation_noun=noun,
                allow_zero=True,
            )
            if corner > diameter / 2.0:
                _error(
                    f"{noun} bullnose corner radius cannot exceed half its diameter.",
                    "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
                )
        elif hasattr(tool, "FlatRadius"):
            flat = _tool_quantity(
                tool,
                "FlatRadius",
                "flat radius",
                operation_noun=noun,
                allow_zero=True,
            )
            if flat > diameter / 2.0:
                _error(
                    f"{noun} bullnose flat radius cannot exceed half its diameter.",
                    "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
                )
        else:
            _error(
                f"{noun} requires a bullnose cutter with CornerRadius or FlatRadius.",
                "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )
    if cutter == "conical":
        angle = _tool_quantity(
            tool,
            "CuttingEdgeAngle",
            "cutting-edge angle",
            operation_noun=noun,
        )
        if angle >= 180.0:
            _error(
                f"{noun} conical cutter angle must be below 180 degrees.",
                "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )
    if cutting_height + float(
        getattr(getattr(tool, "LengthOffset", None), "Value", 0.0) or 0.0
    ) <= 0.0:
        _error(
            f"{noun} cutter length must be positive.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    try:
        try:
            import ocl
        except ImportError:
            import opencamlib as ocl
        import Path.Op.SurfaceSupport as PathSurfaceSupport

        translated = PathSurfaceSupport.OCL_Tool(
            ocl,
            SimpleNamespace(ToolController=boundary.controller),
        ).getOclTool()
    except Exception as exc:
        raise NativeManufactureError(
            f"The exact {noun} cutter could not be translated for OpenCamLib.",
            error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            repair={"native_error_type": type(exc).__name__, "native_error": str(exc)[:320]},
        ) from exc
    if not translated:
        _error(
            f"The exact {noun} cutter is not usable by OpenCamLib.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    return OCLToolFacts(shape_type, cutter, diameter)


def _valid_shape(shape: Any) -> bool:
    return bool(
        shape is not None
        and not bool(getattr(shape, "isNull", lambda: True)())
        and bool(shape.isValid())
    )


def inspect_ocl_geometry(
    boundary: PreparedOperationBoundary,
    request: OCLGeometryRequest,
    *,
    bounds: str,
    noun: str,
) -> OCLGeometryFacts:
    """Inspect exact model/Face topology without retaining borrowed shapes."""

    if bounds not in {"model", "stock"}:
        raise ValueError("bounds must be model or stock")
    stock_shape = getattr(getattr(boundary.job, "Stock", None), "Shape", None)
    if not _valid_shape(stock_shape):
        _error(
            f"{noun} requires a valid exact Job stock shape.",
            "NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
        )
    stock_bounds = stock_shape.BoundBox
    source_top = float(stock_bounds.ZMax)
    face_count = 0
    spans = []
    if request.requested_kind == "faces":
        floor = math.inf
        for item in boundary.geometry:
            shape = item.job_resource.Shape
            if not _valid_shape(shape):
                _error(
                    f"{noun} Job resource {item.job_resource.Name!r} has invalid geometry.",
                    "NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
                )
            source_top = max(source_top, float(shape.BoundBox.ZMax))
            for name in item.subelements:
                face = shape.getElement(name)
                if not _valid_shape(face) or float(face.Area) <= _SETTING_TOLERANCE:
                    _error(
                        f"{noun} geometry {item.public_source.Name}.{name} is not a valid nonzero Face.",
                        "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
                    )
                face_bounds = face.BoundBox
                x_span = float(face_bounds.XLength)
                y_span = float(face_bounds.YLength)
                if math.hypot(x_span, y_span) <= _SETTING_TOLERANCE:
                    _error(
                        f"{noun} geometry {item.public_source.Name}.{name} has no usable XY projection.",
                        "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
                    )
                face_count += 1
                floor = min(floor, float(face_bounds.ZMin))
                source_top = max(source_top, float(face_bounds.ZMax))
                spans.append((x_span, y_span))
        operation_floor = floor
    else:
        operation_floor = (
            float(stock_bounds.ZMin)
            if bounds == "stock"
            else min(float(item.job_resource.Shape.BoundBox.ZMin) for item in boundary.geometry)
        )
        for item in boundary.geometry:
            shape = item.job_resource.Shape
            if not _valid_shape(shape):
                _error(
                    f"{noun} Job resource {item.job_resource.Name!r} has invalid geometry.",
                    "NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
                )
            shape_bounds = stock_bounds if bounds == "stock" else shape.BoundBox
            spans.append((float(shape_bounds.XLength), float(shape_bounds.YLength)))
    avoided = request.avoid_last_face_count
    return OCLGeometryFacts(
        face_count=face_count,
        cutting_face_count=face_count - avoided,
        avoided_face_count=avoided,
        derived_source_top_mm=round(source_top, 9),
        derived_operation_floor_mm=round(operation_floor, 9),
        stock_bottom_mm=round(float(stock_bounds.ZMin), 9),
        spans_xy_mm=tuple(spans),
    )
