# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact transactional positioning for standard Drawing section views."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from SectionViewPosition import (
    SectionViewPositionError,
    apply_section_view_position,
    calculate_axis_alignment,
    calculate_edge_vertex_alignment,
)
from VibeCADNativeDrawingDimensionSupport import (
    drawing_object_key,
    drawing_selection_state,
    drawing_timeline_operations,
    drawing_visibility_state,
    exact_drawing_mapping,
)
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state
from VibeCADNativeDrawingSectionPositionState import (
    NativeDrawingSectionPositionStateError,
    drawing_alignment_base_state,
    drawing_section_position_state,
)
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewState import drawing_view_state
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeTargets import object_identity, resolve_object


_PAGE_FIELDS = frozenset({"object_name", "expected_state_sha256"})
_SECTION_FIELDS = frozenset(
    {"object_name", "expected_section_position_state_sha256"}
)
_SECTION_PROJECTION_FIELDS = _SECTION_FIELDS | frozenset(
    {"expected_projection_state_sha256"}
)
_BASE_FIELDS = frozenset(
    {
        "object_name",
        "expected_state_sha256",
        "expected_projection_state_sha256",
        "expected_alignment_base_state_sha256",
    }
)
_ELEMENT_FIELDS = frozenset({"name"})


@dataclass(frozen=True, slots=True)
class PreparedDrawingSectionPosition:
    operation: str
    page: Any
    page_state_before: dict[str, Any]
    section_view: Any
    section_state_before: dict[str, Any]
    section_definition_before: dict[str, Any]
    section_projection_before: dict[str, Any] | None
    base_view: Any | None
    base_definition_before: dict[str, Any] | None
    base_projection_before: dict[str, Any] | None
    base_alignment_before: dict[str, Any] | None
    section_edge_before: dict[str, Any] | None
    base_vertex_before: dict[str, Any] | None
    axis: str | None
    target_x_mm: float
    target_y_mm: float
    objects_before: tuple[Any, ...]
    timeline_before: tuple[Any, ...]
    page_views_before: tuple[Any, ...]
    selection_before: dict[str, Any]
    visibility_before: tuple[tuple[Any, bool], ...]


