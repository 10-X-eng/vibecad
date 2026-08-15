# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact FEM vector-glyph creation without task-panel automation."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
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
from VibeCADNativeAnalyzePostSampling import post_point_fields
from VibeCADNativeAnalyzeResultState import (
    PreparedResultTarget,
    prepare_result_target,
    result_reference_state,
    result_state,
)
from VibeCADNativeAnalyzeState import is_live
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


MAX_GLYPH_LOCATIONS = 25_000
MAX_REPAIR_FIELDS = 16

_GLYPHS = {
    "arrow": "Arrow",
    "cone": "Cone",
    "cube": "Cube",
    "cylinder": "Cylinder",
    "line": "Line",
    "sphere": "Sphere",
}


@dataclass(frozen=True, slots=True)
class PreparedPostGlyph:
    boundary: AnalyzeCreationBoundary
    source: PreparedResultTarget
    parent_group: Any
    pipeline: Any
    label: str
    glyph: str
    orientation_field: str | None
    scale_field: str | None
    scale_mode: str
    native_vector_scale_mode: str
    scale_factor: float
    sampling_mode: str
    native_mask_mode: str
    stride: int
    maximum_points: int
    expected_location_count: int
    source_point_count: int
    referenced_fields: tuple[tuple[str, int], ...]
    source_was_visible: bool


def _typed_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise NativeAnalyzeError(f"{name} must be one typed object.")
    return dict(value)


def _point_fields(source: Any) -> list[dict[str, Any]]:
    return [
        field
        for field in post_point_fields(source)
        if int(field["components"]) in {1, 3}
    ]


def _field_repair(fields: list[dict[str, Any]]) -> dict[str, Any]:
    values = [
        {"name": field["name"], "components": int(field["components"])}
        for field in fields[:MAX_REPAIR_FIELDS]
    ]
    return {
        "available_point_fields": values,
        "available_point_fields_truncated": len(fields) > MAX_REPAIR_FIELDS,
    }


def _select_field(
    fields: list[dict[str, Any]],
    value: Any,
    *,
    components: int,
    parameter: str,
) -> str:
    name = str(value or "").strip()
    matches = [
        field
        for field in fields
        if field["name"] == name and int(field["components"]) == components
    ]
    if len(matches) != 1:
        raise NativeAnalyzeError(
            f"{parameter} {name!r} must identify one {components}-component point field.",
            error_code="NATIVE_ANALYZE_FIELD_UNAVAILABLE",
            repair={
                "parameter": parameter,
                "required_components": components,
                **_field_repair(fields),
            },
        )
    return name


def _orientation(
    value: Any, fields: list[dict[str, Any]]
) -> tuple[str | None, tuple[tuple[str, int], ...]]:
    setting = _typed_mapping(value, "orientation")
    mode = str(setting.get("mode") or "")
    if mode == "none" and set(setting) == {"mode"}:
        return None, ()
    if mode == "vector_field" and set(setting) == {"mode", "field"}:
        field = _select_field(
            fields,
            setting["field"],
            components=3,
            parameter="orientation.field",
        )
        return field, ((field, 3),)
    raise NativeAnalyzeError(
        "orientation must be {'mode':'none'} or "
        "{'mode':'vector_field','field':<three-component field>}."
    )


def _positive_factor(value: Any) -> float:
    if type(value) not in {int, float}:
        raise NativeAnalyzeError("scaling.factor must be one finite positive number.")
    factor = float(value)
    if not math.isfinite(factor) or not 0.0 < factor <= 1.0e12:
        raise NativeAnalyzeError(
            "scaling.factor must be greater than zero and no greater than 1e12."
        )
    return factor


