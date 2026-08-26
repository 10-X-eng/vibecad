# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact transactional creation of explicit projected Drawing dimensions."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeDrawingDimensionState import (
    drawing_axonometric_dimension_state,
    drawing_dimension_state,
    drawing_extent_state,
    is_drawing_dimension,
    is_drawing_extent,
)
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state
from VibeCADNativeDrawingDimensionSupport import (
    drawing_label_position_in_view_mm,
    provider_drawing_dimension_state,
)
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewState import drawing_view_state
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeLabel import matches_preferred_document_label
from VibeCADNativeTargets import object_identity, read_current_selection, resolve_object


_OPERATION_TYPES = {
    "create_length": "Distance",
    "create_horizontal": "DistanceX",
    "create_vertical": "DistanceY",
    "create_radius": "Radius",
    "create_diameter": "Diameter",
    "create_angle": "Angle",
    "create_three_point_angle": "Angle3Pt",
    "create_area": "Area",
    "create_horizontal_extent": "DistanceX",
    "create_vertical_extent": "DistanceY",
    "create_axonometric_length": "Distance",
}
_DRAWING_DIMENSION_OPERATIONS = frozenset(_OPERATION_TYPES)
_REFERENCE_FIELDS = {
    "create_length": ("references",),
    "create_horizontal": ("references",),
    "create_vertical": ("references",),
    "create_radius": ("edge",),
    "create_diameter": ("edge",),
    "create_angle": ("first_edge", "second_edge"),
    "create_three_point_angle": (
        "first_arm_point",
        "apex_point",
        "second_arm_point",
    ),
    "create_area": ("face",),
    "create_horizontal_extent": ("extent",),
    "create_vertical_extent": ("extent",),
    "create_axonometric_length": ("measurement", "extension_direction_edge"),
}
_EXPECTED_KINDS = {
    "create_length": frozenset({"edge", "vertex"}),
    "create_horizontal": frozenset({"edge", "vertex"}),
    "create_vertical": frozenset({"edge", "vertex"}),
    "create_radius": frozenset({"edge"}),
    "create_diameter": frozenset({"edge"}),
    "create_angle": frozenset({"edge"}),
    "create_three_point_angle": frozenset({"vertex"}),
    "create_area": frozenset({"face"}),
    "create_horizontal_extent": frozenset({"edge"}),
    "create_vertical_extent": frozenset({"edge"}),
    "create_axonometric_length": frozenset({"edge", "vertex"}),
}
_REFERENCE_GUIDANCE = {
    "create_length": "one or two projected EdgeN/VertexN references",
    "create_horizontal": "one or two projected EdgeN/VertexN references",
    "create_vertical": "one or two projected EdgeN/VertexN references",
    "create_radius": "one circular projected EdgeN",
    "create_diameter": "one circular projected EdgeN",
    "create_angle": "two nonparallel projected EdgeN references",
    "create_three_point_angle": (
        "three projected VertexN references ordered first arm, apex, second arm"
    ),
    "create_area": "one projected FaceN",
    "create_horizontal_extent": "a whole view or one to sixty-four projected EdgeN references",
    "create_vertical_extent": "a whole view or one to sixty-four projected EdgeN references",
    "create_axonometric_length": (
        "one projected EdgeN or two VertexN measurement references plus "
        "distinct dimension and extension direction EdgeN references"
    ),
}

_EXTENT_OPERATIONS = frozenset(
    {"create_horizontal_extent", "create_vertical_extent"}
)
_AXONOMETRIC_OPERATION = "create_axonometric_length"
_AXONOMETRIC_VALUE_MODES = frozenset(
    {
        "projected",
        "x_axis_true_length",
        "y_axis_true_length",
        "z_axis_true_length",
    }
)
_LINEAR_DIRECTION = {
    "create_length": "aligned",
    "create_horizontal": "horizontal",
    "create_vertical": "vertical",
}
_GEOMETRY_TOLERANCE = 1.0e-9


@dataclass(frozen=True, slots=True)
class DimensionSpec:
    operation: str
    dimension_type: str
    label: str
    x_mm: float
    y_mm: float
    subelements: tuple[str, ...]
    allow_approximate: bool
    target_scope: str = "references"
    dimension_direction_subelement: str = ""
    extension_direction_subelement: str = ""
    expected_value_mode: str = ""


@dataclass(frozen=True, slots=True)
class PreparedDrawingDimension:
    page: Any
    page_state_before: dict[str, Any]
    page_views_before: tuple[Any, ...]
    view: Any
    view_state_before: dict[str, Any]
    projection_state_before: dict[str, Any]
    element_states_before: tuple[dict[str, Any], ...]
    host_validation: dict[str, Any]
    spec: DimensionSpec
    objects_before: tuple[Any, ...]
    timeline_before: tuple[Any, ...]
    selection_before: dict[str, Any]
    visibility_before: tuple[tuple[Any, bool], ...]


