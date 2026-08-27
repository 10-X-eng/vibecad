# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact atomic creation of general Drawing centerlines."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from VibeCADNativeDrawingDimensionSupport import (
    PreparedDrawingDimensionTarget,
    drawing_dimension_error,
    drawing_object_key,
    drawing_selection_state,
    drawing_timeline_operations,
    drawing_visibility_state,
    exact_drawing_mapping,
    prepare_drawing_dimension_target,
)
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingGeneralCenterLineState import (
    MAX_DRAWING_GENERAL_CENTER_LINES,
    drawing_general_center_line_inventory_state,
    drawing_general_center_line_result_state,
    normalize_general_center_line_host_plan,
)
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewState import drawing_view_state
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeTargets import object_identity


_OPERATIONS = {
    "create_face": ("face", "faces", "Face", 1, 64),
    "create_between_edges": ("between_edges", "edges", "Edge", 2, 2),
    "create_between_vertices": (
        "between_vertices",
        "vertices",
        "Vertex",
        2,
        2,
    ),
}
_TARGET_FIELDS = frozenset({"subelement"})


@dataclass(frozen=True, slots=True)
class DrawingGeneralCenterLineSpec:
    operation: str
    kind: str
    source_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PreparedDrawingGeneralCenterLine:
    target: PreparedDrawingDimensionTarget
    spec: DrawingGeneralCenterLineSpec
    host_validation: dict[str, Any]
    inventory_before: dict[str, Any]


