# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared exact target capture for Native Drawing dimension families."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewState import drawing_view_state
from VibeCADNativeTargets import read_current_selection, resolve_object


@dataclass(frozen=True, slots=True)
class PreparedDrawingDimensionTarget:
    page: Any
    page_state_before: dict[str, Any]
    page_views_before: tuple[Any, ...]
    view: Any
    view_state_before: dict[str, Any]
    projection_state_before: dict[str, Any]
    element_states_before: tuple[dict[str, Any], ...]
    objects_before: tuple[Any, ...]
    timeline_before: tuple[Any, ...]
    selection_before: dict[str, Any]
    visibility_before: tuple[tuple[Any, bool], ...]


def drawing_dimension_error(
    message: str,
    code: str,
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    raise NativeDrawingError(message, error_code=code, repair=repair)


def finite_drawing_coordinate(
    value: Any,
    noun: str,
    *,
    family: str = "dimension",
    limit_mm: float = 10_000.0,
    error_code: str = "NATIVE_DRAWING_DIMENSION_PARAMETERS_INVALID",
) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeDrawingError(
            f"Drawing {family} {noun} must be numeric.",
            error_code=error_code,
        ) from exc
    if not math.isfinite(result) or not -limit_mm <= result <= limit_mm:
        drawing_dimension_error(
            f"Drawing {family} {noun} must be finite and between "
            f"{-limit_mm:g} and {limit_mm:g} mm.",
            error_code,
        )
    return result


def exact_drawing_mapping(
    value: Any,
    keys: frozenset[str],
    noun: str,
    *,
    family: str = "dimension",
    error_code: str = "NATIVE_DRAWING_DIMENSION_PARAMETERS_INVALID",
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or frozenset(value) != keys:
        drawing_dimension_error(
            f"The exact Drawing {family} {noun} is malformed.",
            error_code,
        )
    return value


def drawing_timeline_operations(document: Any) -> tuple[Any, ...]:
    timeline = document.getObject("VibeCADTimeline")
    return tuple(getattr(timeline, "Operations", ()) or ()) if timeline else ()


def drawing_selection_state(document: Any) -> dict[str, Any]:
    try:
        return read_current_selection(document)
    except (AttributeError, ImportError, RuntimeError):
        return {
            "document_uid": str(document.Uid),
            "selected_count": 0,
            "items": [],
        }


def drawing_visibility_state(document: Any) -> tuple[tuple[Any, bool], ...]:
    result = []
    for obj in tuple(document.Objects):
        view_object = getattr(obj, "ViewObject", None)
        if view_object is not None:
            result.append((obj, bool(getattr(view_object, "Visibility", False))))
    return tuple(result)


def drawing_object_key(obj: Any) -> tuple[int, str, str]:
    return (
        int(getattr(obj, "ID", -1)),
        str(getattr(obj, "Name", "") or ""),
        str(getattr(obj, "TypeId", "") or ""),
    )


def matches_drawing_document_label(actual: str, requested: str) -> bool:
    if actual == requested:
        return True
    base = requested.rstrip("0123456789")
    suffix = actual[len(base) :] if actual.startswith(base) else ""
    return len(suffix) >= 3 and suffix.isdigit()


def _require_usable(document: Any, obj: Any, noun: str) -> None:
    checker = getattr(document, "isObjectUsableAtCurrentTimelinePosition", None)
    if callable(checker) and not bool(checker(obj)):
        drawing_dimension_error(
            f"The exact {noun} is not usable at the current History position.",
            "NATIVE_DRAWING_HISTORY_TARGET_UNAVAILABLE",
        )


def _resolve_page(
    document: Any,
    target: Any,
    *,
    family: str,
    code_prefix: str,
) -> tuple[Any, dict[str, Any]]:
    exact = exact_drawing_mapping(
        target,
        frozenset({"object_name", "expected_state_sha256"}),
        "page target",
        family=family,
        error_code=f"{code_prefix}_PARAMETERS_INVALID",
    )
    page = resolve_object(
        document,
        {"document_uid": str(document.Uid), "object_name": exact["object_name"]},
        expected_types=("TechDraw::DrawPage",),
    )
    state = drawing_page_state(page)
    if str(exact["expected_state_sha256"]) != state["state_sha256"]:
        drawing_dimension_error(
            "The exact Drawing page changed after it was inspected.",
            "NATIVE_DRAWING_PAGE_STALE",
            repair={"current_state_sha256": state["state_sha256"]},
        )
    _require_usable(document, page, "Drawing page")
    return page, state


def _resolve_view(
    document: Any,
    page: Any,
    target: Any,
    *,
    family: str,
    code_prefix: str,
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    exact = exact_drawing_mapping(
        target,
        frozenset(
            {
                "object_name",
                "expected_state_sha256",
                "expected_projection_state_sha256",
            }
        ),
        "view target",
        family=family,
        error_code=f"{code_prefix}_PARAMETERS_INVALID",
    )
    view = resolve_object(
        document,
        {"document_uid": str(document.Uid), "object_name": exact["object_name"]},
        expected_types=("TechDraw::DrawViewPart",),
    )
    if view.findParentPage() is not page or view not in tuple(page.Views or ()):
        drawing_dimension_error(
            f"The exact Drawing {family} view does not belong to the exact page.",
            f"{code_prefix}_PAGE_MISMATCH",
        )
    _require_usable(document, view, "Drawing view")
    view_state = drawing_view_state(view)
    if str(exact["expected_state_sha256"]) != view_state["state_sha256"]:
        drawing_dimension_error(
            "The exact Drawing view changed after it was inspected.",
            f"{code_prefix}_VIEW_STALE",
            repair={"current_state_sha256": view_state["state_sha256"]},
        )
    projection_state = drawing_projected_geometry_state(view)
    expected_projection = str(exact["expected_projection_state_sha256"])
    if expected_projection != projection_state["projection_state_sha256"]:
        drawing_dimension_error(
            "The exact Drawing projection changed after it was inspected.",
            f"{code_prefix}_PROJECTION_STALE",
            repair={
                "current_projection_state_sha256": projection_state[
                    "projection_state_sha256"
                ]
            },
        )
    return view, view_state, projection_state


def prepare_drawing_dimension_target(
    document: Any,
    *,
    page_target: Any,
    view_target: Any,
    element_targets: tuple[Mapping[str, Any], ...],
    allowed_element_types: frozenset[str],
    family: str = "dimension",
    code_prefix: str = "NATIVE_DRAWING_DIMENSION",
) -> PreparedDrawingDimensionTarget:
    page, page_state = _resolve_page(
        document,
        page_target,
        family=family,
        code_prefix=code_prefix,
    )
    view, view_state, projection_state = _resolve_view(
        document,
        page,
        view_target,
        family=family,
        code_prefix=code_prefix,
    )
    by_name = {item["name"]: item for item in projection_state["elements"]}
    elements = []
    names = []
    for target in element_targets:
        exact = exact_drawing_mapping(
            target,
            frozenset({"subelement", "expected_element_state_sha256"}),
            "projected reference",
            family=family,
            error_code=f"{code_prefix}_PARAMETERS_INVALID",
        )
        name = str(exact["subelement"] or "")
        element = by_name.get(name)
        if element is None:
            drawing_dimension_error(
                f"Projected reference {name!r} no longer exists in the exact view.",
                f"{code_prefix}_REFERENCE_STALE",
                repair={"inspect_operation": "drawing_projected_geometry"},
            )
        if element["element_type"] not in allowed_element_types:
            drawing_dimension_error(
                f"Projected reference {name!r} has type {element['element_type']!r}.",
                f"{code_prefix}_REFERENCE_TYPE_INVALID",
                repair={"accepted_reference_types": sorted(allowed_element_types)},
            )
        if (
            str(exact["expected_element_state_sha256"])
            != element["element_state_sha256"]
        ):
            drawing_dimension_error(
                f"Projected reference {name!r} changed after it was inspected.",
                f"{code_prefix}_REFERENCE_STALE",
                repair={
                    "subelement": name,
                    "current_element_state_sha256": element[
                        "element_state_sha256"
                    ],
                },
            )
        names.append(name)
        elements.append(element)
    if len(names) != len(set(names)):
        drawing_dimension_error(
            f"A Drawing {family} cannot repeat the same projected reference.",
            f"{code_prefix}_REFERENCES_INVALID",
        )
    return PreparedDrawingDimensionTarget(
        page=page,
        page_state_before=page_state,
        page_views_before=tuple(page.Views or ()),
        view=view,
        view_state_before=view_state,
        projection_state_before=projection_state,
        element_states_before=tuple(elements),
        objects_before=tuple(document.Objects),
        timeline_before=drawing_timeline_operations(document),
        selection_before=drawing_selection_state(document),
        visibility_before=drawing_visibility_state(document),
    )