def _scaling(
    value: Any, fields: list[dict[str, Any]]
) -> tuple[str | None, str, str, float, tuple[tuple[str, int], ...]]:
    setting = _typed_mapping(value, "scaling")
    mode = str(setting.get("mode") or "")
    if mode == "none" and set(setting) == {"mode"}:
        return None, mode, "Not a vector", 1.0, ()
    if set(setting) != {"mode", "field", "factor"}:
        raise NativeAnalyzeError(
            "Field scaling must contain only mode, field, and factor."
        )
    definitions = {
        "scalar_field": (1, "Not a vector"),
        "vector_magnitude": (3, "Scale by magnitude"),
        "vector_components": (3, "Scale by components"),
    }
    if mode not in definitions:
        raise NativeAnalyzeError(
            "scaling.mode must be none, scalar_field, vector_magnitude, or "
            "vector_components."
        )
    components, native_mode = definitions[mode]
    field = _select_field(
        fields,
        setting["field"],
        components=components,
        parameter="scaling.field",
    )
    return (
        field,
        mode,
        native_mode,
        _positive_factor(setting["factor"]),
        ((field, components),),
    )


def _sampling(value: Any, source_point_count: int) -> tuple[str, str, int, int, int]:
    setting = _typed_mapping(value, "sampling")
    mode = str(setting.get("mode") or "")
    if mode == "all" and set(setting) == {"mode"}:
        native_mode = "Use All"
        stride = 1
        maximum = source_point_count
        expected = source_point_count
    elif mode == "every_nth" and set(setting) == {"mode", "stride"}:
        raw_stride = setting["stride"]
        if type(raw_stride) is not int or not 1 <= raw_stride <= 999_999_999:
            raise NativeAnalyzeError(
                "sampling.stride must be an integer from 1 through 999999999."
            )
        native_mode = "Every Nth"
        stride = raw_stride
        maximum = source_point_count
        expected = (source_point_count + stride - 1) // stride
    elif mode == "uniform" and set(setting) == {"mode", "maximum_points"}:
        raw_maximum = setting["maximum_points"]
        if type(raw_maximum) is not int or not 1 <= raw_maximum <= MAX_GLYPH_LOCATIONS:
            raise NativeAnalyzeError(
                f"sampling.maximum_points must be an integer from 1 through "
                f"{MAX_GLYPH_LOCATIONS}."
            )
        native_mode = "Uniform Sampling"
        stride = 1
        maximum = raw_maximum
        expected = min(source_point_count, maximum)
    else:
        raise NativeAnalyzeError(
            "sampling must be {'mode':'all'}, "
            "{'mode':'every_nth','stride':<integer>}, or "
            "{'mode':'uniform','maximum_points':<integer>}."
        )
    if expected > MAX_GLYPH_LOCATIONS:
        raise NativeAnalyzeError(
            "The requested glyph sampling would create too many glyph locations.",
            error_code="NATIVE_ANALYZE_GLYPH_LIMIT_EXCEEDED",
            repair={
                "source_point_count": source_point_count,
                "requested_location_count": expected,
                "maximum_location_count": MAX_GLYPH_LOCATIONS,
                "recommended_sampling": {
                    "mode": "uniform",
                    "maximum_points": MAX_GLYPH_LOCATIONS,
                },
            },
        )
    return mode, native_mode, stride, maximum, expected


