# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact atomic creation of Drawing cosmetic circles and arcs."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeDrawingCosmeticCurveState import (
    DRAWING_COSMETIC_CURVE_KINDS,
    MAX_DRAWING_COSMETIC_CURVES,
    MAX_DRAWING_COSMETIC_RADIUS_MM,
    drawing_cosmetic_curve_inventory_state,
    drawing_cosmetic_curve_result_state,
    normalize_cosmetic_curve_host_plan,
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


_OPERATIONS = frozenset(f"create_{kind}" for kind in DRAWING_COSMETIC_CURVE_KINDS)
_TARGET_FIELDS = frozenset({"subelement"})
_TARGET_ROLES = {
    "one_point_circle": ("center_vertex",),
    "two_point_circle": ("center_vertex", "radius_vertex"),
    "three_point_circle": (
        "first_perimeter_vertex",
        "second_perimeter_vertex",
        "third_perimeter_vertex",
    ),
    "center_start_end_arc": ("center_vertex", "start_vertex", "end_vertex"),
}


@dataclass(frozen=True, slots=True)
class DrawingCosmeticCurveSpec:
    operation: str
    kind: str
    source_names: tuple[str, ...]
    explicit_radius_mm: float | None


@dataclass(frozen=True, slots=True)
class PreparedDrawingCosmeticCurve:
    target: PreparedDrawingDimensionTarget
    spec: DrawingCosmeticCurveSpec
    host_validation: dict[str, Any]
    inventory_before: dict[str, Any]


