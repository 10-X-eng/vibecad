# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact transactional creation of projected Drawing balloons."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeDrawingBalloonState import drawing_balloon_state
from VibeCADNativeDrawingDimensionSupport import (
    PreparedDrawingDimensionTarget,
    drawing_dimension_error,
    drawing_object_key,
    drawing_selection_state,
    drawing_timeline_operations,
    exact_drawing_mapping,
    finite_drawing_coordinate,
    matches_drawing_document_label,
    prepare_drawing_dimension_target,
)
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewState import drawing_view_state
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeTargets import object_identity


@dataclass(frozen=True, slots=True)
class BalloonSpec:
    operation: str
    label: str
    text: str
    anchor_name: str
    offset_x_mm: float
    offset_y_mm: float


@dataclass(frozen=True, slots=True)
class PreparedDrawingBalloon:
    target: PreparedDrawingDimensionTarget
    spec: BalloonSpec
    host_validation: dict[str, Any]
    next_balloon_index_before: int


def _error(
    message: str,
    code: str,
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    drawing_dimension_error(message, code, repair=repair)


def _spec(operation: str, values: Mapping[str, Any]) -> BalloonSpec:
    if operation != "create":
        raise ValueError("operation is not a Drawing balloon operation")
    label = str(values["label"] or "")
    if label != label.strip() or not 1 <= len(label) <= 160:
        _error(
            "A Drawing balloon label must contain 1 to 160 non-padding characters.",
            "NATIVE_DRAWING_BALLOON_PARAMETERS_INVALID",
        )
    text = str(values["text"] or "")
    if not text.strip() or len(text) > 512:
        _error(
            "Drawing balloon text must contain 1 to 512 characters and cannot be blank.",
            "NATIVE_DRAWING_BALLOON_PARAMETERS_INVALID",
        )
    anchor = exact_drawing_mapping(
        values["anchor"],
        frozenset({"subelement"}),
        "anchor",
        family="balloon",
        error_code="NATIVE_DRAWING_BALLOON_PARAMETERS_INVALID",
    )
    anchor_name = str(anchor["subelement"] or "")
    if not anchor_name.startswith(("Edge", "Vertex")):
        _error(
            "A Drawing balloon anchor must be one projected EdgeN or VertexN.",
            "NATIVE_DRAWING_BALLOON_REFERENCE_TYPE_INVALID",
            repair={"accepted_reference_types": ["edge", "vertex"]},
        )
    offset = exact_drawing_mapping(
        values["bubble_offset_in_view_mm"],
        frozenset({"x_mm", "y_mm"}),
        "bubble offset",
        family="balloon",
        error_code="NATIVE_DRAWING_BALLOON_PARAMETERS_INVALID",
    )
    return BalloonSpec(
        operation=operation,
        label=label,
        text=text,
        anchor_name=anchor_name,
        offset_x_mm=finite_drawing_coordinate(
            offset["x_mm"],
            "bubble offset x_mm",
            family="balloon",
            limit_mm=1000.0,
            error_code="NATIVE_DRAWING_BALLOON_PARAMETERS_INVALID",
        ),
        offset_y_mm=finite_drawing_coordinate(
            offset["y_mm"],
            "bubble offset y_mm",
            family="balloon",
            limit_mm=1000.0,
            error_code="NATIVE_DRAWING_BALLOON_PARAMETERS_INVALID",
        ),
    )


def _point(value: Any, noun: str) -> dict[str, float]:
    exact = exact_drawing_mapping(
        value,
        frozenset({"x_mm", "y_mm"}),
        noun,
        family="balloon validation",
        error_code="NATIVE_DRAWING_BALLOON_RUNTIME_UNAVAILABLE",
    )
    return {
        "x_mm": finite_drawing_coordinate(
            exact["x_mm"],
            f"{noun} x_mm",
            family="balloon validation",
            error_code="NATIVE_DRAWING_BALLOON_RUNTIME_UNAVAILABLE",
        ),
        "y_mm": finite_drawing_coordinate(
            exact["y_mm"],
            f"{noun} y_mm",
            family="balloon validation",
            error_code="NATIVE_DRAWING_BALLOON_RUNTIME_UNAVAILABLE",
        ),
    }


def _same(first: float, second: float) -> bool:
    return math.isclose(first, second, rel_tol=1.0e-10, abs_tol=1.0e-9)


def _validate_host(
    view: Any,
    spec: BalloonSpec,
    element_state: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        import TechDrawGui

        validator = getattr(TechDrawGui, "validateProjectedBalloonAnchor", None)
        if not callable(validator):
            _error(
                "The installed TechDraw runtime cannot validate projected balloon anchors.",
                "NATIVE_DRAWING_BALLOON_RUNTIME_UNAVAILABLE",
            )
        raw = validator(view, spec.anchor_name)
    except NativeDrawingError:
        raise
    except Exception as exc:
        _error(
            f"TechDraw rejected projected balloon anchor {spec.anchor_name!r}: "
            f"{str(exc).strip()}",
            "NATIVE_DRAWING_BALLOON_REFERENCE_INVALID",
            repair={
                "accepted_reference_types": ["edge", "vertex"],
                "tool": "drawing.projected_geometry",
            },
        )
    if not isinstance(raw, Mapping) or frozenset(raw) != frozenset(
        {"element_type", "point_in_view_mm", "point_in_source_mm"}
    ):
        _error(
            "TechDraw returned a malformed projected balloon validation result.",
            "NATIVE_DRAWING_BALLOON_RUNTIME_UNAVAILABLE",
        )
    element_type = str(raw["element_type"] or "").casefold()
    point_in_view = _point(raw["point_in_view_mm"], "anchor point in view")
    point_in_source = _point(raw["point_in_source_mm"], "anchor point in source")
    expected_point = (
        element_state["midpoint_in_view_mm"]
        if element_type == "edge"
        else element_state["point_in_view_mm"]
        if element_type == "vertex"
        else None
    )
    scale = float(getattr(view, "Scale", 0.0))
    if (
        expected_point is None
        or element_type != element_state["element_type"]
        or not math.isfinite(scale)
        or scale <= 0.0
        or not _same(point_in_view["x_mm"], float(expected_point["x_mm"]))
        or not _same(point_in_view["y_mm"], float(expected_point["y_mm"]))
        or not _same(
            point_in_source["x_mm"],
            point_in_view["x_mm"] / scale,
        )
        or not _same(
            point_in_source["y_mm"],
            point_in_view["y_mm"] / scale,
        )
    ):
        _error(
            "TechDraw's projected balloon anchor does not match the inspected geometry.",
            "NATIVE_DRAWING_BALLOON_REFERENCE_INVALID",
            repair={"tool": "drawing.projected_geometry"},
        )
    return {
        "element_type": element_type,
        "point_in_view_mm": point_in_view,
        "point_in_source_mm": point_in_source,
    }


def prepare_drawing_balloon(
    document: Any,
    *,
    operation: str,
    values: Mapping[str, Any],
) -> PreparedDrawingBalloon:
    spec = _spec(operation, values)
    target = prepare_drawing_dimension_target(
        document,
        page_target=values["page"],
        view_target=values["view"],
        element_targets=(values["anchor"],),
        allowed_element_types=frozenset({"edge", "vertex"}),
        family="balloon",
        code_prefix="NATIVE_DRAWING_BALLOON",
    )
    element_state = target.element_states_before[0]
    if element_state["name"] != spec.anchor_name:
        _error(
            "The Drawing balloon anchor target is inconsistent.",
            "NATIVE_DRAWING_BALLOON_REFERENCE_INVALID",
        )
    return PreparedDrawingBalloon(
        target=target,
        spec=spec,
        host_validation=_validate_host(target.view, spec, element_state),
        next_balloon_index_before=int(target.page.NextBalloonIndex),
    )


def mutate_drawing_balloon(
    document: Any,
    *,
    prepared: PreparedDrawingBalloon,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedDrawingBalloon):
        raise TypeError("prepared must be a PreparedDrawingBalloon")
    import TechDrawGui

    spec = prepared.spec
    try:
        balloon = TechDrawGui.createProjectedBalloon(
            prepared.target.view,
            spec.anchor_name,
            spec.text,
            spec.label,
            spec.offset_x_mm,
            spec.offset_y_mm,
        )
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_BALLOON_CREATE_FAILED",
            f"TechDraw could not create the projected balloon: {str(exc).strip()}",
        ) from exc
    if (
        getattr(balloon, "Document", None) is not document
        or not balloon.isDerivedFrom("TechDraw::DrawViewBalloon")
    ):
        _error(
            "TechDraw did not create the exact projected balloon.",
            "NATIVE_DRAWING_BALLOON_CREATE_FAILED",
        )
    try:
        document.publishProvisionalTimelineOperationBlock(balloon, (), ())
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_BALLOON_HISTORY_FAILED",
            "The Drawing balloon could not be enrolled in History: "
            f"{str(exc).strip()}",
        ) from exc
    return NativeMutationDraft(
        value={"prepared": prepared, "balloon": balloon},
        recompute_targets=(balloon, prepared.target.view, prepared.target.page),
        created=(object_identity(balloon),),
        changed=(
            object_identity(prepared.target.page),
            object_identity(prepared.target.view),
        ),
    )


