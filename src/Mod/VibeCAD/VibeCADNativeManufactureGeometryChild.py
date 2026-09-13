# SPDX-License-Identifier: LGPL-2.1-or-later

"""Private FreeCADCmd child for exact CAM geometry paging."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import Part

from VibeCADNativeManufactureGeometryRead import geometry_page


def _write(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def _run() -> int:
    request_path = Path(
        os.environ.get("VIBECAD_NATIVE_CAM_GEOMETRY_REQUEST", "")
    ).resolve(strict=True)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    result_path = Path(str(request["result_path"])).resolve()
    try:
        if request.get("schema") != "vibecad-cam-geometry-v1":
            raise ValueError("The CAM geometry request schema is unsupported.")
        shape_path = Path(str(request["shape_path"])).resolve(strict=True)
        if shape_path.parent != request_path.parent or result_path.parent != request_path.parent:
            raise ValueError("The CAM geometry request escaped its private workspace.")
        digest = _digest(shape_path)
        if digest != str(request["shape_sha256"]):
            raise ValueError("The CAM geometry artifact changed before inspection.")
        shape = Part.Shape()
        shape.importBrep(str(shape_path))
        if shape.isNull() or not shape.isValid():
            raise ValueError("The detached CAM geometry is invalid.")
        page = geometry_page(
            shape,
            elements=request["elements"],
            offset=request["offset"],
            page_size=request["page_size"],
        )
        _write(result_path, {"ok": True, "page": page})
        return 0
    except Exception as exc:
        _write(
            result_path,
            {
                "ok": False,
                "error_code": "NATIVE_MANUFACTURE_GEOMETRY_FAILED",
                "message": str(exc)[:320],
            },
        )
        return 1


raise SystemExit(_run())
