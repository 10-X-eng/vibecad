# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact summaries of OpenFOAM fields exported to legacy VTK."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Mapping


def _finite(value, name):
    number = float(value)
    if not math.isfinite(number):
        raise RuntimeError(f"OpenFOAM result {name} is not finite")
    return float(format(number, ".15g"))


def _one_file(paths, description):
    values = tuple(sorted(paths))
    if len(values) != 1:
        raise RuntimeError(
            f"OpenFOAM result export must contain exactly one {description}"
        )
    return values[0]


def _read_dataset(path):
    import vtk

    reader = vtk.vtkDataSetReader()
    reader.SetFileName(str(path))
    reader.ReadAllScalarsOn()
    reader.ReadAllVectorsOn()
    reader.Update()
    dataset = reader.GetOutput()
    if dataset is None or dataset.GetNumberOfCells() < 1:
        raise RuntimeError(f"OpenFOAM result dataset is empty: {path.name}")
    return dataset


def _array(attributes, name, components):
    value = attributes.GetArray(name)
    if value is None or int(value.GetNumberOfComponents()) != components:
        raise RuntimeError(
            f"OpenFOAM result dataset is missing {components}-component field {name}"
        )
    return value


def _speed_range(dataset):
    values = []
    for attributes in (dataset.GetPointData(), dataset.GetCellData()):
        field = attributes.GetArray("U")
        if field is None or int(field.GetNumberOfComponents()) != 3:
            continue
        for index in range(int(field.GetNumberOfTuples())):
            vector = field.GetTuple3(index)
            speed = math.sqrt(sum(float(component) ** 2 for component in vector))
            values.append(_finite(speed, "velocity"))
    if not values:
        raise RuntimeError("OpenFOAM result dataset is missing velocity field U")
    return min(values), max(values)


def _pressure_range(dataset, density):
    values = []
    for attributes in (dataset.GetPointData(), dataset.GetCellData()):
        field = attributes.GetArray("p")
        if field is None or int(field.GetNumberOfComponents()) != 1:
            continue
        for index in range(int(field.GetNumberOfTuples())):
            values.append(
                _finite(field.GetTuple1(index) * density, "pressure")
            )
    if not values:
        raise RuntimeError("OpenFOAM result dataset is missing pressure field p")
    return min(values), max(values)


def _triangle_area_vector(cell):
    if int(cell.GetNumberOfPoints()) != 3:
        return (0.0, 0.0, 0.0)
    points = [cell.GetPoints().GetPoint(index) for index in range(3)]
    first = tuple(points[1][axis] - points[0][axis] for axis in range(3))
    second = tuple(points[2][axis] - points[0][axis] for axis in range(3))
    return (
        0.5 * (first[1] * second[2] - first[2] * second[1]),
        0.5 * (first[2] * second[0] - first[0] * second[2]),
        0.5 * (first[0] * second[1] - first[1] * second[0]),
    )


def _triangle_area(cell):
    return math.sqrt(
        sum(component * component for component in _triangle_area_vector(cell))
    )


def _patch_average(path, density):
    import vtk

    triangle_filter = vtk.vtkTriangleFilter()
    triangle_filter.SetInputData(_read_dataset(path))
    triangle_filter.Update()
    dataset = triangle_filter.GetOutput()
    pressure = _array(dataset.GetCellData(), "p", 1)
    velocity = _array(dataset.GetCellData(), "U", 3)
    total_area = 0.0
    pressure_integral = 0.0
    velocity_integral = [0.0, 0.0, 0.0]
    volumetric_flow = 0.0
    for index in range(int(dataset.GetNumberOfCells())):
        cell = dataset.GetCell(index)
        area_vector = _triangle_area_vector(cell)
        area = math.sqrt(sum(component * component for component in area_vector))
        if area <= 0.0:
            continue
        total_area += area
        pressure_integral += float(pressure.GetTuple1(index)) * density * area
        vector = velocity.GetTuple3(index)
        volumetric_flow += sum(
            float(vector[axis]) * area_vector[axis] for axis in range(3)
        )
        for axis in range(3):
            velocity_integral[axis] += float(vector[axis]) * area
    if not math.isfinite(total_area) or total_area <= 0.0:
        raise RuntimeError(f"OpenFOAM boundary result has no positive area: {path.name}")
    return (
        _finite(total_area, "boundary area"),
        _finite(pressure_integral / total_area, "boundary pressure"),
        [
            _finite(component / total_area, "boundary velocity")
            for component in velocity_integral
        ],
        _finite(volumetric_flow, "boundary volumetric flow rate"),
    )


