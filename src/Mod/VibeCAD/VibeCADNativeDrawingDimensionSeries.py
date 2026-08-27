# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact transactional Drawing chain and coordinate dimension series."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeDrawingDimensionState import drawing_dimension_state
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingDimensionSupport import (
    PreparedDrawingDimensionTarget,
    drawing_dimension_error,
    drawing_object_key,
    drawing_selection_state,
    drawing_timeline_operations,
    matches_drawing_document_label,
    prepare_drawing_dimension_target,
)
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewState import drawing_view_state
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeTargets import object_identity


_OPERATIONS = {
    "create_horizontal_chain": ("chain", "horizontal"),
    "create_vertical_chain": ("chain", "vertical"),
    "create_oblique_chain": ("chain", "oblique"),
    "create_horizontal_coordinate": ("coordinate", "horizontal"),
    "create_vertical_coordinate": ("coordinate", "vertical"),
    "create_oblique_coordinate": ("coordinate", "oblique"),
}
_HOST_PLAN_KEYS = frozenset(
    {"kind", "direction", "input_vertices", "ordered_vertices", "dimension_count"}
)
_HOST_RESULT_KEYS = _HOST_PLAN_KEYS | {"operation_group", "dimensions"}


@dataclass(frozen=True, slots=True)
class DrawingDimensionSeriesSpec:
    operation: str
    kind: str
    direction: str
    label: str
    vertices: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PreparedDrawingDimensionSeries:
    target: PreparedDrawingDimensionTarget
    spec: DrawingDimensionSeriesSpec
    host_plan: dict[str, Any]
    cosmetic_edge_tags_before: frozenset[str]
    cosmetic_vertex_tags_before: frozenset[str]


