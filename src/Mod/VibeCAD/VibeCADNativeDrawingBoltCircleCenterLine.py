# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact atomic creation of Drawing bolt-circle centerlines."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from VibeCADNativeDrawingBoltCircleCenterLineState import (
    MAX_DRAWING_BOLT_CIRCLE_TARGETS,
    MIN_DRAWING_BOLT_CIRCLE_TARGETS,
    drawing_bolt_circle_center_line_result_state,
    normalize_bolt_circle_center_line_host_plan,
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
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state
from VibeCADNativeDrawingLineAttributeState import (
    drawing_line_attribute_inventory_state,
)
from VibeCADNativeDrawingLineLengthState import (
    drawing_line_length_inventory_state,
)
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewState import drawing_view_state
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeTargets import object_identity


@dataclass(frozen=True, slots=True)
class PreparedDrawingBoltCircleCenterLines:
    target: PreparedDrawingDimensionTarget
    source_names: tuple[str, ...]
    host_validation: dict[str, Any]
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
    raw = values["holes"]
    if (
        not isinstance(raw, Sequence)
        or isinstance(raw, (str, bytes))
        or not MIN_DRAWING_BOLT_CIRCLE_TARGETS
        <= len(raw)
        <= MAX_DRAWING_BOLT_CIRCLE_TARGETS
        or any(not isinstance(item, Mapping) for item in raw)
    ):
        _error(
            "Bolt-circle centerlines require 3 to 32 ordered exact projected circle or arc targets.",
            "NATIVE_DRAWING_BOLT_CIRCLE_PARAMETERS_INVALID",
        )
    result = tuple(raw)
    names = []
    for item in result:
        exact = exact_drawing_mapping(
            item,
            frozenset({"subelement", "expected_element_state_sha256"}),
            "bolt-hole source",
            family="bolt-circle centerline",
            error_code="NATIVE_DRAWING_BOLT_CIRCLE_PARAMETERS_INVALID",
        )
        name = str(exact["subelement"] or "")
        if not name.startswith("Edge"):
            _error(
                "Each bolt-hole source must be an exact projected EdgeN.",
                "NATIVE_DRAWING_BOLT_CIRCLE_REFERENCE_TYPE_INVALID",
                repair={
                    "accepted_reference_types": [
                        "projected circle",
                        "projected circular arc",
                    ]
                },
            )
        names.append(name)
    if len(names) != len(set(names)):
        _error(
            "A bolt-circle request cannot repeat a projected hole source.",
            "NATIVE_DRAWING_BOLT_CIRCLE_REFERENCES_INVALID",
        )
    return result


def _validate_host(
    view: Any,
    source_names: tuple[str, ...],
    source_elements: tuple[Mapping[str, Any], ...],
) -> dict[str, Any]:
    try:
        import TechDrawGui

        validator = getattr(
            TechDrawGui, "validateDrawingBoltCircleCenterLines", None
        )
        if not callable(validator):
            _error(
                "The installed TechDraw runtime cannot validate bolt-circle centerlines.",
                "NATIVE_DRAWING_BOLT_CIRCLE_RUNTIME_UNAVAILABLE",
            )
        plan = normalize_bolt_circle_center_line_host_plan(
            validator(view, list(source_names)), created=False
        )
    except NativeDrawingError:
        raise
    except Exception as exc:
        _error(
            "TechDraw rejected the exact bolt-hole sources: "
            f"{str(exc).strip()}",
            "NATIVE_DRAWING_BOLT_CIRCLE_REFERENCES_INVALID",
            repair={
                "accepted_references": (
                    "3 to 32 ordered projected circular EdgeN targets; the first "
                    "three centers define the pattern circle"
                ),
                "requested_subelements": list(source_names),
                "inspect_operation": "drawing_projected_geometry",
            },
        )
    if len(plan["holes"]) != len(source_elements):
        _error(
            "TechDraw returned an incomplete bolt-circle plan.",
            "NATIVE_DRAWING_BOLT_CIRCLE_RUNTIME_UNAVAILABLE",
        )
    for hole, source in zip(plan["holes"], source_elements, strict=True):
        if (
            hole["source_subelement"] != source["name"]
            or source["element_type"] != "edge"
            or "center_in_view_mm" not in source
            or "radius_view_mm" not in source
        ):
            _error(
                "TechDraw's bolt-circle plan does not match the exact projected hole sources.",
                "NATIVE_DRAWING_BOLT_CIRCLE_RUNTIME_UNAVAILABLE",
            )
    return plan


def prepare_drawing_bolt_circle_center_lines(
    document: Any,
    *,
    values: Mapping[str, Any],
) -> PreparedDrawingBoltCircleCenterLines:
    source_targets = _source_targets(values)
    target = prepare_drawing_dimension_target(
        document,
        page_target=values["page"],
        view_target=values["view"],
        element_targets=source_targets,
        allowed_element_types=frozenset({"edge"}),
        family="bolt-circle centerline",
        code_prefix="NATIVE_DRAWING_BOLT_CIRCLE",
    )
    source_names = tuple(
        str(item["name"]) for item in target.element_states_before
    )
    return PreparedDrawingBoltCircleCenterLines(
        target=target,
        source_names=source_names,
        host_validation=_validate_host(
            target.view, source_names, target.element_states_before
        ),
        attribute_inventory_before=drawing_line_attribute_inventory_state(
            target.view
        ),
        length_inventory_before=drawing_line_length_inventory_state(
            target.view
        ),
    )