def _named_boundary(summary, name):
    boundaries = summary.get("boundaries") if isinstance(summary, Mapping) else None
    if not isinstance(boundaries, list):
        raise RuntimeError("OpenFOAM flow summary has no boundary results")
    matches = [boundary for boundary in boundaries if boundary.get("name") == name]
    if len(matches) != 1:
        available = ", ".join(
            sorted(str(boundary.get("name")) for boundary in boundaries)
        )
        raise RuntimeError(
            f"OpenFOAM boundary {name!r} was not found; available boundaries: {available}"
        )
    return matches[0]


def openfoam_flow_performance(
    summary,
    *,
    upstream_boundary,
    downstream_boundary,
    flow_boundary,
):
    """Measure one explicit passage from an incompressible flow summary."""

    upstream_name = str(upstream_boundary)
    downstream_name = str(downstream_boundary)
    flow_name = str(flow_boundary)
    if upstream_name == downstream_name:
        raise RuntimeError("Upstream and downstream boundaries must be different")
    if "density_kg_m3" not in summary:
        raise RuntimeError(
            "This result has no flow-performance data; run OpenFOAM again"
        )
    density = _finite(summary.get("density_kg_m3"), "density")
    if density <= 0.0:
        raise RuntimeError("OpenFOAM result density must be positive")
    upstream = _named_boundary(summary, upstream_name)
    downstream = _named_boundary(summary, downstream_name)
    section = _named_boundary(summary, flow_name)
    if any(
        "outward_volumetric_flow_rate_m3_s" not in boundary
        for boundary in (upstream, downstream, section)
    ):
        raise RuntimeError(
            "This result has no surface flow rates; run OpenFOAM again"
        )
    pressure_drop = _finite(
        float(upstream["pressure_area_average_pa"])
        - float(downstream["pressure_area_average_pa"]),
        "static pressure drop",
    )
    if pressure_drop <= 0.0:
        raise RuntimeError(
            "Upstream boundary pressure must exceed downstream boundary pressure"
        )
    if "geometric_area_m2" not in section:
        raise RuntimeError(
            "This result has no exact geometric flow area; run OpenFOAM again"
        )
    geometric_area = _finite(
        section["geometric_area_m2"], "geometric flow area"
    )
    if geometric_area <= 0.0:
        raise RuntimeError("Geometric flow area must be positive")
    volumetric_flow = abs(
        _finite(
            section["outward_volumetric_flow_rate_m3_s"],
            "volumetric flow rate",
        )
    )
    mass_flow = _finite(volumetric_flow * density, "mass flow rate")
    effective_area = _finite(
        volumetric_flow * math.sqrt(density / (2.0 * pressure_drop)),
        "effective flow area",
    )
    upstream_flow = abs(
        _finite(
            upstream["outward_volumetric_flow_rate_m3_s"],
            "upstream volumetric flow rate",
        )
    )
    downstream_flow = abs(
        _finite(
            downstream["outward_volumetric_flow_rate_m3_s"],
            "downstream volumetric flow rate",
        )
    )
    reference_flow = max(upstream_flow, downstream_flow)
    continuity_error = (
        0.0
        if reference_flow == 0.0
        else _finite(
            100.0 * abs(upstream_flow - downstream_flow) / reference_flow,
            "continuity error",
        )
    )
    return {
        "upstream_boundary": upstream_name,
        "downstream_boundary": downstream_name,
        "flow_boundary": flow_name,
        "density_kg_m3": density,
        "geometric_flow_area_m2": geometric_area,
        "volumetric_flow_rate_m3_s": volumetric_flow,
        "mass_flow_rate_kg_s": mass_flow,
        "static_pressure_drop_pa": pressure_drop,
        "effective_flow_area_m2": effective_area,
        "discharge_coefficient": _finite(
            effective_area / geometric_area, "discharge coefficient"
        ),
        "continuity_error_percent": continuity_error,
    }