def _error(
    message: str,
    code: str,
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    drawing_dimension_error(message, code, repair=repair)


def _spec(operation: str, values: Mapping[str, Any]) -> DrawingDimensionSeriesSpec:
    if operation not in _OPERATIONS:
        raise ValueError("operation is not a Drawing dimension series operation")
    label = str(values["label"] or "")
    if label != label.strip() or not 1 <= len(label) <= 160:
        _error(
            "A Drawing dimension-series label must contain 1 to 160 non-padding characters.",
            "NATIVE_DRAWING_DIMENSION_SERIES_PARAMETERS_INVALID",
        )
    raw_vertices = values["vertices"]
    if not isinstance(raw_vertices, (list, tuple)) or not 3 <= len(raw_vertices) <= 64:
        _error(
            "A Drawing dimension series requires 3 to 64 exact projected vertices.",
            "NATIVE_DRAWING_DIMENSION_SERIES_PARAMETERS_INVALID",
        )
    names = tuple(
        str(item.get("subelement", "") or "")
        if isinstance(item, Mapping)
        else ""
        for item in raw_vertices
    )
    if any(not name.startswith("Vertex") for name in names) or len(set(names)) != len(names):
        _error(
            "A Drawing dimension series requires unique exact VertexN references.",
            "NATIVE_DRAWING_DIMENSION_SERIES_REFERENCES_INVALID",
            repair={"accepted_reference_type": "vertex", "minimum_count": 3, "maximum_count": 64},
        )
    kind, direction = _OPERATIONS[operation]
    return DrawingDimensionSeriesSpec(
        operation=operation,
        kind=kind,
        direction=direction,
        label=label,
        vertices=names,
    )


def _validate_host(view: Any, spec: DrawingDimensionSeriesSpec) -> dict[str, Any]:
    try:
        import TechDrawGui

        validator = getattr(TechDrawGui, "validateDrawingDimensionSeries", None)
        if not callable(validator):
            _error(
                "The installed TechDraw runtime cannot validate dimension series.",
                "NATIVE_DRAWING_DIMENSION_SERIES_RUNTIME_UNAVAILABLE",
            )
        raw = validator(view, spec.kind, spec.direction, list(spec.vertices))
    except NativeDrawingError:
        raise
    except Exception as exc:
        _error(
            f"TechDraw rejected the {spec.direction} {spec.kind} series: {str(exc).strip()}",
            "NATIVE_DRAWING_DIMENSION_SERIES_REFERENCES_INVALID",
            repair={
                "accepted_references": "3 to 64 unique projected VertexN references",
                "ordering": (
                    "the first two vertices establish the baseline direction"
                    if spec.direction == "oblique"
                    else "the first two vertices establish coordinate sign"
                    if spec.kind == "coordinate"
                    else "the host orders vertices geometrically"
                ),
                "tool": "drawing.projected_geometry",
            },
        )
    if not isinstance(raw, Mapping) or frozenset(raw) != _HOST_PLAN_KEYS:
        _error(
            "TechDraw returned a malformed dimension-series validation plan.",
            "NATIVE_DRAWING_DIMENSION_SERIES_RUNTIME_UNAVAILABLE",
        )
    plan = dict(raw)
    if (
        str(plan["kind"]) != spec.kind
        or str(plan["direction"]) != spec.direction
        or tuple(plan["input_vertices"]) != spec.vertices
        or len(tuple(plan["ordered_vertices"])) != len(spec.vertices)
        or set(plan["ordered_vertices"]) != set(spec.vertices)
        or int(plan["dimension_count"]) != len(spec.vertices) - 1
    ):
        _error(
            "TechDraw returned an inconsistent dimension-series validation plan.",
            "NATIVE_DRAWING_DIMENSION_SERIES_RUNTIME_UNAVAILABLE",
        )
    plan["input_vertices"] = list(spec.vertices)
    plan["ordered_vertices"] = [str(value) for value in plan["ordered_vertices"]]
    plan["dimension_count"] = int(plan["dimension_count"])
    return plan


def _cosmetic_tags(view: Any, property_name: str) -> frozenset[str]:
    return frozenset(
        str(getattr(item, "Tag", "") or "")
        for item in tuple(getattr(view, property_name, ()) or ())
    )


def prepare_drawing_dimension_series(
    document: Any,
    *,
    operation: str,
    values: Mapping[str, Any],
) -> PreparedDrawingDimensionSeries:
    spec = _spec(operation, values)
    target = prepare_drawing_dimension_target(
        document,
        page_target=values["page"],
        view_target=values["view"],
        element_targets=tuple(values["vertices"]),
        allowed_element_types=frozenset({"vertex"}),
        family="dimension series",
        code_prefix="NATIVE_DRAWING_DIMENSION_SERIES",
    )
    if tuple(item["name"] for item in target.element_states_before) != spec.vertices:
        _error(
            "The Drawing dimension-series reference order is inconsistent.",
            "NATIVE_DRAWING_DIMENSION_SERIES_REFERENCES_INVALID",
        )
    return PreparedDrawingDimensionSeries(
        target=target,
        spec=spec,
        host_plan=_validate_host(target.view, spec),
        cosmetic_edge_tags_before=_cosmetic_tags(target.view, "CosmeticEdges"),
        cosmetic_vertex_tags_before=_cosmetic_tags(target.view, "CosmeticVertexes"),
    )


def restore_drawing_dimension_series_after_abort(
    document: Any,
    *,
    prepared: PreparedDrawingDimensionSeries,
) -> None:
    """Remove only projection carriers introduced by an aborted series call."""

    view = prepared.target.view
    if (
        getattr(view, "Document", None) is not document
        or document.getObject(str(view.Name)) is not view
    ):
        raise RuntimeError("The exact Drawing source view disappeared during rollback.")
    added_edges = sorted(
        _cosmetic_tags(view, "CosmeticEdges") - prepared.cosmetic_edge_tags_before
    )
    added_vertices = sorted(
        _cosmetic_tags(view, "CosmeticVertexes") - prepared.cosmetic_vertex_tags_before
    )
    import TechDrawGui

    cleaner = getattr(TechDrawGui, "removeDrawingDimensionSeriesCarriers", None)
    if not callable(cleaner):
        raise RuntimeError("The Drawing carrier rollback runtime is unavailable.")
    # A transaction abort restores the properties before this callback, but
    # the view's cosmetic caches can still contain the aborted carriers.
    cleaner(view, added_edges, added_vertices)
    if (
        _cosmetic_tags(view, "CosmeticEdges") != prepared.cosmetic_edge_tags_before
        or _cosmetic_tags(view, "CosmeticVertexes")
        != prepared.cosmetic_vertex_tags_before
    ):
        raise RuntimeError("Drawing carrier rollback did not restore the exact carrier sets.")


def mutate_drawing_dimension_series(
    document: Any,
    *,
    prepared: PreparedDrawingDimensionSeries,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedDrawingDimensionSeries):
        raise TypeError("prepared must be a PreparedDrawingDimensionSeries")
    try:
        import TechDrawGui

        creator = getattr(TechDrawGui, "createDrawingDimensionSeries", None)
        if not callable(creator):
            raise RuntimeError("the compiled dimension-series creator is unavailable")
        raw = creator(
            prepared.target.view,
            prepared.spec.kind,
            prepared.spec.direction,
            list(prepared.spec.vertices),
        )
        if not isinstance(raw, Mapping) or frozenset(raw) != _HOST_RESULT_KEYS:
            raise RuntimeError("the compiled creator returned a malformed result")
        operation_group = raw["operation_group"]
        dimensions = tuple(raw["dimensions"])
        operation_group.Label = prepared.spec.label
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_DIMENSION_SERIES_CREATE_FAILED",
            f"TechDraw could not create the {prepared.spec.direction} "
            f"{prepared.spec.kind} series: {str(exc).strip()}",
        ) from exc
    if (
        operation_group is None
        or str(getattr(operation_group, "TypeId", "")) != "App::DocumentObjectGroup"
        or getattr(operation_group, "Document", None) is not document
        or len(dimensions) != prepared.host_plan["dimension_count"]
        or any(
            getattr(dimension, "Document", None) is not document
            or not dimension.isDerivedFrom("TechDraw::DrawViewDimension")
            for dimension in dimensions
        )
    ):
        raise NativeMutationError(
            "NATIVE_DRAWING_DIMENSION_SERIES_CREATE_FAILED",
            "TechDraw did not create the exact grouped dimension series.",
        )
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "operation_group": operation_group,
            "dimensions": dimensions,
        },
        recompute_targets=(*dimensions, prepared.target.view, prepared.target.page),
        created=tuple(
            object_identity(value) for value in (*dimensions, operation_group)
        ),
        changed=(
            object_identity(prepared.target.page),
            object_identity(prepared.target.view),
        ),
    )


