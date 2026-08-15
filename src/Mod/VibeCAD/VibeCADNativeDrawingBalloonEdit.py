# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact transactional editing of existing TechDraw balloons."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from typing import Any, Mapping

from VibeCADNativeDrawingBalloonSchema import (
    DRAWING_BALLOON_LEADER_ENDS,
    DRAWING_BALLOON_SHAPES,
)
from VibeCADNativeDrawingBalloonState import drawing_balloon_state
from VibeCADNativeDrawingDimensionSupport import (
    drawing_dimension_error,
    drawing_object_key,
    drawing_selection_state,
    drawing_timeline_operations,
    drawing_visibility_state,
    exact_drawing_mapping,
    finite_drawing_coordinate,
)
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewState import drawing_view_state
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeTargets import object_identity, resolve_object


_STYLE_FIELDS = frozenset(
    {
        "bubble_shape",
        "leader_end",
        "bubble_scale",
        "leader_end_scale",
        "kink_length_mm",
        "font_size_mm",
        "line_width_mm",
        "line_visible",
        "color_rgb",
    }
)
_COLOR_FIELDS = frozenset({"red", "green", "blue"})


@dataclass(frozen=True, slots=True)
class DrawingBalloonEditSpec:
    operation: str
    text: str | None = None
    style: Mapping[str, Any] | None = None
    offset_x_mm: float | None = None
    offset_y_mm: float | None = None


@dataclass(frozen=True, slots=True)
class PreparedDrawingBalloonEdit:
    balloon: Any
    page: Any
    source_view: Any
    spec: DrawingBalloonEditSpec
    state_before: dict[str, Any]
    page_state_before: dict[str, Any]
    view_state_before: dict[str, Any]
    projection_state_before: dict[str, Any]
    objects_before: tuple[Any, ...]
    page_views_before: tuple[Any, ...]
    timeline_before: tuple[Any, ...]
    selection_before: dict[str, Any]
    visibility_before: tuple[tuple[Any, bool], ...]
    next_balloon_index_before: int