def _operating_condition(summary, passage):
    if summary.get("converged") is not True:
        raise RuntimeError("OpenFOAM flow comparison requires a converged result")
    required = ("density_kg_m3", "kinematic_viscosity_m2_s")
    if any(name not in summary for name in required):
        raise RuntimeError(
            "This result has no comparable operating-condition data; run OpenFOAM again"
        )
    conditions = []
    for role in ("upstream_boundary", "downstream_boundary", "flow_boundary"):
        boundary = _named_boundary(summary, str(passage[role]))
        condition = boundary.get("condition")
        if not isinstance(condition, Mapping):
            raise RuntimeError(
                "This result has no comparable boundary-condition data; run OpenFOAM again"
            )
        normalized = {
            name: value
            for name, value in condition.items()
            if name not in {"turbulence", "turbulence_reference_speed_m_s"}
        }
        conditions.append((role, normalized))
    return {
        "density_kg_m3": _finite(summary["density_kg_m3"], "density"),
        "kinematic_viscosity_m2_s": _finite(
            summary["kinematic_viscosity_m2_s"], "kinematic viscosity"
        ),
        "passage_conditions": conditions,
    }


def _change(baseline, candidate, name):
    first = _finite(baseline[name], f"baseline {name}")
    second = _finite(candidate[name], f"candidate {name}")
    result = {"value": _finite(second - first, f"{name} change")}
    if first != 0.0:
        result["percent"] = _finite(
            100.0 * (second - first) / abs(first), f"{name} percent change"
        )
    return result


def openfoam_flow_comparison(
    baseline_summary,
    candidate_summary,
    *,
    baseline_passage,
    candidate_passage,
):
    """Compare two explicit passages solved at the same operating conditions."""

    first_conditions = _operating_condition(baseline_summary, baseline_passage)
    second_conditions = _operating_condition(candidate_summary, candidate_passage)
    if first_conditions != second_conditions:
        raise RuntimeError(
            "OpenFOAM flow result operating conditions differ; compare results with "
            "the same fluid properties and passage boundary conditions"
        )
    baseline = openfoam_flow_performance(baseline_summary, **baseline_passage)
    candidate = openfoam_flow_performance(candidate_summary, **candidate_passage)
    metrics = (
        "geometric_flow_area_m2",
        "effective_flow_area_m2",
        "discharge_coefficient",
        "volumetric_flow_rate_m3_s",
        "mass_flow_rate_kg_s",
        "static_pressure_drop_pa",
        "continuity_error_percent",
    )
    return {
        "baseline": baseline,
        "candidate": candidate,
        "changes": {
            name: _change(baseline, candidate, name) for name in metrics
        },
        "baseline_turbulence_model": str(
            baseline_summary.get("turbulence_model") or "unknown"
        ),
        "candidate_turbulence_model": str(
            candidate_summary.get("turbulence_model") or "unknown"
        ),
    }


