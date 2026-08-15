# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact shared ISO 286 fit mutation used by TechDraw UI and Native mode."""

from __future__ import annotations

import math


ISO_286_TOLERANCE_CLASSES = (
    "c11",
    "f7",
    "h6",
    "h7",
    "h9",
    "k6",
    "n6",
    "r6",
    "s6",
    "D10",
    "E9",
    "F8",
    "G7",
    "H7",
    "H8",
    "H11",
    "K7",
    "N7",
    "R7",
    "S7",
)

_PROPERTY_NAMES = (
    "FormatSpec",
    "EqualTolerance",
    "OverTolerance",
    "UnderTolerance",
    "FormatSpecOverTolerance",
    "FormatSpecUnderTolerance",
)


def _tolerance_format(value: float) -> str:
    if value < 0.0:
        return "(%-0.6w)"
    if value > 0.0:
        return "(+%-0.6w)"
    return "( %-0.6w)"


def plan_iso_286_fit(dimension, tolerance_class: str) -> dict:
    if getattr(dimension, "TypeId", "") != "TechDraw::DrawViewDimension":
        raise TypeError("ISO 286 fit requires one TechDraw dimension")
    if tolerance_class not in ISO_286_TOLERANCE_CLASSES:
        raise ValueError("Unsupported ISO 286 tolerance class")
    value = float(dimension.getRawValue())
    if not math.isfinite(value) or not 0.0 < value <= 500.0:
        raise ValueError("ISO 286 fit supports nominal dimensions above 0 through 500 mm")

    # ISO286 remains the established TechDraw table implementation. Importing
    # lazily avoids a module cycle while giving UI and Native one calculation.
    from .TaskHoleShaftFit import ISO286

    calculator = ISO286()
    calculator.calculate(value, tolerance_class[0], int(tolerance_class[1:]))
    over, under = (float(item) for item in calculator.getValues())
    if not all(math.isfinite(item) for item in (over, under)):
        raise RuntimeError("ISO 286 produced a non-finite tolerance")
    previous_format = str(dimension.FormatSpec or "")
    base_format = previous_format.rstrip()
    for known_class in ISO_286_TOLERANCE_CLASSES:
        suffix = f" {known_class}"
        if base_format.endswith(suffix):
            base_format = base_format[: -len(suffix)].rstrip()
            break
    return {
        "object_name": str(dimension.Name),
        "nominal_value_mm": value,
        "tolerance_class": tolerance_class,
        "previous_format_spec": previous_format,
        "format_spec": f"{base_format} {tolerance_class}".strip(),
        "equal_tolerance": False,
        "over_tolerance_mm": over,
        "under_tolerance_mm": under,
        "over_tolerance_format": _tolerance_format(over),
        "under_tolerance_format": _tolerance_format(under),
    }


def apply_iso_286_fit(dimension, tolerance_class: str) -> dict:
    plan = plan_iso_286_fit(dimension, tolerance_class)
    original = {name: getattr(dimension, name) for name in _PROPERTY_NAMES}
    try:
        dimension.FormatSpec = plan["format_spec"]
        dimension.EqualTolerance = plan["equal_tolerance"]
        dimension.OverTolerance = plan["over_tolerance_mm"]
        dimension.UnderTolerance = plan["under_tolerance_mm"]
        dimension.FormatSpecOverTolerance = plan["over_tolerance_format"]
        dimension.FormatSpecUnderTolerance = plan["under_tolerance_format"]
    except Exception:
        for name, value in original.items():
            setattr(dimension, name, value)
        raise
    retained = {
        "format_spec": str(dimension.FormatSpec or ""),
        "equal_tolerance": bool(dimension.EqualTolerance),
        "over_tolerance_mm": float(dimension.OverTolerance),
        "under_tolerance_mm": float(dimension.UnderTolerance),
        "over_tolerance_format": str(dimension.FormatSpecOverTolerance or ""),
        "under_tolerance_format": str(dimension.FormatSpecUnderTolerance or ""),
    }
    expected = {name: plan[name] for name in retained}
    if retained != expected:
        raise RuntimeError("The dimension did not retain its exact ISO 286 fit")
    return plan
