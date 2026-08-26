# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact transactional Drawing rich-text annotation creation."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeDrawingDimensionSupport import (
    drawing_object_key,
    drawing_position_within_page_bounds,
    drawing_selection_state,
    drawing_timeline_operations,
    drawing_visibility_state,
    exact_drawing_mapping,
)
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingRichAnnotationState import (
    drawing_rich_annotation_owner_state,
    drawing_rich_annotation_state,
)
from VibeCADNativeDrawingState import (
    drawing_page_invariants,
    drawing_page_state,
)
from VibeCADNativeLabel import matches_preferred_document_label
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeTargets import object_identity, resolve_object


_LINE_STYLES = frozenset(
    {"no_line", "continuous", "dash", "dot", "dash_dot", "dash_dot_dot"}
)
_HOST_ERROR_CODE = "NATIVE_DRAWING_RICH_ANNOTATION_RUNTIME_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class DrawingRichAnnotationSpec:
    operation: str
    content_kind: str
    content: str
    label: str
    placement: dict[str, float]
    width: dict[str, Any]
    frame: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PreparedDrawingRichAnnotationTarget:
    page: Any
    page_state_before: dict[str, Any]
    page_invariants_before: dict[str, Any]
    page_views_before: tuple[Any, ...]
    owner: Any | None
    owner_state_before: dict[str, Any] | None
    objects_before: tuple[Any, ...]
    timeline_before: tuple[Any, ...]
    selection_before: dict[str, Any]
    visibility_before: tuple[tuple[Any, bool], ...]


@dataclass(frozen=True, slots=True)
class PreparedDrawingRichAnnotation:
    target: PreparedDrawingRichAnnotationTarget
    spec: DrawingRichAnnotationSpec
    host_plan: dict[str, Any]