def _error(
    message: str,
    code: str,
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    drawing_dimension_error(message, code, repair=repair)


def _kind(operation: str) -> str:
    if operation not in _OPERATIONS:
        raise ValueError("operation is not a Drawing cosmetic-curve operation")
    return operation.removeprefix("create_")


def _source_targets(
    kind: str,
    values: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    result = []
    for role in _TARGET_ROLES[kind]:
        exact = exact_drawing_mapping(
            values[role],
            _TARGET_FIELDS,
            role.replace("_", " "),
            family="cosmetic curve",
            error_code="NATIVE_DRAWING_COSMETIC_CURVE_PARAMETERS_INVALID",
        )
        name = str(exact["subelement"] or "")
        if not name.startswith("Vertex"):
            _error(
                f"Cosmetic curve {role} must be an exact projected VertexN.",
                "NATIVE_DRAWING_COSMETIC_CURVE_REFERENCE_TYPE_INVALID",
                repair={"accepted_reference_types": ["projected vertex"]},
            )
        result.append(exact)
    names = [str(item["subelement"]) for item in result]
    if len(names) != len(set(names)):
        _error(
            "Cosmetic curve source vertices must be distinct.",
            "NATIVE_DRAWING_COSMETIC_CURVE_REFERENCES_INVALID",
        )
    return tuple(result)


def _radius(kind: str, values: Mapping[str, Any]) -> float | None:
    if kind != "one_point_circle":
        return None
    try:
        radius = float(values["radius_mm"])
    except (TypeError, ValueError) as exc:
        _error(
            "One-point cosmetic circle radius_mm must be numeric.",
            "NATIVE_DRAWING_COSMETIC_CURVE_PARAMETERS_INVALID",
        )
        raise AssertionError from exc
    if (
        not math.isfinite(radius)
        or radius <= 0.0
        or radius > MAX_DRAWING_COSMETIC_RADIUS_MM
    ):
        _error(
            "One-point cosmetic circle radius_mm must be greater than zero and "
            "no more than 1000000000 millimetres.",
            "NATIVE_DRAWING_COSMETIC_CURVE_PARAMETERS_INVALID",
        )
    return round(radius, 12)


def _validate_host(
    view: Any,
    spec: DrawingCosmeticCurveSpec,
    source_elements: tuple[Mapping[str, Any], ...],
) -> dict[str, Any]:
    try:
        import TechDrawGui

        validator = getattr(TechDrawGui, "validateDrawingCosmeticCurve", None)
        if not callable(validator):
            _error(
                "The installed TechDraw runtime cannot validate cosmetic curves.",
                "NATIVE_DRAWING_COSMETIC_CURVE_RUNTIME_UNAVAILABLE",
            )
        plan = normalize_cosmetic_curve_host_plan(
            validator(
                view,
                spec.kind,
                list(spec.source_names),
                spec.explicit_radius_mm or 0.0,
            ),
            created=False,
        )
    except NativeDrawingError:
        raise
    except Exception as exc:
        _error(
            "TechDraw rejected the exact cosmetic-curve construction: "
            f"{str(exc).strip()}",
            "NATIVE_DRAWING_COSMETIC_CURVE_REFERENCES_INVALID",
            repair={
                "construction": spec.kind,
                "required_roles_in_order": list(_TARGET_ROLES[spec.kind]),
                "requested_subelements": list(spec.source_names),
                "tool": "drawing.projected_geometry",
            },
        )
    if (
        plan["kind"] != spec.kind
        or plan["source_subelements"] != list(spec.source_names)
        or len(source_elements) != len(spec.source_names)
        or any(item["element_type"] != "vertex" for item in source_elements)
    ):
        _error(
            "TechDraw's cosmetic-curve plan does not match the exact projected sources.",
            "NATIVE_DRAWING_COSMETIC_CURVE_RUNTIME_UNAVAILABLE",
        )
    if spec.explicit_radius_mm is not None and not math.isclose(
        plan["geometry"]["radius_mm"],
        spec.explicit_radius_mm,
        rel_tol=1.0e-10,
        abs_tol=1.0e-8,
    ):
        _error(
            "TechDraw's one-point circle plan changed the explicit radius.",
            "NATIVE_DRAWING_COSMETIC_CURVE_RUNTIME_UNAVAILABLE",
        )
    return plan


def prepare_drawing_cosmetic_curve(
    document: Any,
    *,
    operation: str,
    values: Mapping[str, Any],
) -> PreparedDrawingCosmeticCurve:
    kind = _kind(operation)
    source_targets = _source_targets(kind, values)
    target = prepare_drawing_dimension_target(
        document,
        page_target=values["page"],
        view_target=values["view"],
        element_targets=source_targets,
        allowed_element_types=frozenset({"vertex"}),
        family="cosmetic curve",
        code_prefix="NATIVE_DRAWING_COSMETIC_CURVE",
    )
    spec = DrawingCosmeticCurveSpec(
        operation=operation,
        kind=kind,
        source_names=tuple(item["name"] for item in target.element_states_before),
        explicit_radius_mm=_radius(kind, values),
    )
    inventory = drawing_cosmetic_curve_inventory_state(target.view)
    if inventory["curve_count"] >= MAX_DRAWING_COSMETIC_CURVES:
        _error(
            "The Drawing cosmetic-curve inventory already contains 4096 targets.",
            "NATIVE_DRAWING_COSMETIC_CURVE_LIMIT_EXCEEDED",
        )
    return PreparedDrawingCosmeticCurve(
        target=target,
        spec=spec,
        host_validation=_validate_host(
            target.view,
            spec,
            target.element_states_before,
        ),
        inventory_before=inventory,
    )


def mutate_drawing_cosmetic_curve(
    _document: Any,
    *,
    prepared: PreparedDrawingCosmeticCurve,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedDrawingCosmeticCurve):
        raise TypeError("prepared must be PreparedDrawingCosmeticCurve")
    import TechDrawGui

    try:
        created = normalize_cosmetic_curve_host_plan(
            TechDrawGui.createDrawingCosmeticCurve(
                prepared.target.view,
                prepared.spec.kind,
                list(prepared.spec.source_names),
                prepared.spec.explicit_radius_mm or 0.0,
            ),
            created=True,
        )
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_COSMETIC_CURVE_CREATION_FAILED",
            "TechDraw could not create the exact cosmetic circle or arc: "
            f"{str(exc).strip()}",
        ) from exc
    if {key: value for key, value in created.items() if key != "curve_tag"} != (
        prepared.host_validation
    ):
        raise NativeMutationError(
            "NATIVE_DRAWING_COSMETIC_CURVE_CREATION_FAILED",
            "TechDraw created a cosmetic curve inconsistent with preflight.",
        )
    return NativeMutationDraft(
        value={"prepared": prepared, "created": created},
        recompute_targets=(prepared.target.view, prepared.target.page),
        changed=(object_identity(prepared.target.view),),
    )


def _postcondition_error(message: str) -> None:
    raise NativeMutationError(
        "NATIVE_DRAWING_COSMETIC_CURVE_POSTCONDITION_FAILED",
        message,
    )


def _view_boundary(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in state.items()
        if key not in {"state_sha256", "visible_edge_count", "hidden_edge_count"}
    }


def _persistent_curve_boundary(curve: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in curve.items()
        if key not in {"subelement", "curve_state_sha256"}
    }


def _require_old_curves_preserved(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> None:
    after_by_tag = {item["tag"]: item for item in after["curves"]}
    for old in before["curves"]:
        current = after_by_tag.get(old["tag"])
        if current is None or _persistent_curve_boundary(
            current
        ) != _persistent_curve_boundary(old):
            _postcondition_error(
                "Cosmetic-curve creation changed an existing persistent curve."
            )


def _verify_drawing_cosmetic_curve(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedDrawingCosmeticCurve = draft.value["prepared"]
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
            "Cosmetic-curve creation changed objects, page membership, or History."
        )
    if drawing_selection_state(document) != target.selection_before:
        _postcondition_error("Cosmetic-curve creation changed the human selection.")
    if drawing_visibility_state(document) != target.visibility_before:
        _postcondition_error("Cosmetic-curve creation changed object visibility.")
    if drawing_page_state(target.page) != target.page_state_before:
        _postcondition_error("Cosmetic-curve creation changed the Drawing page.")
    if _view_boundary(drawing_view_state(target.view)) != _view_boundary(
        target.view_state_before
    ):
        _postcondition_error(
            "Cosmetic-curve creation changed the Drawing view definition."
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
                "A projected source changed while its cosmetic curve was created."
            )

    inventory = drawing_cosmetic_curve_inventory_state(target.view)
    if inventory["curve_count"] != prepared.inventory_before["curve_count"] + 1:
        _postcondition_error(
            "Cosmetic-curve creation did not add exactly one persistent curve."
        )
    _require_old_curves_preserved(prepared.inventory_before, inventory)
    try:
        result = drawing_cosmetic_curve_result_state(
            target.view,
            draft.value["created"],
            target.element_states_before,
        )
    except Exception as exc:
        _postcondition_error(
            "The created cosmetic curve could not be resolved exactly: "
            f"{str(exc).strip()}"
        )
    return {
        "operation": prepared.spec.operation,
        "cosmetic_curve": result,
    }


def verify_drawing_cosmetic_curve(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    try:
        return _verify_drawing_cosmetic_curve(document, draft)
    except NativeMutationError:
        raise
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_COSMETIC_CURVE_POSTCONDITION_FAILED",
            "The cosmetic circle or arc could not be verified exactly: "
            f"{str(exc).strip()}",
        ) from exc
