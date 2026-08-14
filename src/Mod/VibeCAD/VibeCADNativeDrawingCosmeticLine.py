# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact atomic creation of Drawing parallel and perpendicular cosmetic lines."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from VibeCADNativeDrawingCosmeticLineState import (
    DRAWING_COSMETIC_LINE_CONSTRUCTIONS,
    MAX_DRAWING_COSMETIC_LINES,
    drawing_cosmetic_line_inventory_state,
    drawing_cosmetic_line_result_state,
    drawing_two_point_cosmetic_line_result_state,
    normalize_cosmetic_line_host_plan,
    normalize_two_point_cosmetic_line_host_plan,
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
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewState import drawing_view_state
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeTargets import object_identity


_OPERATIONS = frozenset(
    f"create_{construction}" for construction in DRAWING_COSMETIC_LINE_CONSTRUCTIONS
) | frozenset({"create_between_vertices"})
_TARGET_FIELDS = frozenset({"subelement", "expected_element_state_sha256"})


@dataclass(frozen=True, slots=True)
class DrawingCosmeticLineSpec:
    operation: str
    construction: str
    reference_edge_name: str | None
    through_vertex_name: str | None
    source_vertex_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PreparedDrawingCosmeticLine:
    target: PreparedDrawingDimensionTarget
    spec: DrawingCosmeticLineSpec
    host_validation: dict[str, Any]
    inventory_before: dict[str, Any]


