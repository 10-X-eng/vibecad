# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact transactional Drawing Leader Line creation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Mapping

from VibeCADNativeDrawingDimensionSupport import (
    drawing_object_key,
    drawing_selection_state,
    drawing_timeline_operations,
    drawing_visibility_state,
    exact_drawing_mapping,
)
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingLeaderState import (
    MAX_DRAWING_LEADER_POINTS,
    drawing_leader_owner_state,
    drawing_leader_state,
)
from VibeCADNativeDrawingState import drawing_page_invariants, drawing_page_state
from VibeCADNativeLabel import matches_preferred_document_label
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeTargets import object_identity, resolve_object


_ARROWS = frozenset(
    {
        "filled_arrow",
        "open_arrow",
        "tick",
        "dot",
        "open_circle",
        "fork",
        "filled_triangle",
        "none",
    }
)
_LINE_STYLES = frozenset(
    {"no_line", "continuous", "dash", "dot", "dash_dot", "dash_dot_dot"}
)
_HOST_ERROR_CODE = "NATIVE_DRAWING_LEADER_RUNTIME_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class DrawingLeaderSpec:
    operation: str
    points: tuple[dict[str, float], ...]
    label: str
    symbols: dict[str, str]
    behavior: dict[str, bool]
    line: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PreparedDrawingLeaderTarget:
    page: Any
    page_state_before: dict[str, Any]
    page_invariants_before: dict[str, Any]
    page_views_before: tuple[Any, ...]
    owner: Any
    owner_state_before: dict[str, Any]
    objects_before: tuple[Any, ...]
    timeline_before: tuple[Any, ...]
    selection_before: dict[str, Any]
    visibility_before: tuple[tuple[Any, bool], ...]


@dataclass(frozen=True, slots=True)
class PreparedDrawingLeader:
    target: PreparedDrawingLeaderTarget
    spec: DrawingLeaderSpec
    host_plan: dict[str, Any]


