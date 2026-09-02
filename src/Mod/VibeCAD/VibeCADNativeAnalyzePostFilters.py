# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact scalar FEM post filters built on the shared post-graph contract."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeLabels import assign_prepared_label
from VibeCADNativeAnalyzeHistory import (
    AnalyzeCreationBoundary,
    creation_boundary,
    publish_operation,
    require_boundary,
    verify_operation_block,
)
from VibeCADNativeAnalyzePost import (
    _copy_none_field_color,
    _label,
    _owning_post_pipeline,
    _post_parent_group,
)
from VibeCADNativeAnalyzeResultState import (
    PreparedResultTarget,
    prepare_result_target,
    result_reference_state,
    result_state,
)
from VibeCADNativeAnalyzeState import is_live
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


MAX_FIELD_REPAIR_ITEMS = 16


@dataclass(frozen=True, slots=True)
class PreparedPostScalarClip:
    boundary: AnalyzeCreationBoundary
    source: PreparedResultTarget
    parent_group: Any
    pipeline: Any
    label: str
    scalar_field: str
    threshold: float
    field_range: tuple[float, float]
    inside_out: bool
    source_was_visible: bool


@dataclass(frozen=True, slots=True)
class PreparedPostImplicitFilter:
    boundary: AnalyzeCreationBoundary
    source: PreparedResultTarget
    function: PreparedResultTarget
    parent_group: Any
    pipeline: Any
    kind: str
    label: str
    inside_out: bool | None
    cut_cells: bool | None
    source_was_visible: bool


@dataclass(frozen=True, slots=True)
class PreparedPostContours:
    boundary: AnalyzeCreationBoundary
    source: PreparedResultTarget
    parent_group: Any
    pipeline: Any
    label: str
    field: str
    component: str
    native_component: str
    count: int
    color_by_field: bool
    smoothing: bool
    relaxation: float
    source_was_visible: bool


_IMPLICIT_FUNCTION_TYPES = frozenset(
    {
        "Fem::FemPostPlaneFunction",
        "Fem::FemPostSphereFunction",
        "Fem::FemPostCylinderFunction",
        "Fem::FemPostBoxFunction",
    }
)


def _scalar_fields(source: Any) -> list[dict[str, Any]]:
    fields = []
    for field in result_state(source).get("fields", ()):
        if (
            field.get("association") != "point"
            or int(field.get("components", 0) or 0) != 1
        ):
            continue
        name = str(field.get("name", "") or "")
        raw_range = field.get("range")
        if (
            not name
            or not isinstance(raw_range, list)
            or len(raw_range) != 2
            or not all(type(value) in {int, float} for value in raw_range)
        ):
            continue
        lower, upper = (float(raw_range[0]), float(raw_range[1]))
        if not math.isfinite(lower) or not math.isfinite(upper):
            continue
        item = {"name": name, "range": [lower, upper]}
        unit = field.get("unit")
        if isinstance(unit, str) and unit:
            item["unit"] = unit
        fields.append(item)
    return fields


def _selected_scalar_field(source: Any, name: str) -> dict[str, Any]:
    available = _scalar_fields(source)
    selected = next((field for field in available if field["name"] == name), None)
    if selected is None:
        raise NativeAnalyzeError(
            f"scalar_field {name!r} is not an available scalar point field.",
            error_code="NATIVE_ANALYZE_FIELD_UNAVAILABLE",
            repair={
                "source": {"object_name": str(source.Name)},
                "available_scalar_fields": available[:MAX_FIELD_REPAIR_ITEMS],
                "available_scalar_fields_truncated": (
                    len(available) > MAX_FIELD_REPAIR_ITEMS
                ),
            },
        )
    return selected