def _error(
    message: str,
    code: str,
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    raise NativeDrawingError(message, error_code=code, repair=repair)


def _require_usable(document: Any, obj: Any, noun: str) -> None:
    checker = getattr(document, "isObjectUsableAtCurrentTimelinePosition", None)
    if callable(checker) and not bool(checker(obj)):
        _error(
            f"The exact {noun} is not usable at the current History position.",
            "NATIVE_DRAWING_HISTORY_TARGET_UNAVAILABLE",
        )


def _resolve_page(document: Any, value: Any) -> tuple[Any, dict[str, Any]]:
    exact = exact_drawing_mapping(
        value,
        _PAGE_FIELDS,
        "page target",
        family="section position",
        error_code="NATIVE_DRAWING_SECTION_POSITION_PARAMETERS_INVALID",
    )
    page = resolve_object(
        document,
        {"document_uid": str(document.Uid), "object_name": exact["object_name"]},
        expected_types=("TechDraw::DrawPage",),
    )
    state = drawing_page_state(page)
    if str(exact["expected_state_sha256"]) != state["state_sha256"]:
        _error(
            "The exact Drawing page changed after it was inspected.",
            "NATIVE_DRAWING_PAGE_STALE",
            repair={"current_state_sha256": state["state_sha256"]},
        )
    _require_usable(document, page, "Drawing page")
    return page, state


def _resolve_section(
    document: Any,
    page: Any,
    value: Any,
    *,
    projection_required: bool,
) -> tuple[Any, dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    fields = _SECTION_PROJECTION_FIELDS if projection_required else _SECTION_FIELDS
    exact = exact_drawing_mapping(
        value,
        fields,
        "section-view target",
        family="section position",
        error_code="NATIVE_DRAWING_SECTION_POSITION_PARAMETERS_INVALID",
    )
    section = resolve_object(
        document,
        {"document_uid": str(document.Uid), "object_name": exact["object_name"]},
        expected_types=("TechDraw::DrawViewSection",),
    )
    if str(getattr(section, "TypeId", "")) != "TechDraw::DrawViewSection":
        _error(
            "Section positioning requires one standard, not complex, section view.",
            "NATIVE_DRAWING_SECTION_POSITION_TARGET_INVALID",
        )
    if section.findParentPage() is not page or section not in tuple(page.Views or ()):
        _error(
            "The exact section view does not belong to the exact Drawing page.",
            "NATIVE_DRAWING_SECTION_POSITION_PAGE_MISMATCH",
        )
    _require_usable(document, section, "section view")
    try:
        state = drawing_section_position_state(section)
    except (NativeDrawingSectionPositionStateError, TypeError) as exc:
        _error(
            f"The section-view position state is invalid: {str(exc).strip()}",
            "NATIVE_DRAWING_SECTION_POSITION_TARGET_INVALID",
        )
    if (
        str(exact["expected_section_position_state_sha256"])
        != state["section_position_state_sha256"]
    ):
        _error(
            "The exact section view changed after it was inspected.",
            "NATIVE_DRAWING_SECTION_POSITION_TARGET_STALE",
            repair={
                "current_section_position_state_sha256": state[
                    "section_position_state_sha256"
                ]
            },
        )
    if not state["timeline_usable"] or not state["valid"]:
        _error(
            "The exact section view is invalid at the current History position.",
            "NATIVE_DRAWING_SECTION_POSITION_TARGET_INVALID",
        )
    definition = drawing_view_state(section)
    try:
        projection = drawing_projected_geometry_state(section)
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        _error(
            "The exact section view has no current projected geometry: "
            f"{str(exc).strip()}",
            "NATIVE_DRAWING_SECTION_POSITION_TARGET_INVALID",
        )
    if projection_required:
        if (
            str(exact["expected_projection_state_sha256"])
            != projection["projection_state_sha256"]
        ):
            _error(
                "The section-view projection changed after it was inspected.",
                "NATIVE_DRAWING_SECTION_POSITION_PROJECTION_STALE",
                repair={
                    "current_projection_state_sha256": projection[
                        "projection_state_sha256"
                    ]
                },
            )
    return section, state, definition, projection


def _resolve_element(
    value: Any,
    projection: Mapping[str, Any],
    *,
    kind: str,
) -> dict[str, Any]:
    exact = exact_drawing_mapping(
        value,
        _ELEMENT_FIELDS,
        f"{kind} target",
        family="section position",
        error_code="NATIVE_DRAWING_SECTION_POSITION_PARAMETERS_INVALID",
    )
    name = str(exact["name"] or "")
    element = next(
        (item for item in projection["elements"] if item["name"] == name),
        None,
    )
    if element is None or element["element_type"] != kind:
        _error(
            f"The exact projected {kind} target is unavailable.",
            "NATIVE_DRAWING_SECTION_POSITION_ELEMENT_INVALID",
        )
    return element


def _resolve_base_view(
    document: Any,
    page: Any,
    value: Any,
) -> tuple[Any, dict[str, Any], dict[str, Any], dict[str, Any]]:
    exact = exact_drawing_mapping(
        value,
        _BASE_FIELDS,
        "base-view target",
        family="section position",
        error_code="NATIVE_DRAWING_SECTION_POSITION_PARAMETERS_INVALID",
    )
    view = resolve_object(
        document,
        {"document_uid": str(document.Uid), "object_name": exact["object_name"]},
        expected_types=("TechDraw::DrawViewPart",),
    )
    if view.findParentPage() is not page or view not in tuple(page.Views or ()):
        _error(
            "The exact base view does not belong to the exact Drawing page.",
            "NATIVE_DRAWING_SECTION_POSITION_PAGE_MISMATCH",
        )
    _require_usable(document, view, "base view")
    definition = drawing_view_state(view)
    if str(exact["expected_state_sha256"]) != definition["state_sha256"]:
        _error(
            "The exact base view changed after it was inspected.",
            "NATIVE_DRAWING_SECTION_POSITION_BASE_STALE",
            repair={"current_state_sha256": definition["state_sha256"]},
        )
    projection = drawing_projected_geometry_state(view)
    if (
        str(exact["expected_projection_state_sha256"])
        != projection["projection_state_sha256"]
    ):
        _error(
            "The base-view projection changed after it was inspected.",
            "NATIVE_DRAWING_SECTION_POSITION_PROJECTION_STALE",
            repair={
                "current_projection_state_sha256": projection[
                    "projection_state_sha256"
                ]
            },
        )
    alignment = drawing_alignment_base_state(view)
    if (
        str(exact["expected_alignment_base_state_sha256"])
        != alignment["alignment_base_state_sha256"]
    ):
        _error(
            "The base view's page-position owner changed after it was inspected.",
            "NATIVE_DRAWING_SECTION_POSITION_BASE_STALE",
            repair={
                "current_alignment_base_state_sha256": alignment[
                    "alignment_base_state_sha256"
                ]
            },
        )
    return view, definition, projection, alignment


def prepare_drawing_section_position(
    document: Any,
    *,
    operation: str,
    values: Mapping[str, Any],
) -> PreparedDrawingSectionPosition:
    if operation not in {"align_axis", "align_edge_to_vertex"}:
        raise ValueError("operation is not a section-position operation")
    page, page_state = _resolve_page(document, values["page"])
    section, section_state, section_definition, section_projection = _resolve_section(
        document,
        page,
        values["section_view"],
        projection_required=operation == "align_edge_to_vertex",
    )
    base_view = None
    base_definition = None
    base_projection = None
    base_alignment = None
    section_edge = None
    base_vertex = None
    axis = None
    try:
        if operation == "align_axis":
            axis = str(values["axis"] or "")
            if axis not in {"horizontal", "vertical"}:
                _error(
                    "Section-view axis must be horizontal or vertical.",
                    "NATIVE_DRAWING_SECTION_POSITION_PARAMETERS_INVALID",
                )
            calculation = calculate_axis_alignment(section, axis)
        else:
            assert section_projection is not None
            section_edge = _resolve_element(
                values["section_edge"],
                section_projection,
                kind="edge",
            )
            (
                base_view,
                base_definition,
                base_projection,
                base_alignment,
            ) = _resolve_base_view(document, page, values["base_view"])
            base_vertex = _resolve_element(
                values["base_vertex"],
                base_projection,
                kind="vertex",
            )
            calculation = calculate_edge_vertex_alignment(
                section,
                section_edge["name"],
                base_view,
                base_vertex["name"],
            )
    except SectionViewPositionError as exc:
        _error(
            str(exc),
            "NATIVE_DRAWING_SECTION_POSITION_ALIGNMENT_INVALID",
        )
    target_x = float(calculation["target_x_mm"])
    target_y = float(calculation["target_y_mm"])
    current = section_state["position_on_page_mm"]
    if math.isclose(current["x_mm"], target_x, abs_tol=1.0e-9) and math.isclose(
        current["y_mm"], target_y, abs_tol=1.0e-9
    ):
        _error(
            "The section view is already at the requested aligned position.",
            "NATIVE_DRAWING_SECTION_POSITION_NO_CHANGE",
        )
    selection = drawing_selection_state(document)
    if (
        bool(selection.get("truncated"))
        or int(selection.get("selected_count", 0))
        != len(tuple(selection.get("items", ()) or ()))
    ):
        _error(
            "Reduce the current selection to at most 32 exact objects before "
            "positioning a section view.",
            "NATIVE_DRAWING_SECTION_POSITION_SELECTION_TOO_LARGE",
        )
    return PreparedDrawingSectionPosition(
        operation=operation,
        page=page,
        page_state_before=page_state,
        section_view=section,
        section_state_before=section_state,
        section_definition_before=section_definition,
        section_projection_before=section_projection,
        base_view=base_view,
        base_definition_before=base_definition,
        base_projection_before=base_projection,
        base_alignment_before=base_alignment,
        section_edge_before=section_edge,
        base_vertex_before=base_vertex,
        axis=axis,
        target_x_mm=target_x,
        target_y_mm=target_y,
        objects_before=tuple(document.Objects),
        timeline_before=drawing_timeline_operations(document),
        page_views_before=tuple(page.Views or ()),
        selection_before=selection,
        visibility_before=drawing_visibility_state(document),
    )


def mutate_drawing_section_position(
    _document: Any,
    *,
    prepared: PreparedDrawingSectionPosition,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedDrawingSectionPosition):
        raise TypeError("prepared must be a PreparedDrawingSectionPosition")
    try:
        applied = apply_section_view_position(
            prepared.section_view,
            prepared.target_x_mm,
            prepared.target_y_mm,
        )
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_SECTION_POSITION_CHANGE_FAILED",
            "TechDraw could not position the exact section view: "
            f"{str(exc).strip()}",
        ) from exc
    return NativeMutationDraft(
        value={"prepared": prepared, "applied": applied},
        recompute_targets=(prepared.section_view, prepared.page),
        changed=(object_identity(prepared.section_view),),
    )


def _postcondition_error(message: str) -> None:
    raise NativeMutationError(
        "NATIVE_DRAWING_SECTION_POSITION_POSTCONDITION_FAILED",
        message,
    )


def _section_state_boundary(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> bool:
    ignored = frozenset(
        {"section_position_state_sha256", "position_on_page_mm"}
    )
    return {key: value for key, value in before.items() if key not in ignored} == {
        key: value for key, value in after.items() if key not in ignored
    }


def _view_definition_boundary(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> bool:
    return _stable_view_definition(before) == _stable_view_definition(after)


def _stable_view_definition(value: Mapping[str, Any]) -> dict[str, Any]:
    ignored = frozenset(
        {
            "state_sha256",
            "x_mm",
            "y_mm",
            "visible_edge_count",
            "hidden_edge_count",
        }
    )
    result = {key: item for key, item in value.items() if key not in ignored}
    section = result.get("section")
    if isinstance(section, Mapping):
        result["section"] = {
            key: item
            for key, item in section.items()
            if key != "section_face_count"
        }
    return result


def _view_definition_changes(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> tuple[str, ...]:
    before = _stable_view_definition(before)
    after = _stable_view_definition(after)
    changed = []
    for key in sorted(set(before) | set(after)):
        if before.get(key) == after.get(key):
            continue
        left = before.get(key)
        right = after.get(key)
        if isinstance(left, Mapping) and isinstance(right, Mapping):
            nested = [
                f"{key}.{nested_key}"
                for nested_key in sorted(set(left) | set(right))
                if left.get(nested_key) != right.get(nested_key)
            ]
            changed.extend(nested or [key])
        else:
            changed.append(key)
    return tuple(changed)


def _projection_geometry_boundary(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> bool:
    """Compare projected geometry while ignoring only page placement hashes."""

    def normalized(value: Mapping[str, Any]) -> dict[str, Any]:
        result = {
            key: item
            for key, item in value.items()
            if key != "projection_state_sha256"
        }
        view = dict(result["view"])
        view.pop("view_state_sha256", None)
        result["view"] = view
        return result

    return normalized(before) == normalized(after)


def _verify_drawing_section_position(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedDrawingSectionPosition = draft.value["prepared"]
    if (
        tuple(map(drawing_object_key, document.Objects))
        != tuple(map(drawing_object_key, prepared.objects_before))
        or tuple(map(drawing_object_key, drawing_timeline_operations(document)))
        != tuple(map(drawing_object_key, prepared.timeline_before))
        or tuple(map(drawing_object_key, tuple(prepared.page.Views or ())))
        != tuple(map(drawing_object_key, prepared.page_views_before))
    ):
        _postcondition_error(
            "Section positioning altered objects, page membership, or History."
        )
    if drawing_selection_state(document) != prepared.selection_before:
        _postcondition_error("Section positioning altered the human selection.")
    if drawing_visibility_state(document) != prepared.visibility_before:
        _postcondition_error("Section positioning altered object visibility.")
    if (
        drawing_page_state(prepared.page)["state_sha256"]
        != prepared.page_state_before["state_sha256"]
    ):
        _postcondition_error("Section positioning altered the Drawing page.")

    final_state = drawing_section_position_state(prepared.section_view)
    final_definition = drawing_view_state(prepared.section_view)
    position = final_state["position_on_page_mm"]
    if not math.isclose(
        position["x_mm"], prepared.target_x_mm, abs_tol=1.0e-8
    ) or not math.isclose(
        position["y_mm"], prepared.target_y_mm, abs_tol=1.0e-8
    ):
        _postcondition_error(
            "The section view did not retain its exact requested page position."
        )
    if not _section_state_boundary(prepared.section_state_before, final_state):
        _postcondition_error(
            "Section positioning changed section state beyond page placement."
        )
    if not _view_definition_boundary(
        prepared.section_definition_before,
        final_definition,
    ):
        changed = ", ".join(
            _view_definition_changes(
                prepared.section_definition_before,
                final_definition,
            )
        )
        _postcondition_error(
            "Section positioning changed the projected-view definition beyond page "
            f"placement ({changed})."
        )
    if (
        final_state["section_position_state_sha256"]
        == prepared.section_state_before["section_position_state_sha256"]
    ):
        _postcondition_error(
            "The section position state did not record the requested movement."
        )
    if prepared.section_projection_before is not None and not (
        _projection_geometry_boundary(
            prepared.section_projection_before,
            drawing_projected_geometry_state(prepared.section_view),
        )
    ):
        _postcondition_error(
            "Section positioning altered the section's projected geometry."
        )
    if prepared.base_view is not None:
        if (
            drawing_view_state(prepared.base_view)
            != prepared.base_definition_before
            or drawing_projected_geometry_state(prepared.base_view)
            != prepared.base_projection_before
            or drawing_alignment_base_state(prepared.base_view)
            != prepared.base_alignment_before
        ):
            _postcondition_error(
                "Section positioning altered the exact base view or its page owner."
            )
    old_position = prepared.section_state_before["position_on_page_mm"]
    moved = math.hypot(
        position["x_mm"] - old_position["x_mm"],
        position["y_mm"] - old_position["y_mm"],
    )
    return {
        "operation": prepared.operation,
        "section_position": {
            "section_view_object_name": str(prepared.section_view.Name),
            "section_position_state_sha256": final_state[
                "section_position_state_sha256"
            ],
            "old_position_on_page_mm": old_position,
            "position_on_page_mm": position,
            "moved_distance_mm": round(moved, 9),
            "axis": prepared.axis,
            "section_edge": (
                prepared.section_edge_before["name"]
                if prepared.section_edge_before is not None
                else None
            ),
            "base_view_object_name": (
                str(prepared.base_view.Name)
                if prepared.base_view is not None
                else final_state["base_view_name"]
            ),
            "base_vertex": (
                prepared.base_vertex_before["name"]
                if prepared.base_vertex_before is not None
                else None
            ),
        },
    }


def verify_drawing_section_position(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    try:
        return _verify_drawing_section_position(document, draft)
    except NativeMutationError:
        raise
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_SECTION_POSITION_POSTCONDITION_FAILED",
            "The Drawing section position could not be verified exactly: "
            f"{str(exc).strip()}",
        ) from exc
