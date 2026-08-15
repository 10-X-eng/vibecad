# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact bounded state for projected TechDraw dimensions."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Mapping


MAX_DRAWING_DIMENSION_TEXT_CHARACTERS = 512
MAX_DRAWING_DIMENSION_STATE_MESSAGES = 16
_PROJECTED_NAME = re.compile(r"^(?:Edge|Vertex|Face)(?:0|[1-9][0-9]*)$")
_DIMENSION_TYPES = frozenset(
    {
        "Distance",
        "DistanceX",
        "DistanceY",
        "Radius",
        "Diameter",
        "Angle",
        "Angle3Pt",
        "Area",
    }
)
_CHAMFER_SUFFIX = re.compile(r" x[0-9]+°$")


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_derived(obj: Any, type_id: str) -> bool:
    checker = getattr(obj, "isDerivedFrom", None)
    if callable(checker):
        try:
            return bool(checker(type_id))
        except Exception:
            return False
    return str(getattr(obj, "TypeId", "") or "") == type_id


def is_drawing_dimension(obj: Any) -> bool:
    return _is_derived(obj, "TechDraw::DrawViewDimension")


def is_drawing_extent(obj: Any) -> bool:
    return _is_derived(obj, "TechDraw::DrawViewDimExtent")


def is_drawing_axonometric_dimension(obj: Any) -> bool:
    return (
        is_drawing_dimension(obj)
        and not is_drawing_extent(obj)
        and str(getattr(obj, "Type", "") or "") == "Distance"
        and bool(getattr(obj, "AngleOverride", False))
    )