def prepare_post_scalar_clip(
    document: Any,
    document_uid: str,
    *,
    source: Any,
    label: Any,
    scalar_field: Any,
    threshold: Any,
    inside_out: Any,
) -> PreparedPostScalarClip:
    source_target = prepare_result_target(
        document,
        document_uid,
        source,
        expected_kinds=frozenset({"pipeline", "branch_filter", "filter"}),
    )
    parent_group = _post_parent_group(
        document,
        source_target.result,
        source_target.kind,
    )
    pipeline = _owning_post_pipeline(document, parent_group)
    field_name = str(scalar_field or "").strip()
    field = _selected_scalar_field(source_target.result, field_name)
    if type(threshold) not in {int, float} or not math.isfinite(float(threshold)):
        raise NativeAnalyzeError("threshold must be one finite number.")
    normalized_threshold = float(threshold)
    field_range = (float(field["range"][0]), float(field["range"][1]))
    if not field_range[0] <= normalized_threshold <= field_range[1]:
        raise NativeAnalyzeError(
            f"threshold must be within the current {field_name!r} range.",
            error_code="NATIVE_ANALYZE_VALUE_OUT_OF_RANGE",
            repair={
                "source": {"object_name": str(source_target.result.Name)},
                "scalar_field": field_name,
                "allowed_range": list(field_range),
            },
        )
    if type(inside_out) is not bool:
        raise NativeAnalyzeError("inside_out must be true or false.")
    return PreparedPostScalarClip(
        creation_boundary(document),
        source_target,
        parent_group,
        pipeline,
        _label(label),
        field_name,
        normalized_threshold,
        field_range,
        inside_out,
        bool(source_target.result.ViewObject.Visibility),
    )


