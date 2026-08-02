#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Verify FreeCAD starts through the venv wrapper."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any


SMOKE_CODE = """\
import json
import sys

import FreeCAD as App

print(json.dumps({
    "ok": True,
    "freecad_version": App.Version(),
    "python_executable": sys.executable,
    "python_path_contains_venv": any(".venv" in item for item in sys.path),
}, sort_keys=True))
"""


def parse_payload(output: str) -> dict[str, Any] | None:
    for line in reversed(output.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def run_smoke(wrapper: Path, timeout: int) -> dict[str, Any]:
    script_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix="-freecad-startup-smoke.py", delete=False) as script:
            script.write(SMOKE_CODE)
            script_path = Path(script.name)

        command = [str(wrapper), str(script_path)]
        proc = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    finally:
        if script_path is not None:
            script_path.unlink(missing_ok=True)
    payload = parse_payload(proc.stdout)
    ok = (
        proc.returncode == 0
        and payload is not None
        and payload.get("ok") is True
        and bool(payload.get("freecad_version"))
        and payload.get("python_path_contains_venv") is True
    )
    return {
        "result": "ok" if ok else "failed",
        "returncode": proc.returncode,
        "command": command,
        "payload": payload,
        "stdout": proc.stdout,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wrapper", type=Path, help="FreeCADCmd wrapper to execute")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        report = run_smoke(args.wrapper, args.timeout)
    except subprocess.TimeoutExpired as exc:
        report = {
            "result": "timeout",
            "returncode": None,
            "command": [str(args.wrapper), "<temporary startup smoke script>"],
            "payload": None,
            "stdout": exc.stdout or "",
            "timeout": args.timeout,
        }

    text = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0 if report["result"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
