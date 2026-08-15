# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact preparation for retained Mesh merge and segmentation operations."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeMeshComponents import mesh_components
from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshTargets import PreparedMeshTarget, prepare_mesh_target


MAX_EXPLICIT_FACETS = 250_000


@dataclass(frozen=True, slots=True)
class PreparedSegmentOutput:
    target: PreparedMeshTarget
    facet_indices: tuple[int, ...]
    label: str
    kind: str


@dataclass(frozen=True, slots=True)
class PreparedMeshSegment:
    operation: str
    targets: tuple[PreparedMeshTarget, ...]
    outputs: tuple[PreparedSegmentOutput, ...]
    settings: Mapping[str, Any]


def _label(value: Any, field: str = "label") -> str:
    result = str(value or "").strip()
    if not result or len(result) > 160:
        raise NativeMeshError(f"{field} must contain 1 to 160 visible characters.")
    return result


def _finite(value: Any, field: str, *, nonnegative: bool = False) -> float:
    if type(value) not in {int, float}:
        raise NativeMeshError(f"{field} must be one finite number.")
    result = float(value)
    if not math.isfinite(result) or (nonnegative and result < 0.0):
        qualifier = "finite and non-negative" if nonnegative else "finite"
        raise NativeMeshError(f"{field} must be {qualifier}.")
    return result


def _positive_int(value: Any, field: str, maximum: int = 2_147_483_647) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise NativeMeshError(f"{field} must be between 1 and {maximum}.")
    return value


def _vector(value: Any, field: str, *, point: bool = False) -> tuple[float, float, float]:
    keys = ("x_mm", "y_mm", "z_mm") if point else ("x", "y", "z")
    if not isinstance(value, Mapping) or set(value) != set(keys):
        raise NativeMeshError(f"{field} must contain only {', '.join(keys)}.")
    result = tuple(_finite(value[key], f"{field}.{key}") for key in keys)
    if not point and math.sqrt(sum(number * number for number in result)) <= 1.0e-15:
        raise NativeMeshError(f"{field} must be nonzero.")
    return result  # type: ignore[return-value]


def _exact_targets(
    document: Any,
    document_uid: str,
    values: Any,
    *,
    minimum: int = 1,
) -> tuple[PreparedMeshTarget, ...]:
    if not isinstance(values, list) or not minimum <= len(values) <= 32:
        raise NativeMeshError(f"targets must contain {minimum} to 32 exact Meshes.")
    targets = tuple(
        prepare_mesh_target(document, document_uid, value, require_label=False)
        for value in values
    )
    names = tuple(str(target.source.Name) for target in targets)
    if len(names) != len(set(names)):
        raise NativeMeshError("Exact Mesh targets must not repeat an object.")
    return targets


def _detected_outputs(
    target: PreparedMeshTarget,
    detected: Any,
    prefix: str,
) -> tuple[PreparedSegmentOutput, ...]:
    if not isinstance(detected, list) or not detected:
        raise NativeMeshError(
            "The exact Mesh and settings did not produce any surface segments.",
            error_code="NATIVE_MESH_SEGMENTATION_EMPTY",
        )
    facet_count = int(target.topology["facets"])
    used: set[int] = set()
    kind_counts: dict[str, int] = {}
    outputs = []
    for raw in detected:
        if not isinstance(raw, Mapping) or set(raw) != {"kind", "facet_indices"}:
            raise NativeMeshError("The native segmentation detector returned invalid data.")
        kind = str(raw["kind"] or "").strip()
        values = raw["facet_indices"]
        if not kind or not isinstance(values, list) or not values:
            raise NativeMeshError("The native segmentation detector returned an empty segment.")
        facets = tuple(int(value) for value in values)
        if (
            any(type(value) is not int for value in values)
            or len(facets) != len(set(facets))
            or any(value < 0 or value >= facet_count for value in facets)
            or used.intersection(facets)
        ):
            raise NativeMeshError("The native segmentation detector returned stale facet indices.")
        used.update(facets)
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        outputs.append(
            PreparedSegmentOutput(
                target,
                tuple(sorted(facets)),
                f"{prefix} {kind} {kind_counts[kind]}",
                kind,
            )
        )
    return tuple(outputs)


