# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact read and transactional mutation of persistent Drawing line formats."""

from __future__ import annotations

from dataclasses import dataclass
import math
import struct
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
    MAX_DRAWING_LINE_ATTRIBUTE_TARGETS,
    NativeDrawingLineAttributeStateError,
    drawing_line_attribute_inventory_state,
    drawing_line_attribute_page,
)
from VibeCADNativeDrawingLineDefaults import drawing_line_defaults_state
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewState import drawing_view_state
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeTargets import object_identity


_PERSISTENT_TARGET_FIELDS = frozenset(
    {"kind", "tag", "expected_line_state_sha256"}
)
_PROJECTED_TARGET_FIELDS = frozenset(
    {"kind", "subelement", "expected_line_state_sha256"}
)
_ATTRIBUTE_FIELDS = frozenset(
    {
        "expected_line_defaults_state_sha256",
        "line_number",
        "width_choice",
        "color_rgb",
        "visible",
    }
)
_COLOR_FIELDS = frozenset({"red", "green", "blue"})
_KINDS = frozenset({"projected_edge", "cosmetic_edge", "centerline"})
_WIDTH_CHOICES = frozenset({"thin", "middle", "thick"})


@dataclass(frozen=True, slots=True)
class DrawingLineFormatSpec:
    line_number: int
    width_choice: str
    width_mm: float
    color_rgb: dict[str, float]
    visible: bool

    def state(self) -> dict[str, Any]:
        return {
            "line_number": self.line_number,
            "style_code": self.line_number,
            "width_mm": self.width_mm,
            "color_rgb": self.color_rgb,
            "visible": self.visible,
        }


@dataclass(frozen=True, slots=True)
class PreparedDrawingLineAttributeChange:
    target: PreparedDrawingDimensionTarget
    inventory_before: dict[str, Any]
    line_keys: tuple[tuple[str, str], ...]
    line_states_before: tuple[dict[str, Any], ...]
    format: DrawingLineFormatSpec


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
        family="line attributes",
        code_prefix="NATIVE_DRAWING_LINE_ATTRIBUTES",
    )


def _inventory(
    view: Any,
    expected_sha256: Any,
) -> dict[str, Any]:
    try:
        state = drawing_line_attribute_inventory_state(view)
    except (AttributeError, NativeDrawingLineAttributeStateError, TypeError) as exc:
        _error(
            f"The Drawing line inventory is unavailable: {str(exc).strip()}",
            "NATIVE_DRAWING_LINE_ATTRIBUTES_STATE_INVALID",
        )
    if str(expected_sha256) != state["inventory_state_sha256"]:
        _error(
            "The Drawing line inventory changed after it was inspected.",
            "NATIVE_DRAWING_LINE_ATTRIBUTES_INVENTORY_STALE",
            repair={
                "current_inventory_state_sha256": state[
                    "inventory_state_sha256"
                ]
            },
        )
    return state