def _error(
    message: str,
    code: str,
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    drawing_dimension_error(message, code, repair=repair)


def _source_targets(
    operation: str,
    values: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    _kind, field, prefix, minimum, maximum = _OPERATIONS[operation]
    raw = values[field]
    if (
        not isinstance(raw, Sequence)
        or isinstance(raw, (str, bytes))
        or not minimum <= len(raw) <= maximum
        or any(not isinstance(item, Mapping) for item in raw)
    ):
        _error(
            f"{operation.replace('_', ' ').title()} requires {minimum} to "
            f"{maximum} exact projected {prefix}N targets.",
            "NATIVE_DRAWING_GENERAL_CENTER_LINE_PARAMETERS_INVALID",
        )
    targets = tuple(raw)
    names = []
    for item in targets:
        exact = exact_drawing_mapping(
            item,
            _TARGET_FIELDS,
            "centerline source",
            family="centerline",
            error_code="NATIVE_DRAWING_GENERAL_CENTER_LINE_PARAMETERS_INVALID",
        )
        name = str(exact["subelement"] or "")
        if not name.startswith(prefix):
            _error(
                f"Each {operation.replace('_', ' ')} source must be an exact "
                f"projected {prefix}N.",
                "NATIVE_DRAWING_GENERAL_CENTER_LINE_REFERENCE_TYPE_INVALID",
                repair={"accepted_reference_types": [prefix.casefold()]},
            )
        names.append(name)
    if len(names) != len(set(names)):
        _error(
            "A centerline source cannot be repeated.",
            "NATIVE_DRAWING_GENERAL_CENTER_LINE_REFERENCES_INVALID",
        )
    return targets


def _validate_host(
    view: Any,
    spec: DrawingGeneralCenterLineSpec,
    source_elements: tuple[Mapping[str, Any], ...],
) -> dict[str, Any]:
    try:
        import TechDrawGui

        validator = getattr(TechDrawGui, "validateDrawingGeneralCenterLine", None)
        if not callable(validator):
            _error(
                "The installed TechDraw runtime cannot validate general centerlines.",
                "NATIVE_DRAWING_GENERAL_CENTER_LINE_RUNTIME_UNAVAILABLE",
            )
        plan = normalize_general_center_line_host_plan(
            validator(view, spec.kind, list(spec.source_names)),
            created=False,
        )
    except NativeDrawingError:
        raise
    except Exception as exc:
        _error(
            "TechDraw rejected the exact centerline sources: "
            f"{str(exc).strip()}",
            "NATIVE_DRAWING_GENERAL_CENTER_LINE_REFERENCES_INVALID",
            repair={
                "operation": spec.operation,
                "requested_subelements": list(spec.source_names),
                "tool": "drawing.projected_geometry",
            },
        )
    expected_type = _OPERATIONS[spec.operation][2].casefold()
    if (
        plan["kind"] != spec.kind
        or plan["source_subelements"] != list(spec.source_names)
        or [item["name"] for item in source_elements] != list(spec.source_names)
        or any(item["element_type"] != expected_type for item in source_elements)
    ):
        _error(
            "TechDraw's centerline plan does not match the exact projected targets.",
            "NATIVE_DRAWING_GENERAL_CENTER_LINE_RUNTIME_UNAVAILABLE",
        )
    return plan


def prepare_drawing_general_center_line(
    document: Any,
    *,
    operation: str,
    values: Mapping[str, Any],
) -> PreparedDrawingGeneralCenterLine:
    if operation not in _OPERATIONS:
        raise ValueError("operation is not a general Drawing centerline operation")
    kind, _field, prefix, _minimum, _maximum = _OPERATIONS[operation]
    source_targets = _source_targets(operation, values)
    target = prepare_drawing_dimension_target(
        document,
        page_target=values["page"],
        view_target=values["view"],
        element_targets=source_targets,
        allowed_element_types=frozenset({prefix.casefold()}),
        family="centerline",
        code_prefix="NATIVE_DRAWING_GENERAL_CENTER_LINE",
    )
    spec = DrawingGeneralCenterLineSpec(
        operation=operation,
        kind=kind,
        source_names=tuple(
            item["name"] for item in target.element_states_before
        ),
    )
    inventory = drawing_general_center_line_inventory_state(target.view)
    if inventory["centerline_count"] >= MAX_DRAWING_GENERAL_CENTER_LINES:
        _error(
            "The Drawing centerline inventory already contains 4096 targets.",
            "NATIVE_DRAWING_GENERAL_CENTER_LINE_LIMIT_EXCEEDED",
        )
    return PreparedDrawingGeneralCenterLine(
        target=target,
        spec=spec,
        host_validation=_validate_host(
            target.view, spec, target.element_states_before
        ),
        inventory_before=inventory,
    )


def _creation_plan(created: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in created.items()
        if key not in {"centerline_tag", "subelement"}
    }


def mutate_drawing_general_center_line(
    _document: Any,
    *,
    prepared: PreparedDrawingGeneralCenterLine,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedDrawingGeneralCenterLine):
        raise TypeError("prepared must be PreparedDrawingGeneralCenterLine")
    import TechDrawGui

    try:
        created = normalize_general_center_line_host_plan(
            TechDrawGui.createDrawingGeneralCenterLine(
                prepared.target.view,
                prepared.spec.kind,
                list(prepared.spec.source_names),
            ),
            created=True,
            persistent=True,
        )
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_GENERAL_CENTER_LINE_CREATION_FAILED",
            "TechDraw could not create the exact centerline: "
            f"{str(exc).strip()}",
        ) from exc
    if _creation_plan(created) != prepared.host_validation:
        raise NativeMutationError(
            "NATIVE_DRAWING_GENERAL_CENTER_LINE_CREATION_FAILED",
            "TechDraw created a centerline inconsistent with preflight.",
        )
    return NativeMutationDraft(
        value={"prepared": prepared, "created": created},
        recompute_targets=(prepared.target.view, prepared.target.page),
        changed=(object_identity(prepared.target.view),),
    )


def _postcondition_error(message: str) -> None:
    raise NativeMutationError(
        "NATIVE_DRAWING_GENERAL_CENTER_LINE_POSTCONDITION_FAILED", message
    )


def _view_boundary(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in state.items()
        if key not in {"state_sha256", "visible_edge_count", "hidden_edge_count"}
    }


def _require_old_centerlines_preserved(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> None:
    after_by_tag = {
        item["centerline_tag"]: item for item in after["centerlines"]
    }
    for old in before["centerlines"]:
        current = after_by_tag.get(old["centerline_tag"])
        if current is None or {
            key: value for key, value in current.items() if key != "subelement"
        } != {key: value for key, value in old.items() if key != "subelement"}:
            _postcondition_error(
                "Centerline creation changed an existing persistent centerline."
            )


def _verify_drawing_general_center_line(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedDrawingGeneralCenterLine = draft.value["prepared"]
    target = prepared.target
    if (
        getattr(target.view, "Document", None) is not document
        or target.view.findParentPage() is not target.page
        or tuple(map(drawing_object_key, document.Objects))
        != tuple(map(drawing_object_key, target.objects_before))
        or tuple(map(drawing_object_key, tuple(target.page.Views or ())))
        != tuple(map(drawing_object_key, target.page_views_before))
        or tuple(map(drawing_object_key, drawing_timeline_operations(document)))
        != tuple(map(drawing_object_key, target.timeline_before))
    ):
        _postcondition_error(
            "Centerline creation changed objects, page membership, or History."
        )
    if drawing_selection_state(document) != target.selection_before:
        _postcondition_error("Centerline creation changed the human selection.")
    if drawing_visibility_state(document) != target.visibility_before:
        _postcondition_error("Centerline creation changed object visibility.")
    if drawing_page_state(target.page) != target.page_state_before:
        _postcondition_error("Centerline creation changed the Drawing page.")
    if _view_boundary(drawing_view_state(target.view)) != _view_boundary(
        target.view_state_before
    ):
        _postcondition_error("Centerline creation changed the Drawing view definition.")

    projection = drawing_projected_geometry_state(target.view)
    projected_by_name = {
        item["name"]: item for item in projection["elements"]
    }
    for source in target.element_states_before:
        current = projected_by_name.get(source["name"])
        if (
            current is None
            or current["element_state_sha256"] != source["element_state_sha256"]
        ):
            _postcondition_error(
                "A projected source changed while its centerline was created."
            )

    inventory = drawing_general_center_line_inventory_state(target.view)
    if inventory["centerline_count"] != prepared.inventory_before[
        "centerline_count"
    ] + 1:
        _postcondition_error(
            "Centerline creation did not add exactly one persistent centerline."
        )
    _require_old_centerlines_preserved(prepared.inventory_before, inventory)
    try:
        state = drawing_general_center_line_result_state(
            target.view,
            draft.value["created"],
            target.element_states_before,
        )
    except Exception as exc:
        _postcondition_error(
            "The created centerline could not be resolved exactly: "
            f"{str(exc).strip()}"
        )
    return {"operation": prepared.spec.operation, "centerline": state}


def verify_drawing_general_center_line(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    try:
        return _verify_drawing_general_center_line(document, draft)
    except NativeMutationError:
        raise
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_GENERAL_CENTER_LINE_POSTCONDITION_FAILED",
            "The centerline could not be verified exactly: "
            f"{str(exc).strip()}",
        ) from exc