def _error(
    message: str,
    code: str,
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    raise NativeDrawingError(message, error_code=code, repair=repair)


def _postcondition_error(message: str) -> None:
    raise NativeMutationError(
        "NATIVE_DRAWING_DIMENSION_POSTCONDITION_FAILED",
        message,
    )


def _finite(value: Any, noun: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeDrawingError(
            f"Drawing dimension {noun} must be numeric.",
            error_code="NATIVE_DRAWING_DIMENSION_PARAMETERS_INVALID",
        ) from exc
    if not math.isfinite(result) or not -10_000.0 <= result <= 10_000.0:
        _error(
            f"Drawing dimension {noun} must be finite and between -10000 and 10000 mm.",
            "NATIVE_DRAWING_DIMENSION_PARAMETERS_INVALID",
        )
    return result


def _timeline_operations(document: Any) -> tuple[Any, ...]:
    timeline = document.getObject("VibeCADTimeline")
    return tuple(getattr(timeline, "Operations", ()) or ()) if timeline else ()


def _selection(document: Any) -> dict[str, Any]:
    try:
        return read_current_selection(document)
    except (AttributeError, ImportError, RuntimeError):
        return {
            "document_uid": str(document.Uid),
            "selected_count": 0,
            "items": [],
        }


def _visibility(document: Any) -> tuple[tuple[Any, bool], ...]:
    result = []
    for obj in tuple(document.Objects):
        view_object = getattr(obj, "ViewObject", None)
        if view_object is not None:
            result.append((obj, bool(getattr(view_object, "Visibility", False))))
    return tuple(result)


def _require_usable(document: Any, obj: Any, noun: str) -> None:
    checker = getattr(document, "isObjectUsableAtCurrentTimelinePosition", None)
    if callable(checker) and not bool(checker(obj)):
        _error(
            f"The exact {noun} is not usable at the current History position.",
            "NATIVE_DRAWING_HISTORY_TARGET_UNAVAILABLE",
        )


def _exact_mapping(value: Any, keys: frozenset[str], noun: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or frozenset(value) != keys:
        _error(
            f"The exact Drawing dimension {noun} is malformed.",
            "NATIVE_DRAWING_DIMENSION_PARAMETERS_INVALID",
        )
    return value


def _spec(operation: str, values: Mapping[str, Any]) -> DimensionSpec:
    if operation not in _DRAWING_DIMENSION_OPERATIONS:
        raise ValueError("operation is not an explicit Drawing dimension operation")
    label = str(values["label"] or "")
    if label != label.strip() or not 1 <= len(label) <= 160:
        _error(
            "A Drawing dimension label must contain 1 to 160 non-padding characters.",
            "NATIVE_DRAWING_DIMENSION_PARAMETERS_INVALID",
        )
    position = _exact_mapping(
        values["label_position_in_view_mm"],
        frozenset({"x_mm", "y_mm"}),
        "label position",
    )
    raw_targets = _reference_targets(operation, values)
    measurement_targets = raw_targets
    dimension_direction = ""
    extension_direction = ""
    expected_value_mode = ""
    if operation == _AXONOMETRIC_OPERATION:
        (
            measurement_targets,
            dimension_target,
            extension_target,
            expected_value_mode,
        ) = _axonometric_targets(values)
        dimension_direction = str(dimension_target["subelement"])
        extension_direction = str(extension_target["subelement"])
    if operation in {"create_length", "create_horizontal", "create_vertical"}:
        if not 1 <= len(raw_targets) <= 2:
            _error(
                "A linear Drawing dimension requires one or two references.",
                "NATIVE_DRAWING_DIMENSION_REFERENCES_INVALID",
            )
    target_scope = "references"
    if operation in _EXTENT_OPERATIONS:
        target_scope, _targets = _extent_target(values)
        if target_scope == "edges" and not 1 <= len(raw_targets) <= 64:
            _error(
                "An edge-scoped Drawing extent requires one to sixty-four edges.",
                "NATIVE_DRAWING_DIMENSION_REFERENCES_INVALID",
            )
    target_names = tuple(str(target["subelement"]) for target in raw_targets)
    subelements = tuple(
        str(target["subelement"]) for target in measurement_targets
    )
    if len(target_names) != len(set(target_names)):
        _error(
            "A Drawing dimension cannot repeat the same projected reference.",
            "NATIVE_DRAWING_DIMENSION_REFERENCES_INVALID",
        )
    allow_approximate = (
        bool(values["allow_approximate"])
        if operation in {"create_radius", "create_diameter"}
        else False
    )
    if operation in {"create_radius", "create_diameter"} and type(
        values["allow_approximate"]
    ) is not bool:
        _error(
            "allow_approximate must be a boolean.",
            "NATIVE_DRAWING_DIMENSION_PARAMETERS_INVALID",
        )
    return DimensionSpec(
        operation=operation,
        dimension_type=_OPERATION_TYPES[operation],
        label=label,
        x_mm=_finite(position["x_mm"], "label x_mm"),
        y_mm=_finite(position["y_mm"], "label y_mm"),
        subelements=subelements,
        allow_approximate=allow_approximate,
        target_scope=target_scope,
        dimension_direction_subelement=dimension_direction,
        extension_direction_subelement=extension_direction,
        expected_value_mode=expected_value_mode,
    )


def _extent_target(
    values: Mapping[str, Any],
) -> tuple[str, tuple[Mapping[str, Any], ...]]:
    raw = values["extent"]
    if not isinstance(raw, Mapping):
        _error(
            "A Drawing extent target must be an object.",
            "NATIVE_DRAWING_DIMENSION_PARAMETERS_INVALID",
        )
    scope = str(raw.get("scope", "") or "")
    if scope == "whole_view" and frozenset(raw) == frozenset({"scope"}):
        return scope, ()
    if scope == "edges" and frozenset(raw) == frozenset({"scope", "edges"}):
        edges = raw["edges"]
        if not isinstance(edges, (list, tuple)):
            _error(
                "A Drawing extent edge target must contain an edge array.",
                "NATIVE_DRAWING_DIMENSION_PARAMETERS_INVALID",
            )
        return scope, tuple(edges)
    _error(
        "A Drawing extent must be exactly {scope:'whole_view'} or "
        "{scope:'edges', edges:[...] }.",
        "NATIVE_DRAWING_DIMENSION_PARAMETERS_INVALID",
    )


def _element_target(value: Any, noun: str) -> Mapping[str, Any]:
    return _exact_mapping(
        value,
        frozenset({"subelement"}),
        noun,
    )


def _require_subelement_kind(
    target: Mapping[str, Any],
    prefix: str,
    noun: str,
) -> None:
    if not str(target["subelement"] or "").startswith(prefix):
        _error(
            f"The {noun} must be an exact projected {prefix}N reference.",
            "NATIVE_DRAWING_DIMENSION_REFERENCE_TYPE_INVALID",
            repair={"accepted_reference_type": prefix.casefold()},
        )


def _axonometric_targets(
    values: Mapping[str, Any],
) -> tuple[
    tuple[Mapping[str, Any], ...],
    Mapping[str, Any],
    Mapping[str, Any],
    str,
]:
    measurement = values["measurement"]
    if not isinstance(measurement, Mapping):
        _error(
            "An axonometric measurement must be an object.",
            "NATIVE_DRAWING_DIMENSION_PARAMETERS_INVALID",
        )
    kind = str(measurement.get("kind", "") or "")
    if kind == "edge" and frozenset(measurement) == frozenset(
        {"kind", "dimension_edge"}
    ):
        direction = _element_target(
            measurement["dimension_edge"],
            "axonometric dimension edge",
        )
        _require_subelement_kind(direction, "Edge", "axonometric dimension edge")
        measurement_targets = (direction,)
    elif kind == "vertex_pair" and frozenset(measurement) == frozenset(
        {
            "kind",
            "first_vertex",
            "second_vertex",
            "dimension_direction_edge",
        }
    ):
        first = _element_target(
            measurement["first_vertex"],
            "axonometric first measurement vertex",
        )
        second = _element_target(
            measurement["second_vertex"],
            "axonometric second measurement vertex",
        )
        direction = _element_target(
            measurement["dimension_direction_edge"],
            "axonometric dimension-direction edge",
        )
        _require_subelement_kind(first, "Vertex", "first measurement vertex")
        _require_subelement_kind(second, "Vertex", "second measurement vertex")
        _require_subelement_kind(
            direction,
            "Edge",
            "axonometric dimension-direction edge",
        )
        measurement_targets = (first, second)
    else:
        _error(
            "An axonometric measurement must be exactly an edge or vertex_pair branch.",
            "NATIVE_DRAWING_DIMENSION_PARAMETERS_INVALID",
        )
    extension = _element_target(
        values["extension_direction_edge"],
        "axonometric extension-direction edge",
    )
    _require_subelement_kind(
        extension,
        "Edge",
        "axonometric extension-direction edge",
    )
    expected = str(values["expected_value_mode"] or "")
    if expected not in _AXONOMETRIC_VALUE_MODES:
        _error(
            "An axonometric expected_value_mode is unsupported.",
            "NATIVE_DRAWING_DIMENSION_PARAMETERS_INVALID",
            repair={"allowed_values": sorted(_AXONOMETRIC_VALUE_MODES)},
        )
    return measurement_targets, direction, extension, expected


def _reference_targets(
    operation: str,
    values: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    if operation in {"create_length", "create_horizontal", "create_vertical"}:
        raw = tuple(values["references"])
    elif operation in _EXTENT_OPERATIONS:
        _scope, raw = _extent_target(values)
    elif operation == _AXONOMETRIC_OPERATION:
        measurement, direction, extension, _expected = _axonometric_targets(values)
        raw = (*measurement, extension)
        if all(
            item["subelement"] != direction["subelement"] for item in measurement
        ):
            raw = (*measurement, direction, extension)
    else:
        raw = tuple(values[name] for name in _REFERENCE_FIELDS[operation])
    return tuple(
        _exact_mapping(
            item,
            frozenset({"subelement"}),
            "projected reference",
        )
        for item in raw
    )


def _resolve_page(document: Any, target: Any) -> tuple[Any, dict[str, Any]]:
    exact = _exact_mapping(
        target,
        frozenset({"object_name", "expected_state_sha256"}),
        "page target",
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


def _resolve_view(
    document: Any,
    page: Any,
    target: Any,
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    exact = _exact_mapping(
        target,
        frozenset(
            {
                "object_name",
                "expected_state_sha256",
                "expected_projection_state_sha256",
            }
        ),
        "view target",
    )
    view = resolve_object(
        document,
        {"document_uid": str(document.Uid), "object_name": exact["object_name"]},
        expected_types=("TechDraw::DrawViewPart",),
    )
    # Projection-group items are real page views, but TechDraw nests them
    # under DrawProjGroup instead of listing them directly in DrawPage.Views.
    # findParentPage() is the authoritative relationship for both forms.
    if view.findParentPage() is not page:
        _error(
            "The exact Drawing view does not belong to the exact page.",
            "NATIVE_DRAWING_DIMENSION_PAGE_MISMATCH",
        )
    _require_usable(document, view, "Drawing view")
    view_state = drawing_view_state(view)
    if str(exact["expected_state_sha256"]) != view_state["state_sha256"]:
        _error(
            "The exact Drawing view changed after it was inspected.",
            "NATIVE_DRAWING_DIMENSION_VIEW_STALE",
            repair={"current_state_sha256": view_state["state_sha256"]},
        )
    projection_state = drawing_projected_geometry_state(view)
    if (
        str(exact["expected_projection_state_sha256"])
        != projection_state["projection_state_sha256"]
    ):
        _error(
            "The exact Drawing projection changed after it was inspected.",
            "NATIVE_DRAWING_DIMENSION_PROJECTION_STALE",
            repair={
                "current_projection_state_sha256": projection_state[
                    "projection_state_sha256"
                ]
            },
        )
    return view, view_state, projection_state


def _resolve_elements(
    operation: str,
    projection_state: Mapping[str, Any],
    targets: tuple[Mapping[str, Any], ...],
) -> tuple[dict[str, Any], ...]:
    by_name = {item["name"]: item for item in projection_state["elements"]}
    allowed = _EXPECTED_KINDS[operation]
    result = []
    for target in targets:
        name = str(target["subelement"] or "")
        element = by_name.get(name)
        if element is None:
            _error(
                f"Projected reference {name!r} no longer exists in the exact view.",
                "NATIVE_DRAWING_DIMENSION_REFERENCE_STALE",
                repair={"tool": "drawing.projected_geometry"},
            )
        if element["element_type"] not in allowed:
            _error(
                f"Projected reference {name!r} has type {element['element_type']!r}; "
                f"{operation} accepts {', '.join(sorted(allowed))}.",
                "NATIVE_DRAWING_DIMENSION_REFERENCE_TYPE_INVALID",
                repair={"accepted_reference_types": sorted(allowed)},
            )
        result.append(element)
    return tuple(result)


def _point_xy(value: Mapping[str, Any]) -> tuple[float, float]:
    return float(value["x_mm"]), float(value["y_mm"])


def _line_vector(element: Mapping[str, Any]) -> tuple[float, float]:
    start_x, start_y = _point_xy(element["start_in_view_mm"])
    end_x, end_y = _point_xy(element["end_in_view_mm"])
    return end_x - start_x, end_y - start_y


def _is_line(element: Mapping[str, Any]) -> bool:
    return (
        element.get("element_type") == "edge"
        and str(element.get("geometry_type") or "").casefold() == "line"
        and not bool(element.get("closed"))
    )


def _validate_linear_reference_geometry(
    operation: str,
    elements: tuple[Mapping[str, Any], ...],
) -> None:
    """Reject exactly measurable linear-reference mistakes before mutation."""

    if operation not in _LINEAR_DIRECTION:
        raise ValueError("operation is not a linear Drawing dimension")
    names = [str(element["name"]) for element in elements]
    kinds = tuple(str(element["element_type"]) for element in elements)
    valid: list[str] = []

    if len(elements) == 1 and kinds == ("edge",):
        edge = elements[0]
        if not _is_line(edge):
            _error(
                f"Projected reference {names[0]} is not a projected line.",
                "NATIVE_DRAWING_DIMENSION_REFERENCES_INVALID",
                repair={
                    "requested_subelements": names,
                    "accepted_references": (
                        "one line edge, two vertices, or two parallel line edges"
                    ),
                },
            )
        dx, dy = _line_vector(edge)
        if math.hypot(dx, dy) > _GEOMETRY_TOLERANCE:
            valid.append("aligned")
            if abs(dx) > _GEOMETRY_TOLERANCE:
                valid.append("horizontal")
            if abs(dy) > _GEOMETRY_TOLERANCE:
                valid.append("vertical")
    elif len(elements) == 2 and kinds == ("vertex", "vertex"):
        first_x, first_y = _point_xy(elements[0]["point_in_view_mm"])
        second_x, second_y = _point_xy(elements[1]["point_in_view_mm"])
        dx, dy = second_x - first_x, second_y - first_y
        if math.hypot(dx, dy) > _GEOMETRY_TOLERANCE:
            valid.append("aligned")
        if abs(dx) > _GEOMETRY_TOLERANCE:
            valid.append("horizontal")
        if abs(dy) > _GEOMETRY_TOLERANCE:
            valid.append("vertical")
    elif len(elements) == 2 and kinds == ("edge", "edge"):
        if not all(_is_line(element) for element in elements):
            _error(
                "Linear edge-pair references must both be projected lines.",
                "NATIVE_DRAWING_DIMENSION_REFERENCES_INVALID",
                repair={
                    "requested_subelements": names,
                    "accepted_references": "two parallel projected line edges",
                },
            )
        first_dx, first_dy = _line_vector(elements[0])
        second_dx, second_dy = _line_vector(elements[1])
        first_length = math.hypot(first_dx, first_dy)
        second_length = math.hypot(second_dx, second_dy)
        cross = first_dx * second_dy - first_dy * second_dx
        if (
            first_length <= _GEOMETRY_TOLERANCE
            or second_length <= _GEOMETRY_TOLERANCE
            or abs(cross)
            > _GEOMETRY_TOLERANCE * first_length * second_length
        ):
            _error(
                "Linear edge-pair references must be two parallel projected lines.",
                "NATIVE_DRAWING_DIMENSION_REFERENCES_INVALID",
                repair={
                    "requested_subelements": names,
                    "accepted_references": "two parallel projected line edges",
                },
            )
        first_x, first_y = _point_xy(elements[0]["start_in_view_mm"])
        second_x, second_y = _point_xy(elements[1]["start_in_view_mm"])
        separation = abs(
            first_dx * (second_y - first_y)
            - first_dy * (second_x - first_x)
        ) / first_length
        if separation > _GEOMETRY_TOLERANCE:
            valid.append("aligned")
            if abs(first_dx) <= _GEOMETRY_TOLERANCE:
                valid.append("horizontal")
            if abs(first_dy) <= _GEOMETRY_TOLERANCE:
                valid.append("vertical")
    else:
        # TechDraw also supports explicit point-to-line and single-point
        # coordinate forms. Its validator remains authoritative for those.
        return

    requested = _LINEAR_DIRECTION[operation]
    if requested not in valid:
        noun = " and ".join(names)
        _error(
            f"Projected line references {noun} cannot measure {requested} separation."
            if kinds == ("edge", "edge")
            else f"Projected references {noun} measure 0 mm {requested}.",
            "NATIVE_DRAWING_DIMENSION_REFERENCES_INVALID",
            repair={
                "requested_subelements": names,
                "valid_directions": valid,
            },
        )


def _validate_with_host(view: Any, spec: DimensionSpec) -> dict[str, Any]:
    try:
        if spec.operation == _AXONOMETRIC_OPERATION:
            from TechDrawTools.AxoLengthDimension import (
                analyze_axonometric_length,
            )

            analysis = analyze_axonometric_length(
                view,
                spec.subelements,
                spec.dimension_direction_subelement,
                spec.extension_direction_subelement,
            )
            if analysis.value_mode != spec.expected_value_mode:
                _error(
                    "The axonometric value mode changed after it was inspected.",
                    "NATIVE_DRAWING_DIMENSION_INFERENCE_STALE",
                    repair={"current_value_mode": analysis.value_mode},
                )
            return {
                "geometry_configuration": "axonometric_length",
                "approximate": False,
                "value_mode": analysis.value_mode,
                "line_angle_degrees": float(analysis.line_angle_degrees),
                "extension_angle_degrees": float(
                    analysis.extension_angle_degrees
                ),
            }
        import TechDrawGui

        validator_name = (
            "validateProjectedExtent"
            if spec.operation in _EXTENT_OPERATIONS
            else "validateProjectedDimension"
        )
        validator = getattr(TechDrawGui, validator_name, None)
        if not callable(validator):
            _error(
                "The installed TechDraw runtime cannot validate projected dimensions.",
                "NATIVE_DRAWING_DIMENSION_RUNTIME_UNAVAILABLE",
            )
        if spec.operation in _EXTENT_OPERATIONS:
            raw = validator(view, spec.dimension_type, list(spec.subelements))
        else:
            raw = validator(
                view,
                spec.dimension_type,
                list(spec.subelements),
                spec.allow_approximate,
            )
    except NativeDrawingError:
        raise
    except Exception as exc:
        host_message = str(exc).strip()
        _error(
            f"TechDraw rejected {spec.operation}: {host_message}",
            "NATIVE_DRAWING_DIMENSION_REFERENCES_INVALID",
            repair={
                "accepted_references": _REFERENCE_GUIDANCE[spec.operation],
                "requested_subelements": list(spec.subelements),
                "tool": "drawing.projected_geometry",
            },
        )
    if not isinstance(raw, Mapping) or set(raw) != {
        "geometry_configuration",
        "approximate",
    }:
        _error(
            "TechDraw returned an invalid projected-dimension validation result.",
            "NATIVE_DRAWING_DIMENSION_RUNTIME_UNAVAILABLE",
        )
    configuration = str(raw["geometry_configuration"] or "")
    approximate = raw["approximate"]
    if not configuration or type(approximate) is not bool:
        _error(
            "TechDraw returned an incomplete projected-dimension validation result.",
            "NATIVE_DRAWING_DIMENSION_RUNTIME_UNAVAILABLE",
        )
    return {
        "geometry_configuration": configuration,
        "approximate": approximate,
    }


def prepare_drawing_dimension(
    document: Any,
    *,
    operation: str,
    values: Mapping[str, Any],
) -> PreparedDrawingDimension:
    page, page_state = _resolve_page(document, values["page"])
    view, view_state, projection_state = _resolve_view(
        document,
        page,
        values["view"],
    )
    host_values = dict(values)
    host_values["label_position_in_view_mm"] = drawing_label_position_in_view_mm(
        view,
        host_values.pop("label_position_on_page_mm"),
        page=page,
    )
    spec = _spec(operation, host_values)
    targets = _reference_targets(operation, values)
    element_states = _resolve_elements(operation, projection_state, targets)
    if tuple(item["name"] for item in element_states) != tuple(
        str(target["subelement"]) for target in targets
    ):
        _error(
            "The Drawing dimension reference order is inconsistent.",
            "NATIVE_DRAWING_DIMENSION_REFERENCES_INVALID",
        )
    if operation in _LINEAR_DIRECTION:
        _validate_linear_reference_geometry(operation, element_states)
    validation = _validate_with_host(view, spec)
    return PreparedDrawingDimension(
        page=page,
        page_state_before=page_state,
        page_views_before=tuple(page.Views or ()),
        view=view,
        view_state_before=view_state,
        projection_state_before=projection_state,
        element_states_before=element_states,
        host_validation=validation,
        spec=spec,
        objects_before=tuple(document.Objects),
        timeline_before=_timeline_operations(document),
        selection_before=_selection(document),
        visibility_before=_visibility(document),
    )


def mutate_drawing_dimension(
    document: Any,
    *,
    prepared: PreparedDrawingDimension,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedDrawingDimension):
        raise TypeError("prepared must be a PreparedDrawingDimension")
    import TechDrawGui

    spec = prepared.spec
    creation_details: dict[str, Any] = {}
    try:
        if spec.operation == _AXONOMETRIC_OPERATION:
            from TechDrawTools.AxoLengthDimension import create_axonometric_length

            result = create_axonometric_length(
                prepared.view,
                spec.subelements,
                spec.dimension_direction_subelement,
                spec.extension_direction_subelement,
                label_position_in_view_mm=(spec.x_mm, spec.y_mm),
            )
            dimension = result.dimension
            creation_details = {
                "value_mode": result.analysis.value_mode,
                "projected_value_mm": float(result.projected_value_mm),
                "displayed_value_mm": float(result.displayed_value_mm),
            }
        elif spec.operation in _EXTENT_OPERATIONS:
            dimension = TechDrawGui.createProjectedExtent(
                prepared.view,
                spec.dimension_type,
                list(spec.subelements),
                spec.x_mm,
                spec.y_mm,
            )
        else:
            dimension = TechDrawGui.createProjectedDimension(
                prepared.view,
                spec.dimension_type,
                list(spec.subelements),
                spec.allow_approximate,
                spec.x_mm,
                spec.y_mm,
            )
        dimension.Label = spec.label
        # The host helper touches the source view so dependent presentation
        # can refresh.  This exact mutation recomputes the new dimension and
        # page itself; retain the accepted projection instead of leaving it
        # queued for an unrelated HLR pass on the next dimension.
        prepared.view.purgeTouched()
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_DIMENSION_CREATE_FAILED",
            f"TechDraw could not create {spec.operation}: {str(exc).strip()}",
        ) from exc
    if (
        not is_drawing_dimension(dimension)
        or dimension.Document is not document
        or (
            spec.operation in _EXTENT_OPERATIONS
            and not is_drawing_extent(dimension)
        )
    ):
        _error(
            "TechDraw did not create the exact projected dimension.",
            "NATIVE_DRAWING_DIMENSION_CREATE_FAILED",
        )
    try:
        document.publishProvisionalTimelineOperationBlock(dimension, (), ())
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_DIMENSION_HISTORY_FAILED",
            "The projected dimension could not be enrolled in History: "
            f"{str(exc).strip()}",
        ) from exc
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "dimension": dimension,
            "creation_details": creation_details,
        },
        # The source projection is verified immutable.  Recomputing it here is
        # both unnecessary and, for projection-group items, starts a second
        # asynchronous HLR pass after the detached projection was accepted.
        recompute_targets=(dimension, prepared.page),
        created=(object_identity(dimension),),
        changed=(object_identity(prepared.page),),
    )


def _identity(obj: Any) -> tuple[int, str, str]:
    return (
        int(getattr(obj, "ID", -1)),
        str(getattr(obj, "Name", "") or ""),
        str(getattr(obj, "TypeId", "") or ""),
    )


def _matches_document_label(actual: str, requested: str) -> bool:
    """Accept the request or FreeCAD's deterministic unique-label form."""

    return matches_preferred_document_label(actual, requested)


def _dimension_state_mismatches(
    state: Mapping[str, Any],
    *,
    spec: DimensionSpec,
    page_name: str,
    view_name: str,
    is_extent: bool,
) -> tuple[str, ...]:
    expected_references = [
        {"view_name": view_name, "subelement": name}
        for name in spec.subelements
    ]
    mismatches = []
    checks = (
        ("label", _matches_document_label(str(state["label"]), spec.label)),
        ("page", state["page_name"] == page_name),
        ("view", state["view_name"] == view_name),
        ("dimension_type", state["dimension_type"] == spec.dimension_type),
        ("measure_type", state["measure_type"] == "Projected"),
        (
            "references",
            state["target"]
            == {"scope": spec.target_scope, "subelements": list(spec.subelements)}
            if is_extent
            else state["references"] == expected_references,
        ),
        (
            "label_position_x",
            math.isclose(
                float(state["label_position_in_view_mm"]["x_mm"]),
                spec.x_mm,
                abs_tol=1.0e-9,
            ),
        ),
        (
            "label_position_y",
            math.isclose(
                float(state["label_position_in_view_mm"]["y_mm"]),
                spec.y_mm,
                abs_tol=1.0e-9,
            ),
        ),
        ("measured_value", float(state["measured_value"]["value"]) > 1.0e-12),
        ("timeline_role", state["timeline_role"] == "operation"),
        ("timeline_owner", not state["timeline_owner_name"]),
        ("timeline_usable", bool(state["timeline_usable"])),
        ("valid", bool(state["valid"])),
    )
    mismatches.extend(name for name, matches in checks if not matches)
    return tuple(mismatches)


def _verify_drawing_dimension(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedDrawingDimension = draft.value["prepared"]
    dimension = draft.value["dimension"]
    spec = prepared.spec
    before_ids = {_identity(obj) for obj in prepared.objects_before}
    new_objects = tuple(obj for obj in document.Objects if _identity(obj) not in before_ids)
    page_views = tuple(prepared.page.Views or ())
    if (
        tuple(map(_identity, new_objects)) != (_identity(dimension),)
        or tuple(map(_identity, page_views))
        != tuple(map(_identity, (*prepared.page_views_before, dimension)))
        or tuple(map(_identity, _timeline_operations(document)))
        != tuple(map(_identity, (*prepared.timeline_before, dimension)))
    ):
        _postcondition_error(
            "Drawing dimension creation changed objects, page membership, or History "
            "outside its exact result."
        )
    current_view_state = drawing_view_state(prepared.view)
    current_projection_state = drawing_projected_geometry_state(prepared.view)
    if (
        current_view_state["state_sha256"]
        != prepared.view_state_before["state_sha256"]
        or current_projection_state["projection_state_sha256"]
        != prepared.projection_state_before["projection_state_sha256"]
    ):
        _postcondition_error(
            "Drawing dimension creation unexpectedly changed its source projection.",
        )
    if _selection(document) != prepared.selection_before:
        _postcondition_error(
            "Drawing dimension creation changed the human selection.",
        )
    actual_visibility = tuple(
        (obj, bool(obj.ViewObject.Visibility))
        for obj, _visible in prepared.visibility_before
    )
    if actual_visibility != prepared.visibility_before:
        _postcondition_error(
            "Drawing dimension creation changed existing object visibility.",
        )
    is_extent = spec.operation in _EXTENT_OPERATIONS
    is_axonometric = spec.operation == _AXONOMETRIC_OPERATION
    state = (
        drawing_extent_state(dimension)
        if is_extent
        else drawing_axonometric_dimension_state(dimension)
        if is_axonometric
        else drawing_dimension_state(dimension)
    )
    mismatches = _dimension_state_mismatches(
        state,
        spec=spec,
        page_name=str(prepared.page.Name),
        view_name=str(prepared.view.Name),
        is_extent=is_extent,
    )
    if mismatches == ("measured_value",):
        direction = {
            "create_horizontal": "horizontally",
            "create_vertical": "vertically",
        }.get(spec.operation, "in the requested direction")
        _postcondition_error(
            "The projected references "
            f"{', '.join(spec.subelements)} measure 0 mm {direction}."
        )
    if mismatches:
        _postcondition_error(
            "The projected Drawing dimension did not retain: "
            + ", ".join(mismatches)
            + ".",
        )
    creation_details = draft.value.get("creation_details", {})
    if is_axonometric:
        axonometric = state["axonometric"]
        expected_arbitrary = spec.expected_value_mode != "projected"
        if (
            creation_details.get("value_mode") != spec.expected_value_mode
            or axonometric["arbitrary_display"] is not expected_arbitrary
            or not math.isclose(
                float(axonometric["line_angle_degrees"]),
                float(prepared.host_validation["line_angle_degrees"]),
                abs_tol=1.0e-9,
            )
            or not math.isclose(
                float(axonometric["extension_angle_degrees"]),
                float(prepared.host_validation["extension_angle_degrees"]),
                abs_tol=1.0e-9,
            )
            or float(creation_details.get("projected_value_mm", 0.0)) <= 1.0e-12
            or float(creation_details.get("displayed_value_mm", 0.0)) <= 1.0e-12
        ):
            _postcondition_error(
                "The axonometric dimension did not retain its exact angles or value mode."
            )
    page_state = drawing_page_state(prepared.page)
    if page_state["view_count"] != prepared.page_state_before["view_count"] + 1:
        _postcondition_error(
            "The Drawing page did not retain the new projected dimension.",
        )
    result = {
        "operation": spec.operation,
        "geometry_configuration": prepared.host_validation[
            "geometry_configuration"
        ],
        "approximate": prepared.host_validation["approximate"],
        "page": {
            "object_name": page_state["object_name"],
            "state_sha256": page_state["state_sha256"],
            "view_count": page_state["view_count"],
        },
        "dimension": provider_drawing_dimension_state(state, prepared.view),
    }
    if is_axonometric:
        result["value_mode"] = spec.expected_value_mode
        result["projected_value_mm"] = creation_details["projected_value_mm"]
        result["displayed_value_mm"] = creation_details["displayed_value_mm"]
    return result


def verify_drawing_dimension(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    try:
        return _verify_drawing_dimension(document, draft)
    except NativeMutationError:
        raise
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_DIMENSION_POSTCONDITION_FAILED",
            "The projected dimension could not be verified exactly: "
            f"{str(exc).strip()}",
        ) from exc
