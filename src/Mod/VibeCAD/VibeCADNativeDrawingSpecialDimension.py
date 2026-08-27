# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact transactional creation of specialized projected Drawing dimensions."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeDrawingDimensionSupport import (
    PreparedDrawingDimensionTarget,
    drawing_dimension_error,
    drawing_object_key,
    drawing_selection_state,
    drawing_timeline_operations,
    exact_drawing_mapping,
    finite_drawing_coordinate,
    matches_drawing_document_label,
    prepare_drawing_dimension_target,
    drawing_label_position_in_view_mm,
    provider_drawing_dimension_state,
)
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingSpecialDimensionState import (
    drawing_arc_length_dimension_state,
    drawing_chamfer_dimension_state,
)
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewState import drawing_view_state
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeTargets import object_identity


_DIMENSION_TYPES = {
    "create_horizontal_chamfer": "DistanceX",
    "create_vertical_chamfer": "DistanceY",
}


@dataclass(frozen=True, slots=True)
class ChamferSpec:
    operation: str
    dimension_type: str
    label: str
    x_mm: float
    y_mm: float
    subelements: tuple[str, str]


@dataclass(frozen=True, slots=True)
class PreparedDrawingChamfer:
    target: PreparedDrawingDimensionTarget
    spec: ChamferSpec
    host_validation: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ArcLengthSpec:
    operation: str
    label: str
    x_mm: float
    y_mm: float
    edge_name: str


@dataclass(frozen=True, slots=True)
class PreparedDrawingArcLength:
    target: PreparedDrawingDimensionTarget
    spec: ArcLengthSpec
    host_validation: dict[str, Any]


def _postcondition_error(message: str) -> None:
    raise NativeMutationError(
        "NATIVE_DRAWING_CHAMFER_POSTCONDITION_FAILED",
        message,
    )


def _spec(operation: str, values: Mapping[str, Any]) -> ChamferSpec:
    if operation not in _DIMENSION_TYPES:
        raise ValueError("operation is not a Drawing chamfer operation")
    label = str(values["label"] or "")
    if label != label.strip() or not 1 <= len(label) <= 160:
        drawing_dimension_error(
            "A Drawing chamfer label must contain 1 to 160 non-padding characters.",
            "NATIVE_DRAWING_DIMENSION_PARAMETERS_INVALID",
        )
    position = exact_drawing_mapping(
        values["label_position_in_view_mm"],
        frozenset({"x_mm", "y_mm"}),
        "chamfer label position",
    )
    first = exact_drawing_mapping(
        values["first_vertex"],
        frozenset({"subelement"}),
        "first chamfer vertex",
    )
    second = exact_drawing_mapping(
        values["second_vertex"],
        frozenset({"subelement"}),
        "second chamfer vertex",
    )
    names = (str(first["subelement"]), str(second["subelement"]))
    if names[0] == names[1] or any(not name.startswith("Vertex") for name in names):
        drawing_dimension_error(
            "A Drawing chamfer requires two distinct projected VertexN references.",
            "NATIVE_DRAWING_DIMENSION_REFERENCES_INVALID",
        )
    return ChamferSpec(
        operation=operation,
        dimension_type=_DIMENSION_TYPES[operation],
        label=label,
        x_mm=finite_drawing_coordinate(position["x_mm"], "label x_mm"),
        y_mm=finite_drawing_coordinate(position["y_mm"], "label y_mm"),
        subelements=names,
    )


def _arc_length_spec(operation: str, values: Mapping[str, Any]) -> ArcLengthSpec:
    if operation != "create_arc_length_dimension":
        raise ValueError("operation is not a Drawing arc-length operation")
    label = str(values["label"] or "")
    if label != label.strip() or not 1 <= len(label) <= 160:
        drawing_dimension_error(
            "A Drawing arc-length label must contain 1 to 160 non-padding characters.",
            "NATIVE_DRAWING_DIMENSION_PARAMETERS_INVALID",
        )
    position = exact_drawing_mapping(
        values["label_position_in_view_mm"],
        frozenset({"x_mm", "y_mm"}),
        "arc-length label position",
    )
    edge = exact_drawing_mapping(
        values["arc_edge"],
        frozenset({"subelement"}),
        "arc-length source edge",
    )
    edge_name = str(edge["subelement"] or "")
    if not edge_name.startswith("Edge"):
        drawing_dimension_error(
            "A Drawing arc length requires one projected EdgeN reference.",
            "NATIVE_DRAWING_DIMENSION_REFERENCES_INVALID",
        )
    return ArcLengthSpec(
        operation=operation,
        label=label,
        x_mm=finite_drawing_coordinate(position["x_mm"], "label x_mm"),
        y_mm=finite_drawing_coordinate(position["y_mm"], "label y_mm"),
        edge_name=edge_name,
    )


