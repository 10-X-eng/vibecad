# **************************************************************************
# *   Copyright (c) 2026 VibeCAD contributors                              *
# *                                                                        *
# *   This file is part of the FreeCAD CAx development system.             *
# *                                                                        *
# *   This program is free software; you can redistribute it and/or modify *
# *   it under the terms of the GNU Lesser General Public License (LGPL)   *
# *   as published by the Free Software Foundation; either version 2 of     *
# *   the License, or (at your option) any later version.                   *
# **************************************************************************
"""Discover FEM solver programs available to VibeCAD."""

import os
from pathlib import Path
import shutil
import subprocess

import FreeCAD


_OPENFOAM_PARAMETER_PATH = "User parameter:BaseApp/Preferences/Mod/Fem/OpenFOAM"
_OPENFOAM_ENVIRONMENT_KEY = "EnvironmentFile"


def resolve_executable(program, *, search_path=None):
    """Return the executable path found in the configured path or app bundle."""

    resolved = shutil.which(program, path=search_path)
    if resolved or os.path.dirname(program):
        return resolved

    bundle_bin = os.path.join(FreeCAD.getHomePath(), "bin")
    return shutil.which(program, path=bundle_bin)


def _required_programs(requirements, *, search_path=None):
    programs = {}
    missing = []
    for role, program in requirements:
        resolved = (
            resolve_executable(program, search_path=search_path)
            if search_path
            else resolve_executable(program)
        )
        if resolved:
            programs[role] = resolved
        else:
            missing.append(program)
    return programs, missing


def load_openfoam_environment(environment_file):
    """Load one explicit OpenFOAM environment without changing the app process."""

    path = Path(environment_file).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"OpenFOAM environment file not found: {path}")
    completed = subprocess.run(
        (
            "/bin/bash",
            "--noprofile",
            "--norc",
            "-c",
            'source "$1" >/dev/null && env -0',
            "vibecad-openfoam",
            str(path),
        ),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(os.environ),
        timeout=15,
    )
    environment = {}
    for entry in completed.stdout.split(b"\0"):
        if not entry or b"=" not in entry:
            continue
        name, value = entry.split(b"=", 1)
        environment[name.decode("utf-8")] = value.decode("utf-8")
    if not environment.get("WM_PROJECT_DIR") or not environment.get("PATH"):
        raise RuntimeError(
            "The OpenFOAM environment did not define WM_PROJECT_DIR and PATH."
        )
    environment["VIBECAD_OPENFOAM_ENVIRONMENT_FILE"] = str(path)
    return environment


def _openfoam_environment_file():
    configured = FreeCAD.ParamGet(_OPENFOAM_PARAMETER_PATH).GetString(
        _OPENFOAM_ENVIRONMENT_KEY,
        "",
    )
    explicit = configured or os.environ.get("VIBECAD_OPENFOAM_ENVIRONMENT_FILE", "")
    if explicit:
        return Path(explicit).expanduser()
    project_dir = os.environ.get("WM_PROJECT_DIR", "")
    if project_dir:
        current = Path(project_dir) / "etc" / "bashrc"
        if current.is_file():
            return current
    candidates = sorted(Path("/opt").glob("openfoam*/etc/bashrc"))
    return candidates[0] if len(candidates) == 1 else None


def openfoam_environment():
    """Return a detached environment for one unambiguous OpenFOAM install."""

    environment_file = _openfoam_environment_file()
    if environment_file is None:
        return {}
    try:
        return load_openfoam_environment(environment_file)
    except (OSError, RuntimeError, subprocess.SubprocessError, UnicodeError):
        return {}


def _elmer_programs():
    from femsolver import settings

    programs = {}
    missing = []
    for role, setting_name, program in (
        ("grid", "ElmerGrid", "ElmerGrid"),
        ("solver", "ElmerSolver", "ElmerSolver"),
    ):
        resolved = settings.get_binary(setting_name, True) or resolve_executable(program)
        if resolved:
            programs[role] = resolved
        else:
            missing.append(program)
    return programs, missing


def _single_solver_status(solver, setting_name, program):
    from femsolver import settings

    resolved = settings.get_binary(setting_name, True) or resolve_executable(program)
    return {
        "solver": solver,
        "transport": "native",
        "engine_ready": resolved is not None,
        "programs": {"solver": resolved} if resolved is not None else {},
        "missing": [] if resolved is not None else [program],
    }


def solver_runtime_statuses(solvers=None):
    """Return exact external-program readiness for supported solver engines."""

    supported = ("calculix", "elmer", "mystran", "z88", "openfoam")
    requested = set(supported if solvers is None else tuple(solvers))
    unknown = requested - set(supported)
    if unknown:
        raise ValueError(f"Unsupported FEM solver engines: {sorted(unknown)}")

    statuses = []
    if "calculix" in requested:
        statuses.append(_single_solver_status("calculix", "Calculix", "ccx"))
    if "elmer" in requested:
        elmer_programs, elmer_missing = _elmer_programs()
        statuses.append(
            {
                "solver": "elmer",
                "transport": "native",
                "engine_ready": not elmer_missing,
                "programs": elmer_programs,
                "missing": elmer_missing,
            }
        )
    if "mystran" in requested:
        statuses.append(_single_solver_status("mystran", "Mystran", "mystran"))
    if "z88" in requested:
        statuses.append(_single_solver_status("z88", "Z88", "z88r"))
    if "openfoam" in requested:
        foam_environment = openfoam_environment()
        foam_path = foam_environment.get("PATH")
        openfoam_programs, openfoam_missing = _required_programs(
            (
                ("mesh_import", "ideasUnvToFoam"),
                ("mesh_scale", "transformPoints"),
                ("mesh_check", "checkMesh"),
                ("solver", "foamRun"),
                ("result_export", "foamToVTK"),
            ),
            search_path=foam_path,
        )
        boundary_update = (
            resolve_executable("changeDictionary", search_path=foam_path)
            if foam_path
            else resolve_executable("changeDictionary")
        )
        if boundary_update:
            openfoam_programs["boundary_update"] = boundary_update
        statuses.append(
            {
                "solver": "openfoam",
                "transport": "native",
                "engine_ready": not openfoam_missing,
                "programs": openfoam_programs,
                "missing": openfoam_missing,
                "environment_file": foam_environment.get(
                    "VIBECAD_OPENFOAM_ENVIRONMENT_FILE",
                    "",
                ),
            }
        )
    return tuple(statuses)
