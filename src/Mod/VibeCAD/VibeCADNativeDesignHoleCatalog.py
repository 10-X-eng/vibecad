# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded access to the live Part Design Hole catalogs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeModelHoleSchema import THREAD_STANDARDS


MAX_STANDARDS = 16
MAX_SIZES_PER_STANDARD = 256
MAX_CATALOG_TEXT = 64
MAX_HEAD_DEFINITION_FILES = 64
_DYNAMIC_STANDARD = {
    "metric": "ISOMetricProfile",
    "metricfine": "ISOMetricFineProfile",
}


def _text(value: Any, *, field: str) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > MAX_CATALOG_TEXT:
        raise NativeModelError(f"The Hole catalog contains an invalid {field}.")
    return clean


def _text_list(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list) or len(value) > 64:
        raise NativeModelError(f"The Hole catalog contains invalid {field} values.")
    return [_text(item, field=field) for item in value]


def _size(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise NativeModelError("The Hole catalog contains an invalid size.")
    result = {"designation": _text(value.get("designation"), field="size")}
    for source, target in (
        ("diameter_mm", "diameter_mm"),
        ("pitch_mm", "pitch_mm"),
        ("tap_drill_mm", "tap_drill_mm"),
    ):
        number = float(value.get(source, 0.0))
        if number < 0.0 or number > 1_000_000.0:
            raise NativeModelError("The Hole catalog contains an invalid dimension.")
        result[target] = number
    return result


def _native_catalog() -> dict[str, dict[str, Any]]:
    import PartDesign

    raw = PartDesign.getHoleThreadCatalog()
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_STANDARDS:
        raise NativeModelError("The native Hole thread catalog is unavailable.")
    result: dict[str, dict[str, Any]] = {}
    for value in raw:
        if not isinstance(value, Mapping):
            raise NativeModelError("The native Hole thread catalog is invalid.")
        standard = _text(value.get("standard"), field="standard")
        sizes = value.get("sizes")
        if not isinstance(sizes, list) or not 1 <= len(sizes) <= MAX_SIZES_PER_STANDARD:
            raise NativeModelError("The native Hole size catalog is invalid.")
        result[standard] = {
            "standard": standard,
            "sizes": [_size(item) for item in sizes],
            "classes": _text_list(value.get("classes"), field="class"),
            "fits": _text_list(value.get("fits"), field="fit"),
            "heads": [],
        }
    expected = {"None", *THREAD_STANDARDS}
    if set(result) != expected:
        raise NativeModelError("The native Hole standards changed unexpectedly.")
    return result


def _dynamic_head_directories() -> tuple[Path, ...]:
    import FreeCAD as App

    return (
        Path(App.getResourceDir()) / "Mod" / "PartDesign" / "Resources" / "Hole",
        Path(App.getUserAppDataDir()) / "PartDesign" / "Hole",
    )


def _load_dynamic_heads() -> dict[str, list[dict[str, Any]]]:
    result = {standard: [] for standard in THREAD_STANDARDS}
    files_seen = 0
    for directory in _dynamic_head_directories():
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            files_seen += 1
            if files_seen > MAX_HEAD_DEFINITION_FILES:
                raise NativeModelError("The bounded Hole head catalog is too large.")
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise NativeModelError(
                    f"Hole head definition {path.name!r} is unreadable."
                ) from exc
            if not isinstance(value, Mapping):
                raise NativeModelError("A Hole head definition is invalid.")
            standard = _DYNAMIC_STANDARD.get(str(value.get("thread_type") or ""))
            kind = str(value.get("cut_type") or "")
            if standard is None or kind not in {"counterbore", "countersink"}:
                raise NativeModelError("A Hole head definition has unsupported metadata.")
            dimensions = value.get("data")
            if (
                not isinstance(dimensions, list)
                or not 1 <= len(dimensions) <= MAX_SIZES_PER_STANDARD
            ):
                raise NativeModelError("A Hole head definition has invalid size data.")
            supported_sizes = []
            for dimension in dimensions:
                if not isinstance(dimension, Mapping):
                    raise NativeModelError("A Hole head definition has invalid dimensions.")
                supported_sizes.append(
                    _text(dimension.get("thread"), field="head size")
                )
            if len(set(supported_sizes)) != len(supported_sizes):
                raise NativeModelError("A Hole head definition repeats a size.")
            item = {
                "designation": _text(value.get("name"), field="head designation"),
                "kind": kind,
                "supported_sizes": supported_sizes,
            }
            existing = result[standard]
            existing[:] = [
                entry
                for entry in existing
                if entry["designation"] != item["designation"]
            ]
            existing.append(item)
    return result


def load_hole_catalog() -> dict[str, dict[str, Any]]:
    catalog = _native_catalog()
    for standard, heads in _load_dynamic_heads().items():
        catalog[standard]["heads"].extend(heads)
    return catalog


def read_hole_catalog(standard: str | None) -> dict[str, Any]:
    catalog = load_hole_catalog()
    if standard is None:
        return {
            "standards": [
                {
                    "standard": name,
                    "size_count": len(catalog[name]["sizes"]),
                    "classes": list(catalog[name]["classes"]),
                    "fits": list(catalog[name]["fits"]),
                    "heads": list(catalog[name]["heads"]),
                }
                for name in THREAD_STANDARDS
            ]
        }
    if standard not in THREAD_STANDARDS:
        raise NativeModelError("That Hole thread standard is unavailable.")
    entry = catalog[standard]
    return {
        "standard": standard,
        "sizes": list(entry["sizes"]),
        "classes": list(entry["classes"]),
        "fits": list(entry["fits"]),
        "heads": list(entry["heads"]),
    }


def require_hole_catalog_selection(
    catalog: Mapping[str, Mapping[str, Any]],
    *,
    standard: str,
    size: str,
    thread_class: str | None = None,
    fit: str | None = None,
) -> Mapping[str, Any]:
    entry = catalog.get(standard)
    if entry is None:
        raise NativeModelError("That Hole thread standard is unavailable.")
    if size not in {item["designation"] for item in entry["sizes"]}:
        raise NativeModelError("That Hole size is unavailable for the selected standard.")
    if thread_class is not None and thread_class not in entry["classes"]:
        raise NativeModelError("That Hole thread class is unavailable.")
    if fit is not None and fit not in entry["fits"]:
        raise NativeModelError("That Hole clearance fit is unavailable.")
    return entry
