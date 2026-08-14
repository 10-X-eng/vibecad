# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact atomic customization of Drawing dimension formats and Balloon text."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Mapping

from VibeCADNativeDrawingDimensionSupport import (
    drawing_dimension_error,
    drawing_object_key,
    drawing_selection_state,
    drawing_timeline_operations,
    drawing_visibility_state,
    exact_drawing_mapping,
)
from VibeCADNativeDrawingFormatState import (
    MAX_DRAWING_FORMAT_CHARACTERS,
    drawing_format_state,
)
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeTargets import object_identity, resolve_object


_TARGET_FIELDS = frozenset(
    {"object_name", "expected_format_state_sha256"}
)


@dataclass(frozen=True, slots=True)
class DrawingFormatSpec:
    operation: str
    target_kind: str
    value: str


@dataclass(frozen=True, slots=True)
class PreparedDrawingFormatChange:
    target: Any
    page: Any
    spec: DrawingFormatSpec
    host_validation: dict[str, str]
    state_before: dict[str, Any]
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


def _spec(operation: str, values: Mapping[str, Any]) -> DrawingFormatSpec:
    if operation == "set_dimension_format":
        target_kind = "dimension"
        value = values["format_spec"]
    elif operation == "set_balloon_text":
        target_kind = "balloon"
        value = values["text"]
    else:
        raise ValueError("operation is not a Drawing format operation")
    if not isinstance(value, str) or len(value) > MAX_DRAWING_FORMAT_CHARACTERS:
        _error(
            "Drawing format text must contain at most 512 characters.",
            "NATIVE_DRAWING_FORMAT_PARAMETERS_INVALID",
        )
    return DrawingFormatSpec(
        operation=operation,
        target_kind=target_kind,
        value=value,
    )


def _resolve_target(
    document: Any,
    spec: DrawingFormatSpec,
    raw_target: Any,
) -> tuple[Any, Any, dict[str, Any]]:
    exact = exact_drawing_mapping(
        raw_target,
        _TARGET_FIELDS,
        f"{spec.target_kind} format target",
        family="format customization",
        error_code="NATIVE_DRAWING_FORMAT_PARAMETERS_INVALID",
    )
    expected_type = (
        "TechDraw::DrawViewDimension"
        if spec.target_kind == "dimension"
        else "TechDraw::DrawViewBalloon"
    )
    target = resolve_object(
        document,
        {
            "document_uid": str(document.Uid),
            "object_name": str(exact["object_name"]),
        },
        expected_types=(expected_type,),
    )
    try:
        state = drawing_format_state(target)
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        _error(
            "The exact Drawing format target is unavailable: "
            f"{str(exc).strip()}",
            "NATIVE_DRAWING_FORMAT_TARGET_INVALID",
        )
    if state["target_kind"] != spec.target_kind:
        _error(
            "The Drawing format operation does not match the selected target type.",
            "NATIVE_DRAWING_FORMAT_TARGET_TYPE_INVALID",
            repair={"target_kind": state["target_kind"]},
        )
    if (
        str(exact["expected_format_state_sha256"])
        != state["format_state_sha256"]
    ):
        _error(
            "The exact Drawing format target changed after it was inspected.",
            "NATIVE_DRAWING_FORMAT_TARGET_STALE",
            repair={
                "current_format_state_sha256": state[
                    "format_state_sha256"
                ]
            },
        )
    if not state["timeline_usable"]:
        _error(
            "The exact Drawing format target is unavailable at the current History position.",
            "NATIVE_DRAWING_FORMAT_TARGET_UNAVAILABLE",
        )
    page = target.findParentPage()
    if (
        page is None
        or getattr(page, "Document", None) is not document
        or target not in tuple(page.Views or ())
    ):
        _error(
            "The exact Drawing format target is not attached to its live page.",
            "NATIVE_DRAWING_FORMAT_PAGE_MISMATCH",
        )
    if state["current_value_sha256"] == hashlib.sha256(
        spec.value.encode("utf-8")
    ).hexdigest():
        _error(
            "The Drawing target already has the requested complete format text.",
            "NATIVE_DRAWING_NO_CHANGE",
        )
    return target, page, state


