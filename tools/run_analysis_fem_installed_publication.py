#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

"""Run the four-solver publication A/B gate in an installed VibeCAD host."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


MARKER = "VIBECAD_ANALYSIS_FEM_INSTALLED_PUBLICATION_OK "
REPO_ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = (
    REPO_ROOT
    / "src"
    / "Mod"
    / "VibeCAD"
    / "vibecad_tests"
    / "analysis_fem_installed_publication_integration.py"
)


def _candidates(explicit: str | None) -> list[Path]:
    values: list[Path] = []
    if explicit:
        values.append(Path(explicit).expanduser())
    configured = os.environ.get("VIBECAD_FREECADCMD", "").strip()
    if configured:
        values.append(Path(configured).expanduser())
    for name in ("FreeCADCmd", "freecadcmd", "FreeCADCmd.exe", "freecadcmd.exe"):
        located = shutil.which(name)
        if located:
            values.append(Path(located))
    values.extend(
        (
            REPO_ROOT / "build" / "release" / "bin" / "FreeCADCmd",
            REPO_ROOT / "build" / "release" / "bin" / "FreeCADCmd.exe",
        )
    )
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        programs = Path(local_app_data) / "Programs"
        if programs.is_dir():
            installed = sorted(
                programs.glob("VibeCAD */bin/FreeCADCmd.exe"),
                reverse=True,
            )
            values.extend(
                candidate
                for candidate in installed
                if ".vibecad-rollback" not in str(candidate).lower()
            )
            values.extend(
                candidate
                for candidate in installed
                if ".vibecad-rollback" in str(candidate).lower()
            )
    result = []
    seen = set()
    for value in values:
        resolved = value.resolve()
        key = os.path.normcase(str(resolved))
        if key not in seen:
            seen.add(key)
            result.append(resolved)
    return result


def resolve_freecadcmd(explicit: str | None = None) -> Path:
    for candidate in _candidates(explicit):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "No installed VibeCAD/FreeCAD command host was found. Pass --freecadcmd "
        "or set VIBECAD_FREECADCMD."
    )


def parse_report(output: str) -> dict:
    marker_index = output.find(MARKER)
    if marker_index < 0:
        raise RuntimeError("The installed FEM publication success marker is missing.")
    start = marker_index + len(MARKER)
    report, _end = json.JSONDecoder().raw_decode(output[start:].lstrip())
    if not isinstance(report, dict) or report.get("runtime") != "installed-freecadcmd":
        raise RuntimeError("The installed FEM publication report is malformed.")
    return report


def run(executable: Path) -> dict:
    script = str(INTEGRATION.resolve())
    python = (
        "exec(compile(open(r'"
        + script
        + "', encoding='utf-8').read(), r'"
        + script
        + "', 'exec'), {'__name__':'__main__','__file__':r'"
        + script
        + "'})"
    )
    completed = subprocess.run(
        [str(executable), "--safe-mode", "-c", python],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    output = completed.stdout + completed.stderr
    if completed.returncode:
        raise RuntimeError(
            f"Installed FEM publication gate exited {completed.returncode}.\n{output}"
        )
    return parse_report(output)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freecadcmd", help="Exact installed FreeCADCmd executable")
    args = parser.parse_args(argv)
    executable = resolve_freecadcmd(args.freecadcmd)
    report = run(executable)
    solvers = ", ".join(sorted(report["solvers"]))
    print(f"Installed FEM publication parity passed: {solvers}")
    print(f"Host: {executable}")
    print(
        "Scope: synthetic result fields; publication parity only; "
        "physical solver validation is not claimed."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
