# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact atomic creation of Drawing thread representations."""

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
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state
from VibeCADNativeDrawingLineAttributeState import (
    drawing_line_attribute_inventory_state,
)
from VibeCADNativeDrawingLineLengthState import (
    drawing_line_length_inventory_state,
)
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingThreadRepresentationState import (
    MAX_DRAWING_THREAD_BOTTOM_TARGETS,
    drawing_thread_bottom_result_state,
    drawing_thread_side_result_state,
    normalize_thread_bottom_host_plans,
    normalize_thread_side_host_plan,
)
from VibeCADNativeDrawingViewState import drawing_view_state
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeTargets import object_identity


_OPERATIONS = {
    "create_hole_side": ("hole_side", "side", "boundary_edges"),
    "create_hole_bottom": ("hole_bottom", "bottom", "circles"),
    "create_bolt_side": ("bolt_side", "side", "boundary_edges"),
    "create_bolt_bottom": ("bolt_bottom", "bottom", "circles"),
}


@dataclass(frozen=True, slots=True)
class DrawingThreadRepresentationSpec:
    operation: str
    kind: str
    orientation: str
    source_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PreparedDrawingThreadRepresentation:
    target: PreparedDrawingDimensionTarget
    spec: DrawingThreadRepresentationSpec
    host_validation: dict[str, Any] | list[dict[str, Any]]
    attribute_inventory_before: dict[str, Any]
    length_inventory_before: dict[str, Any]


