# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic exact-target replacement of complete Drawing dimension edit state."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeDrawingDimensionEditState import drawing_dimension_edit_state
from VibeCADNativeDrawingDimensionSupport import (
    drawing_dimension_error,
    drawing_object_key,
    drawing_selection_state,
    drawing_timeline_operations,
    drawing_visibility_state,
    exact_drawing_mapping,
)
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeTargets import object_identity, resolve_object


_TARGET_FIELDS = frozenset({"object_name", "expected_edit_state_sha256"})
_DISPLAY_FIELDS = frozenset({"format_spec", "arbitrary"})
_TOLERANCE_FIELDS = frozenset(
    {
        "unit",
        "theoretical_exact",
        "equal",
        "over",
        "under",
        "arbitrary",
        "over_format_spec",
        "under_format_spec",
    }
)
_LAYOUT_FIELDS = frozenset(
    {
        "label_position_in_view_mm",
        "angle_override",
        "line_angle_degrees",
        "extension_angle_degrees",
    }
)
_POSITION_FIELDS = frozenset({"x_mm", "y_mm"})
_APPEARANCE_FIELDS = frozenset(
    {"flip_arrowheads", "color_rgb", "font_size_mm", "standard_and_style"}
)
_COLOR_FIELDS = frozenset({"red", "green", "blue"})
_STYLES = {
    "iso_oriented": 0,
    "iso_referencing": 1,
    "asme_inlined": 2,
    "asme_referencing": 3,
}


@dataclass(frozen=True, slots=True)
class PreparedDrawingDimensionEdit:
    dimension: Any
    page: Any
    desired: dict[str, Any]
    state_before: dict[str, Any]
    structure_before: dict[str, Any]
    page_state_before: dict[str, Any]
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
    drawing_dimension_error(message, code, repair=repair)


def _mapping(value: Any, fields: frozenset[str], noun: str) -> Mapping[str, Any]:
    return exact_drawing_mapping(
        value,
        fields,
        noun,
        family="dimension edit",
        error_code="NATIVE_DRAWING_DIMENSION_EDIT_PARAMETERS_INVALID",
    )


def _finite(value: Any, noun: str, minimum: float, maximum: float) -> float:
    if type(value) not in {int, float}:
        _error(
            f"Drawing dimension {noun} must be numeric.",
            "NATIVE_DRAWING_DIMENSION_EDIT_PARAMETERS_INVALID",
        )
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        _error(
            f"Drawing dimension {noun} must be between {minimum:g} and {maximum:g}.",
            "NATIVE_DRAWING_DIMENSION_EDIT_PARAMETERS_INVALID",
        )
    return result


def _boolean(value: Any, noun: str) -> bool:
    if type(value) is not bool:
        _error(
            f"Drawing dimension {noun} must be a boolean.",
            "NATIVE_DRAWING_DIMENSION_EDIT_PARAMETERS_INVALID",
        )
    return value


def _text(value: Any, noun: str) -> str:
    if not isinstance(value, str) or len(value) > 512:
        _error(
            f"Drawing dimension {noun} must contain at most 512 characters.",
            "NATIVE_DRAWING_DIMENSION_EDIT_PARAMETERS_INVALID",
        )
    return value


