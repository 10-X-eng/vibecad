# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact transactional ISO 286 fit editing for Drawing dimensions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeDrawingDimensionSupport import (
    drawing_object_key,
    drawing_selection_state,
    drawing_timeline_operations,
    drawing_visibility_state,
    exact_drawing_mapping,
)
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingFormatState import drawing_format_state
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeTargets import object_identity, resolve_object


_TARGET_FIELDS = frozenset({"object_name", "expected_format_state_sha256"})
_FIT_PROPERTIES = (
    "FormatSpec",
    "EqualTolerance",
    "OverTolerance",
    "UnderTolerance",
    "FormatSpecOverTolerance",
    "FormatSpecUnderTolerance",
)


@dataclass(frozen=True, slots=True)
class PreparedDrawingFit:
    dimension: Any
    page: Any
    tolerance_class: str
    plan: dict[str, Any]
    properties_before: dict[str, Any]
    format_state_before: dict[str, Any]
    page_state_before: dict[str, Any]
    objects_before: tuple[Any, ...]
    page_views_before: tuple[Any, ...]
    timeline_before: tuple[Any, ...]
    selection_before: dict[str, Any]
    visibility_before: tuple[tuple[Any, bool], ...]


def _error(message: str, code: str, *, repair: Mapping[str, Any] | None = None):
    raise NativeDrawingError(message, error_code=code, repair=repair)


def _property_state(dimension: Any) -> dict[str, Any]:
    return {
        "FormatSpec": str(getattr(dimension, "FormatSpec", "") or ""),
        "EqualTolerance": bool(getattr(dimension, "EqualTolerance", False)),
        "OverTolerance": float(getattr(dimension, "OverTolerance", 0.0)),
        "UnderTolerance": float(getattr(dimension, "UnderTolerance", 0.0)),
        "FormatSpecOverTolerance": str(
            getattr(dimension, "FormatSpecOverTolerance", "") or ""
        ),
        "FormatSpecUnderTolerance": str(
            getattr(dimension, "FormatSpecUnderTolerance", "") or ""
        ),
    }


def _expected_properties(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "FormatSpec": plan["format_spec"],
        "EqualTolerance": plan["equal_tolerance"],
        "OverTolerance": plan["over_tolerance_mm"],
        "UnderTolerance": plan["under_tolerance_mm"],
        "FormatSpecOverTolerance": plan["over_tolerance_format"],
        "FormatSpecUnderTolerance": plan["under_tolerance_format"],
    }


def prepare_drawing_fit(
    document: Any,
    *,
    values: Mapping[str, Any],
) -> PreparedDrawingFit:
    target = exact_drawing_mapping(
        values["dimension"],
        _TARGET_FIELDS,
        "ISO 286 dimension target",
        family="hole/shaft fit",
        error_code="NATIVE_DRAWING_FIT_PARAMETERS_INVALID",
    )
    dimension = resolve_object(
        document,
        {
            "document_uid": str(document.Uid),
            "object_name": str(target["object_name"]),
        },
        expected_types=("TechDraw::DrawViewDimension",),
    )
    state = drawing_format_state(dimension)
    if str(target["expected_format_state_sha256"]) != state[
        "format_state_sha256"
    ]:
        _error(
            "The exact Drawing dimension changed after it was inspected.",
            "NATIVE_DRAWING_FIT_TARGET_STALE",
            repair={"current_format_state_sha256": state["format_state_sha256"]},
        )
    page = dimension.findParentPage()
    if page is None or page.Document is not document or dimension not in tuple(page.Views):
        _error(
            "The exact Drawing dimension is not attached to its page.",
            "NATIVE_DRAWING_FIT_TARGET_INVALID",
        )
    tolerance_class = str(values["tolerance_class"])
    try:
        from TechDrawTools.FitBuilder import plan_iso_286_fit

        plan = plan_iso_286_fit(dimension, tolerance_class)
    except Exception as exc:
        _error(
            f"TechDraw rejected the ISO 286 fit: {str(exc).strip()}",
            "NATIVE_DRAWING_FIT_INVALID",
        )
    properties = _property_state(dimension)
    if properties == _expected_properties(plan):
        _error(
            "The Drawing dimension already has the requested ISO 286 fit.",
            "NATIVE_DRAWING_NO_CHANGE",
        )
    return PreparedDrawingFit(
        dimension=dimension,
        page=page,
        tolerance_class=tolerance_class,
        plan=plan,
        properties_before=properties,
        format_state_before=state,
        page_state_before=drawing_page_state(page),
        objects_before=tuple(document.Objects),
        page_views_before=tuple(page.Views),
        timeline_before=drawing_timeline_operations(document),
        selection_before=drawing_selection_state(document),
        visibility_before=drawing_visibility_state(document),
    )


