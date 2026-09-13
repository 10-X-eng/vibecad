# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared exact configuration service for human and automated CAM setup edits."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping, Sequence

import Constants


EDITABLE_FIELDS = frozenset(
    {
        "label",
        "description",
        "machine",
        "postprocessor",
        "postprocessor_args",
        "fixtures",
        "split_output",
        "output_order",
        "geometry_tolerance_mm",
    }
)
OUTPUT_ORDERS = ("Fixture", "Tool", "Operation")
MAX_PUBLISHED_TEXT = 4096


def _sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            dict(value),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _quantity_mm(value: Any) -> float:
    reader = getattr(value, "getValueAs", None)
    raw = reader("mm") if callable(reader) else getattr(value, "Value", value)
    result = float(raw)
    if not math.isfinite(result):
        raise ValueError("geometry_tolerance_mm must be finite")
    return round(result, 9)


def _catalog_names(values: Sequence[Any]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            str(value or "").strip()
            for value in values
            if str(value or "").strip()
        )
    )


def _available_machine_names() -> tuple[str, ...]:
    from Machine.models.machine import MachineFactory

    return tuple(
        name
        for name in MachineFactory.list_configurations()
        if name and name != "<any>"
    )


def _available_postprocessor_names() -> tuple[str, ...]:
    import Path

    return _catalog_names(Path.Preferences.allEnabledLegacyPostProcessors())