def _normalize_desired(
    values: Mapping[str, Any], state: Mapping[str, Any]
) -> dict[str, Any]:
    display = _mapping(values["display"], _DISPLAY_FIELDS, "display")
    tolerance = _mapping(values["tolerance"], _TOLERANCE_FIELDS, "tolerance")
    layout = _mapping(values["layout"], _LAYOUT_FIELDS, "layout")
    position = _mapping(
        layout["label_position_in_view_mm"],
        _POSITION_FIELDS,
        "label position",
    )
    appearance = _mapping(values["appearance"], _APPEARANCE_FIELDS, "appearance")
    color = _mapping(appearance["color_rgb"], _COLOR_FIELDS, "color")
    unit = tolerance["unit"]
    if unit not in {"mm", "degrees"} or unit != state["tolerance"]["unit"]:
        _error(
            "Drawing dimension tolerance unit does not match its dimension type.",
            "NATIVE_DRAWING_DIMENSION_EDIT_UNIT_MISMATCH",
            repair={"required_unit": state["tolerance"]["unit"]},
        )
    theoretical = _boolean(tolerance["theoretical_exact"], "theoretical-exact state")
    equal = _boolean(tolerance["equal"], "equal-tolerance state")
    arbitrary_tolerance = _boolean(
        tolerance["arbitrary"],
        "arbitrary-tolerance state",
    )
    over = _finite(tolerance["over"], "over tolerance", -1.0e6, 1.0e6)
    under = _finite(tolerance["under"], "under tolerance", -1.0e6, 1.0e6)
    if theoretical and (
        equal
        or arbitrary_tolerance
        or not math.isclose(over, 0.0, abs_tol=1.0e-12)
        or not math.isclose(under, 0.0, abs_tol=1.0e-12)
    ):
        _error(
            "A theoretically exact dimension must have no numeric or arbitrary tolerance.",
            "NATIVE_DRAWING_DIMENSION_EDIT_TOLERANCE_INVALID",
        )
    if equal and (
        theoretical
        or over < 0.0
        or not math.isclose(under, -over, rel_tol=1.0e-10, abs_tol=1.0e-9)
    ):
        _error(
            "An equal tolerance requires a nonnegative over value and its exact negative under value.",
            "NATIVE_DRAWING_DIMENSION_EDIT_TOLERANCE_INVALID",
        )
    style = appearance["standard_and_style"]
    if style not in _STYLES:
        _error(
            "Drawing dimension standard/style is unsupported.",
            "NATIVE_DRAWING_DIMENSION_EDIT_PARAMETERS_INVALID",
        )
    channels = {}
    for name in ("red", "green", "blue"):
        raw = color[name]
        if type(raw) is not int or not 0 <= raw <= 255:
            _error(
                "Drawing dimension RGB channels must be integers from 0 through 255.",
                "NATIVE_DRAWING_DIMENSION_EDIT_PARAMETERS_INVALID",
            )
        channels[name] = raw
    return {
        "display": {
            "format_spec": _text(display["format_spec"], "format"),
            "arbitrary": _boolean(display["arbitrary"], "arbitrary-display state"),
        },
        "tolerance": {
            "unit": unit,
            "theoretical_exact": theoretical,
            "equal": equal,
            "over": over,
            "under": under,
            "arbitrary": arbitrary_tolerance,
            "over_format_spec": _text(
                tolerance["over_format_spec"],
                "over-tolerance format",
            ),
            "under_format_spec": _text(
                tolerance["under_format_spec"],
                "under-tolerance format",
            ),
        },
        "layout": {
            "label_position_in_view_mm": {
                "x_mm": _finite(
                    position["x_mm"], "label X coordinate", -10_000.0, 10_000.0
                ),
                "y_mm": _finite(
                    position["y_mm"], "label Y coordinate", -10_000.0, 10_000.0
                ),
            },
            "angle_override": _boolean(
                layout["angle_override"], "angle-override state"
            ),
            "line_angle_degrees": _finite(
                layout["line_angle_degrees"],
                "line angle",
                -360.0,
                360.0,
            ),
            "extension_angle_degrees": _finite(
                layout["extension_angle_degrees"],
                "extension angle",
                -360.0,
                360.0,
            ),
        },
        "appearance": {
            "flip_arrowheads": _boolean(
                appearance["flip_arrowheads"],
                "flip-arrowheads state",
            ),
            "color_rgb": channels,
            "font_size_mm": _finite(
                appearance["font_size_mm"],
                "font size",
                1.0e-9,
                1_000.0,
            ),
            "standard_and_style": style,
        },
    }


def _link_state(value: Any) -> list[dict[str, Any]]:
    result = []
    for obj, raw_names in tuple(value or ()):
        names = (raw_names,) if isinstance(raw_names, str) else tuple(raw_names or ())
        result.append(
            {
                "object_name": str(getattr(obj, "Name", "") or ""),
                "subelements": [str(name or "") for name in names],
            }
        )
    return result


