#!/usr/bin/env python3
"""Collect OpenFOAM forceCoeffs output into VibeCAD's stable JSON contract.

This script is intentionally independent of FreeCAD.  It can be called at the
end of an OpenFOAM Allrun script.  It parses the *header actually written by the
case* instead of hard-coding one OpenFOAM version's column order.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

SCHEMA = "vibecad.openfoam.collector/1"


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def parse_force_coeffs(path: Path) -> dict[str, float]:
    header: list[str] | None = None
    row: list[float] | None = None
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            body = line.lstrip("#").strip()
            tokens = body.split()
            if tokens and _normalize(tokens[0]) == "time" and len(tokens) >= 4:
                header = tokens
            continue
        try:
            values = [float(v) for v in line.split()]
        except ValueError:
            continue
        row = values
    if row is None:
        raise RuntimeError(f"No numeric forceCoeffs rows found in {path}")
    if header is None:
        # Common forceCoeffs leading columns.  We only use the first 7 and reject
        # shorter files rather than guessing a different layout.
        header = ["Time", "Cd", "Cs", "Cl", "CmRoll", "CmPitch", "CmYaw"]
    if len(row) < min(7, len(header)):
        raise RuntimeError("forceCoeffs row has too few columns")
    values = {header[i]: row[i] for i in range(min(len(header), len(row)))}
    normalized = {_normalize(k): v for k, v in values.items()}

    def required(*names: str) -> float:
        for name in names:
            key = _normalize(name)
            if key in normalized:
                return float(normalized[key])
        raise RuntimeError(f"Missing forceCoeffs column: {names[0]}")

    return {
        "time": required("Time"),
        "cd": required("Cd"),
        "cs": required("Cs"),
        "cl": required("Cl"),
        "cl_roll": required("CmRoll", "ClRoll"),
        "cm_pitch": required("CmPitch", "Cm"),
        "cn_yaw": required("CmYaw", "CnYaw"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="OpenFOAM forceCoeffs coefficient.dat")
    parser.add_argument("--output", default="vibecad_result.json")
    parser.add_argument("--openfoam-version", default="unknown")
    parser.add_argument("--converged", choices=("true", "false", "unknown"), default="unknown")
    args = parser.parse_args()

    values = parse_force_coeffs(Path(args.input))
    converged = None if args.converged == "unknown" else args.converged == "true"
    payload = {
        "schema_version": SCHEMA,
        "openfoam_version": args.openfoam_version,
        "coefficients": {
            "cd": values["cd"],
            "cl": values["cl"],
            "cs": values["cs"],
            "cl_roll": values["cl_roll"],
            "cm_pitch": values["cm_pitch"],
            "cn_yaw": values["cn_yaw"],
        },
        "converged": converged,
        "iterations": None,
        "residuals": {},
        "last_time": values["time"],
        "warnings": [] if converged is True else ["Convergence not independently established by the collector."],
    }
    Path(args.output).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
