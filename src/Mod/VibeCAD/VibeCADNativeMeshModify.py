# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact retained Mesh modification preparation, creation, and proof."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeMeshComponents import resolve_component_facets
from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshState import mesh_geometry_sha256, mesh_object_state
from VibeCADNativeMeshTargets import (
    PreparedMeshTarget,
    is_active_mesh_input as _active,
    is_live as _live,
    mesh_target_still_exact as _still_exact,
    prepare_mesh_target as _prepare_target,
    prepare_mesh_targets as _prepare_targets,
    replace_mesh_target as _replace_target,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import (
    object_identity,
    object_reference,
)


MAX_EXPANDED_SMOOTHING_POINTS = 250_000

_FEATURES = {
    "harmonize_normals": ("Mesh::HarmonizeNormals", "HarmonizedNormals"),
    "flip_normals": ("Mesh::FlipNormals", "FlippedNormals"),
    "fill_holes": ("Mesh::FillHoles", "FilledHoles"),
    "fill_boundary": ("Mesh::FacetEdit", "FilledBoundary"),
    "add_triangle": ("Mesh::FacetEdit", "AddedTriangle"),
    "remove_components": ("Mesh::FacetEdit", "RemovedComponents"),
    "smooth": ("Mesh::Smoothing", "Smoothing"),
    "decimate": ("Mesh::Decimation", "Decimation"),
    "scale": ("Mesh::Scale", "Scale"),
    "gmsh_remesh": ("Mesh::GmshRemesh", "GmshRemesh"),
}
_OPERATION_LABELS = {
    "harmonize_normals": "Harmonize mesh normals",
    "flip_normals": "Flip mesh normals",
    "fill_holes": "Fill mesh holes",
    "fill_boundary": "Fill mesh boundary",
    "add_triangle": "Add mesh triangle",
    "remove_components": "Remove mesh components",
    "smooth": "Smooth mesh",
    "decimate": "Decimate mesh",
    "scale": "Scale mesh",
    "gmsh_remesh": "Gmsh remesh",
}


@dataclass(frozen=True, slots=True)
class PreparedMeshModification:
    operation: str
    targets: tuple[PreparedMeshTarget, ...]
    settings: Mapping[str, Any]


def _finite(value: Any, field: str) -> float:
    if type(value) not in {int, float}:
        raise NativeMeshError(f"{field} must be one finite number.")
    result = float(value)
    if not math.isfinite(result):
        raise NativeMeshError(f"{field} must be one finite number.")
    return result


def _expand_point_selection(selection: Any, point_count: int) -> tuple[int, ...]:
    if not isinstance(selection, Mapping):
        raise NativeMeshError("selection must use all, point_indices, or point_ranges.")
    kind = str(selection.get("kind") or "")
    if kind == "all" and set(selection) == {"kind"}:
        return ()
    if kind == "point_indices" and set(selection) == {"kind", "point_indices"}:
        raw_indices = selection.get("point_indices")
        if not isinstance(raw_indices, list) or not raw_indices:
            raise NativeMeshError("point_indices must contain exact zero-based indices.")
        indices = tuple(raw_indices)
    elif kind == "point_ranges" and set(selection) == {"kind", "ranges"}:
        ranges = selection.get("ranges")
        if not isinstance(ranges, list) or not ranges:
            raise NativeMeshError("ranges must contain exact inclusive point ranges.")
        expanded: list[int] = []
        for item in ranges:
            if not isinstance(item, Mapping) or set(item) != {"first_index", "last_index"}:
                raise NativeMeshError(
                    "Every point range must contain first_index and last_index."
                )
            first = item["first_index"]
            last = item["last_index"]
            if type(first) is not int or type(last) is not int or first < 0 or last < first:
                raise NativeMeshError(
                    "Every point range must be an ordered inclusive zero-based range."
                )
            if len(expanded) + last - first + 1 > MAX_EXPANDED_SMOOTHING_POINTS:
                raise NativeMeshError(
                    "The smoothing selection expands beyond 250000 points; use a smaller region."
                )
            expanded.extend(range(first, last + 1))
        indices = tuple(expanded)
    else:
        raise NativeMeshError("selection must use all, point_indices, or point_ranges exactly.")
    if len(indices) > MAX_EXPANDED_SMOOTHING_POINTS:
        raise NativeMeshError("The smoothing selection exceeds 250000 points.")
    if any(type(index) is not int or index < 0 or index >= point_count for index in indices):
        raise NativeMeshError("A smoothing point index is outside the exact source Mesh.")
    if len(indices) != len(set(indices)):
        raise NativeMeshError("A smoothing selection must not repeat or overlap point indices.")
    return tuple(sorted(indices))


def _require_changed_trial(target: PreparedMeshTarget, operation: str, setting: int = 0) -> None:
    trial = target.source.Mesh.copy()
    before = target.source_geometry_sha256
    try:
        if operation == "harmonize_normals":
            trial.harmonizeNormals()
        elif operation == "fill_holes":
            trial.fillupHoles(setting, 0)
        else:
            raise AssertionError(operation)
    except Exception as exc:
        raise NativeMeshError(f"The {operation} preflight failed on the exact Mesh.") from exc
    if mesh_geometry_sha256(trial) == before:
        message = (
            "The exact Mesh already has coherent facet normals."
            if operation == "harmonize_normals"
            else "No boundary within maximum_boundary_edges can be filled on the exact Mesh."
        )
        raise NativeMeshError(message, error_code="NATIVE_MESH_OPERATION_NO_CHANGE")


def prepare_mesh_modification(
    document: Any,
    document_uid: str,
    operation: str,
    values: Mapping[str, Any],
) -> PreparedMeshModification:
    if operation not in _FEATURES or operation == "gmsh_remesh":
        raise NativeMeshError("The requested immediate Mesh modification is unavailable.")
    settings: dict[str, Any] = {}
    if operation in {
        "harmonize_normals",
        "flip_normals",
        "fill_holes",
        "smooth",
        "decimate",
        "scale",
    }:
        targets = _prepare_targets(
            document,
            document_uid,
            values["targets"],
            extra_keys=("selection",) if operation == "smooth" else (),
        )
    else:
        targets = (_prepare_target(document, document_uid, values["target"]),)

    if operation == "harmonize_normals":
        for target in targets:
            _require_changed_trial(target, operation)
    elif operation == "fill_holes":
        maximum = values["maximum_boundary_edges"]
        if type(maximum) is not int or not 3 <= maximum <= 10_000:
            raise NativeMeshError("maximum_boundary_edges must be between 3 and 10000.")
        settings["maximum_boundary_edges"] = maximum
        for target in targets:
            _require_changed_trial(target, operation, maximum)
    elif operation == "fill_boundary":
        seed = values["seed_facet_index"]
        level = values["refinement_level"]
        if type(seed) is not int or not 0 <= seed < int(targets[0].topology["facets"]):
            raise NativeMeshError("seed_facet_index is outside the exact source Mesh.")
        if type(level) is not int or not 0 <= level <= 10:
            raise NativeMeshError("refinement_level must be between 0 and 10.")
        settings.update(seed_facet_index=seed, refinement_level=level)
    elif operation == "add_triangle":
        raw = values["point_indices"]
        if not isinstance(raw, list) or len(raw) != 3 or len(set(raw)) != 3:
            raise NativeMeshError("point_indices must contain three distinct point indices.")
        point_count = int(targets[0].topology["points"])
        if any(type(index) is not int or index < 0 or index >= point_count for index in raw):
            raise NativeMeshError("An Add Triangle point index is outside the exact source Mesh.")
        targets = (_replace_target(targets[0], point_indices=tuple(raw)),)
    elif operation == "remove_components":
        facets = resolve_component_facets(targets[0].source.Mesh, values["selection"])
        targets = (_replace_target(targets[0], facet_indices=facets),)
        settings["removed_facet_count"] = len(facets)
    elif operation == "smooth":
        prepared_targets = []
        for target, raw in zip(targets, values["targets"]):
            indices = _expand_point_selection(raw["selection"], int(target.topology["points"]))
            prepared_targets.append(_replace_target(target, point_indices=indices))
        targets = tuple(prepared_targets)
        raw_settings = values["settings"]
        if not isinstance(raw_settings, Mapping):
            raise NativeMeshError("settings must identify one smoothing method exactly.")
        method = str(raw_settings.get("method") or "")
        iterations = raw_settings.get("iterations")
        if method not in {"taubin", "laplace", "median"}:
            raise NativeMeshError("method must be taubin, laplace, or median.")
        if type(iterations) is not int or not 1 <= iterations <= 10_000:
            raise NativeMeshError("iterations must be between 1 and 10000.")
        settings.update(method=method, iterations=iterations)
        if method in {"taubin", "laplace"}:
            settings["lambda"] = _finite(raw_settings.get("lambda"), "lambda")
        if method == "taubin":
            settings["mu"] = _finite(raw_settings.get("mu"), "mu")
    elif operation == "decimate":
        raw_settings = values["settings"]
        if not isinstance(raw_settings, Mapping):
            raise NativeMeshError("settings must identify one decimation mode exactly.")
        mode = str(raw_settings.get("mode") or "")
        if mode == "target_facets":
            target_count = raw_settings.get("target_facet_count")
            if type(target_count) is not int or target_count < 1:
                raise NativeMeshError("target_facet_count must be a positive integer.")
            if any(target_count >= int(target.topology["facets"]) for target in targets):
                raise NativeMeshError(
                    "target_facet_count must be smaller than every exact source Mesh."
                )
            settings.update(mode=mode, target_facet_count=target_count)
        elif mode == "percentage":
            reduction = _finite(raw_settings.get("reduction_percent"), "reduction_percent")
            tolerance = _finite(raw_settings.get("tolerance_mm"), "tolerance_mm")
            if not 0.0 < reduction < 100.0 or tolerance < 0.0:
                raise NativeMeshError(
                    "percentage decimation needs 0 < reduction_percent < 100 and tolerance_mm >= 0."
                )
            settings.update(
                mode=mode,
                reduction_percent=reduction,
                tolerance_mm=tolerance,
            )
        else:
            raise NativeMeshError("mode must be target_facets or percentage.")
    elif operation == "scale":
        factor = _finite(values["factor"], "factor")
        if factor <= 0.0 or factor == 1.0:
            raise NativeMeshError("factor must be positive and different from 1.")
        maximum = 3.4028234663852886e38
        for target in targets:
            points, _facets = target.source.Mesh.Topology
            for point in points:
                if any(
                    not math.isfinite(float(value) * factor)
                    or abs(float(value) * factor) > maximum
                    for value in (point.x, point.y, point.z)
                ):
                    raise NativeMeshError("factor would create invalid Mesh coordinates.")
        settings["factor"] = factor
    return PreparedMeshModification(operation, targets, settings)


def _configure_result(result: Any, prepared: PreparedMeshModification, target: PreparedMeshTarget) -> None:
    operation = prepared.operation
    settings = prepared.settings
    if operation == "fill_holes":
        result.FillupHolesOfLength = settings["maximum_boundary_edges"]
        result.Method = "Flat"
    elif operation == "fill_boundary":
        result.Action = "Fill Hole"
        result.SeedFacet = settings["seed_facet_index"]
        result.Level = settings["refinement_level"]
        result.AcceptedSource = target.source.Mesh
    elif operation == "add_triangle":
        result.Action = "Add Triangle"
        result.Indices = list(target.point_indices)
        result.AcceptedSource = target.source.Mesh
    elif operation == "remove_components":
        result.Action = "Remove Facets"
        result.Indices = list(target.facet_indices)
        result.AcceptedSource = target.source.Mesh
    elif operation == "smooth":
        result.Method = {"taubin": "Taubin", "laplace": "Laplace", "median": "Median"}[
            settings["method"]
        ]
        result.Iterations = settings["iterations"]
        if "lambda" in settings:
            result.Lambda = settings["lambda"]
        if "mu" in settings:
            result.Mu = settings["mu"]
        result.PointIndices = list(target.point_indices)
        if target.point_indices:
            result.SelectionSource = target.source.Mesh
    elif operation == "decimate":
        absolute = settings["mode"] == "target_facets"
        result.UseTargetFacetCount = absolute
        if absolute:
            result.TargetFacetCount = settings["target_facet_count"]
        else:
            result.Tolerance = settings["tolerance_mm"]
            result.Reduction = settings["reduction_percent"]
    elif operation == "scale":
        result.Factor = settings["factor"]


def create_mesh_modification(
    document: Any,
    prepared: PreparedMeshModification,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedMeshModification):
        raise TypeError("prepared must be a PreparedMeshModification")
    if any(not _still_exact(document, target) for target in prepared.targets):
        raise NativeMeshError(
            "An exact Mesh changed after modification preflight.",
            error_code="NATIVE_MESH_STATE_STALE",
        )
    import Mesh  # noqa: F401 - registers retained Mesh operation types
    import MeshGui

    type_id, base_name = _FEATURES[prepared.operation]
    results = []
    for target in prepared.targets:
        result = document.addObject(type_id, document.getUniqueObjectName(base_name))
        if result is None or not bool(result.isDerivedFrom("Mesh::Feature")):
            raise NativeMeshError("The retained Mesh operation could not be created.")
        result.Label = target.label
        result.Source = target.source
        _configure_result(result, prepared, target)
        results.append(result)
    group = MeshGui.publishReplacingOutputs(
        str(document.Name),
        [target.source for target in prepared.targets],
        results,
        base_name + "Results",
        _OPERATION_LABELS[prepared.operation].title(),
        _OPERATION_LABELS[prepared.operation],
    )
    created_objects = [*results, *([group] if group is not None else [])]
    return NativeMutationDraft(
        value={"prepared": prepared, "results": tuple(results), "group": group},
        recompute_targets=tuple(created_objects),
        created=tuple(object_identity(obj) for obj in created_objects),
        replaced=tuple(object_identity(target.source) for target in prepared.targets),
    )


def _history_postcondition(
    document: Any,
    prepared: PreparedMeshModification,
    results: tuple[Any, ...],
    group: Any | None,
) -> bool:
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None:
        return False
    operations = list(getattr(timeline, "Operations", ()) or ())
    sources = tuple(target.source for target in prepared.targets)
    if group is None:
        return (
            len(results) == 1
            and operations.count(results[0]) == 1
            and str(getattr(results[0], "VibeCADTimelineRole", "") or "") == "operation"
            and getattr(results[0], "VibeCADTimelineOwner", None) is None
            and tuple(getattr(results[0], "VibeCADTimelineReplacedInputs", ()) or ())
            == ((sources[0],) if prepared.targets[0].source_visible else ())
        )
    return (
        _live(document, group)
        and str(getattr(group, "TypeId", "")) == "Mesh::OutputGroup"
        and operations.count(group) == 1
        and str(getattr(group, "VibeCADTimelineRole", "") or "") == "operation"
        and getattr(group, "VibeCADTimelineOwner", None) is None
        and tuple(getattr(group, "Sources", ()) or ()) == sources
        and tuple(getattr(group, "Group", ()) or ()) == results
        and str(getattr(group, "InputMode", "") or "") == "Replacement"
        and str(getattr(group, "OperationKind", "") or "")
        == _OPERATION_LABELS[prepared.operation]
        and tuple(getattr(group, "VibeCADTimelineReplacedInputs", ()) or ())
        == tuple(target.source for target in prepared.targets if target.source_visible)
        and all(
            str(getattr(result, "VibeCADTimelineRole", "") or "") == "resource"
            and getattr(result, "VibeCADTimelineOwner", None) is group
            for result in results
        )
    )


def _quantity_value(value: Any) -> float:
    return float(getattr(value, "Value", value))


def _settings_postcondition(
    prepared: PreparedMeshModification,
    target: PreparedMeshTarget,
    result: Any,
) -> bool:
    operation = prepared.operation
    settings = prepared.settings
    if str(getattr(result, "TypeId", "")) != _FEATURES[operation][0]:
        return False
    if operation == "fill_holes":
        return (
            int(result.FillupHolesOfLength) == settings["maximum_boundary_edges"]
            and str(result.Method) == "Flat"
        )
    if operation == "fill_boundary":
        return (
            str(result.Action) == "Fill Hole"
            and int(result.SeedFacet) == settings["seed_facet_index"]
            and int(result.Level) == settings["refinement_level"]
            and mesh_geometry_sha256(result.AcceptedSource)
            == target.source_geometry_sha256
        )
    if operation == "add_triangle":
        return (
            str(result.Action) == "Add Triangle"
            and tuple(int(value) for value in result.Indices) == target.point_indices
            and mesh_geometry_sha256(result.AcceptedSource)
            == target.source_geometry_sha256
        )
    if operation == "remove_components":
        return (
            str(result.Action) == "Remove Facets"
            and tuple(int(value) for value in result.Indices) == target.facet_indices
            and mesh_geometry_sha256(result.AcceptedSource)
            == target.source_geometry_sha256
        )
    if operation == "smooth":
        expected_method = {
            "taubin": "Taubin",
            "laplace": "Laplace",
            "median": "Median",
        }[settings["method"]]
        return (
            str(result.Method) == expected_method
            and int(result.Iterations) == settings["iterations"]
            and tuple(int(value) for value in result.PointIndices) == target.point_indices
            and (
                "lambda" not in settings
                or math.isclose(float(result.Lambda), settings["lambda"], rel_tol=1e-6)
            )
            and (
                "mu" not in settings
                or math.isclose(float(result.Mu), settings["mu"], rel_tol=1e-6)
            )
            and (
                not target.point_indices
                or mesh_geometry_sha256(result.SelectionSource)
                == target.source_geometry_sha256
            )
        )
    if operation == "decimate":
        absolute = settings["mode"] == "target_facets"
        return (
            bool(result.UseTargetFacetCount) is absolute
            and (
                not absolute
                or int(result.TargetFacetCount) == settings["target_facet_count"]
            )
            and (
                absolute
                or (
                    math.isclose(float(result.Tolerance), settings["tolerance_mm"], rel_tol=1e-6)
                    and math.isclose(
                        float(result.Reduction),
                        settings["reduction_percent"],
                        rel_tol=1e-6,
                    )
                )
            )
        )
    if operation == "scale":
        return math.isclose(float(result.Factor), settings["factor"], rel_tol=1e-6)
    if operation == "gmsh_remesh":
        return (
            int(result.Algorithm) == settings["algorithm_id"]
            and math.isclose(
                _quantity_value(result.MinimumElementSize),
                settings["minimum_element_size_mm"],
                rel_tol=1e-9,
                abs_tol=1e-9,
            )
            and math.isclose(
                _quantity_value(result.MaximumElementSize),
                settings["maximum_element_size_mm"],
                rel_tol=1e-9,
                abs_tol=1e-9,
            )
            and math.isclose(
                _quantity_value(result.SurfaceAngle),
                settings["surface_angle_degrees"],
                rel_tol=1e-9,
                abs_tol=1e-9,
            )
            and int(result.TimeoutSeconds) == settings["timeout_seconds"]
            and str(result.Executable) == settings["_executable"]
            and mesh_geometry_sha256(result.CachedSource)
            == target.source_geometry_sha256
            and mesh_geometry_sha256(result.CachedResult)
            == settings["_accepted_result_sha256"]
        )
    return True


def verify_mesh_modification(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    value = draft.value
    prepared = value["prepared"]
    results = value["results"]
    group = value["group"]
    if not isinstance(prepared, PreparedMeshModification):
        raise NativeMeshError("The Mesh modification lost its prepared state.")
    if len(results) != len(prepared.targets) or not _history_postcondition(
        document, prepared, results, group
    ):
        raise NativeMeshError("The Mesh modification failed its exact History postcondition.")
    summaries = []
    for target, result in zip(prepared.targets, results):
        status = str(getattr(result, "getStatusString", lambda: "")() or "").strip()
        output_mesh = getattr(result, "Mesh", None)
        output_facets = int(getattr(output_mesh, "CountFacets", 0) or 0)
        allow_empty = prepared.operation == "remove_components"
        if (
            not _live(document, target.source)
            or not _live(document, result)
            or getattr(result, "Source", None) is not target.source
            or str(getattr(result, "Label", "")) != target.label
            or not _settings_postcondition(prepared, target, result)
            or not bool(result.isValid())
            or (not allow_empty and output_facets < 1)
            or mesh_object_state(target.source).get("state_sha256")
            != target.expected_state_sha256
            or mesh_geometry_sha256(target.source.Mesh) != target.source_geometry_sha256
            or bool(target.source.Visibility)
        ):
            raise NativeMeshError(
                status
                if not bool(result.isValid())
                else "A retained Mesh result failed its exact postcondition."
            )
        if mesh_geometry_sha256(output_mesh) == target.source_geometry_sha256:
            raise NativeMeshError(
                "The retained Mesh operation did not change its source.",
                error_code="NATIVE_MESH_OPERATION_NO_CHANGE",
            )
        summaries.append(
            {
                "source": object_reference(target.source),
                "result": mesh_object_state(result),
            }
        )
    response: dict[str, Any] = {
        "operation": prepared.operation,
        "outputs": summaries,
        "settings": {
            name: setting
            for name, setting in prepared.settings.items()
            if not name.startswith("_")
        },
    }
    if group is not None:
        response["operation_controller"] = object_reference(group)
    return response
