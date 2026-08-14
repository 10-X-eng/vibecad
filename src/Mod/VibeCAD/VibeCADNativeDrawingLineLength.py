# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact reads and transactional symmetric resizing for Drawing lines."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeDrawingDimensionSupport import (
    PreparedDrawingDimensionTarget,
    drawing_dimension_error,
    drawing_object_key,
    drawing_selection_state,
    drawing_timeline_operations,
    exact_drawing_mapping,
    prepare_drawing_dimension_target,
)
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state
from VibeCADNativeDrawingLineAttributeState import (
    drawing_line_attribute_inventory_state,
)
from VibeCADNativeDrawingLineLengthState import (
    MAX_DRAWING_LINE_DELTA_MM,
    MIN_DRAWING_LINE_DELTA_MM,
    NativeDrawingLineLengthStateError,
    drawing_line_length_inventory_state,
    drawing_line_length_page,
)
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewState import drawing_view_state
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeTargets import object_identity


_TARGET_FIELDS = frozenset(
    {"kind", "tag", "expected_line_length_state_sha256"}
)
_KINDS = frozenset({"cosmetic_edge", "centerline"})
_OPERATIONS = frozenset({"extend", "shorten"})


@dataclass(frozen=True, slots=True)
class PreparedDrawingLineLengthChange:
    operation: str
    target: PreparedDrawingDimensionTarget
    inventory_before: dict[str, Any]
    line_before: dict[str, Any]
    attribute_inventory_before: dict[str, Any]
    delta_distance_mm: float


