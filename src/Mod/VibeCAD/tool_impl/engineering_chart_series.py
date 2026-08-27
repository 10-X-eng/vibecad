# SPDX-License-Identifier: LGPL-2.1-or-later

"""Read-only projections over FreeCAD-owned engineering visualizations."""

from __future__ import annotations

from hashlib import sha256
import math
import struct
from typing import Any

from .analysis_contracts import AnalysisContractError, CanonicalJson
from .engineering_experience import EngineeringChartAxis, EngineeringChartSeries


_KINDS = {"Table": "table", "Histogram": "histogram", "Lineplot": "line_plot"}


def _text(value: Any, field: str, maximum: int = 160) -> str:
    result = str(value or "").strip()
    if not result or len(result) > maximum or any(ord(char) < 0x20 for char in result):
        raise AnalysisContractError(f"{field} must contain 1 through {maximum} visible characters.")
    return result


def _table_summary(table: Any) -> dict[str, Any]:
    # Keep the dependency on the existing bounded reader explicit and lazy so
    # this facade remains importable outside a FreeCAD runtime.
    from VibeCADNativeAnalyzeVisualizationState import table_summary

    try:
        return table_summary(table)
    except Exception as exc:
        raise AnalysisContractError(
            "Visualization owner has no valid bounded table."
        ) from exc


def _table_sha256(table: Any, summary: dict[str, Any]) -> str:
    """Fingerprint the bounded owner table without retaining a value copy."""

    digest = sha256()
    rows = int(summary["row_count"])
    columns = int(summary["column_count"])
    digest.update(struct.pack("!QQ", rows, columns))
    try:
        for column_index in range(columns):
            array = table.GetColumn(column_index)
            name = str(array.GetName() or f"column_{column_index}").encode("utf-8")
            digest.update(struct.pack("!Q", len(name)))
            digest.update(name)
            for row in range(rows):
                number = float(array.GetTuple1(row))
                if not math.isfinite(number):
                    raise ValueError("non-finite chart value")
                digest.update(struct.pack("!d", number))
    except Exception as exc:
        raise AnalysisContractError(
            "Visualization owner table changed or became unreadable during projection."
        ) from exc
    return digest.hexdigest()


def _field_unit(extractor: Any, native_field: str) -> str | None:
    if native_field == "Index":
        return "1"
    if native_field == "Position":
        return "mm"
    if native_field == "Frames":
        return None
    try:
        from VibeCADNativeAnalyzePostSampling import post_point_fields

        fields = post_point_fields(extractor.Source)
    except Exception:
        return None
    matches = [str(item.get("unit") or "") for item in fields if item.get("name") == native_field]
    if len(matches) != 1 or not matches[0]:
        return None
    return _text(matches[0], "chart axis unit", 48)


def _axis(column: dict[str, Any], label: str, unit: str | None) -> EngineeringChartAxis:
    limits = column["range"]
    return EngineeringChartAxis(label, unit, float(limits[0]), float(limits[1]))


def chart_series_from_visualization(visualization: Any) -> EngineeringChartSeries:
    """Describe one live owner visualization without copying its value arrays."""

    proxy = getattr(visualization, "Proxy", None)
    if str(getattr(proxy, "Type", "")) != "Fem::FemPostVisualization":
        raise AnalysisContractError("Object is not a FEM post visualization owner.")
    native_kind = str(getattr(proxy, "VisualizationType", ""))
    kind = _KINDS.get(native_kind)
    if kind is None:
        raise AnalysisContractError("Visualization owner kind is unsupported.")
    extractors = tuple(getattr(visualization, "Group", ()) or ())
    if len(extractors) != 1:
        raise AnalysisContractError("Visualization must have exactly one owning extractor.")
    extractor = extractors[0]
    if str(getattr(getattr(extractor, "Proxy", None), "VisualizationType", "")) != native_kind:
        raise AnalysisContractError("Visualization and extractor owner kinds disagree.")
    table = getattr(visualization, "Table", None)
    summary = _table_summary(table)
    table_digest = _table_sha256(table, summary)
    columns = tuple(summary["columns"])
    expected = 2 if kind == "line_plot" else 1
    if len(columns) < expected or (kind == "line_plot" and len(columns) % 2):
        raise AnalysisContractError("Visualization table shape does not match its owner kind.")

    view = getattr(visualization, "ViewObject", None)
    x_label = str(getattr(view, "XLabel", "") or "").strip()
    y_label = str(getattr(view, "YLabel", "") or "").strip()
    x_field = str(getattr(extractor, "XField", "") or "")
    y_field = str(getattr(extractor, "YField", "") or "")
    if kind == "line_plot":
        x_axes = columns[0::2]
        y_columns = columns[1::2]
        x_min = min(float(item["range"][0]) for item in x_axes)
        x_max = max(float(item["range"][1]) for item in x_axes)
        x_axis = EngineeringChartAxis(
            x_label or _text(x_axes[0]["name"], "x axis label"),
            _field_unit(extractor, x_field), x_min, x_max,
        )
        y_unit = _field_unit(extractor, y_field)
        y_axes = tuple(
            _axis(
                column,
                y_label or _text(column["name"], "series label"),
                y_unit,
            )
            for column in y_columns
        )
    elif kind == "histogram":
        source_column = columns[0]
        x_axis = _axis(
            source_column,
            x_label or _text(source_column["name"], "histogram value label"),
            _field_unit(extractor, x_field),
        )
        y_axes = ()
    else:
        x_axis = None
        y_columns = columns
        y_unit = _field_unit(extractor, x_field)
        y_axes = tuple(
            _axis(
                column,
                _text(column["name"], "table column label"),
                y_unit,
            )
            for column in y_columns
        )
    identity = {
        "series_id": _text(getattr(visualization, "Name", None), "visualization name"),
        "label": _text(getattr(visualization, "Label", None), "visualization label"),
        "kind": kind,
        "row_count": int(summary["row_count"]),
        "columns": columns,
        "table_sha256": table_digest,
        "x_axis": None if x_axis is None else x_axis.to_dict(),
        "y_axes": [item.to_dict() for item in y_axes],
    }
    digest = CanonicalJson.from_value(identity).sha256()
    return EngineeringChartSeries(
        identity["series_id"], identity["label"], kind,
        identity["row_count"], x_axis, y_axes, digest,
    )


def chart_series_from_analysis(analysis: Any) -> tuple[EngineeringChartSeries, ...]:
    """Discover bounded chart owners directly contained by one Analysis."""

    projected = []
    for member in tuple(getattr(analysis, "Group", ()) or ()):
        try:
            projected.append(chart_series_from_visualization(member))
        except AnalysisContractError:
            continue
    if len(projected) > 64:
        raise AnalysisContractError("Analysis chart owners exceed their presentation bound.")
    return tuple(projected)


__all__ = ["chart_series_from_analysis", "chart_series_from_visualization"]