def _error(
    message: str,
    code: str,
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    raise NativeDrawingError(message, error_code=code, repair=repair)


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _finite(
    value: Any,
    noun: str,
    *,
    minimum: float,
    maximum: float,
    error_code: str = "NATIVE_DRAWING_LEADER_PARAMETERS_INVALID",
) -> float:
    if isinstance(value, bool):
        _error(f"Drawing leader {noun} must be numeric.", error_code)
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeDrawingError(
            f"Drawing leader {noun} must be numeric.",
            error_code=error_code,
        ) from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        _error(
            f"Drawing leader {noun} is outside its documented range.",
            error_code,
        )
    return round(result, 12)


def _boolean(value: Any, noun: str, error_code: str) -> bool:
    if not isinstance(value, bool):
        _error(f"Drawing leader {noun} must be true or false.", error_code)
    return value


def _point(
    value: Any,
    noun: str,
    *,
    error_code: str = "NATIVE_DRAWING_LEADER_PARAMETERS_INVALID",
) -> dict[str, float]:
    exact = exact_drawing_mapping(
        value,
        frozenset({"x_mm", "y_mm"}),
        noun,
        family="leader",
        error_code=error_code,
    )
    return {
        name: _finite(
            exact[name],
            f"{noun} {name}",
            minimum=-1_000_000.0,
            maximum=1_000_000.0,
            error_code=error_code,
        )
        for name in ("x_mm", "y_mm")
    }


def _points(
    value: Any,
    noun: str,
    *,
    error_code: str = "NATIVE_DRAWING_LEADER_PARAMETERS_INVALID",
) -> tuple[dict[str, float], ...]:
    if isinstance(value, (str, bytes, Mapping)):
        _error(f"Drawing leader {noun} must be an ordered point array.", error_code)
    try:
        raw = tuple(value)
    except TypeError as exc:
        raise NativeDrawingError(
            f"Drawing leader {noun} must be an ordered point array.",
            error_code=error_code,
        ) from exc
    if not 2 <= len(raw) <= MAX_DRAWING_LEADER_POINTS:
        _error(
            "A Drawing leader requires 2 through 64 ordered page points.",
            error_code,
        )
    result = tuple(
        _point(item, f"{noun} item {index}", error_code=error_code)
        for index, item in enumerate(raw)
    )
    for previous, current in zip(result, result[1:]):
        if all(
            math.isclose(previous[name], current[name], rel_tol=0.0, abs_tol=1.0e-9)
            for name in ("x_mm", "y_mm")
        ):
            _error(
                "A Drawing leader may not contain consecutive duplicate points.",
                error_code,
            )
    return result


def _symbols(
    value: Any,
    *,
    error_code: str = "NATIVE_DRAWING_LEADER_PARAMETERS_INVALID",
) -> dict[str, str]:
    exact = exact_drawing_mapping(
        value,
        frozenset({"start", "end"}),
        "symbols",
        family="leader",
        error_code=error_code,
    )
    result = {name: str(exact[name] or "") for name in ("start", "end")}
    if any(symbol not in _ARROWS for symbol in result.values()):
        _error(
            "Drawing leader symbols must be documented TechDraw arrow types.",
            error_code,
            repair={"accepted_symbols": sorted(_ARROWS)},
        )
    return result


def _behavior(
    value: Any,
    *,
    error_code: str = "NATIVE_DRAWING_LEADER_PARAMETERS_INVALID",
) -> dict[str, bool]:
    fields = frozenset({"scalable", "auto_horizontal", "rotates_with_owner"})
    exact = exact_drawing_mapping(
        value,
        fields,
        "behavior",
        family="leader",
        error_code=error_code,
    )
    return {
        name: _boolean(exact[name], f"behavior {name}", error_code)
        for name in fields
    }


def _color(
    value: Any,
    *,
    error_code: str = "NATIVE_DRAWING_LEADER_PARAMETERS_INVALID",
) -> dict[str, float]:
    exact = exact_drawing_mapping(
        value,
        frozenset({"red", "green", "blue"}),
        "line color",
        family="leader",
        error_code=error_code,
    )
    result = {}
    for name in ("red", "green", "blue"):
        channel = _finite(
            exact[name],
            f"line color {name}",
            minimum=0.0,
            maximum=1.0,
            error_code=error_code,
        )
        # Match App::PropertyColor's durable eight-bit representation so the
        # requested, preflight, live, and reopened states are identical.
        result[name] = round(math.floor(channel * 255.0 + 0.5) / 255.0, 12)
    return result


def _line(
    value: Any,
    *,
    error_code: str = "NATIVE_DRAWING_LEADER_PARAMETERS_INVALID",
) -> dict[str, Any]:
    exact = exact_drawing_mapping(
        value,
        frozenset({"line_width_mm", "line_style", "color_rgb"}),
        "line",
        family="leader",
        error_code=error_code,
    )
    style = str(exact["line_style"] or "")
    if style not in _LINE_STYLES:
        _error(
            "Drawing leader line style is unsupported.",
            error_code,
            repair={"accepted_line_styles": sorted(_LINE_STYLES)},
        )
    return {
        "line_width_mm": _finite(
            exact["line_width_mm"],
            "line width",
            minimum=0.0,
            maximum=100.0,
            error_code=error_code,
        ),
        "line_style": style,
        "color_rgb": _color(exact["color_rgb"], error_code=error_code),
    }


def _spec(operation: str, values: Mapping[str, Any]) -> DrawingLeaderSpec:
    label = str(values["label"] or "")
    if label != label.strip() or not 1 <= len(label) <= 160:
        _error(
            "A Drawing leader label requires 1 to 160 non-padding characters.",
            "NATIVE_DRAWING_LEADER_PARAMETERS_INVALID",
        )
    return DrawingLeaderSpec(
        operation=operation,
        points=_points(values["points_on_page_mm"], "page points"),
        label=label,
        symbols=_symbols(values["symbols"]),
        behavior=_behavior(values["behavior"]),
        line=_line(values["line"]),
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
) -> PreparedDrawingLeaderTarget:
    page_exact = exact_drawing_mapping(
        page_target,
        frozenset({"object_name", "expected_state_sha256"}),
        "page target",
        family="leader",
        error_code="NATIVE_DRAWING_LEADER_PARAMETERS_INVALID",
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

    owner_exact = exact_drawing_mapping(
        owner_target,
        frozenset({"object_name", "expected_owner_state_sha256"}),
        "owner target",
        family="leader",
        error_code="NATIVE_DRAWING_LEADER_PARAMETERS_INVALID",
    )
    owner = resolve_object(
        document,
        {"document_uid": str(document.Uid), "object_name": owner_exact["object_name"]},
        expected_types=("TechDraw::DrawView",),
    )
    owner_state = drawing_leader_owner_state(owner, page=page)
    if (
        str(owner_exact["expected_owner_state_sha256"])
        != owner_state["owner_state_sha256"]
    ):
        _error(
            "The exact Drawing leader owner changed after it was inspected.",
            "NATIVE_DRAWING_LEADER_OWNER_STALE",
            repair={
                "current_owner_state_sha256": owner_state["owner_state_sha256"]
            },
        )
    _require_usable(document, owner, "Drawing leader owner")
    return PreparedDrawingLeaderTarget(
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


def _same_number(left: Any, right: Any, *, tolerance: float = 1.0e-9) -> bool:
    return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=tolerance)


def _same_point(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return all(_same_number(left[name], right[name]) for name in ("x_mm", "y_mm"))


def _same_points(left: Any, right: Any) -> bool:
    return len(left) == len(right) and all(
        _same_point(first, second) for first, second in zip(left, right, strict=True)
    )


def _same_line(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return (
        left["line_style"] == right["line_style"]
        and _same_number(left["line_width_mm"], right["line_width_mm"])
        and all(
            _same_number(
                left["color_rgb"][name],
                right["color_rgb"][name],
                tolerance=1.0e-6,
            )
            for name in ("red", "green", "blue")
        )
    )


def _host_points(value: Any, noun: str) -> tuple[dict[str, float], ...]:
    return _points(value, noun, error_code=_HOST_ERROR_CODE)


def _normalize_host_plan(raw: Any) -> dict[str, Any]:
    fields = frozenset(
        {
            "page_name",
            "owner_name",
            "object_name",
            "label",
            "requested_points_on_page_mm",
            "owner_transform",
            "stored",
            "rendered_points_on_page_mm",
            "symbols",
            "behavior",
            "line",
        }
    )
    exact = exact_drawing_mapping(
        raw,
        fields,
        "host plan",
        family="leader",
        error_code=_HOST_ERROR_CODE,
    )
    for name in ("page_name", "owner_name", "object_name", "label"):
        if not isinstance(exact[name], str) or not exact[name]:
            _error(f"TechDraw returned an invalid leader {name}.", _HOST_ERROR_CODE)
    transform = exact_drawing_mapping(
        exact["owner_transform"],
        frozenset({"position_on_page_mm", "scale", "rotation_degrees"}),
        "host owner transform",
        family="leader",
        error_code=_HOST_ERROR_CODE,
    )
    stored = exact_drawing_mapping(
        exact["stored"],
        frozenset({"anchor_in_owner_mm", "waypoints_in_owner_mm"}),
        "host stored geometry",
        family="leader",
        error_code=_HOST_ERROR_CODE,
    )
    return {
        "page_name": exact["page_name"],
        "owner_name": exact["owner_name"],
        "object_name": exact["object_name"],
        "label": exact["label"],
        "requested_points_on_page_mm": _host_points(
            exact["requested_points_on_page_mm"], "host requested points"
        ),
        "owner_transform": {
            "position_on_page_mm": _point(
                transform["position_on_page_mm"],
                "host owner position",
                error_code=_HOST_ERROR_CODE,
            ),
            "scale": _finite(
                transform["scale"],
                "host owner scale",
                minimum=1.0e-12,
                maximum=1_000_000.0,
                error_code=_HOST_ERROR_CODE,
            ),
            "rotation_degrees": _finite(
                transform["rotation_degrees"],
                "host owner rotation",
                minimum=-1_000_000.0,
                maximum=1_000_000.0,
                error_code=_HOST_ERROR_CODE,
            ),
        },
        "stored": {
            "anchor_in_owner_mm": _point(
                stored["anchor_in_owner_mm"],
                "host stored anchor",
                error_code=_HOST_ERROR_CODE,
            ),
            "waypoints_in_owner_mm": _host_points(
                stored["waypoints_in_owner_mm"], "host stored waypoints"
            ),
        },
        "rendered_points_on_page_mm": _host_points(
            exact["rendered_points_on_page_mm"], "host rendered points"
        ),
        "symbols": _symbols(exact["symbols"], error_code=_HOST_ERROR_CODE),
        "behavior": _behavior(exact["behavior"], error_code=_HOST_ERROR_CODE),
        "line": _line(exact["line"], error_code=_HOST_ERROR_CODE),
    }


def _host_plan(
    target: PreparedDrawingLeaderTarget,
    spec: DrawingLeaderSpec,
    *,
    apply: bool,
) -> tuple[dict[str, Any], Any | None]:
    try:
        import TechDrawGui

        function = (
            TechDrawGui.createDrawingLeaderLine
            if apply
            else TechDrawGui.validateDrawingLeaderLine
        )
        color = spec.line["color_rgb"]
        raw = function(
            target.page,
            target.owner,
            tuple((point["x_mm"], point["y_mm"]) for point in spec.points),
            spec.label,
            spec.symbols["start"],
            spec.symbols["end"],
            spec.behavior["scalable"],
            spec.behavior["auto_horizontal"],
            spec.behavior["rotates_with_owner"],
            spec.line["line_width_mm"],
            spec.line["line_style"],
            color["red"],
            color["green"],
            color["blue"],
        )
    except NativeDrawingError:
        raise
    except Exception as exc:
        if apply:
            raise NativeMutationError(
                "NATIVE_DRAWING_LEADER_CREATE_FAILED",
                f"TechDraw rejected the Leader Line: {str(exc).strip()}",
            ) from exc
        _error(
            f"TechDraw rejected the Leader Line: {str(exc).strip()}",
            "NATIVE_DRAWING_LEADER_GEOMETRY_INVALID",
            repair={
                "accepted_points": "2 through 64 distinct consecutive points inside the exact page",
                "read_operation": "read_leader_defaults",
            },
        )
    leader = raw.get("leader") if apply and isinstance(raw, Mapping) else None
    if apply and isinstance(raw, Mapping):
        raw = {name: value for name, value in raw.items() if name != "leader"}
    return _normalize_host_plan(raw), leader


def drawing_leader_defaults_state() -> dict[str, Any]:
    try:
        import TechDrawGui

        raw = TechDrawGui.drawingLeaderDefaults()
    except Exception as exc:
        _error(
            f"TechDraw Leader Line defaults are unavailable: {str(exc).strip()}",
            _HOST_ERROR_CODE,
        )
    exact = exact_drawing_mapping(
        raw,
        frozenset({"symbols", "behavior", "line"}),
        "host defaults",
        family="leader",
        error_code=_HOST_ERROR_CODE,
    )
    return {
        "symbols": _symbols(exact["symbols"], error_code=_HOST_ERROR_CODE),
        "behavior": _behavior(exact["behavior"], error_code=_HOST_ERROR_CODE),
        "line": _line(exact["line"], error_code=_HOST_ERROR_CODE),
    }


def prepare_drawing_leader(
    document: Any,
    *,
    operation: str,
    values: Mapping[str, Any],
) -> PreparedDrawingLeader:
    spec = _spec(operation, values)
    target = _target(
        document,
        page_target=values["page"],
        owner_target=values["owner"],
    )
    page_geometry = target.page_state_before.get("template_geometry")
    if not isinstance(page_geometry, Mapping):
        _error(
            "The exact Drawing page has no usable paper bounds.",
            "NATIVE_DRAWING_LEADER_GEOMETRY_INVALID",
        )
    page_width = _finite(
        page_geometry.get("width_mm"),
        "page width",
        minimum=1.0e-12,
        maximum=1_000_000.0,
        error_code="NATIVE_DRAWING_LEADER_GEOMETRY_INVALID",
    )
    page_height = _finite(
        page_geometry.get("height_mm"),
        "page height",
        minimum=1.0e-12,
        maximum=1_000_000.0,
        error_code="NATIVE_DRAWING_LEADER_GEOMETRY_INVALID",
    )
    if any(
        not 0.0 <= point["x_mm"] <= page_width
        or not 0.0 <= point["y_mm"] <= page_height
        for point in spec.points
    ):
        _error(
            "Every Drawing leader point must lie within the exact page bounds.",
            "NATIVE_DRAWING_LEADER_GEOMETRY_INVALID",
            repair={"page_width_mm": page_width, "page_height_mm": page_height},
        )
    plan, _leader = _host_plan(target, spec, apply=False)
    owner_state = target.owner_state_before
    transform = plan["owner_transform"]
    matches = {
        "page": plan["page_name"] == str(target.page.Name),
        "owner": plan["owner_name"] == str(target.owner.Name),
        "requested points": _same_points(
            plan["requested_points_on_page_mm"], spec.points
        ),
        "owner position": _same_point(
            transform["position_on_page_mm"], owner_state["position_on_page_mm"]
        ),
        "owner scale": _same_number(transform["scale"], owner_state["scale"]),
        "owner rotation": _same_number(
            transform["rotation_degrees"], owner_state["rotation_degrees"]
        ),
        "symbols": plan["symbols"] == spec.symbols,
        "behavior": plan["behavior"] == spec.behavior,
        "line": _same_line(plan["line"], spec.line),
    }
    mismatch = next((name for name, valid in matches.items() if not valid), None)
    if mismatch is not None:
        _error(
            f"TechDraw's Leader Line plan does not match the requested {mismatch}.",
            _HOST_ERROR_CODE,
        )
    return PreparedDrawingLeader(target=target, spec=spec, host_plan=plan)


def mutate_drawing_leader(
    document: Any,
    *,
    prepared: PreparedDrawingLeader,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedDrawingLeader):
        raise TypeError("prepared must be a PreparedDrawingLeader")
    applied, leader = _host_plan(prepared.target, prepared.spec, apply=True)
    if applied != prepared.host_plan or leader is None:
        raise NativeMutationError(
            "NATIVE_DRAWING_LEADER_CREATE_FAILED",
            "TechDraw created a Leader Line inconsistent with preflight.",
        )
    if (
        getattr(leader, "Document", None) is not document
        or not leader.isDerivedFrom("TechDraw::DrawLeaderLine")
    ):
        raise NativeMutationError(
            "NATIVE_DRAWING_LEADER_CREATE_FAILED",
            "TechDraw did not create the requested Leader Line type.",
        )
    return NativeMutationDraft(
        value={"prepared": prepared, "leader": leader},
        recompute_targets=(),
        created=(object_identity(leader),),
    )


def _postcondition(message: str) -> None:
    raise NativeMutationError(
        "NATIVE_DRAWING_LEADER_POSTCONDITION_FAILED",
        message,
    )


def verify_drawing_leader(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedDrawingLeader = draft.value["prepared"]
    leader = draft.value["leader"]
    target = prepared.target
    before_keys = {drawing_object_key(obj) for obj in target.objects_before}
    new_objects = tuple(
        obj for obj in document.Objects if drawing_object_key(obj) not in before_keys
    )
    if tuple(map(drawing_object_key, new_objects)) != (drawing_object_key(leader),):
        _postcondition("Leader creation changed objects outside its result.")
    if tuple(target.page.Views or ()) != (*target.page_views_before, leader):
        _postcondition("Leader creation did not append one exact page view.")
    if drawing_timeline_operations(document) != (*target.timeline_before, leader):
        _postcondition("Leader creation was not one exact History operation.")
    expected_page = dict(target.page_invariants_before)
    expected_page["view_names"] = [
        *target.page_invariants_before["view_names"],
        str(leader.Name),
    ]
    if drawing_page_invariants(target.page) != expected_page:
        _postcondition("Leader creation changed unrelated Drawing page state.")
    if drawing_leader_owner_state(target.owner, page=target.page) != target.owner_state_before:
        _postcondition("Leader creation changed its owner view definition.")
    if drawing_selection_state(document) != target.selection_before:
        _postcondition("Leader creation changed the human selection.")
    if tuple(
        (obj, bool(obj.ViewObject.Visibility))
        for obj, _visible in target.visibility_before
    ) != target.visibility_before:
        _postcondition("Leader creation changed existing visibility.")

    state = drawing_leader_state(leader)
    plan = prepared.host_plan
    checks = (
        (
            "label",
            matches_preferred_document_label(state["label"], prepared.spec.label),
        ),
        ("page", state["page_name"] == str(target.page.Name)),
        ("owner", state["owner"]["object_name"] == str(target.owner.Name)),
        (
            "owner state",
            state["owner"]["owner_state_sha256"]
            == target.owner_state_before["owner_state_sha256"],
        ),
        (
            "rendered points",
            _same_points(
                state["rendered_points_on_page_mm"],
                plan["rendered_points_on_page_mm"],
            ),
        ),
        (
            "stored anchor",
            _same_point(state["anchor_in_owner_mm"], plan["stored"]["anchor_in_owner_mm"]),
        ),
        ("stored geometry", state["storage_sha256"] == _digest(plan["stored"])),
        ("symbols", state["symbols"] == plan["symbols"]),
        ("behavior", state["behavior"] == plan["behavior"]),
        ("line", _same_line(state["line"], plan["line"])),
        ("History role", state["timeline_role"] == "operation"),
        ("History ownership", not state["timeline_owner_name"]),
        ("History availability", state["timeline_usable"]),
        ("validity", state["valid"]),
    )
    mismatch = next((name for name, valid in checks if not valid), None)
    if mismatch is not None:
        _postcondition(
            f"The created Drawing Leader Line does not match its requested {mismatch}."
        )
    page_state = drawing_page_state(target.page)
    if page_state["view_count"] != target.page_state_before["view_count"] + 1:
        _postcondition("The Drawing page did not retain the new Leader Line.")
    return {
        "leader": state,
        "next": {
            "tool": "inspect.query",
            "operation": "drawing_document",
            "page_name": str(target.page.Name),
        },
    }
