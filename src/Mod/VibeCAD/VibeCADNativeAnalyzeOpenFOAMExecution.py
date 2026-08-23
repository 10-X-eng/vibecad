# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact OpenFOAM input preparation from one live FEM analysis."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeFluidState import (
    fluid_constraint_kind,
    fluid_constraint_state,
)
from VibeCADNativeAnalyzeMeshState import (
    fem_mesh_definition_state,
    fem_mesher_kind,
)
from VibeCADNativeAnalyzeState import is_live, material_kind, material_state
from VibeCADNativeAnalyzeStudy import study_intent_state


_FACE = re.compile(r"^Face([1-9][0-9]*)$")
_PRESSURE_ANCHORS = frozenset(
    {
        "inlet_total_pressure",
        "outlet_total_pressure",
        "outlet_static_pressure",
        "outlet_velocity",
        "outlet_outflow",
    }
)


def _analysis_for_solver(solver: Any) -> Any:
    document = solver.Document
    analyses = []
    for obj in tuple(document.Objects):
        try:
            if obj.isDerivedFrom("Fem::FemAnalysis") and solver in tuple(
                obj.Group or ()
            ):
                analyses.append(obj)
        except Exception:
            continue
    if len(analyses) != 1:
        raise NativeAnalyzeError(
            "The OpenFOAM solver must belong to exactly one analysis."
        )
    return analyses[0]


def _active_members(analysis: Any) -> tuple[Any, ...]:
    return tuple(
        member
        for member in tuple(analysis.Group or ())
        if not bool(getattr(member, "Suppressed", False))
    )


def _generated_gmsh_mesh(members: tuple[Any, ...]) -> tuple[Any, Any]:
    meshes = []
    for member in members:
        try:
            is_mesh = bool(member.isDerivedFrom("Fem::FemMeshObject"))
        except Exception:
            continue
        if not is_mesh:
            continue
        state = fem_mesh_definition_state(member)
        if state["generated"]:
            meshes.append((member, state))
    if len(meshes) != 1:
        raise NativeAnalyzeError(
            "OpenFOAM requires exactly one generated FEM volume mesh in the analysis.",
            error_code="NATIVE_ANALYZE_SOLVER_NOT_READY",
        )
    mesh, state = meshes[0]
    if fem_mesher_kind(mesh) != "gmsh":
        raise NativeAnalyzeError(
            "OpenFOAM currently requires a generated Gmsh mesh.",
            error_code="NATIVE_ANALYZE_SOLVER_NOT_READY",
        )
    if int(state["topology"]["volumes"]) < 1:
        raise NativeAnalyzeError(
            "The OpenFOAM Gmsh mesh contains no volume elements.",
            error_code="NATIVE_ANALYZE_SOLVER_NOT_READY",
        )
    source = getattr(mesh, "Shape", None)
    if not is_live(mesh.Document, source):
        raise NativeAnalyzeError("The OpenFOAM mesh has no live geometry source.")
    return mesh, source


def _fluid_material(members: tuple[Any, ...]) -> dict[str, Any]:
    states = []
    for member in members:
        try:
            kind = material_kind(member)
        except NativeAnalyzeError as exc:
            if exc.error_code == "NATIVE_ANALYZE_TARGET_TYPE_INVALID":
                continue
            raise
        if kind == "fluid":
            states.append(material_state(member))
    if len(states) != 1:
        raise NativeAnalyzeError(
            "OpenFOAM requires exactly one fluid material in the analysis.",
            error_code="NATIVE_ANALYZE_SOLVER_NOT_READY",
        )
    state = states[0]
    if state.get("references"):
        raise NativeAnalyzeError(
            "OpenFOAM currently requires the fluid material to apply to the whole domain.",
            error_code="NATIVE_ANALYZE_SOLVER_NOT_READY",
        )
    properties = dict(state.get("properties") or {})
    missing = [
        name
        for name in ("density_kg_m3", "kinematic_viscosity_m2_s")
        if float(properties.get(name, 0.0) or 0.0) <= 0.0
    ]
    if missing:
        raise NativeAnalyzeError(
            "The OpenFOAM fluid material is missing positive "
            + ", ".join(missing)
            + ".",
            error_code="NATIVE_ANALYZE_SOLVER_NOT_READY",
        )
    return properties


