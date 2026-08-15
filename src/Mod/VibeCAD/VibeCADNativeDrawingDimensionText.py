# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact atomic Drawing dimension prefix and precision changes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from VibeCADNativeDrawingDimensionSupport import (
    drawing_dimension_error,
    drawing_object_key,
    drawing_selection_state,
    drawing_timeline_operations,
    drawing_visibility_state,
    exact_drawing_mapping,
)
from VibeCADNativeDrawingDimensionTextState import (
    DRAWING_DIMENSION_TEXT_OPERATIONS,
    MAX_DRAWING_DIMENSION_TEXT_TARGETS,
    MAX_DRAWING_REPETITION_COUNT,
    normalize_dimension_text_host_plans,
)
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingFormatState import drawing_format_state
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeTargets import object_identity, resolve_object


_PAGE_FIELDS = frozenset({"object_name", "expected_state_sha256"})
_DIMENSION_FIELDS = frozenset(
    {"object_name", "expected_format_state_sha256"}
)


@dataclass(frozen=True, slots=True)
class DrawingDimensionTextSpec:
    operation: str
    repetition_text: str


@dataclass(frozen=True, slots=True)
class PreparedDrawingDimensionText:
    page: Any
    dimensions: tuple[Any, ...]
    spec: DrawingDimensionTextSpec
    host_validation: tuple[dict[str, Any], ...]
    states_before: tuple[dict[str, Any], ...]
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


def _spec(operation: str, values: Mapping[str, Any]) -> DrawingDimensionTextSpec:
    if operation not in DRAWING_DIMENSION_TEXT_OPERATIONS:
        raise ValueError("operation is not a Drawing dimension-text operation")
    repetition_text = ""
    if operation == "insert_repetition_prefix":
        count = values["repeat_count"]
        if type(count) is not int or not 1 <= count <= MAX_DRAWING_REPETITION_COUNT:
            _error(
                "Drawing repetition prefix repeat_count must be an integer from 1 "
                "to 9999.",
                "NATIVE_DRAWING_DIMENSION_TEXT_PARAMETERS_INVALID",
            )
        repetition_text = str(count)
    return DrawingDimensionTextSpec(
        operation=operation,
        repetition_text=repetition_text,
    )


def _resolve_page(document: Any, raw: Any) -> tuple[Any, dict[str, Any]]:
    exact = exact_drawing_mapping(
        raw,
        _PAGE_FIELDS,
        "page",
        family="dimension text",
        error_code="NATIVE_DRAWING_DIMENSION_TEXT_PARAMETERS_INVALID",
    )
    page = resolve_object(
        document,
        {
            "document_uid": str(document.Uid),
            "object_name": str(exact["object_name"]),
        },
        expected_types=("TechDraw::DrawPage",),
    )
    state = drawing_page_state(page)
    if str(exact["expected_state_sha256"]) != state["state_sha256"]:
        _error(
            "The exact Drawing page changed after it was inspected.",
            "NATIVE_DRAWING_DIMENSION_TEXT_PAGE_STALE",
            repair={"current_state_sha256": state["state_sha256"]},
        )
    return page, state