def _error(
    message: str,
    code: str,
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    drawing_dimension_error(message, code, repair=repair)


def _target(
    document: Any,
    values: Mapping[str, Any],
) -> PreparedDrawingDimensionTarget:
    return prepare_drawing_dimension_target(
        document,
        page_target=values["page"],
        view_target=values["view"],
        element_targets=(),
        allowed_element_types=frozenset(),
        family="line length",
        code_prefix="NATIVE_DRAWING_LINE_LENGTH",
    )


def _inventory(view: Any, expected_sha256: Any) -> dict[str, Any]:
    try:
        state = drawing_line_length_inventory_state(view)
    except (AttributeError, NativeDrawingLineLengthStateError, TypeError) as exc:
        _error(
            f"The Drawing line-length inventory is unavailable: {str(exc).strip()}",
            "NATIVE_DRAWING_LINE_LENGTH_STATE_INVALID",
        )
    if str(expected_sha256) != state["inventory_state_sha256"]:
        _error(
            "The Drawing line-length inventory changed after it was inspected.",
            "NATIVE_DRAWING_LINE_LENGTH_INVENTORY_STALE",
            repair={
                "current_inventory_state_sha256": state[
                    "inventory_state_sha256"
                ]
            },
        )
    return state


def read_drawing_line_lengths(
    document: Any,
    *,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    """Read one exact bounded page of straight persistent lines."""

    target = _target(document, values)
    try:
        page = drawing_line_length_page(
            target.view,
            expected_inventory_state_sha256=str(
                values["expected_inventory_state_sha256"]
            ),
            offset=values["offset"],
            page_size=values["page_size"],
        )
    except (NativeDrawingLineLengthStateError, TypeError, ValueError) as exc:
        _error(str(exc), "NATIVE_DRAWING_LINE_LENGTH_READ_INVALID")
    return {"line_lengths": page}


def _delta(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        _error(
            "Drawing line delta_distance_mm must be numeric.",
            "NATIVE_DRAWING_LINE_LENGTH_PARAMETERS_INVALID",
        )
        raise AssertionError from exc
    if (
        not math.isfinite(result)
        or not MIN_DRAWING_LINE_DELTA_MM
        <= result
        <= MAX_DRAWING_LINE_DELTA_MM
    ):
        _error(
            "Drawing line delta_distance_mm must be between 0.000001 and "
            "1000000 millimetres.",
            "NATIVE_DRAWING_LINE_LENGTH_PARAMETERS_INVALID",
        )
    return round(result, 12)


def _resolve_line(
    value: Any,
    inventory: Mapping[str, Any],
) -> dict[str, Any]:
    exact = exact_drawing_mapping(
        value,
        _TARGET_FIELDS,
        "target",
        family="line length",
        error_code="NATIVE_DRAWING_LINE_LENGTH_PARAMETERS_INVALID",
    )
    kind = str(exact["kind"] or "")
    tag = str(exact["tag"] or "")
    if kind not in _KINDS:
        _error(
            "Drawing line target kind must be cosmetic_edge or centerline.",
            "NATIVE_DRAWING_LINE_LENGTH_PARAMETERS_INVALID",
        )
    line = next(
        (
            item
            for item in inventory["lines"]
            if item["kind"] == kind and item["tag"] == tag
        ),
        None,
    )
    if line is None:
        _error(
            "The exact target is unavailable or is not a straight persistent line.",
            "NATIVE_DRAWING_LINE_LENGTH_TARGET_STALE",
            repair={"read_operation": "drawing.line_length/read_view"},
        )
    if (
        str(exact["expected_line_length_state_sha256"])
        != line["line_length_state_sha256"]
    ):
        _error(
            f"Drawing line {line['subelement']} changed after it was inspected.",
            "NATIVE_DRAWING_LINE_LENGTH_TARGET_STALE",
            repair={
                "kind": line["kind"],
                "tag": line["tag"],
                "current_line_length_state_sha256": line[
                    "line_length_state_sha256"
                ],
            },
        )
    return line


def prepare_drawing_line_length_change(
    document: Any,
    *,
    operation: str,
    values: Mapping[str, Any],
) -> PreparedDrawingLineLengthChange:
    if operation not in _OPERATIONS:
        raise ValueError("operation must be extend or shorten")
    target = _target(document, values)
    inventory = _inventory(
        target.view,
        values["expected_inventory_state_sha256"],
    )
    line = _resolve_line(values["target"], inventory)
    delta = _delta(values["delta_distance_mm"])
    if operation == "shorten" and 2.0 * delta >= line["length_mm"]:
        _error(
            "Drawing line shortening distance must be less than half the current "
            "line length.",
            "NATIVE_DRAWING_LINE_LENGTH_TOO_SHORT",
            repair={"maximum_exclusive_mm": line["length_mm"] / 2.0},
        )
    return PreparedDrawingLineLengthChange(
        operation=operation,
        target=target,
        inventory_before=inventory,
        line_before=line,
        attribute_inventory_before=drawing_line_attribute_inventory_state(
            target.view
        ),
        delta_distance_mm=delta,
    )


def mutate_drawing_line_length(
    _document: Any,
    *,
    prepared: PreparedDrawingLineLengthChange,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedDrawingLineLengthChange):
        raise TypeError("prepared must be a PreparedDrawingLineLengthChange")
    import TechDrawGui

    line = prepared.line_before
    try:
        TechDrawGui.changeDrawingLineLength(
            prepared.target.view,
            line["kind"],
            line["tag"],
            prepared.operation,
            prepared.delta_distance_mm,
        )
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_LINE_LENGTH_CHANGE_FAILED",
            "TechDraw could not change the exact line length: "
            f"{str(exc).strip()}",
        ) from exc
    return NativeMutationDraft(
        value={"prepared": prepared},
        recompute_targets=(prepared.target.view, prepared.target.page),
        changed=(object_identity(prepared.target.view),),
    )


def _postcondition_error(message: str) -> None:
    raise NativeMutationError(
        "NATIVE_DRAWING_LINE_LENGTH_POSTCONDITION_FAILED",
        message,
    )


def _close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1.0e-10, abs_tol=1.0e-8)


def _point_close(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return _close(float(left["x_mm"]), float(right["x_mm"])) and _close(
        float(left["y_mm"]), float(right["y_mm"])
    )


def _expected_line(prepared: PreparedDrawingLineLengthChange) -> dict[str, Any]:
    before = prepared.line_before
    start = before["start_in_view_mm"]
    end = before["end_in_view_mm"]
    length = before["length_mm"]
    ux = (end["x_mm"] - start["x_mm"]) / length
    uy = (end["y_mm"] - start["y_mm"]) / length
    signed = (
        prepared.delta_distance_mm
        if prepared.operation == "extend"
        else -prepared.delta_distance_mm
    )
    return {
        "start_in_view_mm": {
            "x_mm": start["x_mm"] - ux * signed,
            "y_mm": start["y_mm"] - uy * signed,
        },
        "end_in_view_mm": {
            "x_mm": end["x_mm"] + ux * signed,
            "y_mm": end["y_mm"] + uy * signed,
        },
        "length_mm": length + 2.0 * signed,
        "centerline_extension_mm": (
            before["centerline_extension_mm"] + signed
            if before["centerline_extension_mm"] is not None
            else None
        ),
    }


def _view_boundary(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    ignored = frozenset({"state_sha256", "visible_edge_count", "hidden_edge_count"})
    return {
        key: value for key, value in before.items() if key not in ignored
    } == {key: value for key, value in after.items() if key not in ignored}


def _projection_boundary(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    target_name: str,
) -> bool:
    for name in ("coordinate_space", "axis_convention", "view_scale"):
        if before[name] != after[name]:
            return False
    for name in ("edge_count", "vertex_count", "face_count", "element_count"):
        if before[name] != after[name]:
            return False
    before_by_name = {item["name"]: item for item in before["elements"]}
    after_by_name = {item["name"]: item for item in after["elements"]}
    if frozenset(before_by_name) != frozenset(after_by_name):
        return False
    changing = frozenset(
        {
            "element_state_sha256",
            "length_view_mm",
            "bounds_in_view_mm",
            "start_in_view_mm",
            "end_in_view_mm",
            "midpoint_in_view_mm",
        }
    )
    for name, old in before_by_name.items():
        new = after_by_name[name]
        if name != target_name:
            if old != new:
                return False
            continue
        if {key: value for key, value in old.items() if key not in changing} != {
            key: value for key, value in new.items() if key not in changing
        }:
            return False
    return True


def _verify_drawing_line_length(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedDrawingLineLengthChange = draft.value["prepared"]
    target = prepared.target
    if (
        tuple(map(drawing_object_key, document.Objects))
        != tuple(map(drawing_object_key, target.objects_before))
        or tuple(map(drawing_object_key, tuple(target.page.Views or ())))
        != tuple(map(drawing_object_key, target.page_views_before))
        or tuple(map(drawing_object_key, drawing_timeline_operations(document)))
        != tuple(map(drawing_object_key, target.timeline_before))
    ):
        _postcondition_error(
            "Line-length change altered objects, page membership, or History."
        )
    if drawing_selection_state(document) != target.selection_before:
        _postcondition_error("Line-length change altered the human selection.")
    visibility = tuple(
        (obj, bool(obj.ViewObject.Visibility))
        for obj, _visible in target.visibility_before
    )
    if visibility != target.visibility_before:
        _postcondition_error("Line-length change altered object visibility.")
    if not _view_boundary(target.view_state_before, drawing_view_state(target.view)):
        _postcondition_error("Line-length change altered the Drawing view definition.")

    inventory = drawing_line_length_inventory_state(target.view)
    before_by_key = {
        (line["kind"], line["tag"]): line
        for line in prepared.inventory_before["lines"]
    }
    after_by_key = {
        (line["kind"], line["tag"]): line for line in inventory["lines"]
    }
    if frozenset(before_by_key) != frozenset(after_by_key):
        _postcondition_error("Line-length change altered persistent line identities.")
    key = (prepared.line_before["kind"], prepared.line_before["tag"])
    changed = after_by_key[key]
    for other_key, old in before_by_key.items():
        if other_key != key and after_by_key[other_key] != old:
            other = after_by_key[other_key]
            changed_fields = sorted(
                name
                for name in frozenset(old) | frozenset(other)
                if old.get(name) != other.get(name)
            )
            _postcondition_error(
                f"Non-target Drawing {other['kind']} {other['tag']} changed "
                f"fields {', '.join(changed_fields)} unexpectedly "
                f"({old['line_length_state_sha256']} -> "
                f"{other['line_length_state_sha256']})."
            )
    expected = _expected_line(prepared)
    if (
        changed["kind"] != prepared.line_before["kind"]
        or changed["tag"] != prepared.line_before["tag"]
        or changed["subelement"] != prepared.line_before["subelement"]
        or not _point_close(
            changed["start_in_view_mm"], expected["start_in_view_mm"]
        )
        or not _point_close(changed["end_in_view_mm"], expected["end_in_view_mm"])
        or not _close(changed["length_mm"], expected["length_mm"])
        or (
            expected["centerline_extension_mm"] is None
            and changed["centerline_extension_mm"] is not None
        )
        or (
            expected["centerline_extension_mm"] is not None
            and (
                changed["centerline_extension_mm"] is None
                or not _close(
                    changed["centerline_extension_mm"],
                    expected["centerline_extension_mm"],
                )
            )
        )
    ):
        _postcondition_error(
            "The target line did not retain its identity and exact requested length."
        )
    if (
        changed["line_length_state_sha256"]
        == prepared.line_before["line_length_state_sha256"]
        or inventory["inventory_state_sha256"]
        == prepared.inventory_before["inventory_state_sha256"]
    ):
        _postcondition_error("The persistent Drawing line geometry did not change.")
    if (
        drawing_line_attribute_inventory_state(target.view)
        != prepared.attribute_inventory_before
    ):
        _postcondition_error("Line-length change altered persistent line attributes.")

    projection = drawing_projected_geometry_state(target.view)
    if not _projection_boundary(
        target.projection_state_before,
        projection,
        changed["subelement"],
    ):
        _postcondition_error(
            "Line-length change altered projected geometry outside its exact target."
        )
    projected = next(
        item
        for item in projection["elements"]
        if item["name"] == changed["subelement"]
    )
    if not _close(
        projected["length_view_mm"],
        changed["length_mm"] * projection["view_scale"],
    ):
        _postcondition_error(
            "The projected target length does not match its persistent line state."
        )
    page_state = drawing_page_state(target.page)
    if page_state["view_count"] != target.page_state_before["view_count"]:
        _postcondition_error("Line-length change altered Drawing page membership.")
    return {
        "operation": prepared.operation,
        "line_length": {
            "view_object_name": inventory["view_object_name"],
            "inventory_state_sha256": inventory["inventory_state_sha256"],
            "projection_state_sha256": projection["projection_state_sha256"],
            "delta_distance_mm": prepared.delta_distance_mm,
            "changed_line": changed,
        },
    }


def verify_drawing_line_length(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    try:
        return _verify_drawing_line_length(document, draft)
    except NativeMutationError:
        raise
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_LINE_LENGTH_POSTCONDITION_FAILED",
            "The Drawing line-length change could not be verified exactly: "
            f"{str(exc).strip()}",
        ) from exc