def _postcondition_error(message: str) -> None:
    raise NativeMutationError(
        "NATIVE_DRAWING_BALLOON_POSTCONDITION_FAILED",
        message,
    )


def _verify_drawing_balloon(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedDrawingBalloon = draft.value["prepared"]
    target = prepared.target
    spec = prepared.spec
    balloon = draft.value["balloon"]
    before_keys = {drawing_object_key(obj) for obj in target.objects_before}
    new_objects = tuple(
        obj for obj in document.Objects if drawing_object_key(obj) not in before_keys
    )
    if (
        tuple(map(drawing_object_key, new_objects)) != (drawing_object_key(balloon),)
        or tuple(map(drawing_object_key, tuple(target.page.Views or ())))
        != tuple(map(drawing_object_key, (*target.page_views_before, balloon)))
        or tuple(map(drawing_object_key, drawing_timeline_operations(document)))
        != tuple(map(drawing_object_key, (*target.timeline_before, balloon)))
    ):
        _postcondition_error(
            "Balloon creation changed objects, page membership, or History outside its result."
        )
    if int(target.page.NextBalloonIndex) != prepared.next_balloon_index_before:
        _postcondition_error(
            "Explicit Balloon text unexpectedly consumed the page auto-number sequence."
        )
    if (
        drawing_view_state(target.view)["state_sha256"]
        != target.view_state_before["state_sha256"]
        or drawing_projected_geometry_state(target.view)[
            "projection_state_sha256"
        ]
        != target.projection_state_before["projection_state_sha256"]
    ):
        _postcondition_error("Balloon creation changed its source projection.")
    if drawing_selection_state(document) != target.selection_before:
        _postcondition_error("Balloon creation changed the human selection.")
    actual_visibility = tuple(
        (obj, bool(obj.ViewObject.Visibility))
        for obj, _visible in target.visibility_before
    )
    if actual_visibility != target.visibility_before:
        _postcondition_error("Balloon creation changed existing visibility.")

    state = drawing_balloon_state(balloon)
    source_element = target.element_states_before[0]
    expected_anchor = {
        "subelement": spec.anchor_name,
        "element_type": source_element["element_type"],
        "element_state_sha256": source_element["element_state_sha256"],
        "point_in_view_mm": prepared.host_validation["point_in_view_mm"],
    }
    offset = state["bubble_offset_in_view_mm"]
    actual_anchor = state["anchor"]
    actual_point = actual_anchor["point_in_view_mm"]
    expected_point = expected_anchor["point_in_view_mm"]
    anchor_matches = (
        actual_anchor["subelement"] == expected_anchor["subelement"]
        and actual_anchor["element_type"] == expected_anchor["element_type"]
        and actual_anchor["element_state_sha256"]
        == expected_anchor["element_state_sha256"]
        and _same(float(actual_point["x_mm"]), float(expected_point["x_mm"]))
        and _same(float(actual_point["y_mm"]), float(expected_point["y_mm"]))
    )
    mismatches = [
        name
        for name, matches in (
            (
                "label",
                matches_drawing_document_label(state["label"], spec.label),
            ),
            ("text", state["text"] == spec.text),
            ("page", state["page_name"] == str(target.page.Name)),
            ("source_view", state["source_view_name"] == str(target.view.Name)),
            ("anchor", anchor_matches),
            ("bubble_offset_x", _same(float(offset["x_mm"]), spec.offset_x_mm)),
            ("bubble_offset_y", _same(float(offset["y_mm"]), spec.offset_y_mm)),
            ("timeline_role", state["timeline_role"] == "operation"),
            ("timeline_owner", not state["timeline_owner_name"]),
            ("timeline_usable", bool(state["timeline_usable"])),
            ("valid", bool(state["valid"])),
        )
        if not matches
    ]
    if mismatches:
        _postcondition_error(
            "The Balloon did not retain these exact requested fields: "
            + ", ".join(mismatches)
            + "."
        )
    page_state = drawing_page_state(target.page)
    if page_state["view_count"] != target.page_state_before["view_count"] + 1:
        _postcondition_error("The Drawing page did not retain the new Balloon.")
    return {
        "operation": spec.operation,
        "page": {
            "object_name": page_state["object_name"],
            "state_sha256": page_state["state_sha256"],
            "view_count": page_state["view_count"],
        },
        "balloon": state,
    }


def verify_drawing_balloon(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    try:
        return _verify_drawing_balloon(document, draft)
    except NativeMutationError:
        raise
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_BALLOON_POSTCONDITION_FAILED",
            "The Drawing balloon could not be verified exactly: "
            f"{str(exc).strip()}",
        ) from exc