def prepare_post_glyph(
    document: Any,
    document_uid: str,
    *,
    source: Any,
    label: Any,
    glyph: Any,
    orientation: Any,
    scaling: Any,
    sampling: Any,
) -> PreparedPostGlyph:
    source_target = prepare_result_target(
        document,
        document_uid,
        source,
        expected_kinds=frozenset({"pipeline", "branch_filter", "filter"}),
    )
    parent_group = _post_parent_group(document, source_target.result, source_target.kind)
    pipeline = _owning_post_pipeline(document, parent_group)
    native_glyph = _GLYPHS.get(str(glyph or "").strip().lower())
    if native_glyph is None:
        raise NativeAnalyzeError(
            "glyph must be arrow, cone, cube, cylinder, line, or sphere."
        )
    fields = _point_fields(source_target.result)
    orientation_field, orientation_references = _orientation(orientation, fields)
    (
        scale_field,
        scale_mode,
        vector_scale_mode,
        scale_factor,
        scale_references,
    ) = _scaling(scaling, fields)
    source_state = result_state(source_target.result, include_ranges=False)
    source_point_count = int(source_state["point_count"])
    if not bool(source_state["data_available"]) or source_point_count < 1:
        raise NativeAnalyzeError("The exact post source has no points for glyph placement.")
    sampling_mode, native_mask_mode, stride, maximum, expected = _sampling(
        sampling, source_point_count
    )
    references = tuple(dict.fromkeys((*orientation_references, *scale_references)))
    return PreparedPostGlyph(
        creation_boundary(document),
        source_target,
        parent_group,
        pipeline,
        _label(label),
        native_glyph,
        orientation_field,
        scale_field,
        scale_mode,
        vector_scale_mode,
        scale_factor,
        sampling_mode,
        native_mask_mode,
        stride,
        maximum,
        expected,
        source_point_count,
        references,
        bool(source_target.result.ViewObject.Visibility),
    )