def _curvature_requests(values: Any) -> tuple[tuple[Any, ...], ...]:
    if not isinstance(values, list) or not 1 <= len(values) <= 4:
        raise NativeMeshError("surfaces must contain 1 to 4 curvature requests.")
    requests = []
    kinds = []
    for value in values:
        if not isinstance(value, Mapping):
            raise NativeMeshError("Every curvature surface must be one exact typed object.")
        kind = str(value.get("kind") or "")
        kinds.append(kind)
        minimum = _positive_int(value.get("minimum_facets"), "minimum_facets")
        if kind == "plane" and set(value) == {
            "kind",
            "minimum_facets",
            "curvature_tolerance",
        }:
            parameters = (
                _finite(value["curvature_tolerance"], "curvature_tolerance", nonnegative=True),
            )
            native_kind = "Plane"
        elif kind == "cylinder" and set(value) == {
            "kind",
            "minimum_facets",
            "curvature_per_mm",
            "flat_curvature_tolerance",
            "curved_curvature_tolerance",
        }:
            parameters = (
                _finite(value["curvature_per_mm"], "curvature_per_mm", nonnegative=True),
                _finite(
                    value["flat_curvature_tolerance"],
                    "flat_curvature_tolerance",
                    nonnegative=True,
                ),
                _finite(
                    value["curved_curvature_tolerance"],
                    "curved_curvature_tolerance",
                    nonnegative=True,
                ),
            )
            native_kind = "Cylinder"
        elif kind == "sphere" and set(value) == {
            "kind",
            "minimum_facets",
            "curvature_per_mm",
            "curvature_tolerance",
        }:
            parameters = (
                _finite(value["curvature_per_mm"], "curvature_per_mm", nonnegative=True),
                _finite(value["curvature_tolerance"], "curvature_tolerance", nonnegative=True),
            )
            native_kind = "Sphere"
        elif kind == "freeform" and set(value) == {
            "kind",
            "minimum_facets",
            "maximum_curvature_per_mm",
            "minimum_curvature_per_mm",
            "maximum_curvature_tolerance",
            "minimum_curvature_tolerance",
        }:
            parameters = (
                _finite(value["maximum_curvature_per_mm"], "maximum_curvature_per_mm"),
                _finite(value["minimum_curvature_per_mm"], "minimum_curvature_per_mm"),
                _finite(
                    value["maximum_curvature_tolerance"],
                    "maximum_curvature_tolerance",
                    nonnegative=True,
                ),
                _finite(
                    value["minimum_curvature_tolerance"],
                    "minimum_curvature_tolerance",
                    nonnegative=True,
                ),
            )
            native_kind = "Freeform"
        else:
            raise NativeMeshError(
                "Each curvature surface must exactly match plane, cylinder, sphere, or freeform."
            )
        requests.append((native_kind, minimum, parameters))
    if len(kinds) != len(set(kinds)):
        raise NativeMeshError("surfaces must not repeat a curvature surface kind.")
    return tuple(requests)


def _best_fit_requests(values: Any) -> tuple[tuple[Any, ...], ...]:
    if not isinstance(values, list) or not 1 <= len(values) <= 3:
        raise NativeMeshError("surfaces must contain 1 to 3 best-fit requests.")
    requests = []
    kinds = []
    for value in values:
        if not isinstance(value, Mapping):
            raise NativeMeshError("Every best-fit surface must be one exact typed object.")
        kind = str(value.get("kind") or "")
        if kind not in {"plane", "cylinder", "sphere"}:
            raise NativeMeshError("Best-fit kind must be plane, cylinder, or sphere.")
        allowed = {"kind", "minimum_facets", "distance_tolerance_mm", "initial"}
        if not set(value).issubset(allowed) or set(value) - {"initial"} != allowed - {"initial"}:
            raise NativeMeshError("A best-fit surface contains unsupported fields.")
        kinds.append(kind)
        minimum = _positive_int(value["minimum_facets"], "minimum_facets")
        tolerance = _finite(
            value["distance_tolerance_mm"],
            "distance_tolerance_mm",
            nonnegative=True,
        )
        initial = value.get("initial")
        parameters: tuple[float, ...] = ()
        if initial is not None:
            if not isinstance(initial, Mapping):
                raise NativeMeshError("initial must exactly describe the selected surface.")
            if kind == "plane" and set(initial) == {"point_mm", "normal"}:
                parameters = (*_vector(initial["point_mm"], "initial.point_mm", point=True), *_vector(initial["normal"], "initial.normal"))
            elif kind == "cylinder" and set(initial) == {"base_mm", "axis", "radius_mm"}:
                radius = _finite(initial["radius_mm"], "initial.radius_mm", nonnegative=True)
                if radius <= 0.0:
                    raise NativeMeshError("initial.radius_mm must be positive.")
                parameters = (*_vector(initial["base_mm"], "initial.base_mm", point=True), *_vector(initial["axis"], "initial.axis"), radius)
            elif kind == "sphere" and set(initial) == {"center_mm", "radius_mm"}:
                radius = _finite(initial["radius_mm"], "initial.radius_mm", nonnegative=True)
                if radius <= 0.0:
                    raise NativeMeshError("initial.radius_mm must be positive.")
                parameters = (*_vector(initial["center_mm"], "initial.center_mm", point=True), radius)
            else:
                raise NativeMeshError("initial does not match its best-fit surface kind.")
        requests.append((kind.title(), minimum, tolerance, parameters or None))
    if len(kinds) != len(set(kinds)):
        raise NativeMeshError("surfaces must not repeat a best-fit surface kind.")
    return tuple(requests)