def _resolve_dimensions(
    document: Any,
    page: Any,
    raw: Any,
) -> tuple[tuple[Any, ...], tuple[dict[str, Any], ...]]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or not (
        1 <= len(raw) <= MAX_DRAWING_DIMENSION_TEXT_TARGETS
    ):
        _error(
            "Drawing dimension text changes require 1 to 64 exact dimensions.",
            "NATIVE_DRAWING_DIMENSION_TEXT_PARAMETERS_INVALID",
        )
    dimensions = []
    states = []
    names = []
    for item in raw:
        exact = exact_drawing_mapping(
            item,
            _DIMENSION_FIELDS,
            "dimension target",
            family="dimension text",
            error_code="NATIVE_DRAWING_DIMENSION_TEXT_PARAMETERS_INVALID",
        )
        dimension = resolve_object(
            document,
            {
                "document_uid": str(document.Uid),
                "object_name": str(exact["object_name"]),
            },
            expected_types=("TechDraw::DrawViewDimension",),
        )
        try:
            state = drawing_format_state(dimension)
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            _error(
                "The exact Drawing dimension-text target is unavailable: "
                f"{str(exc).strip()}",
                "NATIVE_DRAWING_DIMENSION_TEXT_TARGET_INVALID",
            )
        if (
            dimension.findParentPage() is not page
            or dimension not in tuple(page.Views or ())
        ):
            _error(
                "Every exact dimension-text target must belong to the exact page.",
                "NATIVE_DRAWING_DIMENSION_TEXT_PAGE_MISMATCH",
            )
        if state["target_kind"] != "dimension" or not state["timeline_usable"]:
            _error(
                "Every dimension-text target must be a usable Drawing dimension at "
                "the current History position.",
                "NATIVE_DRAWING_DIMENSION_TEXT_TARGET_UNAVAILABLE",
            )
        expected_hash = str(exact["expected_format_state_sha256"])
        if expected_hash != state["format_state_sha256"]:
            _error(
                f"Drawing dimension {dimension.Name!r} changed after it was inspected.",
                "NATIVE_DRAWING_DIMENSION_TEXT_TARGET_STALE",
                repair={
                    "object_name": str(dimension.Name),
                    "current_format_state_sha256": state["format_state_sha256"],
                },
            )
        if state.get("current_value_truncated"):
            _error(
                f"Drawing dimension {dimension.Name!r} has a format longer than the "
                "supported 512-character exact contract.",
                "NATIVE_DRAWING_DIMENSION_TEXT_TARGET_INVALID",
            )
        dimensions.append(dimension)
        states.append(state)
        names.append(str(dimension.Name))
    if len(names) != len(set(names)):
        _error(
            "A Drawing dimension-text request cannot repeat a target.",
            "NATIVE_DRAWING_DIMENSION_TEXT_TARGETS_INVALID",
        )
    return tuple(dimensions), tuple(states)


def _validate_host(
    dimensions: tuple[Any, ...],
    spec: DrawingDimensionTextSpec,
) -> tuple[dict[str, Any], ...]:
    try:
        import TechDrawGui

        validator = getattr(TechDrawGui, "validateDrawingDimensionText", None)
        if not callable(validator):
            _error(
                "The installed TechDraw runtime cannot validate dimension text.",
                "NATIVE_DRAWING_DIMENSION_TEXT_RUNTIME_UNAVAILABLE",
            )
        plans = normalize_dimension_text_host_plans(
            validator(
                list(dimensions),
                spec.operation,
                spec.repetition_text,
            ),
            operation=spec.operation,
            repetition_text=spec.repetition_text,
        )
    except NativeDrawingError:
        raise
    except Exception as exc:
        _error(
            "TechDraw rejected the exact dimension-text operation: "
            f"{str(exc).strip()}",
            "NATIVE_DRAWING_DIMENSION_TEXT_INVALID",
        )
    names = [str(dimension.Name) for dimension in dimensions]
    if [plan["object_name"] for plan in plans] != names:
        _error(
            "TechDraw's dimension-text plan does not match the ordered exact targets.",
            "NATIVE_DRAWING_DIMENSION_TEXT_RUNTIME_UNAVAILABLE",
        )
    inapplicable = [
        {
            "object_name": plan["object_name"],
            "reason": plan["inapplicable_reason"],
        }
        for plan in plans
        if not plan["changed"]
    ]
    if inapplicable:
        _error(
            "The exact dimension-text batch is inapplicable to one or more targets; "
            "no dimensions were changed.",
            "NATIVE_DRAWING_DIMENSION_TEXT_INAPPLICABLE",
            repair={"inapplicable_targets": inapplicable},
        )
    return tuple(plans)


def prepare_drawing_dimension_text(
    document: Any,
    *,
    operation: str,
    values: Mapping[str, Any],
) -> PreparedDrawingDimensionText:
    spec = _spec(operation, values)
    page, page_state = _resolve_page(document, values["page"])
    dimensions, states = _resolve_dimensions(
        document,
        page,
        values["dimensions"],
    )
    return PreparedDrawingDimensionText(
        page=page,
        dimensions=dimensions,
        spec=spec,
        host_validation=_validate_host(dimensions, spec),
        states_before=states,
        page_state_before=page_state,
        objects_before=tuple(document.Objects),
        page_views_before=tuple(page.Views or ()),
        timeline_before=drawing_timeline_operations(document),
        selection_before=drawing_selection_state(document),
        visibility_before=drawing_visibility_state(document),
    )