def _error(
    message: str,
    code: str,
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    drawing_dimension_error(message, code, repair=repair)


def _source_targets(
    operation: str, values: Mapping[str, Any]
) -> tuple[tuple[Mapping[str, Any], ...], DrawingThreadRepresentationSpec]:
    try:
        kind, orientation, field = _OPERATIONS[operation]
    except KeyError as exc:
        raise ValueError(
            "operation is not a Drawing thread-representation operation"
        ) from exc
    raw = values[field]
    valid_count = (
        isinstance(raw, Sequence)
        and not isinstance(raw, (str, bytes))
        and (
            len(raw) == 2
            if orientation == "side"
            else 1 <= len(raw) <= MAX_DRAWING_THREAD_BOTTOM_TARGETS
        )
        and all(isinstance(item, Mapping) for item in raw)
    )
    if not valid_count:
        _error(
            (
                "A thread-side representation requires exactly two ordered "
                "projected boundary edges."
                if orientation == "side"
                else "A thread-bottom representation requires 1 to 32 projected full circles."
            ),
            "NATIVE_DRAWING_THREAD_PARAMETERS_INVALID",
        )
    targets = tuple(raw)
    names = []
    for index, item in enumerate(targets):
        exact = exact_drawing_mapping(
            item,
            frozenset({"subelement", "expected_element_state_sha256"}),
            f"thread source {index}",
            family="thread representation",
            error_code="NATIVE_DRAWING_THREAD_PARAMETERS_INVALID",
        )
        name = str(exact["subelement"] or "")
        if not name.startswith("Edge"):
            _error(
                "Each thread source must be an exact projected EdgeN.",
                "NATIVE_DRAWING_THREAD_REFERENCE_TYPE_INVALID",
                repair={
                    "accepted_reference_types": [
                        "two projected straight parallel edges"
                        if orientation == "side"
                        else "projected full circle"
                    ]
                },
            )
        names.append(name)
    if len(names) != len(set(names)):
        _error(
            "A thread representation cannot repeat a projected source.",
            "NATIVE_DRAWING_THREAD_REFERENCES_INVALID",
        )
    return targets, DrawingThreadRepresentationSpec(
        operation=operation,
        kind=kind,
        orientation=orientation,
        source_names=tuple(names),
    )


def _validate_host(
    view: Any,
    spec: DrawingThreadRepresentationSpec,
    source_elements: tuple[Mapping[str, Any], ...],
) -> dict[str, Any] | list[dict[str, Any]]:
    try:
        import TechDrawGui

        if spec.orientation == "side":
            validator = getattr(TechDrawGui, "validateDrawingThreadSide", None)
            if not callable(validator):
                _error(
                    "The installed TechDraw runtime cannot validate thread-side representations.",
                    "NATIVE_DRAWING_THREAD_RUNTIME_UNAVAILABLE",
                )
            plan: dict[str, Any] | list[dict[str, Any]] = (
                normalize_thread_side_host_plan(
                    validator(view, spec.kind, list(spec.source_names)),
                    created=False,
                )
            )
        else:
            validator = getattr(TechDrawGui, "validateDrawingThreadBottom", None)
            if not callable(validator):
                _error(
                    "The installed TechDraw runtime cannot validate thread-bottom representations.",
                    "NATIVE_DRAWING_THREAD_RUNTIME_UNAVAILABLE",
                )
            plan = normalize_thread_bottom_host_plans(
                validator(view, spec.kind, list(spec.source_names)),
                created=False,
            )
    except NativeDrawingError:
        raise
    except Exception as exc:
        _error(
            f"TechDraw rejected the exact thread sources: {str(exc).strip()}",
            "NATIVE_DRAWING_THREAD_REFERENCES_INVALID",
            repair={
                "accepted_references": (
                    "exactly two distinct projected nonzero parallel straight EdgeN targets"
                    if spec.orientation == "side"
                    else "1 to 32 distinct projected full-circle EdgeN targets"
                ),
                "requested_subelements": list(spec.source_names),
                "inspect_operation": "drawing_projected_geometry",
            },
        )
    if spec.orientation == "side":
        if (
            not isinstance(plan, Mapping)
            or plan["kind"] != spec.kind
            or tuple(plan["source_subelements"]) != spec.source_names
        ):
            _error(
                "TechDraw's thread-side plan does not match the exact projected sources.",
                "NATIVE_DRAWING_THREAD_RUNTIME_UNAVAILABLE",
            )
    else:
        if (
            not isinstance(plan, list)
            or tuple(item["source_subelement"] for item in plan) != spec.source_names
            or any(item["kind"] != spec.kind for item in plan)
        ):
            _error(
                "TechDraw's thread-bottom plans do not match the exact projected circles.",
                "NATIVE_DRAWING_THREAD_RUNTIME_UNAVAILABLE",
            )
    if len(source_elements) != len(spec.source_names) or any(
        source["name"] != name or source["element_type"] != "edge"
        for source, name in zip(source_elements, spec.source_names, strict=True)
    ):
        _error(
            "The exact projected thread targets are inconsistent.",
            "NATIVE_DRAWING_THREAD_RUNTIME_UNAVAILABLE",
        )
    return plan


def prepare_drawing_thread_representation(
    document: Any,
    *,
    operation: str,
    values: Mapping[str, Any],
) -> PreparedDrawingThreadRepresentation:
    source_targets, spec = _source_targets(operation, values)
    target = prepare_drawing_dimension_target(
        document,
        page_target=values["page"],
        view_target=values["view"],
        element_targets=source_targets,
        allowed_element_types=frozenset({"edge"}),
        family="thread representation",
        code_prefix="NATIVE_DRAWING_THREAD",
    )
    return PreparedDrawingThreadRepresentation(
        target=target,
        spec=spec,
        host_validation=_validate_host(target.view, spec, target.element_states_before),
        attribute_inventory_before=drawing_line_attribute_inventory_state(target.view),
        length_inventory_before=drawing_line_length_inventory_state(target.view),
    )


def _without_created_tags(
    orientation: str, plan: dict[str, Any] | list[dict[str, Any]]
) -> dict[str, Any] | list[dict[str, Any]]:
    if orientation == "side":
        assert isinstance(plan, dict)
        return {
            **plan,
            "lines": [
                {
                    **line,
                    "segment": {
                        key: value
                        for key, value in line["segment"].items()
                        if key != "tag"
                    },
                }
                for line in plan["lines"]
            ],
        }
    assert isinstance(plan, list)
    return [
        {key: value for key, value in item.items() if key != "arc_tag"} for item in plan
    ]


def mutate_drawing_thread_representation(
    _document: Any,
    *,
    prepared: PreparedDrawingThreadRepresentation,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedDrawingThreadRepresentation):
        raise TypeError("prepared must be PreparedDrawingThreadRepresentation")
    import TechDrawGui

    try:
        if prepared.spec.orientation == "side":
            created: dict[str, Any] | list[dict[str, Any]] = (
                normalize_thread_side_host_plan(
                    TechDrawGui.createDrawingThreadSide(
                        prepared.target.view,
                        prepared.spec.kind,
                        list(prepared.spec.source_names),
                    ),
                    created=True,
                )
            )
        else:
            created = normalize_thread_bottom_host_plans(
                TechDrawGui.createDrawingThreadBottom(
                    prepared.target.view,
                    prepared.spec.kind,
                    list(prepared.spec.source_names),
                ),
                created=True,
            )
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_THREAD_CREATION_FAILED",
            "TechDraw could not create the exact thread representation: "
            f"{str(exc).strip()}",
        ) from exc
    if (
        _without_created_tags(prepared.spec.orientation, created)
        != prepared.host_validation
    ):
        raise NativeMutationError(
            "NATIVE_DRAWING_THREAD_CREATION_FAILED",
            "TechDraw created a thread representation inconsistent with preflight.",
        )
    return NativeMutationDraft(
        value={"prepared": prepared, "created": created},
        recompute_targets=(prepared.target.view, prepared.target.page),
        changed=(object_identity(prepared.target.view),),
    )


