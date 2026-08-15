# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact atomic creation of Drawing cosmetic vertices."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from VibeCADNativeDrawingCosmeticVertexState import (
    MAX_DRAWING_COSMETIC_VERTICES,
    drawing_cosmetic_vertex_inventory_state,
    drawing_explicit_vertex_result_state,
    drawing_intersection_vertex_result_state,
    drawing_midpoint_vertex_result_state,
    drawing_offset_vertex_result_state,
    drawing_quadrant_vertex_result_state,
    normalize_drawing_vertex_point,
    normalize_drawing_vertex_offset,
    normalize_explicit_vertex_host_plan,
    normalize_midpoint_vertex_host_plan,
    normalize_offset_vertex_host_plan,
    normalize_quadrant_vertex_host_plan,
    normalize_vertex_intersection_host_plan,
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
    {
        "create_intersections",
        "create_offset",
        "create_point",
        "create_midpoints",
        "create_quadrants",
    }
)


@dataclass(frozen=True, slots=True)
class DrawingCosmeticVertexSpec:
    operation: str
    source_names: tuple[str, ...]
    offset_mm: dict[str, float] | None
    point_in_view_mm: dict[str, float] | None


@dataclass(frozen=True, slots=True)
class PreparedDrawingCosmeticVertex:
    target: PreparedDrawingDimensionTarget
    spec: DrawingCosmeticVertexSpec
    host_validation: dict[str, Any]
    inventory_before: dict[str, Any]