def _validate_host(view: Any, spec: ChamferSpec) -> dict[str, Any]:
    try:
        import TechDrawGui

        validator = getattr(TechDrawGui, "validateProjectedChamfer", None)
        if not callable(validator):
            drawing_dimension_error(
                "The installed TechDraw runtime cannot validate chamfer dimensions.",
                "NATIVE_DRAWING_DIMENSION_RUNTIME_UNAVAILABLE",
            )
        raw = validator(view, spec.dimension_type, list(spec.subelements))
    except NativeDrawingError:
        raise
    except Exception as exc:
        drawing_dimension_error(
            f"TechDraw rejected {spec.operation}: {str(exc).strip()}",
            "NATIVE_DRAWING_DIMENSION_REFERENCES_INVALID",
            repair={
                "accepted_references": "two distinct projected VertexN references",
                "requested_subelements": list(spec.subelements),
                "tool": "drawing.projected_geometry",
            },
        )
    if not isinstance(raw, Mapping) or set(raw) != {
        "geometry_configuration",
        "approximate",
    }:
        drawing_dimension_error(
            "TechDraw returned an invalid chamfer validation result.",
            "NATIVE_DRAWING_DIMENSION_RUNTIME_UNAVAILABLE",
        )
    configuration = str(raw["geometry_configuration"] or "")
    approximate = raw["approximate"]
    if not configuration or type(approximate) is not bool or approximate:
        drawing_dimension_error(
            "TechDraw returned an incomplete chamfer validation result.",
            "NATIVE_DRAWING_DIMENSION_RUNTIME_UNAVAILABLE",
        )
    return {
        "geometry_configuration": configuration,
        "approximate": approximate,
    }


def _validate_arc_length_host(
    view: Any,
    spec: ArcLengthSpec,
) -> dict[str, Any]:
    try:
        import TechDrawGui

        validator = getattr(TechDrawGui, "validateProjectedArcLength", None)
        if not callable(validator):
            drawing_dimension_error(
                "The installed TechDraw runtime cannot validate arc lengths.",
                "NATIVE_DRAWING_DIMENSION_RUNTIME_UNAVAILABLE",
            )
        raw = validator(view, spec.edge_name)
    except NativeDrawingError:
        raise
    except Exception as exc:
        drawing_dimension_error(
            f"TechDraw rejected {spec.operation}: {str(exc).strip()}",
            "NATIVE_DRAWING_DIMENSION_REFERENCES_INVALID",
            repair={
                "accepted_references": "one exact open circular ArcOfCircle EdgeN",
                "requested_subelement": spec.edge_name,
                "tool": "drawing.projected_geometry",
            },
        )
    if not isinstance(raw, Mapping) or set(raw) != {
        "geometry_configuration",
        "arc_length_mm",
    }:
        drawing_dimension_error(
            "TechDraw returned an invalid arc-length validation result.",
            "NATIVE_DRAWING_DIMENSION_RUNTIME_UNAVAILABLE",
        )
    configuration = str(raw["geometry_configuration"] or "")
    length = finite_drawing_coordinate(raw["arc_length_mm"], "arc length")
    if configuration != "circular_arc" or length <= 0.0:
        drawing_dimension_error(
            "TechDraw returned an incomplete arc-length validation result.",
            "NATIVE_DRAWING_DIMENSION_RUNTIME_UNAVAILABLE",
        )
    return {"geometry_configuration": configuration, "arc_length_mm": length}