def create_post_scalar_clip(
    document: Any,
    prepared: PreparedPostScalarClip,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedPostScalarClip):
        raise TypeError("prepared must be a PreparedPostScalarClip")
    require_boundary(document, prepared.boundary)
    source = prepared.source.result
    parent_group = prepared.parent_group
    pipeline = prepared.pipeline
    current_field = _selected_scalar_field(source, prepared.scalar_field)
    current_range = tuple(float(value) for value in current_field["range"])
    if (
        not is_live(document, source)
        or not is_live(document, parent_group)
        or not is_live(document, pipeline)
        or _post_parent_group(document, source, prepared.source.kind) is not parent_group
        or _owning_post_pipeline(document, parent_group) is not pipeline
        or current_range != prepared.field_range
    ):
        raise NativeAnalyzeError(
            "The exact post-processing source or scalar field changed after clip preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    try:
        clip = document.addObject(
            "Fem::FemPostScalarClipFilter",
            document.getUniqueObjectName("ScalarClip"),
        )
        prepared = assign_prepared_label(clip, prepared)
        parent_group.addObject(clip)
        clip.ViewObject.DisplayMode = "Surface"
        clip.ViewObject.SelectionStyle = "BoundBox"
        _copy_none_field_color(source, clip)
        replaced = (source,) if prepared.source_was_visible else ()
        publish_operation(
            document,
            prepared.boundary,
            clip,
            replaced_inputs=replaced,
        )
        if document.recompute() is False:
            raise NativeAnalyzeError(
                "The FEM scalar clip could not populate its input-field choices."
            )
        available = tuple(clip.getEnumerationsOfProperty("Scalars") or ())
        if prepared.scalar_field not in available:
            raise NativeAnalyzeError(
                "The exact scalar field was no longer available after the clip joined "
                "its pipeline.",
                error_code="NATIVE_ANALYZE_STATE_STALE",
                repair={
                    "source": {"object_name": str(source.Name)},
                    "available_scalar_fields": list(available[:MAX_FIELD_REPAIR_ITEMS]),
                    "available_scalar_fields_truncated": (
                        len(available) > MAX_FIELD_REPAIR_ITEMS
                    ),
                },
            )
        clip.Scalars = prepared.scalar_field
        clip.Value = prepared.threshold
        clip.InsideOut = prepared.inside_out
        source.ViewObject.Visibility = False
        clip.ViewObject.Visibility = True
    except NativeAnalyzeError:
        raise
    except Exception as exc:
        raise NativeAnalyzeError(
            f"The FEM scalar clip could not be created: {exc}",
            error_code="NATIVE_ANALYZE_FACTORY_FAILED",
        ) from exc
    changed_objects = tuple(dict.fromkeys((parent_group, pipeline)))
    return NativeMutationDraft(
        value={"prepared": prepared, "clip": clip, "replaced": replaced},
        recompute_targets=(clip, parent_group, pipeline),
        created=(object_identity(clip),),
        changed=tuple(object_identity(obj) for obj in changed_objects),
    )


def verify_post_scalar_clip(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    clip = draft.value["clip"]
    replaced = draft.value["replaced"]
    source = prepared.source.result
    parent_group = prepared.parent_group
    pipeline = prepared.pipeline
    verify_operation_block(
        document,
        prepared.boundary,
        clip,
        replaced_inputs=replaced,
    )
    state = result_state(clip)
    checks = {
        "live scalar clip": is_live(document, clip),
        "parent group membership": clip in tuple(parent_group.Group or ()),
        "owning pipeline": _owning_post_pipeline(document, clip) is pipeline,
        "source retained": is_live(document, source),
        "label": str(clip.Label) == prepared.label,
        "scalar field": str(clip.Scalars) == prepared.scalar_field,
        "threshold": math.isclose(
            float(clip.Value), prepared.threshold, rel_tol=0.0, abs_tol=1e-12
        ),
        "direction": bool(clip.InsideOut) is prepared.inside_out,
        "result data": bool(state["data_available"]),
        "clip visible": bool(clip.ViewObject.Visibility),
        "source hidden": not bool(source.ViewObject.Visibility),
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise NativeAnalyzeError(
            "The FEM scalar clip failed its exact postcondition: "
            + ", ".join(failures)
            + "."
        )
    return {
        "created_scalar_clip": state,
        "source": result_reference_state(source),
        "parent_group": result_reference_state(parent_group),
        "pipeline": result_reference_state(pipeline),
        "selected_field": {
            "name": prepared.scalar_field,
            "source_range": list(prepared.field_range),
            "unit": _selected_scalar_field(source, prepared.scalar_field).get("unit"),
            "threshold": prepared.threshold,
        },
        "presentation": {
            "visible_object": str(clip.Name),
            "hidden_source": str(source.Name),
        },
    }


def prepare_post_implicit_filter(
    document: Any,
    document_uid: str,
    *,
    kind: str,
    source: Any,
    function: Any,
    label: Any,
    inside_out: Any | None = None,
    cut_cells: Any | None = None,
) -> PreparedPostImplicitFilter:
    if kind not in {"cut", "region_clip"}:
        raise AssertionError(f"Unhandled implicit post-filter kind: {kind}")
    source_target = prepare_result_target(
        document,
        document_uid,
        source,
        expected_kinds=frozenset({"pipeline", "branch_filter", "filter"}),
    )
    function_target = prepare_result_target(
        document,
        document_uid,
        function,
        expected_kinds=frozenset({"function"}),
    )
    if str(function_target.result.TypeId) not in _IMPLICIT_FUNCTION_TYPES:
        raise NativeAnalyzeError(
            "The exact function is not a supported plane, sphere, cylinder, or box.",
            error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
        )
    parent_group = _post_parent_group(
        document,
        source_target.result,
        source_target.kind,
    )
    pipeline = _owning_post_pipeline(document, parent_group)
    function_state = result_state(function_target.result, include_ranges=False)
    if function_state["post_pipeline_owners"] != [str(pipeline.Name)]:
        raise NativeAnalyzeError(
            "The exact implicit function does not belong to the source pipeline.",
            error_code="NATIVE_ANALYZE_TARGET_RELATION_INVALID",
            repair={
                "source_pipeline": str(pipeline.Name),
                "function_pipeline_owners": function_state[
                    "post_pipeline_owners"
                ],
            },
        )
    if kind == "region_clip":
        if type(inside_out) is not bool or type(cut_cells) is not bool:
            raise NativeAnalyzeError(
                "inside_out and cut_cells must each be true or false."
            )
        normalized_inside_out = inside_out
        normalized_cut_cells = cut_cells
    else:
        if inside_out is not None or cut_cells is not None:
            raise NativeAnalyzeError(
                "A cut filter accepts no inside_out or cut_cells setting."
            )
        normalized_inside_out = None
        normalized_cut_cells = None
    return PreparedPostImplicitFilter(
        creation_boundary(document),
        source_target,
        function_target,
        parent_group,
        pipeline,
        kind,
        _label(label),
        normalized_inside_out,
        normalized_cut_cells,
        bool(source_target.result.ViewObject.Visibility),
    )


def create_post_implicit_filter(
    document: Any,
    prepared: PreparedPostImplicitFilter,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedPostImplicitFilter):
        raise TypeError("prepared must be a PreparedPostImplicitFilter")
    require_boundary(document, prepared.boundary)
    source = prepared.source.result
    function = prepared.function.result
    parent_group = prepared.parent_group
    pipeline = prepared.pipeline
    function_state = result_state(function, include_ranges=False)
    if (
        not is_live(document, source)
        or not is_live(document, function)
        or not is_live(document, parent_group)
        or not is_live(document, pipeline)
        or _post_parent_group(document, source, prepared.source.kind) is not parent_group
        or _owning_post_pipeline(document, parent_group) is not pipeline
        or function_state["state_sha256"] != prepared.function.expected_state_sha256
        or function_state["post_pipeline_owners"] != [str(pipeline.Name)]
    ):
        raise NativeAnalyzeError(
            "The exact source pipeline or implicit function changed after preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    type_id = (
        "Fem::FemPostCutFilter"
        if prepared.kind == "cut"
        else "Fem::FemPostClipFilter"
    )
    name = "Cut" if prepared.kind == "cut" else "Clip"
    try:
        post_filter = document.addObject(
            type_id,
            document.getUniqueObjectName(name),
        )
        prepared = assign_prepared_label(post_filter, prepared)
        parent_group.addObject(post_filter)
        post_filter.Function = function
        if prepared.kind == "region_clip":
            post_filter.InsideOut = prepared.inside_out
            post_filter.CutCells = prepared.cut_cells
        post_filter.ViewObject.DisplayMode = "Surface"
        post_filter.ViewObject.SelectionStyle = "BoundBox"
        _copy_none_field_color(source, post_filter)
        replaced = (source,) if prepared.source_was_visible else ()
        publish_operation(
            document,
            prepared.boundary,
            post_filter,
            replaced_inputs=replaced,
        )
        source.ViewObject.Visibility = False
        post_filter.ViewObject.Visibility = True
    except NativeAnalyzeError:
        raise
    except Exception as exc:
        label = "cut" if prepared.kind == "cut" else "region clip"
        raise NativeAnalyzeError(
            f"The FEM {label} filter could not be created: {exc}",
            error_code="NATIVE_ANALYZE_FACTORY_FAILED",
        ) from exc
    changed_objects = tuple(dict.fromkeys((parent_group, pipeline)))
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "filter": post_filter,
            "replaced": replaced,
        },
        recompute_targets=(post_filter, parent_group, pipeline),
        created=(object_identity(post_filter),),
        changed=tuple(object_identity(obj) for obj in changed_objects),
    )


def verify_post_implicit_filter(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    post_filter = draft.value["filter"]
    replaced = draft.value["replaced"]
    source = prepared.source.result
    function = prepared.function.result
    parent_group = prepared.parent_group
    pipeline = prepared.pipeline
    verify_operation_block(
        document,
        prepared.boundary,
        post_filter,
        replaced_inputs=replaced,
    )
    state = result_state(post_filter)
    expected_type = (
        "Fem::FemPostCutFilter"
        if prepared.kind == "cut"
        else "Fem::FemPostClipFilter"
    )
    checks = {
        "live filter": is_live(document, post_filter),
        "filter type": str(post_filter.TypeId) == expected_type,
        "parent group membership": post_filter in tuple(parent_group.Group or ()),
        "owning pipeline": _owning_post_pipeline(document, post_filter) is pipeline,
        "source retained": is_live(document, source),
        "function retained": is_live(document, function),
        "function link": post_filter.Function is function,
        "label": str(post_filter.Label) == prepared.label,
        "result data": bool(state["data_available"]),
        "filter visible": bool(post_filter.ViewObject.Visibility),
        "source hidden": not bool(source.ViewObject.Visibility),
    }
    if prepared.kind == "region_clip":
        checks["clip direction"] = bool(post_filter.InsideOut) is prepared.inside_out
        checks["cell handling"] = bool(post_filter.CutCells) is prepared.cut_cells
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        label = "cut" if prepared.kind == "cut" else "region clip"
        raise NativeAnalyzeError(
            f"The FEM {label} filter failed its exact postcondition: "
            + ", ".join(failures)
            + "."
        )
    result_key = "created_cut" if prepared.kind == "cut" else "created_region_clip"
    return {
        result_key: state,
        "source": result_reference_state(source),
        "function": result_reference_state(function),
        "parent_group": result_reference_state(parent_group),
        "pipeline": result_reference_state(pipeline),
        "presentation": {
            "visible_object": str(post_filter.Name),
            "hidden_source": str(source.Name),
        },
    }


def _contour_fields(source: Any) -> list[dict[str, Any]]:
    result = []
    for field in result_state(source).get("fields", ()):
        if field.get("association") != "point":
            continue
        components = int(field.get("components", 0) or 0)
        raw_range = field.get("range")
        if components < 1 or components > 3 or not isinstance(raw_range, list):
            continue
        if len(raw_range) != 2 or not all(
            type(value) in {int, float} and math.isfinite(float(value))
            for value in raw_range
        ):
            continue
        item = {
            "name": str(field.get("name", "") or ""),
            "components": components,
            "range": [float(raw_range[0]), float(raw_range[1])],
        }
        if field.get("unit"):
            item["unit"] = str(field["unit"])
        if item["name"]:
            result.append(item)
    return result


def prepare_post_contours(
    document: Any,
    document_uid: str,
    *,
    source: Any,
    label: Any,
    field: Any,
    component: Any,
    count: Any,
    color_by_field: Any,
    smoothing: Any,
    relaxation: Any,
) -> PreparedPostContours:
    source_target = prepare_result_target(
        document,
        document_uid,
        source,
        expected_kinds=frozenset({"pipeline", "branch_filter", "filter"}),
    )
    parent_group = _post_parent_group(
        document,
        source_target.result,
        source_target.kind,
    )
    pipeline = _owning_post_pipeline(document, parent_group)
    field_name = str(field or "").strip()
    available = _contour_fields(source_target.result)
    selected = next((item for item in available if item["name"] == field_name), None)
    if selected is None:
        raise NativeAnalyzeError(
            f"field {field_name!r} is not an available contour point field.",
            error_code="NATIVE_ANALYZE_FIELD_UNAVAILABLE",
            repair={
                "source": {"object_name": str(source_target.result.Name)},
                "available_contour_fields": available[:MAX_FIELD_REPAIR_ITEMS],
                "available_contour_fields_truncated": (
                    len(available) > MAX_FIELD_REPAIR_ITEMS
                ),
            },
        )
    if selected["range"][0] == selected["range"][1]:
        raise NativeAnalyzeError(
            f"field {field_name!r} has no varying range from which to form contours."
        )
    normalized_component = str(component or "").strip().lower()
    if selected["components"] == 1:
        allowed_components = {"scalar": "Not a vector"}
    else:
        allowed_components = {"magnitude": "Magnitude", "x": "X", "y": "Y"}
        if selected["components"] >= 3:
            allowed_components["z"] = "Z"
    if normalized_component not in allowed_components:
        raise NativeAnalyzeError(
            f"component {normalized_component!r} is invalid for {field_name!r}.",
            error_code="NATIVE_ANALYZE_FIELD_COMPONENT_INVALID",
            repair={
                "field": field_name,
                "components": selected["components"],
                "allowed_components": list(allowed_components),
            },
        )
    if type(count) is not int or not 1 <= count <= 1000:
        raise NativeAnalyzeError("count must be an integer from 1 through 1000.")
    if type(color_by_field) is not bool or type(smoothing) is not bool:
        raise NativeAnalyzeError(
            "color_by_field and smoothing must each be true or false."
        )
    if type(relaxation) not in {int, float} or not math.isfinite(float(relaxation)):
        raise NativeAnalyzeError("relaxation must be one finite number.")
    normalized_relaxation = float(relaxation)
    if not 0.0 <= normalized_relaxation <= 1.0:
        raise NativeAnalyzeError("relaxation must be between 0 and 1.")
    return PreparedPostContours(
        creation_boundary(document),
        source_target,
        parent_group,
        pipeline,
        _label(label),
        field_name,
        normalized_component,
        allowed_components[normalized_component],
        count,
        color_by_field,
        smoothing,
        normalized_relaxation,
        bool(source_target.result.ViewObject.Visibility),
    )


def create_post_contours(
    document: Any,
    prepared: PreparedPostContours,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedPostContours):
        raise TypeError("prepared must be a PreparedPostContours")
    require_boundary(document, prepared.boundary)
    source = prepared.source.result
    parent_group = prepared.parent_group
    pipeline = prepared.pipeline
    current_fields = _contour_fields(source)
    if (
        not is_live(document, source)
        or not is_live(document, parent_group)
        or not is_live(document, pipeline)
        or _post_parent_group(document, source, prepared.source.kind) is not parent_group
        or _owning_post_pipeline(document, parent_group) is not pipeline
        or prepared.field not in {item["name"] for item in current_fields}
    ):
        raise NativeAnalyzeError(
            "The exact post-processing source or contour field changed after preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    try:
        contours = document.addObject(
            "Fem::FemPostContoursFilter",
            document.getUniqueObjectName("Contours"),
        )
        prepared = assign_prepared_label(contours, prepared)
        parent_group.addObject(contours)
        contours.ViewObject.DisplayMode = "Surface"
        contours.ViewObject.SelectionStyle = "BoundBox"
        _copy_none_field_color(source, contours)
        replaced = (source,) if prepared.source_was_visible else ()
        publish_operation(
            document,
            prepared.boundary,
            contours,
            replaced_inputs=replaced,
        )
        if document.recompute() is False:
            raise NativeAnalyzeError(
                "The FEM contours filter could not populate its field choices."
            )
        available_fields = tuple(contours.getEnumerationsOfProperty("Field") or ())
        if prepared.field not in available_fields:
            raise NativeAnalyzeError(
                "The exact contour field was no longer available after the filter "
                "joined its pipeline.",
                error_code="NATIVE_ANALYZE_STATE_STALE",
                repair={"available_fields": list(available_fields[:16])},
            )
        contours.Field = prepared.field
        available_components = tuple(
            contours.getEnumerationsOfProperty("VectorMode") or ()
        )
        if prepared.native_component not in available_components:
            raise NativeAnalyzeError(
                "The requested contour component was no longer available after field "
                "selection.",
                error_code="NATIVE_ANALYZE_STATE_STALE",
                repair={"available_components": list(available_components)},
            )
        contours.VectorMode = prepared.native_component
        contours.NumberOfContours = prepared.count
        contours.NoColor = not prepared.color_by_field
        contours.EnableSmoothing = prepared.smoothing
        contours.RelaxationFactor = prepared.relaxation
        if prepared.color_by_field:
            contours.ViewObject.Field = prepared.field
            component_index = available_components.index(prepared.native_component)
            contours.ViewObject.Component = component_index
        else:
            contours.ViewObject.Field = "None"
        source.ViewObject.Visibility = False
        contours.ViewObject.Visibility = True
    except NativeAnalyzeError:
        raise
    except Exception as exc:
        raise NativeAnalyzeError(
            f"The FEM contours filter could not be created: {exc}",
            error_code="NATIVE_ANALYZE_FACTORY_FAILED",
        ) from exc
    changed_objects = tuple(dict.fromkeys((parent_group, pipeline)))
    return NativeMutationDraft(
        value={"prepared": prepared, "contours": contours, "replaced": replaced},
        recompute_targets=(contours, parent_group, pipeline),
        created=(object_identity(contours),),
        changed=tuple(object_identity(obj) for obj in changed_objects),
    )


def verify_post_contours(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    contours = draft.value["contours"]
    replaced = draft.value["replaced"]
    source = prepared.source.result
    parent_group = prepared.parent_group
    pipeline = prepared.pipeline
    verify_operation_block(
        document,
        prepared.boundary,
        contours,
        replaced_inputs=replaced,
    )
    state = result_state(contours)
    expected_view_field = prepared.field if prepared.color_by_field else "None"
    checks = {
        "live contours": is_live(document, contours),
        "parent group membership": contours in tuple(parent_group.Group or ()),
        "owning pipeline": _owning_post_pipeline(document, contours) is pipeline,
        "source retained": is_live(document, source),
        "label": str(contours.Label) == prepared.label,
        "field": str(contours.Field) == prepared.field,
        "component": str(contours.VectorMode) == prepared.native_component,
        "count": int(contours.NumberOfContours) == prepared.count,
        "color mode": bool(contours.NoColor) is (not prepared.color_by_field),
        "smoothing": bool(contours.EnableSmoothing) is prepared.smoothing,
        "relaxation": math.isclose(
            float(contours.RelaxationFactor),
            prepared.relaxation,
            rel_tol=0.0,
            abs_tol=1e-12,
        ),
        "view field": str(contours.ViewObject.Field) == expected_view_field,
        "result data": bool(state["data_available"]),
        "contours visible": bool(contours.ViewObject.Visibility),
        "source hidden": not bool(source.ViewObject.Visibility),
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise NativeAnalyzeError(
            "The FEM contours filter failed its exact postcondition: "
            + ", ".join(failures)
            + "."
        )
    return {
        "created_contours": state,
        "source": result_reference_state(source),
        "parent_group": result_reference_state(parent_group),
        "pipeline": result_reference_state(pipeline),
        "selected_field": {
            "name": prepared.field,
            "component": prepared.component,
        },
        "presentation": {
            "visible_object": str(contours.Name),
            "hidden_source": str(source.Name),
            "color_by_field": prepared.color_by_field,
        },
    }