def _require_current_source(document: Any, prepared: PreparedPostGlyph) -> None:
    source = prepared.source.result
    source_state = result_state(source, include_ranges=False)
    fields = {field["name"]: int(field["components"]) for field in _point_fields(source)}
    if (
        not is_live(document, source)
        or not is_live(document, prepared.parent_group)
        or not is_live(document, prepared.pipeline)
        or _post_parent_group(document, source, prepared.source.kind)
        is not prepared.parent_group
        or _owning_post_pipeline(document, prepared.parent_group) is not prepared.pipeline
        or source_state["state_sha256"] != prepared.source.expected_state_sha256
        or int(source_state["point_count"]) != prepared.source_point_count
        or any(fields.get(name) != components for name, components in prepared.referenced_fields)
    ):
        raise NativeAnalyzeError(
            "The exact post source or selected glyph field changed after preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )


def create_post_glyph(
    document: Any, prepared: PreparedPostGlyph
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedPostGlyph):
        raise TypeError("prepared must be a PreparedPostGlyph")
    require_boundary(document, prepared.boundary)
    _require_current_source(document, prepared)
    source = prepared.source.result
    try:
        import ObjectsFem

        glyph_filter = ObjectsFem.makePostFilterGlyph(
            document,
            prepared.parent_group,
            document.getUniqueObjectName("Glyph"),
        )
        glyph_filter.Label = prepared.label
        glyph_filter.ViewObject.DisplayMode = "Surface"
        glyph_filter.ViewObject.SelectionStyle = "BoundBox"
        _copy_none_field_color(source, glyph_filter)
        replaced = (source,) if prepared.source_was_visible else ()
        publish_operation(
            document,
            prepared.boundary,
            glyph_filter,
            replaced_inputs=replaced,
        )
        source.ViewObject.Visibility = False
        glyph_filter.ViewObject.Visibility = True
        if document.recompute() is False:
            raise NativeAnalyzeError(
                "The FEM glyph filter could not discover its input fields."
            )
        glyph_filter.Glyph = prepared.glyph
        glyph_filter.MaskMode = prepared.native_mask_mode
        glyph_filter.Stride = prepared.stride
        glyph_filter.MaxNumber = prepared.maximum_points
        glyph_filter.ScaleFactor = prepared.scale_factor
        glyph_filter.OrientationData = prepared.orientation_field or "None"
        glyph_filter.ScaleData = prepared.scale_field or "None"
        glyph_filter.VectorScaleMode = prepared.native_vector_scale_mode
    except NativeAnalyzeError:
        raise
    except Exception as exc:
        raise NativeAnalyzeError(
            f"The FEM glyph filter could not be created: {exc}",
            error_code="NATIVE_ANALYZE_FACTORY_FAILED",
        ) from exc
    changed = tuple(dict.fromkeys((prepared.parent_group, prepared.pipeline)))
    return NativeMutationDraft(
        value={"prepared": prepared, "glyph_filter": glyph_filter, "replaced": replaced},
        recompute_targets=(glyph_filter, prepared.parent_group, prepared.pipeline),
        created=(object_identity(glyph_filter),),
        changed=tuple(object_identity(obj) for obj in changed),
    )


def verify_post_glyph(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    glyph_filter = draft.value["glyph_filter"]
    replaced = draft.value["replaced"]
    source = prepared.source.result
    verify_operation_block(
        document,
        prepared.boundary,
        glyph_filter,
        replaced_inputs=replaced,
    )
    state = result_state(glyph_filter)
    output_point_count = int(state["point_count"])
    output_cell_count = int(state["cell_count"])
    proxy_type = str(getattr(glyph_filter.Proxy, "Type", "") or "")
    checks = {
        "live glyph": is_live(document, glyph_filter),
        "glyph type": str(glyph_filter.TypeId) == "Fem::PostFilterPython",
        "glyph proxy": proxy_type == "Fem::PostFilterPython",
        "parent group membership": glyph_filter in tuple(prepared.parent_group.Group or ()),
        "owning pipeline": _owning_post_pipeline(document, glyph_filter)
        is prepared.pipeline,
        "source retained": is_live(document, source),
        "label": str(glyph_filter.Label) == prepared.label,
        "glyph": str(glyph_filter.Glyph) == prepared.glyph,
        "orientation": str(glyph_filter.OrientationData)
        == (prepared.orientation_field or "None"),
        "scale field": str(glyph_filter.ScaleData) == (prepared.scale_field or "None"),
        "vector scale mode": str(glyph_filter.VectorScaleMode)
        == prepared.native_vector_scale_mode,
        "scale factor": math.isclose(
            float(glyph_filter.ScaleFactor),
            prepared.scale_factor,
            rel_tol=0.0,
            abs_tol=1.0e-12,
        ),
        "mask mode": str(glyph_filter.MaskMode) == prepared.native_mask_mode,
        "stride": int(glyph_filter.Stride) == prepared.stride,
        "maximum points": int(glyph_filter.MaxNumber) == prepared.maximum_points,
        "output data": bool(state["data_available"])
        and output_point_count > 0
        and output_cell_count > 0,
        "glyph visible": bool(glyph_filter.ViewObject.Visibility),
        "source hidden": not bool(source.ViewObject.Visibility),
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise NativeAnalyzeError(
            "The FEM glyph filter failed its exact postcondition: "
            + ", ".join(failures)
            + "."
        )
    orientation = (
        {"mode": "none"}
        if prepared.orientation_field is None
        else {"mode": "vector_field", "field": prepared.orientation_field}
    )
    if prepared.scale_field is None:
        scaling = {"mode": "none"}
    else:
        scaling = {
            "mode": prepared.scale_mode,
            "field": prepared.scale_field,
            "factor": prepared.scale_factor,
        }
    if prepared.sampling_mode == "all":
        sampling = {"mode": "all"}
    elif prepared.sampling_mode == "every_nth":
        sampling = {"mode": "every_nth", "stride": prepared.stride}
    else:
        sampling = {"mode": "uniform", "maximum_points": prepared.maximum_points}
    return {
        "created_glyph": state,
        "glyph": {
            "shape": prepared.glyph.lower(),
            "orientation": orientation,
            "scaling": scaling,
            "sampling": sampling,
            "source_point_count": prepared.source_point_count,
            "maximum_glyph_locations": prepared.expected_location_count,
            "output_point_count": output_point_count,
            "output_cell_count": output_cell_count,
        },
        "source": result_reference_state(source),
        "pipeline": result_reference_state(prepared.pipeline),
        "presentation": {
            "visible_object": str(glyph_filter.Name),
            "hidden_source": str(source.Name),
        },
    }
