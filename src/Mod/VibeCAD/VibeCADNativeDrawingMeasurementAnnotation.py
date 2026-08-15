# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact transactional creation of host-measured Drawing annotations."""

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
    matches_drawing_document_label,
    prepare_drawing_dimension_target,
)
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state
from VibeCADNativeDrawingMeasurementAnnotationState import (
    MAX_DRAWING_MEASUREMENT_ELEMENTS,
    drawing_measurement_annotation_state,
)
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewState import drawing_view_state
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeTargets import object_identity


@dataclass(frozen=True, slots=True)
class MeasurementAnnotationSpec:
    operation: str
    kind: str
    label: str
    element_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PreparedDrawingMeasurementAnnotation:
    target: PreparedDrawingDimensionTarget
    spec: MeasurementAnnotationSpec
    host_validation: dict[str, Any]
    next_balloon_index_before: int


def _error(
    message: str,
    code: str,
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    drawing_dimension_error(message, code, repair=repair)


def _same(first: float, second: float) -> bool:
    return math.isclose(first, second, rel_tol=1.0e-9, abs_tol=1.0e-8)


def _finite(value: Any, noun: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeDrawingError(
            f"Drawing measurement {noun} must be numeric.",
            error_code="NATIVE_DRAWING_MEASUREMENT_RUNTIME_UNAVAILABLE",
        ) from exc
    if not math.isfinite(result) or abs(result) > 1.0e18:
        _error(
            f"Drawing measurement {noun} is outside the supported range.",
            "NATIVE_DRAWING_MEASUREMENT_RUNTIME_UNAVAILABLE",
        )
    return result


def _point(value: Any, noun: str) -> dict[str, float]:
    exact = exact_drawing_mapping(
        value,
        frozenset({"x_mm", "y_mm"}),
        noun,
        family="measurement annotation",
        error_code="NATIVE_DRAWING_MEASUREMENT_RUNTIME_UNAVAILABLE",
    )
    return {
        "x_mm": _finite(exact["x_mm"], f"{noun} X coordinate"),
        "y_mm": _finite(exact["y_mm"], f"{noun} Y coordinate"),
    }


def _spec(operation: str, values: Mapping[str, Any]) -> MeasurementAnnotationSpec:
    kinds = {
        "create_area_annotation": ("area", "Face"),
        "create_arc_length_annotation": ("arc_length", "Edge"),
    }
    if operation not in kinds:
        raise ValueError("operation is not a Drawing measurement annotation operation")
    label = str(values["label"] or "")
    if label != label.strip() or not 1 <= len(label) <= 160:
        _error(
            "A Drawing measurement annotation label must contain 1 to 160 "
            "non-padding characters.",
            "NATIVE_DRAWING_MEASUREMENT_PARAMETERS_INVALID",
        )
    raw_elements = values["elements"]
    if not isinstance(raw_elements, (list, tuple)) or not (
        1 <= len(raw_elements) <= MAX_DRAWING_MEASUREMENT_ELEMENTS
    ):
        _error(
            "A Drawing measurement annotation requires 1 to 64 exact elements.",
            "NATIVE_DRAWING_MEASUREMENT_PARAMETERS_INVALID",
        )
    prefix = kinds[operation][1]
    names = []
    for index, raw in enumerate(raw_elements):
        exact = exact_drawing_mapping(
            raw,
            frozenset({"subelement", "expected_element_state_sha256"}),
            f"element {index}",
            family="measurement annotation",
            error_code="NATIVE_DRAWING_MEASUREMENT_PARAMETERS_INVALID",
        )
        name = str(exact["subelement"] or "")
        if not name.startswith(prefix):
            _error(
                f"{operation} requires only projected {prefix}N elements.",
                "NATIVE_DRAWING_MEASUREMENT_REFERENCE_TYPE_INVALID",
                repair={"accepted_reference_types": [prefix.casefold()]},
            )
        names.append(name)
    if len(names) != len(set(names)):
        _error(
            "A Drawing measurement annotation cannot repeat an element.",
            "NATIVE_DRAWING_MEASUREMENT_REFERENCES_INVALID",
        )
    return MeasurementAnnotationSpec(
        operation=operation,
        kind=kinds[operation][0],
        label=label,
        element_names=tuple(names),
    )


def _expected_value(
    spec: MeasurementAnnotationSpec,
    target: PreparedDrawingDimensionTarget,
) -> float:
    scale = float(target.projection_state_before["view_scale"])
    if spec.kind == "area":
        return sum(
            float(element["area_view_mm2"])
            for element in target.element_states_before
        ) / (scale * scale)
    return sum(
        float(element["length_view_mm"])
        for element in target.element_states_before
    ) / scale


def _validate_host(
    target: PreparedDrawingDimensionTarget,
    spec: MeasurementAnnotationSpec,
) -> dict[str, Any]:
    try:
        import TechDrawGui

        validator = getattr(
            TechDrawGui,
            "validateProjectedMeasurementAnnotation",
            None,
        )
        if not callable(validator):
            _error(
                "The installed TechDraw runtime cannot validate projected "
                "measurement annotations.",
                "NATIVE_DRAWING_MEASUREMENT_RUNTIME_UNAVAILABLE",
            )
        raw = validator(target.view, spec.kind, list(spec.element_names))
    except NativeDrawingError:
        raise
    except Exception as exc:
        _error(
            "TechDraw rejected the projected measurement annotation: "
            f"{str(exc).strip()}",
            "NATIVE_DRAWING_MEASUREMENT_REFERENCE_INVALID",
            repair={"inspect_operation": "drawing_projected_geometry"},
        )
    expected_fields = frozenset(
        {
            "kind",
            "elements",
            "value",
            "unit",
            "anchor_in_view_mm",
            "anchor_in_source_mm",
            "text",
        }
    )
    if not isinstance(raw, Mapping) or frozenset(raw) != expected_fields:
        _error(
            "TechDraw returned malformed projected measurement data.",
            "NATIVE_DRAWING_MEASUREMENT_RUNTIME_UNAVAILABLE",
        )
    elements = tuple(str(name or "") for name in tuple(raw["elements"] or ()))
    value = _finite(raw["value"], "value")
    anchor_in_view = _point(raw["anchor_in_view_mm"], "anchor in view")
    anchor_in_source = _point(raw["anchor_in_source_mm"], "anchor in source")
    scale = float(target.projection_state_before["view_scale"])
    text = str(raw["text"] or "")
    expected_unit = "mm^2" if spec.kind == "area" else "mm"
    if (
        str(raw["kind"] or "") != spec.kind
        or elements != spec.element_names
        or str(raw["unit"] or "") != expected_unit
        or not text
        or len(text) > 512
        or not _same(value, _expected_value(spec, target))
        or not _same(anchor_in_source["x_mm"], anchor_in_view["x_mm"] / scale)
        or not _same(anchor_in_source["y_mm"], anchor_in_view["y_mm"] / scale)
    ):
        _error(
            "TechDraw's measured annotation does not match the inspected geometry.",
            "NATIVE_DRAWING_MEASUREMENT_REFERENCE_INVALID",
            repair={"inspect_operation": "drawing_projected_geometry"},
        )
    return {
        "kind": spec.kind,
        "elements": list(elements),
        "value": value,
        "unit": expected_unit,
        "anchor_in_view_mm": anchor_in_view,
        "anchor_in_source_mm": anchor_in_source,
        "text": text,
    }


def prepare_drawing_measurement_annotation(
    document: Any,
    *,
    operation: str,
    values: Mapping[str, Any],
) -> PreparedDrawingMeasurementAnnotation:
    spec = _spec(operation, values)
    raw_elements = tuple(values["elements"])
    target = prepare_drawing_dimension_target(
        document,
        page_target=values["page"],
        view_target=values["view"],
        element_targets=raw_elements,
        allowed_element_types=frozenset(
            {"face"} if spec.kind == "area" else {"edge"}
        ),
        family="measurement annotation",
        code_prefix="NATIVE_DRAWING_MEASUREMENT",
    )
    if tuple(item["name"] for item in target.element_states_before) != (
        spec.element_names
    ):
        _error(
            "The Drawing measurement element targets are inconsistent.",
            "NATIVE_DRAWING_MEASUREMENT_REFERENCE_INVALID",
        )
    return PreparedDrawingMeasurementAnnotation(
        target=target,
        spec=spec,
        host_validation=_validate_host(target, spec),
        next_balloon_index_before=int(target.page.NextBalloonIndex),
    )


def mutate_drawing_measurement_annotation(
    document: Any,
    *,
    prepared: PreparedDrawingMeasurementAnnotation,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedDrawingMeasurementAnnotation):
        raise TypeError("prepared must be a PreparedDrawingMeasurementAnnotation")
    import TechDrawGui

    try:
        annotation = TechDrawGui.createProjectedMeasurementAnnotation(
            prepared.target.view,
            prepared.spec.kind,
            list(prepared.spec.element_names),
            prepared.spec.label,
        )
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_MEASUREMENT_CREATE_FAILED",
            "TechDraw could not create the projected measurement annotation: "
            f"{str(exc).strip()}",
        ) from exc
    if (
        getattr(annotation, "Document", None) is not document
        or not annotation.isDerivedFrom("TechDraw::DrawViewBalloon")
    ):
        _error(
            "TechDraw did not create the exact measurement annotation.",
            "NATIVE_DRAWING_MEASUREMENT_CREATE_FAILED",
        )
    try:
        document.publishProvisionalTimelineOperationBlock(annotation, (), ())
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_MEASUREMENT_HISTORY_FAILED",
            "The measurement annotation could not be enrolled in History: "
            f"{str(exc).strip()}",
        ) from exc
    return NativeMutationDraft(
        value={"prepared": prepared, "annotation": annotation},
        recompute_targets=(
            annotation,
            prepared.target.view,
            prepared.target.page,
        ),
        created=(object_identity(annotation),),
        changed=(
            object_identity(prepared.target.page),
            object_identity(prepared.target.view),
        ),
    )