def read_drawing_line_attributes(
    document: Any,
    *,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    """Read one exact bounded page from a view's persistent line inventory."""

    target = _target(document, values)
    try:
        page = drawing_line_attribute_page(
            target.view,
            expected_inventory_state_sha256=str(
                values["expected_inventory_state_sha256"]
            ),
            offset=values["offset"],
            page_size=values["page_size"],
        )
    except (NativeDrawingLineAttributeStateError, TypeError, ValueError) as exc:
        _error(
            str(exc),
            "NATIVE_DRAWING_LINE_ATTRIBUTES_READ_INVALID",
        )
    return {"line_attributes": page}


def _finite_color(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        _error(
            f"Drawing line color {name} must be numeric.",
            "NATIVE_DRAWING_LINE_ATTRIBUTES_PARAMETERS_INVALID",
        )
        raise AssertionError from exc
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        _error(
            f"Drawing line color {name} must be between 0 and 1.",
            "NATIVE_DRAWING_LINE_ATTRIBUTES_PARAMETERS_INVALID",
        )
    return round(struct.unpack("f", struct.pack("f", result))[0], 12)


def _format(value: Any) -> DrawingLineFormatSpec:
    exact = exact_drawing_mapping(
        value,
        _ATTRIBUTE_FIELDS,
        "complete format",
        family="line attributes",
        error_code="NATIVE_DRAWING_LINE_ATTRIBUTES_PARAMETERS_INVALID",
    )
    defaults = drawing_line_defaults_state()
    if (
        str(exact["expected_line_defaults_state_sha256"])
        != defaults["state_sha256"]
    ):
        _error(
            "The Drawing line style catalog or width choices changed after inspection.",
            "NATIVE_DRAWING_LINE_DEFAULTS_STALE",
            repair={"current_line_defaults_state_sha256": defaults["state_sha256"]},
        )
    line_number = exact["line_number"]
    if (
        type(line_number) is not int
        or not 1 <= line_number <= defaults["available_style_count"]
    ):
        _error(
            "Drawing line_number must name one style in the current published catalog.",
            "NATIVE_DRAWING_LINE_ATTRIBUTES_PARAMETERS_INVALID",
            repair={"allowed_values": list(range(1, defaults["available_style_count"] + 1))},
        )
    width_choice = str(exact["width_choice"] or "")
    if width_choice not in _WIDTH_CHOICES:
        _error(
            "Drawing width_choice must be thin, middle, or thick.",
            "NATIVE_DRAWING_LINE_ATTRIBUTES_PARAMETERS_INVALID",
            repair={"allowed_values": sorted(_WIDTH_CHOICES)},
        )
    color = exact_drawing_mapping(
        exact["color_rgb"],
        _COLOR_FIELDS,
        "color_rgb",
        family="line attributes",
        error_code="NATIVE_DRAWING_LINE_ATTRIBUTES_PARAMETERS_INVALID",
    )
    visible = exact["visible"]
    if type(visible) is not bool:
        _error(
            "Drawing line visible must be true or false.",
            "NATIVE_DRAWING_LINE_ATTRIBUTES_PARAMETERS_INVALID",
        )
    return DrawingLineFormatSpec(
        line_number=line_number,
        width_choice=width_choice,
        width_mm=defaults["available_widths"][f"{width_choice}_mm"],
        color_rgb={
            name: _finite_color(color[name], name)
            for name in ("red", "green", "blue")
        },
        visible=visible,
    )


def _resolve_lines(
    raw_targets: Any,
    inventory: Mapping[str, Any],
) -> tuple[tuple[tuple[str, str], ...], tuple[dict[str, Any], ...]]:
    if not isinstance(raw_targets, (list, tuple)) or not 1 <= len(
        raw_targets
    ) <= MAX_DRAWING_LINE_ATTRIBUTE_TARGETS:
        _error(
            "Drawing line attributes require 1 to 32 exact targets.",
            "NATIVE_DRAWING_LINE_ATTRIBUTES_PARAMETERS_INVALID",
        )
    by_key = {_line_key(line): line for line in inventory["lines"]}
    keys = []
    states = []
    for raw in raw_targets:
        if not isinstance(raw, Mapping):
            _error(
                "Each Drawing line target must be an exact object.",
                "NATIVE_DRAWING_LINE_ATTRIBUTES_PARAMETERS_INVALID",
            )
        kind = str(raw.get("kind", "") or "")
        fields = (
            _PROJECTED_TARGET_FIELDS
            if kind == "projected_edge"
            else _PERSISTENT_TARGET_FIELDS
        )
        exact = exact_drawing_mapping(
            raw,
            fields,
            "line target",
            family="line attributes",
            error_code="NATIVE_DRAWING_LINE_ATTRIBUTES_PARAMETERS_INVALID",
        )
        key = (
            kind,
            str(
                exact["subelement"]
                if kind == "projected_edge"
                else exact["tag"]
            ),
        )
        if key[0] not in _KINDS:
            _error(
                "Drawing line target kind must be projected_edge, cosmetic_edge, or centerline.",
                "NATIVE_DRAWING_LINE_ATTRIBUTES_PARAMETERS_INVALID",
            )
        if key in keys:
            _error(
                "A Drawing line target was provided more than once.",
                "NATIVE_DRAWING_LINE_ATTRIBUTES_TARGETS_INVALID",
            )
        state = by_key.get(key)
        if state is None:
            _error(
                "An exact Drawing line target no longer exists in the view.",
                "NATIVE_DRAWING_LINE_ATTRIBUTES_TARGET_STALE",
                repair={"read_operation": "drawing.line_attributes/read_view"},
            )
        if str(exact["expected_line_state_sha256"]) != state["line_state_sha256"]:
            _error(
                f"Drawing line {state['subelement']} changed after it was inspected.",
                "NATIVE_DRAWING_LINE_ATTRIBUTES_TARGET_STALE",
                repair={
                    "kind": state["kind"],
                    (
                        "subelement"
                        if state["kind"] == "projected_edge"
                        else "tag"
                    ): key[1],
                    "current_line_state_sha256": state["line_state_sha256"],
                },
            )
        keys.append(key)
        states.append(state)
    return tuple(keys), tuple(states)


def _line_key(line: Mapping[str, Any]) -> tuple[str, str]:
    kind = str(line["kind"])
    return (
        kind,
        str(line["subelement"] if kind == "projected_edge" else line["tag"]),
    )


def prepare_drawing_line_attribute_change(
    document: Any,
    *,
    values: Mapping[str, Any],
) -> PreparedDrawingLineAttributeChange:
    target = _target(document, values)
    inventory = _inventory(
        target.view,
        values["expected_inventory_state_sha256"],
    )
    keys, states = _resolve_lines(values["targets"], inventory)
    format_spec = _format(values["attributes"])
    requested = format_spec.state()
    if all(state["format"] == requested for state in states):
        _error(
            "Every exact Drawing line target already has the requested format.",
            "NATIVE_DRAWING_NO_CHANGE",
        )
    return PreparedDrawingLineAttributeChange(
        target=target,
        inventory_before=inventory,
        line_keys=keys,
        line_states_before=states,
        format=format_spec,
    )


def mutate_drawing_line_attributes(
    _document: Any,
    *,
    prepared: PreparedDrawingLineAttributeChange,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedDrawingLineAttributeChange):
        raise TypeError("prepared must be a PreparedDrawingLineAttributeChange")
    import TechDrawGui

    try:
        TechDrawGui.changeDrawingLineAttributes(
            prepared.target.view,
            list(prepared.line_keys),
            prepared.format.line_number,
            prepared.format.width_mm,
            prepared.format.color_rgb["red"],
            prepared.format.color_rgb["green"],
            prepared.format.color_rgb["blue"],
            prepared.format.visible,
        )
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_LINE_ATTRIBUTES_CHANGE_FAILED",
            f"TechDraw could not change the exact line attributes: {str(exc).strip()}",
        ) from exc
    return NativeMutationDraft(
        value={"prepared": prepared},
        recompute_targets=(prepared.target.view, prepared.target.page),
        changed=(object_identity(prepared.target.view),),
    )


def _postcondition_error(message: str) -> None:
    raise NativeMutationError(
        "NATIVE_DRAWING_LINE_ATTRIBUTES_POSTCONDITION_FAILED",
        message,
    )


def _view_boundary(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    ignored = frozenset({"state_sha256", "visible_edge_count", "hidden_edge_count"})
    return {
        key: value for key, value in before.items() if key not in ignored
    } == {key: value for key, value in after.items() if key not in ignored}


def _projection_boundary(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    target_visibility: Mapping[str, bool],
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
    for name, old in before_by_name.items():
        new = after_by_name[name]
        if name not in target_visibility:
            if old != new:
                return False
            continue
        old_geometry = {
            key: value
            for key, value in old.items()
            if key not in {"visible", "element_state_sha256"}
        }
        new_geometry = {
            key: value
            for key, value in new.items()
            if key not in {"visible", "element_state_sha256"}
        }
        if old_geometry != new_geometry or new["visible"] != target_visibility[name]:
            return False
    return True


def _verify_drawing_line_attributes(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedDrawingLineAttributeChange = draft.value["prepared"]
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
            "Line-attribute change altered objects, page membership, or History."
        )
    if drawing_selection_state(document) != target.selection_before:
        _postcondition_error("Line-attribute change altered the human selection.")
    visibility = tuple(
        (obj, bool(obj.ViewObject.Visibility))
        for obj, _visible in target.visibility_before
    )
    if visibility != target.visibility_before:
        _postcondition_error("Line-attribute change altered object visibility.")

    view_state = drawing_view_state(target.view)
    if not _view_boundary(target.view_state_before, view_state):
        _postcondition_error("Line-attribute change altered the Drawing view definition.")
    projection = drawing_projected_geometry_state(target.view)
    target_visibility = {
        state["subelement"]: prepared.format.visible
        for state in prepared.line_states_before
    }
    if not _projection_boundary(
        target.projection_state_before,
        projection,
        target_visibility,
    ):
        _postcondition_error(
            "Line-attribute change altered projected geometry outside target visibility."
        )

    inventory = drawing_line_attribute_inventory_state(target.view)
    before_by_key = {
        _line_key(line): line for line in prepared.inventory_before["lines"]
    }
    after_by_key = {_line_key(line): line for line in inventory["lines"]}
    if frozenset(before_by_key) != frozenset(after_by_key):
        _postcondition_error("Line-attribute change altered persistent line identities.")
    requested = prepared.format.state()
    changed = []
    for key, old in before_by_key.items():
        new = after_by_key[key]
        if key in prepared.line_keys:
            if (
                _line_key(new) != _line_key(old)
                or new["subelement"] != old["subelement"]
                or new["format"] != requested
            ):
                _postcondition_error(
                    "A target line did not retain its identity and requested format."
                )
            changed.append(new)
        elif new != old:
            _postcondition_error("A non-target Drawing line changed unexpectedly.")
    if inventory["inventory_state_sha256"] == prepared.inventory_before[
        "inventory_state_sha256"
    ]:
        _postcondition_error("The persistent Drawing line inventory did not change.")
    page_state = drawing_page_state(target.page)
    if page_state["view_count"] != target.page_state_before["view_count"]:
        _postcondition_error("Line-attribute change altered Drawing page membership.")
    return {
        "operation": "set",
        "line_attributes": {
            "view_object_name": inventory["view_object_name"],
            "inventory_state_sha256": inventory["inventory_state_sha256"],
            "line_count": inventory["line_count"],
            "projected_edge_count": inventory["projected_edge_count"],
            "cosmetic_edge_count": inventory["cosmetic_edge_count"],
            "centerline_count": inventory["centerline_count"],
            "changed_lines": changed,
        },
    }


def verify_drawing_line_attributes(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    try:
        return _verify_drawing_line_attributes(document, draft)
    except NativeMutationError:
        raise
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_LINE_ATTRIBUTES_POSTCONDITION_FAILED",
            "The Drawing line-attribute change could not be verified exactly: "
            f"{str(exc).strip()}",
        ) from exc