def _global_initial_state(
    states: list[dict[str, Any]],
    kind: str,
) -> dict[str, Any] | None:
    matches = [state for state in states if state["constraint_kind"] == kind]
    if len(matches) > 1:
        raise NativeAnalyzeError(f"OpenFOAM accepts at most one {kind} constraint.")
    if not matches:
        return None
    state = matches[0]
    if state.get("references"):
        raise NativeAnalyzeError(
            f"OpenFOAM requires {kind} to apply to the whole domain."
        )
    return state


def _initial_fields(states: list[dict[str, Any]]) -> tuple[tuple[float, ...], float]:
    velocity = [0.0, 0.0, 0.0]
    initial_velocity = _global_initial_state(states, "initial_flow_velocity")
    if initial_velocity is not None:
        components = dict(initial_velocity["definition"]["components"])
        for index, axis in enumerate(("x", "y", "z")):
            component = components.get(axis)
            if component is None:
                continue
            if component["kind"] != "value":
                raise NativeAnalyzeError(
                    "OpenFOAM initial velocity currently requires numeric components."
                )
            velocity[index] = float(component["value_m_s"])
    initial_pressure = _global_initial_state(states, "initial_pressure")
    pressure = (
        float(initial_pressure["definition"]["pressure_pa"])
        if initial_pressure is not None
        else 0.0
    )
    return tuple(velocity), pressure


def _boundary_patches(
    states: list[dict[str, Any]],
    source: Any,
) -> dict[str, dict[str, Any]]:
    face_count = len(tuple(source.Shape.Faces))
    expected = {f"Face{index}" for index in range(1, face_count + 1)}
    assigned: dict[str, dict[str, Any]] = {}
    for state in states:
        kind = state["constraint_kind"]
        if kind != "fluid_boundary":
            if kind == "flow_velocity":
                raise NativeAnalyzeError(
                    "OpenFOAM uses typed fluid face boundaries, not flow_velocity."
                )
            continue
        definition = dict(state["definition"])
        if definition["turbulence"] != {"kind": "none"}:
            raise NativeAnalyzeError(
                f"Fluid boundary {state['object_name']} requires turbulence not supported "
                "by the selected laminar solver."
            )
        if definition["thermal"] != {"kind": "adiabatic"}:
            raise NativeAnalyzeError(
                f"Fluid boundary {state['object_name']} requires thermal flow not supported "
                "by the selected isothermal solver."
            )
        for reference in state.get("references") or ():
            if reference.get("object_name") != source.Name:
                raise NativeAnalyzeError(
                    f"Fluid boundary {state['object_name']} references a different geometry source."
                )
            for subelement in reference.get("subelements") or ():
                name = str(subelement)
                if _FACE.fullmatch(name) is None or name not in expected:
                    raise NativeAnalyzeError(
                        f"Fluid boundary {state['object_name']} contains invalid face {name}."
                    )
                if name in assigned:
                    raise NativeAnalyzeError(
                        f"Fluid face {name} has more than one boundary."
                    )
                assigned[name] = dict(definition["condition"])
    missing = sorted(expected - set(assigned), key=lambda item: int(item[4:]))
    if missing:
        raise NativeAnalyzeError(
            "OpenFOAM requires one boundary on every fluid-domain face; unassigned: "
            + ", ".join(missing)
            + ".",
            error_code="NATIVE_ANALYZE_SOLVER_NOT_READY",
        )
    if not any(value["kind"] in _PRESSURE_ANCHORS for value in assigned.values()):
        raise NativeAnalyzeError(
            "OpenFOAM requires at least one pressure-defining inlet or outlet boundary.",
            error_code="NATIVE_ANALYZE_SOLVER_NOT_READY",
        )
    return {
        name: assigned[name]
        for name in sorted(assigned, key=lambda item: int(item[4:]))
    }