def _postcondition_error(message: str) -> None:
    raise NativeMutationError(
        "NATIVE_DRAWING_MEASUREMENT_POSTCONDITION_FAILED",
        message,
    )


def _verify(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedDrawingMeasurementAnnotation = draft.value["prepared"]
    target = prepared.target
    spec = prepared.spec
    annotation = draft.value["annotation"]
    before_keys = {drawing_object_key(obj) for obj in target.objects_before}
    new_objects = tuple(
        obj for obj in document.Objects if drawing_object_key(obj) not in before_keys
    )
    if (
        tuple(map(drawing_object_key, new_objects))
        != (drawing_object_key(annotation),)
        or tuple(map(drawing_object_key, tuple(target.page.Views or ())))
        != tuple(map(drawing_object_key, (*target.page_views_before, annotation)))
        or tuple(map(drawing_object_key, drawing_timeline_operations(document)))
        != tuple(map(drawing_object_key, (*target.timeline_before, annotation)))
    ):
        _postcondition_error(
            "Measurement creation changed objects, page membership, or History "
            "outside its result."
        )
    if int(target.page.NextBalloonIndex) != prepared.next_balloon_index_before:
        _postcondition_error(
            "Host-derived measurement text consumed the human Balloon number sequence."
        )
    if (
        drawing_view_state(target.view)["state_sha256"]
        != target.view_state_before["state_sha256"]
        or drawing_projected_geometry_state(target.view)[
            "projection_state_sha256"
        ]
        != target.projection_state_before["projection_state_sha256"]
    ):
        _postcondition_error(
            "Measurement annotation creation changed its source projection."
        )
    if drawing_selection_state(document) != target.selection_before:
        _postcondition_error(
            "Measurement annotation creation changed the human selection."
        )
    visibility = tuple(
        (obj, bool(obj.ViewObject.Visibility))
        for obj, _visible in target.visibility_before
    )
    if visibility != target.visibility_before:
        _postcondition_error(
            "Measurement annotation creation changed existing visibility."
        )

    state = drawing_measurement_annotation_state(annotation)
    source_elements = state["source_elements"]
    expected_sources = [
        {
            "subelement": element["name"],
            "element_type": element["element_type"],
            "element_state_sha256": element["element_state_sha256"],
        }
        for element in target.element_states_before
    ]
    host = prepared.host_validation
    anchor = state["anchor_in_source_mm"]
    mismatches = [
        noun
        for noun, matches in (
            (
                "label",
                matches_drawing_document_label(state["label"], spec.label),
            ),
            ("kind", state["kind"] == spec.kind),
            ("unit", state["unit"] == host["unit"]),
            ("value", _same(float(state["value"]), float(host["value"]))),
            ("measurement_current", bool(state["measurement_current"])),
            ("text", state["text"] == host["text"]),
            ("sources", source_elements == expected_sources),
            ("page", state["page_name"] == str(target.page.Name)),
            ("source_view", state["source_view_name"] == str(target.view.Name)),
            (
                "anchor_x",
                _same(
                    float(anchor["x_mm"]),
                    float(host["anchor_in_source_mm"]["x_mm"]),
                ),
            ),
            (
                "anchor_y",
                _same(
                    float(anchor["y_mm"]),
                    float(host["anchor_in_source_mm"]["y_mm"]),
                ),
            ),
            (
                "anchor_matches_source",
                spec.kind != "area" or state["anchor_matches_source"] is True,
            ),
            ("default_placement", bool(state["default_placement"])),
            ("timeline_role", state["timeline_role"] == "operation"),
            ("timeline_owner", not state["timeline_owner_name"]),
            ("timeline_usable", bool(state["timeline_usable"])),
            ("valid", bool(state["valid"])),
        )
        if not matches
    ]
    if mismatches:
        _postcondition_error(
            "The measurement annotation did not retain these exact fields: "
            + ", ".join(mismatches)
            + "."
        )
    page_state = drawing_page_state(target.page)
    if page_state["view_count"] != target.page_state_before["view_count"] + 1:
        _postcondition_error(
            "The Drawing page did not retain the measurement annotation."
        )
    return {
        "operation": spec.operation,
        "page": {
            "object_name": page_state["object_name"],
            "state_sha256": page_state["state_sha256"],
            "view_count": page_state["view_count"],
        },
        "measurement_annotation": state,
    }


def verify_drawing_measurement_annotation(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    try:
        return _verify(document, draft)
    except NativeMutationError:
        raise
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_MEASUREMENT_POSTCONDITION_FAILED",
            "The Drawing measurement annotation could not be verified exactly: "
            f"{str(exc).strip()}",
        ) from exc