def _finite(value: Any, noun: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Drawing dimension {noun} is not numeric.") from exc
    if not math.isfinite(result) or abs(result) > 1_000_000_000_000.0:
        raise ValueError(f"Drawing dimension {noun} is outside the supported range.")
    return round(result, 12)


def _references(dimension: Any) -> list[dict[str, str]]:
    result = []
    for obj, raw_names in tuple(getattr(dimension, "References2D", ()) or ()):
        names = (raw_names,) if isinstance(raw_names, str) else tuple(raw_names or ())
        for raw_name in names:
            name = str(raw_name or "")
            object_name = str(getattr(obj, "Name", "") or "")
            if (
                getattr(obj, "Document", None) is not dimension.Document
                or not object_name
                or _PROJECTED_NAME.fullmatch(name) is None
            ):
                raise ValueError("Drawing dimension references are malformed.")
            result.append({"view_name": object_name, "subelement": name})
    if not result:
        raise ValueError("Drawing dimension has no projected references.")
    return result


def _state_messages(dimension: Any) -> list[str]:
    result = []
    for raw in tuple(getattr(dimension, "State", ()) or ()):
        message = str(raw or "").strip()
        if message:
            result.append(message[:256])
        if len(result) >= MAX_DRAWING_DIMENSION_STATE_MESSAGES:
            break
    return result


def _timeline_usable(dimension: Any) -> bool:
    document = getattr(dimension, "Document", None)
    checker = getattr(document, "isObjectUsableAtCurrentTimelinePosition", None)
    return bool(not callable(checker) or checker(dimension))


def _raw_link_references(
    dimension: Any,
    property_name: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    result: list[dict[str, Any]] = []
    issues: list[str] = []
    try:
        raw_entries = tuple(getattr(dimension, property_name, ()) or ())
    except Exception:
        return [], [f"{property_name} cannot be read"]
    for index, raw_entry in enumerate(raw_entries[:64]):
        if not isinstance(raw_entry, tuple) or len(raw_entry) != 2:
            issues.append(f"{property_name}[{index}] is malformed")
            continue
        obj, raw_names = raw_entry
        object_name = str(getattr(obj, "Name", "") or "") if obj else ""
        object_type = str(getattr(obj, "TypeId", "") or "") if obj else ""
        same_document = bool(
            obj is not None
            and getattr(obj, "Document", None) is getattr(dimension, "Document", None)
        )
        if not object_name or not same_document:
            issues.append(f"{property_name}[{index}] has no live local object")
        names = (raw_names,) if isinstance(raw_names, str) else tuple(raw_names or ())
        if not names:
            names = ("",)
        for raw_name in names[:64]:
            name = str(raw_name or "")[:256]
            result.append(
                {
                    "object_name": object_name,
                    "object_type": object_type,
                    "same_document": same_document,
                    "subelement": name,
                }
            )
    if len(raw_entries) > 64:
        issues.append(f"{property_name} exceeds the 64-reference inspection limit")
    return result, issues[:16]


def _raw_arc_source(dimension: Any) -> dict[str, Any]:
    try:
        raw = getattr(dimension, "ArcLengthSource", None)
    except Exception:
        raw = None
    if not isinstance(raw, tuple) or len(raw) != 2:
        return {"object_name": "", "subelements": []}
    obj, raw_names = raw
    names = (raw_names,) if isinstance(raw_names, str) else tuple(raw_names or ())
    return {
        "object_name": str(getattr(obj, "Name", "") or "") if obj else "",
        "subelements": [str(name or "")[:256] for name in names[:4]],
    }


def _projected_reference_issues(
    dimension: Any,
    references: list[dict[str, Any]],
    projection_names_by_view: dict[str, frozenset[str] | None],
) -> list[str]:
    if not references:
        return ["References2D has no projected target"]
    from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state

    document = getattr(dimension, "Document", None)
    issues = []
    extent = is_drawing_extent(dimension)
    for index, reference in enumerate(references[:64]):
        object_name = str(reference["object_name"] or "")
        subelement = str(reference["subelement"] or "")
        view = document.getObject(object_name) if document and object_name else None
        if view is None or not _is_derived(view, "TechDraw::DrawViewPart"):
            issues.append(f"References2D[{index}] does not target a projected view")
            continue
        if not subelement:
            if not extent:
                issues.append(f"References2D[{index}] has no projected subelement")
            continue
        if _PROJECTED_NAME.fullmatch(subelement) is None:
            issues.append(f"References2D[{index}] has a malformed projected subelement")
            continue
        if object_name not in projection_names_by_view:
            try:
                projection = drawing_projected_geometry_state(view)
                projection_names_by_view[object_name] = frozenset(
                    str(item["name"]) for item in projection["elements"]
                )
            except (AttributeError, RuntimeError, TypeError, ValueError):
                projection_names_by_view[object_name] = None
        available = projection_names_by_view[object_name]
        if available is None or subelement not in available:
            issues.append(
                f"References2D[{index}] projected subelement is unavailable"
            )
    return issues[:16]


def _repair_kind(dimension: Any, dimension_type: str) -> str:
    if is_drawing_extent(dimension):
        direction = int(getattr(dimension, "DirExtent", -1))
        if dimension_type == "DistanceX" and direction == 0:
            return "horizontal_extent"
        if dimension_type == "DistanceY" and direction == 1:
            return "vertical_extent"
        return "unsupported"
    if bool(getattr(dimension, "AngleOverride", False)):
        return "axonometric_length" if dimension_type == "Distance" else "unsupported"
    format_spec = str(getattr(dimension, "FormatSpec", "") or "")
    arc_source = _raw_arc_source(dimension)
    try:
        arc_length = float(getattr(dimension, "ArcLengthValue", 0.0))
    except (TypeError, ValueError):
        arc_length = 0.0
    if (
        dimension_type == "Distance"
        and (
            format_spec.startswith("◠ ")
            or arc_length > 0.0
            or bool(arc_source["object_name"])
        )
    ):
        return "arc_length"
    if dimension_type in {"DistanceX", "DistanceY"} and _CHAMFER_SUFFIX.search(
        format_spec
    ):
        return (
            "horizontal_chamfer"
            if dimension_type == "DistanceX"
            else "vertical_chamfer"
        )
    return {
        "Distance": "length",
        "DistanceX": "horizontal",
        "DistanceY": "vertical",
        "Radius": "radius",
        "Diameter": "diameter",
        "Angle": "angle",
        "Angle3Pt": "three_point_angle",
        "Area": "area",
    }.get(dimension_type, "unsupported")


def drawing_dimension_repair_state(
    dimension: Any,
    *,
    projection_names_by_view: dict[str, frozenset[str] | None] | None = None,
) -> dict[str, Any]:
    """Return a durable exact target even when dimension references are broken."""

    if not is_drawing_dimension(dimension):
        raise TypeError("dimension must be a TechDraw::DrawViewDimension")
    references2d, issues2d = _raw_link_references(dimension, "References2D")
    references3d, issues3d = _raw_link_references(dimension, "References3D")
    projection_cache = (
        projection_names_by_view
        if projection_names_by_view is not None
        else {}
    )
    issues2d.extend(
        _projected_reference_issues(dimension, references2d, projection_cache)
    )
    page = dimension.findParentPage()
    page_name = str(getattr(page, "Name", "") or "") if page else ""
    document = getattr(dimension, "Document", None)
    if not page_name or getattr(page, "Document", None) is not document:
        issues2d.append("dimension is not attached to a live page")
    dimension_type = str(getattr(dimension, "Type", "") or "")
    repair_kind = _repair_kind(dimension, dimension_type)
    if repair_kind == "unsupported":
        issues2d.append("dimension semantic kind is unsupported")
    format_spec = str(getattr(dimension, "FormatSpec", "") or "")
    host_valid = bool(dimension.isValid())
    references_valid = not issues2d
    valid = host_valid and references_valid
    exact = {
        "object_name": str(getattr(dimension, "Name", "") or ""),
        "label": str(getattr(dimension, "Label", "") or ""),
        "type_id": str(getattr(dimension, "TypeId", "") or ""),
        "page_name": page_name,
        "dimension_type": dimension_type,
        "measure_type": str(getattr(dimension, "MeasureType", "") or ""),
        "repair_kind": repair_kind,
        "references_2d": references2d,
        "references_3d": references3d,
        "label_position_in_view_mm": {
            "x_mm": _finite(getattr(dimension, "X", 0.0), "label X coordinate"),
            "y_mm": _finite(getattr(dimension, "Y", 0.0), "label Y coordinate"),
        },
        "angle_override": bool(getattr(dimension, "AngleOverride", False)),
        "line_angle_degrees": _finite(
            getattr(dimension, "LineAngle", 0.0), "line angle"
        ),
        "extension_angle_degrees": _finite(
            getattr(dimension, "ExtensionAngle", 0.0), "extension angle"
        ),
        "arbitrary_display": bool(getattr(dimension, "Arbitrary", False)),
        "format_spec_sha256": hashlib.sha256(format_spec.encode("utf-8")).hexdigest(),
        "arc_length_source": _raw_arc_source(dimension),
        "arc_length_value_mm": _finite(
            getattr(dimension, "ArcLengthValue", 0.0), "arc length value"
        ),
        "timeline_role": str(getattr(dimension, "VibeCADTimelineRole", "") or ""),
        "timeline_owner_name": str(
            getattr(getattr(dimension, "VibeCADTimelineOwner", None), "Name", "") or ""
        ),
        "timeline_usable": _timeline_usable(dimension),
        "host_valid": host_valid,
        "references_valid": references_valid,
        "valid": valid,
        "error": not valid,
    }
    issues = [*issues2d, *issues3d][:16]
    return {
        **exact,
        "repairable": bool(
            page_name
            and repair_kind != "unsupported"
            and exact["measure_type"] == "Projected"
            and exact["timeline_usable"]
        ),
        "issues": issues,
        "repair_state_sha256": _digest(exact),
    }


def drawing_dimension_state(dimension: Any) -> dict[str, Any]:
    """Return one projected dimension's exact identity and measured result."""

    if not is_drawing_dimension(dimension):
        raise TypeError("dimension must be a TechDraw::DrawViewDimension")
    dimension_type = str(getattr(dimension, "Type", "") or "")
    measure_type = str(getattr(dimension, "MeasureType", "") or "")
    if dimension_type not in _DIMENSION_TYPES or measure_type != "Projected":
        raise ValueError("Drawing dimension type or measurement mode is unsupported.")
    references = _references(dimension)
    view_names = {item["view_name"] for item in references}
    if len(view_names) != 1:
        raise ValueError("Drawing dimension references must belong to one exact view.")
    page = dimension.findParentPage()
    page_name = str(getattr(page, "Name", "") or "") if page else ""
    if not page_name or getattr(page, "Document", None) is not dimension.Document:
        raise ValueError("Drawing dimension is not attached to a live page.")
    raw_value = _finite(dimension.getRawValue(), "measured value")
    text = str(dimension.getText() or "")
    text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    state_messages = _state_messages(dimension)
    valid = bool(dimension.isValid())
    unit = (
        "degrees"
        if dimension_type in {"Angle", "Angle3Pt"}
        else "mm^2"
        if dimension_type == "Area"
        else "mm"
    )
    exact = {
        "object_name": str(getattr(dimension, "Name", "") or ""),
        "label": str(getattr(dimension, "Label", "") or ""),
        "type_id": str(getattr(dimension, "TypeId", "") or ""),
        "page_name": page_name,
        "view_name": next(iter(view_names)),
        "dimension_type": dimension_type,
        "measure_type": measure_type,
        "references": references,
        "label_position_in_view_mm": {
            "x_mm": _finite(getattr(dimension, "X", 0.0), "label X coordinate"),
            "y_mm": _finite(getattr(dimension, "Y", 0.0), "label Y coordinate"),
        },
        "measured_value": {"value": raw_value, "unit": unit},
        "formatted_text_sha256": text_sha256,
        "formatted_text_characters": len(text),
        "timeline_role": str(
            getattr(dimension, "VibeCADTimelineRole", "") or ""
        ),
        "timeline_owner_name": str(
            getattr(getattr(dimension, "VibeCADTimelineOwner", None), "Name", "")
            or ""
        ),
        "timeline_usable": _timeline_usable(dimension),
        "valid": valid,
    }
    # DocumentObject.State is transient recompute bookkeeping. Report it for
    # diagnosis, but keep it out of the durable exact-target hash so an
    # otherwise identical undo/redo or reopen does not become falsely stale.
    result = {
        **exact,
        "state_messages": state_messages,
        "state_sha256": _digest(exact),
    }
    result["formatted_text"] = text[:MAX_DRAWING_DIMENSION_TEXT_CHARACTERS]
    if len(text) > MAX_DRAWING_DIMENSION_TEXT_CHARACTERS:
        result["formatted_text_truncated"] = True
    return result


def _extent_references(dimension: Any) -> tuple[str, list[str]]:
    view_name = ""
    subelements: list[str] = []
    for obj, raw_names in tuple(getattr(dimension, "References2D", ()) or ()):
        object_name = str(getattr(obj, "Name", "") or "")
        if (
            getattr(obj, "Document", None) is not dimension.Document
            or not object_name
        ):
            raise ValueError("Drawing extent references are malformed.")
        if view_name and object_name != view_name:
            raise ValueError("Drawing extent references must belong to one view.")
        view_name = object_name
        names = (raw_names,) if isinstance(raw_names, str) else tuple(raw_names or ())
        for raw_name in names:
            name = str(raw_name or "")
            if not name:
                continue
            if not name.startswith("Edge") or _PROJECTED_NAME.fullmatch(name) is None:
                raise ValueError("Drawing extent references must be projected edges.")
            subelements.append(name)
    if not view_name or len(subelements) != len(set(subelements)):
        raise ValueError("Drawing extent has no unique live projected view target.")
    return view_name, subelements


def drawing_extent_state(dimension: Any) -> dict[str, Any]:
    """Return one projected extent's exact target and measured result."""

    if not is_drawing_extent(dimension):
        raise TypeError("dimension must be a TechDraw::DrawViewDimExtent")
    dimension_type = str(getattr(dimension, "Type", "") or "")
    direction = int(getattr(dimension, "DirExtent", -1))
    expected_direction = 0 if dimension_type == "DistanceX" else 1
    if dimension_type not in {"DistanceX", "DistanceY"} or direction != expected_direction:
        raise ValueError("Drawing extent type and direction are inconsistent.")
    measure_type = str(getattr(dimension, "MeasureType", "") or "")
    if measure_type != "Projected":
        raise ValueError("Drawing extent measurement mode is unsupported.")
    view_name, subelements = _extent_references(dimension)
    page = dimension.findParentPage()
    page_name = str(getattr(page, "Name", "") or "") if page else ""
    if not page_name or getattr(page, "Document", None) is not dimension.Document:
        raise ValueError("Drawing extent is not attached to a live page.")
    raw_value = _finite(dimension.getRawValue(), "measured value")
    text = str(dimension.getText() or "")
    exact = {
        "object_name": str(getattr(dimension, "Name", "") or ""),
        "label": str(getattr(dimension, "Label", "") or ""),
        "type_id": str(getattr(dimension, "TypeId", "") or ""),
        "page_name": page_name,
        "view_name": view_name,
        "dimension_type": dimension_type,
        "extent_direction": "horizontal" if direction == 0 else "vertical",
        "measure_type": measure_type,
        "target": {
            "scope": "edges" if subelements else "whole_view",
            "subelements": subelements,
        },
        "label_position_in_view_mm": {
            "x_mm": _finite(getattr(dimension, "X", 0.0), "label X coordinate"),
            "y_mm": _finite(getattr(dimension, "Y", 0.0), "label Y coordinate"),
        },
        "measured_value": {"value": raw_value, "unit": "mm"},
        "formatted_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "formatted_text_characters": len(text),
        "timeline_role": str(getattr(dimension, "VibeCADTimelineRole", "") or ""),
        "timeline_owner_name": str(
            getattr(getattr(dimension, "VibeCADTimelineOwner", None), "Name", "")
            or ""
        ),
        "timeline_usable": _timeline_usable(dimension),
        "valid": bool(dimension.isValid()),
    }
    result = {
        **exact,
        "state_messages": _state_messages(dimension),
        "state_sha256": _digest(exact),
        "formatted_text": text[:MAX_DRAWING_DIMENSION_TEXT_CHARACTERS],
    }
    if len(text) > MAX_DRAWING_DIMENSION_TEXT_CHARACTERS:
        result["formatted_text_truncated"] = True
    return result


def drawing_axonometric_dimension_state(dimension: Any) -> dict[str, Any]:
    """Return exact persisted output state for an axonometric length."""

    base = drawing_dimension_state(dimension)
    if base["dimension_type"] != "Distance":
        raise ValueError("An axonometric length must be a Distance dimension.")
    angle_override = bool(getattr(dimension, "AngleOverride", False))
    if not angle_override:
        raise ValueError("An axonometric length requires an angle override.")
    format_spec = str(getattr(dimension, "FormatSpec", "") or "")
    axonometric = {
        "angle_override": True,
        "line_angle_degrees": _finite(
            getattr(dimension, "LineAngle", 0.0),
            "line angle",
        ),
        "extension_angle_degrees": _finite(
            getattr(dimension, "ExtensionAngle", 0.0),
            "extension angle",
        ),
        "arbitrary_display": bool(getattr(dimension, "Arbitrary", False)),
        "format_spec_sha256": hashlib.sha256(
            format_spec.encode("utf-8")
        ).hexdigest(),
        "format_spec_characters": len(format_spec),
    }
    exact = {
        "base_state_sha256": base["state_sha256"],
        "axonometric": axonometric,
    }
    return {
        **base,
        "axonometric": {
            **axonometric,
            "format_spec": format_spec[:MAX_DRAWING_DIMENSION_TEXT_CHARACTERS],
        },
        "state_sha256": _digest(exact),
    }