def _facet_selection(value: Any, facet_count: int) -> tuple[int, ...]:
    if not isinstance(value, Mapping):
        raise NativeMeshError("selection must use facet_indices or facet_ranges.")
    kind = str(value.get("kind") or "")
    if kind == "facet_indices" and set(value) == {"kind", "facet_indices"}:
        raw = value["facet_indices"]
        if not isinstance(raw, list) or not raw or len(raw) > 256:
            raise NativeMeshError("facet_indices must contain 1 to 256 exact indices.")
        facets = tuple(raw)
    elif kind == "facet_ranges" and set(value) == {"kind", "ranges"}:
        ranges = value["ranges"]
        if not isinstance(ranges, list) or not 1 <= len(ranges) <= 256:
            raise NativeMeshError("ranges must contain 1 to 256 exact inclusive ranges.")
        expanded = []
        for item in ranges:
            if not isinstance(item, Mapping) or set(item) != {"first_index", "last_index"}:
                raise NativeMeshError("Every facet range needs first_index and last_index.")
            first, last = item["first_index"], item["last_index"]
            if type(first) is not int or type(last) is not int or first < 0 or last < first:
                raise NativeMeshError("Every facet range must be ordered and zero-based.")
            if len(expanded) + last - first + 1 > MAX_EXPLICIT_FACETS:
                raise NativeMeshError("The facet ranges expand beyond 250000 facets.")
            expanded.extend(range(first, last + 1))
        facets = tuple(expanded)
    else:
        raise NativeMeshError("selection must use facet_indices or facet_ranges exactly.")
    if (
        any(type(index) is not int or index < 0 or index >= facet_count for index in facets)
        or len(facets) != len(set(facets))
    ):
        raise NativeMeshError("The selection repeats or exceeds exact source facet indices.")
    return tuple(sorted(facets))


