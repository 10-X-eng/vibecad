# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared configuration for retained Mesh modification features."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


FEATURE_TYPES = {
    "repair": "Mesh::Repair",
    "harmonize_normals": "Mesh::HarmonizeNormals",
    "flip_normals": "Mesh::FlipNormals",
    "fill_holes": "Mesh::FillHoles",
    "fill_boundary": "Mesh::FacetEdit",
    "add_triangle": "Mesh::FacetEdit",
    "remove_components": "Mesh::FacetEdit",
    "smooth": "Mesh::Smoothing",
    "decimate": "Mesh::Decimation",
    "scale": "Mesh::Scale",
}

_REPAIR_PROPERTIES = {
    "orientation": "HarmonizeNormals",
    "duplicates": "RemoveDuplicates",
    "non_manifold_topology": "RemoveNonManifolds",
    "indices": "FixIndices",
    "degeneracies": "FixDegenerations",
    "self_intersections": "FixSelfIntersections",
    "surface_folds": "RemoveFolds",
}


def configure_mesh_feature(
    result: Any,
    *,
    source_mesh: Any,
    operation: str,
    settings: Mapping[str, Any],
    point_indices: Sequence[int] = (),
    facet_indices: Sequence[int] = (),
) -> None:
    """Apply one already-validated operation contract to its retained feature."""

    if operation == "repair":
        repairs = frozenset(str(value) for value in settings["repairs"])
        for name, property_name in _REPAIR_PROPERTIES.items():
            setattr(result, property_name, name in repairs)
        result.RemoveNonManifoldPoints = "non_manifold_topology" in repairs
        result.FillHolesMaxEdges = int(settings["maximum_boundary_edges"])
        iterations = int(settings["max_iterations"])
        result.Repeat = iterations > 1
        result.MaxIterations = iterations
    elif operation == "fill_holes":
        result.FillupHolesOfLength = int(settings["maximum_boundary_edges"])
        result.Method = "Flat"
    elif operation == "fill_boundary":
        result.Action = "Fill Hole"
        result.SeedFacet = int(settings["seed_facet_index"])
        result.Level = int(settings["refinement_level"])
        result.AcceptedSource = source_mesh
    elif operation == "add_triangle":
        result.Action = "Add Triangle"
        result.Indices = [int(value) for value in point_indices]
        result.AcceptedSource = source_mesh
    elif operation == "remove_components":
        result.Action = "Remove Facets"
        result.Indices = [int(value) for value in facet_indices]
        result.AcceptedSource = source_mesh
    elif operation == "smooth":
        result.Method = {
            "taubin": "Taubin",
            "laplace": "Laplace",
            "median": "Median",
        }[str(settings["method"])]
        result.Iterations = int(settings["iterations"])
        if "lambda" in settings:
            result.Lambda = float(settings["lambda"])
        if "mu" in settings:
            result.Mu = float(settings["mu"])
        result.PointIndices = [int(value) for value in point_indices]
        if point_indices:
            result.SelectionSource = source_mesh
    elif operation == "decimate":
        absolute = settings["mode"] == "target_facets"
        result.UseTargetFacetCount = bool(absolute)
        if absolute:
            result.TargetFacetCount = int(settings["target_facet_count"])
        else:
            result.Tolerance = float(settings["tolerance_mm"])
            value = float(settings["reduction_percent"])
            result.Reduction = int(round(value))
            result.PreciseReduction = value
            result.UsePreciseReduction = True
    elif operation == "scale":
        result.Factor = float(settings["factor"])
    elif operation not in {"harmonize_normals", "flip_normals"}:
        raise ValueError(f"Unsupported Mesh modification operation: {operation}")
