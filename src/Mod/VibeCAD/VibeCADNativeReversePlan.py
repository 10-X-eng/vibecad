# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact preparation and detached processing for Reverse Engineering tools."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeMeshTargets import (
    PreparedMeshTarget,
    is_active_mesh_input,
    is_live,
    mesh_target_still_exact,
    prepare_mesh_target,
)
from VibeCADNativePointTargets import (
    PreparedPointTarget,
    point_target_still_exact,
    prepare_point_target,
)
from VibeCADNativeTargets import NativeObjectRef, resolve_object


@dataclass(frozen=True, slots=True)
class PreparedGeometryFitTarget:
    source: Any
    object_name: str
    label: str
    expected_state_sha256: str
    source_visible: bool
    type_id: str
    geometry: Any
    global_placement: Any
    parent_placement: Any


@dataclass(frozen=True, slots=True)
class PreparedMeshFitTarget:
    exact: PreparedMeshTarget
    mesh: Any
    global_placement: Any


@dataclass(frozen=True, slots=True)
class PreparedReversePlan:
    operation: str
    point_targets: tuple[PreparedPointTarget, ...]
    geometry_targets: tuple[PreparedGeometryFitTarget, ...]
    mesh_targets: tuple[PreparedMeshFitTarget, ...]
    settings: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ProcessedReverseOutput:
    label: str
    kind: str
    geometry: Any
    placement: Any
    metrics: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ProcessedReversePlan:
    prepared: PreparedReversePlan
    outputs: tuple[ProcessedReverseOutput, ...]


def _label(value: Any, field: str = "result_label") -> str:
    result = str(value or "").strip()
    if not result or len(result) > 160:
        raise NativeMeshError(f"{field} must contain 1 to 160 visible characters.")
    return result