def _without_created_tags(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **{
            key: value
            for key, value in plan.items()
            if key != "pattern_circle_tag"
        },
        "holes": [
            {
                **{
                    key: value
                    for key, value in hole.items()
                    if key != "center_line"
                },
                "center_line": {
                    key: value
                    for key, value in hole["center_line"].items()
                    if key != "tag"
                },
            }
            for hole in plan["holes"]
        ],
    }


def mutate_drawing_bolt_circle_center_lines(
    _document: Any,
    *,
    prepared: PreparedDrawingBoltCircleCenterLines,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedDrawingBoltCircleCenterLines):
        raise TypeError("prepared must be PreparedDrawingBoltCircleCenterLines")
    import TechDrawGui

    try:
        created = normalize_bolt_circle_center_line_host_plan(
            TechDrawGui.createDrawingBoltCircleCenterLines(
                prepared.target.view, list(prepared.source_names)
            ),
            created=True,
        )
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_BOLT_CIRCLE_CREATION_FAILED",
            "TechDraw could not create the exact bolt-circle centerlines: "
            f"{str(exc).strip()}",
        ) from exc
    if _without_created_tags(created) != prepared.host_validation:
        raise NativeMutationError(
            "NATIVE_DRAWING_BOLT_CIRCLE_CREATION_FAILED",
            "TechDraw created bolt-circle centerlines inconsistent with preflight.",
        )
    return NativeMutationDraft(
        value={"prepared": prepared, "created": created},
        recompute_targets=(prepared.target.view, prepared.target.page),
        changed=(object_identity(prepared.target.view),),
    )


def _postcondition_error(message: str) -> None:
    raise NativeMutationError(
        "NATIVE_DRAWING_BOLT_CIRCLE_POSTCONDITION_FAILED", message
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
    before: Mapping[str, Any], after: Mapping[str, Any]
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
                "Bolt-circle creation changed an existing persistent line."
            )


def _verify_drawing_bolt_circle_center_lines(
    document: Any, draft: NativeMutationDraft
) -> dict[str, Any]:
    prepared: PreparedDrawingBoltCircleCenterLines = draft.value["prepared"]
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
            "Bolt-circle creation changed objects, page membership, or History."
        )
    if drawing_selection_state(document) != target.selection_before:
        _postcondition_error("Bolt-circle creation changed the human selection.")
    if drawing_visibility_state(document) != target.visibility_before:
        _postcondition_error("Bolt-circle creation changed object visibility.")
    if drawing_page_state(target.page) != target.page_state_before:
        _postcondition_error("Bolt-circle creation changed the Drawing page.")
    if _view_boundary(drawing_view_state(target.view)) != _view_boundary(
        target.view_state_before
    ):
        _postcondition_error(
            "Bolt-circle creation changed the Drawing view definition."
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
                "A source hole changed while its bolt-circle centerlines were created."
            )

    attributes = drawing_line_attribute_inventory_state(target.view)
    lengths = drawing_line_length_inventory_state(target.view)
    hole_count = len(prepared.source_names)
    if (
        attributes["line_count"]
        != prepared.attribute_inventory_before["line_count"] + hole_count + 1
        or attributes["cosmetic_edge_count"]
        != prepared.attribute_inventory_before["cosmetic_edge_count"]
        + hole_count
        + 1
        or attributes["centerline_count"]
        != prepared.attribute_inventory_before["centerline_count"]
        or lengths["line_count"]
        != prepared.length_inventory_before["line_count"] + hole_count
        or lengths["cosmetic_edge_count"]
        != prepared.length_inventory_before["cosmetic_edge_count"] + hole_count
        or lengths["centerline_count"]
        != prepared.length_inventory_before["centerline_count"]
    ):
        _postcondition_error(
            "Bolt-circle creation did not add exactly one pattern circle and one radial line per hole."
        )
    _require_old_lines_preserved(
        prepared.attribute_inventory_before, attributes
    )
    _require_old_lines_preserved(prepared.length_inventory_before, lengths)
    try:
        state = drawing_bolt_circle_center_line_result_state(
            target.view,
            draft.value["created"],
            target.element_states_before,
        )
    except Exception as exc:
        _postcondition_error(
            "The created bolt-circle centerlines could not be resolved exactly: "
            f"{str(exc).strip()}"
        )
    return {"operation": "create", "bolt_circle_center_lines": state}


def verify_drawing_bolt_circle_center_lines(
    document: Any, draft: NativeMutationDraft
) -> dict[str, Any]:
    try:
        return _verify_drawing_bolt_circle_center_lines(document, draft)
    except NativeMutationError:
        raise
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_BOLT_CIRCLE_POSTCONDITION_FAILED",
            "The bolt-circle centerlines could not be verified exactly: "
            f"{str(exc).strip()}",
        ) from exc
