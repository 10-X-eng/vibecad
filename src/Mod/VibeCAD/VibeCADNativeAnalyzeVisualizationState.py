# SPDX-License-Identifier: LGPL-2.1-or-later

"""Typed field selectors and compact table state for FEM visualizations."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzePostSampling import post_point_fields


MAX_VISUALIZATION_CELLS = 500_000
MAX_REPAIR_FIELDS = 16

_COMPONENTS = {
    1: {"scalar": "Not a vector"},
    2: {"x": "X", "y": "Y"},
    3: {"x": "X", "y": "Y", "z": "Z"},
    6: {
        "xx": "XX",
        "yy": "YY",
        "zz": "ZZ",
        "xy": "XY",
        "xz": "XZ",
        "yz": "YZ",
    },
}


@dataclass(frozen=True, slots=True)
class PreparedAxisSelector:
    kind: str
    native_field: str
    native_component: str
    components: int
    unit: str

    def response(self) -> dict[str, Any]:
        if self.kind == "point_index":
            return {"kind": "point_index", "unit": "1"}
        if self.kind == "position":
            return {
                "kind": "position",
                "component": self.native_component.lower(),
                "unit": "mm",
            }
        result = {
            "kind": "field",
            "name": self.native_field,
            "component": (
                "scalar"
                if self.native_component == "Not a vector"
                else self.native_component.lower()
            ),
        }
        if self.unit:
            result["unit"] = self.unit
        return result


@dataclass(frozen=True, slots=True)
class PreparedVisualizationExtraction:
    mode: str
    x: PreparedAxisSelector
    y: PreparedAxisSelector | None
    point_index: int | None
    extract_all_frames: bool
    series_name: str
    frame_count: int
    expected_rows: int
    expected_columns: int

    def response(self) -> dict[str, Any]:
        if self.mode == "point_over_frames":
            result = {
                "mode": self.mode,
                "point_index": self.point_index,
                "series_name": self.series_name,
                "frame_count": self.frame_count,
            }
            if self.y is None:
                result["value"] = self.x.response()
            else:
                result["x"] = {"kind": "frames"}
                result["y"] = self.y.response()
            return result
        result = {
            "mode": "field",
            "all_frames": self.extract_all_frames,
            "series_name": self.series_name,
        }
        if self.y is None:
            result["value"] = self.x.response()
        else:
            result["x"] = self.x.response()
            result["y"] = self.y.response()
        return result


def visible_text(value: Any, name: str, maximum: int = 160) -> str:
    text = str(value or "").strip()
    if (
        not text
        or len(text) > maximum
        or any(ord(character) < 0x20 for character in text)
    ):
        raise NativeAnalyzeError(
            f"{name} must contain 1 to {maximum} visible characters."
        )
    return text


def optional_visible_text(value: Any, name: str, maximum: int = 160) -> str:
    text = str(value or "").strip()
    if len(text) > maximum or any(ord(character) < 0x20 for character in text):
        raise NativeAnalyzeError(
            f"{name} must contain no more than {maximum} visible characters."
        )
    return text


def source_time_steps(source: Any) -> tuple[float, ...]:
    try:
        from vtkmodules.vtkCommonExecutionModel import vtkStreamingDemandDrivenPipeline

        information = source.getOutputAlgorithm().GetOutputInformation(0)
        key = vtkStreamingDemandDrivenPipeline.TIME_STEPS()
        values = information.Get(key) if information.Has(key) else ()
    except Exception:
        return ()
    result = []
    for value in tuple(values or ()):
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(number):
            result.append(float(format(number, ".15g")))
    return tuple(result)


def _field_repair(fields: list[dict[str, Any]]) -> dict[str, Any]:
    result = []
    for field in fields[:MAX_REPAIR_FIELDS]:
        components = int(field["components"])
        item = {
            "name": field["name"],
            "components": components,
            "allowed_components": list(_COMPONENTS.get(components, ())),
        }
        if field.get("unit"):
            item["unit"] = field["unit"]
        result.append(item)
    return {
        "available_point_fields": result,
        "available_point_fields_truncated": len(fields) > MAX_REPAIR_FIELDS,
    }


def prepare_axis_selector(
    source: Any,
    value: Any,
    *,
    axis: str,
    allow_point_index: bool,
) -> PreparedAxisSelector:
    if not isinstance(value, Mapping):
        raise NativeAnalyzeError(f"{axis} selector must be one typed object.")
    selector = dict(value)
    kind = str(selector.get("kind") or "")
    if kind == "point_index" and allow_point_index and set(selector) == {"kind"}:
        return PreparedAxisSelector(kind, "Index", "Not a vector", 1, "1")
    if kind == "position" and set(selector) == {"kind", "component"}:
        component = str(selector["component"] or "").strip().lower()
        if component not in {"x", "y", "z"}:
            raise NativeAnalyzeError(
                f"{axis}.component must be x, y, or z for a position selector."
            )
        return PreparedAxisSelector(kind, "Position", component.upper(), 3, "mm")
    if kind != "field" or set(selector) != {"kind", "name", "component"}:
        choices = "point_index, position, or field" if allow_point_index else "position or field"
        raise NativeAnalyzeError(f"{axis} selector must be {choices} with its exact fields.")
    fields = post_point_fields(source)
    name = str(selector["name"] or "").strip()
    matches = [field for field in fields if field["name"] == name]
    if len(matches) != 1:
        raise NativeAnalyzeError(
            f"{axis} field {name!r} must identify exactly one point field.",
            error_code="NATIVE_ANALYZE_FIELD_UNAVAILABLE",
            repair={"axis": axis, **_field_repair(fields)},
        )
    selected = matches[0]
    components = int(selected["components"])
    choices = _COMPONENTS.get(components, {})
    component = str(selector["component"] or "").strip().lower()
    if component not in choices:
        raise NativeAnalyzeError(
            f"{axis}.component {component!r} is invalid for field {name!r}.",
            error_code="NATIVE_ANALYZE_FIELD_COMPONENT_INVALID",
            repair={
                "axis": axis,
                "field": name,
                "field_components": components,
                "allowed_components": list(choices),
            },
        )
    return PreparedAxisSelector(
        kind,
        name,
        choices[component],
        components,
        str(selected.get("unit", "") or ""),
    )


def prepare_extraction(
    source: Any,
    value: Any,
    *,
    dimension: int,
    source_point_count: int,
) -> PreparedVisualizationExtraction:
    if not isinstance(value, Mapping):
        raise NativeAnalyzeError("data must be one typed extraction object.")
    data = dict(value)
    mode = str(data.get("mode") or "")
    frame_count = len(source_time_steps(source))
    if dimension == 1 and mode == "field" and set(data) == {
        "mode",
        "value",
        "all_frames",
        "series_name",
    }:
        x = prepare_axis_selector(
            source, data["value"], axis="data.value", allow_point_index=True
        )
        y = None
        extract_all = data["all_frames"]
    elif dimension == 2 and mode == "field" and set(data) == {
        "mode",
        "x",
        "y",
        "all_frames",
        "series_name",
    }:
        x = prepare_axis_selector(
            source, data["x"], axis="data.x", allow_point_index=True
        )
        y = prepare_axis_selector(
            source, data["y"], axis="data.y", allow_point_index=False
        )
        extract_all = data["all_frames"]
    elif dimension == 1 and mode == "point_over_frames" and set(data) == {
        "mode",
        "point_index",
        "value",
        "series_name",
    }:
        x = prepare_axis_selector(
            source, data["value"], axis="data.value", allow_point_index=True
        )
        y = None
        extract_all = True
    elif dimension == 2 and mode == "point_over_frames" and set(data) == {
        "mode",
        "point_index",
        "y",
        "series_name",
    }:
        x = PreparedAxisSelector("frames", "Frames", "Not a vector", 1, "")
        y = prepare_axis_selector(
            source, data["y"], axis="data.y", allow_point_index=False
        )
        extract_all = True
    else:
        raise NativeAnalyzeError(
            f"data does not match a {dimension}D field or point-over-frames extraction."
        )
    if type(extract_all) is not bool:
        raise NativeAnalyzeError("data.all_frames must be true or false.")
    series_name = visible_text(data["series_name"], "data.series_name")
    if mode == "point_over_frames":
        raw_index = data["point_index"]
        if type(raw_index) is not int or not 0 <= raw_index < source_point_count:
            raise NativeAnalyzeError(
                "data.point_index must be a zero-based point index on the exact source.",
                repair={
                    "minimum_point_index": 0,
                    "maximum_point_index": source_point_count - 1,
                },
            )
        if frame_count < 1:
            raise NativeAnalyzeError(
                "point_over_frames requires a source with at least one time step."
            )
        point_index = raw_index
        rows = frame_count
        columns = dimension
    else:
        if extract_all and frame_count < 1:
            raise NativeAnalyzeError(
                "data.all_frames=true requires a source with at least one time step."
            )
        point_index = None
        rows = source_point_count
        columns = dimension * (frame_count if extract_all else 1)
    cell_count = rows * columns
    if rows < 1 or columns < 1 or cell_count > MAX_VISUALIZATION_CELLS:
        raise NativeAnalyzeError(
            "The requested visualization extraction exceeds its bounded data budget.",
            error_code="NATIVE_ANALYZE_VISUALIZATION_LIMIT_EXCEEDED",
            repair={
                "expected_rows": rows,
                "expected_columns": columns,
                "expected_cells": cell_count,
                "maximum_cells": MAX_VISUALIZATION_CELLS,
            },
        )
    return PreparedVisualizationExtraction(
        mode,
        x,
        y,
        point_index,
        extract_all,
        series_name,
        frame_count,
        rows,
        columns,
    )


def table_summary(table: Any) -> dict[str, Any]:
    try:
        row_count = int(table.GetNumberOfRows())
        column_count = int(table.GetNumberOfColumns())
    except Exception as exc:
        raise NativeAnalyzeError("The visualization has no readable VTK table.") from exc
    if (
        row_count < 1
        or column_count < 1
        or row_count * column_count > MAX_VISUALIZATION_CELLS
    ):
        raise NativeAnalyzeError(
            "The visualization table is empty or exceeds its bounded data budget."
        )
    columns = []
    for index in range(column_count):
        array = table.GetColumn(index)
        if array is None or int(array.GetNumberOfComponents()) != 1:
            raise NativeAnalyzeError(
                "A visualization table column is missing or not scalar."
            )
        values = []
        for row in range(row_count):
            number = float(array.GetTuple1(row))
            if not math.isfinite(number):
                raise NativeAnalyzeError(
                    "The visualization table contains a non-finite value."
                )
            values.append(number)
        columns.append(
            {
                "name": str(array.GetName() or f"column_{index}")[:160],
                "range": [
                    float(format(min(values), ".15g")),
                    float(format(max(values), ".15g")),
                ],
                "first": float(format(values[0], ".15g")),
                "last": float(format(values[-1], ".15g")),
            }
        )
    return {
        "row_count": row_count,
        "column_count": column_count,
        "columns": columns,
    }