def _geometry_target(
    document: Any,
    document_uid: str,
    value: Any,
    *,
    result_label: str,
    expected_types: tuple[str, ...] = ("App::GeoFeature",),
) -> PreparedGeometryFitTarget:
    required = {"object_name", "expected_state_sha256"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise NativeMeshError(
            "The exact geometry source must contain only object_name and expected_state_sha256."
        )
    reference = NativeObjectRef(document_uid, str(value["object_name"]))
    source = resolve_object(document, reference, expected_types=expected_types)
    if not is_active_mesh_input(source):
        raise NativeMeshError(
            "The exact geometry is not active at the current History position.",
            error_code="NATIVE_REVERSE_HISTORY_TARGET_INACTIVE",
        )
    state = mesh_object_state(source)
    expected = str(value["expected_state_sha256"])
    if state.get("state_sha256") != expected:
        raise NativeMeshError(
            "The exact geometry changed after the provider read its state.",
            error_code="NATIVE_REVERSE_STATE_STALE",
            repair={
                "target": {"object_name": reference.object_name},
                "current_state_sha256": state.get("state_sha256"),
                "current_topology": state.get("topology"),
            },
        )
    geometry = source.getPropertyOfGeometry()
    if geometry is None:
        raise NativeMeshError("The exact source does not provide point-bearing geometry.")
    try:
        global_placement = source.getGlobalPlacement()
        parent_placement = global_placement * source.Placement.inverse()
    except Exception as exc:
        raise NativeMeshError("The exact source placement could not be detached.") from exc
    return PreparedGeometryFitTarget(
        source=source,
        object_name=str(source.Name),
        label=_label(result_label),
        expected_state_sha256=expected,
        source_visible=bool(source.Visibility),
        type_id=str(source.TypeId),
        geometry=geometry,
        global_placement=global_placement,
        parent_placement=parent_placement,
    )


def _labeled_geometry_targets(
    document: Any,
    document_uid: str,
    values: Any,
) -> tuple[PreparedGeometryFitTarget, ...]:
    if not isinstance(values, list) or not 1 <= len(values) <= 16:
        raise NativeMeshError("geometry_sources must contain 1 to 16 exact sources.")
    result = []
    for value in values:
        required = {"object_name", "expected_state_sha256", "result_label"}
        if not isinstance(value, Mapping) or set(value) != required:
            raise NativeMeshError(
                "Every geometry source must contain object_name, expected_state_sha256, and result_label."
            )
        result.append(
            _geometry_target(
                document,
                document_uid,
                {name: value[name] for name in required - {"result_label"}},
                result_label=value["result_label"],
            )
        )
    names = [target.object_name for target in result]
    if len(names) != len(set(names)):
        raise NativeMeshError("geometry_sources must not repeat a source object.")
    return tuple(result)


def _mesh_fit_targets(
    document: Any,
    document_uid: str,
    values: Any,
    field: str,
) -> tuple[PreparedMeshFitTarget, ...]:
    if not isinstance(values, list) or not 1 <= len(values) <= 16:
        raise NativeMeshError(f"{field} must contain 1 to 16 exact Meshes.")
    result = []
    names = []
    for value in values:
        required = {"object_name", "expected_state_sha256", "result_label"}
        if not isinstance(value, Mapping) or set(value) != required:
            raise NativeMeshError(f"Every {field} item must contain exactly its published fields.")
        exact = prepare_mesh_target(
            document,
            document_uid,
            {
                "object_name": value["object_name"],
                "expected_state_sha256": value["expected_state_sha256"],
                "label": value["result_label"],
            },
        )
        result.append(PreparedMeshFitTarget(exact, exact.source.Mesh, exact.source.getGlobalPlacement()))
        names.append(str(exact.source.Name))
    if len(names) != len(set(names)):
        raise NativeMeshError(f"{field} must not repeat a Mesh.")
    return tuple(result)


def _structured_targets(
    document: Any,
    document_uid: str,
    values: Any,
) -> tuple[PreparedPointTarget, ...]:
    if not isinstance(values, list) or not 1 <= len(values) <= 16:
        raise NativeMeshError("structured_clouds must contain 1 to 16 exact point grids.")
    targets = []
    for value in values:
        required = {
            "object_name",
            "expected_state_sha256",
            "expected_point_count",
            "result_label",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            raise NativeMeshError(
                "Every structured cloud must contain exactly its published target fields."
            )
        target = prepare_point_target(
            document,
            document_uid,
            {
                "object_name": value["object_name"],
                "expected_state_sha256": value["expected_state_sha256"],
                "expected_point_count": value["expected_point_count"],
                "label": value["result_label"],
            },
        )
        if str(target.source.TypeId) != "Points::Structured" or target.width < 2 or target.height < 2:
            raise NativeMeshError(
                f"{target.source.Name} is not one complete structured point grid."
            )
        targets.append(target)
    names = [str(target.source.Name) for target in targets]
    if len(names) != len(set(names)):
        raise NativeMeshError("structured_clouds must not repeat a point grid.")
    return tuple(targets)


def _finite(value: Any, field: str, minimum: float, maximum: float) -> float:
    if type(value) not in {int, float}:
        raise NativeMeshError(f"{field} must be one finite number.")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise NativeMeshError(f"{field} must be between {minimum:g} and {maximum:g}.")
    return result


def _integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise NativeMeshError(f"{field} must be an integer from {minimum} through {maximum}.")
    return value


def _vector(value: Any, field: str) -> tuple[float, float, float]:
    if not isinstance(value, Mapping) or set(value) != {"x", "y", "z"}:
        raise NativeMeshError(f"{field} must contain only x, y, and z.")
    result = tuple(_finite(value[axis], f"{field}.{axis}", -1.0, 1.0) for axis in ("x", "y", "z"))
    length = math.sqrt(sum(component * component for component in result))
    if length <= 1.0e-12:
        raise NativeMeshError(f"{field} must be a non-zero direction.")
    return tuple(component / length for component in result)


def _surface_settings(values: Mapping[str, Any]) -> dict[str, Any]:
    u_degree = _integer(values["u_degree"], "u_degree", 1, 11)
    v_degree = _integer(values["v_degree"], "v_degree", 1, 11)
    u_poles = _integer(values["u_control_points"], "u_control_points", 2, 100)
    v_poles = _integer(values["v_control_points"], "v_control_points", 2, 100)
    if u_degree >= u_poles or v_degree >= v_poles:
        raise NativeMeshError(
            "Each B-spline surface degree must be smaller than its control-point count."
        )
    smoothing = values["smoothing"]
    smoothing_fields = {
        "enabled",
        "total_weight",
        "gradient_weight",
        "bending_weight",
        "curvature_weight",
    }
    if not isinstance(smoothing, Mapping) or set(smoothing) != smoothing_fields:
        raise NativeMeshError("smoothing must contain exactly its published fields.")
    if type(smoothing["enabled"]) is not bool:
        raise NativeMeshError("smoothing.enabled must be true or false.")
    uv = values["uv_directions"]
    if not isinstance(uv, Mapping):
        raise NativeMeshError("uv_directions must select automatic or explicit directions.")
    mode = str(uv.get("mode") or "")
    if mode == "automatic" and set(uv) == {"mode"}:
        directions = None
    elif mode == "explicit" and set(uv) == {"mode", "u_direction", "v_direction"}:
        u = _vector(uv["u_direction"], "uv_directions.u_direction")
        v = _vector(uv["v_direction"], "uv_directions.v_direction")
        if abs(sum(a * b for a, b in zip(u, v))) >= 0.999999:
            raise NativeMeshError("Explicit U and V directions must not be parallel.")
        directions = (u, v)
    else:
        raise NativeMeshError("uv_directions must match one published mode exactly.")
    return {
        "u_degree": u_degree,
        "v_degree": v_degree,
        "u_control_points": u_poles,
        "v_control_points": v_poles,
        "iterations": _integer(values["iterations"], "iterations", -1, 100),
        "patch_size_factor": _finite(values["patch_size_factor"], "patch_size_factor", 1.0, 2.0),
        "parameter_correction": bool(values["parameter_correction"]),
        "smoothing": {
            "enabled": smoothing["enabled"],
            "total_weight": _finite(smoothing["total_weight"], "smoothing.total_weight", 0.0, 1000.0),
            "gradient_weight": _finite(smoothing["gradient_weight"], "smoothing.gradient_weight", 0.0, 1.0),
            "bending_weight": _finite(smoothing["bending_weight"], "smoothing.bending_weight", 0.0, 1.0),
            "curvature_weight": _finite(smoothing["curvature_weight"], "smoothing.curvature_weight", 0.0, 1.0),
        },
        "uv_directions": directions,
    }


def _curve_settings(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise NativeMeshError("fit must select approximation or smoothing.")
    mode = str(value.get("mode") or "")
    shared = {"mode", "maximum_degree", "continuity", "closed", "tolerance_mm"}
    continuity = str(value.get("continuity") or "")
    if continuity not in {"C0", "G1", "C1", "G2", "C2", "C3", "CN"}:
        raise NativeMeshError("fit.continuity must be C0, G1, C1, G2, C2, C3, or CN.")
    if type(value.get("closed")) is not bool:
        raise NativeMeshError("fit.closed must be true or false.")
    result = {
        "mode": mode,
        "maximum_degree": _integer(value.get("maximum_degree"), "fit.maximum_degree", 2, 11),
        "continuity": continuity,
        "closed": value["closed"],
        "tolerance_mm": _finite(value.get("tolerance_mm"), "fit.tolerance_mm", 1.0e-15, 1000.0),
    }
    if mode == "approximation":
        required = shared | {"minimum_degree", "parametrization"}
        if set(value) != required:
            raise NativeMeshError("An approximation fit must contain exactly its published fields.")
        minimum = _integer(value["minimum_degree"], "fit.minimum_degree", 1, 11)
        if minimum > result["maximum_degree"]:
            raise NativeMeshError("fit.minimum_degree cannot exceed fit.maximum_degree.")
        parametrization = str(value["parametrization"])
        if parametrization not in {"automatic", "chord_length", "centripetal", "uniform"}:
            raise NativeMeshError("fit.parametrization is unavailable.")
        result.update(minimum_degree=minimum, parametrization=parametrization)
        return result
    if mode == "smoothing":
        weights = {"curve_length_weight", "curvature_weight", "torsion_weight"}
        if set(value) != shared | weights:
            raise NativeMeshError("A smoothing fit must contain exactly its published fields.")
        result.update(
            **{
                name: _finite(value[name], f"fit.{name}", 0.0, 1.0)
                for name in weights
            }
        )
        return result
    raise NativeMeshError("fit.mode must be approximation or smoothing.")


def prepare_reverse_plan(
    document: Any,
    document_uid: str,
    operation: str,
    values: Mapping[str, Any],
) -> PreparedReversePlan:
    if operation == "poisson_reconstruction":
        target = prepare_point_target(document, document_uid, values["target"], require_label=False)
        return PreparedReversePlan(
            operation,
            (target,),
            (),
            (),
            {
                "result_label": _label(values["result_label"]),
                "octree_depth": _integer(values["octree_depth"], "octree_depth", 4, 10),
                "solver_divide": _integer(values["solver_divide"], "solver_divide", 1, 20),
                "samples_per_node": _finite(values["samples_per_node"], "samples_per_node", 1.0, 50.0),
                "normal_neighbors": _integer(values["normal_neighbors"], "normal_neighbors", 3, 128),
            },
        )
    if operation == "view_triangulation":
        return PreparedReversePlan(
            operation,
            _structured_targets(document, document_uid, values["structured_clouds"]),
            (),
            (),
            {},
        )
    if operation == "approx_plane":
        return PreparedReversePlan(
            operation,
            (),
            _labeled_geometry_targets(document, document_uid, values["geometry_sources"]),
            (),
            {},
        )
    mesh_fields = {
        "approx_cylinder": "cylinder_meshes",
        "approx_sphere": "sphere_meshes",
        "approx_polynomial": "polynomial_meshes",
    }
    if operation in mesh_fields:
        field = mesh_fields[operation]
        return PreparedReversePlan(
            operation,
            (),
            (),
            _mesh_fit_targets(document, document_uid, values[field], field),
            {},
        )
    if operation == "approx_surface":
        target = _geometry_target(
            document,
            document_uid,
            values["surface_source"],
            result_label=values["result_label"],
            expected_types=("Points::Feature", "Mesh::Feature"),
        )
        return PreparedReversePlan(
            operation,
            (),
            (target,),
            (),
            _surface_settings(values),
        )
    if operation == "approx_curve":
        target = prepare_point_target(
            document,
            document_uid,
            {**values["curve_source"], "label": values["result_label"]},
        )
        if target.point_count < 2:
            raise NativeMeshError("B-spline curve fitting requires at least two ordered points.")
        return PreparedReversePlan(
            operation,
            (target,),
            (),
            (),
            _curve_settings(values["fit"]),
        )
    raise NativeMeshError("The requested Reverse Engineering operation is unavailable.")


def reverse_plan_still_exact(document: Any, prepared: PreparedReversePlan) -> bool:
    return (
        all(point_target_still_exact(document, target) for target in prepared.point_targets)
        and all(
            is_live(document, target.source)
            and is_active_mesh_input(target.source)
            and mesh_object_state(target.source).get("state_sha256") == target.expected_state_sha256
            for target in prepared.geometry_targets
        )
        and all(mesh_target_still_exact(document, target.exact) for target in prepared.mesh_targets)
    )


def _cancelled(cancelled: Any) -> None:
    if cancelled():
        from VibeCADNativeBackground import NativeBackgroundCancelled

        raise NativeBackgroundCancelled()


def _mesh_metrics(mesh: Any) -> dict[str, int]:
    return {
        "point_count": int(mesh.CountPoints),
        "facet_count": int(mesh.CountFacets),
    }


def process_reverse_plan(
    prepared: PreparedReversePlan,
    *,
    cancelled: Any,
    progress: Any,
) -> ProcessedReversePlan:
    import FreeCAD as App
    import ReverseEngineering

    _cancelled(cancelled)
    progress(5, "Processing detached Reverse Engineering data")
    operation = prepared.operation
    outputs: list[ProcessedReverseOutput] = []

    if operation == "poisson_reconstruction":
        if not hasattr(ReverseEngineering, "poissonReconstruction"):
            raise NativeMeshError(
                "Poisson reconstruction requires a VibeCAD build with PCL Surface support.",
                error_code="NATIVE_POISSON_UNAVAILABLE",
            )
        target = prepared.point_targets[0]
        settings = prepared.settings
        try:
            mesh = ReverseEngineering.poissonReconstruction(
                Points=target.points,
                KSearch=settings["normal_neighbors"],
                OctreeDepth=settings["octree_depth"],
                SolverDivide=settings["solver_divide"],
                SamplesPerNode=settings["samples_per_node"],
            )
        except Exception as exc:
            raise NativeMeshError("Poisson reconstruction failed for the exact point cloud.") from exc
        if int(mesh.CountFacets) < 1:
            raise NativeMeshError("Poisson reconstruction produced an empty Mesh.")
        outputs.append(
            ProcessedReverseOutput(
                settings["result_label"],
                "mesh",
                mesh,
                target.placement,
                {**_mesh_metrics(mesh), **settings},
            )
        )
        progress(90, "Poisson surface reconstructed")
        return ProcessedReversePlan(prepared, tuple(outputs))

    if operation == "view_triangulation":
        total = len(prepared.point_targets)
        for index, target in enumerate(prepared.point_targets):
            _cancelled(cancelled)
            try:
                mesh = ReverseEngineering.triangulateNativeStructured(
                    target.points, target.width, target.height
                )
            except Exception as exc:
                raise NativeMeshError(
                    f"{target.source.Name} could not be triangulated as a structured point grid."
                ) from exc
            outputs.append(
                ProcessedReverseOutput(
                    target.label,
                    "mesh",
                    mesh,
                    target.placement,
                    {
                        **_mesh_metrics(mesh),
                        "grid_width": target.width,
                        "grid_height": target.height,
                    },
                )
            )
            progress(10 + int(80 * (index + 1) / total), f"Triangulated {index + 1} of {total}")
        return ProcessedReversePlan(prepared, tuple(outputs))

    if operation == "approx_plane":
        total = len(prepared.geometry_targets)
        for index, target in enumerate(prepared.geometry_targets):
            _cancelled(cancelled)
            try:
                fit = ReverseEngineering.fitNativePlane(target.geometry)
            except Exception as exc:
                raise NativeMeshError(f"{target.object_name} could not be fit to a plane.") from exc
            outputs.append(
                ProcessedReverseOutput(
                    target.label,
                    "plane",
                    {"length_mm": float(fit["length_mm"]), "width_mm": float(fit["width_mm"])},
                    target.parent_placement * fit["placement"],
                    {
                        "rms_deviation_mm": float(fit["rms_deviation_mm"]),
                        "length_mm": float(fit["length_mm"]),
                        "width_mm": float(fit["width_mm"]),
                    },
                )
            )
            progress(10 + int(80 * (index + 1) / total), f"Fit plane {index + 1} of {total}")
        return ProcessedReversePlan(prepared, tuple(outputs))

    if operation in {"approx_cylinder", "approx_sphere", "approx_polynomial"}:
        method_name = {
            "approx_cylinder": "fitNativeCylinder",
            "approx_sphere": "fitNativeSphere",
            "approx_polynomial": "fitNativePolynomial",
        }[operation]
        kind = {
            "approx_cylinder": "cylinder",
            "approx_sphere": "sphere",
            "approx_polynomial": "part_shape",
        }[operation]
        method = getattr(ReverseEngineering, method_name)
        total = len(prepared.mesh_targets)
        for index, target in enumerate(prepared.mesh_targets):
            _cancelled(cancelled)
            try:
                fit = method(target.mesh)
            except Exception as exc:
                raise NativeMeshError(
                    f"{target.exact.source.Name} could not complete {operation}."
                ) from exc
            metrics = {"rms_deviation_mm": float(fit["rms_deviation_mm"])}
            if kind == "cylinder":
                geometry = {
                    "radius_mm": float(fit["radius_mm"]),
                    "height_mm": float(fit["height_mm"]),
                }
                placement = target.global_placement * fit["placement"]
                metrics.update(geometry)
            elif kind == "sphere":
                geometry = {"radius_mm": float(fit["radius_mm"])}
                placement = target.global_placement * App.Placement(fit["center"], App.Rotation())
                metrics.update(geometry)
            else:
                geometry = fit["shape"]
                placement = target.global_placement
            outputs.append(
                ProcessedReverseOutput(target.exact.label, kind, geometry, placement, metrics)
            )
            progress(10 + int(80 * (index + 1) / total), f"Completed fit {index + 1} of {total}")
        return ProcessedReversePlan(prepared, tuple(outputs))

    if operation == "approx_surface":
        target = prepared.geometry_targets[0]
        settings = prepared.settings
        smoothing = settings["smoothing"]
        kwargs = {
            "Points": target.geometry,
            "UDegree": settings["u_degree"],
            "VDegree": settings["v_degree"],
            "NbUPoles": settings["u_control_points"],
            "NbVPoles": settings["v_control_points"],
            "Smooth": smoothing["enabled"],
            "Weight": smoothing["total_weight"],
            "Grad": smoothing["gradient_weight"],
            "Bend": smoothing["bending_weight"],
            "Curv": smoothing["curvature_weight"],
            "Iterations": settings["iterations"],
            "Correction": settings["parameter_correction"],
            "PatchFactor": settings["patch_size_factor"],
        }
        if settings["uv_directions"] is not None:
            u, v = settings["uv_directions"]
            kwargs["UVDirs"] = (App.Vector(*u), App.Vector(*v))
        try:
            surface = ReverseEngineering.approxSurface(**kwargs)
            shape = surface.toShape()
        except Exception as exc:
            raise NativeMeshError(f"{target.object_name} could not be fit to a B-spline surface.") from exc
        if shape.isNull():
            raise NativeMeshError("B-spline surface fitting produced an empty shape.")
        outputs.append(
            ProcessedReverseOutput(
                target.label,
                "part_shape",
                shape,
                target.global_placement,
                {
                    "surface_degree_u": int(surface.UDegree),
                    "surface_degree_v": int(surface.VDegree),
                    "control_points_u": int(surface.NbUPoles),
                    "control_points_v": int(surface.NbVPoles),
                },
            )
        )
        progress(90, "B-spline surface fitted")
        return ProcessedReversePlan(prepared, tuple(outputs))

    if operation == "approx_curve":
        target = prepared.point_targets[0]
        settings = prepared.settings
        kwargs: dict[str, Any] = {
            "Points": target.points,
            "Closed": settings["closed"],
            "MaxDegree": settings["maximum_degree"],
            "Continuity": {"C0": 0, "G1": 1, "C1": 2, "G2": 3, "C2": 4, "C3": 5, "CN": 6}[
                settings["continuity"]
            ],
            "Tolerance": settings["tolerance_mm"],
        }
        if settings["mode"] == "approximation":
            kwargs["MinDegree"] = settings["minimum_degree"]
            parametrization = settings["parametrization"]
            if parametrization != "automatic":
                kwargs["ParametrizationType"] = {
                    "chord_length": "ChordLength",
                    "centripetal": "Centripetal",
                    "uniform": "Uniform",
                }[parametrization]
        else:
            kwargs.update(
                Weight1=settings["curve_length_weight"],
                Weight2=settings["curvature_weight"],
                Weight3=settings["torsion_weight"],
            )
        try:
            curve = ReverseEngineering.approxCurve(**kwargs)
            shape = curve.toShape()
        except Exception as exc:
            raise NativeMeshError(f"{target.source.Name} could not be fit to a B-spline curve.") from exc
        if shape.isNull():
            raise NativeMeshError("B-spline curve fitting produced an empty shape.")
        outputs.append(
            ProcessedReverseOutput(
                target.label,
                "part_shape",
                shape,
                target.placement,
                {
                    "curve_degree": int(curve.Degree),
                    "control_point_count": len(curve.getPoles()),
                    "closed": bool(settings["closed"]),
                    "fit_mode": settings["mode"],
                },
            )
        )
        progress(90, "B-spline curve fitted")
        return ProcessedReversePlan(prepared, tuple(outputs))

    raise NativeMeshError("The requested Reverse Engineering operation is unavailable.")