def _postcondition_error(message: str) -> None:
    raise NativeMutationError("NATIVE_DRAWING_THREAD_POSTCONDITION_FAILED", message)


def _view_boundary(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in state.items()
        if key not in {"state_sha256", "visible_edge_count", "hidden_edge_count"}
    }


def _persistent_line_boundary(line: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in line.items()
        if key
        not in {
            "subelement",
            "line_state_sha256",
            "line_length_state_sha256",
        }
    }


def _require_old_lines_preserved(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> None:
    after_by_identity = {(line["kind"], line["tag"]): line for line in after["lines"]}
    for old in before["lines"]:
        current = after_by_identity.get((old["kind"], old["tag"]))
        if current is None or _persistent_line_boundary(
            current
        ) != _persistent_line_boundary(old):
            _postcondition_error(
                "Thread-representation creation changed an existing persistent line."
            )


def _verify_drawing_thread_representation(
    document: Any, draft: NativeMutationDraft
) -> dict[str, Any]:
    prepared: PreparedDrawingThreadRepresentation = draft.value["prepared"]
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
            "Thread-representation creation changed objects, page membership, or History."
        )
    if drawing_selection_state(document) != target.selection_before:
        _postcondition_error(
            "Thread-representation creation changed the human selection."
        )
    if drawing_visibility_state(document) != target.visibility_before:
        _postcondition_error(
            "Thread-representation creation changed object visibility."
        )
    if drawing_page_state(target.page) != target.page_state_before:
        _postcondition_error("Thread-representation creation changed the Drawing page.")
    if _view_boundary(drawing_view_state(target.view)) != _view_boundary(
        target.view_state_before
    ):
        _postcondition_error(
            "Thread-representation creation changed the Drawing view definition."
        )

    projection = drawing_projected_geometry_state(target.view)
    projected_by_name = {element["name"]: element for element in projection["elements"]}
    for source in target.element_states_before:
        current = projected_by_name.get(source["name"])
        if (
            current is None
            or current["element_state_sha256"] != source["element_state_sha256"]
        ):
            _postcondition_error(
                "A projected source changed while its thread representation was created."
            )

    attributes = drawing_line_attribute_inventory_state(target.view)
    lengths = drawing_line_length_inventory_state(target.view)
    if prepared.spec.orientation == "side":
        created_plan = draft.value["created"]
        assert isinstance(created_plan, dict)
        added_attributes = len(created_plan["lines"])
        added_lengths = added_attributes
    else:
        created_plans = draft.value["created"]
        assert isinstance(created_plans, list)
        added_attributes = len(created_plans)
        added_lengths = 0
    if (
        attributes["line_count"]
        != prepared.attribute_inventory_before["line_count"] + added_attributes
        or attributes["cosmetic_edge_count"]
        != prepared.attribute_inventory_before["cosmetic_edge_count"] + added_attributes
        or attributes["centerline_count"]
        != prepared.attribute_inventory_before["centerline_count"]
        or lengths["line_count"]
        != prepared.length_inventory_before["line_count"] + added_lengths
        or lengths["cosmetic_edge_count"]
        != prepared.length_inventory_before["cosmetic_edge_count"] + added_lengths
        or lengths["centerline_count"]
        != prepared.length_inventory_before["centerline_count"]
    ):
        _postcondition_error(
            "Thread-representation creation added an unexpected set of cosmetic geometry."
        )
    _require_old_lines_preserved(prepared.attribute_inventory_before, attributes)
    _require_old_lines_preserved(prepared.length_inventory_before, lengths)
    try:
        if prepared.spec.orientation == "side":
            state = drawing_thread_side_result_state(
                target.view,
                draft.value["created"],
                target.element_states_before,
            )
        else:
            state = drawing_thread_bottom_result_state(
                target.view,
                draft.value["created"],
                target.element_states_before,
            )
    except Exception as exc:
        _postcondition_error(
            "The created thread representation could not be resolved exactly: "
            f"{str(exc).strip()}"
        )
    return {
        "operation": prepared.spec.operation,
        "thread_representation": state,
    }


def verify_drawing_thread_representation(
    document: Any, draft: NativeMutationDraft
) -> dict[str, Any]:
    try:
        return _verify_drawing_thread_representation(document, draft)
    except NativeMutationError:
        raise
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_THREAD_POSTCONDITION_FAILED",
            "The thread representation could not be verified exactly: "
            f"{str(exc).strip()}",
        ) from exc