def _write_mesh(mesh: Any, expected_patches: set[str], path: Path) -> None:
    import Fem

    detached = Fem.FemMesh(mesh.FemMesh)
    face_groups = set()
    for group_index in tuple(detached.Groups):
        name = str(detached.getGroupName(group_index))
        if _FACE.fullmatch(name):
            if name in face_groups:
                raise NativeAnalyzeError(
                    f"The Gmsh mesh contains duplicate group {name}."
                )
            face_groups.add(name)
        else:
            detached.removeGroup(group_index)
    if face_groups != expected_patches:
        missing = sorted(expected_patches - face_groups)
        extra = sorted(face_groups - expected_patches)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unexpected " + ", ".join(extra))
        raise NativeAnalyzeError(
            "The Gmsh face groups do not match the CFD domain: "
            + "; ".join(details)
            + "."
        )
    detached.write(str(path))
    if not path.is_file() or path.stat().st_size < 1:
        raise NativeAnalyzeError("The OpenFOAM UNV mesh export produced no artifact.")


def prepare_openfoam_request(
    solver: Any,
    root: Path,
) -> tuple[
    None,
    tuple[tuple[str, tuple[str, ...]], ...],
    dict[str, str],
    dict[str, Any],
]:
    if str(solver.FlowRegime) != "steady" or str(solver.TurbulenceModel) != "laminar":
        raise NativeAnalyzeError("This OpenFOAM solver supports steady laminar flow.")
    analysis = _analysis_for_solver(solver)
    intent = study_intent_state(analysis)
    if (
        not intent.get("declared")
        or "fluid" not in set(intent.get("physics") or ())
        or intent.get("regime") != "steady"
    ):
        raise NativeAnalyzeError(
            "The analysis must declare a steady fluid study before OpenFOAM can run.",
            error_code="NATIVE_ANALYZE_SOLVER_NOT_READY",
        )
    members = _active_members(analysis)
    mesh, source = _generated_gmsh_mesh(members)
    material = _fluid_material(members)
    fluid_states = []
    for member in members:
        try:
            fluid_constraint_kind(member)
            fluid_states.append(fluid_constraint_state(member))
        except NativeAnalyzeError as exc:
            if exc.error_code != "NATIVE_ANALYZE_TARGET_TYPE_INVALID":
                raise
    patches = _boundary_patches(fluid_states, source)
    initial_velocity, initial_pressure = _initial_fields(fluid_states)

    from femsolver.openfoam.case import SteadyIncompressibleCase, build_case_files

    case = SteadyIncompressibleCase(
        density_kg_m3=float(material["density_kg_m3"]),
        kinematic_viscosity_m2_s=float(material["kinematic_viscosity_m2_s"]),
        max_iterations=int(solver.MaxIterations),
        write_every_iterations=int(solver.WriteEveryIterations),
        pressure_tolerance=float(solver.PressureTolerance),
        velocity_tolerance=float(solver.VelocityTolerance),
        initial_velocity_m_s=initial_velocity,
        initial_pressure_pa=initial_pressure,
        patches=patches,
    )
    for relative, content in build_case_files(case).items():
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8", newline="\n")
    mesh_path = root / "mesh.unv"
    _write_mesh(mesh, set(patches), mesh_path)

    from femsolver.runtime import openfoam_environment, solver_runtime_statuses

    environment = openfoam_environment()
    status = solver_runtime_statuses({"openfoam"})[0]
    if not status["engine_ready"]:
        raise NativeAnalyzeError(
            "OpenFOAM is missing required programs: "
            + ", ".join(status["missing"])
            + ".",
            error_code="NATIVE_ANALYZE_SOLVER_UNAVAILABLE",
        )
    programs = status["programs"]
    commands = (
        (programs["mesh_import"], (str(mesh_path),)),
        (programs["mesh_scale"], ("scale=(0.001 0.001 0.001)",)),
        (programs["mesh_check"], ("-allGeometry",)),
        (programs["solver"], ()),
        (programs["result_export"], ("-latestTime", "-ascii")),
    )
    return (
        None,
        commands,
        environment,
        {
            "result_glob": "VTK/*.vtk",
            "solver_log": "solver-4.log",
        },
    )