def openfoam_flow_summary(
    working_directory,
    *,
    result_glob,
    density_kg_m3,
    patches: Mapping[str, str],
    patch_areas_m2: Mapping[str, float] | None = None,
    patch_conditions: Mapping[str, Mapping[str, object]] | None = None,
    kinematic_viscosity_m2_s: float | None = None,
    turbulence_model: str = "laminar",
    converged: bool | None = None,
):
    """Return exact field ranges and area averages from one completed case."""

    root = Path(working_directory)
    density = _finite(density_kg_m3, "density")
    if density <= 0.0:
        raise RuntimeError("OpenFOAM result density must be positive")
    if not isinstance(patches, Mapping) or not patches:
        raise RuntimeError("OpenFOAM result boundary metadata is missing")
    viscosity = None
    if kinematic_viscosity_m2_s is not None:
        viscosity = _finite(kinematic_viscosity_m2_s, "kinematic viscosity")
        if viscosity <= 0.0:
            raise RuntimeError("OpenFOAM result kinematic viscosity must be positive")
    conditions = None
    if patch_conditions is not None:
        if not isinstance(patch_conditions, Mapping) or set(patch_conditions) != set(
            patches
        ):
            raise RuntimeError(
                "OpenFOAM boundary conditions must match the boundary metadata"
            )
        conditions = {}
        for name, value in patch_conditions.items():
            if not isinstance(value, Mapping) or not str(value.get("kind") or ""):
                raise RuntimeError(
                    f"OpenFOAM boundary condition for {name} is invalid"
                )
            conditions[name] = dict(value)
    geometric_areas = None
    if patch_areas_m2 is not None:
        if not isinstance(patch_areas_m2, Mapping) or set(patch_areas_m2) != set(
            patches
        ):
            raise RuntimeError(
                "OpenFOAM geometric boundary areas must match the boundary metadata"
            )
        geometric_areas = {
            name: _finite(patch_areas_m2[name], f"boundary {name} geometric area")
            for name in patches
        }
        if any(area <= 0.0 for area in geometric_areas.values()):
            raise RuntimeError("OpenFOAM geometric boundary areas must be positive")
    internal_path = _one_file(root.glob(str(result_glob)), "internal VTK file")
    internal = _read_dataset(internal_path)
    pressure_range = _pressure_range(internal, density)
    velocity_range = _speed_range(internal)
    vtk_root = internal_path.parent
    boundaries = []
    for name, kind in patches.items():
        boundary_path = _one_file(
            (vtk_root / str(name)).glob("*.vtk"),
            f"VTK boundary file for {name}",
        )
        area, pressure, velocity, volumetric_flow = _patch_average(
            boundary_path, density
        )
        boundary = {
            "name": str(name),
            "kind": str(kind),
            "area_m2": area,
            "pressure_area_average_pa": pressure,
            "velocity_area_average_m_s": velocity,
            "outward_volumetric_flow_rate_m3_s": volumetric_flow,
            "outward_mass_flow_rate_kg_s": _finite(
                volumetric_flow * density, "boundary mass flow rate"
            ),
        }
        if geometric_areas is not None:
            boundary["geometric_area_m2"] = geometric_areas[name]
        if conditions is not None:
            boundary["condition"] = conditions[name]
        boundaries.append(boundary)

    result = {
        "format_version": 1,
        "turbulence_model": str(turbulence_model),
        "pressure_unit": "Pa",
        "velocity_unit": "m/s",
        "density_kg_m3": density,
        "pressure_range_pa": list(pressure_range),
        "velocity_magnitude_range_m_s": list(velocity_range),
        "maximum_velocity_m_s": velocity_range[1],
        "boundaries": boundaries,
    }
    if viscosity is not None:
        result["kinematic_viscosity_m2_s"] = viscosity
    if converged is not None:
        result["converged"] = bool(converged)
    inlets = [value for value in boundaries if value["kind"].startswith("inlet_")]
    outlets = [value for value in boundaries if value["kind"].startswith("outlet_")]
    if (
        len(inlets) == 1
        and len(outlets) == 1
        and inlets[0]["kind"] != "inlet_total_pressure"
        and outlets[0]["kind"] != "outlet_total_pressure"
    ):
        result["static_pressure_drop_pa"] = _finite(
            inlets[0]["pressure_area_average_pa"]
            - outlets[0]["pressure_area_average_pa"],
            "static pressure drop",
        )
        result["pressure_drop_from"] = inlets[0]["name"]
        result["pressure_drop_to"] = outlets[0]["name"]
    return result


def normalize_openfoam_internal_result(path, density_kg_m3):
    """Store pressure in Pa and publish semantic field names for presentation."""

    import vtk

    source = Path(path)
    density = _finite(density_kg_m3, "density")
    if density <= 0.0:
        raise RuntimeError("OpenFOAM result density must be positive")
    dataset = _read_dataset(source)
    turbulence_fields = {
        "k": "Turbulent Kinetic Energy",
        "omega": "Specific Dissipation Rate",
        "nut": "Turbulent Kinematic Viscosity",
    }
    for attributes in (dataset.GetPointData(), dataset.GetCellData()):
        pressure = _array(attributes, "p", 1)
        for index in range(int(pressure.GetNumberOfTuples())):
            pressure.SetTuple1(index, float(pressure.GetTuple1(index)) * density)
        pressure.SetName("Pressure")
        velocity = _array(attributes, "U", 3)
        velocity.SetName("Velocity")
        for source_name, display_name in turbulence_fields.items():
            field = attributes.GetArray(source_name)
            if field is not None and int(field.GetNumberOfComponents()) == 1:
                field.SetName(display_name)
    destination = source.with_name(source.stem + "-normalized.vtk")
    writer = vtk.vtkDataSetWriter()
    writer.SetFileName(str(destination))
    writer.SetInputData(dataset)
    writer.SetFileTypeToBinary()
    if int(writer.Write()) != 1 or not destination.is_file():
        raise RuntimeError("OpenFOAM normalized VTK result could not be written")
    destination.replace(source)