def _structure_state(dimension: Any) -> dict[str, Any]:
    arc_source = getattr(dimension, "ArcLengthSource", None)
    if not isinstance(arc_source, tuple) or len(arc_source) != 2:
        arc_source = (None, ())
    vector = getattr(dimension, "AreaLeaderPoint", None)
    return {
        "object_name": str(dimension.Name),
        "label": str(dimension.Label),
        "page_name": str(dimension.findParentPage().Name),
        "type": str(dimension.Type),
        "measure_type": str(dimension.MeasureType),
        "references_2d": _link_state(dimension.References2D),
        "references_3d": _link_state(dimension.References3D),
        "arc_length_source": _link_state((arc_source,)),
        "arc_length_value": float(
            getattr(dimension.ArcLengthValue, "Value", dimension.ArcLengthValue)
        ),
        "inverted": bool(dimension.Inverted),
        "show_supplementary": bool(dimension.ShowSupplementary),
        "use_actual_area": bool(dimension.UseActualArea),
        "use_area_leader_point": bool(dimension.UseAreaLeaderPoint),
        "area_leader_point": [
            float(getattr(vector, "x", 0.0)),
            float(getattr(vector, "y", 0.0)),
            float(getattr(vector, "z", 0.0)),
        ],
        "show_units": bool(dimension.ShowUnits),
    }


def _validate_format(dimension: Any, desired: Mapping[str, Any]) -> None:
    if desired["display"]["arbitrary"]:
        return
    try:
        import TechDrawGui

        result = dict(
            TechDrawGui.validateDrawingFormatCustomization(
                dimension,
                desired["display"]["format_spec"],
            )
        )
    except Exception as exc:
        _error(
            f"TechDraw rejected the complete dimension format: {str(exc).strip()}",
            "NATIVE_DRAWING_DIMENSION_EDIT_FORMAT_INVALID",
            repair={"accepted_placeholders": ["%f", "%.2f", "%g", "%w", "%r"]},
        )
    if result.get("target_kind") != "dimension":
        _error(
            "TechDraw did not validate the exact dimension format target.",
            "NATIVE_DRAWING_DIMENSION_EDIT_RUNTIME_UNAVAILABLE",
        )


def _apply_display(dimension: Any, display: Mapping[str, Any]) -> None:
    if display["arbitrary"]:
        dimension.Arbitrary = True
        dimension.FormatSpec = display["format_spec"]
    else:
        dimension.FormatSpec = display["format_spec"]
        dimension.Arbitrary = False


def prepare_drawing_dimension_edit(
    document: Any,
    *,
    values: Mapping[str, Any],
) -> PreparedDrawingDimensionEdit:
    target = _mapping(values["dimension"], _TARGET_FIELDS, "target")
    dimension = resolve_object(
        document,
        {"document_uid": str(document.Uid), "object_name": str(target["object_name"])},
        expected_types=("TechDraw::DrawViewDimension",),
    )
    try:
        state = drawing_dimension_edit_state(dimension)
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        _error(
            f"The exact Drawing dimension cannot be edited: {str(exc).strip()}",
            "NATIVE_DRAWING_DIMENSION_EDIT_TARGET_INVALID",
        )
    if str(target["expected_edit_state_sha256"]) != state["edit_state_sha256"]:
        _error(
            "The exact Drawing dimension changed after it was inspected.",
            "NATIVE_DRAWING_DIMENSION_EDIT_STALE",
            repair={"current_edit_state_sha256": state["edit_state_sha256"]},
        )
    if not state["timeline_usable"] or not state["valid"]:
        _error(
            "The exact Drawing dimension is invalid or unavailable at the current History position.",
            "NATIVE_DRAWING_DIMENSION_EDIT_TARGET_UNAVAILABLE",
        )
    desired = _normalize_desired(values, state)
    if all(desired[name] == state[name] for name in desired):
        _error(
            "The Drawing dimension already has the requested complete edit state.",
            "NATIVE_DRAWING_NO_CHANGE",
        )
    _validate_format(dimension, desired)
    page = dimension.findParentPage()
    return PreparedDrawingDimensionEdit(
        dimension=dimension,
        page=page,
        desired=desired,
        state_before=state,
        structure_before=_structure_state(dimension),
        page_state_before=drawing_page_state(page),
        objects_before=tuple(document.Objects),
        page_views_before=tuple(page.Views or ()),
        timeline_before=drawing_timeline_operations(document),
        selection_before=drawing_selection_state(document),
        visibility_before=drawing_visibility_state(document),
    )