def mutate_drawing_dimension_text(
    _document: Any,
    *,
    prepared: PreparedDrawingDimensionText,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedDrawingDimensionText):
        raise TypeError("prepared must be PreparedDrawingDimensionText")
    import TechDrawGui

    try:
        applied = normalize_dimension_text_host_plans(
            TechDrawGui.changeDrawingDimensionText(
                list(prepared.dimensions),
                prepared.spec.operation,
                prepared.spec.repetition_text,
            ),
            operation=prepared.spec.operation,
            repetition_text=prepared.spec.repetition_text,
        )
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_DIMENSION_TEXT_CHANGE_FAILED",
            "TechDraw could not apply the exact dimension-text operation: "
            f"{str(exc).strip()}",
        ) from exc
    if tuple(applied) != prepared.host_validation:
        raise NativeMutationError(
            "NATIVE_DRAWING_DIMENSION_TEXT_CHANGE_FAILED",
            "TechDraw applied dimension text inconsistent with preflight.",
        )
    return NativeMutationDraft(
        value={"prepared": prepared},
        recompute_targets=(*prepared.dimensions, prepared.page),
        changed=tuple(object_identity(item) for item in prepared.dimensions),
    )


def _postcondition_error(message: str) -> None:
    raise NativeMutationError(
        "NATIVE_DRAWING_DIMENSION_TEXT_POSTCONDITION_FAILED",
        message,
    )


def _format_boundary(state: Mapping[str, Any]) -> dict[str, Any]:
    ignored = frozenset(
        {
            "format_state_sha256",
            "current_value",
            "current_value_sha256",
            "current_value_characters",
            "current_value_truncated",
            "rendered_text",
            "rendered_text_sha256",
            "rendered_text_characters",
            "rendered_text_truncated",
            "state_messages",
        }
    )
    return {key: value for key, value in state.items() if key not in ignored}


def _verify_drawing_dimension_text(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedDrawingDimensionText = draft.value["prepared"]
    if (
        tuple(map(drawing_object_key, document.Objects))
        != tuple(map(drawing_object_key, prepared.objects_before))
        or tuple(map(drawing_object_key, tuple(prepared.page.Views or ())))
        != tuple(map(drawing_object_key, prepared.page_views_before))
        or tuple(map(drawing_object_key, drawing_timeline_operations(document)))
        != tuple(map(drawing_object_key, prepared.timeline_before))
    ):
        _postcondition_error(
            "Dimension-text editing changed objects, page membership, or History."
        )
    if drawing_selection_state(document) != prepared.selection_before:
        _postcondition_error("Dimension-text editing changed the human selection.")
    if drawing_visibility_state(document) != prepared.visibility_before:
        _postcondition_error("Dimension-text editing changed object visibility.")
    if drawing_page_state(prepared.page) != prepared.page_state_before:
        _postcondition_error("Dimension-text editing changed the Drawing page.")

    results = []
    for dimension, before, plan in zip(
        prepared.dimensions,
        prepared.states_before,
        prepared.host_validation,
        strict=True,
    ):
        if (
            getattr(dimension, "Document", None) is not document
            or dimension.findParentPage() is not prepared.page
        ):
            _postcondition_error(
                "A dimension-text target left its exact document or page."
            )
        current = drawing_format_state(dimension)
        if _format_boundary(current) != _format_boundary(before):
            _postcondition_error(
                "Dimension-text editing changed a target outside its format text."
            )
        if (
            current["current_value"] != plan["format_spec_after"]
            or current["format_state_sha256"] == before["format_state_sha256"]
        ):
            _postcondition_error(
                "A Drawing dimension did not retain its planned format text."
            )
        results.append(
            {
                "object_name": str(dimension.Name),
                "previous_format_state_sha256": before["format_state_sha256"],
                "format_state_sha256": current["format_state_sha256"],
                "format_spec_before": plan["format_spec_before"],
                "format_spec_after": plan["format_spec_after"],
                "inserted_prefix": plan["inserted_prefix"] or None,
                "decimal_places_before": plan["decimal_places_before"],
                "decimal_places_after": plan["decimal_places_after"],
                "rendered_text": current["rendered_text"],
            }
        )
    return {
        "operation": prepared.spec.operation,
        "changed_count": len(results),
        "dimensions": results,
    }


def verify_drawing_dimension_text(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    try:
        return _verify_drawing_dimension_text(document, draft)
    except NativeMutationError:
        raise
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_DIMENSION_TEXT_POSTCONDITION_FAILED",
            "The Drawing dimension-text operation could not be verified exactly: "
            f"{str(exc).strip()}",
        ) from exc