def _error(
    message: str,
    code: str,
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    raise NativeDrawingError(message, error_code=code, repair=repair)


def _finite(
    value: Any,
    noun: str,
    *,
    minimum: float,
    maximum: float,
    strictly_positive: bool = False,
    error_code: str = "NATIVE_DRAWING_RICH_ANNOTATION_PARAMETERS_INVALID",
) -> float:
    if isinstance(value, bool):
        _error(
            f"Drawing rich annotation {noun} must be numeric.",
            error_code,
        )
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeDrawingError(
            f"Drawing rich annotation {noun} must be numeric.",
            error_code=error_code,
        ) from exc
    if (
        not math.isfinite(result)
        or not minimum <= result <= maximum
        or (strictly_positive and result <= 0.0)
    ):
        _error(
            f"Drawing rich annotation {noun} is outside its documented range.",
            error_code,
        )
    return result


def _boolean(value: Any, noun: str, error_code: str) -> bool:
    if not isinstance(value, bool):
        _error(
            f"Drawing rich annotation {noun} must be true or false.",
            error_code,
        )
    return value


def _width(
    value: Any,
    *,
    error_code: str = "NATIVE_DRAWING_RICH_ANNOTATION_PARAMETERS_INVALID",
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _error(
            "Drawing rich annotation width must select automatic or fixed mode.",
            error_code,
        )
    mode = str(value.get("mode") or "")
    expected = frozenset({"mode"}) if mode == "automatic" else frozenset({"mode", "value_mm"})
    exact = exact_drawing_mapping(
        value,
        expected,
        "width",
        family="rich annotation",
        error_code=error_code,
    )
    if mode == "automatic":
        return {"mode": "automatic"}
    if mode != "fixed":
        _error(
            "Drawing rich annotation width mode must be automatic or fixed.",
            error_code,
        )
    return {
        "mode": "fixed",
        "value_mm": _finite(
            exact["value_mm"],
            "fixed width",
            minimum=0.0,
            maximum=1_000_000.0,
            strictly_positive=True,
            error_code=error_code,
        ),
    }


def _requested_width(value: Any) -> dict[str, Any]:
    if isinstance(value, str) and value in {"auto", "automatic"}:
        return {"mode": "automatic"}
    if type(value) in {int, float}:
        return {
            "mode": "fixed",
            "value_mm": _finite(
                value,
                "fixed width",
                minimum=0.0,
                maximum=1_000_000.0,
                strictly_positive=True,
            ),
        }
    _error(
        "Drawing note width must be automatic or a positive number.",
        "NATIVE_DRAWING_RICH_ANNOTATION_PARAMETERS_INVALID",
    )


def _color(
    value: Any,
    *,
    error_code: str = "NATIVE_DRAWING_RICH_ANNOTATION_PARAMETERS_INVALID",
) -> dict[str, float]:
    exact = exact_drawing_mapping(
        value,
        frozenset({"red", "green", "blue"}),
        "frame color",
        family="rich annotation",
        error_code=error_code,
    )
    return {
        name: _finite(
            exact[name],
            f"frame color {name}",
            minimum=0.0,
            maximum=1.0,
            error_code=error_code,
        )
        for name in ("red", "green", "blue")
    }


def _frame(
    value: Any,
    *,
    error_code: str = "NATIVE_DRAWING_RICH_ANNOTATION_PARAMETERS_INVALID",
) -> dict[str, Any]:
    exact = exact_drawing_mapping(
        value,
        frozenset({"visible", "line_width_mm", "line_style", "color_rgb"}),
        "frame",
        family="rich annotation",
        error_code=error_code,
    )
    style = str(exact["line_style"] or "")
    if style not in _LINE_STYLES:
        _error(
            "Drawing rich annotation frame line style is unsupported.",
            error_code,
            repair={"accepted_line_styles": sorted(_LINE_STYLES)},
        )
    return {
        "visible": _boolean(exact["visible"], "frame visibility", error_code),
        "line_width_mm": _finite(
            exact["line_width_mm"],
            "frame line width",
            minimum=0.0,
            maximum=100.0,
            error_code=error_code,
        ),
        "line_style": style,
        "color_rgb": _color(exact["color_rgb"], error_code=error_code),
    }


def _requested_frame(
    value: Any,
    defaults: Mapping[str, Any],
) -> dict[str, Any]:
    if value is None:
        return dict(defaults)
    if not isinstance(value, Mapping) or not set(value) <= {
        "visible",
        "line_width_mm",
        "line_style",
        "color_rgb",
    }:
        _error(
            "Drawing note frame fields are invalid.",
            "NATIVE_DRAWING_RICH_ANNOTATION_PARAMETERS_INVALID",
        )
    merged = {**defaults, **value}
    return _frame(merged)


def _same_number(left: Any, right: Any, *, tolerance: float = 1.0e-9) -> bool:
    return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=tolerance)


def _same_placement(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return all(_same_number(left[name], right[name]) for name in ("x_mm", "y_mm"))


def _same_width(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return left.get("mode") == right.get("mode") and (
        left.get("mode") == "automatic"
        or _same_number(left.get("value_mm"), right.get("value_mm"))
    )


def _same_frame(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return (
        left.get("visible") is right.get("visible")
        and left.get("line_style") == right.get("line_style")
        and _same_number(left.get("line_width_mm"), right.get("line_width_mm"))
        and all(
            _same_number(
                left["color_rgb"][name],
                right["color_rgb"][name],
                tolerance=1.0e-6,
            )
            for name in ("red", "green", "blue")
        )
    )


def _spec(
    operation: str,
    values: Mapping[str, Any],
    defaults: Mapping[str, Any],
) -> DrawingRichAnnotationSpec:
    label = str(values["label"] or "")
    if label != label.strip() or not 1 <= len(label) <= 160:
        _error(
            "A Drawing rich annotation label requires 1 to 160 non-padding characters.",
            "NATIVE_DRAWING_RICH_ANNOTATION_PARAMETERS_INVALID",
        )
    placement_value = exact_drawing_mapping(
        values["placement_on_page_mm"],
        frozenset({"x_mm", "y_mm"}),
        "placement",
        family="rich annotation",
        error_code="NATIVE_DRAWING_RICH_ANNOTATION_PARAMETERS_INVALID",
    )
    placement = {
        name: _finite(
            placement_value[name],
            f"placement {name}",
            minimum=-1_000_000.0,
            maximum=1_000_000.0,
        )
        for name in ("x_mm", "y_mm")
    }
    if operation == "plain_text":
        content_kind = "plain_text"
        content = str(values["text"] or "")
    elif operation == "safe_html":
        content_kind = "safe_html"
        content = str(values["html"] or "")
    else:
        raise ValueError("operation is not a Drawing rich annotation creation")
    if not content or not content.strip():
        _error(
            "A Drawing rich annotation requires visible non-whitespace content.",
            "NATIVE_DRAWING_RICH_ANNOTATION_CONTENT_INVALID",
        )
    return DrawingRichAnnotationSpec(
        operation=operation,
        content_kind=content_kind,
        content=content,
        label=label,
        placement=placement,
        width=_requested_width(values.get("width", "automatic")),
        frame=_requested_frame(values.get("frame"), defaults["frame"]),
    )


def _require_usable(document: Any, obj: Any, noun: str) -> None:
    checker = getattr(document, "isObjectUsableAtCurrentTimelinePosition", None)
    if callable(checker) and not bool(checker(obj)):
        _error(
            f"The exact {noun} is not usable at the current History position.",
            "NATIVE_DRAWING_HISTORY_TARGET_UNAVAILABLE",
        )


def _target(
    document: Any,
    *,
    page_target: Any,
    owner_target: Any,
) -> PreparedDrawingRichAnnotationTarget:
    page_exact = exact_drawing_mapping(
        page_target,
        frozenset({"object_name", "expected_state_sha256"}),
        "page target",
        family="rich annotation",
        error_code="NATIVE_DRAWING_RICH_ANNOTATION_PARAMETERS_INVALID",
    )
    page = resolve_object(
        document,
        {"document_uid": str(document.Uid), "object_name": page_exact["object_name"]},
        expected_types=("TechDraw::DrawPage",),
    )
    page_state = drawing_page_state(page)
    if str(page_exact["expected_state_sha256"]) != page_state["state_sha256"]:
        _error(
            "The exact Drawing page changed after it was inspected.",
            "NATIVE_DRAWING_PAGE_STALE",
            repair={"current_state_sha256": page_state["state_sha256"]},
        )
    _require_usable(document, page, "Drawing page")

    if owner_target == "page":
        owner = None
        owner_state = None
    elif isinstance(owner_target, Mapping):
        owner_exact = exact_drawing_mapping(
            owner_target,
            frozenset({"object_name", "expected_owner_state_sha256"}),
            "owner target",
            family="rich annotation",
            error_code="NATIVE_DRAWING_RICH_ANNOTATION_PARAMETERS_INVALID",
        )
        owner = resolve_object(
            document,
            {"document_uid": str(document.Uid), "object_name": owner_exact["object_name"]},
            expected_types=("TechDraw::DrawView",),
        )
        owner_state = drawing_rich_annotation_owner_state(owner, page=page)
        if str(owner_exact["expected_owner_state_sha256"]) != owner_state["owner_state_sha256"]:
            _error(
                "The exact Drawing annotation owner changed after it was inspected.",
                "NATIVE_DRAWING_RICH_ANNOTATION_OWNER_STALE",
                repair={"current_owner_state_sha256": owner_state["owner_state_sha256"]},
            )
        _require_usable(document, owner, "Drawing annotation owner")
    else:
        _error(
            "Drawing note owner must be page or one exact Drawing view.",
            "NATIVE_DRAWING_RICH_ANNOTATION_PARAMETERS_INVALID",
        )
    return PreparedDrawingRichAnnotationTarget(
        page=page,
        page_state_before=page_state,
        page_invariants_before=drawing_page_invariants(page),
        page_views_before=tuple(page.Views or ()),
        owner=owner,
        owner_state_before=owner_state,
        objects_before=tuple(document.Objects),
        timeline_before=drawing_timeline_operations(document),
        selection_before=drawing_selection_state(document),
        visibility_before=drawing_visibility_state(document),
    )


def _normalize_host_content(raw: Any) -> dict[str, Any]:
    fields = frozenset(
        {
        "input_kind",
        "stored_html_sha256",
        "plain_text_sha256",
        "plain_text_preview",
        "plain_text_characters",
        "block_count",
        "fragment_count",
        "link_count",
        "has_rich_formatting",
        }
    )
    exact = exact_drawing_mapping(
        raw,
        fields,
        "host content plan",
        family="rich annotation",
        error_code=_HOST_ERROR_CODE,
    )
    for name in (
        "input_kind",
        "stored_html_sha256",
        "plain_text_sha256",
        "plain_text_preview",
    ):
        if not isinstance(exact[name], str):
            _error(
                f"TechDraw returned a non-string rich annotation {name}.",
                _HOST_ERROR_CODE,
            )
    counts: dict[str, int] = {}
    for name in (
        "plain_text_characters",
        "block_count",
        "fragment_count",
        "link_count",
    ):
        value = exact[name]
        if isinstance(value, bool) or not isinstance(value, int):
            _error(
                f"TechDraw returned a non-integer rich annotation {name}.",
                _HOST_ERROR_CODE,
            )
        counts[name] = value
    result = {
        "input_kind": exact["input_kind"],
        "stored_html_sha256": exact["stored_html_sha256"],
        "plain_text_sha256": exact["plain_text_sha256"],
        "plain_text_preview": exact["plain_text_preview"],
        **counts,
        "has_rich_formatting": _boolean(
            exact["has_rich_formatting"],
            "host formatting flag",
            _HOST_ERROR_CODE,
        ),
    }
    if (
        result["input_kind"] not in {"plain_text", "safe_html"}
        or any(
            len(result[name]) != 64
            or any(character not in "0123456789abcdef" for character in result[name])
            for name in ("stored_html_sha256", "plain_text_sha256")
        )
        or len(result["plain_text_preview"]) > 160
        or not 0 <= result["plain_text_characters"] <= 8192
        or not 0 <= result["block_count"] <= 256
        or not 0 <= result["fragment_count"] <= 2048
        or not 0 <= result["link_count"] <= 128
    ):
        _error(
            "TechDraw returned invalid rich annotation content planning data.",
            _HOST_ERROR_CODE,
        )
    return result


def _normalize_host_plan(raw: Any) -> dict[str, Any]:
    fields = frozenset(
        {
        "page_name",
        "owner",
        "object_name",
        "label",
        "content",
        "placement_on_page_mm",
        "width",
        "frame",
        }
    )
    exact = exact_drawing_mapping(
        raw,
        fields,
        "host plan",
        family="rich annotation",
        error_code=_HOST_ERROR_CODE,
    )
    owner_raw = exact["owner"]
    if not isinstance(owner_raw, Mapping):
        _error(
            "TechDraw returned a malformed rich annotation owner plan.",
            _HOST_ERROR_CODE,
        )
    owner_kind = owner_raw.get("kind")
    owner_fields = (
        frozenset({"kind"})
        if owner_kind == "page"
        else frozenset({"kind", "object_name"})
        if owner_kind == "view"
        else frozenset()
    )
    if not owner_fields:
        _error(
            "TechDraw returned an invalid rich annotation owner kind.",
            _HOST_ERROR_CODE,
        )
    owner_exact = exact_drawing_mapping(
        owner_raw,
        owner_fields,
        "host owner plan",
        family="rich annotation",
        error_code=_HOST_ERROR_CODE,
    )
    placement_exact = exact_drawing_mapping(
        exact["placement_on_page_mm"],
        frozenset({"x_mm", "y_mm"}),
        "host placement plan",
        family="rich annotation",
        error_code=_HOST_ERROR_CODE,
    )
    for name in ("page_name", "object_name", "label"):
        if not isinstance(exact[name], str):
            _error(
                f"TechDraw returned a non-string rich annotation {name}.",
                _HOST_ERROR_CODE,
            )
    for name, value in owner_exact.items():
        if not isinstance(value, str):
            _error(
                f"TechDraw returned a non-string rich annotation owner {name}.",
                _HOST_ERROR_CODE,
            )
    result = {
        "page_name": exact["page_name"],
        "owner": dict(owner_exact),
        "object_name": exact["object_name"],
        "label": exact["label"],
        "content": _normalize_host_content(exact["content"]),
        "placement_on_page_mm": {
            name: _finite(
                placement_exact[name],
                f"host placement {name}",
                minimum=-1_000_000.0,
                maximum=1_000_000.0,
                error_code=_HOST_ERROR_CODE,
            )
            for name in ("x_mm", "y_mm")
        },
        "width": _width(exact["width"], error_code=_HOST_ERROR_CODE),
        "frame": _frame(exact["frame"], error_code=_HOST_ERROR_CODE),
    }
    if (
        not result["page_name"]
        or not result["object_name"]
        or not result["label"]
        or result["owner"].get("kind") not in {"page", "view"}
    ):
        _error(
            "TechDraw returned invalid rich annotation identities.",
            _HOST_ERROR_CODE,
        )
    return result


def _host_plan(
    target: PreparedDrawingRichAnnotationTarget,
    spec: DrawingRichAnnotationSpec,
    *,
    apply: bool,
) -> tuple[dict[str, Any], Any | None]:
    try:
        import TechDrawGui

        function = (
            TechDrawGui.createDrawingRichAnnotation
            if apply
            else TechDrawGui.validateDrawingRichAnnotation
        )
        width_mm = -1.0 if spec.width["mode"] == "automatic" else spec.width["value_mm"]
        color = spec.frame["color_rgb"]
        raw = function(
            target.page,
            target.owner,
            spec.content_kind,
            spec.content,
            spec.label,
            spec.placement["x_mm"],
            spec.placement["y_mm"],
            width_mm,
            spec.frame["visible"],
            spec.frame["line_width_mm"],
            spec.frame["line_style"],
            color["red"],
            color["green"],
            color["blue"],
        )
    except NativeDrawingError:
        raise
    except Exception as exc:
        if apply:
            raise NativeMutationError(
                "NATIVE_DRAWING_RICH_ANNOTATION_CREATE_FAILED",
                f"TechDraw rejected the rich annotation: {str(exc).strip()}",
            ) from exc
        _error(
            f"TechDraw rejected the rich annotation: {str(exc).strip()}",
            "NATIVE_DRAWING_RICH_ANNOTATION_CONTENT_INVALID",
            repair={
                "accepted_content": "plain_text or bounded resource-free safe_html",
            },
        )
    annotation = raw.get("annotation") if apply and isinstance(raw, Mapping) else None
    if apply and isinstance(raw, Mapping):
        raw = {name: value for name, value in raw.items() if name != "annotation"}
    return _normalize_host_plan(raw), annotation


def drawing_rich_annotation_defaults_state() -> dict[str, Any]:
    try:
        import TechDrawGui

        raw = TechDrawGui.drawingRichAnnotationDefaults()
    except Exception as exc:
        _error(
            f"TechDraw rich annotation defaults are unavailable: {str(exc).strip()}",
            "NATIVE_DRAWING_RICH_ANNOTATION_RUNTIME_UNAVAILABLE",
        )
    exact = exact_drawing_mapping(
        raw,
        frozenset({"width", "frame"}),
        "host defaults",
        family="rich annotation",
        error_code=_HOST_ERROR_CODE,
    )
    return {
        "width": _width(exact["width"], error_code=_HOST_ERROR_CODE),
        "frame": _frame(exact["frame"], error_code=_HOST_ERROR_CODE),
    }


def prepare_drawing_rich_annotation(
    document: Any,
    *,
    operation: str,
    values: Mapping[str, Any],
) -> PreparedDrawingRichAnnotation:
    defaults = drawing_rich_annotation_defaults_state()
    spec = _spec(operation, values, defaults)
    target = _target(
        document,
        page_target=values["page"],
        owner_target=values.get("owner", "page"),
    )
    drawing_position_within_page_bounds(
        target.page,
        spec.placement,
        noun="note",
        error_code="NATIVE_DRAWING_RICH_ANNOTATION_PLACEMENT_INVALID",
    )
    plan, _annotation = _host_plan(target, spec, apply=False)
    expected_owner = (
        {"kind": "page"}
        if target.owner is None
        else {"kind": "view", "object_name": str(target.owner.Name)}
    )
    matches = {
        "page": plan["page_name"] == str(target.page.Name),
        "owner": plan["owner"] == expected_owner,
        "content kind": plan["content"]["input_kind"] == spec.content_kind,
        "placement": _same_placement(plan["placement_on_page_mm"], spec.placement),
        "width": _same_width(plan["width"], spec.width),
        "frame": _same_frame(plan["frame"], spec.frame),
    }
    mismatch = next((name for name, matches_value in matches.items() if not matches_value), None)
    if mismatch is not None:
        _error(
            f"TechDraw's rich annotation plan does not match the requested {mismatch}.",
            "NATIVE_DRAWING_RICH_ANNOTATION_RUNTIME_UNAVAILABLE",
        )
    return PreparedDrawingRichAnnotation(target=target, spec=spec, host_plan=plan)


def mutate_drawing_rich_annotation(
    document: Any,
    *,
    prepared: PreparedDrawingRichAnnotation,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedDrawingRichAnnotation):
        raise TypeError("prepared must be a PreparedDrawingRichAnnotation")
    applied, annotation = _host_plan(prepared.target, prepared.spec, apply=True)
    if applied != prepared.host_plan or annotation is None:
        raise NativeMutationError(
            "NATIVE_DRAWING_RICH_ANNOTATION_CREATE_FAILED",
            "TechDraw created a rich annotation inconsistent with preflight.",
        )
    if (
        getattr(annotation, "Document", None) is not document
        or not annotation.isDerivedFrom("TechDraw::DrawRichAnno")
    ):
        raise NativeMutationError(
            "NATIVE_DRAWING_RICH_ANNOTATION_CREATE_FAILED",
            "TechDraw did not create the requested rich annotation type.",
        )
    return NativeMutationDraft(
        value={"prepared": prepared, "annotation": annotation},
        recompute_targets=(),
        created=(object_identity(annotation),),
    )


def _postcondition(message: str) -> None:
    raise NativeMutationError(
        "NATIVE_DRAWING_RICH_ANNOTATION_POSTCONDITION_FAILED",
        message,
    )


def verify_drawing_rich_annotation(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedDrawingRichAnnotation = draft.value["prepared"]
    annotation = draft.value["annotation"]
    target = prepared.target
    before_keys = {drawing_object_key(obj) for obj in target.objects_before}
    new_objects = tuple(
        obj for obj in document.Objects if drawing_object_key(obj) not in before_keys
    )
    if tuple(map(drawing_object_key, new_objects)) != (drawing_object_key(annotation),):
        _postcondition("Rich annotation creation changed objects outside its result.")
    if tuple(target.page.Views or ()) != (*target.page_views_before, annotation):
        _postcondition("Rich annotation creation did not append one exact page view.")
    if drawing_timeline_operations(document) != (*target.timeline_before, annotation):
        _postcondition("Rich annotation creation was not one exact History operation.")
    expected_page = dict(target.page_invariants_before)
    expected_page["view_names"] = [
        *target.page_invariants_before["view_names"],
        str(annotation.Name),
    ]
    if drawing_page_invariants(target.page) != expected_page:
        _postcondition("Rich annotation creation changed unrelated Drawing page state.")
    if target.owner is not None and drawing_rich_annotation_owner_state(
        target.owner, page=target.page
    ) != target.owner_state_before:
        _postcondition("Rich annotation creation changed its owner view definition.")
    if drawing_selection_state(document) != target.selection_before:
        _postcondition("Rich annotation creation changed the human selection.")
    if tuple(
        (obj, bool(obj.ViewObject.Visibility))
        for obj, _visible in target.visibility_before
    ) != target.visibility_before:
        _postcondition("Rich annotation creation changed existing visibility.")

    state = drawing_rich_annotation_state(annotation)
    expected_owner = (
        {"kind": "page"}
        if target.owner is None
        else {
            "kind": "view",
            "object_name": str(target.owner.Name),
            "type_id": str(target.owner.TypeId),
            "owner_state_sha256": target.owner_state_before["owner_state_sha256"],
        }
    )
    checks = (
        ("label", matches_preferred_document_label(state["label"], prepared.spec.label)),
        ("page", state["page_name"] == str(target.page.Name)),
        ("owner", state["owner"] == expected_owner),
        ("content", all(
            state["content"][name] == prepared.host_plan["content"][name]
            for name in (
                "stored_html_sha256",
                "plain_text_sha256",
                "plain_text_preview",
                "plain_text_characters",
                "block_count",
                "fragment_count",
                "link_count",
                "has_rich_formatting",
            )
        )),
        (
            "placement",
            _same_placement(
                state["placement_on_page_mm"],
                prepared.host_plan["placement_on_page_mm"],
            ),
        ),
        ("width", _same_width(state["width"], prepared.host_plan["width"])),
        ("frame", _same_frame(state["frame"], prepared.host_plan["frame"])),
        ("origin policy", state["origin_centered"] is False),
        ("History availability", state["timeline_usable"]),
        ("validity", state["valid"]),
    )
    mismatch = next((name for name, matches in checks if not matches), None)
    if mismatch is not None:
        _postcondition(
            f"The created Drawing rich annotation does not match its requested {mismatch}."
        )
    page_state = drawing_page_state(target.page)
    if page_state["view_count"] != target.page_state_before["view_count"] + 1:
        _postcondition("The Drawing page did not retain the new rich annotation.")
    return {
        "page": {
            "object_name": page_state["object_name"],
            "state_sha256": page_state["state_sha256"],
            "view_count": page_state["view_count"],
        },
        "annotation": state,
        "next": {
            "tool": "inspect.query",
            "operation": "drawing_document",
            "page_name": str(target.page.Name),
        },
    }