def prepare_drawing_chamfer(
    document: Any,
    *,
    operation: str,
    values: Mapping[str, Any],
) -> PreparedDrawingChamfer:
    element_targets = (
        values["first_vertex"],
        values["second_vertex"],
    )
    target = prepare_drawing_dimension_target(
        document,
        page_target=values["page"],
        view_target=values["view"],
        element_targets=element_targets,
        allowed_element_types=frozenset({"vertex"}),
    )
    host_values = dict(values)
    host_values["label_position_in_view_mm"] = drawing_label_position_in_view_mm(
        target.view,
        host_values.pop("label_position_on_page_mm"),
        page=target.page,
    )
    spec = _spec(operation, host_values)
    if tuple(item["name"] for item in target.element_states_before) != spec.subelements:
        drawing_dimension_error(
            "The Drawing chamfer vertex order is inconsistent.",
            "NATIVE_DRAWING_DIMENSION_REFERENCES_INVALID",
        )
    return PreparedDrawingChamfer(
        target=target,
        spec=spec,
        host_validation=_validate_host(target.view, spec),
    )


def prepare_drawing_arc_length(
    document: Any,
    *,
    operation: str,
    values: Mapping[str, Any],
) -> PreparedDrawingArcLength:
    target = prepare_drawing_dimension_target(
        document,
        page_target=values["page"],
        view_target=values["view"],
        element_targets=(values["arc_edge"],),
        allowed_element_types=frozenset({"edge"}),
    )
    host_values = dict(values)
    host_values["label_position_in_view_mm"] = drawing_label_position_in_view_mm(
        target.view,
        host_values.pop("label_position_on_page_mm"),
        page=target.page,
    )
    spec = _arc_length_spec(operation, host_values)
    if target.element_states_before[0]["name"] != spec.edge_name:
        drawing_dimension_error(
            "The Drawing arc-length edge target is inconsistent.",
            "NATIVE_DRAWING_DIMENSION_REFERENCES_INVALID",
        )
    return PreparedDrawingArcLength(
        target=target,
        spec=spec,
        host_validation=_validate_arc_length_host(target.view, spec),
    )


def mutate_drawing_chamfer(
    document: Any,
    *,
    prepared: PreparedDrawingChamfer,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedDrawingChamfer):
        raise TypeError("prepared must be a PreparedDrawingChamfer")
    import TechDrawGui

    spec = prepared.spec
    try:
        dimension = TechDrawGui.createProjectedChamfer(
            prepared.target.view,
            spec.dimension_type,
            list(spec.subelements),
            spec.x_mm,
            spec.y_mm,
        )
        dimension.Label = spec.label
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_CHAMFER_CREATE_FAILED",
            f"TechDraw could not create {spec.operation}: {str(exc).strip()}",
        ) from exc
    if (
        dimension.Document is not document
        or not dimension.isDerivedFrom("TechDraw::DrawViewDimension")
    ):
        drawing_dimension_error(
            "TechDraw did not create the exact chamfer dimension.",
            "NATIVE_DRAWING_CHAMFER_CREATE_FAILED",
        )
    try:
        document.publishProvisionalTimelineOperationBlock(dimension, (), ())
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_CHAMFER_HISTORY_FAILED",
            "The chamfer dimension could not be enrolled in History: "
            f"{str(exc).strip()}",
        ) from exc
    return NativeMutationDraft(
        value={"prepared": prepared, "dimension": dimension},
        recompute_targets=(dimension, prepared.target.view, prepared.target.page),
        created=(object_identity(dimension),),
        changed=(
            object_identity(prepared.target.page),
            object_identity(prepared.target.view),
        ),
    )


def mutate_drawing_arc_length(
    document: Any,
    *,
    prepared: PreparedDrawingArcLength,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedDrawingArcLength):
        raise TypeError("prepared must be a PreparedDrawingArcLength")
    import TechDrawGui

    spec = prepared.spec
    try:
        dimension = TechDrawGui.createProjectedArcLength(
            prepared.target.view,
            spec.edge_name,
            spec.x_mm,
            spec.y_mm,
        )
        dimension.Label = spec.label
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_ARC_LENGTH_CREATE_FAILED",
            f"TechDraw could not create {spec.operation}: {str(exc).strip()}",
        ) from exc
    if (
        dimension.Document is not document
        or not dimension.isDerivedFrom("TechDraw::DrawViewDimension")
    ):
        drawing_dimension_error(
            "TechDraw did not create the exact arc-length dimension.",
            "NATIVE_DRAWING_ARC_LENGTH_CREATE_FAILED",
        )
    try:
        document.publishProvisionalTimelineOperationBlock(dimension, (), ())
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_ARC_LENGTH_HISTORY_FAILED",
            "The arc-length dimension could not be enrolled in History: "
            f"{str(exc).strip()}",
        ) from exc
    return NativeMutationDraft(
        value={"prepared": prepared, "dimension": dimension},
        recompute_targets=(dimension, prepared.target.view, prepared.target.page),
        created=(object_identity(dimension),),
        changed=(
            object_identity(prepared.target.page),
            object_identity(prepared.target.view),
        ),
    )


