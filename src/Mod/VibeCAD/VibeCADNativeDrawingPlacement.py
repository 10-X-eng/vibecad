# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic explicit placement of Drawing views and dimension labels."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Callable, Mapping

from VibeCADNativeDrawingDimensionState import is_drawing_dimension
from VibeCADNativeDrawingDimensionSupport import (
    drawing_object_key,
    drawing_position_within_page_bounds,
    drawing_selection_state,
    drawing_timeline_operations,
    drawing_visibility_state,
    exact_drawing_mapping,
)
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingPlacementState import (
    NativeDrawingPlacementStateError,
    drawing_dimension_label_placement_state,
    drawing_dimension_view_origin_on_page,
    drawing_note_placement_state,
    drawing_view_placement_owner,
    drawing_view_placement_state,
    drawing_view_position_on_page,
    is_positionable_drawing_note,
    is_positionable_drawing_view,
)
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeTargets import object_identity, resolve_object


_PAGE_FIELDS = frozenset({"object_name", "expected_state_sha256"})
_TARGET_FIELDS = frozenset(
    {"object_name", "expected_placement_state_sha256", "position"}
)
_POSITION_FIELDS = frozenset({"x_mm", "y_mm"})


@dataclass(frozen=True, slots=True)
class PreparedDrawingPlacementItem:
    obj: Any
    state_before: dict[str, Any]
    position: dict[str, float]
    changed: bool


@dataclass(frozen=True, slots=True)
class PreparedDrawingPlacement:
    operation: str
    page: Any
    page_state_before: dict[str, Any]
    items: tuple[PreparedDrawingPlacementItem, ...]
    inventory_before: tuple[dict[str, Any], ...]
    objects_before: tuple[Any, ...]
    page_views_before: tuple[Any, ...]
    timeline_before: tuple[Any, ...]
    selection_before: dict[str, Any]
    visibility_before: tuple[tuple[Any, bool], ...]


