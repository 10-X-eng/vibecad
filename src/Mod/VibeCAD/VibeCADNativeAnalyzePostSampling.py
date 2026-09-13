# SPDX-License-Identifier: LGPL-2.1-or-later

"""Source-preserving FEM probes and compact stress-linearization reads."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

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


MAX_COORDINATE_MM = 1_000_000_000.0
MAX_REPAIR_FIELDS = 16

_COMPONENTS = {
    1: {"scalar": (0, "Scalar")},
    2: {"magnitude": (0, "Magnitude"), "x": (1, "X"), "y": (2, "Y")},
    3: {
        "magnitude": (0, "Magnitude"),
        "x": (1, "X"),
        "y": (2, "Y"),
        "z": (3, "Z"),
    },
    6: {
        "magnitude": (0, "Magnitude"),
        "xx": (1, "XX"),
        "yy": (2, "YY"),
        "zz": (3, "ZZ"),
        "xy": (4, "XY"),
        "yz": (5, "YZ"),
        "zx": (6, "ZX"),
    },
}

_STRESS_FIELDS = frozenset(
    {
        "Tresca Stress",
        "von Mises Stress",
        "Major Principal Stress",
        "Intermediate Principal Stress",
        "Minor Principal Stress",
        "Stress xx component",
        "Stress xy component",
        "Stress xz component",
        "Stress yy component",
        "Stress yz component",
        "Stress zz component",
    }
)


@dataclass(frozen=True, slots=True)
class PreparedPostLineSample:
    boundary: AnalyzeCreationBoundary
    source: PreparedResultTarget
    parent_group: Any
    pipeline: Any
    label: str
    field: str
    field_components: int
    component: str
    component_index: int
    native_component: str
    unit: str
    start_mm: tuple[float, float, float]
    end_mm: tuple[float, float, float]
    resolution: int
    source_was_visible: bool


@dataclass(frozen=True, slots=True)
class PreparedPostPointSample:
    boundary: AnalyzeCreationBoundary
    source: PreparedResultTarget
    parent_group: Any
    pipeline: Any
    label: str
    field: str
    field_components: int
    unit: str
    point_mm: tuple[float, float, float]
    source_was_visible: bool


def _vector_mm(value: Any, name: str) -> tuple[float, float, float]:
    if not isinstance(value, Mapping) or set(value) != {"x", "y", "z"}:
        raise NativeAnalyzeError(f"{name} must contain only x, y, and z.")
    result = []
    for axis in ("x", "y", "z"):
        raw = value[axis]
        if type(raw) not in {int, float} or not math.isfinite(float(raw)):
            raise NativeAnalyzeError(f"{name}.{axis} must be one finite number.")
        coordinate = float(raw)
        if not -MAX_COORDINATE_MM <= coordinate <= MAX_COORDINATE_MM:
            raise NativeAnalyzeError(
                f"{name}.{axis} must be between -1000000000 and 1000000000 mm."
            )
        result.append(coordinate)
    return tuple(result)


def _finite(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise NativeAnalyzeError(f"{name} is not a finite numerical value.") from exc
    if not math.isfinite(number):
        raise NativeAnalyzeError(f"{name} is not a finite numerical value.")
    return float(format(number, ".15g"))


def post_point_fields(source: Any) -> list[dict[str, Any]]:
    try:
        point_data = source.getDataSet().GetPointData()
        count = int(point_data.GetNumberOfArrays())
    except Exception as exc:
        raise NativeAnalyzeError(
            "The exact post-processing source has no readable point-field data."
        ) from exc
    known_units = {
        str(field.get("name", "")): str(field.get("unit", "") or "")
        for field in result_state(source).get("fields", ())
        if field.get("association") == "point"
    }
    fields = []
    for index in range(count):
        try:
            array = point_data.GetArray(index)
            name = str(array.GetName() or "") if array is not None else ""
            components = int(array.GetNumberOfComponents()) if array is not None else 0
        except Exception:
            continue
        if name and components > 0:
            fields.append(
                {
                    "name": name,
                    "components": components,
                    "unit": known_units.get(name, ""),
                }
            )
    return fields


def select_post_point_field(source: Any, value: Any) -> dict[str, Any]:
    name = str(value or "").strip()
    available = post_point_fields(source)
    selected = next((field for field in available if field["name"] == name), None)
    if selected is None:
        raise NativeAnalyzeError(
            f"field {name!r} is not an available point field.",
            error_code="NATIVE_ANALYZE_FIELD_UNAVAILABLE",
            repair={
                "source": {"object_name": str(source.Name)},
                "available_point_fields": available[:MAX_REPAIR_FIELDS],
                "available_point_fields_truncated": len(available) > MAX_REPAIR_FIELDS,
            },
        )
    return selected


def _graph_target(document: Any, document_uid: str, value: Any):
    source = prepare_result_target(
        document,
        document_uid,
        value,
        expected_kinds=frozenset({"pipeline", "branch_filter", "filter"}),
    )
    parent = _post_parent_group(document, source.result, source.kind)
    return source, parent, _owning_post_pipeline(document, parent)


def prepare_post_line_sample(
    document: Any,
    document_uid: str,
    *,
    source: Any,
    label: Any,
    field: Any,
    component: Any,
    start_mm: Any,
    end_mm: Any,
    resolution: Any,
) -> PreparedPostLineSample:
    source_target, parent_group, pipeline = _graph_target(
        document, document_uid, source
    )
    selected = select_post_point_field(source_target.result, field)
    choices = _COMPONENTS.get(int(selected["components"]), {"magnitude": (0, "Magnitude")})
    normalized_component = str(component or "").strip().lower()
    if normalized_component not in choices:
        raise NativeAnalyzeError(
            f"component {normalized_component!r} is invalid for field {selected['name']!r}.",
            error_code="NATIVE_ANALYZE_FIELD_COMPONENT_INVALID",
            repair={
                "field": selected["name"],
                "field_components": selected["components"],
                "allowed_components": list(choices),
            },
        )
    first = _vector_mm(start_mm, "start_mm")
    second = _vector_mm(end_mm, "end_mm")
    if math.dist(first, second) <= 1e-12:
        raise NativeAnalyzeError("start_mm and end_mm must define a nonzero line.")
    if type(resolution) is not int or not 1 <= resolution <= 100_000:
        raise NativeAnalyzeError("resolution must be an integer from 1 through 100000.")
    component_index, native_component = choices[normalized_component]
    return PreparedPostLineSample(
        creation_boundary(document),
        source_target,
        parent_group,
        pipeline,
        _label(label),
        selected["name"],
        int(selected["components"]),
        normalized_component,
        component_index,
        native_component,
        str(selected.get("unit", "") or ""),
        first,
        second,
        resolution,
        bool(source_target.result.ViewObject.Visibility),
    )


def prepare_post_point_sample(
    document: Any,
    document_uid: str,
    *,
    source: Any,
    label: Any,
    field: Any,
    point_mm: Any,
) -> PreparedPostPointSample:
    source_target, parent_group, pipeline = _graph_target(
        document, document_uid, source
    )
    selected = select_post_point_field(source_target.result, field)
    return PreparedPostPointSample(
        creation_boundary(document),
        source_target,
        parent_group,
        pipeline,
        _label(label),
        selected["name"],
        int(selected["components"]),
        str(selected.get("unit", "") or ""),
        _vector_mm(point_mm, "point_mm"),
        bool(source_target.result.ViewObject.Visibility),
    )


def _require_current_graph(document: Any, prepared: Any) -> None:
    source = prepared.source.result
    if (
        not is_live(document, source)
        or not is_live(document, prepared.parent_group)
        or not is_live(document, prepared.pipeline)
        or _post_parent_group(document, source, prepared.source.kind)
        is not prepared.parent_group
        or _owning_post_pipeline(document, prepared.parent_group) is not prepared.pipeline
    ):
        raise NativeAnalyzeError(
            "The exact post-processing graph changed after sample preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    selected = select_post_point_field(source, prepared.field)
    if int(selected["components"]) != prepared.field_components:
        raise NativeAnalyzeError(
            "The selected point field changed after sample preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )


def _configure_view(sample: Any, field: str, native_component: str) -> None:
    try:
        fields = tuple(sample.ViewObject.getEnumerationsOfProperty("Field") or ())
        if field in fields:
            sample.ViewObject.Field = field
            components = tuple(
                sample.ViewObject.getEnumerationsOfProperty("Component") or ()
            )
            if native_component in components:
                sample.ViewObject.Component = native_component
    except Exception:
        pass


def create_post_line_sample(
    document: Any, prepared: PreparedPostLineSample
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedPostLineSample):
        raise TypeError("prepared must be a PreparedPostLineSample")
    require_boundary(document, prepared.boundary)
    _require_current_graph(document, prepared)
    source = prepared.source.result
    try:
        sample = document.addObject(
            "Fem::FemPostDataAlongLineFilter",
            document.getUniqueObjectName("DataAlongLine"),
        )
        prepared = assign_prepared_label(sample, prepared)
        prepared.parent_group.addObject(sample)
        sample.Point1 = prepared.start_mm
        sample.Point2 = prepared.end_mm
        sample.Resolution = prepared.resolution
        sample.ViewObject.DisplayMode = "Surface"
        sample.ViewObject.SelectionStyle = "BoundBox"
        _copy_none_field_color(source, sample)
        publish_operation(document, prepared.boundary, sample)
        if document.recompute() is False:
            raise NativeAnalyzeError("The FEM line sample could not populate its input data.")
        sample.configureDataAlongLine(
            prepared.field,
            prepared.component_index,
            prepared.unit,
        )
        _configure_view(sample, prepared.field, prepared.native_component)
        sample.ViewObject.Visibility = True
    except NativeAnalyzeError:
        raise
    except Exception as exc:
        raise NativeAnalyzeError(
            f"The FEM line sample could not be created: {exc}",
            error_code="NATIVE_ANALYZE_FACTORY_FAILED",
        ) from exc
    changed = tuple(dict.fromkeys((prepared.parent_group, prepared.pipeline)))
    return NativeMutationDraft(
        value={"prepared": prepared, "sample": sample},
        recompute_targets=(sample, prepared.parent_group, prepared.pipeline),
        created=(object_identity(sample),),
        changed=tuple(object_identity(obj) for obj in changed),
    )


def create_post_point_sample(
    document: Any, prepared: PreparedPostPointSample
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedPostPointSample):
        raise TypeError("prepared must be a PreparedPostPointSample")
    require_boundary(document, prepared.boundary)
    _require_current_graph(document, prepared)
    source = prepared.source.result
    try:
        sample = document.addObject(
            "Fem::FemPostDataAtPointFilter",
            document.getUniqueObjectName("DataAtPoint"),
        )
        prepared = assign_prepared_label(sample, prepared)
        prepared.parent_group.addObject(sample)
        sample.Center = prepared.point_mm
        sample.ViewObject.DisplayMode = "Surface"
        sample.ViewObject.SelectionStyle = "BoundBox"
        _copy_none_field_color(source, sample)
        publish_operation(document, prepared.boundary, sample)
        if document.recompute() is False:
            raise NativeAnalyzeError("The FEM point sample could not populate its input data.")
        sample.configureDataAtPoint(prepared.field, prepared.unit)
        native_component = "Scalar" if prepared.field_components == 1 else "Magnitude"
        _configure_view(sample, prepared.field, native_component)
        sample.ViewObject.Visibility = True
    except NativeAnalyzeError:
        raise
    except Exception as exc:
        raise NativeAnalyzeError(
            f"The FEM point sample could not be created: {exc}",
            error_code="NATIVE_ANALYZE_FACTORY_FAILED",
        ) from exc
    changed = tuple(dict.fromkeys((prepared.parent_group, prepared.pipeline)))
    return NativeMutationDraft(
        value={"prepared": prepared, "sample": sample},
        recompute_targets=(sample, prepared.parent_group, prepared.pipeline),
        created=(object_identity(sample),),
        changed=tuple(object_identity(obj) for obj in changed),
    )


def _valid_mask(sample: Any, expected_count: int) -> list[bool] | None:
    try:
        point_data = sample.getDataSet().GetPointData()
        for name in ("ValidPointArray", "vtkValidPointMask"):
            array = point_data.GetArray(name)
            if array is not None and int(array.GetNumberOfTuples()) == expected_count:
                return [bool(array.GetTuple1(index)) for index in range(expected_count)]
    except Exception:
        pass
    return None


def _line_summary(sample: Any, prepared: PreparedPostLineSample) -> dict[str, Any]:
    distances = [_finite(value, "line distance") for value in tuple(sample.XAxisData)]
    values = [_finite(value, "line sample") for value in tuple(sample.YAxisData)]
    if len(distances) != prepared.resolution + 1 or len(values) != len(distances):
        raise NativeAnalyzeError(
            "The FEM line sample did not produce the requested number of samples."
        )
    if any(right < left for left, right in zip(distances, distances[1:])):
        raise NativeAnalyzeError("The FEM line-sample distances are not monotonic.")
    mask = _valid_mask(sample, len(values))
    valid_values = (
        [value for value, valid in zip(values, mask, strict=True) if valid]
        if mask is not None
        else values
    )
    if not valid_values:
        raise NativeAnalyzeError(
            "The FEM line does not intersect valid result data.",
            error_code="NATIVE_ANALYZE_SAMPLE_OUTSIDE_RESULT",
        )
    result = {
        "field": prepared.field,
        "component": prepared.component,
        "sample_count": len(values),
        "valid_sample_count": len(valid_values),
        "validity_mask_available": mask is not None,
        "distance_range_mm": [distances[0], distances[-1]],
        "value_range": [min(valid_values), max(valid_values)],
        "first_sample": {"distance_mm": distances[0], "value": values[0]},
        "last_sample": {"distance_mm": distances[-1], "value": values[-1]},
    }
    if mask is not None:
        result["first_sample"]["valid"] = mask[0]
        result["last_sample"]["valid"] = mask[-1]
    if prepared.unit:
        result["unit"] = prepared.unit
    return result


def _point_summary(sample: Any, prepared: PreparedPostPointSample) -> dict[str, Any]:
    values = tuple(sample.PointData or ())
    if len(values) != 1:
        try:
            dataset = sample.getDataSet()
            output_point_count = int(dataset.GetNumberOfPoints())
            output_fields = [field["name"] for field in post_point_fields(sample)]
        except Exception:
            output_point_count = 0
            output_fields = []
        raise NativeAnalyzeError(
            "The FEM point probe did not produce exactly one value.",
            repair={
                "field": prepared.field,
                "point_mm": list(prepared.point_mm),
                "point_value_count": len(values),
                "output_point_count": output_point_count,
                "output_point_fields": output_fields[:MAX_REPAIR_FIELDS],
            },
        )
    mask = _valid_mask(sample, 1)
    valid = mask[0] if mask is not None else True
    if not valid:
        raise NativeAnalyzeError(
            "The requested point is outside valid result data.",
            error_code="NATIVE_ANALYZE_SAMPLE_OUTSIDE_RESULT",
            repair={"point_mm": list(prepared.point_mm)},
        )
    result = {
        "field": prepared.field,
        "component": "scalar" if prepared.field_components == 1 else "magnitude",
        "point_mm": list(prepared.point_mm),
        "value": _finite(values[0], "point sample"),
        "valid": True,
        "validity_mask_available": mask is not None,
    }
    if prepared.unit:
        result["unit"] = prepared.unit
    return result


def _verify_sample_common(document: Any, prepared: Any, sample: Any, type_id: str) -> dict[str, bool]:
    source = prepared.source.result
    return {
        "live sample": is_live(document, sample),
        "sample type": str(sample.TypeId) == type_id,
        "parent group membership": sample in tuple(prepared.parent_group.Group or ()),
        "owning pipeline": _owning_post_pipeline(document, sample) is prepared.pipeline,
        "source retained": is_live(document, source),
        "source visibility preserved": bool(source.ViewObject.Visibility)
        is prepared.source_was_visible,
        "sample visible": bool(sample.ViewObject.Visibility),
        "label": str(sample.Label) == prepared.label,
    }


def verify_post_line_sample(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    sample = draft.value["sample"]
    verify_operation_block(document, prepared.boundary, sample)
    checks = _verify_sample_common(
        document, prepared, sample, "Fem::FemPostDataAlongLineFilter"
    )
    checks.update(
        {
            "field": str(sample.PlotData) == prepared.field,
            "component": str(sample.PlotDataComponent) == prepared.native_component,
            "unit": str(sample.Unit) == prepared.unit,
            "resolution": int(sample.Resolution) == prepared.resolution,
        }
    )
    summary = _line_summary(sample, prepared)
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise NativeAnalyzeError(
            "The FEM line sample failed its exact postcondition: "
            + ", ".join(failures)
            + "."
        )
    return {
        "created_line_sample": result_state(sample, include_ranges=False),
        "sample": summary,
        "source": result_reference_state(prepared.source.result),
        "pipeline": result_reference_state(prepared.pipeline),
        "presentation": {
            "visible_sample": str(sample.Name),
            "source_visibility_preserved": True,
        },
    }


def verify_post_point_sample(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    sample = draft.value["sample"]
    verify_operation_block(document, prepared.boundary, sample)
    checks = _verify_sample_common(
        document, prepared, sample, "Fem::FemPostDataAtPointFilter"
    )
    checks.update(
        {
            "field": str(sample.FieldName) == prepared.field,
            "unit": str(sample.Unit) == prepared.unit,
        }
    )
    summary = _point_summary(sample, prepared)
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise NativeAnalyzeError(
            "The FEM point sample failed its exact postcondition: "
            + ", ".join(failures)
            + "."
        )
    return {
        "created_point_sample": result_state(sample, include_ranges=False),
        "sample": summary,
        "source": result_reference_state(prepared.source.result),
        "pipeline": result_reference_state(prepared.pipeline),
        "presentation": {
            "visible_sample": str(sample.Name),
            "source_visibility_preserved": True,
        },
    }


def linearized_stress_summary(
    document: Any, document_uid: str, target: Any
) -> dict[str, Any]:
    prepared = prepare_result_target(
        document,
        document_uid,
        target,
        expected_kinds=frozenset({"filter"}),
    )
    sample = prepared.result
    if str(sample.TypeId) != "Fem::FemPostDataAlongLineFilter":
        raise NativeAnalyzeError(
            "Stress linearization requires an exact FEM data-along-line filter.",
            error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
            repair={"accepted_type": "Fem::FemPostDataAlongLineFilter"},
        )
    field = str(sample.PlotData or "")
    unit = str(getattr(sample, "Unit", "") or "")
    if not unit:
        unit = next(
            (
                str(item.get("unit", "") or "")
                for item in post_point_fields(sample)
                if item["name"] == field
            ),
            "",
        )
    try:
        import FreeCAD as App

        pressure_unit = App.Units.Quantity(f"1 {unit}").Unit == App.Units.Quantity(
            "1 Pa"
        ).Unit
    except Exception:
        pressure_unit = False
    if not pressure_unit:
        raise NativeAnalyzeError(
            "Stress linearization requires a pressure-unit field sampled along the line.",
            error_code="NATIVE_ANALYZE_FIELD_UNAVAILABLE",
            repair={
                "field": field,
                "field_unit": unit,
                "required_unit_dimension": "pressure",
                "known_native_stress_fields": sorted(_STRESS_FIELDS),
            },
        )
    distances = [_finite(value, "line distance") for value in tuple(sample.XAxisData)]
    stresses = [_finite(value, "line stress") for value in tuple(sample.YAxisData)]
    if len(distances) < 2 or len(stresses) != len(distances):
        raise NativeAnalyzeError("Stress linearization requires at least two line samples.")
    if any(right <= left for left, right in zip(distances, distances[1:])):
        raise NativeAnalyzeError(
            "Stress-linearization sample distances must be strictly increasing."
        )
    thickness = distances[-1] - distances[0]
    if thickness <= 1e-12:
        raise NativeAnalyzeError("Stress-linearization thickness must be greater than zero.")
    centered = [distance - (distances[0] + thickness * 0.5) for distance in distances]
    membrane = sum(
        (centered[index + 1] - centered[index])
        * (stresses[index + 1] + stresses[index])
        for index in range(len(stresses) - 1)
    ) / (2.0 * thickness)
    first_moment = 0.0
    for index in range(len(stresses) - 1):
        first = centered[index]
        second = centered[index + 1]
        slope = (stresses[index + 1] - stresses[index]) / (second - first)
        intercept = stresses[index] - slope * first
        first_moment += (
            slope * (second**3 - first**3) / 3.0
            + intercept * (second**2 - first**2) / 2.0
        )
    bending_slope = 12.0 * first_moment / thickness**3
    membrane_bending = [
        membrane + bending_slope * coordinate
        for coordinate in centered
    ]
    residual = [
        total - linearized
        for total, linearized in zip(stresses, membrane_bending, strict=True)
    ]
    result = {
        "source": result_reference_state(sample),
        "field": field,
        "component": "scalar",
        "integration": "piecewise_linear_exact",
        "sample_count": len(stresses),
        "thickness_mm": _finite(thickness, "thickness"),
        "membrane": _finite(membrane, "membrane stress"),
        "membrane_plus_bending": {
            "first_surface": _finite(membrane_bending[0], "first surface stress"),
            "second_surface": _finite(membrane_bending[-1], "second surface stress"),
            "range": [
                _finite(min(membrane_bending), "minimum linearized stress"),
                _finite(max(membrane_bending), "maximum linearized stress"),
            ],
        },
        "total_stress": {
            "first_surface": stresses[0],
            "second_surface": stresses[-1],
            "range": [min(stresses), max(stresses)],
        },
        "peak_residual_range": [min(residual), max(residual)],
    }
    if unit:
        result["unit"] = unit
    return result