def _verify_drawing_chamfer(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedDrawingChamfer = draft.value["prepared"]
    target = prepared.target
    spec = prepared.spec
    dimension = draft.value["dimension"]
    before_keys = {drawing_object_key(obj) for obj in target.objects_before}
    new_objects = tuple(
        obj for obj in document.Objects if drawing_object_key(obj) not in before_keys
    )
    if (
        tuple(map(drawing_object_key, new_objects)) != (drawing_object_key(dimension),)
        or tuple(map(drawing_object_key, tuple(target.page.Views or ())))
        != tuple(map(drawing_object_key, (*target.page_views_before, dimension)))
        or tuple(map(drawing_object_key, drawing_timeline_operations(document)))
        != tuple(map(drawing_object_key, (*target.timeline_before, dimension)))
    ):
        _postcondition_error(
            "Chamfer creation changed objects, page membership, or History outside its result."
        )
    if (
        drawing_view_state(target.view)["state_sha256"]
        != target.view_state_before["state_sha256"]
        or drawing_projected_geometry_state(target.view)[
            "projection_state_sha256"
        ]
        != target.projection_state_before["projection_state_sha256"]
    ):
        _postcondition_error("Chamfer creation changed its source projection.")
    if drawing_selection_state(document) != target.selection_before:
        _postcondition_error("Chamfer creation changed the human selection.")
    actual_visibility = tuple(
        (obj, bool(obj.ViewObject.Visibility))
        for obj, _visible in target.visibility_before
    )
    if actual_visibility != target.visibility_before:
        _postcondition_error("Chamfer creation changed existing visibility.")
    state = drawing_chamfer_dimension_state(dimension)
    expected_references = [
        {"view_name": str(target.view.Name), "subelement": name}
        for name in spec.subelements
    ]
    if (
        not matches_drawing_document_label(state["label"], spec.label)
        or state["page_name"] != str(target.page.Name)
        or state["view_name"] != str(target.view.Name)
        or state["dimension_type"] != spec.dimension_type
        or state["references"] != expected_references
        or not math.isclose(
            float(state["label_position_in_view_mm"]["x_mm"]),
            spec.x_mm,
            abs_tol=1.0e-9,
        )
        or not math.isclose(
            float(state["label_position_in_view_mm"]["y_mm"]),
            spec.y_mm,
            abs_tol=1.0e-9,
        )
        or float(state["measured_value"]["value"]) <= 1.0e-12
        or state["timeline_role"] != "operation"
        or state["timeline_owner_name"]
        or not state["timeline_usable"]
        or not state["valid"]
    ):
        _postcondition_error("The chamfer did not retain its exact requested state.")
    page_state = drawing_page_state(target.page)
    if page_state["view_count"] != target.page_state_before["view_count"] + 1:
        _postcondition_error("The Drawing page did not retain the new chamfer.")
    return {
        "operation": spec.operation,
        "geometry_configuration": prepared.host_validation[
            "geometry_configuration"
        ],
        "page": {
            "object_name": page_state["object_name"],
            "state_sha256": page_state["state_sha256"],
            "view_count": page_state["view_count"],
        },
        "dimension": provider_drawing_dimension_state(state, target.view),
    }


def verify_drawing_chamfer(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    try:
        return _verify_drawing_chamfer(document, draft)
    except NativeMutationError:
        raise
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_CHAMFER_POSTCONDITION_FAILED",
            "The chamfer dimension could not be verified exactly: "
            f"{str(exc).strip()}",
        ) from exc


def _arc_length_postcondition_error(message: str) -> None:
    raise NativeMutationError(
        "NATIVE_DRAWING_ARC_LENGTH_POSTCONDITION_FAILED",
        message,
    )


def _verify_drawing_arc_length(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedDrawingArcLength = draft.value["prepared"]
    target = prepared.target
    spec = prepared.spec
    dimension = draft.value["dimension"]
    before_keys = {drawing_object_key(obj) for obj in target.objects_before}
    new_objects = tuple(
        obj for obj in document.Objects if drawing_object_key(obj) not in before_keys
    )
    if (
        tuple(map(drawing_object_key, new_objects)) != (drawing_object_key(dimension),)
        or tuple(map(drawing_object_key, tuple(target.page.Views or ())))
        != tuple(map(drawing_object_key, (*target.page_views_before, dimension)))
        or tuple(map(drawing_object_key, drawing_timeline_operations(document)))
        != tuple(map(drawing_object_key, (*target.timeline_before, dimension)))
    ):
        _arc_length_postcondition_error(
            "Arc-length creation changed objects, page membership, or History "
            "outside its exact result."
        )
    if (
        drawing_view_state(target.view)["state_sha256"]
        != target.view_state_before["state_sha256"]
    ):
        _arc_length_postcondition_error(
            "Arc-length creation changed persistent source-view settings."
        )

    projection_after = drawing_projected_geometry_state(target.view)
    state = drawing_arc_length_dimension_state(dimension)
    source_edge = target.element_states_before[0]
    if (
        projection_after["projection_state_sha256"]
        != target.projection_state_before["projection_state_sha256"]
    ):
        _arc_length_postcondition_error(
            "Arc-length creation changed its source projection."
        )
    if drawing_selection_state(document) != target.selection_before:
        _arc_length_postcondition_error(
            "Arc-length creation changed the human selection."
        )
    actual_visibility = tuple(
        (obj, bool(obj.ViewObject.Visibility))
        for obj, _visible in target.visibility_before
    )
    if actual_visibility != target.visibility_before:
        _arc_length_postcondition_error(
            "Arc-length creation changed existing visibility."
        )
    expected_source = {
        "view_name": str(target.view.Name),
        "subelement": spec.edge_name,
        "element_state_sha256": source_edge["element_state_sha256"],
    }
    if (
        not matches_drawing_document_label(state["label"], spec.label)
        or state["page_name"] != str(target.page.Name)
        or state["view_name"] != str(target.view.Name)
        or state["dimension_type"] != "Distance"
        or state["arc_length"]["source"] != expected_source
        or not math.isclose(
            float(state["arc_length"]["length_mm"]),
            float(prepared.host_validation["arc_length_mm"]),
            rel_tol=1.0e-9,
            abs_tol=1.0e-9,
        )
        or not math.isclose(
            float(state["label_position_in_view_mm"]["x_mm"]),
            spec.x_mm,
            abs_tol=1.0e-9,
        )
        or not math.isclose(
            float(state["label_position_in_view_mm"]["y_mm"]),
            spec.y_mm,
            abs_tol=1.0e-9,
        )
        or state["timeline_role"] != "operation"
        or state["timeline_owner_name"]
        or not state["timeline_usable"]
        or not state["valid"]
    ):
        _arc_length_postcondition_error(
            "The arc-length dimension did not retain its exact requested state."
        )
    page_state = drawing_page_state(target.page)
    if page_state["view_count"] != target.page_state_before["view_count"] + 1:
        _arc_length_postcondition_error(
            "The Drawing page did not retain the new arc-length dimension."
        )
    return {
        "operation": spec.operation,
        "geometry_configuration": prepared.host_validation[
            "geometry_configuration"
        ],
        "page": {
            "object_name": page_state["object_name"],
            "state_sha256": page_state["state_sha256"],
            "view_count": page_state["view_count"],
        },
        "dimension": provider_drawing_dimension_state(state, target.view),
    }


def verify_drawing_arc_length(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    try:
        return _verify_drawing_arc_length(document, draft)
    except NativeMutationError:
        raise
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_ARC_LENGTH_POSTCONDITION_FAILED",
            "The arc-length dimension could not be verified exactly: "
            f"{str(exc).strip()}",
        ) from exc