def _error(
    message: str,
    code: str,
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    drawing_dimension_error(message, code, repair=repair)


def _intersection_targets(values: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw = values["edges"]
    if (
        not isinstance(raw, Sequence)
        or isinstance(raw, (str, bytes))
        or len(raw) != 2
        or any(not isinstance(item, Mapping) for item in raw)
    ):
        _error(
            "Intersection vertices require exactly two exact projected edges.",
            "NATIVE_DRAWING_COSMETIC_VERTEX_PARAMETERS_INVALID",
        )
    targets = tuple(raw)
    names = []
    for item in targets:
        exact = exact_drawing_mapping(
            item,
            frozenset({"subelement", "expected_element_state_sha256"}),
            "intersection source",
            family="cosmetic vertex",
            error_code="NATIVE_DRAWING_COSMETIC_VERTEX_PARAMETERS_INVALID",
        )
        name = str(exact["subelement"] or "")
        if not name.startswith("Edge"):
            _error(
                "Each intersection source must be an exact projected EdgeN.",
                "NATIVE_DRAWING_COSMETIC_VERTEX_REFERENCE_TYPE_INVALID",
                repair={"accepted_reference_types": ["projected edge"]},
            )
        names.append(name)
    if len(set(names)) != 2:
        _error(
            "Intersection vertices require two different projected edges.",
            "NATIVE_DRAWING_COSMETIC_VERTEX_REFERENCES_INVALID",
        )
    return targets


def _offset_target(
    values: Mapping[str, Any],
) -> tuple[tuple[Mapping[str, Any], ...], dict[str, float]]:
    exact = exact_drawing_mapping(
        values["source_vertex"],
        frozenset({"subelement", "expected_element_state_sha256"}),
        "offset source",
        family="cosmetic vertex",
        error_code="NATIVE_DRAWING_COSMETIC_VERTEX_PARAMETERS_INVALID",
    )
    name = str(exact["subelement"] or "")
    if not name.startswith("Vertex"):
        _error(
            "An offset cosmetic vertex requires one exact projected VertexN.",
            "NATIVE_DRAWING_COSMETIC_VERTEX_REFERENCE_TYPE_INVALID",
            repair={"accepted_reference_types": ["projected vertex"]},
        )
    try:
        offset = normalize_drawing_vertex_offset(values["offset_mm"])
    except Exception as exc:
        _error(
            f"The explicit Drawing-view offset is invalid: {str(exc).strip()}",
            "NATIVE_DRAWING_COSMETIC_VERTEX_PARAMETERS_INVALID",
        )
    return (exact,), offset


def _derived_edge_targets(
    values: Mapping[str, Any],
    *,
    noun: str,
) -> tuple[Mapping[str, Any], ...]:
    raw = values["edges"]
    if (
        not isinstance(raw, Sequence)
        or isinstance(raw, (str, bytes))
        or not 1 <= len(raw) <= 64
        or any(not isinstance(item, Mapping) for item in raw)
    ):
        _error(
            f"{noun.title()} vertices require between one and 64 exact projected edges.",
            "NATIVE_DRAWING_COSMETIC_VERTEX_PARAMETERS_INVALID",
        )
    targets = tuple(raw)
    names = []
    for item in targets:
        exact = exact_drawing_mapping(
            item,
            frozenset({"subelement", "expected_element_state_sha256"}),
            f"{noun} source",
            family="cosmetic vertex",
            error_code="NATIVE_DRAWING_COSMETIC_VERTEX_PARAMETERS_INVALID",
        )
        name = str(exact["subelement"] or "")
        if not name.startswith("Edge"):
            _error(
                f"Each {noun} source must be an exact projected EdgeN.",
                "NATIVE_DRAWING_COSMETIC_VERTEX_REFERENCE_TYPE_INVALID",
                repair={"accepted_reference_types": ["projected edge"]},
            )
        names.append(name)
    if len(names) != len(set(names)):
        _error(
            f"A {noun} source edge cannot be repeated.",
            "NATIVE_DRAWING_COSMETIC_VERTEX_REFERENCES_INVALID",
        )
    return targets


def _explicit_point(values: Mapping[str, Any]) -> dict[str, float]:
    try:
        return normalize_drawing_vertex_point(values["point_in_view_mm"])
    except Exception as exc:
        _error(
            f"The explicit Drawing-view point is invalid: {str(exc).strip()}",
            "NATIVE_DRAWING_COSMETIC_VERTEX_PARAMETERS_INVALID",
        )


def _planned_vertex_count(operation: str, plan: Mapping[str, Any]) -> int:
    if operation == "create_intersections":
        return len(plan["vertices"])
    if operation == "create_midpoints":
        return len(plan["midpoints"])
    if operation == "create_quadrants":
        return sum(len(source["vertices"]) for source in plan["sources"])
    return 1


def _validate_host(
    view: Any,
    spec: DrawingCosmeticVertexSpec,
    source_elements: tuple[Mapping[str, Any], ...],
) -> dict[str, Any]:
    try:
        import TechDrawGui

        if spec.operation == "create_intersections":
            validator = getattr(TechDrawGui, "validateDrawingVertexIntersections", None)
            if not callable(validator):
                _error(
                    "The installed TechDraw runtime cannot validate intersection vertices.",
                    "NATIVE_DRAWING_COSMETIC_VERTEX_RUNTIME_UNAVAILABLE",
                )
            plan = normalize_vertex_intersection_host_plan(
                validator(view, list(spec.source_names)), created=False
            )
        elif spec.operation == "create_midpoints":
            validator = getattr(
                TechDrawGui, "validateDrawingMidpointVertices", None
            )
            if not callable(validator):
                _error(
                    "The installed TechDraw runtime cannot validate midpoint vertices.",
                    "NATIVE_DRAWING_COSMETIC_VERTEX_RUNTIME_UNAVAILABLE",
                )
            plan = normalize_midpoint_vertex_host_plan(
                validator(view, list(spec.source_names)), created=False
            )
        elif spec.operation == "create_quadrants":
            validator = getattr(
                TechDrawGui, "validateDrawingQuadrantVertices", None
            )
            if not callable(validator):
                _error(
                    "The installed TechDraw runtime cannot validate quadrant vertices.",
                    "NATIVE_DRAWING_COSMETIC_VERTEX_RUNTIME_UNAVAILABLE",
                )
            plan = normalize_quadrant_vertex_host_plan(
                validator(view, list(spec.source_names)), created=False
            )
        elif spec.operation == "create_offset":
            validator = getattr(TechDrawGui, "validateDrawingOffsetVertex", None)
            if not callable(validator):
                _error(
                    "The installed TechDraw runtime cannot validate offset vertices.",
                    "NATIVE_DRAWING_COSMETIC_VERTEX_RUNTIME_UNAVAILABLE",
                )
            assert spec.offset_mm is not None
            plan = normalize_offset_vertex_host_plan(
                validator(
                    view,
                    spec.source_names[0],
                    spec.offset_mm["x_mm"],
                    spec.offset_mm["y_mm"],
                ),
                created=False,
            )
        else:
            validator = getattr(
                TechDrawGui, "validateDrawingCosmeticVertexPoint", None
            )
            if not callable(validator):
                _error(
                    "The installed TechDraw runtime cannot validate a cosmetic-vertex point.",
                    "NATIVE_DRAWING_COSMETIC_VERTEX_RUNTIME_UNAVAILABLE",
                )
            assert spec.point_in_view_mm is not None
            plan = normalize_explicit_vertex_host_plan(
                validator(
                    view,
                    spec.point_in_view_mm["x_mm"],
                    spec.point_in_view_mm["y_mm"],
                ),
                created=False,
            )
    except NativeDrawingError:
        raise
    except Exception as exc:
        action = {
            "create_intersections": "intersection sources",
            "create_offset": "offset source and coordinates",
            "create_point": "view and coordinates",
            "create_midpoints": "midpoint sources",
            "create_quadrants": "quadrant sources",
        }[spec.operation]
        _error(
            f"TechDraw rejected the exact cosmetic-vertex {action}: {str(exc).strip()}",
            "NATIVE_DRAWING_COSMETIC_VERTEX_REFERENCES_INVALID",
            repair={
                "accepted_references": {
                    "create_intersections": (
                        "exactly two distinct projected EdgeN targets with at least one intersection"
                    ),
                    "create_offset": (
                        "one exact projected VertexN and a finite unscaled X/Y offset"
                    ),
                    "create_point": (
                        "one exact Drawing view and a finite unscaled X/Y point"
                    ),
                    "create_midpoints": (
                        "between one and 64 unique exact projected EdgeN targets"
                    ),
                    "create_quadrants": (
                        "between one and 64 unique exact projected EdgeN targets"
                    ),
                }[spec.operation],
                "requested_subelements": list(spec.source_names),
                "inspect_operation": "drawing_projected_geometry",
            },
        )
    if (
        len(source_elements) != len(spec.source_names)
        or tuple(item["name"] for item in source_elements) != spec.source_names
    ):
        _error(
            "TechDraw's cosmetic-vertex plan does not match the exact projected sources.",
            "NATIVE_DRAWING_COSMETIC_VERTEX_RUNTIME_UNAVAILABLE",
        )
    if spec.operation == "create_intersections":
        if plan["source_subelements"] != list(spec.source_names) or any(
            item["element_type"] != "edge" for item in source_elements
        ):
            _error(
                "TechDraw's intersection plan does not match the exact projected edges.",
                "NATIVE_DRAWING_COSMETIC_VERTEX_RUNTIME_UNAVAILABLE",
            )
    elif spec.operation == "create_midpoints":
        if [
            item["source_subelement"] for item in plan["midpoints"]
        ] != list(spec.source_names) or any(
            item["element_type"] != "edge" for item in source_elements
        ):
            _error(
                "TechDraw's midpoint plan does not match the exact projected edges.",
                "NATIVE_DRAWING_COSMETIC_VERTEX_RUNTIME_UNAVAILABLE",
            )
    elif spec.operation == "create_quadrants":
        if [
            item["source_subelement"] for item in plan["sources"]
        ] != list(spec.source_names) or any(
            item["element_type"] != "edge" for item in source_elements
        ):
            _error(
                "TechDraw's quadrant plan does not match the exact projected edges.",
                "NATIVE_DRAWING_COSMETIC_VERTEX_RUNTIME_UNAVAILABLE",
            )
    elif spec.operation == "create_offset" and (
        plan["source_subelement"] != spec.source_names[0]
        or plan["offset_mm"] != spec.offset_mm
        or source_elements[0]["element_type"] != "vertex"
    ):
        _error(
            "TechDraw's offset plan does not match the exact projected vertex and offset.",
            "NATIVE_DRAWING_COSMETIC_VERTEX_RUNTIME_UNAVAILABLE",
        )
    elif (
        spec.operation == "create_point"
        and plan["point_in_view_mm"] != spec.point_in_view_mm
    ):
        _error(
            "TechDraw's explicit-point plan does not match the requested view point.",
            "NATIVE_DRAWING_COSMETIC_VERTEX_RUNTIME_UNAVAILABLE",
        )
    return plan


def prepare_drawing_cosmetic_vertex(
    document: Any,
    *,
    operation: str,
    values: Mapping[str, Any],
) -> PreparedDrawingCosmeticVertex:
    if operation not in _OPERATIONS:
        raise ValueError("operation is not a Drawing cosmetic-vertex operation")
    point = None
    if operation == "create_intersections":
        source_targets = _intersection_targets(values)
        offset = None
        allowed = frozenset({"edge"})
    elif operation == "create_offset":
        source_targets, offset = _offset_target(values)
        allowed = frozenset({"vertex"})
    elif operation == "create_midpoints":
        source_targets = _derived_edge_targets(values, noun="midpoint")
        offset = None
        allowed = frozenset({"edge"})
    elif operation == "create_quadrants":
        source_targets = _derived_edge_targets(values, noun="quadrant")
        offset = None
        allowed = frozenset({"edge"})
    else:
        source_targets = ()
        offset = None
        point = _explicit_point(values)
        allowed = frozenset()
    target = prepare_drawing_dimension_target(
        document,
        page_target=values["page"],
        view_target=values["view"],
        element_targets=source_targets,
        allowed_element_types=allowed,
        family="cosmetic vertex",
        code_prefix="NATIVE_DRAWING_COSMETIC_VERTEX",
    )
    spec = DrawingCosmeticVertexSpec(
        operation=operation,
        source_names=tuple(item["name"] for item in target.element_states_before),
        offset_mm=offset,
        point_in_view_mm=point,
    )
    inventory = drawing_cosmetic_vertex_inventory_state(target.view)
    host_validation = _validate_host(
        target.view,
        spec,
        target.element_states_before,
    )
    expected_additions = _planned_vertex_count(operation, host_validation)
    if inventory["vertex_count"] + expected_additions > MAX_DRAWING_COSMETIC_VERTICES:
        _error(
            "The resulting Drawing cosmetic-vertex inventory would exceed 4096 targets.",
            "NATIVE_DRAWING_COSMETIC_VERTEX_LIMIT_EXCEEDED",
        )
    return PreparedDrawingCosmeticVertex(
        target=target,
        spec=spec,
        host_validation=host_validation,
        inventory_before=inventory,
    )


def _without_created_tags(operation: str, plan: Mapping[str, Any]) -> dict[str, Any]:
    if operation == "create_intersections":
        return {
            **plan,
            "vertices": [
                {key: value for key, value in item.items() if key != "tag"}
                for item in plan["vertices"]
            ],
        }
    if operation == "create_offset":
        return {
            **plan,
            "vertex": {
                key: value for key, value in plan["vertex"].items() if key != "tag"
            },
        }
    if operation == "create_midpoints":
        return {
            "midpoints": [
                {
                    **item,
                    "vertex": {
                        key: value
                        for key, value in item["vertex"].items()
                        if key != "tag"
                    },
                }
                for item in plan["midpoints"]
            ]
        }
    if operation == "create_quadrants":
        return {
            "sources": [
                {
                    **source,
                    "vertices": [
                        {
                            key: value
                            for key, value in vertex.items()
                            if key != "tag"
                        }
                        for vertex in source["vertices"]
                    ],
                }
                for source in plan["sources"]
            ]
        }
    return {key: value for key, value in plan.items() if key != "tag"}


def mutate_drawing_cosmetic_vertex(
    _document: Any,
    *,
    prepared: PreparedDrawingCosmeticVertex,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedDrawingCosmeticVertex):
        raise TypeError("prepared must be PreparedDrawingCosmeticVertex")
    import TechDrawGui

    try:
        if prepared.spec.operation == "create_intersections":
            created = normalize_vertex_intersection_host_plan(
                TechDrawGui.createDrawingVertexIntersections(
                    prepared.target.view,
                    list(prepared.spec.source_names),
                ),
                created=True,
            )
        elif prepared.spec.operation == "create_midpoints":
            created = normalize_midpoint_vertex_host_plan(
                TechDrawGui.createDrawingMidpointVertices(
                    prepared.target.view,
                    list(prepared.spec.source_names),
                ),
                created=True,
            )
        elif prepared.spec.operation == "create_quadrants":
            created = normalize_quadrant_vertex_host_plan(
                TechDrawGui.createDrawingQuadrantVertices(
                    prepared.target.view,
                    list(prepared.spec.source_names),
                ),
                created=True,
            )
        elif prepared.spec.operation == "create_offset":
            assert prepared.spec.offset_mm is not None
            created = normalize_offset_vertex_host_plan(
                TechDrawGui.createDrawingOffsetVertex(
                    prepared.target.view,
                    prepared.spec.source_names[0],
                    prepared.spec.offset_mm["x_mm"],
                    prepared.spec.offset_mm["y_mm"],
                ),
                created=True,
            )
        else:
            assert prepared.spec.point_in_view_mm is not None
            created = normalize_explicit_vertex_host_plan(
                TechDrawGui.createDrawingCosmeticVertexPoint(
                    prepared.target.view,
                    prepared.spec.point_in_view_mm["x_mm"],
                    prepared.spec.point_in_view_mm["y_mm"],
                ),
                created=True,
            )
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_COSMETIC_VERTEX_CREATION_FAILED",
            "TechDraw could not create the exact cosmetic vertex or vertices: "
            f"{str(exc).strip()}",
        ) from exc
    if (
        _without_created_tags(prepared.spec.operation, created)
        != prepared.host_validation
    ):
        raise NativeMutationError(
            "NATIVE_DRAWING_COSMETIC_VERTEX_CREATION_FAILED",
            "TechDraw created cosmetic vertices inconsistent with preflight.",
        )
    return NativeMutationDraft(
        value={"prepared": prepared, "created": created},
        recompute_targets=(prepared.target.view, prepared.target.page),
        changed=(object_identity(prepared.target.view),),
    )


def _postcondition_error(message: str) -> None:
    raise NativeMutationError(
        "NATIVE_DRAWING_COSMETIC_VERTEX_POSTCONDITION_FAILED", message
    )


def _view_boundary(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in state.items()
        if key not in {"state_sha256", "visible_edge_count", "hidden_edge_count"}
    }


def _persistent_vertex_boundary(vertex: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in vertex.items()
        if key not in {"subelement", "vertex_state_sha256"}
    }


def _require_old_vertices_preserved(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> None:
    after_by_tag = {item["tag"]: item for item in after["vertices"]}
    for old in before["vertices"]:
        current = after_by_tag.get(old["tag"])
        if current is None or _persistent_vertex_boundary(
            current
        ) != _persistent_vertex_boundary(old):
            _postcondition_error(
                "Cosmetic-vertex creation changed an existing persistent vertex."
            )


def _verify_drawing_cosmetic_vertex(
    document: Any, draft: NativeMutationDraft
) -> dict[str, Any]:
    prepared: PreparedDrawingCosmeticVertex = draft.value["prepared"]
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
            "Cosmetic-vertex creation changed objects, page membership, or History."
        )
    if drawing_selection_state(document) != target.selection_before:
        _postcondition_error("Cosmetic-vertex creation changed the human selection.")
    if drawing_visibility_state(document) != target.visibility_before:
        _postcondition_error("Cosmetic-vertex creation changed object visibility.")
    if drawing_page_state(target.page) != target.page_state_before:
        _postcondition_error("Cosmetic-vertex creation changed the Drawing page.")
    if _view_boundary(drawing_view_state(target.view)) != _view_boundary(
        target.view_state_before
    ):
        _postcondition_error(
            "Cosmetic-vertex creation changed the Drawing view definition."
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
                "A projected source changed while its cosmetic vertex was created."
            )

    inventory = drawing_cosmetic_vertex_inventory_state(target.view)
    created_plan = draft.value["created"]
    added = _planned_vertex_count(prepared.spec.operation, created_plan)
    if inventory["vertex_count"] != prepared.inventory_before["vertex_count"] + added:
        _postcondition_error(
            "Cosmetic-vertex creation added an unexpected number of persistent vertices."
        )
    _require_old_vertices_preserved(prepared.inventory_before, inventory)
    try:
        if prepared.spec.operation == "create_intersections":
            state = drawing_intersection_vertex_result_state(
                target.view,
                created_plan,
                target.element_states_before,
            )
        elif prepared.spec.operation == "create_midpoints":
            state = drawing_midpoint_vertex_result_state(
                target.view,
                created_plan,
                target.element_states_before,
            )
        elif prepared.spec.operation == "create_quadrants":
            state = drawing_quadrant_vertex_result_state(
                target.view,
                created_plan,
                target.element_states_before,
            )
        elif prepared.spec.operation == "create_offset":
            state = drawing_offset_vertex_result_state(
                target.view,
                created_plan,
                target.element_states_before[0],
            )
        else:
            state = drawing_explicit_vertex_result_state(
                target.view,
                created_plan,
            )
    except Exception as exc:
        _postcondition_error(
            "The created cosmetic vertex state could not be resolved exactly: "
            f"{str(exc).strip()}"
        )
    return {
        "operation": prepared.spec.operation,
        "cosmetic_vertices": state,
    }


def verify_drawing_cosmetic_vertex(
    document: Any, draft: NativeMutationDraft
) -> dict[str, Any]:
    try:
        return _verify_drawing_cosmetic_vertex(document, draft)
    except NativeMutationError:
        raise
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_COSMETIC_VERTEX_POSTCONDITION_FAILED",
            "The cosmetic vertex or vertices could not be verified exactly: "
            f"{str(exc).strip()}",
        ) from exc