def search_setup_options(
    category: str,
    *,
    query: str,
    offset: int,
    page_size: int,
    machine_names: Sequence[Any] | None = None,
    postprocessor_names: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """Return one exact searchable page of values accepted by setup editing."""

    if category == "machine":
        values = _catalog_names(
            _available_machine_names() if machine_names is None else machine_names
        )
    elif category == "postprocessor":
        values = _catalog_names(
            _available_postprocessor_names()
            if postprocessor_names is None
            else postprocessor_names
        )
    else:
        raise ValueError("category must be machine or postprocessor")
    normalized_query = str(query or "").strip().casefold()
    matches = [
        value
        for value in values
        if not normalized_query or normalized_query in value.casefold()
    ]
    start = min(int(offset), len(matches))
    stop = min(start + int(page_size), len(matches))
    return {
        "category": category,
        "query": normalized_query,
        "offset": start,
        "count": stop - start,
        "total": len(matches),
        "next_offset": stop if stop < len(matches) else None,
        "values": matches[start:stop],
    }


def setup_configuration_state(job: Any) -> dict[str, Any]:
    """Return bounded authored configuration for one explicit CAM Job."""

    description = str(getattr(job, "Description", "") or "")
    postprocessor_args = str(getattr(job, "PostProcessorArgs", "") or "")
    exact = {
        "object_name": str(getattr(job, "Name", "") or ""),
        "label": str(getattr(job, "Label", "") or ""),
        "description": description,
        "machine": str(getattr(job, "Machine", "") or ""),
        "postprocessor": str(getattr(job, "PostProcessor", "") or ""),
        "postprocessor_args": postprocessor_args,
        "fixtures": [str(value) for value in tuple(getattr(job, "Fixtures", ()) or ())],
        "split_output": bool(getattr(job, "SplitOutput", False)),
        "output_order": str(getattr(job, "OrderOutputBy", "") or ""),
        "geometry_tolerance_mm": _quantity_mm(
            getattr(job, "GeometryTolerance", 0.0)
        ),
    }
    result = dict(exact)
    result["description"] = description[:MAX_PUBLISHED_TEXT]
    result["description_truncated"] = len(description) > MAX_PUBLISHED_TEXT
    result["postprocessor_args"] = postprocessor_args[:MAX_PUBLISHED_TEXT]
    result["postprocessor_args_truncated"] = (
        len(postprocessor_args) > MAX_PUBLISHED_TEXT
    )
    result["state_sha256"] = _sha256(exact)
    return result


def normalize_setup_changes(
    changes: Mapping[str, Any],
    *,
    machine_names: Sequence[Any] | None = None,
    postprocessor_names: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """Validate a complete partial edit before any Job property is changed."""

    if not isinstance(changes, Mapping) or not changes:
        raise ValueError("setup changes must contain at least one editable field")
    unexpected = set(changes) - EDITABLE_FIELDS
    if unexpected:
        raise ValueError(
            f"unsupported CAM setup field: {sorted(unexpected)[0]}"
        )
    result: dict[str, Any] = {}
    for name in ("label", "description", "postprocessor_args"):
        if name in changes:
            result[name] = str(changes[name] or "")

    if "machine" in changes:
        value = str(changes["machine"] or "").strip()
        available = _catalog_names(
            _available_machine_names() if machine_names is None else machine_names
        )
        if value and value not in available:
            raise ValueError(
                f"machine {value!r} is unavailable; expected one catalog name"
            )
        result["machine"] = value

    if "postprocessor" in changes:
        value = str(changes["postprocessor"] or "").strip()
        available = _catalog_names(
            _available_postprocessor_names()
            if postprocessor_names is None
            else postprocessor_names
        )
        if value and value not in available:
            raise ValueError(
                f"postprocessor {value!r} is unavailable; expected one enabled name"
            )
        result["postprocessor"] = value

    if "fixtures" in changes:
        raw = changes["fixtures"]
        if isinstance(raw, (str, bytes, bytearray)) or not isinstance(
            raw,
            (list, tuple),
        ):
            raise ValueError("fixtures must be an ordered list of work offsets")
        fixtures = tuple(str(value or "").strip() for value in raw)
        if len(fixtures) > len(Constants.GCODE_FIXTURES):
            raise ValueError("fixtures contains too many work offsets")
        if len(set(fixtures)) != len(fixtures) or any(
            value not in Constants.GCODE_FIXTURES for value in fixtures
        ):
            raise ValueError("fixtures must contain distinct supported work offsets")
        result["fixtures"] = list(fixtures)

    if "split_output" in changes:
        if not isinstance(changes["split_output"], bool):
            raise ValueError("split_output must be boolean")
        result["split_output"] = changes["split_output"]

    if "output_order" in changes:
        value = str(changes["output_order"] or "")
        if value not in OUTPUT_ORDERS:
            raise ValueError("output_order must be Fixture, Tool, or Operation")
        result["output_order"] = value

    if "geometry_tolerance_mm" in changes:
        value = _quantity_mm(changes["geometry_tolerance_mm"])
        if value <= 0.0:
            raise ValueError("geometry_tolerance_mm must be greater than zero")
        result["geometry_tolerance_mm"] = value
    return result


def _assign_postprocessor(job: Any, value: str, options: Sequence[Any]) -> None:
    type_reader = getattr(job, "getTypeIdOfProperty", None)
    if callable(type_reader) and str(type_reader("PostProcessor")) == (
        "App::PropertyEnumeration"
    ):
        choices = list(dict.fromkeys(("", *_catalog_names(options), value)))
        job.PostProcessor = choices
    job.PostProcessor = value


def apply_setup_configuration(
    job: Any,
    changes: Mapping[str, Any],
    *,
    machine_names: Sequence[Any] | None = None,
    postprocessor_names: Sequence[Any] | None = None,
    recompute: bool = True,
) -> dict[str, Any]:
    """Apply one validated setup edit to the explicit Job."""

    available_machines = _catalog_names(
        _available_machine_names() if machine_names is None else machine_names
    )
    current_machine = str(getattr(job, "Machine", "") or "").strip()
    if current_machine and current_machine not in available_machines:
        available_machines = (*available_machines, current_machine)
    available_posts = _catalog_names(
        _available_postprocessor_names()
        if postprocessor_names is None
        else postprocessor_names
    )
    current_post = str(getattr(job, "PostProcessor", "") or "").strip()
    if current_post and current_post not in available_posts:
        available_posts = (*available_posts, current_post)
    normalized = normalize_setup_changes(
        changes,
        machine_names=available_machines,
        postprocessor_names=available_posts,
    )
    assignments = {
        "label": "Label",
        "description": "Description",
        "machine": "Machine",
        "postprocessor_args": "PostProcessorArgs",
        "fixtures": "Fixtures",
        "split_output": "SplitOutput",
        "output_order": "OrderOutputBy",
        "geometry_tolerance_mm": "GeometryTolerance",
    }
    for name, property_name in assignments.items():
        if name in normalized:
            setattr(job, property_name, normalized[name])
    if "postprocessor" in normalized:
        _assign_postprocessor(job, normalized["postprocessor"], available_posts)
    executor = getattr(getattr(job, "Proxy", None), "execute", None)
    if recompute and callable(executor):
        executor(job)
    return setup_configuration_state(job)
