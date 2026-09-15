# SPDX-License-Identifier: LGPL-2.1-or-later
"""Private command-line STEP writer; receives a BREP, never a live document."""

import hashlib
import json
import math
from pathlib import Path
import sys

import Part


MAX_STEP_BYTES = 50 * 1024 * 1024


def _solid(shape):
    if shape.isNull() or not shape.isValid() or len(shape.Solids) != 1:
        raise ValueError("The folded export must contain one valid solid")
    bounds = shape.BoundBox
    values = (shape.Volume, bounds.XLength, bounds.YLength, bounds.ZLength)
    if not all(math.isfinite(value) and value > 0 for value in values):
        raise ValueError("The folded export has invalid dimensions")
    return values


def run(workspace):
    workspace = Path(workspace).resolve(strict=True)
    result = workspace/"result.json"
    try:
        request = json.loads((workspace/"request.json").read_text())
        if request.get("schema") != "vibecad-rmfg-step-v1":
            raise ValueError("Unsupported folded export request")
        source = workspace/"folded.brep"
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != request["brep_sha256"]:
            raise ValueError("The detached folded geometry changed before export")
        shape = Part.Shape()
        shape.importBrep(str(source))
        expected = _solid(shape)
        target = workspace/"folded.step"
        shape.exportStep(str(target))
        if not 0 < target.stat().st_size <= MAX_STEP_BYTES:
            raise ValueError("RMFG STEP exports must contain at most 50 MiB")
        restored = Part.Shape()
        restored.read(str(target))
        actual = _solid(restored)
        if not all(math.isclose(before, after, rel_tol=1e-6, abs_tol=1e-6)
                   for before, after in zip(expected, actual)):
            raise ValueError("The STEP round trip changed the folded solid")
        result.write_text(json.dumps({"ok": True, "schema": request["schema"],
            "step_sha256": hashlib.sha256(target.read_bytes()).hexdigest()}))
        return 0
    except Exception as error:
        result.write_text(json.dumps({"ok": False, "schema": "vibecad-rmfg-step-v1",
                                      "message": str(error)[:320]}))
        return 1


if __name__ == "__main__":
    # FreeCADCmd treats SystemExit as immediate process exit, bypassing its
    # application teardown while background startup work may still use Qt.
    # Return to the console/EOF so native shutdown joins that work. The parent
    # checks result.json as well as the process exit status.
    run(sys.argv[1])