def _error(
    message: str,
    code: str,
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    drawing_dimension_error(message, code, repair=repair)


def _same(first: Any, second: Any) -> bool:
    try:
        return math.isclose(
            float(first),
            float(second),
            rel_tol=1.0e-10,
            abs_tol=1.0e-9,
        )
    except (TypeError, ValueError):
        return False


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        _error(
            "Drawing balloon text must contain 1 to 512 characters and cannot be blank.",
            "NATIVE_DRAWING_BALLOON_PARAMETERS_INVALID",
        )
    return value


def _bounded_number(
    value: Any,
    noun: str,
    *,
    minimum: float,
    maximum: float,
    exclusive_minimum: bool = False,
) -> float:
    result = finite_drawing_coordinate(
        value,
        noun,
        family="balloon style",
        limit_mm=max(abs(minimum), abs(maximum)),
        error_code="NATIVE_DRAWING_BALLOON_PARAMETERS_INVALID",
    )
    below = result <= minimum if exclusive_minimum else result < minimum
    if below or result > maximum:
        qualifier = "greater than" if exclusive_minimum else "at least"
        _error(
            f"Drawing balloon style {noun} must be {qualifier} {minimum:g} "
            f"and no more than {maximum:g}.",
            "NATIVE_DRAWING_BALLOON_PARAMETERS_INVALID",
        )
    return round(result, 12)


def _color(value: Any) -> dict[str, int]:
    exact = exact_drawing_mapping(
        value,
        _COLOR_FIELDS,
        "style color_rgb",
        family="balloon",
        error_code="NATIVE_DRAWING_BALLOON_PARAMETERS_INVALID",
    )
    result = {}
    for name in sorted(_COLOR_FIELDS):
        channel = exact[name]
        if isinstance(channel, bool) or not isinstance(channel, int) or not 0 <= channel <= 255:
            _error(
                f"Drawing balloon color channel {name} must be an integer from 0 to 255.",
                "NATIVE_DRAWING_BALLOON_PARAMETERS_INVALID",
            )
        result[name] = int(channel)
    return result


def _style(value: Any) -> dict[str, Any]:
    exact = exact_drawing_mapping(
        value,
        _STYLE_FIELDS,
        "complete editable style",
        family="balloon",
        error_code="NATIVE_DRAWING_BALLOON_PARAMETERS_INVALID",
    )
    bubble_shape = exact["bubble_shape"]
    if bubble_shape not in DRAWING_BALLOON_SHAPES:
        _error(
            "Drawing balloon bubble_shape is not one of the published values.",
            "NATIVE_DRAWING_BALLOON_PARAMETERS_INVALID",
            repair={"allowed_values": list(DRAWING_BALLOON_SHAPES)},
        )
    leader_end = exact["leader_end"]
    if leader_end not in DRAWING_BALLOON_LEADER_ENDS:
        _error(
            "Drawing balloon leader_end is not one of the published values.",
            "NATIVE_DRAWING_BALLOON_PARAMETERS_INVALID",
            repair={"allowed_values": list(DRAWING_BALLOON_LEADER_ENDS)},
        )
    line_visible = exact["line_visible"]
    if not isinstance(line_visible, bool):
        _error(
            "Drawing balloon line_visible must be true or false.",
            "NATIVE_DRAWING_BALLOON_PARAMETERS_INVALID",
        )
    return {
        "bubble_shape": str(bubble_shape),
        "leader_end": str(leader_end),
        "bubble_scale": _bounded_number(
            exact["bubble_scale"],
            "bubble_scale",
            minimum=0.0,
            maximum=1000.0,
            exclusive_minimum=True,
        ),
        "leader_end_scale": _bounded_number(
            exact["leader_end_scale"],
            "leader_end_scale",
            minimum=0.0,
            maximum=1000.0,
            exclusive_minimum=True,
        ),
        "kink_length_mm": _bounded_number(
            exact["kink_length_mm"],
            "kink_length_mm",
            minimum=-1000.0,
            maximum=1000.0,
        ),
        "font_size_mm": _bounded_number(
            exact["font_size_mm"],
            "font_size_mm",
            minimum=0.0,
            maximum=1000.0,
        ),
        "line_width_mm": _bounded_number(
            exact["line_width_mm"],
            "line_width_mm",
            minimum=0.0,
            maximum=100.0,
        ),
        "line_visible": line_visible,
        "color_rgb": _color(exact["color_rgb"]),
    }


def _offset(value: Any) -> tuple[float, float]:
    exact = exact_drawing_mapping(
        value,
        frozenset({"x_mm", "y_mm"}),
        "bubble offset",
        family="balloon",
        error_code="NATIVE_DRAWING_BALLOON_PARAMETERS_INVALID",
    )
    return (
        finite_drawing_coordinate(
            exact["x_mm"],
            "bubble offset x_mm",
            family="balloon",
            limit_mm=1000.0,
            error_code="NATIVE_DRAWING_BALLOON_PARAMETERS_INVALID",
        ),
        finite_drawing_coordinate(
            exact["y_mm"],
            "bubble offset y_mm",
            family="balloon",
            limit_mm=1000.0,
            error_code="NATIVE_DRAWING_BALLOON_PARAMETERS_INVALID",
        ),
    )


def _spec(operation: str, values: Mapping[str, Any]) -> DrawingBalloonEditSpec:
    if operation == "set_text":
        return DrawingBalloonEditSpec(operation=operation, text=_text(values["text"]))
    if operation == "set_style":
        return DrawingBalloonEditSpec(operation=operation, style=_style(values["style"]))
    if operation == "move_bubble":
        x_mm, y_mm = _offset(values["bubble_offset_in_view_mm"])
        return DrawingBalloonEditSpec(
            operation=operation,
            offset_x_mm=x_mm,
            offset_y_mm=y_mm,
        )
    raise ValueError("operation is not a Drawing balloon edit operation")


def _resolve_balloon(
    document: Any,
    target: Any,
) -> tuple[Any, dict[str, Any]]:
    exact = exact_drawing_mapping(
        target,
        frozenset({"object_name", "expected_state_sha256"}),
        "Balloon target",
        family="balloon",
        error_code="NATIVE_DRAWING_BALLOON_PARAMETERS_INVALID",
    )
    balloon = resolve_object(
        document,
        {"document_uid": str(document.Uid), "object_name": exact["object_name"]},
        expected_types=("TechDraw::DrawViewBalloon",),
    )
    try:
        state = drawing_balloon_state(balloon)
    except (TypeError, ValueError) as exc:
        _error(
            f"The exact Drawing Balloon is not editable: {str(exc).strip()}",
            "NATIVE_DRAWING_BALLOON_STATE_INVALID",
        )
    if str(exact["expected_state_sha256"]) != state["state_sha256"]:
        _error(
            "The exact Drawing Balloon changed after it was inspected.",
            "NATIVE_DRAWING_BALLOON_STALE",
            repair={"current_state_sha256": state["state_sha256"]},
        )
    if not state["valid"] or not state["timeline_usable"]:
        _error(
            "The exact Drawing Balloon is invalid or unavailable at the current History position.",
            "NATIVE_DRAWING_BALLOON_TARGET_UNAVAILABLE",
        )
    return balloon, state


def _assert_change(spec: DrawingBalloonEditSpec, state: Mapping[str, Any]) -> None:
    if spec.operation == "set_text" and state["text"] == spec.text:
        _error(
            "The Drawing Balloon already has the requested text.",
            "NATIVE_DRAWING_NO_CHANGE",
        )
    if spec.operation == "set_style" and state["style"] == {
        **state["style"],
        **dict(spec.style or {}),
    }:
        _error(
            "The Drawing Balloon already has the requested editable style.",
            "NATIVE_DRAWING_NO_CHANGE",
        )
    offset = state["bubble_offset_in_view_mm"]
    if (
        spec.operation == "move_bubble"
        and _same(offset["x_mm"], spec.offset_x_mm)
        and _same(offset["y_mm"], spec.offset_y_mm)
    ):
        _error(
            "The Drawing Balloon already has the requested bubble offset.",
            "NATIVE_DRAWING_NO_CHANGE",
        )


def prepare_drawing_balloon_edit(
    document: Any,
    *,
    operation: str,
    values: Mapping[str, Any],
) -> PreparedDrawingBalloonEdit:
    spec = _spec(operation, values)
    balloon, state = _resolve_balloon(document, values["balloon"])
    _assert_change(spec, state)
    page = balloon.findParentPage()
    source_view = balloon.SourceView
    return PreparedDrawingBalloonEdit(
        balloon=balloon,
        page=page,
        source_view=source_view,
        spec=spec,
        state_before=state,
        page_state_before=drawing_page_state(page),
        view_state_before=drawing_view_state(source_view),
        projection_state_before=drawing_projected_geometry_state(source_view),
        objects_before=tuple(document.Objects),
        page_views_before=tuple(page.Views or ()),
        timeline_before=drawing_timeline_operations(document),
        selection_before=drawing_selection_state(document),
        visibility_before=drawing_visibility_state(document),
        next_balloon_index_before=int(page.NextBalloonIndex),
    )


def mutate_drawing_balloon_edit(
    _document: Any,
    *,
    prepared: PreparedDrawingBalloonEdit,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedDrawingBalloonEdit):
        raise TypeError("prepared must be a PreparedDrawingBalloonEdit")
    balloon = prepared.balloon
    spec = prepared.spec
    if spec.operation == "set_text":
        balloon.Text = spec.text
    elif spec.operation == "set_style":
        style = dict(spec.style or {})
        balloon.BubbleShape = style["bubble_shape"]
        balloon.EndType = style["leader_end"]
        balloon.ShapeScale = style["bubble_scale"]
        balloon.EndTypeScale = style["leader_end_scale"]
        balloon.KinkLength = style["kink_length_mm"]
        balloon.ViewObject.Fontsize = style["font_size_mm"]
        balloon.ViewObject.LineWidth = style["line_width_mm"]
        balloon.ViewObject.LineVisible = style["line_visible"]
        color = style["color_rgb"]
        balloon.ViewObject.Color = (
            color["red"] / 255.0,
            color["green"] / 255.0,
            color["blue"] / 255.0,
        )
    elif spec.operation == "move_bubble":
        scale = float(prepared.source_view.Scale)
        origin = prepared.state_before["anchor_in_source_mm"]
        balloon.X = float(origin["x_mm"]) + float(spec.offset_x_mm) / scale
        balloon.Y = float(origin["y_mm"]) + float(spec.offset_y_mm) / scale
    else:
        raise NativeMutationError(
            "NATIVE_DRAWING_BALLOON_OPERATION_INVALID",
            "The prepared Drawing Balloon edit operation is unsupported.",
        )
    return NativeMutationDraft(
        value={"prepared": prepared},
        recompute_targets=(balloon, prepared.source_view, prepared.page),
        changed=(object_identity(balloon),),
    )


def _postcondition_error(message: str) -> None:
    raise NativeMutationError(
        "NATIVE_DRAWING_BALLOON_POSTCONDITION_FAILED",
        message,
    )


def _assert_boundary(prepared: PreparedDrawingBalloonEdit) -> None:
    document = prepared.balloon.Document
    if (
        tuple(map(drawing_object_key, document.Objects))
        != tuple(map(drawing_object_key, prepared.objects_before))
        or tuple(map(drawing_object_key, tuple(prepared.page.Views or ())))
        != tuple(map(drawing_object_key, prepared.page_views_before))
        or tuple(map(drawing_object_key, drawing_timeline_operations(document)))
        != tuple(map(drawing_object_key, prepared.timeline_before))
    ):
        _postcondition_error(
            "Balloon editing changed document objects, page membership, or History."
        )
    if int(prepared.page.NextBalloonIndex) != prepared.next_balloon_index_before:
        _postcondition_error("Balloon editing changed the page auto-number sequence.")
    if (
        drawing_view_state(prepared.source_view)["state_sha256"]
        != prepared.view_state_before["state_sha256"]
        or drawing_projected_geometry_state(prepared.source_view)[
            "projection_state_sha256"
        ]
        != prepared.projection_state_before["projection_state_sha256"]
    ):
        _postcondition_error("Balloon editing changed its source projection.")
    if drawing_selection_state(document) != prepared.selection_before:
        _postcondition_error("Balloon editing changed the human selection.")
    visibility = tuple(
        (obj, bool(obj.ViewObject.Visibility))
        for obj, _visible in prepared.visibility_before
    )
    if visibility != prepared.visibility_before:
        _postcondition_error("Balloon editing changed object visibility.")


def _unchanged_fields(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    ignored: frozenset[str],
) -> list[str]:
    return [
        name
        for name in before
        if name not in ignored and after.get(name) != before[name]
    ]


def _verify_requested_change(
    prepared: PreparedDrawingBalloonEdit,
    state: Mapping[str, Any],
) -> list[str]:
    before = prepared.state_before
    spec = prepared.spec
    mismatches: list[str] = []
    if spec.operation == "set_text":
        mismatches.extend(
            _unchanged_fields(
                before,
                state,
                frozenset(
                    {
                        "text",
                        "text_sha256",
                        "text_characters",
                        "text_truncated",
                        "state_messages",
                        "state_sha256",
                    }
                ),
            )
        )
        expected_text = str(spec.text)
        if state["text"] != expected_text:
            mismatches.append("text")
        if state["text_characters"] != len(expected_text):
            mismatches.append("text_characters")
        expected_hash = hashlib.sha256(expected_text.encode("utf-8")).hexdigest()
        if state["text_sha256"] != expected_hash:
            mismatches.append("text_sha256")
    elif spec.operation == "set_style":
        mismatches.extend(
            _unchanged_fields(
                before,
                state,
                frozenset({"style", "state_messages", "state_sha256"}),
            )
        )
        expected_style = {**before["style"], **dict(spec.style or {})}
        if state["style"] != expected_style:
            mismatches.append("style")
    elif spec.operation == "move_bubble":
        mismatches.extend(
            _unchanged_fields(
                before,
                state,
                frozenset(
                    {
                        "bubble_in_source_mm",
                        "bubble_offset_in_view_mm",
                        "state_messages",
                        "state_sha256",
                    }
                ),
            )
        )
        offset = state["bubble_offset_in_view_mm"]
        if not _same(offset["x_mm"], spec.offset_x_mm):
            mismatches.append("bubble_offset_x")
        if not _same(offset["y_mm"], spec.offset_y_mm):
            mismatches.append("bubble_offset_y")
    if state["state_sha256"] == before["state_sha256"]:
        mismatches.append("state_sha256")
    return sorted(set(mismatches))


def _verify_drawing_balloon_edit(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedDrawingBalloonEdit = draft.value["prepared"]
    if prepared.balloon.Document is not document:
        _postcondition_error("The edited Balloon left its exact document.")
    _assert_boundary(prepared)
    state = drawing_balloon_state(prepared.balloon)
    mismatches = _verify_requested_change(prepared, state)
    if mismatches:
        _postcondition_error(
            "The Balloon edit changed or failed to retain these exact fields: "
            + ", ".join(mismatches)
            + "."
        )
    page_state = drawing_page_state(prepared.page)
    if page_state["view_count"] != prepared.page_state_before["view_count"]:
        _postcondition_error("Balloon editing changed the Drawing page membership.")
    return {"operation": prepared.spec.operation, "balloon": state}


def verify_drawing_balloon_edit(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    try:
        return _verify_drawing_balloon_edit(document, draft)
    except NativeMutationError:
        raise
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_BALLOON_POSTCONDITION_FAILED",
            "The Drawing Balloon edit could not be verified exactly: "
            f"{str(exc).strip()}",
        ) from exc