def mutate_drawing_fit(
    _document: Any,
    *,
    prepared: PreparedDrawingFit,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedDrawingFit):
        raise TypeError("prepared must be a PreparedDrawingFit")
    try:
        from TechDrawTools.FitBuilder import apply_iso_286_fit

        applied = apply_iso_286_fit(
            prepared.dimension,
            prepared.tolerance_class,
        )
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_FIT_FAILED",
            f"TechDraw could not apply the ISO 286 fit: {str(exc).strip()}",
        ) from exc
    if applied != prepared.plan:
        raise NativeMutationError(
            "NATIVE_DRAWING_FIT_FAILED",
            "TechDraw applied a different ISO 286 fit than it validated.",
        )
    return NativeMutationDraft(
        value={"prepared": prepared},
        recompute_targets=(prepared.dimension, prepared.page),
        changed=(object_identity(prepared.dimension),),
    )


def verify_drawing_fit(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared: PreparedDrawingFit = draft.value["prepared"]
    if (
        tuple(map(drawing_object_key, document.Objects))
        != tuple(map(drawing_object_key, prepared.objects_before))
        or tuple(map(drawing_object_key, prepared.page.Views))
        != tuple(map(drawing_object_key, prepared.page_views_before))
        or tuple(map(drawing_object_key, drawing_timeline_operations(document)))
        != tuple(map(drawing_object_key, prepared.timeline_before))
        or drawing_selection_state(document) != prepared.selection_before
        or drawing_visibility_state(document) != prepared.visibility_before
        or _property_state(prepared.dimension) != _expected_properties(prepared.plan)
    ):
        raise NativeMutationError(
            "NATIVE_DRAWING_FIT_POSTCONDITION_FAILED",
            "ISO 286 fit editing changed state outside the exact dimension properties.",
        )
    page_state = drawing_page_state(prepared.page)
    if page_state["view_names"] != prepared.page_state_before["view_names"]:
        raise NativeMutationError(
            "NATIVE_DRAWING_FIT_POSTCONDITION_FAILED",
            "ISO 286 fit editing changed Drawing page membership.",
        )
    state = drawing_format_state(prepared.dimension)
    tolerance = state["tolerance"]
    if (
        state["current_value"] != prepared.plan["format_spec"]
        or tolerance["equal"] is not False
        or tolerance["over_mm"] != round(prepared.plan["over_tolerance_mm"], 12)
        or tolerance["under_mm"] != round(prepared.plan["under_tolerance_mm"], 12)
        or tolerance["over_format"] != prepared.plan["over_tolerance_format"]
        or tolerance["under_format"] != prepared.plan["under_tolerance_format"]
    ):
        raise NativeMutationError(
            "NATIVE_DRAWING_FIT_POSTCONDITION_FAILED",
            "The Drawing dimension did not retain its exact ISO 286 fit.",
        )
    return {
        "operation": "apply_iso_286_fit",
        "dimension": {
            "object_name": state["object_name"],
            "format_state_sha256": state["format_state_sha256"],
            "format_spec": state["current_value"],
            "rendered_text": state["rendered_text"],
        },
        "fit": {
            "nominal_value_mm": prepared.plan["nominal_value_mm"],
            "tolerance_class": prepared.tolerance_class,
            **tolerance,
        },
    }