def mutate_drawing_dimension_edit(
    _document: Any,
    *,
    prepared: PreparedDrawingDimensionEdit,
) -> NativeMutationDraft:
    dimension = prepared.dimension
    desired = prepared.desired
    display = desired["display"]
    tolerance = desired["tolerance"]
    layout = desired["layout"]
    appearance = desired["appearance"]
    _apply_display(dimension, display)
    dimension.TheoreticalExact = tolerance["theoretical_exact"]
    dimension.EqualTolerance = tolerance["equal"]
    dimension.OverTolerance = tolerance["over"]
    dimension.UnderTolerance = tolerance["under"]
    dimension.ArbitraryTolerances = tolerance["arbitrary"]
    dimension.FormatSpecOverTolerance = tolerance["over_format_spec"]
    dimension.FormatSpecUnderTolerance = tolerance["under_format_spec"]
    position = layout["label_position_in_view_mm"]
    dimension.X = position["x_mm"]
    dimension.Y = position["y_mm"]
    dimension.AngleOverride = layout["angle_override"]
    dimension.LineAngle = layout["line_angle_degrees"]
    dimension.ExtensionAngle = layout["extension_angle_degrees"]
    view_object = dimension.ViewObject
    view_object.FlipArrowheads = appearance["flip_arrowheads"]
    color = appearance["color_rgb"]
    view_object.Color = (
        color["red"] / 255.0,
        color["green"] / 255.0,
        color["blue"] / 255.0,
    )
    view_object.Fontsize = appearance["font_size_mm"]
    view_object.StandardAndStyle = _STYLES[appearance["standard_and_style"]]
    return NativeMutationDraft(
        value={"prepared": prepared},
        recompute_targets=(dimension, prepared.page),
        changed=(object_identity(dimension),),
    )


def _postcondition(message: str) -> None:
    raise NativeMutationError(
        "NATIVE_DRAWING_DIMENSION_EDIT_POSTCONDITION_FAILED", message
    )


def verify_drawing_dimension_edit(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedDrawingDimensionEdit = draft.value["prepared"]
    if (
        tuple(map(drawing_object_key, document.Objects))
        != tuple(map(drawing_object_key, prepared.objects_before))
        or tuple(map(drawing_object_key, tuple(prepared.page.Views or ())))
        != tuple(map(drawing_object_key, prepared.page_views_before))
        or tuple(map(drawing_object_key, drawing_timeline_operations(document)))
        != tuple(map(drawing_object_key, prepared.timeline_before))
        or drawing_selection_state(document) != prepared.selection_before
        or drawing_visibility_state(document) != prepared.visibility_before
        or drawing_page_state(prepared.page) != prepared.page_state_before
        or _structure_state(prepared.dimension) != prepared.structure_before
    ):
        _postcondition(
            "Dimension editing changed objects, references, page membership, History, selection, or visibility."
        )
    try:
        state = drawing_dimension_edit_state(prepared.dimension)
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        _postcondition(
            f"The edited Drawing dimension cannot be read back: {str(exc).strip()}"
        )
    if any(state[name] != prepared.desired[name] for name in prepared.desired):
        _postcondition(
            "The Drawing dimension did not retain the requested complete edit state."
        )
    if state["edit_state_sha256"] == prepared.state_before["edit_state_sha256"]:
        _postcondition("The Drawing dimension edit produced no exact state change.")
    return {"operation": "edit", "dimension": state}
