# SPDX-License-Identifier: LGPL-2.1-or-later

"""Authoritative bounded access to the shipped CAM thread catalogs."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import difflib
import math
from pathlib import Path as FilePath
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError


@dataclass(frozen=True, slots=True)
class ThreadSeries:
    public_name: str
    native_type: str
    filename: str
    side: str
    unit_system: str


@dataclass(frozen=True, slots=True)
class ResolvedThreadDefinition:
    kind: str
    series: str | None
    side: str
    native_type: str
    designation: str
    fit_percent: int
    major_diameter_mm: float
    minor_diameter_mm: float
    pitch_mm: float
    threads_per_inch: int


THREAD_SERIES = {
    item.public_name: item
    for item in (
        ThreadSeries(
            "imperial_external_2a",
            "ImperialExternal2A",
            "imperial-external-2A.csv",
            "external",
            "imperial",
        ),
        ThreadSeries(
            "imperial_external_3a",
            "ImperialExternal3A",
            "imperial-external-3A.csv",
            "external",
            "imperial",
        ),
        ThreadSeries(
            "imperial_internal_2b",
            "ImperialInternal2B",
            "imperial-internal-2B.csv",
            "internal",
            "imperial",
        ),
        ThreadSeries(
            "imperial_internal_3b",
            "ImperialInternal3B",
            "imperial-internal-3B.csv",
            "internal",
            "imperial",
        ),
        ThreadSeries(
            "metric_external_4g6g",
            "MetricExternal4G6G",
            "metric-external-4G6G.csv",
            "external",
            "metric",
        ),
        ThreadSeries(
            "metric_external_6g",
            "MetricExternal6G",
            "metric-external-6G.csv",
            "external",
            "metric",
        ),
        ThreadSeries(
            "metric_internal_6h",
            "MetricInternal6H",
            "metric-internal-6H.csv",
            "internal",
            "metric",
        ),
    )
}


def _error(message: str, *, repair: Mapping[str, Any] | None = None) -> None:
    raise NativeManufactureError(
        message,
        error_code="NATIVE_ARGUMENTS_INVALID",
        repair=dict(repair or {}),
    )


def _exact_fields(
    value: Any,
    fields: frozenset[str],
    noun: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        _error(f"{noun} must contain exactly: {', '.join(sorted(fields))}.")
    return value


def _finite_positive(value: Any, noun: str) -> float:
    if isinstance(value, bool):
        _error(f"{noun} must be one positive finite number.")
    try:
        result = float(value)
    except (TypeError, ValueError):
        _error(f"{noun} must be one positive finite number.")
    if not math.isfinite(result) or not 0.0 < result <= 1_000_000.0:
        _error(f"{noun} must be greater than zero and at most 1000000.")
    return round(result, 9)


def _catalog_path(series: ThreadSeries) -> FilePath:
    import FreeCAD as App

    path = (
        FilePath(App.getHomePath())
        / "Mod"
        / "CAM"
        / "Data"
        / "Threads"
        / series.filename
    )
    if not path.is_file():
        raise NativeManufactureError(
            f"The shipped CAM thread catalog {series.filename!r} is unavailable.",
            error_code="NATIVE_MANUFACTURE_THREAD_CATALOG_UNAVAILABLE",
        )
    return path


def _rows(series: ThreadSeries) -> tuple[dict[str, str], ...]:
    try:
        with _catalog_path(series).open(newline="", encoding="utf-8") as stream:
            return tuple(dict(row) for row in csv.DictReader(stream))
    except NativeManufactureError:
        raise
    except (OSError, csv.Error) as exc:
        raise NativeManufactureError(
            f"The shipped CAM thread catalog {series.filename!r} could not be read.",
            error_code="NATIVE_MANUFACTURE_THREAD_CATALOG_INVALID",
        ) from exc


def _diameter_mm(value: str, series: ThreadSeries) -> float:
    result = float(value)
    if series.unit_system == "imperial":
        result *= 25.4
    return round(result, 9)


def _row_summary(row: Mapping[str, str], series: ThreadSeries) -> dict[str, Any]:
    result = {
        "designation": str(row["name"]),
        "major_diameter_range_mm": {
            "minimum": _diameter_mm(row["dMajorMin"], series),
            "maximum": _diameter_mm(row["dMajorMax"], series),
        },
        "minor_diameter_range_mm": {
            "minimum": _diameter_mm(row["dMinorMin"], series),
            "maximum": _diameter_mm(row["dMinorMax"], series),
        },
    }
    if series.unit_system == "metric":
        result["pitch_mm"] = round(float(row["pitch"]), 9)
    else:
        result["threads_per_inch"] = int(row["tpi"])
    return result


def read_thread_catalog(
    *,
    series: str,
    query: str,
    offset: int,
    page_size: int,
) -> dict[str, Any]:
    """Return one bounded ordered page from an authoritative thread table."""

    definition = THREAD_SERIES.get(str(series or ""))
    if definition is None:
        _error(
            "Thread catalog series is unsupported.",
            repair={"accepted_series": sorted(THREAD_SERIES)},
        )
    clean_query = str(query or "").strip()
    if len(clean_query) > 80:
        _error("Thread catalog query must contain at most 80 characters.")
    start_requested = int(offset)
    size = int(page_size)
    rows = _rows(definition)
    if clean_query:
        folded = clean_query.casefold()
        rows = tuple(row for row in rows if folded in str(row["name"]).casefold())
    start = min(start_requested, len(rows))
    stop = min(start + size, len(rows))
    return {
        "thread_catalog": {
            "series": definition.public_name,
            "side": definition.side,
            "unit_system": definition.unit_system,
            "query": clean_query,
            "offset": start,
            "count": stop - start,
            "total": len(rows),
            "next_offset": stop if stop < len(rows) else None,
            "items": [_row_summary(row, definition) for row in rows[start:stop]],
        }
    }


def _standard_definition(raw: Mapping[str, Any]) -> ResolvedThreadDefinition:
    request = _exact_fields(
        raw,
        frozenset({"kind", "series", "designation", "fit_percent"}),
        "Standard thread definition",
    )
    series = THREAD_SERIES.get(str(request["series"] or ""))
    if series is None:
        _error(
            "Standard thread series is unsupported.",
            repair={"accepted_series": sorted(THREAD_SERIES)},
        )
    designation = str(request["designation"] or "").strip()
    fit = request["fit_percent"]
    if isinstance(fit, bool) or not isinstance(fit, int) or not 0 <= fit <= 100:
        _error("Standard thread fit_percent must be an integer from 0 through 100.")
    rows = _rows(series)
    row = next((item for item in rows if item["name"] == designation), None)
    if row is None:
        names = [str(item["name"]) for item in rows]
        suggestions = difflib.get_close_matches(designation, names, n=5, cutoff=0.35)
        _error(
            f"Thread designation {designation!r} is not in {series.public_name}.",
            repair={
                "series": series.public_name,
                "close_designations": suggestions,
                "use": "manufacture.inspect/read_thread_catalog",
            },
        )
    fraction = fit / 100.0
    major_min = _diameter_mm(row["dMajorMin"], series)
    major_max = _diameter_mm(row["dMajorMax"], series)
    minor_min = _diameter_mm(row["dMinorMin"], series)
    minor_max = _diameter_mm(row["dMinorMax"], series)
    pitch = round(float(row["pitch"]), 9) if series.unit_system == "metric" else 0.0
    tpi = int(row["tpi"]) if series.unit_system == "imperial" else 0
    return ResolvedThreadDefinition(
        kind="standard",
        series=series.public_name,
        side=series.side,
        native_type=series.native_type,
        designation=designation,
        fit_percent=fit,
        major_diameter_mm=round(major_min + (major_max - major_min) * fraction, 9),
        minor_diameter_mm=round(minor_min + (minor_max - minor_min) * fraction, 9),
        pitch_mm=pitch,
        threads_per_inch=tpi,
    )


def _custom_definition(raw: Mapping[str, Any]) -> ResolvedThreadDefinition:
    request = _exact_fields(
        raw,
        frozenset(
            {"kind", "side", "major_diameter_mm", "minor_diameter_mm", "pitch"}
        ),
        "Custom thread definition",
    )
    side = str(request["side"] or "")
    if side not in {"internal", "external"}:
        _error("Custom thread side must be internal or external.")
    pitch_request = request["pitch"]
    if not isinstance(pitch_request, Mapping):
        _error("Custom thread pitch must be one closed pitch request.")
    pitch_kind = str(pitch_request.get("kind") or "")
    if pitch_kind == "pitch_mm":
        pitch_fields = _exact_fields(
            pitch_request,
            frozenset({"kind", "value"}),
            "Custom metric pitch",
        )
        pitch_mm = _finite_positive(pitch_fields["value"], "Custom thread pitch")
        tpi = 0
    elif pitch_kind == "threads_per_inch":
        pitch_fields = _exact_fields(
            pitch_request,
            frozenset({"kind", "value"}),
            "Custom imperial pitch",
        )
        value = pitch_fields["value"]
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 99:
            _error("Custom threads_per_inch must be an integer from 1 through 99.")
        pitch_mm = 0.0
        tpi = value
    else:
        _error("Custom thread pitch kind must be pitch_mm or threads_per_inch.")
    return ResolvedThreadDefinition(
        kind="custom",
        series=None,
        side=side,
        native_type="CustomInternal" if side == "internal" else "CustomExternal",
        designation="",
        fit_percent=50,
        major_diameter_mm=_finite_positive(
            request["major_diameter_mm"], "Custom thread major diameter"
        ),
        minor_diameter_mm=_finite_positive(
            request["minor_diameter_mm"], "Custom thread minor diameter"
        ),
        pitch_mm=pitch_mm,
        threads_per_inch=tpi,
    )


def resolve_thread_definition(raw: Any) -> ResolvedThreadDefinition:
    """Resolve one closed custom or catalog-backed thread definition."""

    if not isinstance(raw, Mapping):
        _error("Thread definition must be one closed definition request.")
    kind = str(raw.get("kind") or "")
    if kind == "standard":
        result = _standard_definition(raw)
    elif kind == "custom":
        result = _custom_definition(raw)
    else:
        _error("Thread definition kind must be standard or custom.")
    if result.minor_diameter_mm >= result.major_diameter_mm:
        _error("Thread minor diameter must be smaller than its major diameter.")
    return result