def _compact_dimension(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "object_name": state["object_name"],
        "label": state["label"],
        "state_sha256": state["state_sha256"],
        "dimension_type": state["dimension_type"],
        "references": state["references"],
        "label_position_in_view_mm": state["label_position_in_view_mm"],
        "measured_value": state["measured_value"],
    }


def _verify_series(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared: PreparedDrawingDimensionSeries = draft.value["prepared"]
    target = prepared.target
    spec = prepared.spec
    operation_group = draft.value["operation_group"]
    dimensions = tuple(draft.value["dimensions"])
    before_keys = {drawing_object_key(value) for value in target.objects_before}
    new_objects = tuple(
        value for value in document.Objects if drawing_object_key(value) not in before_keys
    )
    expected_objects = (*dimensions, operation_group)
    expected_page_views = (*target.page_views_before, *dimensions)
    # History stores the complete semantic block contiguously: resource
    # dimensions first, followed by their operation root.
    expected_timeline = (*target.timeline_before, *dimensions, operation_group)
    actual_page_views = tuple(target.page.Views or ())
    actual_timeline = drawing_timeline_operations(document)
    actual_members = tuple(operation_group.Group or ())
    object_scope_valid = (
        len(new_objects) == len(expected_objects)
        and {drawing_object_key(value) for value in new_objects}
        == {drawing_object_key(value) for value in expected_objects}
    )
    if (
        not object_scope_valid
        or actual_page_views != expected_page_views
        or actual_timeline != expected_timeline
        or actual_members != dimensions
    ):
        names = lambda values: [str(getattr(value, "Name", "") or "") for value in values]
        raise NativeMutationError(
            "NATIVE_DRAWING_DIMENSION_SERIES_POSTCONDITION_FAILED",
            "Dimension-series creation changed objects, page membership, grouping, or History "
            "outside its exact result: "
            f"new={names(new_objects)!r}, page_views={names(actual_page_views)!r}, "
            f"history={names(actual_timeline)!r}, members={names(actual_members)!r}.",
        )
    if (
        str(getattr(operation_group, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(operation_group, "VibeCADTimelineOwner", None) is not None
        or not matches_drawing_document_label(str(operation_group.Label), spec.label)
        or any(
            str(getattr(dimension, "VibeCADTimelineRole", "") or "") != "resource"
            or getattr(dimension, "VibeCADTimelineOwner", None) is not operation_group
            for dimension in dimensions
        )
    ):
        raise NativeMutationError(
            "NATIVE_DRAWING_DIMENSION_SERIES_POSTCONDITION_FAILED",
            "The dimension-series History operation or resource ownership is invalid.",
        )
    checker = getattr(document, "isObjectUsableAtCurrentTimelinePosition", None)
    if callable(checker) and (
        not checker(operation_group) or any(not checker(value) for value in dimensions)
    ):
        raise NativeMutationError(
            "NATIVE_DRAWING_DIMENSION_SERIES_POSTCONDITION_FAILED",
            "The dimension-series History block is not usable at the current position.",
        )
    if drawing_selection_state(document) != target.selection_before:
        raise NativeMutationError(
            "NATIVE_DRAWING_DIMENSION_SERIES_POSTCONDITION_FAILED",
            "Dimension-series creation changed the human selection.",
        )
    current_visibility = tuple(
        (value, bool(value.ViewObject.Visibility))
        for value, _visible in target.visibility_before
    )
    if current_visibility != target.visibility_before:
        raise NativeMutationError(
            "NATIVE_DRAWING_DIMENSION_SERIES_POSTCONDITION_FAILED",
            "Dimension-series creation changed existing object visibility.",
        )

    projection_after = drawing_projected_geometry_state(target.view)
    before_elements = {
        item["name"]: item for item in target.projection_state_before["elements"]
    }
    after_elements = {item["name"]: item for item in projection_after["elements"]}
    if any(
        name not in after_elements
        or after_elements[name]["element_state_sha256"]
        != state["element_state_sha256"]
        for name, state in before_elements.items()
    ):
        raise NativeMutationError(
            "NATIVE_DRAWING_DIMENSION_SERIES_POSTCONDITION_FAILED",
            "Dimension-series creation changed a pre-existing projected element.",
        )
    added_elements = [
        state for name, state in after_elements.items() if name not in before_elements
    ]
    if spec.direction != "oblique" and (
        drawing_view_state(target.view)["state_sha256"]
        != target.view_state_before["state_sha256"]
        or projection_after["projection_state_sha256"]
        != target.projection_state_before["projection_state_sha256"]
        or added_elements
    ):
        raise NativeMutationError(
            "NATIVE_DRAWING_DIMENSION_SERIES_POSTCONDITION_FAILED",
            "Horizontal or vertical dimension-series creation changed its source projection.",
        )
    if spec.direction == "oblique" and (
        any(item["element_type"] not in {"edge", "vertex"} for item in added_elements)
        or len(added_elements) > len(spec.vertices) * 2
    ):
        raise NativeMutationError(
            "NATIVE_DRAWING_DIMENSION_SERIES_POSTCONDITION_FAILED",
            "Oblique dimension-series carrier geometry exceeded its exact bounded scope.",
        )

    states = tuple(drawing_dimension_state(value) for value in dimensions)
    ordered = tuple(prepared.host_plan["ordered_vertices"])
    for index, state in enumerate(states):
        references = tuple(item["subelement"] for item in state["references"])
        if (
            state["page_name"] != str(target.page.Name)
            or state["view_name"] != str(target.view.Name)
            or state["measure_type"] != "Projected"
            or state["dimension_type"]
            != ("Distance" if spec.direction == "oblique" else "DistanceX" if spec.direction == "horizontal" else "DistanceY")
            or len(references) != 2
            or len(set(references)) != 2
            or float(state["measured_value"]["value"]) <= 1.0e-12
            or state["timeline_role"] != "resource"
            or state["timeline_owner_name"] != str(operation_group.Name)
            or not state["timeline_usable"]
            or not state["valid"]
        ):
            raise NativeMutationError(
                "NATIVE_DRAWING_DIMENSION_SERIES_POSTCONDITION_FAILED",
                f"Dimension-series member {index} did not retain its exact valid state.",
            )
        if spec.direction != "oblique":
            expected = (
                (ordered[index], ordered[index + 1])
                if spec.kind == "chain"
                else (ordered[0], ordered[index + 1])
            )
            if references != expected:
                raise NativeMutationError(
                    "NATIVE_DRAWING_DIMENSION_SERIES_POSTCONDITION_FAILED",
                    f"Dimension-series member {index} retained the wrong ordered references.",
                )

    page_state = drawing_page_state(target.page)
    if page_state["view_count"] != target.page_state_before["view_count"] + len(dimensions):
        raise NativeMutationError(
            "NATIVE_DRAWING_DIMENSION_SERIES_POSTCONDITION_FAILED",
            "The Drawing page did not retain every dimension-series member.",
        )
    return {
        "operation": spec.operation,
        "series": {
            "kind": spec.kind,
            "direction": spec.direction,
            "input_vertices": list(spec.vertices),
            "ordered_vertices": list(ordered),
            "dimension_count": len(dimensions),
        },
        "history_operation": {
            "object_name": str(operation_group.Name),
            "label": str(operation_group.Label),
            "type_id": str(operation_group.TypeId),
            "timeline_role": "operation",
            "resource_names": [str(value.Name) for value in dimensions],
        },
        "dimensions": [_compact_dimension(state) for state in states],
        "carrier_geometry": {
            "added_edge_names": [
                item["name"] for item in added_elements if item["element_type"] == "edge"
            ],
            "added_vertex_names": [
                item["name"] for item in added_elements if item["element_type"] == "vertex"
            ],
            "projection_state_sha256": projection_after["projection_state_sha256"],
        },
        "page": {
            "object_name": page_state["object_name"],
            "state_sha256": page_state["state_sha256"],
            "view_count": page_state["view_count"],
        },
    }


def verify_drawing_dimension_series(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    try:
        return _verify_series(document, draft)
    except NativeMutationError:
        raise
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_DIMENSION_SERIES_POSTCONDITION_FAILED",
            "The Drawing dimension series could not be verified exactly: "
            f"{str(exc).strip()}",
        ) from exc
