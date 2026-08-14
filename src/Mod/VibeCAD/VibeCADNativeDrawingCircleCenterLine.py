# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact atomic creation of Drawing circle centerline crosses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from VibeCADNativeDrawingCircleCenterLineState import (
    MAX_DRAWING_CIRCLE_CENTER_LINE_TARGETS,
    drawing_circle_center_line_result_state,
    normalize_circle_center_line_host_pairs,
)
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
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state
from VibeCADNativeDrawingLineAttributeState import (
    drawing_line_attribute_inventory_state,
)
from VibeCADNativeDrawingLineLengthState import (
    drawing_line_length_inventory_state,
)
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewState import drawing_view_state
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeTargets import object_identity


@dataclass(frozen=True, slots=True)
class PreparedDrawingCircleCenterLines:
    target: PreparedDrawingDimensionTarget
    source_names: tuple[str, ...]
    host_validation: tuple[dict[str, Any], ...]
    attribute_inventory_before: dict[str, Any]
    length_inventory_before: dict[str, Any]


def _error(
    message: str,
    code: str,
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    drawing_dimension_error(message, code, repair=repair)


def _source_targets(values: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw = values["circles"]
    if (
        not isinstance(raw, Sequence)
        or isinstance(raw, (str, bytes))
        or not 1 <= len(raw) <= MAX_DRAWING_CIRCLE_CENTER_LINE_TARGETS
        or any(not isinstance(item, Mapping) for item in raw)
    ):
        _error(
            "Circle centerlines require 1 to 32 exact projected circle or arc targets.",
            "NATIVE_DRAWING_CIRCLE_CENTER_LINE_PARAMETERS_INVALID",
        )
    result = tuple(raw)
    names = []
    for item in result:
        exact = exact_drawing_mapping(
            item,
            frozenset({"subelement", "expected_element_state_sha256"}),
            "circle centerline source",
            family="circle centerline",
            error_code=(
                "NATIVE_DRAWING_CIRCLE_CENTER_LINE_PARAMETERS_INVALID"
            ),
        )
        name = str(exact["subelement"] or "")
        if not name.startswith("Edge"):
            _error(
                "Each circle centerline source must be an exact projected EdgeN.",
                "NATIVE_DRAWING_CIRCLE_CENTER_LINE_REFERENCE_TYPE_INVALID",
                repair={"accepted_reference_types": ["projected circle", "projected circular arc"]},
            )
        names.append(name)
    if len(names) != len(set(names)):
        _error(
            "A circle centerline request cannot repeat a projected source.",
            "NATIVE_DRAWING_CIRCLE_CENTER_LINE_REFERENCES_INVALID",
        )
    return result


def _validate_host(
    view: Any,
    source_names: tuple[str, ...],
    source_elements: tuple[Mapping[str, Any], ...],
) -> tuple[dict[str, Any], ...]:
    try:
        import TechDrawGui

        validator = getattr(
            TechDrawGui,
            "validateDrawingCircleCenterLines",
            None,
        )
        if not callable(validator):
            _error(
                "The installed TechDraw runtime cannot validate circle centerlines.",
                "NATIVE_DRAWING_CIRCLE_CENTER_LINE_RUNTIME_UNAVAILABLE",
            )
        pairs = normalize_circle_center_line_host_pairs(
            validator(view, list(source_names)),
            created=False,
        )
    except NativeDrawingError:
        raise
    except Exception as exc:
        _error(
            "TechDraw rejected the exact circle centerline sources: "
            f"{str(exc).strip()}",
            "NATIVE_DRAWING_CIRCLE_CENTER_LINE_REFERENCES_INVALID",
            repair={
                "accepted_references": "1 to 32 projected circular EdgeN targets",
                "requested_subelements": list(source_names),
                "inspect_operation": "drawing_projected_geometry",
            },
        )
    if len(pairs) != len(source_elements):
        _error(
            "TechDraw returned an incomplete circle centerline plan.",
            "NATIVE_DRAWING_CIRCLE_CENTER_LINE_RUNTIME_UNAVAILABLE",
        )
    for pair, source in zip(pairs, source_elements, strict=True):
        if (
            pair["source_subelement"] != source["name"]
            or source["element_type"] != "edge"
            or "center_in_view_mm" not in source
            or "radius_view_mm" not in source
        ):
            _error(
                "TechDraw's circle centerline plan does not match the exact projected sources.",
                "NATIVE_DRAWING_CIRCLE_CENTER_LINE_RUNTIME_UNAVAILABLE",
            )
    return tuple(pairs)


def prepare_drawing_circle_center_lines(
    document: Any,
    *,
    values: Mapping[str, Any],
) -> PreparedDrawingCircleCenterLines:
    source_targets = _source_targets(values)
    target = prepare_drawing_dimension_target(
        document,
        page_target=values["page"],
        view_target=values["view"],
        element_targets=source_targets,
        allowed_element_types=frozenset({"edge"}),
        family="circle centerline",
        code_prefix="NATIVE_DRAWING_CIRCLE_CENTER_LINE",
    )
    source_names = tuple(
        str(item["name"]) for item in target.element_states_before
    )
    return PreparedDrawingCircleCenterLines(
        target=target,
        source_names=source_names,
        host_validation=_validate_host(
            target.view,
            source_names,
            target.element_states_before,
        ),
        attribute_inventory_before=drawing_line_attribute_inventory_state(
            target.view
        ),
        length_inventory_before=drawing_line_length_inventory_state(
            target.view
        ),
    )


def _without_created_tags(pair: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **{
            key: value
            for key, value in pair.items()
            if key not in {"horizontal", "vertical"}
        },
        "horizontal": {
            key: value
            for key, value in pair["horizontal"].items()
            if key != "tag"
        },
        "vertical": {
            key: value
            for key, value in pair["vertical"].items()
            if key != "tag"
        },
    }


def mutate_drawing_circle_center_lines(
    _document: Any,
    *,
    prepared: PreparedDrawingCircleCenterLines,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedDrawingCircleCenterLines):
        raise TypeError("prepared must be PreparedDrawingCircleCenterLines")
    import TechDrawGui

    try:
        created = normalize_circle_center_line_host_pairs(
            TechDrawGui.createDrawingCircleCenterLines(
                prepared.target.view,
                list(prepared.source_names),
            ),
            created=True,
        )
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_CIRCLE_CENTER_LINE_CREATION_FAILED",
            "TechDraw could not create the exact circle centerlines: "
            f"{str(exc).strip()}",
        ) from exc
    plans = tuple(_without_created_tags(pair) for pair in created)
    if plans != prepared.host_validation:
        raise NativeMutationError(
            "NATIVE_DRAWING_CIRCLE_CENTER_LINE_CREATION_FAILED",
            "TechDraw created circle centerlines inconsistent with preflight.",
        )
    return NativeMutationDraft(
        value={"prepared": prepared, "created": tuple(created)},
        recompute_targets=(prepared.target.view, prepared.target.page),
        changed=(object_identity(prepared.target.view),),
    )


def _postcondition_error(message: str) -> None:
    raise NativeMutationError(
        "NATIVE_DRAWING_CIRCLE_CENTER_LINE_POSTCONDITION_FAILED",
        message,
    )


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
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> None:
    after_by_identity = {
        (line["kind"], line["tag"]): line for line in after["lines"]
    }
    for old in before["lines"]:
        current = after_by_identity.get((old["kind"], old["tag"]))
        if (
            current is None
            or _persistent_line_boundary(current)
            != _persistent_line_boundary(old)
        ):
            _postcondition_error(
                "Circle centerline creation changed an existing persistent line."
            )


def _verify_drawing_circle_center_lines(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedDrawingCircleCenterLines = draft.value["prepared"]
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
            "Circle centerline creation changed objects, page membership, or History."
        )
    if drawing_selection_state(document) != target.selection_before:
        _postcondition_error("Circle centerline creation changed the human selection.")
    if drawing_visibility_state(document) != target.visibility_before:
        _postcondition_error("Circle centerline creation changed object visibility.")
    if drawing_page_state(target.page) != target.page_state_before:
        _postcondition_error("Circle centerline creation changed the Drawing page.")
    if _view_boundary(drawing_view_state(target.view)) != _view_boundary(
        target.view_state_before
    ):
        _postcondition_error(
            "Circle centerline creation changed the Drawing view definition."
        )

    projection = drawing_projected_geometry_state(target.view)
    projected_by_name = {
        element["name"]: element for element in projection["elements"]
    }
    for source in target.element_states_before:
        current = projected_by_name.get(source["name"])
        if (
            current is None
            or current["element_state_sha256"]
            != source["element_state_sha256"]
        ):
            _postcondition_error(
                "A source circle changed while its centerlines were created."
            )

    attributes = drawing_line_attribute_inventory_state(target.view)
    lengths = drawing_line_length_inventory_state(target.view)
    created_count = len(prepared.source_names) * 2
    if (
        attributes["line_count"]
        != prepared.attribute_inventory_before["line_count"] + created_count
        or attributes["cosmetic_edge_count"]
        != prepared.attribute_inventory_before["cosmetic_edge_count"]
        + created_count
        or attributes["centerline_count"]
        != prepared.attribute_inventory_before["centerline_count"]
        or lengths["line_count"]
        != prepared.length_inventory_before["line_count"] + created_count
        or lengths["cosmetic_edge_count"]
        != prepared.length_inventory_before["cosmetic_edge_count"]
        + created_count
        or lengths["centerline_count"]
        != prepared.length_inventory_before["centerline_count"]
    ):
        _postcondition_error(
            "Circle centerline creation did not add exactly two persistent lines per source."
        )
    _require_old_lines_preserved(
        prepared.attribute_inventory_before,
        attributes,
    )
    _require_old_lines_preserved(
        prepared.length_inventory_before,
        lengths,
    )
    try:
        state = drawing_circle_center_line_result_state(
            target.view,
            draft.value["created"],
            target.element_states_before,
        )
    except Exception as exc:
        _postcondition_error(
            "The created circle centerlines could not be resolved exactly: "
            f"{str(exc).strip()}"
        )
    return {"operation": "create", "circle_center_lines": state}


def verify_drawing_circle_center_lines(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    try:
        return _verify_drawing_circle_center_lines(document, draft)
    except NativeMutationError:
        raise
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_CIRCLE_CENTER_LINE_POSTCONDITION_FAILED",
            "The circle centerlines could not be verified exactly: "
            f"{str(exc).strip()}",
        ) from exc