def _validate_host(target: Any, spec: DrawingFormatSpec) -> dict[str, str]:
    try:
        import TechDrawGui

        validator = getattr(
            TechDrawGui,
            "validateDrawingFormatCustomization",
            None,
        )
        if not callable(validator):
            _error(
                "The installed TechDraw runtime cannot validate format customization.",
                "NATIVE_DRAWING_FORMAT_RUNTIME_UNAVAILABLE",
            )
        raw = validator(target, spec.value)
    except NativeDrawingError:
        raise
    except Exception as exc:
        _error(
            "TechDraw rejected the requested complete format: "
            f"{str(exc).strip()}",
            "NATIVE_DRAWING_FORMAT_INVALID",
            repair=(
                {
                    "accepted_placeholders": [
                        "%f",
                        "%.2f",
                        "%g",
                        "%w",
                        "%r",
                    ]
                }
                if spec.target_kind == "dimension"
                else None
            ),
        )
    if not isinstance(raw, Mapping) or frozenset(raw) != frozenset(
        {"target_kind", "value", "preview"}
    ):
        _error(
            "TechDraw returned malformed format validation data.",
            "NATIVE_DRAWING_FORMAT_RUNTIME_UNAVAILABLE",
        )
    result = {
        "target_kind": str(raw["target_kind"] or ""),
        "value": str(raw["value"] or ""),
        "preview": str(raw["preview"] or ""),
    }
    if (
        result["target_kind"] != spec.target_kind
        or result["value"] != spec.value
        or len(result["preview"]) > 2048
    ):
        _error(
            "TechDraw's format validation result does not match the exact request.",
            "NATIVE_DRAWING_FORMAT_RUNTIME_UNAVAILABLE",
        )
    return result


def prepare_drawing_format_change(
    document: Any,
    *,
    operation: str,
    values: Mapping[str, Any],
) -> PreparedDrawingFormatChange:
    spec = _spec(operation, values)
    raw_target = (
        values["dimension"]
        if spec.target_kind == "dimension"
        else values["balloon"]
    )
    target, page, state = _resolve_target(document, spec, raw_target)
    return PreparedDrawingFormatChange(
        target=target,
        page=page,
        spec=spec,
        host_validation=_validate_host(target, spec),
        state_before=state,
        page_state_before=drawing_page_state(page),
        objects_before=tuple(document.Objects),
        page_views_before=tuple(page.Views or ()),
        timeline_before=drawing_timeline_operations(document),
        selection_before=drawing_selection_state(document),
        visibility_before=drawing_visibility_state(document),
    )


def mutate_drawing_format(
    _document: Any,
    *,
    prepared: PreparedDrawingFormatChange,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedDrawingFormatChange):
        raise TypeError("prepared must be a PreparedDrawingFormatChange")
    import TechDrawGui

    try:
        returned = TechDrawGui.applyDrawingFormatCustomization(
            prepared.target,
            prepared.spec.value,
        )
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_FORMAT_CHANGE_FAILED",
            "TechDraw could not apply the exact complete format: "
            f"{str(exc).strip()}",
        ) from exc
    if dict(returned) != prepared.host_validation:
        raise NativeMutationError(
            "NATIVE_DRAWING_FORMAT_CHANGE_FAILED",
            "TechDraw applied a format result inconsistent with preflight.",
        )
    return NativeMutationDraft(
        value={"prepared": prepared},
        recompute_targets=(prepared.target, prepared.page),
        changed=(object_identity(prepared.target),),
    )


def _postcondition_error(message: str) -> None:
    raise NativeMutationError(
        "NATIVE_DRAWING_FORMAT_POSTCONDITION_FAILED",
        message,
    )


def _boundary(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
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
    return {
        key: value for key, value in before.items() if key not in ignored
    } == {key: value for key, value in after.items() if key not in ignored}


def _verify_drawing_format(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedDrawingFormatChange = draft.value["prepared"]
    if (
        getattr(prepared.target, "Document", None) is not document
        or prepared.target.findParentPage() is not prepared.page
        or tuple(map(drawing_object_key, document.Objects))
        != tuple(map(drawing_object_key, prepared.objects_before))
        or tuple(map(drawing_object_key, tuple(prepared.page.Views or ())))
        != tuple(map(drawing_object_key, prepared.page_views_before))
        or tuple(map(drawing_object_key, drawing_timeline_operations(document)))
        != tuple(map(drawing_object_key, prepared.timeline_before))
    ):
        _postcondition_error(
            "Format customization changed objects, page membership, or History."
        )
    if drawing_selection_state(document) != prepared.selection_before:
        _postcondition_error("Format customization changed the human selection.")
    if drawing_visibility_state(document) != prepared.visibility_before:
        _postcondition_error("Format customization changed object visibility.")
    if drawing_page_state(prepared.page) != prepared.page_state_before:
        _postcondition_error("Format customization changed the Drawing page definition.")

    state = drawing_format_state(prepared.target)
    if not _boundary(prepared.state_before, state):
        _postcondition_error(
            "Format customization changed the target outside its complete format text."
        )
    if (
        state["current_value"] != prepared.spec.value
        or state["current_value_characters"] != len(prepared.spec.value)
        or state["format_state_sha256"]
        == prepared.state_before["format_state_sha256"]
    ):
        _postcondition_error(
            "The Drawing target did not retain the requested complete format text."
        )
    return {
        "operation": prepared.spec.operation,
        "format_target": state,
        "host_preview": prepared.host_validation["preview"],
    }


def verify_drawing_format(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    try:
        return _verify_drawing_format(document, draft)
    except NativeMutationError:
        raise
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_FORMAT_POSTCONDITION_FAILED",
            "The Drawing format change could not be verified exactly: "
            f"{str(exc).strip()}",
        ) from exc