def prepare_mesh_segment(
    document: Any,
    document_uid: str,
    operation: str,
    values: Mapping[str, Any],
) -> PreparedMeshSegment:
    if operation == "merge":
        targets = _exact_targets(document, document_uid, values["sources"], minimum=2)
        return PreparedMeshSegment(
            operation,
            targets,
            (),
            {"result_label": _label(values["result_label"], "result_label")},
        )

    if operation == "mesh_boundary":
        raw_targets = values["targets"]
        if not isinstance(raw_targets, list) or not 1 <= len(raw_targets) <= 32:
            raise NativeMeshError("targets must contain 1 to 32 labeled exact Meshes.")
        targets = tuple(
            prepare_mesh_target(document, document_uid, value)
            for value in raw_targets
        )
        names = tuple(str(target.source.Name) for target in targets)
        if len(names) != len(set(names)):
            raise NativeMeshError("Boundary targets must not repeat a Mesh.")
        make_faces = values["make_faces_when_closed"]
        if type(make_faces) is not bool:
            raise NativeMeshError("make_faces_when_closed must be true or false.")
        return PreparedMeshSegment(operation, targets, (), {"make_faces": make_faces})

    if operation == "segmentation_from_components":
        targets = _exact_targets(document, document_uid, values["targets"])
    else:
        targets = (
            prepare_mesh_target(document, document_uid, values["target"], require_label=False),
        )
    prefix = _label(values.get("result_label_prefix"), "result_label_prefix") if "result_label_prefix" in values else ""

    if operation in {"split_components", "segmentation_from_components"}:
        outputs = []
        split_targets = []
        for target in targets:
            components = mesh_components(target.source.Mesh)
            if len(components) <= 1:
                if operation == "split_components":
                    raise NativeMeshError(
                        "The exact Mesh has only one connected component.",
                        error_code="NATIVE_MESH_SINGLE_COMPONENT",
                    )
                continue
            split_targets.append(target)
            for index, component in enumerate(components, 1):
                outputs.append(
                    PreparedSegmentOutput(
                        target,
                        component.facet_indices,
                        f"{prefix} {index}" if len(targets) == 1 else f"{prefix} {target.source.Label} {index}",
                        "Connected component",
                    )
                )
        if not outputs:
            raise NativeMeshError("No selected Mesh contains multiple connected components.")
        return PreparedMeshSegment(operation, tuple(split_targets), tuple(outputs), {})

    target = targets[0]
    if operation == "segmentation_manual":
        facets = _facet_selection(values["selection"], int(target.topology["facets"]))
        result = values["result"]
        if not isinstance(result, Mapping):
            raise NativeMeshError("result must choose extract or split.")
        mode = str(result.get("mode") or "")
        selected = PreparedSegmentOutput(
            target,
            facets,
            _label(result.get("segment_label"), "segment_label"),
            "Manual selection",
        )
        outputs = [selected]
        if mode == "extract" and set(result) == {"mode", "segment_label"}:
            pass
        elif mode == "split" and set(result) == {"mode", "segment_label", "remainder_label"}:
            selected_set = set(facets)
            remainder = tuple(
                index for index in range(int(target.topology["facets"])) if index not in selected_set
            )
            if not remainder:
                raise NativeMeshError("split requires at least one unselected remainder facet.")
            outputs.append(
                PreparedSegmentOutput(
                    target,
                    remainder,
                    _label(result["remainder_label"], "remainder_label"),
                    "Manual selection remainder",
                )
            )
        else:
            raise NativeMeshError("result must match extract or split exactly.")
        return PreparedMeshSegment(operation, targets, tuple(outputs), {"mode": mode})

    try:
        import MeshGui

        if operation == "mesh_segmentation":
            smoothing = values["smoothing_steps"]
            if type(smoothing) is not int or not 0 <= smoothing <= 10_000:
                raise NativeMeshError("smoothing_steps must be between 0 and 10000.")
            requests = _curvature_requests(values["surfaces"])
            detected = MeshGui.detectCurvatureSegments(target.source, requests, smoothing)
            outputs = _detected_outputs(target, detected, prefix)
            settings = {"surface_requests": requests, "smoothing_steps": smoothing}
        elif operation == "segmentation_best_fit":
            requests = _best_fit_requests(values["surfaces"])
            detected = MeshGui.detectBestFitSegments(target.source, requests)
            outputs = _detected_outputs(target, detected, prefix)
            settings = {"surface_requests": requests}
        elif operation == "reverse_segmentation":
            minimum = _positive_int(values["minimum_facets"], "minimum_facets")
            curvature = _finite(values["curvature_tolerance"], "curvature_tolerance", nonnegative=True)
            distance = _finite(values["distance_tolerance_mm"], "distance_tolerance_mm", nonnegative=True)
            smoothing = values["smoothing_steps"]
            include_unused = values["include_unused_facets"]
            create_faces = values["create_boundary_faces"]
            if type(smoothing) is not int or not 0 <= smoothing <= 10_000:
                raise NativeMeshError("smoothing_steps must be between 0 and 10000.")
            if type(include_unused) is not bool or type(create_faces) is not bool:
                raise NativeMeshError("include_unused_facets and create_boundary_faces must be booleans.")
            detected = MeshGui.detectPlanarSegments(
                target.source,
                minimum,
                curvature,
                distance,
                smoothing,
            )
            outputs = list(_detected_outputs(target, detected, prefix)) if detected else []
            if include_unused:
                used = {index for output in outputs for index in output.facet_indices}
                unused = tuple(
                    index for index in range(int(target.topology["facets"])) if index not in used
                )
                if unused:
                    outputs.append(
                        PreparedSegmentOutput(target, unused, f"{prefix} Unused", "Unused facets")
                    )
            if not outputs:
                raise NativeMeshError(
                    "The exact Mesh and planar settings did not produce any segments.",
                    error_code="NATIVE_MESH_SEGMENTATION_EMPTY",
                )
            outputs = tuple(outputs)
            settings = {
                "minimum_facets": minimum,
                "curvature_tolerance": curvature,
                "distance_tolerance_mm": distance,
                "smoothing_steps": smoothing,
                "include_unused_facets": include_unused,
                "create_boundary_faces": create_faces,
            }
        else:
            raise NativeMeshError("The requested Mesh segment operation is unavailable.")
    except NativeMeshError:
        raise
    except Exception as exc:
        raise NativeMeshError("The native Mesh segmentation detector failed.") from exc
    return PreparedMeshSegment(operation, targets, tuple(outputs), settings)
