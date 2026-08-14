# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact preparation and detached processing for Native point-cloud operations."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeMeshTargets import is_active_mesh_input, is_live
from VibeCADNativePointTargets import (
    PreparedPointTarget,
    point_target_still_exact,
    prepare_point_target,
)
from VibeCADNativeTargets import NativeObjectRef, resolve_object


@dataclass(frozen=True, slots=True)
class PreparedGeometryTarget:
    source: Any
    object_name: str
    label: str
    expected_state_sha256: str
    geometry: Any
    output_placement: Any
    source_visible: bool


@dataclass(frozen=True, slots=True)
class PreparedPointPlan:
    operation: str
    point_targets: tuple[PreparedPointTarget, ...]
    geometry_targets: tuple[PreparedGeometryTarget, ...]
    settings: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ProcessedPointOutput:
    label: str
    points: Any
    placement: Any
    width: int = 0
    height: int = 0
    intensities: tuple[float, ...] = ()
    colors: tuple[tuple[float, ...], ...] = ()
    normals: tuple[tuple[float, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class ProcessedPointPlan:
    prepared: PreparedPointPlan
    outputs: tuple[ProcessedPointOutput, ...]
    dropped_attributes: tuple[str, ...] = ()


def _label(value: Any, field: str = "label") -> str:
    result = str(value or "").strip()
    if not result or len(result) > 160:
        raise NativeMeshError(f"{field} must contain 1 to 160 visible characters.")
    return result


def _positive(value: Any, field: str, maximum: float) -> float:
    if type(value) not in {int, float}:
        raise NativeMeshError(f"{field} must be one finite positive number.")
    result = float(value)
    if not math.isfinite(result) or result <= 0.0 or result > maximum:
        raise NativeMeshError(
            f"{field} must be greater than zero and no more than {maximum:g}."
        )
    return result


def _geometry_target(
    document: Any,
    document_uid: str,
    value: Any,
) -> PreparedGeometryTarget:
    required = {"object_name", "expected_state_sha256", "label"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise NativeMeshError(
            "Every geometry source must contain object_name, expected_state_sha256, and label."
        )
    reference = NativeObjectRef(document_uid, str(value["object_name"]))
    source = resolve_object(document, reference, expected_types=("App::GeoFeature",))
    if not is_active_mesh_input(source):
        raise NativeMeshError(
            "The exact geometry is not active at the current History position.",
            error_code="NATIVE_POINT_CLOUD_HISTORY_TARGET_INACTIVE",
        )
    state = mesh_object_state(source)
    expected = str(value["expected_state_sha256"])
    if state.get("state_sha256") != expected:
        raise NativeMeshError(
            "The exact geometry changed after the provider read its state.",
            error_code="NATIVE_POINT_CLOUD_STATE_STALE",
            repair={
                "source": {"object_name": reference.object_name},
                "current_state_sha256": state.get("state_sha256"),
                "current_topology": state.get("topology"),
            },
        )
    geometry = source.getPropertyOfGeometry()
    if geometry is None:
        raise NativeMeshError(
            "The exact source does not expose geometry that can be sampled into points."
        )
    try:
        output_placement = source.getGlobalPlacement() * source.Placement.inverse()
    except Exception as exc:
        raise NativeMeshError(
            "The exact geometry placement could not be detached for point sampling."
        ) from exc
    return PreparedGeometryTarget(
        source,
        str(source.Name),
        _label(value["label"]),
        expected,
        geometry,
        output_placement,
        bool(source.Visibility),
    )


def _point_targets(
    document: Any,
    document_uid: str,
    values: Any,
    *,
    minimum: int,
    maximum: int,
) -> tuple[PreparedPointTarget, ...]:
    if not isinstance(values, list) or not minimum <= len(values) <= maximum:
        raise NativeMeshError(
            f"point_clouds must contain {minimum} to {maximum} exact point clouds."
        )
    targets = tuple(
        prepare_point_target(document, document_uid, value, require_label=False)
        for value in values
    )
    names = [str(target.source.Name) for target in targets]
    if len(names) != len(set(names)):
        raise NativeMeshError("point_clouds must not repeat a point cloud.")
    return targets


def _finite(value: Any, field: str) -> float:
    if type(value) not in {int, float}:
        raise NativeMeshError(f"{field} must be one finite number.")
    result = float(value)
    if not math.isfinite(result):
        raise NativeMeshError(f"{field} must be one finite number.")
    return result


def _polygon(value: Any) -> tuple[tuple[float, float, float], ...]:
    if not isinstance(value, list) or not 3 <= len(value) <= 256:
        raise NativeMeshError("polygon must contain 3 to 256 model-space vertices.")
    points = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping) or set(item) != {"x_mm", "y_mm", "z_mm"}:
            raise NativeMeshError(
                f"polygon[{index}] must contain only x_mm, y_mm, and z_mm."
            )
        points.append(
            (
                _finite(item["x_mm"], f"polygon[{index}].x_mm"),
                _finite(item["y_mm"], f"polygon[{index}].y_mm"),
                _finite(item["z_mm"], f"polygon[{index}].z_mm"),
            )
        )
    if points[0] == points[-1]:
        raise NativeMeshError(
            "Do not repeat the first polygon vertex; polygon closure is implicit."
        )
    if len(points) != len(set(points)):
        raise NativeMeshError("polygon vertices must be distinct.")
    return tuple(points)


def _polygon_result(value: Any) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    if not isinstance(value, Mapping):
        raise NativeMeshError("result must identify one point-cloud cut result mode.")
    mode = str(value.get("mode") or "")
    if mode in {"keep_inside", "keep_outside"}:
        if set(value) != {"mode", "result_label"}:
            raise NativeMeshError(f"{mode} requires only mode and result_label.")
        return mode, (mode,), (_label(value["result_label"], "result_label"),)
    if mode == "split":
        if set(value) != {"mode", "inside_result_label", "outside_result_label"}:
            raise NativeMeshError(
                "split requires inside_result_label and outside_result_label."
            )
        return (
            mode,
            ("keep_inside", "keep_outside"),
            (
                _label(value["inside_result_label"], "inside_result_label"),
                _label(value["outside_result_label"], "outside_result_label"),
            ),
        )
    raise NativeMeshError("result.mode must be keep_inside, keep_outside, or split.")


def prepare_point_plan(
    document: Any,
    document_uid: str,
    operation: str,
    values: Mapping[str, Any],
) -> PreparedPointPlan:
    if operation == "convert_to_points":
        raw = values["geometry_sources"]
        if not isinstance(raw, list) or not 1 <= len(raw) <= 16:
            raise NativeMeshError(
                "geometry_sources must contain 1 to 16 exact geometry objects."
            )
        targets = tuple(_geometry_target(document, document_uid, item) for item in raw)
        names = [str(target.source.Name) for target in targets]
        if len(names) != len(set(names)):
            raise NativeMeshError("geometry_sources must not repeat a geometry object.")
        return PreparedPointPlan(
            operation,
            (),
            targets,
            {
                "maximum_distance_mm": _positive(
                    values["maximum_distance_mm"], "maximum_distance_mm", 1_000_000.0
                )
            },
        )
    if operation == "structure":
        target = prepare_point_target(
            document, document_uid, values["target"], require_label=False
        )
        return PreparedPointPlan(
            operation,
            (target,),
            (),
            {
                "result_label": _label(values["result_label"], "result_label"),
                "coordinate_tolerance_mm": _positive(
                    values["coordinate_tolerance_mm"],
                    "coordinate_tolerance_mm",
                    1_000_000.0,
                ),
            },
        )
    if operation == "merge":
        return PreparedPointPlan(
            operation,
            _point_targets(
                document, document_uid, values["point_clouds"], minimum=2, maximum=16
            ),
            (),
            {"result_label": _label(values["result_label"], "result_label")},
        )
    if operation == "polygon_cut":
        target = prepare_point_target(
            document, document_uid, values["target"], require_label=False
        )
        mode, regions, labels = _polygon_result(values["result"])
        return PreparedPointPlan(
            operation,
            (target,),
            (),
            {
                "polygon": _polygon(values["polygon"]),
                "result_mode": mode,
                "regions": regions,
                "labels": labels,
            },
        )
    raise NativeMeshError("The requested point-cloud operation is unavailable.")


def point_plan_still_exact(document: Any, prepared: PreparedPointPlan) -> bool:
    if any(not point_target_still_exact(document, target) for target in prepared.point_targets):
        return False
    return all(
        is_live(document, target.source)
        and is_active_mesh_input(target.source)
        and mesh_object_state(target.source).get("state_sha256")
        == target.expected_state_sha256
        for target in prepared.geometry_targets
    )


def _mapped(values: tuple[Any, ...], indices: list[int], missing: Any) -> tuple[Any, ...]:
    if not values:
        return ()
    return tuple(missing if index < 0 else values[index] for index in indices)


def _subset(values: tuple[Any, ...], indices: list[int]) -> tuple[Any, ...]:
    return tuple(values[index] for index in indices) if values else ()


def process_point_plan(
    prepared: PreparedPointPlan,
    *,
    cancelled: Any,
    progress: Any,
) -> ProcessedPointPlan:
    import FreeCAD as App
    import Points

    if cancelled():
        from VibeCADNativeBackground import NativeBackgroundCancelled

        raise NativeBackgroundCancelled()
    progress(5, "Processing detached point-cloud data")
    if prepared.operation == "convert_to_points":
        outputs = []
        distance = prepared.settings["maximum_distance_mm"]
        total = len(prepared.geometry_targets)
        for index, target in enumerate(prepared.geometry_targets):
            if cancelled():
                from VibeCADNativeBackground import NativeBackgroundCancelled

                raise NativeBackgroundCancelled()
            try:
                sampled = Points.sampleNativeGeometry(target.geometry, distance)
            except Exception as exc:
                raise NativeMeshError(
                    f"{target.object_name} could not be sampled into point data."
                ) from exc
            outputs.append(
                ProcessedPointOutput(
                    target.label,
                    sampled["points"],
                    target.output_placement,
                    normals=tuple(tuple(float(v) for v in item) for item in sampled["normals"]),
                )
            )
            progress(10 + int(75 * (index + 1) / total), f"Sampled {index + 1} of {total}")
        return ProcessedPointPlan(prepared, tuple(outputs))

    target = prepared.point_targets[0]
    if prepared.operation == "structure":
        if target.width > 0:
            data = {
                "points": target.points,
                "width": target.width,
                "height": target.height,
                "source_indices": list(range(target.point_count)),
            }
        else:
            try:
                data = Points.structureNativePointCloud(
                    target.points, prepared.settings["coordinate_tolerance_mm"]
                )
            except Exception as exc:
                raise NativeMeshError(
                    "The exact point cloud could not be arranged into one unambiguous X/Y grid."
                ) from exc
        indices = [int(value) for value in data["source_indices"]]
        output = ProcessedPointOutput(
            prepared.settings["result_label"],
            data["points"],
            target.placement,
            int(data["width"]),
            int(data["height"]),
            _mapped(target.intensities, indices, math.nan),
            _mapped(target.colors, indices, (0.0, 0.0, 0.0, 0.0)),
            _mapped(target.normals, indices, (math.nan, math.nan, math.nan)),
        )
        progress(90, "Structured point grid verified")
        return ProcessedPointPlan(prepared, (output,))

    if prepared.operation == "merge":
        targets = prepared.point_targets
        try:
            data = Points.mergeNativePointClouds(
                [item.points for item in targets], [item.placement for item in targets]
            )
        except Exception as exc:
            raise NativeMeshError("The exact point clouds could not be merged.") from exc
        mapping = [(int(source), int(index)) for source, index in data["source_indices"]]
        dropped = []

        def merged_attribute(name: str) -> tuple[Any, ...]:
            attributes = [getattr(item, name) for item in targets]
            if not all(attributes):
                if any(attributes):
                    dropped.append(name)
                return ()
            return tuple(attributes[source][index] for source, index in mapping)

        normals = merged_attribute("normals")
        if normals:
            transformed = []
            for source, index in mapping:
                value = targets[source].placement.Rotation.multVec(
                    App.Vector(*targets[source].normals[index])
                )
                transformed.append((float(value.x), float(value.y), float(value.z)))
            normals = tuple(transformed)
        output = ProcessedPointOutput(
            prepared.settings["result_label"],
            data["points"],
            App.Placement(),
            intensities=merged_attribute("intensities"),
            colors=merged_attribute("colors"),
            normals=normals,
        )
        progress(90, "Merged point data verified")
        return ProcessedPointPlan(prepared, (output,), tuple(sorted(set(dropped))))

    polygon = [App.Vector(*point) for point in prepared.settings["polygon"]]
    outputs = []
    for region, label in zip(prepared.settings["regions"], prepared.settings["labels"]):
        try:
            data = Points.selectNativePointCloud(
                target.points, target.placement, polygon, region == "keep_inside"
            )
        except Exception as exc:
            raise NativeMeshError(
                "The model-space polygon could not be applied to the exact point cloud."
            ) from exc
        count = int(data["point_count"])
        if count < 1 or count >= target.point_count:
            raise NativeMeshError(
                f"The polygon does not produce a nonempty changed result for {label}."
            )
        indices = [int(value) for value in data["source_indices"]]
        outputs.append(
            ProcessedPointOutput(
                label,
                data["points"],
                target.placement,
                intensities=_subset(target.intensities, indices),
                colors=_subset(target.colors, indices),
                normals=_subset(target.normals, indices),
            )
        )
    progress(90, "Polygon point selection verified")
    return ProcessedPointPlan(prepared, tuple(outputs))