def _error(
    message: str,
    code: str,
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    drawing_dimension_error(message, code, repair=repair)


def _target(
    values: Mapping[str, Any],
    field: str,
    prefix: str,
    accepted_type: str,
) -> Mapping[str, Any]:
    exact = exact_drawing_mapping(
        values[field],
        _TARGET_FIELDS,
        field.replace("_", " "),
        family="cosmetic line",
        error_code="NATIVE_DRAWING_COSMETIC_LINE_PARAMETERS_INVALID",
    )
    name = str(exact["subelement"] or "")
    if not name.startswith(prefix):
        _error(
            f"Cosmetic line {field} must be an exact projected {prefix}N.",
            "NATIVE_DRAWING_COSMETIC_LINE_REFERENCE_TYPE_INVALID",
            repair={"accepted_reference_types": [accepted_type]},
        )
    return exact


def _two_point_targets(
    values: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    raw = values["vertices"]
    if (
        not isinstance(raw, Sequence)
        or isinstance(raw, (str, bytes))
        or len(raw) != 2
        or any(not isinstance(item, Mapping) for item in raw)
    ):
        _error(
            "A two-point cosmetic line requires exactly two exact projected vertices.",
            "NATIVE_DRAWING_COSMETIC_LINE_PARAMETERS_INVALID",
        )
    targets = tuple(
        exact_drawing_mapping(
            item,
            _TARGET_FIELDS,
            "two-point cosmetic-line source",
            family="cosmetic line",
            error_code="NATIVE_DRAWING_COSMETIC_LINE_PARAMETERS_INVALID",
        )
        for item in raw
    )
    names = [str(item["subelement"] or "") for item in targets]
    if any(not name.startswith("Vertex") for name in names):
        _error(
            "Each two-point cosmetic-line source must be an exact projected VertexN.",
            "NATIVE_DRAWING_COSMETIC_LINE_REFERENCE_TYPE_INVALID",
            repair={"accepted_reference_types": ["projected vertex"]},
        )
    if len(set(names)) != 2:
        _error(
            "A two-point cosmetic line requires two different projected vertices.",
            "NATIVE_DRAWING_COSMETIC_LINE_REFERENCES_INVALID",
        )
    return targets


def _validate_host(
    view: Any,
    spec: DrawingCosmeticLineSpec,
    source_elements: tuple[Mapping[str, Any], ...],
) -> dict[str, Any]:
    try:
        import TechDrawGui

        if spec.operation == "create_between_vertices":
            validator = getattr(
                TechDrawGui, "validateDrawingTwoPointCosmeticLine", None
            )
        else:
            validator = getattr(TechDrawGui, "validateDrawingCosmeticLine", None)
        if not callable(validator):
            _error(
                "The installed TechDraw runtime cannot validate cosmetic lines.",
                "NATIVE_DRAWING_COSMETIC_LINE_RUNTIME_UNAVAILABLE",
            )
        if spec.operation == "create_between_vertices":
            plan = normalize_two_point_cosmetic_line_host_plan(
                validator(view, list(spec.source_vertex_names)),
                created=False,
            )
        else:
            assert spec.reference_edge_name is not None
            assert spec.through_vertex_name is not None
            plan = normalize_cosmetic_line_host_plan(
                validator(
                    view,
                    spec.construction,
                    spec.reference_edge_name,
                    spec.through_vertex_name,
                ),
                created=False,
            )
    except NativeDrawingError:
        raise
    except Exception as exc:
        _error(
            "TechDraw rejected the exact cosmetic-line construction: "
            f"{str(exc).strip()}",
            "NATIVE_DRAWING_COSMETIC_LINE_REFERENCES_INVALID",
            repair={
                "construction": spec.construction,
                "required_roles": ["reference_edge", "through_vertex"],
                "requested_subelements": list(spec.source_vertex_names)
                if spec.operation == "create_between_vertices"
                else [spec.reference_edge_name, spec.through_vertex_name],
                "accepted_reference_types": [
                    "projected straight edge",
                    "projected vertex",
                ],
                "inspect_operation": "drawing_projected_geometry",
            },
        )
    if spec.operation == "create_between_vertices":
        valid = (
            len(source_elements) == 2
            and all(item["element_type"] == "vertex" for item in source_elements)
            and plan["construction"] == "between_vertices"
            and plan["source_vertex_subelements"]
            == list(spec.source_vertex_names)
        )
    else:
        valid = (
            len(source_elements) == 2
            and source_elements[0]["element_type"] == "edge"
            and source_elements[1]["element_type"] == "vertex"
            and plan["construction"] == spec.construction
            and plan["reference_edge_subelement"] == spec.reference_edge_name
            and plan["through_vertex_subelement"] == spec.through_vertex_name
        )
    if not valid:
        _error(
            "TechDraw's cosmetic-line plan does not match the exact projected roles.",
            "NATIVE_DRAWING_COSMETIC_LINE_RUNTIME_UNAVAILABLE",
        )
    return plan


def prepare_drawing_cosmetic_line(
    document: Any,
    *,
    operation: str,
    values: Mapping[str, Any],
) -> PreparedDrawingCosmeticLine:
    if operation not in _OPERATIONS:
        raise ValueError("operation is not a Drawing cosmetic-line operation")
    if operation == "create_between_vertices":
        element_targets = _two_point_targets(values)
        allowed_types = frozenset({"vertex"})
    else:
        reference_edge = _target(
            values,
            "reference_edge",
            "Edge",
            "projected straight edge",
        )
        through_vertex = _target(
            values,
            "through_vertex",
            "Vertex",
            "projected vertex",
        )
        element_targets = (reference_edge, through_vertex)
        allowed_types = frozenset({"edge", "vertex"})
    target = prepare_drawing_dimension_target(
        document,
        page_target=values["page"],
        view_target=values["view"],
        element_targets=element_targets,
        allowed_element_types=allowed_types,
        family="cosmetic line",
        code_prefix="NATIVE_DRAWING_COSMETIC_LINE",
    )
    if operation != "create_between_vertices" and (
        target.element_states_before[0]["element_type"] != "edge"
        or target.element_states_before[1]["element_type"] != "vertex"
    ):
        _error(
            "Cosmetic line roles require a projected straight EdgeN followed by a "
            "projected VertexN.",
            "NATIVE_DRAWING_COSMETIC_LINE_REFERENCE_TYPE_INVALID",
            repair={
                "reference_edge": "projected straight edge",
                "through_vertex": "projected vertex",
                "inspect_operation": "drawing_projected_geometry",
            },
        )
    spec = DrawingCosmeticLineSpec(
        operation=operation,
        construction=operation.removeprefix("create_"),
        reference_edge_name=(
            None
            if operation == "create_between_vertices"
            else target.element_states_before[0]["name"]
        ),
        through_vertex_name=(
            None
            if operation == "create_between_vertices"
            else target.element_states_before[1]["name"]
        ),
        source_vertex_names=(
            tuple(item["name"] for item in target.element_states_before)
            if operation == "create_between_vertices"
            else ()
        ),
    )
    inventory = drawing_cosmetic_line_inventory_state(target.view)
    if inventory["line_count"] >= MAX_DRAWING_COSMETIC_LINES:
        _error(
            "The Drawing cosmetic-line inventory already contains 4096 targets.",
            "NATIVE_DRAWING_COSMETIC_LINE_LIMIT_EXCEEDED",
        )
    return PreparedDrawingCosmeticLine(
        target=target,
        spec=spec,
        host_validation=_validate_host(
            target.view,
            spec,
            target.element_states_before,
        ),
        inventory_before=inventory,
    )


def mutate_drawing_cosmetic_line(
    _document: Any,
    *,
    prepared: PreparedDrawingCosmeticLine,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedDrawingCosmeticLine):
        raise TypeError("prepared must be PreparedDrawingCosmeticLine")
    import TechDrawGui

    try:
        if prepared.spec.operation == "create_between_vertices":
            created = normalize_two_point_cosmetic_line_host_plan(
                TechDrawGui.createDrawingTwoPointCosmeticLine(
                    prepared.target.view,
                    list(prepared.spec.source_vertex_names),
                ),
                created=True,
            )
        else:
            assert prepared.spec.reference_edge_name is not None
            assert prepared.spec.through_vertex_name is not None
            created = normalize_cosmetic_line_host_plan(
                TechDrawGui.createDrawingCosmeticLine(
                    prepared.target.view,
                    prepared.spec.construction,
                    prepared.spec.reference_edge_name,
                    prepared.spec.through_vertex_name,
                ),
                created=True,
            )
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_COSMETIC_LINE_CREATION_FAILED",
            f"TechDraw could not create the exact cosmetic line: {str(exc).strip()}",
        ) from exc
    if {key: value for key, value in created.items() if key != "line_tag"} != (
        prepared.host_validation
    ):
        raise NativeMutationError(
            "NATIVE_DRAWING_COSMETIC_LINE_CREATION_FAILED",
            "TechDraw created a cosmetic line inconsistent with preflight.",
        )
    return NativeMutationDraft(
        value={"prepared": prepared, "created": created},
        recompute_targets=(prepared.target.view, prepared.target.page),
        changed=(object_identity(prepared.target.view),),
    )


def _postcondition_error(message: str) -> None:
    raise NativeMutationError(
        "NATIVE_DRAWING_COSMETIC_LINE_POSTCONDITION_FAILED",
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
        if key not in {"subelement", "line_state_sha256"}
    }


def _require_old_lines_preserved(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> None:
    after_by_tag = {item["tag"]: item for item in after["lines"]}
    for old in before["lines"]:
        current = after_by_tag.get(old["tag"])
        if current is None or _persistent_line_boundary(
            current
        ) != _persistent_line_boundary(old):
            _postcondition_error(
                "Cosmetic-line creation changed an existing persistent line."
            )


def _verify_drawing_cosmetic_line(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedDrawingCosmeticLine = draft.value["prepared"]
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
            "Cosmetic-line creation changed objects, page membership, or History."
        )
    if drawing_selection_state(document) != target.selection_before:
        _postcondition_error("Cosmetic-line creation changed the human selection.")
    if drawing_visibility_state(document) != target.visibility_before:
        _postcondition_error("Cosmetic-line creation changed object visibility.")
    if drawing_page_state(target.page) != target.page_state_before:
        _postcondition_error("Cosmetic-line creation changed the Drawing page.")
    if _view_boundary(drawing_view_state(target.view)) != _view_boundary(
        target.view_state_before
    ):
        _postcondition_error(
            "Cosmetic-line creation changed the Drawing view definition."
        )

    projection = drawing_projected_geometry_state(target.view)
    projected_by_name = {item["name"]: item for item in projection["elements"]}
    for source in target.element_states_before:
        current = projected_by_name.get(source["name"])
        if (
            current is None
            or current["element_state_sha256"] != source["element_state_sha256"]
        ):
            _postcondition_error(
                "A projected source changed while its cosmetic line was created."
            )

    inventory = drawing_cosmetic_line_inventory_state(target.view)
    if inventory["line_count"] != prepared.inventory_before["line_count"] + 1:
        _postcondition_error(
            "Cosmetic-line creation did not add exactly one persistent line."
        )
    _require_old_lines_preserved(prepared.inventory_before, inventory)
    try:
        if prepared.spec.operation == "create_between_vertices":
            result = drawing_two_point_cosmetic_line_result_state(
                target.view,
                draft.value["created"],
                target.element_states_before,
            )
        else:
            result = drawing_cosmetic_line_result_state(
                target.view,
                draft.value["created"],
                target.element_states_before,
            )
    except Exception as exc:
        _postcondition_error(
            "The created cosmetic line could not be resolved exactly: "
            f"{str(exc).strip()}"
        )
    return {
        "operation": prepared.spec.operation,
        "cosmetic_line": result,
    }


def verify_drawing_cosmetic_line(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    try:
        return _verify_drawing_cosmetic_line(document, draft)
    except NativeMutationError:
        raise
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_COSMETIC_LINE_POSTCONDITION_FAILED",
            "The cosmetic line could not be verified "
            f"exactly: {str(exc).strip()}",
        ) from exc
