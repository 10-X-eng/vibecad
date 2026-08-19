# SPDX-License-Identifier: LGPL-2.1-or-later

"""FreeCADCmd entry point for the isolated Python geometry fallback."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

_MODULE_DIR = Path(__file__).resolve().parent
if str(_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(_MODULE_DIR))

from VibeCADGeometryFallback import execute_request


def run(request_path: str | Path) -> dict[str, Any]:
    path = Path(request_path)
    request = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(request, dict):
        raise ValueError("The geometry request is not an object.")
    result_path = Path(str(request.get("result_path") or ""))
    if not result_path.name:
        raise ValueError("The geometry request has no result path.")
    result = execute_request(request)
    if not isinstance(result, dict):
        raise TypeError("The geometry fallback returned a non-object result.")
    result["execution_mode"] = "isolated_freecadcmd_fallback"
    if result.get("failure_stage") == "in_process_fallback":
        result["failure_stage"] = "fallback_process"
    result_path.write_text(
        json.dumps(result, ensure_ascii=True, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = [
        value
        for value in list(sys.argv[1:] if argv is None else argv)
        if value != "--pass" and Path(value).resolve() != Path(__file__).resolve()
    ]
    if not arguments:
        print("A geometry request path is required.", file=sys.stderr)
        return 2
    try:
        run(arguments[-1])
    except Exception as exc:
        print(f"VibeCAD geometry fallback failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ != "VibeCADGeometryFallbackRunner":
    raise SystemExit(main())
