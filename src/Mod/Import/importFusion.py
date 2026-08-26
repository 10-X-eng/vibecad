# SPDX-License-Identifier: LGPL-2.1-or-later
"""File > Open / Import handler for Autodesk Fusion archives (.f3d, .f3z).

Fusion native files are ZIP archives. Modern Fusion writes Zstandard
(method 93) members. Geometry is taken from the OGS display mesh
(`Fusion_mesh_*`) which every Fusion archive includes; parametric history
is not reconstructed.
"""

from __future__ import annotations

import builtins
import os
from typing import List, Sequence

from cad_geometry import (
    Triangle,
    add_triangles_to_document,
    pack_fusion_mesh,
    parse_fusion_mesh,
)
from cadzip import is_zip, open_zip

IMPORT_TYPE = "Autodesk Fusion Design (*.f3d *.F3D *.f3z *.F3Z)"


def open(filename):
    """Create a new document and load *filename* into it (File > Open)."""
    import FreeCAD

    name = os.path.splitext(os.path.basename(filename))[0] or "Fusion"
    doc = FreeCAD.newDocument(name)
    insert(filename, doc.Name)
    return doc


def insert(filename, docname):
    """Load *filename* into the named document (File > Import)."""
    import FreeCAD

    doc = _document(docname)
    label = os.path.splitext(os.path.basename(filename))[0] or "Fusion"
    groups = read_fusion_meshes(filename)
    if not groups:
        raise RuntimeError(
            f"No importable Fusion geometry in '{filename}'. "
            "The archive has no OGS display mesh."
        )
    last = None
    for i, triangles in enumerate(groups):
        part_label = label if len(groups) == 1 else f"{label}_{i + 1}"
        last = add_triangles_to_document(doc, triangles, part_label)
    try:
        doc.recompute()
    except Exception:
        pass
    return last


def read_fusion_meshes(filename: str) -> List[List[Triangle]]:
    """Return one triangle list per OGS mesh found in a .f3d or .f3z file."""
    path = os.fspath(filename)
    ext = os.path.splitext(path)[1].lower()
    if ext == ".f3z":
        return _read_f3z(path)
    if ext == ".f3d" or is_zip(path):
        return _read_f3d(path)
    raise RuntimeError(f"Not a Fusion archive: {filename}")


def _read_f3z(path: str) -> List[List[Triangle]]:
    import tempfile

    groups: List[List[Triangle]] = []
    with open_zip(path) as zf:
        inner = [
            n
            for n in zf.namelist()
            if n.lower().endswith(".f3d") and not n.endswith("/")
        ]
        if not inner:
            # Some .f3z files are themselves a Fusion document zip.
            return _read_f3d(path)
        with tempfile.TemporaryDirectory(prefix="vibecad-f3z-") as tmp:
            for name in inner:
                dest = os.path.join(tmp, os.path.basename(name) or "part.f3d")
                with builtins.open(dest, "wb") as fh:
                    fh.write(zf.read(name))
                groups.extend(_read_f3d(dest))
    return groups


def _read_f3d(path: str) -> List[List[Triangle]]:
    if not is_zip(path):
        raise RuntimeError(f"Fusion file is not a ZIP archive: {path}")
    groups: List[List[Triangle]] = []
    with open_zip(path) as zf:
        mesh_names = [
            n
            for n in zf.namelist()
            if _is_ogs_mesh_name(n)
        ]
        mesh_names.sort()
        for name in mesh_names:
            data = zf.read(name)
            tris = parse_fusion_mesh(data)
            if tris:
                groups.append(tris)
    return groups


def _is_ogs_mesh_name(name: str) -> bool:
    base = os.path.basename(name).lower()
    if "stream_mesh" in base:
        return False
    return base.startswith("fusion_mesh_") or base.startswith("mesh_")


def write_f3d(path: str, triangles: Sequence[Triangle]) -> None:
    """Write a STORE-compressed Fusion-layout archive with one OGS mesh."""
    import zipfile

    mesh = pack_fusion_mesh(list(triangles))
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("Manifest.dat", b"FusionDocument")
        zf.writestr(
            "FusionAssetName[Active]/OGS.BlobFolder/OGS/DefaultScene/Fusion_mesh_000",
            mesh,
        )


def write_f3z(path: str, parts: Sequence[Sequence[Triangle]]) -> None:
    """Write a .f3z ZIP containing one .f3d per part."""
    import io
    import zipfile

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as zf:
        for i, tris in enumerate(parts):
            buf = io.BytesIO()
            write_f3d_fp(buf, tris)
            zf.writestr(f"part{i + 1}.f3d", buf.getvalue())


def write_f3d_fp(fp, triangles: Sequence[Triangle]) -> None:
    import zipfile

    mesh = pack_fusion_mesh(list(triangles))
    with zipfile.ZipFile(fp, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("Manifest.dat", b"FusionDocument")
        zf.writestr(
            "FusionAssetName[Active]/OGS.BlobFolder/OGS/DefaultScene/Fusion_mesh_000",
            mesh,
        )


def _document(docname):
    import FreeCAD

    if docname:
        try:
            return FreeCAD.getDocument(docname)
        except Exception:
            return FreeCAD.newDocument(docname)
    doc = FreeCAD.ActiveDocument
    if doc is None:
        doc = FreeCAD.newDocument()
    return doc