def _error(
    message: str,
    code: str,
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    raise NativeDrawingError(message, error_code=code, repair=repair)


def _mapping(value: Any, fields: frozenset[str], noun: str) -> Mapping[str, Any]:
    return exact_drawing_mapping(
        value,
        fields,
        noun,
        family="placement",
        error_code="NATIVE_DRAWING_PLACEMENT_PARAMETERS_INVALID",
    )


def _position(value: Any, noun: str) -> dict[str, float]:
    exact = _mapping(value, _POSITION_FIELDS, noun)
    result = {}
    for field in ("x_mm", "y_mm"):
        raw = exact[field]
        if type(raw) not in {int, float}:
            _error(
                f"Drawing {noun} {field} must be numeric.",
                "NATIVE_DRAWING_PLACEMENT_PARAMETERS_INVALID",
            )
        number = float(raw)
        if not math.isfinite(number) or not -10_000.0 <= number <= 10_000.0:
            _error(
                f"Drawing {noun} {field} must be between -10000 and 10000 mm.",
                "NATIVE_DRAWING_PLACEMENT_PARAMETERS_INVALID",
            )
        result[field] = 0.0 if number == 0.0 else number
    return result


def _resolve_page(document: Any, value: Any) -> tuple[Any, dict[str, Any]]:
    target = _mapping(value, _PAGE_FIELDS, "page target")
    page = resolve_object(
        document,
        {"document_uid": str(document.Uid), "object_name": target["object_name"]},
        expected_types=("TechDraw::DrawPage",),
    )
    state = drawing_page_state(page)
    if str(target["expected_state_sha256"]) != state["state_sha256"]:
        _error(
            "The exact Drawing page changed after it was inspected.",
            "NATIVE_DRAWING_PAGE_STALE",
            repair={"current_state_sha256": state["state_sha256"]},
        )
    checker = getattr(document, "isObjectUsableAtCurrentTimelinePosition", None)
    if callable(checker) and not bool(checker(page)):
        _error(
            "The exact Drawing page is unavailable at the current History position.",
            "NATIVE_DRAWING_HISTORY_TARGET_UNAVAILABLE",
        )
    return page, state


def _inventory(
    page: Any,
    *,
    operation: str,
) -> tuple[dict[str, Any], ...]:
    if operation == "place_views":
        return tuple(
            drawing_view_placement_state(obj)
            for obj in tuple(page.Views or ())
            if is_positionable_drawing_view(obj)
        )
    if operation == "place_dimension_labels":
        return tuple(
            drawing_dimension_label_placement_state(obj)
            for obj in tuple(page.Views or ())
            if is_drawing_dimension(obj)
        )
    if operation == "place_notes":
        return tuple(
            drawing_note_placement_state(obj)
            for obj in tuple(page.Views or ())
            if is_positionable_drawing_note(obj)
        )
    raise ValueError("operation is not a Drawing placement operation")


def _resolve_items(
    document: Any,
    page: Any,
    *,
    operation: str,
    values: Any,
    inventory: tuple[dict[str, Any], ...],
) -> tuple[PreparedDrawingPlacementItem, ...]:
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 64:
        _error(
            "Drawing placement requires 1 to 64 items.",
            "NATIVE_DRAWING_PLACEMENT_PARAMETERS_INVALID",
        )
    states = {state["object_name"]: state for state in inventory}
    if operation == "place_views":
        position_field = "position_on_page_mm"
        expected_types = ("TechDraw::DrawView",)
        state_reader: Callable[[Any], dict[str, Any]] = drawing_view_placement_state
    elif operation == "place_dimension_labels":
        position_field = "label_position_on_page_mm"
        expected_types = ("TechDraw::DrawViewDimension",)
        state_reader = drawing_dimension_label_placement_state
    else:
        position_field = "position_on_page_mm"
        expected_types = ("TechDraw::DrawRichAnno",)
        state_reader = drawing_note_placement_state
    seen: set[str] = set()
    prepared_by_target: dict[str, PreparedDrawingPlacementItem] = {}
    result = []
    for raw in values:
        if not isinstance(raw, Mapping):
            _error(
                "Every Drawing placement item must be an object.",
                "NATIVE_DRAWING_PLACEMENT_PARAMETERS_INVALID",
            )
        normalized = dict(raw)
        if position_field not in normalized:
            _error(
                f"Every Drawing placement item requires {position_field}.",
                "NATIVE_DRAWING_PLACEMENT_PARAMETERS_INVALID",
            )
        normalized["position"] = normalized.pop(position_field)
        exact = _mapping(normalized, _TARGET_FIELDS, "item")
        name = str(exact["object_name"] or "")
        if name in seen:
            _error(
                "Each Drawing item may appear only once in one placement operation.",
                "NATIVE_DRAWING_PLACEMENT_TARGETS_INVALID",
            )
        seen.add(name)
        requested_position = _position(exact["position"], position_field)
        requested_position = drawing_position_within_page_bounds(
            page,
            requested_position,
            noun=(
                "dimension label"
                if operation == "place_dimension_labels"
                else "item"
            ),
            error_code="NATIVE_DRAWING_PLACEMENT_OUTSIDE_DRAWING_AREA",
        )
        requested_obj = resolve_object(
            document,
            {"document_uid": str(document.Uid), "object_name": name},
            expected_types=expected_types,
        )
        if operation == "place_views":
            try:
                obj = drawing_view_placement_owner(requested_obj)
                requested_current = drawing_view_position_on_page(requested_obj)
            except (
                AttributeError,
                NativeDrawingPlacementStateError,
                RuntimeError,
                TypeError,
                ValueError,
            ):
                _error(
                    f"Drawing view {name!r} has no movable placement owner.",
                    "NATIVE_DRAWING_PLACEMENT_TARGET_INVALID",
                )
            target_name = str(getattr(obj, "Name", "") or "")
            state = states.get(target_name)
            if state is not None:
                owner_current = state[position_field]
                position = {
                    field: float(owner_current[field])
                    + requested_position[field]
                    - float(requested_current[field])
                    for field in ("x_mm", "y_mm")
                }
                position = _position(position, "resolved view placement")
        else:
            obj = requested_obj
            target_name = name
            state = states.get(target_name)
            if operation == "place_dimension_labels":
                try:
                    origin = drawing_dimension_view_origin_on_page(obj)
                except (
                    AttributeError,
                    NativeDrawingPlacementStateError,
                    RuntimeError,
                    TypeError,
                    ValueError,
                ):
                    _error(
                        f"Drawing dimension {name!r} has no exact parent-view placement.",
                        "NATIVE_DRAWING_PLACEMENT_TARGET_INVALID",
                    )
                position = {
                    field: requested_position[field]
                    - origin[field]
                    for field in ("x_mm", "y_mm")
                }
                position = _position(position, "resolved dimension-label placement")
            else:
                position = requested_position
        if state is None:
            _error(
                f"Drawing item {name!r} is not a placeable target on the exact page.",
                "NATIVE_DRAWING_PLACEMENT_PAGE_MISMATCH",
            )
        if str(exact["expected_placement_state_sha256"]) != state[
            "placement_state_sha256"
        ]:
            _error(
                f"Drawing item {name!r} changed after it was inspected.",
                "NATIVE_DRAWING_PLACEMENT_TARGET_STALE",
                repair={"object_name": name},
            )
        if not state["valid"] or not state["timeline_usable"]:
            _error(
                f"Drawing item {name!r} is invalid or unavailable in History.",
                "NATIVE_DRAWING_PLACEMENT_TARGET_INVALID",
            )
        if operation in {"place_views", "place_notes"} and state["locked"]:
            _error(
                f"Drawing view {name!r} has its position locked.",
                "NATIVE_DRAWING_VIEW_POSITION_LOCKED",
                repair={"object_name": name, "tool": "drawing.set_view_locks"},
            )
        if obj not in tuple(page.Views or ()):
            _error(
                f"Drawing item {name!r} is not a top-level member of the exact page.",
                "NATIVE_DRAWING_PLACEMENT_PAGE_MISMATCH",
            )
        existing = prepared_by_target.get(target_name)
        if existing is not None:
            if any(
                not math.isclose(
                    existing.position[field],
                    position[field],
                    rel_tol=0.0,
                    abs_tol=1.0e-8,
                )
                for field in ("x_mm", "y_mm")
            ):
                _error(
                    "Projected child positions require contradictory translations "
                    f"of projection group {target_name!r}.",
                    "NATIVE_DRAWING_PLACEMENT_TARGETS_INVALID",
                )
            continue
        state_position_field = (
            "label_position_in_view_mm"
            if operation == "place_dimension_labels"
            else position_field
        )
        current = state[state_position_field]
        changed = any(
            not math.isclose(
                float(current[field]),
                position[field],
                rel_tol=0.0,
                abs_tol=1.0e-9,
            )
            for field in ("x_mm", "y_mm")
        )
        item = PreparedDrawingPlacementItem(
            obj=obj,
            state_before=state_reader(obj),
            position=position,
            changed=changed,
        )
        prepared_by_target[target_name] = item
        result.append(item)
    if not any(item.changed for item in result):
        _error(
            "Every Drawing item already has the requested placement.",
            "NATIVE_DRAWING_NO_CHANGE",
        )
    return tuple(result)


def prepare_drawing_placement(
    document: Any,
    *,
    operation: str,
    values: Mapping[str, Any],
) -> PreparedDrawingPlacement:
    if operation not in {"place_views", "place_dimension_labels", "place_notes"}:
        raise ValueError("operation is not a Drawing placement operation")
    page, page_state = _resolve_page(document, values["page"])
    try:
        inventory = _inventory(page, operation=operation)
    except (
        AttributeError,
        NativeDrawingPlacementStateError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as exc:
        _error(
            f"Drawing placement state is unavailable: {str(exc).strip()}",
            "NATIVE_DRAWING_PLACEMENT_STATE_INVALID",
        )
    item_field = {
        "place_views": "views",
        "place_dimension_labels": "dimensions",
        "place_notes": "notes",
    }[operation]
    items = _resolve_items(
        document,
        page,
        operation=operation,
        values=values[item_field],
        inventory=inventory,
    )
    return PreparedDrawingPlacement(
        operation=operation,
        page=page,
        page_state_before=page_state,
        items=items,
        inventory_before=inventory,
        objects_before=tuple(document.Objects),
        page_views_before=tuple(page.Views or ()),
        timeline_before=drawing_timeline_operations(document),
        selection_before=drawing_selection_state(document),
        visibility_before=drawing_visibility_state(document),
    )


def mutate_drawing_placement(
    _document: Any,
    *,
    prepared: PreparedDrawingPlacement,
) -> NativeMutationDraft:
    for item in prepared.items:
        if not item.changed:
            continue
        item.obj.X = item.position["x_mm"]
        item.obj.Y = item.position["y_mm"]
    changed = tuple(
        object_identity(item.obj) for item in prepared.items if item.changed
    )
    recompute = tuple(item.obj for item in prepared.items if item.changed)
    return NativeMutationDraft(
        value={"prepared": prepared},
        recompute_targets=recompute + (prepared.page,),
        changed=changed,
    )


def _without_position(state: Mapping[str, Any], position_field: str) -> dict[str, Any]:
    return {
        key: value
        for key, value in state.items()
        if key not in {position_field, "placement_state_sha256"}
    }


def _postcondition(message: str) -> None:
    raise NativeMutationError(
        "NATIVE_DRAWING_PLACEMENT_POSTCONDITION_FAILED",
        message,
    )


def verify_drawing_placement(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedDrawingPlacement = draft.value["prepared"]
    if (
        tuple(map(drawing_object_key, document.Objects))
        != tuple(map(drawing_object_key, prepared.objects_before))
        or tuple(map(drawing_object_key, tuple(prepared.page.Views or ())))
        != tuple(map(drawing_object_key, prepared.page_views_before))
        or tuple(map(drawing_object_key, drawing_timeline_operations(document)))
        != tuple(map(drawing_object_key, prepared.timeline_before))
    ):
        _postcondition("Drawing placement altered objects, page membership, or History.")
    if drawing_selection_state(document) != prepared.selection_before:
        _postcondition("Drawing placement altered the human selection.")
    if drawing_visibility_state(document) != prepared.visibility_before:
        _postcondition("Drawing placement altered object visibility.")
    if drawing_page_state(prepared.page) != prepared.page_state_before:
        _postcondition("Drawing placement altered the page definition.")

    position_field = (
        "label_position_in_view_mm"
        if prepared.operation == "place_dimension_labels"
        else "position_on_page_mm"
    )
    try:
        inventory = _inventory(prepared.page, operation=prepared.operation)
    except Exception as exc:
        _postcondition(
            f"Drawing placement state could not be read back: {str(exc).strip()}"
        )
    before_by_name = {
        state["object_name"]: state for state in prepared.inventory_before
    }
    after_by_name = {state["object_name"]: state for state in inventory}
    if tuple(before_by_name) != tuple(after_by_name):
        _postcondition("Drawing placement changed placeable item identities or order.")
    requested = {str(item.obj.Name): item for item in prepared.items}
    returned = []
    for name, before in before_by_name.items():
        after = after_by_name[name]
        item = requested.get(name)
        if item is None:
            if after != before:
                _postcondition(f"Non-target Drawing item {name!r} changed unexpectedly.")
            continue
        if _without_position(before, position_field) != _without_position(
            after, position_field
        ):
            _postcondition(f"Drawing item {name!r} changed outside placement.")
        actual = after[position_field]
        if any(
            not math.isclose(
                float(actual[field]),
                item.position[field],
                rel_tol=0.0,
                abs_tol=1.0e-8,
            )
            for field in ("x_mm", "y_mm")
        ):
            _postcondition(f"Drawing item {name!r} did not retain its placement.")
        if item.changed and after["placement_state_sha256"] == before[
            "placement_state_sha256"
        ]:
            _postcondition(f"Drawing item {name!r} produced no exact state change.")
        if prepared.operation == "place_dimension_labels":
            origin = drawing_dimension_view_origin_on_page(item.obj)
            returned_position = {
                field: round(float(origin[field]) + float(actual[field]), 9)
                for field in ("x_mm", "y_mm")
            }
            returned.append(
                {
                    "object_name": name,
                    "label_position_on_page_mm": returned_position,
                }
            )
        else:
            returned.append({"object_name": name, position_field: dict(actual)})
    return {
        "operation": prepared.operation,
        "page": {"object_name": str(prepared.page.Name)},
        "changed_count": sum(item.changed for item in prepared.items),
        "items": returned,
    }


__all__ = [
    "PreparedDrawingPlacement",
    "mutate_drawing_placement",
    "prepare_drawing_placement",
    "verify_drawing_placement",
]
